"""The job worker (from packsure e97a509): `python -m watchful.worker` runs jobs until stopped; `--until-idle` stops
when none is due. It puts `worker_up` (namespace Watchful, no dimensions) every Tuning jobs.heartbeat_s, which the
worker down alarm reads. Where RUN_JOBS_IN_APP is true (ioflood, a laptop) the app starts one in a process of its own
(`InAppWorker`, running `serve`) and starts it again whenever it dies; on AWS systemd runs and restarts it."""
import argparse
import json
import logging
import multiprocessing as mp
import threading
import time

log = logging.getLogger(__name__)

WATCH_S = 0.5
MAX_RESPAWN_S = 60
HEARTBEAT = "worker_up"


def _worker(settings, tuning):
    from watchful.components import make_engine
    from watchful.jobs import JobQueue, Worker
    return Worker(JobQueue(make_engine(settings)), settings, tuning)


def serve(settings_json: str, tuning_json: str, stop) -> None:
    """Runs jobs until `stop` (a multiprocessing Event) is set."""
    from watchful.components import make_metrics
    from watchful.config import Settings
    from watchful.metrics import start_heartbeat
    from watchful.tuning import Tuning

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(processName)s %(name)s: %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    settings = Settings(**json.loads(settings_json), _env_file=None)
    tuning = Tuning.model_validate_json(tuning_json)
    start_heartbeat(make_metrics(settings), HEARTBEAT, tuning.jobs.heartbeat_s, stop=stop)
    _worker(settings, tuning).run(stop)


class InAppWorker:
    """The app's own job worker process. One that dies is started again at once if it had beaten, else less and less
    often (it cannot start)."""

    def __init__(self, settings, tuning):
        self.settings, self.tuning = settings, tuning
        self._ctx = mp.get_context("spawn")
        self._halt = threading.Event()  # the watcher's: start no other
        self._lock = threading.Lock()
        self.process = None
        self._stop = None  # the process's own Event: finish the job in hand and stop
        self._started = 0.0
        self.failures = 0

    def start(self) -> None:
        self._spawn()
        threading.Thread(target=self._watch, daemon=True, name="jobs-watch").start()

    def _spawn(self) -> None:
        with self._lock:
            if self._halt.is_set():
                return
            self._started = time.time()
            # an Event of its own: one a killed process was waiting on can never be set again (set waits for it)
            self._stop = self._ctx.Event()
            self.process = self._ctx.Process(target=serve, args=(self.settings.model_dump_json(),
                                                                 self.tuning.model_dump_json(), self._stop),
                                             daemon=True, name="jobs")
            self.process.start()

    def _beat_since_start(self) -> bool:
        from watchful.jobs import beat_path
        try:
            return beat_path(self.settings.data_dir).stat().st_mtime >= self._started
        except OSError:
            return False

    def _watch(self) -> None:
        while not self._halt.wait(WATCH_S):
            if self.process is not None and self.process.is_alive():
                continue
            self.failures = 0 if self._beat_since_start() else self.failures + 1
            wait = min(MAX_RESPAWN_S, 2 ** self.failures) if self.failures else 0
            log.error("the job worker stopped (exit %s); starting another in %s s",
                      getattr(self.process, "exitcode", None), wait)
            if self._halt.wait(wait):
                return
            try:
                self._spawn()
            except Exception:  # tried again after the next back off; the watcher never stops
                log.exception("the job worker could not be started")

    def stopping(self) -> None:
        """The app is stopping: the worker finishes the job in hand, and none is started again."""
        if self._halt.is_set():
            return
        self._halt.set()
        with self._lock:
            stop = self._stop
        if stop is not None:  # on a thread: a worker killed just now would hold the app's shutdown on it
            threading.Thread(target=stop.set, daemon=True, name="jobs-stop").start()

    def join(self, timeout: float) -> None:
        """Waits for the worker to stop, then cuts it off; its job's lease lets another worker run it again."""
        self.stopping()
        with self._lock:
            process = self.process
        if process is None:
            return
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(5)


def main(argv: list[str] | None = None) -> None:
    from watchful.components import make_metrics
    from watchful.config import get_settings
    from watchful.metrics import start_heartbeat
    from watchful.tuning import load_tuning

    args = argparse.ArgumentParser(prog="python -m watchful.worker")
    args.add_argument("--until-idle", action="store_true", help="stop when no job is due")
    until_idle = args.parse_args(argv).until_idle
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    tuning = load_tuning(settings.tuning_file)
    if not until_idle:
        start_heartbeat(make_metrics(settings), HEARTBEAT, tuning.jobs.heartbeat_s)
    _worker(settings, tuning).run(until_idle=until_idle)


if __name__ == "__main__":
    main()

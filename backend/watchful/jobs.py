"""Background jobs from the job table (from packsure e97a509). A module registers a handler per kind with
`@handler(kind)` and is listed in HANDLER_MODULES; a handler gets a `JobContext` and the job's payload. Jobs belong to
the deployment; a society's job names its workspace_id in the payload and reads through `ctx.repo(workspace_id)`.
Handlers may run again after a crash or a lost lease, so they must be safe to repeat. Long handlers call
`ctx.extend_lease()` between steps; it raises LeaseLost once another worker has claimed the job, and the late worker
then leaves the job alone. A handler that has to wait (Telegram's limits, an edit too soon after the last) raises
`Defer(seconds)`: the job is queued again for then without using up an attempt.

`@handler(kind, daily=True)` also has a long running worker queue that job once a day (in TIMEZONE), and
`@handler(kind, urgent=True)` runs that kind on a lane of its own as well, with a short lease (Tuning
jobs.urgent_lease_s), so an alert never waits behind a long job. `@periodic` functions run every Tuning
jobs.periodic_s in a long running worker (they queue timed jobs, such as each society's 9 AM summary).
A worker beats (DATA_DIR/worker.beat) while it runs.

Run a worker with `python -m watchful.worker` (not this module: run as __main__ it would be a second copy of this
module, and the handlers register into the first)."""
import hashlib
import importlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, delete, orm, select, update
from sqlalchemy.exc import IntegrityError

from watchful.config import Settings
from watchful.db import models as m
from watchful.tuning import Tuning

log = logging.getLogger("watchful.jobs")

HANDLER_MODULES = ("watchful.alerts.incidents", "watchful.alerts.telegram", "watchful.alerts.summary",
                   "watchful.billing.jobs", "watchful.demo")

_HANDLERS: dict[str, Callable] = {}
_DAILY: list[str] = []
_URGENT: list[str] = []
_STARTS: list[Callable] = []
_BETWEEN: list[Callable] = []
_PERIODIC: list[Callable] = []


def handler(kind: str, daily: bool = False, urgent: bool = False):
    def add(fn: Callable) -> Callable:
        _HANDLERS[kind] = fn
        for wanted, kinds in ((daily, _DAILY), (urgent, _URGENT)):
            if wanted and kind not in kinds:
                kinds.append(kind)
        return fn
    return add


def _hook(hooks: list[Callable]):
    def add(fn: Callable) -> Callable:
        if fn not in hooks:
            hooks.append(fn)
        return fn
    return add


on_start = _hook(_STARTS)  # fn(worker) when a long running worker starts
between_jobs = _hook(_BETWEEN)  # fn(worker, monotonic now) after each job and each idle wait
periodic = _hook(_PERIODIC)  # fn(worker, now) every Tuning jobs.periodic_s


BEATS_PER_ALIVE = 5  # beats in each Tuning jobs.worker_alive_s


def beat_path(data_dir) -> Path:
    return Path(data_dir) / "worker.beat"


def worker_alive(data_dir, within_s: float) -> bool:
    """A job worker has beaten within this many seconds."""
    try:
        return time.time() - beat_path(data_dir).stat().st_mtime <= within_s
    except OSError:
        return False


def job_id_for(key: str) -> str:
    """The id of the one job a key may ever have (a society's summary for a day, one step of an escalation)."""
    return "job_" + hashlib.sha256(key.encode()).hexdigest()[:16]


class LeaseLost(Exception):
    """Another worker claimed this job after its lease ran out."""


class Defer(Exception):
    """Raised by a handler that must wait: the job runs again after `seconds`, without using up an attempt."""

    def __init__(self, seconds: float):
        super().__init__(f"deferred {seconds:.1f} s")
        self.seconds = max(0.0, float(seconds))


class JobQueue:
    """The job table."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = orm.sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def tx(self) -> Iterator[orm.Session]:
        with self._sessions.begin() as s:
            yield s

    def enqueue(self, kind: str, payload: dict, run_after: datetime | None = None, key: str | None = None,
                session: orm.Session | None = None) -> m.Job | None:
        """Queues a job, in `session`'s transaction when given. With a key there is only ever one job for it: None
        when it already exists."""
        job = m.Job(id=job_id_for(key) if key else None, kind=kind, payload=payload, run_after=run_after or m.utcnow())
        if session is not None:
            if key and session.get(m.Job, job.id) is not None:
                return None
            session.add(job)
            return job
        try:
            with self.tx() as s:
                if key and s.get(m.Job, job.id) is not None:
                    return None
                s.add(job)
        except IntegrityError:  # another process queued the same key just now
            return None
        return job

    def next_job(self, now: datetime | None = None, lease_s: float = 300, max_attempts: int = 5,
                 kinds: list[str] | None = None) -> m.Job | None:
        """Claims the next due job for one worker, of these kinds only when given. A running job whose lease has
        passed (its worker died) is due again, or failed when that was its last attempt. One UPDATE claims it, so
        two workers never share a job."""
        now = now or m.utcnow()
        dead = (update(m.Job)
                .where(m.Job.status == "running", m.Job.run_after <= now, m.Job.attempts >= max_attempts)
                .values(status="failed", error="the worker stopped before finishing its last attempt")
                .execution_options(synchronize_session=False))
        due = (select(m.Job.id)
               .where(m.Job.status.in_(("queued", "running")), m.Job.run_after <= now,
                      m.Job.attempts < max_attempts, *([m.Job.kind.in_(kinds)] if kinds else []))
               .order_by(m.Job.run_after, m.Job.id).limit(1)
               .with_for_update(skip_locked=True).scalar_subquery())
        claim = (update(m.Job).where(m.Job.id == due)
                 .values(status="running", attempts=m.Job.attempts + 1, run_after=now + timedelta(seconds=lease_s))
                 .returning(m.Job).execution_options(synchronize_session=False))
        with self.tx() as s:
            s.execute(dead)
            return s.scalars(claim).first()

    # A worker passes the attempts value its claim returned: once its lease ran out and another worker claimed the
    # job, attempts has moved on and the late worker's calls change nothing.

    def extend_lease(self, job_id: str, seconds: float, attempts: int | None = None) -> bool:
        """More time for a long job; False when the job is no longer running, or no longer this claim's."""
        mine = [] if attempts is None else [m.Job.attempts == attempts]
        with self.tx() as s:
            return s.execute(update(m.Job).where(m.Job.id == job_id, m.Job.status == "running", *mine)
                             .values(run_after=m.utcnow() + timedelta(seconds=seconds))).rowcount > 0

    def job_done(self, job_id: str, attempts: int | None = None) -> None:
        mine = [] if attempts is None else [m.Job.attempts == attempts]
        with self.tx() as s:
            s.execute(update(m.Job).where(m.Job.id == job_id, *mine).values(status="done", error=None))

    def job_failed(self, job_id: str, error: str, retry_in_s: float = 60, max_attempts: int = 5,
                   attempts: int | None = None) -> None:
        """Queued again after a pause, or failed for good after max_attempts."""
        with self.tx() as s:
            job = s.get(m.Job, job_id)
            if job is None or (attempts is not None and job.attempts != attempts):
                return
            job.error = error
            if job.attempts >= max_attempts:
                job.status = "failed"
            else:
                job.status, job.run_after = "queued", m.utcnow() + timedelta(seconds=retry_in_s)

    def job_deferred(self, job_id: str, run_after: datetime, attempts: int) -> None:
        """Queued again for later; the attempt it used is given back."""
        with self.tx() as s:
            s.execute(update(m.Job).where(m.Job.id == job_id, m.Job.status == "running", m.Job.attempts == attempts)
                      .values(status="queued", run_after=run_after, attempts=attempts - 1))

    def delete_finished_before(self, before: datetime) -> int:
        with self.tx() as s:
            return s.execute(delete(m.Job).where(m.Job.status.in_(("done", "failed")),
                                                 m.Job.created_at < before)).rowcount


@dataclass
class JobContext:
    job: m.Job
    worker: "Worker"
    lease_s: float
    now: datetime  # when the job was claimed

    @property
    def settings(self) -> Settings:
        return self.worker.settings

    @property
    def tuning(self) -> Tuning:
        return self.worker.tuning

    @property
    def queue(self) -> JobQueue:
        return self.worker.queue

    def repo(self, workspace_id: str):
        return self.worker.repo(workspace_id)

    def extend_lease(self) -> None:
        if not self.queue.extend_lease(self.job.id, self.lease_s, attempts=self.job.attempts):
            raise LeaseLost(f"job {self.job.id} was claimed by another worker")


class Worker:
    """Runs jobs. The parts handlers use (a society's repository, blobs, the alert channel) are made on first use
    unless given."""

    def __init__(self, queue: JobQueue, settings: Settings, tuning: Tuning, repo_for: Callable | None = None,
                 blobs=None, channel=None, error_wait_s: float | None = None):
        self.queue, self.settings, self.tuning = queue, settings, tuning
        t = tuning.jobs
        self.lease_s, self.max_attempts, self.retry_in_s = t.lease_s, t.max_attempts, t.retry_in_s
        self.idle_s, self.urgent_idle_s, self.daily_check_s = t.idle_s, t.urgent_idle_s, t.daily_check_s
        self.error_wait_s = t.error_wait_s if error_wait_s is None else error_wait_s
        self._repo_for, self._blobs, self._channel = repo_for, blobs, channel
        self._repos: dict[str, object] = {}
        self._parts = threading.Lock()
        self.state: dict = {}  # what handler modules keep for the worker's life (Telegram's per chat limits, faces)
        self._daily_checked = self._periodic_checked = -float("inf")
        for module in HANDLER_MODULES:
            importlib.import_module(module)

    def repo(self, workspace_id: str):
        with self._parts:
            if workspace_id not in self._repos:
                if self._repo_for is not None:
                    self._repos[workspace_id] = self._repo_for(workspace_id)
                else:
                    from watchful.components import make_repo
                    self._repos[workspace_id] = make_repo(self.settings, workspace_id)
            return self._repos[workspace_id]

    @property
    def blobs(self):
        with self._parts:
            if self._blobs is None:
                from watchful.components import make_blobs
                self._blobs = make_blobs(self.settings)
            return self._blobs

    @property
    def channel(self):
        with self._parts:
            if self._channel is None:
                from watchful.components import make_alert_channel
                self._channel = make_alert_channel(self.settings, self.tuning)
            return self._channel

    def run_once(self, now: datetime | None = None, kinds: list[str] | None = None) -> m.Job | None:
        """Claims and runs the next due job (of these kinds only, when given); None when there is none."""
        now = now or m.utcnow()
        job = self.queue.next_job(now=now, lease_s=self.lease_s, max_attempts=self.max_attempts, kinds=kinds)
        if job is None:
            return None
        lease = self.lease_s
        if job.kind in _URGENT and self.tuning.jobs.urgent_lease_s < lease:
            lease = self.tuning.jobs.urgent_lease_s  # found again soon after a restart
            self.queue.extend_lease(job.id, lease, attempts=job.attempts)
        try:
            fn = _HANDLERS.get(job.kind)
            if fn is None:
                raise LookupError(f"no handler for {job.kind} jobs")
            fn(JobContext(job, self, lease, now), dict(job.payload))
        except LeaseLost as e:
            log.warning("%s", e)
        except Defer as d:
            self.queue.job_deferred(job.id, now + timedelta(seconds=d.seconds), job.attempts)
        except Exception as e:
            log.exception("job %s (%s) failed", job.id, job.kind)
            # an urgent job is an alert someone waits for; it may run twice if Telegram took it but the answer was lost
            retry = self.tuning.jobs.urgent_retry_s if job.kind in _URGENT else self.retry_in_s
            self.queue.job_failed(job.id, f"{type(e).__name__}: {e}", retry, self.max_attempts, attempts=job.attempts)
        else:
            self.queue.job_done(job.id, attempts=job.attempts)
        return job

    def run_due(self, now: datetime | None = None, limit: int = 1000) -> list[m.Job]:
        """Runs every job due at `now`, one after another (tests and tools)."""
        ran = []
        while len(ran) < limit and (job := self.run_once(now=now)) is not None:
            ran.append(job)
        return ran

    def schedule_daily(self, now: datetime | None = None) -> list[str]:
        """Queues each daily job not yet queued today; returns the kinds queued."""
        now = now or m.utcnow()
        day = now.astimezone(ZoneInfo(self.settings.timezone)).date().isoformat()
        return [kind for kind in _DAILY if self.queue.enqueue(kind, {"day": day}, now, key=f"daily|{kind}|{day}")]

    def run_periodic(self, now: datetime | None = None) -> None:
        now = now or m.utcnow()
        for fn in _PERIODIC:
            try:
                fn(self, now)
            except Exception:
                log.exception("%s failed", fn.__name__)

    def beat(self) -> None:
        try:
            path = beat_path(self.settings.data_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            log.exception("the worker could not beat")

    def between_jobs(self, now: float | None = None) -> None:
        self.beat()
        for fn in _BETWEEN:
            try:
                fn(self, time.monotonic() if now is None else now)
            except Exception:
                log.exception("%s failed between jobs", fn.__name__)

    def run(self, stop: threading.Event | None = None, until_idle: bool = False) -> None:
        """Runs jobs until stopped, or until none is due when until_idle. A failure outside a job (the database
        busy or away) is logged and waited out; the loop keeps going. Only a worker that keeps running queues the
        daily and timed jobs, runs the start functions and has the urgent lane."""
        stop = stop or threading.Event()
        if not until_idle and not stop.is_set():
            self.beat()
            threading.Thread(target=self._beating, args=(stop,), daemon=True, name="jobs-beat").start()
            for fn in _STARTS:
                try:
                    fn(self)
                except Exception:
                    log.exception("%s failed at worker start", fn.__name__)
            if _URGENT:
                threading.Thread(target=self._loop, args=(stop, False, list(_URGENT)), daemon=True,
                                 name="jobs-urgent").start()
        self._loop(stop, until_idle)

    def _beating(self, stop: threading.Event) -> None:
        """Beats while the worker runs, even through a long job on both lanes."""
        while not stop.wait(self.tuning.jobs.worker_alive_s / BEATS_PER_ALIVE):
            self.beat()

    def _loop(self, stop: threading.Event, until_idle: bool, kinds: list[str] | None = None) -> None:
        """The main loop (every kind, the daily and timed jobs), or the urgent lane (`kinds` only)."""
        while not stop.is_set():
            try:
                if kinds is None and not until_idle:
                    if time.monotonic() - self._daily_checked >= self.daily_check_s:
                        self.schedule_daily()
                        self._daily_checked = time.monotonic()
                    if time.monotonic() - self._periodic_checked >= self.tuning.jobs.periodic_s:
                        self.run_periodic()
                        self._periodic_checked = time.monotonic()
                job = self.run_once(kinds=kinds)
            except Exception:
                log.exception("the job loop failed; trying again in %s s", self.error_wait_s)
                stop.wait(self.error_wait_s)
                continue
            self.between_jobs()
            if job is None:
                if until_idle:
                    return
                stop.wait(self.idle_s if kinds is None else self.urgent_idle_s)


@handler("jobs.cleanup", daily=True)
def _cleanup(ctx: JobContext, payload: dict) -> None:
    """Finished jobs older than Tuning jobs.keep_done_days go."""
    ctx.queue.delete_finished_before(ctx.now - timedelta(days=ctx.tuning.jobs.keep_done_days))

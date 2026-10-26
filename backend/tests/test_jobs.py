"""The job table and worker (from packsure e97a509): each job runs once, a dead worker's job runs again, a late worker
cannot touch a job it lost, a deferred job keeps its attempts, and the worker beats and puts worker_up."""
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from watchful import jobs as jobs_module
from watchful.config import Settings
from watchful.db.models import Job, utcnow
from watchful.db.session import make_engine, migrate
from watchful.jobs import Defer, JobQueue, Worker, handler, worker_alive
from watchful.metrics import start_heartbeat
from watchful.tuning import load_tuning

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def queue(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    migrate(engine)
    return JobQueue(engine)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, session_secret="s", data_dir=tmp_path / "data",
                    database_url=f"sqlite:///{tmp_path / 'jobs.db'}")


ran: list[tuple[str, dict]] = []


@handler("test.record")
def _record(ctx, payload):
    ran.append((ctx.job.id, payload))


@handler("test.wait")
def _wait(ctx, payload):
    if payload.get("until") and ctx.now.timestamp() < payload["until"]:
        raise Defer(payload["until"] - ctx.now.timestamp())
    ran.append((ctx.job.id, payload))


def get(queue, job_id) -> Job:
    with queue.tx() as s:
        return s.get(Job, job_id)


def test_job_queue_runs_each_job_once(queue):
    queued = {queue.enqueue("test.record", {"n": i}).id for i in range(40)}
    queue.enqueue("later", {}, run_after=utcnow() + timedelta(hours=1))
    claimed = []

    def worker():
        while (job := queue.next_job()) is not None:
            claimed.append(job.id)
            queue.job_done(job.id)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(queued)
    assert queue.next_job() is None

    stuck = queue.enqueue("test.record", {})
    assert queue.next_job(lease_s=60).id == stuck.id
    assert queue.next_job(lease_s=60) is None
    retried = queue.next_job(now=utcnow() + timedelta(seconds=61))
    assert retried.id == stuck.id and retried.attempts == 2


def test_job_that_dies_on_its_last_attempt_fails(queue):
    job = queue.enqueue("test.record", {})
    assert queue.next_job(lease_s=60, max_attempts=1).id == job.id
    assert queue.next_job(now=utcnow() + timedelta(seconds=61), max_attempts=1) is None
    dead = get(queue, job.id)
    assert dead.status == "failed" and dead.error

    long = queue.enqueue("export", {})
    assert queue.next_job(lease_s=60).id == long.id
    assert queue.extend_lease(long.id, 600)
    assert queue.next_job(now=utcnow() + timedelta(seconds=61)) is None
    assert not queue.extend_lease("job_unknown", 60)
    queue.job_failed("job_unknown", "boom")


def test_worker_whose_lease_ran_out_cannot_touch_the_job(queue):
    job = queue.enqueue("test.record", {})
    late = queue.next_job(lease_s=60)
    now = queue.next_job(now=utcnow() + timedelta(seconds=61), lease_s=60)
    assert late.id == now.id == job.id and (late.attempts, now.attempts) == (1, 2)
    assert not queue.extend_lease(job.id, 600, attempts=late.attempts)
    queue.job_failed(job.id, "late worker", attempts=late.attempts)
    queue.job_done(job.id, attempts=late.attempts)
    queue.job_deferred(job.id, utcnow(), attempts=late.attempts)
    assert (get(queue, job.id).status, get(queue, job.id).error) == ("running", None)
    assert queue.extend_lease(job.id, 600, attempts=now.attempts)
    queue.job_done(job.id, attempts=now.attempts)
    assert get(queue, job.id).status == "done"


def test_keyed_jobs_and_deferral(queue, settings):
    t0 = utcnow()
    first = queue.enqueue("test.wait", {"until": (t0 + timedelta(seconds=30)).timestamp()}, t0, key="once|a")
    assert first is not None and queue.enqueue("test.wait", {}, t0, key="once|a") is None
    worker = Worker(queue, settings, load_tuning())
    ran.clear()
    assert worker.run_once(now=t0).id == first.id
    deferred = get(queue, first.id)
    assert (deferred.status, deferred.attempts) == ("queued", 0)
    assert deferred.run_after == t0 + timedelta(seconds=30)
    assert worker.run_once(now=t0 + timedelta(seconds=29)) is None
    assert worker.run_once(now=t0 + timedelta(seconds=30)).id == first.id and len(ran) == 1
    assert get(queue, first.id).status == "done"

    assert sorted(worker.schedule_daily(t0)) == ["demo_reset", "incidents.retention", "jobs.cleanup", "plan_sweep"] and \
        worker.schedule_daily(t0) == []
    worker.run_due(t0)
    old = queue.enqueue("test.record", {}, t0 - timedelta(days=10))
    with queue.tx() as s:
        row = s.get(Job, old.id)
        row.created_at, row.status = t0 - timedelta(days=10), "done"
    assert queue.delete_finished_before(t0 - timedelta(days=7)) == 1


@handler("test.urgent_boom", urgent=True)
def _urgent_boom(ctx, payload):
    raise RuntimeError("Telegram took it but the answer was lost")


def test_a_failed_urgent_job_is_tried_again_in_seconds(queue, settings):
    """M-d: an urgent job (an alert being sent) is not left for a minute after a failure."""
    tuning = load_tuning()
    urgent = queue.enqueue("test.urgent_boom", {})
    plain = queue.enqueue("test.plain_boom", {})
    worker = Worker(queue, settings, tuning)
    worker.run_once(kinds=["test.urgent_boom"])
    worker.run_once(kinds=["test.plain_boom"])
    soon = (get(queue, urgent.id).run_after - utcnow()).total_seconds()
    late = (get(queue, plain.id).run_after - utcnow()).total_seconds()
    assert get(queue, urgent.id).status == "queued" and soon <= tuning.jobs.urgent_retry_s
    assert late > tuning.jobs.urgent_retry_s * 2  # an unknown kind fails like any job: a minute


def test_worker_keeps_running_after_a_database_error(queue, settings):
    class Flaky(JobQueue):
        calls = 0

        def next_job(self, **kw):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise OperationalError("UPDATE jobs", {}, Exception("database is locked"))
            return super().next_job(**kw)

    flaky = Flaky(queue.engine)
    job = flaky.enqueue("test.record", {"n": 1})
    Worker(flaky, settings, load_tuning(), error_wait_s=0).run(until_idle=True)
    assert Flaky.calls == 3  # the error, the job, then nothing due
    assert get(queue, job.id).status == "done"
    unknown = flaky.enqueue("no.such.kind", {})
    Worker(flaky, settings, load_tuning()).run_once()
    assert "no handler" in get(queue, unknown.id).error


def test_worker_beats_while_it_runs(queue, settings):
    tuning = load_tuning(overrides={"jobs": {"worker_alive_s": 0.5, "idle_s": 0.05}})
    stop = threading.Event()
    assert not worker_alive(settings.data_dir, 0.5)
    t = threading.Thread(target=Worker(queue, settings, tuning).run, args=(stop,), daemon=True)
    t.start()
    end = time.monotonic() + 5
    while not worker_alive(settings.data_dir, 0.5) and time.monotonic() < end:
        time.sleep(0.05)
    time.sleep(0.8)
    assert worker_alive(settings.data_dir, 0.5)
    stop.set()
    t.join(5)
    time.sleep(0.6)
    assert not worker_alive(settings.data_dir, 0.5)


def test_heartbeat_puts_worker_up_without_dimensions():
    points = []
    metrics = SimpleNamespace(put=lambda name, value, dims=None, unit="None": points.append((name, value, dims, unit)),
                              flush=lambda: None)
    stop = threading.Event()
    thread = start_heartbeat(metrics, "worker_up", 0.05, stop=stop)
    time.sleep(0.2)
    stop.set()
    thread.join(2)
    assert len(points) >= 2 and set(points) == {("worker_up", 1, None, "Count")}


def test_the_job_worker_watcher_outlives_a_failed_start(monkeypatch, settings):
    from watchful import worker
    monkeypatch.setattr(worker, "WATCH_S", 0.01)
    monkeypatch.setattr(worker, "MAX_RESPAWN_S", 0.05)
    jobs = worker.InAppWorker(settings, load_tuning())
    jobs.process = SimpleNamespace(is_alive=lambda: False, exitcode=-9)  # it died
    tries = []

    def spawn():
        tries.append(time.monotonic())
        if len(tries) < 3:
            raise OSError("too many open files")
        jobs.process = SimpleNamespace(is_alive=lambda: True, exitcode=None)

    monkeypatch.setattr(jobs, "_spawn", spawn)
    watcher = threading.Thread(target=jobs._watch, daemon=True)
    watcher.start()
    end = time.monotonic() + 5
    while len(tries) < 3 and time.monotonic() < end:
        time.sleep(0.01)
    jobs._halt.set()
    watcher.join(2)
    assert len(tries) == 3 and jobs.process.is_alive() and jobs.failures >= 2


def test_in_app_worker_runs_jobs_in_its_own_process_and_stops(queue, settings):
    from watchful.worker import InAppWorker
    job = queue.enqueue("jobs.cleanup", {})
    jobs = InAppWorker(settings, load_tuning(overrides={"jobs": {"idle_s": 0.1}}))
    jobs.start()
    try:
        end = time.monotonic() + 60
        while get(queue, job.id).status != "done" and time.monotonic() < end:
            time.sleep(0.2)
        assert get(queue, job.id).status == "done" and jobs.process.name == "jobs"
        assert worker_alive(settings.data_dir, 60)
    finally:
        jobs.join(10)
    assert not jobs.process.is_alive()


def test_worker_command_runs_due_jobs(queue, settings, tmp_path):
    job = queue.enqueue("jobs.cleanup", {})
    env = {"SESSION_SECRET": "s", "DATA_KEY": os.environ["DATA_KEY"], "DATA_DIR": str(tmp_path / "data"),
           "DATABASE_URL": f"sqlite:///{tmp_path / 'jobs.db'}", "PYTHONPATH": str(ROOT / "backend"),
           "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-m", "watchful.worker", "--until-idle"], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert get(queue, job.id).status == "done"
    assert jobs_module.HANDLER_MODULES == ("watchful.alerts.incidents", "watchful.alerts.telegram",
                                           "watchful.alerts.summary", "watchful.billing.jobs", "watchful.demo")

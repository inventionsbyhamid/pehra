import json
import subprocess
import time

import pytest

from accounts import society_repo
from watchful.agent.llm import FakeLLM
from watchful.components import make_blobs
from watchful.config import Settings
from watchful.rules.presets import preset
from watchful.store.db import Camera, WatchRow
from watchful.tuning import Tuning
from watchful.vision.models import sample_path
from watchful.vision.zones import Zone
from watchful.workers import WorkerManager

LEFT = Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])
ALERT = json.dumps({"tool": "raise_alert", "args": {"summary": "Someone came in."}, "why": "."})


@pytest.fixture(scope="module")
def clip20():
    out = sample_path("vtest20.mp4")
    if not out.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sample_path("vtest.avi")), "-t", "20",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture
def manager(tmp_path):
    s = Settings(demo_password="x", session_secret="y", data_dir=tmp_path, demo_realtime=False, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    repo = society_repo(s)
    m = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=FakeLLM([ALERT] * 200))
    yield m, repo
    m.shutdown()


def wait(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.5)
    return False


def test_demo_camera_alerts_once_per_moment(manager, clip20):
    m, repo = manager
    cam = repo.add_camera(Camera(None, "Plaza", "demo", str(clip20), [LEFT]))
    repo.add_watch(WatchRow(None, cam.id, "someone enters the left side",
                            preset("enters", zone="left").model_dump(), True))
    q = m.subscribe(cam.id)
    m.start(cam.id)
    assert wait(lambda: m.status(cam.id).loops >= 1 and repo.list_alerts(cam.id), 90)
    assert wait(lambda: m.status(cam.id).loops >= 2, 90)
    time.sleep(3)
    alerts = repo.list_alerts(cam.id, limit=500)
    assert alerts and all(a.clip_key and a.status == "alert" for a in alerts)
    assert len({a.fingerprint for a in alerts}) == len(alerts)
    kinds, frame = set(), None
    while not q.empty():
        msg = q.get_nowait()
        kinds.add(msg["type"])
        frame = msg if msg["type"] == "frame" else frame
    assert "frame" in kinds and "alert" in kinds
    assert frame["width"] > 0 and isinstance(frame["tracks"], list)
    m.stop(cam.id)
    assert wait(lambda: not m.status(cam.id).running, 10)


def test_upload_scan_creates_alerts(manager, clip20):
    m, repo = manager
    cam = repo.add_camera(Camera(None, "Upload", "file", str(clip20), [LEFT]))
    repo.add_watch(WatchRow(None, cam.id, "enters left", preset("enters", zone="left").model_dump(), True))
    scan_id = m.scan_upload(cam.id)
    assert wait(lambda: m.scan(scan_id)["state"] == "done", 120)
    info = m.scan(scan_id)
    assert info["alert_ids"] and info["stats"]["x_realtime"] > 0
    assert repo.get_alert(info["alert_ids"][0]).summary == "Someone came in."


def test_a_critical_candidate_is_stored_even_if_checking_crashes(manager, monkeypatch):
    import watchful.workers as workers
    from watchful.evidence import EvidencePack
    from watchful.rules.engine import Candidate
    m, repo = manager

    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(workers, "verify", boom)
    c = Candidate("w1", "cam", 10.0, 1, "critical", {"reasons": ["fell in the kitchen"]}, [], "fpx", "Fall", "?")
    m._verify_and_store(EvidencePack(c, [], []))
    a = repo.list_alerts("cam")[0]
    assert a.status == "unverified" and "fell in the kitchen" in a.summary


def test_crashed_camera_is_restarted_and_bad_watches_are_skipped(manager, clip20):
    from watchful.rules.watch import Condition, Watch
    m, repo = manager
    m.supervise_every_s = 0.5
    cam = repo.add_camera(Camera(None, "Plaza", "demo", str(clip20), [LEFT]))
    broken = Watch(name="old zone", verify="?", conditions=[Condition(signal="entered", zones=["gone"])])
    repo.add_watch(WatchRow(None, cam.id, "x", broken.model_dump(), True))
    m.start(cam.id)
    assert wait(lambda: m.status(cam.id).source_status == "live", 30)
    assert "old zone" in " ".join(m.status(cam.id).problems)
    first = m.handles[cam.id].process.pid
    m.handles[cam.id].process.kill()
    assert wait(lambda: m.handles[cam.id].process.pid != first and m.status(cam.id).running, 30)


def test_rescanning_an_upload_replaces_its_timeline(manager, clip20, tmp_path):
    import subprocess as sp
    m, repo = manager
    night = tmp_path / "night.mp4"
    sp.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip20), "-c", "copy",
            "-metadata", "creation_time=2026-10-20T23:30:00Z", str(night)], check=True)
    cam = repo.add_camera(Camera(None, "Upload", "file", str(night), [LEFT]))
    repo.add_watch(WatchRow(None, cam.id, "enters left", preset("enters", zone="left").model_dump(), True))
    first = m.scan_upload(cam.id)
    assert wait(lambda: m.scan(first)["state"] == "done", 120)
    events, alerts = len(repo.query_events(camera_id=cam.id, limit=10000)), len(repo.list_alerts(cam.id, 500))
    assert repo.get_camera(cam.id).last_scan["start_epoch"] == 1792539000.0
    second = m.scan_upload(cam.id)
    assert wait(lambda: m.scan(second)["state"] == "done", 120)
    assert len(repo.query_events(camera_id=cam.id, limit=10000)) == events
    assert len(repo.list_alerts(cam.id, 500)) == alerts and m.scan(second)["alert_ids"] == []


def test_tamper_becomes_an_alert(manager):
    m, repo = manager
    m._handle({"type": "tamper", "camera_id": "camT", "ts": 100.0, "kind": "covered", "detail": "flat"})
    a = repo.list_alerts("camT")[0]
    assert a.status == "alert" and "covered" in a.summary and a.severity == "warning"

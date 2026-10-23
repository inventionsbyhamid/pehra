import json
import subprocess
import time

import pytest

from watchful.agent.llm import FakeLLM
from watchful.components import make_blobs, make_repo
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
    s = Settings(demo_password="x", session_secret="y", data_dir=tmp_path, demo_realtime=False, _env_file=None)
    repo = make_repo(s)
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
    kinds = set()
    while not q.empty():
        kinds.add(q.get_nowait()["type"])
    assert "frame" in kinds and "alert" in kinds
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

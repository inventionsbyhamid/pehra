import subprocess
import time

import numpy as np
import pytest

from watchful.ingest.clips import ClipRecorder
from watchful.ingest.sources import Frame
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import AlertRow, Camera, EventRow, SqliteRepository, WatchRow
from watchful.tuning import ClipTuning
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "w.db")


def event(cam, ts, zone="kitchen", kind="enter", cls="person"):
    return EventRow(None, cam, ts, kind, 1, cls, zone, {"dwell_s": 3}, "k.jpg", [0.1] * 4, None, None)


def test_camera_and_watch_round_trip(repo):
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x", [Zone("z1", "gate", [(0, 0), (1, 0), (1, 1)])],
                                 True, {"falls": {"hold_s": 3}}))
    got = repo.get_camera(cam.id)
    assert got.zones[0].name == "gate" and got.tuning == {"falls": {"hold_s": 3}}
    got.name = "Front gate"
    repo.update_camera(got)
    assert [c.name for c in repo.list_cameras()] == ["Front gate"]
    w = repo.add_watch(WatchRow(None, cam.id, "car enters", {"name": "x"}, True))
    w.enabled = False
    repo.update_watch(w)
    assert repo.list_watches(cam.id)[0].enabled is False


def test_query_events_filters(repo):
    repo.add_events([event("c1", 10), event("c1", 20, zone="door"), event("c1", 30), event("c2", 15)])
    rows = repo.query_events(camera_id="c1", zone="kitchen", since=5, until=25)
    assert [r.ts for r in rows] == [10]
    assert rows[0].data == {"dwell_s": 3} and rows[0].signature == [0.1] * 4
    assert len(repo.query_events(kinds=["enter"])) == 4


def test_alerts_and_fingerprint_window(repo):
    now = time.time()
    a = repo.add_alert(AlertRow(None, "c1", "w1", now, "critical", "alert", "fell", None, None, [], None, "fp1"))
    assert repo.alert_fingerprint_exists("fp1", within_s=60)
    assert not repo.alert_fingerprint_exists("fp2", within_s=60)
    a.feedback = "up"
    repo.update_alert(a)
    assert repo.list_alerts(camera_id="c1")[0].feedback == "up"


def test_enrollments_and_ai_calls(repo):
    repo.add_enrollment(Enrollment("p1", "Sana", [0.5] * 128))
    assert repo.list_enrollments()[0].name == "Sana"
    repo.delete_enrollment("p1")
    assert repo.list_enrollments() == []
    repo.record_ai_call()
    repo.record_ai_call()
    assert repo.count_ai_calls_today() == 2


def test_blobs(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    blobs.put("a/b.jpg", b"123", "image/jpeg")
    assert blobs.get("a/b.jpg") == b"123" and blobs.url("a/b.jpg") == "/blobs/a/b.jpg"
    old = tmp_path / "blobs" / "a" / "b.jpg"
    past = time.time() - 9 * 86400
    import os
    os.utime(old, (past, past))
    assert blobs.delete_older_than(7) == 1


def test_clip_written_and_playable(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    rec = ClipRecorder(blobs, fps=5, cfg=ClipTuning(before_s=5, after_s=5))
    for i in range(80):
        img = np.full((240, 320, 3), (i * 3) % 255, np.uint8)
        rec.on_frame(Frame(img, 100 + i * 0.2, i))
        if i == 40:
            rec.request(108.0, "clips/x.mp4")
    rec.close()
    path = tmp_path / "blobs" / "clips" / "x.mp4"
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name:format=duration", "-of", "default=nw=1", str(path)],
                         capture_output=True, text=True).stdout
    assert "codec_name=h264" in out
    duration = float(out.split("duration=")[1].split()[0])
    assert 9 <= duration <= 11

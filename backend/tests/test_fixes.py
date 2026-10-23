"""Gaps found while building the web app."""
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from test_api import StubManager, login, watch_reply
from watchful.agent.ask import AskContext, people_groups
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import AlertRow, Camera, EventRow, SqliteRepository
from watchful.vision.track import Tracker
from watchful.workers import should_record


def app_client(tmp_path):
    s = Settings(demo_password="pw", session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    app = create_app(s, llm=FakeLLM([watch_reply()]), manager=StubManager())
    c = TestClient(app)
    login(c)
    return app, c


def test_track_ids_differ_between_sessions():
    a, b = Tracker(), Tracker()
    assert abs(a._next_id - b._next_id) >= 0 and a._next_id > 1_000_000


def test_demo_records_once(tmp_path):
    repo = SqliteRepository(tmp_path / "w.db")
    cam = repo.add_camera(Camera(None, "Plaza", "demo", "x.mp4"))
    assert should_record(repo, cam)
    repo.add_events([EventRow(None, cam.id, 1.0, "enter", 1, "person", "z", {}, None, None, None, None)])
    assert not should_record(repo, cam)
    assert should_record(repo, repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x")))


def test_zone_change_clears_a_demo_timeline(tmp_path):
    app, c = app_client(tmp_path)
    repo = app.state.w.repo
    cam = repo.add_camera(Camera(None, "Plaza", "demo", "x.mp4"))
    repo.add_events([EventRow(None, cam.id, 1.0, "enter", 1, "person", "z", {}, None, None, None, None)])
    zones = [{"name": "left", "polygon": [[0, 0], [1, 0], [1, 1]]}]
    assert c.patch(f"/api/cameras/{cam.id}", json={"zones": zones}).status_code == 200
    assert repo.query_events(camera_id=cam.id) == []


def test_people_groups_list_their_tracks(tmp_path):
    repo = SqliteRepository(tmp_path / "w.db")
    repo.add_events([EventRow(None, "cam", t, k, 7, "person", None, {}, None, None, None, "Sana")
                     for t, k in ((10.0, "track_start"), (40.0, "track_end"))])
    g = people_groups(AskContext(repo, LocalBlobStore(tmp_path), "cam", "Asia/Kolkata"))[0]
    assert g["tracks"] == [{"track_id": 7, "first_ts": 10.0, "last_ts": 40.0}]


def test_get_and_delete_camera(tmp_path):
    app, c = app_client(tmp_path)
    repo = app.state.w.repo
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    assert c.get(f"/api/cameras/{cam['id']}").json()["name"] == "Gate"
    repo.add_alert(AlertRow(None, cam["id"], None, 1.0, "info", "alert", "x", None, None, [], None, "f"))
    assert c.delete(f"/api/cameras/{cam['id']}").status_code == 200
    assert c.get(f"/api/cameras/{cam['id']}").status_code == 404 and repo.list_alerts(cam["id"]) == []


def test_lookout_from_an_alert(tmp_path):
    app, c = app_client(tmp_path)
    repo = app.state.w.repo
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    repo.add_events([EventRow(None, cam["id"], 100.0, "track_start", 5, "person", None, {}, "k.jpg", [0.1] * 4,
                              [1.0, 0.0], None)])
    a = repo.add_alert(AlertRow(None, cam["id"], None, 103.0, "info", "alert", "x", None, None, [], None, "f",
                                track_id=5))
    r = c.post("/api/lookouts", json={"camera_ids": [cam["id"]], "alert_id": a.id})
    assert r.status_code == 200 and r.json()["watch_ids"]


def test_enrolment_photo_kept_by_retention_and_removed_on_delete(tmp_path):
    app, c = app_client(tmp_path)
    blobs = app.state.w.blobs
    from watchful.vision.models import sample_path
    photo = cv2.imencode(".jpg", cv2.imread(str(sample_path("basketball1.png"))))[1].tobytes()
    pid = c.post("/api/people", data={"name": "P", "consent": "true"}, files={"photo": ("p.jpg", photo)}).json()["person_id"]
    old = time.time() - 30 * 86400
    import os
    os.utime(tmp_path / "blobs" / "people" / f"{pid}.jpg", (old, old))
    blobs.delete_older_than(7)
    assert blobs.get(f"people/{pid}.jpg")
    c.delete(f"/api/people/{pid}")
    assert not (tmp_path / "blobs" / "people" / f"{pid}.jpg").exists()

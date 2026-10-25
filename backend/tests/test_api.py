import json
import subprocess

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from accounts import SOCIETY, browser
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings
from watchful.store.db import AlertRow, EventRow
from watchful.vision.models import sample_path
from watchful.workers import Subscription, WorkerStatus


class StubManager:
    def __init__(self):
        self.started, self.scans, self.subs = [], {}, {}

    def start(self, camera_id, workspace_id=None):
        self.started.append(camera_id)

    def stop(self, camera_id):
        pass

    def restart(self, camera_id):
        self.started.append(camera_id)

    def status(self, camera_id):
        return WorkerStatus(running=camera_id in self.started, source_status="live", fps=5.0)

    def subscribe(self, camera_id):
        sub = Subscription()
        jpg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
        sub.put({"type": "frame", "camera_id": camera_id, "jpeg": jpg, "ts": 1.0})
        self.subs[camera_id] = sub
        return sub

    def unsubscribe(self, camera_id, sub):
        pass

    def push_frame(self, camera_id, jpeg, ts):
        pass

    def scan_upload(self, camera_id, workspace_id=None):
        self.scans["scn_1"] = {"state": "scanning", "progress": 0.0, "camera_id": camera_id}
        return "scn_1"

    def scan(self, scan_id):
        return self.scans.get(scan_id)

    def approve(self, alert_id, workspace_id=None):
        return True

    def shutdown(self):
        pass


def watch_reply():
    return json.dumps({"watch": {"name": "Enters left", "subject": {"classes": ["person"]},
                                 "conditions": [{"signal": "entered", "zones": ["left"]}], "verify": "?"},
                       "problem": None})


@pytest.fixture
def app_and_client(tmp_path):
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, max_upload_mb=1, _env_file=None)
    app = create_app(s, llm=FakeLLM([watch_reply()]), manager=StubManager())
    return app, browser(app)


def test_login_required(app_and_client):
    app, _ = app_and_client
    c = TestClient(app)
    assert c.get("/api/cameras").status_code == 401
    assert c.post("/api/login", json={"user": "judge", "password": "pw"}).status_code in (404, 405)
    assert c.get("/healthz").json()["opencv"].startswith("5.")


def test_cameras_and_watches(app_and_client):
    app, c = app_and_client
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    assert [x["name"] for x in c.get("/api/cameras").json()] == ["Gate"]
    zones = [{"id": "z1", "name": "left", "polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]}]
    assert c.patch(f"/api/cameras/{cam['id']}", json={"zones": zones}).status_code == 200
    r = c.post(f"/api/cameras/{cam['id']}/watches", json={"text": "tell me when someone enters the left side"})
    assert r.status_code == 200 and r.json()["watch"]["conditions"][0]["signal"] == "entered"
    assert len(c.get(f"/api/cameras/{cam['id']}/watches").json()) == 1


def test_upload_rejects_non_video_and_big_files(app_and_client):
    _, c = app_and_client
    r = c.post("/api/uploads", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400 and "not a video" in r.json()["detail"]
    r = c.post("/api/uploads", files={"file": ("big.mp4", b"0" * (2 * 1024 * 1024), "video/mp4")})
    assert r.status_code == 400 and "200 MB" not in r.json()["detail"] and "1 MB" in r.json()["detail"]


def test_upload_valid_starts_scan(app_and_client, tmp_path):
    _, c = app_and_client
    clip = tmp_path / "tiny.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sample_path("vtest.avi")), "-t", "2", "-vf",
                    "scale=320:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    r = c.post("/api/uploads", files={"file": ("tiny.mp4", clip.read_bytes(), "video/mp4")})
    assert r.status_code == 200 and r.json()["scan_id"] == "scn_1"
    assert c.get("/api/scans/scn_1").json()["state"] == "scanning"


def test_alerts_feedback_and_ask(app_and_client):
    app, c = app_and_client
    repo = app.state.ctx.repo_for(SOCIETY)
    a = repo.add_alert(AlertRow(None, "cam", "w", 1.0, "info", "alert", "Someone came in.", None, None, [], None, "f"))
    assert c.get("/api/alerts").json()[0]["summary"] == "Someone came in."
    assert c.post(f"/api/alerts/{a.id}/feedback", json={"value": "up"}).status_code == 200
    assert repo.get_alert(a.id).feedback == "up"
    app.state.ctx.llm = FakeLLM([json.dumps({"tool": "answer", "why": ".",
                                               "args": {"text": "Nobody came in.", "items": [], "confidence": "high"}})])
    r = c.post("/api/ask", json={"question": "who came in?"})
    assert r.status_code == 200 and r.json()["text"] == "Nobody came in."


def test_people_need_consent(app_and_client):
    _, c = app_and_client
    photo = cv2.imencode(".jpg", cv2.imread(str(sample_path("basketball1.png"))))[1].tobytes()
    r = c.post("/api/people", data={"name": "Player", "consent": "false"}, files={"photo": ("p.jpg", photo)})
    assert r.status_code == 400 and "consent" in r.json()["detail"]
    r = c.post("/api/people", data={"name": "Player", "consent": "true"}, files={"photo": ("p.jpg", photo)})
    assert r.status_code == 200 and [p["name"] for p in c.get("/api/people").json()] == ["Player"]


def test_websocket_sends_frames(app_and_client):
    _, c = app_and_client
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    with c.websocket_connect(f"/ws/cameras/{cam['id']}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "frame" and msg["jpeg_b64"]


def test_search_endpoint(app_and_client):
    import numpy as np

    class Stub:
        def text(self, q):
            return np.array([1.0, 0.0], np.float32)

        def image(self, crop):
            return np.array([1.0, 0.0], np.float32)

    app, c = app_and_client
    app.state.ctx._embedder = Stub()
    app.state.ctx.llm = FakeLLM([])
    r = c.post("/api/search", json={"text": "a red car"})
    assert r.status_code == 200 and r.json()["results"] == [] and r.json()["query"]["looks_like"] == "a red car"
    assert c.post("/api/search", json={}).status_code == 400

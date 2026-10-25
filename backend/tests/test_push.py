"""Cameras that send their own frames: device tokens (one camera of one society, shown once, stored hashed,
rotatable, closing open sockets at once), the /cam page, the bridge, and the Task 2 review fixes on the camera routes
and sockets (only user kinds, private stream addresses refused, admin only push, audited edits, removed members
dropped from open sockets)."""
import importlib.util
import json
import logging
import re
import socket
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
import uvicorn
from sqlalchemy import delete, select
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from accounts import join, session_cookie
from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings
from watchful.db import models as m
from watchful.ingest.push import hash_token, qr_svg
from watchful.store.db import Camera, WatchRow
from watchful.tuning import load_tuning

ROOT = Path(__file__).resolve().parents[2]
A, B = "ws_green_park", "ws_lake_view"
JPEG = cv2.imencode(".jpg", np.full((48, 64, 3), 128, np.uint8))[1].tobytes()


def load_bridge():
    spec = importlib.util.spec_from_file_location("pehra_bridge", ROOT / "bridge" / "pehra_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingManager(StubManager):
    def __init__(self):
        super().__init__()
        self.frames: list[tuple[str, int]] = []
        self.stopped: list[str] = []

    def push_frame(self, camera_id, jpeg, ts):
        self.frames.append((camera_id, len(jpeg)))

    def stop(self, camera_id):
        self.stopped.append(camera_id)

    def subscribe(self, camera_id):
        from watchful.workers import Subscription
        sub = Subscription()
        self.subs[camera_id] = sub
        return sub

    def count(self, camera_id):
        return sum(1 for c, _ in self.frames if c == camera_id)


class Server:
    """The app under uvicorn on a fixed port, which can be stopped and started again (the bridge test)."""

    def __init__(self, tmp_path, recheck_s=15.0):
        dist = tmp_path / "dist"
        dist.mkdir(exist_ok=True)
        (dist / "app.html").write_text("<html>the app shell</html>")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.host = f"127.0.0.1:{self.port}"
        s = Settings(session_secret="s" * 32, data_dir=tmp_path, web_dist=dist, public_base_url=f"http://{self.host}",
                     _env_file=None)
        self.manager = RecordingManager()
        self.app = create_app(s, llm=FakeLLM([]), manager=self.manager,
                              tuning=load_tuning(None, {"push": {"recheck_s": recheck_s, "max_fps": 100}}))
        self.ctx = self.app.state.ctx
        self.srv = None
        self.start()

    def start(self):
        self.srv = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                                 lifespan="off"))
        threading.Thread(target=self.srv.run, daemon=True).start()
        deadline = time.time() + 10
        while not self.srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert self.srv.started

    def stop(self):
        self.srv.should_exit = True
        deadline = time.time() + 10
        while self.srv.started and time.time() < deadline and not getattr(self.srv, "_stopped", False):
            if not any(t.is_alive() for t in threading.enumerate() if t.name.startswith("uvicorn")):
                break
            time.sleep(0.05)
        time.sleep(0.5)

    def client(self, email="asha@example.com", workspace_id=A, role="owner"):
        name, value = session_cookie(self.app, join(self.app, email, workspace_id, role), workspace_id)
        c = httpx.Client(base_url=f"http://{self.host}", headers={"origin": f"http://{self.host}"},
                         cookies={name: value})
        return c, f"{name}={value}"


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path)
    yield srv
    srv.srv.should_exit = True


def closed_with(ws, timeout=5.0) -> int:
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            ws.recv(timeout=max(0.01, deadline - time.time()))
    except ConnectionClosed as e:
        return e.rcvd.code if e.rcvd else -1
    except TimeoutError:
        pass
    raise AssertionError("the socket stayed open")


def device_camera(c, name="Pool"):
    cam = c.post("/api/cameras", json={"name": name, "kind": "webcam", "purpose": "pool"}).json()
    issued = c.post(f"/api/cameras/{cam['id']}/device-token")
    assert issued.status_code == 200, issued.text
    return cam, issued.json()


def wait_for(fn, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    return False


# ---- device tokens ----

def test_a_token_pushes_frames_to_its_own_camera_only(server):
    a, _ = server.client()
    b, _ = server.client("bina@example.com", B)
    pool, issued = device_camera(a, "Pool")
    lake, lake_issued = device_camera(b, "Lake gate")
    token = issued["token"]
    assert issued["url"] == f"http://{server.host}/cam/{token}" and len(token) >= 32

    with server.ctx.db.tx() as s:  # only the hash is kept
        rows = s.scalars(select(m.DeviceToken)).all()
    assert {r.token_hash for r in rows} == {hash_token(token), hash_token(lake_issued["token"])}
    assert all(token not in json.dumps(r.__dict__, default=str) for r in rows)

    info = httpx.get(f"http://{server.host}/cam/{token}/info").json()
    assert info["camera"] == "Pool" and info["society"] == A and info["blur"] is True
    assert info["send"]["fps"] > 0 and info["retention_days"] == 7

    with connect(f"ws://{server.host}/ws/cam/{token}") as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert hello == {"type": "hello", "camera": "Pool", "fps": 5.0, "width": 960, "quality": 0.72, "blur": True}
        for _ in range(5):
            ws.send(JPEG)
            time.sleep(0.02)
        ws.send(b"not a picture")
        ws.send(b"\xff\xd8" + b"\0" * (1024 * 1024 + 1))  # over max_frame_kb
        assert wait_for(lambda: server.manager.count(pool["id"]) == 5)
        assert pool["id"] in server.manager.started
        # the live view on another device sees the phone's picture; the camera tile gets it too
        _, cookie = server.client()
        with connect(f"ws://{server.host}/ws/cameras/{pool['id']}", additional_headers={"Cookie": cookie}) as live:
            msg = json.loads(live.recv(timeout=5))
            assert msg["type"] == "frame" and msg["jpeg_b64"]
        assert a.get(f"/api/cameras/{pool['id']}/frame").content == JPEG
        assert a.get(f"/api/cameras/{pool['id']}/device-token").json()["sending"] is True
    assert server.manager.count(lake["id"]) == 0
    assert wait_for(lambda: pool["id"] in server.manager.stopped)  # nobody sends it any more

    # a token names its camera; there is no way to point it at another society's camera
    assert b.get(f"/api/cameras/{pool['id']}/device-token").status_code == 404
    assert b.post(f"/api/cameras/{pool['id']}/device-token").status_code == 404
    with connect(f"ws://{server.host}/ws/cam/{token}?camera={lake['id']}") as ws:
        json.loads(ws.recv(timeout=5))
        ws.send(JPEG)
    assert wait_for(lambda: server.manager.count(pool["id"]) == 6) and server.manager.count(lake["id"]) == 0


def test_a_rotated_token_stops_working(server):
    a, _ = server.client()
    pool, first = device_camera(a)
    with connect(f"ws://{server.host}/ws/cam/{first['token']}") as ws:
        ws.recv(timeout=5)
        ws.send(JPEG)
        started = time.time()
        second = a.post(f"/api/cameras/{pool['id']}/device-token").json()
        assert closed_with(ws) == 4410 and time.time() - started < 2  # closed at once, not at the next check
    assert second["token"] != first["token"]
    with connect(f"ws://{server.host}/ws/cam/{first['token']}") as ws:
        assert closed_with(ws) == 4404
    assert httpx.get(f"http://{server.host}/cam/{first['token']}/info").status_code == 404
    with connect(f"ws://{server.host}/ws/cam/{second['token']}") as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "hello"

        # turning the camera off closes it too, and the device is told why
        assert a.patch(f"/api/cameras/{pool['id']}", json={"enabled": False}).status_code == 200
        assert closed_with(ws) == 4412
    with connect(f"ws://{server.host}/ws/cam/{second['token']}") as ws:
        assert closed_with(ws) == 4412
    a.patch(f"/api/cameras/{pool['id']}", json={"enabled": True})

    # removing the link, or deleting the camera, closes the socket at once
    with connect(f"ws://{server.host}/ws/cam/{second['token']}") as ws:
        ws.recv(timeout=5)
        assert a.delete(f"/api/cameras/{pool['id']}/device-token").json()["removed"] is True
        assert closed_with(ws) == 4410
    third = a.post(f"/api/cameras/{pool['id']}/device-token").json()
    with connect(f"ws://{server.host}/ws/cam/{third['token']}") as ws:
        ws.recv(timeout=5)
        assert a.delete(f"/api/cameras/{pool['id']}").status_code == 200
        assert closed_with(ws) == 4410
    with server.ctx.db.tx() as s:
        assert s.scalars(select(m.DeviceToken)).all() == []


def test_a_newer_device_takes_the_camera_over(server):
    a, _ = server.client()
    _, issued = device_camera(a)
    url = f"ws://{server.host}/ws/cam/{issued['token']}"
    with connect(url) as laptop:
        laptop.recv(timeout=5)
        with connect(url) as phone:
            phone.recv(timeout=5)
            assert closed_with(laptop) == 4409
            phone.send(JPEG)


def test_device_links_are_for_admins_and_cameras_that_send(server):
    a, _ = server.client()
    viewer, _ = server.client("vic@example.com", A, "viewer")
    pool = a.post("/api/cameras", json={"name": "Pool", "kind": "webcam"}).json()
    stream = a.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://93.184.215.14/x"}).json()
    assert viewer.post(f"/api/cameras/{pool['id']}/device-token").status_code == 403
    assert viewer.delete(f"/api/cameras/{pool['id']}/device-token").status_code == 403
    assert a.post(f"/api/cameras/{stream['id']}/device-token").status_code == 400
    assert viewer.get(f"/api/cameras/{pool['id']}/device-token").json() == {"linked": False, "sending": False}
    issued = a.post(f"/api/cameras/{pool['id']}/device-token")
    assert issued.headers["cache-control"] == "no-store"
    shown = viewer.get(f"/api/cameras/{pool['id']}/device-token").json()
    assert shown["linked"] is True and issued.json()["token"] not in json.dumps(shown)
    assert issued.json()["token"] not in json.dumps(a.get(f"/api/cameras/{pool['id']}").json())
    with server.ctx.db.tx() as s:
        actions = [r.action for r in s.scalars(select(m.Audit).where(m.Audit.workspace_id == A))]
    assert "camera.device_link" in actions
    with connect(f"ws://{server.host}/ws/cam/not-a-real-token-at-all") as ws:
        assert closed_with(ws) == 4404


def test_unknown_tokens_count_as_guesses(tmp_path):
    srv = Server(tmp_path)
    try:
        codes = []
        for i in range(12):
            with connect(f"ws://{srv.host}/ws/cam/guess{i:04d}xxxxxxxx") as ws:
                codes.append(closed_with(ws))
        assert codes[:10] == [4404] * 10 and codes[10:] == [4429, 4429]
        assert httpx.get(f"http://{srv.host}/cam/guess0000xxxxxxxx/info").status_code == 429
    finally:
        srv.srv.should_exit = True


def test_staff_register_cameras_keep_faces(server):
    a, _ = server.client()
    pool, issued = device_camera(a, "Service gate")
    repo = server.ctx.repo_for(A)
    watch = {"name": "Staff arrive", "subject": {"classes": ["person"], "who": "enrolled"},
             "conditions": [{"signal": "entered", "zones": []}]}
    repo.add_watch(WatchRow(None, pool["id"], "log staff arriving", watch, True))
    assert httpx.get(f"http://{server.host}/cam/{issued['token']}/info").json()["blur"] is False


def test_the_device_page_and_its_qr_code(server):
    a, _ = server.client()
    _, issued = device_camera(a)
    page = httpx.get(f"http://{server.host}/cam/{issued['token']}")
    assert page.status_code == 200 and "the app shell" in page.text
    csp = page.headers["content-security-policy"]
    assert "'wasm-unsafe-eval'" in csp and "frame-ancestors 'none'" in csp
    assert page.headers["referrer-policy"] == "no-referrer" and page.headers["cache-control"] == "no-store"
    assert "'unsafe-eval'" not in httpx.get(f"http://{server.host}/cameras/x").headers["content-security-policy"]

    svg = httpx.get(f"http://{server.host}/cam/{issued['token']}/qr.svg")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert decode_svg(svg.text) == issued["url"] == decode_svg(issued["qr_svg"])
    assert httpx.get(f"http://{server.host}/cam/nope-nope-nope-nope/qr.svg").status_code == 404


def decode_svg(svg: str) -> str:
    """Draws our own SVG path back to pixels and reads it with OpenCV's QR detector."""
    n = int(re.search(r'viewBox="0 0 (\d+) ', svg).group(1))
    grid = np.full((n, n), 255, np.uint8)
    for x, y, w in re.findall(r"M(\d+) (\d+)h(\d+)", svg):
        grid[int(y), int(x):int(x) + int(w)] = 0
    big = cv2.resize(grid, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    return cv2.QRCodeDetector().detectAndDecode(big)[0]


def test_qr_codes_read_back():
    for text in ("https://example.com/cam/" + "A" * 32, "http://127.0.0.1:8000/cam/x"):
        assert decode_svg(qr_svg(text)) == text


# ---- the bridge ----

def write_clip(path: Path, frames=40, fps=10):
    if not cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG):
        pytest.skip("this OpenCV build has no FFmpeg (the bridge reads cameras with it)")
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (320, 240))
    for i in range(frames):
        img = np.full((240, 320, 3), 40, np.uint8)
        cv2.circle(img, (20 + i * 7, 120), 20, (255, 255, 255), -1)
        out.write(img)
    out.release()
    return path


def test_the_bridge_reconnects(tmp_path, caplog):
    bridge = load_bridge()
    srv = Server(tmp_path)
    a, _ = srv.client()
    pool, issued = device_camera(a, "Lobby")
    clip = write_clip(tmp_path / "lobby.avi")  # a recording stands in for the RTSP camera
    cam = bridge.Camera(issued["token"], str(clip))
    run = bridge.Bridge(f"http://{srv.host}", [cam], fps=20, backoff=(0.2, 0.4)).start()
    try:
        assert wait_for(lambda: srv.manager.count(pool["id"]) >= 5)
        assert cam.connects == 1
        srv.stop()
        before = srv.manager.count(pool["id"])
        time.sleep(1.0)
        srv.start()
        assert wait_for(lambda: cam.connects == 2, timeout=15)
        assert wait_for(lambda: srv.manager.count(pool["id"]) >= before + 5)

        # a replaced link stops this camera for good, with a message saying what to do
        a.post(f"/api/cameras/{pool['id']}/device-token")
        assert wait_for(lambda: cam.stopped_because is not None)
        assert "replaced" in cam.stopped_because and not run.running()
    finally:
        run.close()
        srv.srv.should_exit = True
    assert issued["token"] not in caplog.text


def test_the_bridge_never_logs_credentials(tmp_path, caplog):
    bridge = load_bridge()
    caplog.set_level(logging.INFO, logger="bridge")
    cam = bridge.parse_camera("Ab3dEf6hIj9kLmNoPqRsTuVwXyZ01234=rtsp://admin:s3cr3t@127.0.0.1:1/Streaming/Channels/102")
    run = bridge.Bridge("http://127.0.0.1:1", [cam], backoff=(0.1, 0.2)).start()
    time.sleep(2.0)
    run.close()
    assert "can't read the camera" in caplog.text and "can't reach the server" in caplog.text
    assert "s3cr3t" not in caplog.text and "admin" not in caplog.text
    assert "Ab3dEf6hIj9kLmNoPqRsTuVwXyZ01234" not in caplog.text and "Ab3d" in caplog.text
    assert bridge.redact("failed rtsp://admin:pw@10.0.0.2/x and http://u:p@h/y") == "failed rtsp://10.0.0.2/x and http://h/y"
    with pytest.raises(ValueError):
        bridge.parse_camera("rtsp://admin:pw@10.0.0.2/x")
    assert bridge.socket_url("https://control.example.com/", "tok") == "wss://control.example.com/ws/cam/tok"
    with pytest.raises(ValueError):
        bridge.socket_url("control.example.com", "tok")


def test_the_bridge_reads_a_cameras_file(tmp_path):
    bridge = load_bridge()
    f = tmp_path / "cameras.txt"
    f.write_text("# the gate\nAb3dEf6hIj9kLmNoPqRsTuVwXyZ01234=rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/102\n"
                 "\nZy9xWv8uTs7rQp6oNm5lKj4iHg3fEd2c=rtsp://admin:pw@192.168.1.64:554/cam/realmonitor?channel=2&subtype=1\n")
    cams = bridge.read_cameras_file(str(f))
    assert [c.token[:4] for c in cams] == ["Ab3d", "Zy9x"] and cams[1].url.endswith("subtype=1")
    assert "pw" not in cams[0].name and "192.168.1.64" in cams[0].name


# ---- Task 2 review fixes on the camera routes and sockets ----

def test_members_add_only_streams_and_devices(server, tmp_path):
    a, _ = server.client()
    secret = tmp_path / "other-society-keyframe.jpg"
    secret.write_bytes(JPEG)
    for body in ({"name": "x", "kind": "demo", "url": str(secret)}, {"name": "x", "kind": "file", "url": str(secret)},
                 {"name": "x", "kind": "stream", "url": "rtsp://127.0.0.1:8000/x"},
                 {"name": "x", "kind": "stream", "url": "http://169.254.169.254/latest/meta-data/"},
                 {"name": "x", "kind": "stream", "url": "rtsp://10.0.0.8/x"},
                 {"name": "x", "kind": "stream", "url": "file:///etc/passwd"},
                 {"name": "x", "kind": "stream", "nvr": {"brand": "hikvision", "host": "192.168.1.64"}},
                 {"name": "x", "kind": "stream", "nvr": {"brand": "sony", "host": "93.184.215.14"}},
                 {"name": "x", "kind": "webcam", "url": "file:///etc/passwd"},
                 {"name": "x", "kind": "webcam", "purpose": "kitchen"}):
        res = a.post("/api/cameras", json=body)
        assert res.status_code == 400, (body, res.text)
    assert a.get("/api/cameras").json() == []

    # an NVR's substream from its template, its password sealed and never shown
    nvr = {"brand": "hikvision", "host": "93.184.215.14", "channel": 3, "user": "admin", "password": "s3cr3t"}
    cam = a.post("/api/cameras", json={"name": "Gate", "kind": "stream", "nvr": nvr}).json()
    assert cam["url"] == "rtsp://admin:***@93.184.215.14:554/Streaming/Channels/302"
    with server.ctx.db.tx() as s:
        sealed = s.scalar(select(m.Camera.url_enc).where(m.Camera.id == cam["id"]))
    assert "s3cr3t" not in sealed
    assert server.ctx.repo_for(A).get_camera(cam["id"]).url.endswith("s3cr3t@93.184.215.14:554/Streaming/Channels/302")

    # a bridge camera keeps its address on the society's network; the server never reads it
    lan = {"brand": "cp_plus", "host": "192.168.1.108", "channel": 2, "user": "admin", "password": "pw"}
    bridged = a.post("/api/cameras", json={"name": "Lobby", "kind": "webcam", "nvr": lan, "purpose": "lobby"}).json()
    assert bridged["kind"] == "webcam" and bridged["purpose"] == "lobby" and bridged["id"] not in server.manager.started
    issued = a.post(f"/api/cameras/{bridged['id']}/device-token").json()
    assert issued["bridge_camera"] == (f"{issued['token']}=rtsp://admin:pw@192.168.1.108:554/cam/realmonitor"
                                       "?channel=2&subtype=1")
    assert {b["id"] for b in a.get("/api/nvr").json()["brands"]} == {"hikvision", "cp_plus", "dahua"}

    # a clip outside the server's own folders is never opened, even when a row says so
    planted = server.ctx.repo_for(A).add_camera(Camera(None, "Planted", "demo", str(secret)))
    assert a.get(f"/api/cameras/{planted.id}/frame").status_code == 404


def test_a_server_inside_the_society_may_pull_private_streams(tmp_path):
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, allow_private_streams=True, _env_file=None)
    app = create_app(s, llm=FakeLLM([]), manager=StubManager())
    from accounts import browser
    c = browser(app)
    assert c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://192.168.1.64/x"}).status_code == 200


def test_demo_clips_come_only_from_the_samples_folder(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    settings = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    monkeypatch.setattr(seed, "get_settings", lambda: settings)
    demo = tmp_path / "demo.json"
    demo.write_text(json.dumps({"cameras": [{"name": "x", "clip": "/etc/passwd", "zones": []}]}))
    with pytest.raises(SystemExit, match="samples folder"):
        seed.main(str(demo), "ws_demo")
    (tmp_path / "samples").mkdir()
    write_clip(tmp_path / "samples" / "plaza.avi", frames=5)
    demo.write_text(json.dumps({"cameras": [{"name": "Plaza", "clip": str(tmp_path / "samples" / "plaza.avi"),
                                             "zones": []}]}))
    seed.main(str(demo), "ws_demo")
    from watchful.components import make_repo
    assert [c.kind for c in make_repo(settings, "ws_demo").list_cameras()] == ["demo"]


def test_only_admins_push_frames(server):
    a, _ = server.client()
    _, viewer = server.client("vic@example.com", A, "viewer")
    _, supervisor = server.client("sam@example.com", A, "supervisor")
    pool = a.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
    for cookie in (viewer, supervisor):
        with connect(f"ws://{server.host}/ws/cameras/{pool['id']}/push", additional_headers={"Cookie": cookie}) as ws:
            assert closed_with(ws) == 4403
    assert server.manager.count(pool["id"]) == 0 and pool["id"] not in server.manager.started
    _, owner = server.client()
    with connect(f"ws://{server.host}/ws/cameras/{pool['id']}/push", additional_headers={"Cookie": owner}) as ws:
        ws.send(JPEG)
        assert wait_for(lambda: server.manager.count(pool["id"]) == 1)


def test_camera_edits_are_audited(server):
    a, _ = server.client()
    cam = a.post("/api/cameras", json={"name": "Gate", "kind": "webcam"}).json()
    zone = {"id": "z1", "name": "gate", "polygon": [[0, 0], [1, 0], [1, 1]]}
    assert a.patch(f"/api/cameras/{cam['id']}", json={"name": "Main gate", "zones": [zone]}).status_code == 200
    assert a.patch(f"/api/cameras/{cam['id']}", json={"enabled": False}).status_code == 200
    added = a.post(f"/api/cameras/{cam['id']}/zones/z1/examples", data={"state": "open"},
                   files={"file": ("open.jpg", JPEG, "image/jpeg")})
    assert added.status_code == 200, added.text
    assert a.delete(f"/api/examples/{added.json()['id']}").status_code == 200
    with server.ctx.db.tx() as s:
        rows = [(r.action, r.data) for r in s.scalars(select(m.Audit).where(m.Audit.workspace_id == A)
                                                      .order_by(m.Audit.ts))]
    edits = [d for action, d in rows if action == "camera.edit"]
    assert [e["changed"] for e in edits] == [["name", "zones"], ["enabled"]] and edits[1]["enabled"] is False
    assert [action for action, _ in rows][-2:] == ["camera.example_add", "camera.example_delete"]
    assert all(r[1].get("camera") == cam["id"] for r in rows if r[0] in ("camera.edit", "camera.example_add"))


def test_a_removed_member_loses_open_sockets(tmp_path):
    srv = Server(tmp_path, recheck_s=0.3)
    try:
        a, _ = srv.client()
        _, viewer = srv.client("vic@example.com", A, "viewer")
        _, admin = srv.client("adi@example.com", A, "admin")
        cam = a.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
        url = f"ws://{srv.host}/ws/cameras/{cam['id']}"
        with connect(url, additional_headers={"Cookie": viewer}) as live, \
                connect(f"{url}/push", additional_headers={"Cookie": admin}) as push:
            push.send(JPEG)
            time.sleep(0.5)
            with srv.ctx.db.tx() as s:
                users = {u.email: u.id for u in s.scalars(select(m.User))}
                s.execute(delete(m.Member).where(m.Member.user_id.in_([users["vic@example.com"],
                                                                       users["adi@example.com"]])))
            assert closed_with(live, 3) == 4401
            assert closed_with(push, 3) == 4401
        # an admin made a viewer can still watch but no longer push
        with srv.ctx.db.tx() as s:
            s.add(m.Member(workspace_id=A, user_id=users["adi@example.com"], role="admin"))
        with connect(f"{url}/push", additional_headers={"Cookie": admin}) as push:
            push.send(JPEG)
            time.sleep(0.4)
            with srv.ctx.db.tx() as s:
                s.get(m.Member, {"workspace_id": A, "user_id": users["adi@example.com"]}).role = "viewer"
            assert closed_with(push, 3) == 4401
    finally:
        srv.srv.should_exit = True

"""The app under a real uvicorn server: WebSockets and single page app routes."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings


@pytest.fixture
def server(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>watchful app</html>")
    s = Settings(demo_password="pw", session_secret="s" * 32, data_dir=tmp_path, web_dist=dist, _env_file=None)
    app = create_app(s, llm=FakeLLM([]), manager=StubManager())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    srv.should_exit = True


def test_live_websocket_over_uvicorn(server):
    with httpx.Client(base_url=f"http://{server}") as c:
        assert c.post("/api/login", json={"user": "judge", "password": "pw"}).status_code == 200
        cam = c.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    with connect(f"ws://{server}/ws/cameras/{cam['id']}", additional_headers={"Cookie": cookie}) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "frame"
    with connect(f"ws://{server}/ws/cameras/{cam['id']}/push", additional_headers={"Cookie": cookie}) as ws:
        ws.send(b"\xff\xd8 not really a jpeg")


def test_app_routes_fall_back_to_the_web_app(server):
    r = httpx.get(f"http://{server}/cameras/cam_123")
    assert r.status_code == 200 and "watchful app" in r.text
    assert httpx.get(f"http://{server}/api/nope").status_code in (401, 404)


def test_signed_out_socket_closes_with_4401(server):
    from websockets.exceptions import ConnectionClosed
    with connect(f"ws://{server}/ws/cameras/cam_1") as ws:
        try:
            ws.recv(timeout=5)
            assert False, "expected a close"
        except ConnectionClosed as e:
            assert e.rcvd.code == 4401


def test_me_reports_the_timezone(server):
    with httpx.Client(base_url=f"http://{server}") as c:
        r = c.post("/api/login", json={"user": "judge", "password": "pw"})
        assert r.json()["timezone"] == "Asia/Kolkata" and c.get("/api/me").json()["timezone"] == "Asia/Kolkata"

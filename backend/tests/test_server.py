"""The app under a real uvicorn server: WebSockets and single page app routes."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from accounts import SOCIETY, join, session_cookie
from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings


APPS = {}


def server_app(server):
    return APPS[server]


@pytest.fixture
def server(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>watchful app</html>")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, web_dist=dist, public_base_url=f"http://127.0.0.1:{port}",
                 _env_file=None)
    app = create_app(s, llm=FakeLLM([]), manager=StubManager())
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    APPS[f"127.0.0.1:{port}"] = app
    yield f"127.0.0.1:{port}"
    srv.should_exit = True


def _signed_in(server, email="judge@example.com", workspace_id=SOCIETY):
    app = server_app(server)
    name, value = session_cookie(app, join(app, email, workspace_id), workspace_id)
    c = httpx.Client(base_url=f"http://{server}", headers={"origin": f"http://{server}"}, cookies={name: value})
    return c, f"{name}={value}"


def test_live_websocket_over_uvicorn(server):
    c, cookie = _signed_in(server)
    with c:
        cam = c.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
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
    c, _ = _signed_in(server)
    with c:
        me = c.get("/api/me").json()
        assert me["timezone"] == "Asia/Kolkata" and me["workspace"]["timezone"] == "Asia/Kolkata"


def test_a_socket_from_another_site_or_society_is_refused(server):
    from websockets.exceptions import ConnectionClosed
    c, cookie = _signed_in(server)
    cam = c.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
    _, stranger = _signed_in(server, "stranger@example.com", "ws_other")
    for headers, code in (({"Cookie": cookie, "Origin": "https://evil.example"}, 4401), ({"Cookie": stranger}, 4404)):
        with connect(f"ws://{server}/ws/cameras/{cam['id']}", additional_headers=headers) as ws:
            try:
                ws.recv(timeout=5)
                assert False, "expected a close"
            except ConnectionClosed as e:
                assert e.rcvd.code == code


def test_closed_live_view_is_released_quickly(server, monkeypatch):
    c, cookie = _signed_in(server)
    cam = c.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
    released = []
    manager = server_app(server).state.ctx.manager
    monkeypatch.setattr(manager, "unsubscribe", lambda cid, sub: released.append(cid))
    with connect(f"ws://{server}/ws/cameras/{cam['id']}", additional_headers={"Cookie": cookie}) as ws:
        ws.recv(timeout=5)
    for _ in range(20):
        if released:
            break
        time.sleep(0.1)
    assert released == [cam["id"]]


def test_push_only_for_webcams_and_stops_only_when_told(server, monkeypatch):
    """A camera that sends its own pictures keeps running when its sender drops, so with no pictures it goes offline
    (health.py); a device that says stop (a normal close) stops it."""
    from websockets.exceptions import ConnectionClosed
    c, cookie = _signed_in(server)
    stream = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    with connect(f"ws://{server}/ws/cameras/{stream['id']}/push", additional_headers={"Cookie": cookie}) as ws:
        try:
            ws.recv(timeout=5)
            assert False
        except ConnectionClosed as e:
            assert e.rcvd.code == 4400
    stopped = []
    manager = server_app(server).state.ctx.manager
    monkeypatch.setattr(manager, "stop", lambda cid: stopped.append(cid))
    cam = c.post("/api/cameras", json={"name": "Desk", "kind": "webcam"}).json()
    ws = connect(f"ws://{server}/ws/cameras/{cam['id']}/push", additional_headers={"Cookie": cookie})
    ws.send(b"\xff\xd8")
    time.sleep(0.3)
    ws.socket.close()  # the connection drops: no close handshake
    time.sleep(1)
    assert stopped == []
    with connect(f"ws://{server}/ws/cameras/{cam['id']}/push", additional_headers={"Cookie": cookie}) as ws:
        ws.send(b"\xff\xd8")
        time.sleep(0.3)
        ws.close(1000)  # the device's Stop
    for _ in range(20):
        if stopped:
            break
        time.sleep(0.1)
    assert stopped == [cam["id"]]

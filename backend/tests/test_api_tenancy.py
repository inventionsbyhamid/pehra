"""A member of one society never reaches another's data through the API: every route with an id answers 404 or 403
for another society's id, lists show only the member's own society, and pictures are served only to their owners."""
import json
import re
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from accounts import BASE, browser
from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.alerts.incidents import open_or_touch_incident
from watchful.api.app import create_app
from watchful.config import Settings
from watchful.db import models as m
from watchful.store.db import AlertRow, Camera, EventRow, WatchRow
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone

A, B = "ws_green_park", "ws_lake_view"


@pytest.fixture
def world(tmp_path):
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    manager = StubManager()
    app = create_app(s, llm=FakeLLM([]), manager=manager)
    a, b = browser(app, "asha@example.com", A), browser(app, "bina@example.com", B)
    ctx = app.state.ctx
    repo = ctx.repo_for(B)
    cam = repo.add_camera(Camera(None, "Lake gate", "stream", "rtsp://lake", [Zone("z1", "gate", [(0, 0), (1, 1)])]))
    event = EventRow(None, cam.id, time.time(), "enter", 7, "person", "gate", {}, f"{cam.id}/kf/7.jpg", [0.1], None,
                     None)
    repo.add_events([event])
    watch = repo.add_watch(WatchRow(None, cam.id, "someone at the gate", {"name": "x"}, True))
    alert = repo.add_alert(AlertRow(None, cam.id, watch.id, time.time(), "critical", "alert", "Lake alert",
                                    f"{cam.id}/clips/a.mp4", f"{cam.id}/kf/7.jpg", [], None, "fp-b", 7))
    repo.add_enrollment(Enrollment("per_lake", "Lake staff", [0.5] * 128, thumbnail_key="people/per_lake.jpg"))
    example = repo.add_state_example(cam.id, "z1", "open", f"{cam.id}/zones/z1/day/1.jpg")
    for key in (f"{cam.id}/kf/7.jpg", "people/per_lake.jpg", f"{B}/x/1.jpg"):
        ctx.blobs.put(key, b"\xff\xd8 picture", "image/jpeg")
    manager.scans["scn_lake"] = {"state": "done", "progress": 1.0, "camera_id": cam.id}
    incident = open_or_touch_incident(repo, cam.id, watch.id, "critical", "Lake incident", None, time.time())
    with ctx.db.tx() as tx:
        link = m.TelegramLink(workspace_id=B, chat_id="-555", title="Lake guards")
        tx.add(link)
    invite = b.post("/api/invites", json={"role": "viewer"}).json()["invite"]
    with ctx.db.tx() as tx:
        bina = tx.scalar(select(m.User.id).where(m.User.email == "bina@example.com"))
    ids = {"camera_id": cam.id, "watch_id": watch.id, "alert_id": alert.id, "scan_id": "scn_lake",
           "person_id": "per_lake", "user_id": bina, "invite_id": invite["id"], "token": invite["id"],
           "key": f"{cam.id}/kf/7.jpg", "zone_id": "z1", "example_id": example, "incident_id": incident.id,
           "link_id": link.id}
    return app, a, b, ids, event


BODIES = {
    ("PATCH", "/api/cameras/{camera_id}"): {"name": "Taken"},
    ("POST", "/api/cameras/{camera_id}/watches"): {"text": "someone enters"},
    ("POST", "/api/cameras/{camera_id}/watches/preview"): {"text": "someone enters"},
    ("PATCH", "/api/watches/{watch_id}"): {"enabled": False},
    ("POST", "/api/alerts/{alert_id}/feedback"): {"value": "down"},
    ("PATCH", "/api/members/{user_id}"): {"role": "viewer"},
}
FORMS = {("POST", "/api/cameras/{camera_id}/zones/{zone_id}/examples"): {"state": "closed"}}


def every_route(routes):
    """The app's routes, inside included routers too (FastAPI keeps those wrapped; no prefixes are used)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        yield from every_route(inner.routes) if inner is not None else (route,)


def test_every_route_is_scoped(world):
    app, a, b, ids, event = world
    checked = []
    for route in every_route(app.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        params = re.findall(r"{(\w+)(?::\w+)?}", route.path)
        if not params:
            continue
        for method in route.methods - {"HEAD"}:
            missing = [p for p in params if p not in ids]
            assert not missing, f"{method} {route.path}: add an id for {missing} to this test"
            path = re.sub(r"{(\w+)(?::\w+)?}", lambda mt: ids[mt.group(1)], route.path)
            body, form = BODIES.get((method, route.path)), FORMS.get((method, route.path))
            res = a.request(method, path, data=form) if form else a.request(method, path, json=body)
            assert res.status_code in (403, 404), f"{method} {route.path} answered {res.status_code}: {res.text}"
            checked.append(f"{method} {route.path}")
    assert len(checked) >= 20

    for path, body in (("/api/ask", {"question": "who came?", "camera_id": ids["camera_id"]}),
                       ("/api/search", {"text": "a red car", "camera_id": ids["camera_id"]}),
                       ("/api/lookouts", {"camera_ids": [ids["camera_id"]], "description": "red shirt"}),
                       ("/api/lookouts", {"camera_ids": [], "event_id": event.id}),
                       ("/api/me/workspace", {"workspace_id": B})):
        res = a.post(path, json=body)
        assert res.status_code in (403, 404), f"POST {path} answered {res.status_code}: {res.text}"

    secrets = [ids["camera_id"], ids["watch_id"], ids["alert_id"], "per_lake", "Lake", "bina@example.com", B]
    for path in ("/api/cameras", "/api/alerts", f"/api/alerts?camera_id={ids['camera_id']}", "/api/metrics",
                 "/api/people", "/api/members", "/api/invites", "/api/me", "/api/incidents", "/api/telegram",
                 "/api/incidents/precision"):
        res = a.get(path)
        assert res.status_code == 200, path
        assert not [x for x in secrets if x in res.text], f"{path} shows another society's data"

    repo = app.state.ctx.repo_for(B)
    assert repo.get_camera(ids["camera_id"]).name == "Lake gate" and repo.get_alert(ids["alert_id"]).feedback is None
    assert [w.enabled for w in repo.list_watches()] == [True] and [e.name for e in repo.list_enrollments()] == [
        "Lake staff"]
    assert [x[2] for x in repo.list_state_examples(ids["camera_id"])] == ["open"]
    assert b.get(f"/api/cameras/{ids['camera_id']}").status_code == 200


def test_blob_from_another_society_is_refused(world):
    app, a, b, ids, _ = world
    blobs = app.state.ctx.blobs
    for key in (ids["key"], "people/per_lake.jpg", f"{B}/x/1.jpg"):
        url = blobs.url(key)
        assert url.startswith("/api/blobs/") and "s=" in url
        assert b.get(url).status_code == 200 and b.get(url).content == b"\xff\xd8 picture"
        assert a.get(url).status_code == 404, key
        assert TestClient(app, base_url=BASE).get(url).status_code == 401
        assert b.get(url.split("?")[0]).status_code == 404  # unsigned
        assert b.get(url.replace("s=", "s=x")).status_code == 404  # tampered
    expired = blobs.signer.url(ids["key"], now=time.time() - 2 * blobs.signer.ttl_s)
    assert b.get(expired).status_code == 404
    other = blobs.signer.url(f"{ids['camera_id']}/kf/8.jpg")
    assert b.get(other.replace("kf/8.jpg", "kf/7.jpg")).status_code == 404  # a signature is for one key
    alerts = b.get("/api/alerts").json()
    assert alerts[0]["keyframe_url"].startswith("/api/blobs/") and b.get(alerts[0]["keyframe_url"]).status_code == 200
    assert json.dumps(a.get("/api/alerts").json()) == "[]"

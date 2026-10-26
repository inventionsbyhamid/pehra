"""A member of one society never reaches another's data through the API: every route with an id answers 404 or 403
for another society's id, lists show only the member's own society, and pictures are served only to their owners."""
import json
import re
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from accounts import BASE, browser, on_plan
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
    event = EventRow(None, cam.id, time.time(), "enter", 7, "person", "gate", {}, f"{B}/{cam.id}/kf/7.jpg", [0.1],
                     None, None)
    repo.add_events([event])
    watch = repo.add_watch(WatchRow(None, cam.id, "someone at the gate", {"name": "x"}, True))
    alert = repo.add_alert(AlertRow(None, cam.id, watch.id, time.time(), "critical", "alert", "Lake alert",
                                    f"{B}/{cam.id}/clips/a.mp4", f"{B}/{cam.id}/kf/7.jpg", [], None, "fp-b", 7))
    repo.add_enrollment(Enrollment("per_lake", "Lake staff", [0.5] * 128, thumbnail_key=f"{B}/people/per_lake.jpg"))
    example = repo.add_state_example(cam.id, "z1", "open", f"{B}/{cam.id}/states/z1/1_day.jpg")
    for key in (f"{B}/{cam.id}/kf/7.jpg", f"{B}/people/per_lake.jpg", f"{B}/x/1.jpg", f"{cam.id}/kf/old.jpg"):
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
           "key": f"{B}/{cam.id}/kf/7.jpg", "zone_id": "z1", "example_id": example, "incident_id": incident.id,
           "link_id": link.id, "staff_id": "per_lake"}
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
    on_plan(app.state.ctx.db, A)  # the gate log and the staff register come with the Society plan
    for path in ("/api/cameras", "/api/alerts", f"/api/alerts?camera_id={ids['camera_id']}", "/api/metrics",
                 "/api/people", "/api/members", "/api/invites", "/api/me", "/api/incidents", "/api/telegram",
                 "/api/incidents/precision", "/api/billing", "/api/staff", "/api/staff?removed=true",
                 "/api/logs/vehicles", "/api/logs/staff", "/api/logs/vehicles.csv", "/api/logs/staff.csv",
                 "/api/logs/watches", "/api/logs/watches.csv", "/api/checks/today"):
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
    for key in (ids["key"], f"{B}/people/per_lake.jpg", f"{B}/x/1.jpg"):
        url = blobs.url(key)
        assert url.startswith("/api/blobs/") and "s=" in url
        assert b.get(url).status_code == 200 and b.get(url).content == b"\xff\xd8 picture"
        assert a.get(url).status_code == 404, key
        assert TestClient(app, base_url=BASE).get(url).status_code == 401
        assert b.get(url.split("?")[0]).status_code == 404  # unsigned
        assert b.get(url.replace("s=", "s=x")).status_code == 404  # tampered
    expired = blobs.signer.url(ids["key"], now=time.time() - 2 * blobs.signer.ttl_s)
    assert b.get(expired).status_code == 404
    other = blobs.signer.url(f"{B}/{ids['camera_id']}/kf/8.jpg")
    assert b.get(other.replace("kf/8.jpg", "kf/7.jpg")).status_code == 404  # a signature is for one key
    alerts = b.get("/api/alerts").json()
    assert alerts[0]["keyframe_url"].startswith("/api/blobs/") and b.get(alerts[0]["keyframe_url"]).status_code == 200
    assert json.dumps(a.get("/api/alerts").json()) == "[]"
    # a key outside the society's prefix is served to no one, even under the society's own camera id
    assert b.get(blobs.url(f"{ids['camera_id']}/kf/old.jpg")).status_code == 404


def test_a_watch_never_points_at_another_societys_picture(world):
    """A sighting to look out for (same_as) puts its picture in front of the verifier and on alerts, so only a lookout
    sets it, from this society's own sighting; a person to look out for must be one of this society's enrolled
    staff."""
    app, a, _, ids, event = world
    repo = app.state.ctx.repo_for(A)
    cam = repo.add_camera(Camera(None, "Green gate", "stream", "rtsp://green"))
    mine = EventRow(None, cam.id, time.time(), "enter", 3, "person", None, {}, f"{A}/{cam.id}/kf/3.jpg", [0.2], [0.3],
                    None)
    repo.add_events([mine])
    theirs = {"event_id": event.id, "crop_key": ids["key"], "embedding": [0.1], "expires_at": time.time() + 3600}
    own = {"event_id": mine.id, "crop_key": mine.keyframe_key, "embedding": [0.3], "expires_at": time.time() + 3600}
    lookout = {"name": "Lookout", "subject": {"classes": ["person"]}, "conditions": [{"signal": "present"}]}
    for same in (theirs, own):
        for body in ({"watch": {**lookout, "subject": {"classes": ["person"], "same_as": same}}, "text": "lookout"},
                     {"preset": "lookout", "fill": {"same_as": same}}):
            res = a.post(f"/api/cameras/{cam.id}/watches", json=body)
            assert res.status_code in (200, 400), res.text  # a checked client watch may be refused outright
            if res.status_code == 200:
                assert res.json()["watch"]["subject"]["same_as"] is None
    saved = [w.watch for w in repo.list_watches(cam.id)]
    assert saved and all(w["subject"].get("same_as") is None for w in saved)
    assert ids["key"] not in json.dumps(saved)

    assert a.post("/api/lookouts", json={"camera_ids": [cam.id], "event_id": event.id}).status_code == 404
    made = a.post("/api/lookouts", json={"camera_ids": [cam.id], "event_id": mine.id})
    assert made.status_code == 200
    (watch_id,) = made.json()["watch_ids"]
    same_as = next(w for w in repo.list_watches(cam.id) if w.id == watch_id).watch["subject"]["same_as"]
    assert same_as["crop_key"] == mine.keyframe_key and same_as["event_id"] == mine.id

    for person in ("per_lake", "per_nobody"):  # another society's staff, and nobody
        assert a.post("/api/lookouts", json={"camera_ids": [cam.id], "person_id": person}).status_code == 404
    repo.add_enrollment(Enrollment("per_green", "Green staff", [0.5] * 128))
    found = a.post("/api/lookouts", json={"camera_ids": [cam.id], "person_id": "per_green"})
    assert found.status_code == 200
    (watch_id,) = found.json()["watch_ids"]
    assert next(w for w in repo.list_watches(cam.id) if w.id == watch_id).watch["subject"]["who"] == "per_green"

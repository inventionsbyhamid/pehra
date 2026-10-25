"""What the web app's pages need from the server: the Telegram link /incidents/{id} opens the app's own page, and one
incident comes with a short title and what happened to it, from its own society only."""
from datetime import timedelta

from fastapi.testclient import TestClient

from accounts import browser
from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.alerts import incidents as inc_
from watchful.api.app import create_app
from watchful.config import Settings
from watchful.rules.presets import preset
from watchful.store.db import Camera, WatchRow

T0 = 1_790_000_000.0


def make_app(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>the landing page</html>")
    (dist / "app.html").write_text("<html>the app shell</html>")
    s = Settings(_env_file=None, session_secret="s" * 32, data_dir=tmp_path, web_dist=dist)
    return create_app(s, llm=FakeLLM([]), manager=StubManager())


def test_the_telegram_link_opens_the_app(tmp_path):
    client = TestClient(make_app(tmp_path))
    for path in ("/incidents/inc_123", "/app/incidents/inc_123", "/app/settings/team"):
        r = client.get(path)
        assert r.status_code == 200 and "the app shell" in r.text, path
    assert "the landing page" in client.get("/").text


def test_an_incident_has_a_title_and_a_timeline_in_its_own_society(tmp_path):
    app = make_app(tmp_path)
    guard = browser(app, "sunil@example.com", "ws_green_park", "supervisor")
    other = browser(app, "neha@example.com", "ws_lake_view", "owner")
    repo = app.state.ctx.repo_for("ws_green_park")
    cam = repo.add_camera(Camera(None, "Pool", "stream", "rtsp://pool")).id
    words = repo.add_watch(WatchRow(None, cam, "someone in the water after pool hours",
                                    preset("enters", zone="pool").model_dump()))
    named = repo.add_watch(WatchRow(None, cam, "fall", preset("fall").model_dump()))

    inc = inc_.open_or_touch_incident(repo, cam, words.id, "critical", "A person is in the water.", None, T0)
    inc_.open_or_touch_incident(repo, cam, words.id, "critical", "", None, T0 + 30)
    health = inc_.open_or_touch_incident(repo, cam, "camera_offline", "warning", "Pool camera is offline.", None, T0)
    fall = inc_.open_or_touch_incident(repo, cam, named.id, "warning", "Someone fell.", None, T0)
    assert guard.post(f"/api/incidents/{inc.id}/acknowledge").json()["title"] == "someone in the water after pool hours"

    listed = {i["id"]: i["title"] for i in guard.get("/api/incidents").json()}
    assert listed[health.id] == "Pool camera is offline."  # no watch: its reason
    assert listed[fall.id] == preset("fall").name  # a preset's key is not words; its watch name is

    got = guard.get(f"/api/incidents/{inc.id}").json()
    assert (got["title"], got["count"], got["status"], got["alert_id"]) == \
        ("someone in the water after pool hours", 2, "acknowledged", None)
    kinds = [(step["kind"], step.get("by")) for step in got["timeline"]]
    assert kinds == [("opened", None), ("repeated", None), ("acknowledged", "sunil@example.com")]
    assert got["timeline"][0]["text"] == "A person is in the water."

    inc_.false_alarm(repo, fall.id, inc_.Actor("telegram:7", "Ravi", "telegram"), T0 + 60)
    steps = guard.get(f"/api/incidents/{fall.id}").json()["timeline"]
    assert steps[-1] | {"at": None} == {"at": None, "kind": "false_alarm", "by": "Ravi", "via": "telegram"}

    assert other.get(f"/api/incidents/{inc.id}").status_code == 404
    assert guard.get("/api/incidents/inc_nope").status_code == 404


def test_a_resolved_incident_says_how_it_closed(tmp_path):
    app = make_app(tmp_path)
    viewer = browser(app, "vik@example.com", "ws_green_park", "viewer")
    repo = app.state.ctx.repo_for("ws_green_park")
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://gate")).id
    inc = inc_.open_or_touch_incident(repo, cam, "camera_offline", "warning", "Gate camera is offline.", None, T0)
    inc_.resolve(repo, inc.id, inc_.when(T0) + timedelta(minutes=5), note="The camera is back")
    steps = viewer.get(f"/api/incidents/{inc.id}").json()["timeline"]
    assert [(s["kind"], s.get("by")) for s in steps] == [("opened", None), ("resolved", "The camera is back")]

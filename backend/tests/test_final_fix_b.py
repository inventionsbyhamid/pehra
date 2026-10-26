"""Final fix pass B: watches and claims that looked like they worked and did not, as tests."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from watchful.agent.compiler import CompileContext, Invalid, check_watch, compile_watch
from watchful.agent.llm import FakeLLM
from watchful.rules.engine import RuleEngine, validate
from watchful.rules.presets import preset, preset_names
from watchful.rules.watch import Condition, Subject, Watch, select_watches
from watchful.store.db import WatchRow
from watchful.tuning import Tuning
from watchful.vision.reason import Tick

TZ = "Asia/Kolkata"


def at(hh, mm=0, ss=0, day=23, tz=TZ):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=ZoneInfo(tz)).timestamp()


def W(*conds, classes=("person",), **kw):
    return Watch(name="w", subject=Subject(classes=list(classes)), conditions=list(conds), verify="?",
                 cooldown_s=kw.pop("cooldown_s", 1), **kw)


def reply(watch, setup=()):
    return json.dumps({"watch": watch, "coverage": [], "setup": list(setup), "cannot": [], "about_age": False})


@pytest.fixture
def app(tmp_path):
    from test_api import StubManager
    from watchful.api import create_app
    from watchful.config import Settings
    return create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                      manager=StubManager())


# I2: a per signal contract, so a watch that could never fire is never saved or run

KINDS = {"gate": "area", "main": "line"}
NEVER = {
    "zone_state compared as a number": Condition(signal="zone_state", zones=["gate"], value="open"),
    "zone_state without a zone": Condition(signal="zone_state", op="is", value="open"),
    "posture compared as a number": Condition(signal="posture", value="fallen"),
    "camera_health compared as a number": Condition(signal="camera_health", value="offline"),
    "attribute compared as a number": Condition(signal="attribute", params={"name": "plate_colour"}, value="yellow"),
    "attribute without a name": Condition(signal="attribute", op="is", value="yellow"),
    "a list without in": Condition(signal="camera_health", op="is", value=["offline", "covered"]),
    "a posture that is never given": Condition(signal="posture", op="is", value="lying"),
    "a camera state that is never given": Condition(signal="camera_health", op="in", value=["dark", "offline"]),
    "looks_like without text": Condition(signal="looks_like", params={"not_text": "a person"}),
    "zone_looks_like without text": Condition(signal="zone_looks_like", zones=["gate"], params={"hold_s": 5}),
    "words without a word": Condition(signal="zone_state", zones=["gate"], op="is", value=True),
    "a number with words": Condition(signal="dwell_s", zones=["gate"], op=">=", value="long"),
}


@pytest.mark.parametrize("name", list(NEVER))
def test_a_watch_that_can_never_fire_is_refused(name):
    w = W(NEVER[name], classes=("person", "car"))
    assert validate(w, KINDS), name
    with pytest.raises(ValueError):
        RuleEngine([("w", w)], [], Tuning(), TZ)


@pytest.mark.parametrize("name", list(NEVER))
def test_a_stored_watch_that_can_never_fire_does_not_run_and_says_why(name):
    w = W(NEVER[name], classes=("person", "car"))
    from watchful.vision.zones import Zone
    gate = Zone("g1", "gate", [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])
    ok, notes = select_watches([WatchRow("w1", "cam", "text", w.model_dump(), True)], "cam", [gate])
    assert ok == [] and notes and notes[0].startswith("w: ")


def test_watches_that_can_fire_pass_the_contract():
    good = [Condition(signal="zone_state", zones=["gate"], op="is", value="open"),
            Condition(signal="zone_state", zones=["gate"], op="!=", value="closed"),
            Condition(signal="posture", op="is", value="fallen"),
            Condition(signal="posture", op="in", value="falling, fallen"),
            Condition(signal="camera_health", op="in", value=["offline", "covered"]),
            Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="yellow"),
            Condition(signal="attribute", params={"name": "plate_text"}, op="contains", value="KA01"),
            Condition(signal="looks_like", params={"text": "a person with a ladder"}),
            Condition(signal="zone_looks_like", params={"text": "smoke"}),
            Condition(signal="dwell_s", zones=["gate"], op=">=", value=30),
            Condition(signal="count", zones=["gate"], op=">=", value="3"),
            Condition(signal="entered", zones=["gate"]),
            Condition(signal="crossed", params={"line": "main", "direction": "in"}, op="is", value=True)]
    for c in good:
        assert validate(W(c), KINDS) == [], c
    fill = {"zone": "gate", "entries": ["gate"], "climbs": ["gate"]}
    for name in preset_names():
        assert validate(preset(name, **fill), KINDS) == [], name


class _Words:
    """An image model where every picture looks like smoke."""

    def text(self, t):
        return np.array([1.0, 0.0], np.float32) if t == "smoke" else np.array([0.0, 1.0], np.float32)

    def image(self, crop):
        return np.array([1.0, 0.0], np.float32)


def test_zone_looks_like_without_a_zone_watches_the_whole_picture():
    """The real AI made "smoke anywhere on this camera" a zone_looks_like with no zone; that read False for ever."""
    w = W(Condition(signal="zone_looks_like", params={"text": "smoke", "not_text": "clear air"}), classes=())
    assert validate(w, {}) == []
    e = RuleEngine([("w", w)], [], Tuning(), TZ, embedder=_Words())
    picture = np.full((240, 320, 3), 120, np.uint8)
    out = e.on_tick("cam", Tick(at(12), (320, 240), False, [], []), at(12), picture)
    assert len(out) == 1 and "smoke" in out[0].evidence["reasons"][0]


def test_the_compiler_asks_the_ai_again_on_a_contract_error():
    bad = reply({"name": "Smoke", "subject": {"classes": ["person"]}, "verify": "?",
                 "conditions": [{"signal": "zone_state", "value": "smoky"}]})
    good = reply({"name": "Smoke", "subject": {"classes": ["person"]}, "verify": "Is there smoke?",
                  "conditions": [{"signal": "zone_looks_like", "params": {"text": "smoke", "not_text": "clear air"}}]})
    llm = FakeLLM([bad, good])
    r = compile_watch("smoke anywhere on this camera", CompileContext(meaning=lambda text: False), llm)
    assert r.watch is not None and r.watch.conditions[0].signal == "zone_looks_like"
    retry = llm.calls[1][1][-1].text
    assert "zone_state" in retry and "needs a zone" in retry


def test_a_client_save_of_a_watch_that_can_never_fire_is_refused_in_plain_words():
    spec = W(Condition(signal="posture", value="fallen")).model_dump()
    with pytest.raises(Invalid) as e:
        check_watch(spec, "someone falls", CompileContext())
    assert "posture" in str(e.value) and "is, !=, contains or in" in str(e.value)


# I3: camera health and face incidents are measured by OpenCV, not checked by the AI, and say so

def _tool_ctx(tmp_path):
    import cv2
    from watchful.agent.tools import ToolContext
    from watchful.store.blobs import LocalBlobStore
    from watchful.store.db import SqliteRepository
    jpg = cv2.imencode(".jpg", np.full((240, 320, 3), 60, np.uint8))[1].tobytes()
    return ToolContext(SqliteRepository(tmp_path / "w.db"), LocalBlobStore(tmp_path), "cam",
                       frames=lambda t0, t1, n: [jpg] * n, crops=lambda tid, ts: jpg, poses=lambda tid, s: [])


def _cand(severity="warning", watch="Gate", reasons=("came into gate",), **evidence):
    from watchful.rules.engine import Candidate
    return Candidate("w1", "cam", 100.0, 1, severity, {"reasons": list(reasons), **evidence}, [], "fp", watch, "?")


@pytest.mark.parametrize("method", ["camera", "face"])
def test_measured_watches_say_no_ai_ran(tmp_path, method):
    from watchful.agent.verifier import verify
    llm = FakeLLM([])
    v = verify(_cand(method=method, reasons=["camera covered"]), _tool_ctx(tmp_path), llm)
    assert v.status == "measured" and llm.calls == []


def test_a_measured_incident_is_not_told_as_a_possibility(tmp_path):
    from telegram_fakes import T0, make_env
    from watchful.agent.verifier import verify
    from watchful.alerts import incidents as inc_
    from watchful.alerts.telegram import told
    env = make_env(tmp_path)
    c = _cand("warning", "A camera is offline, covered, turned or blurred", ["camera covered"], method="camera")
    c.camera_id, c.ts = env.cams["gate"], T0.timestamp()
    inc = inc_.after_check(env.a, c, verify(c, _tool_ctx(tmp_path), FakeLLM([])), "alt_1")
    assert inc.agent_json["verdict"] == "measured"
    assert told(inc) == "A camera is offline, covered, turned or blurred: camera covered."
    face = _cand("critical", "Ravi at the gate", [], method="face")
    face.watch_id, face.camera_id, face.ts, face.fingerprint = "w2", env.cams["pool"], T0.timestamp(), "fp2"
    after = inc_.after_check(env.a, face, verify(face, _tool_ctx(tmp_path), FakeLLM([])), "alt_2")
    assert after.agent_json["verdict"] == "measured" and told(after) == "Ravi at the gate: recognised by face."


# I6: past the daily AI cap or in an AI outage, warning and info triggers are filed, not lost

@pytest.mark.parametrize("severity", ["warning", "info"])
@pytest.mark.parametrize("failure,words", [("limit", "daily limit reached"),
                                           ("down", "the AI could not be reached")])
def test_unchecked_triggers_are_filed_as_unverified(tmp_path, severity, failure, words):
    from watchful.agent.llm import AILimitReached
    from watchful.agent.verifier import verify
    from telegram_fakes import T0, make_env
    from watchful.alerts import incidents as inc_
    error = AILimitReached("daily AI limit reached") if failure == "limit" else ConnectionError("refused")
    v = verify(_cand(severity), _tool_ctx(tmp_path), FakeLLM([error]))
    assert v.status == "unverified" and v.summary == f"Gate: came into gate. Not checked by the AI: {words}."
    env = make_env(tmp_path)
    for i in range(3):
        c = _cand(severity)
        c.camera_id, c.ts, c.fingerprint = env.cams["gate"], T0.timestamp() + i, f"fp{i}"
        inc = inc_.after_check(env.a, c, v, f"alt_{i}")
    assert inc is not None and inc.count == 3 and inc.severity == severity  # grouped like any other
    assert inc.agent_json["verdict"] == "unverified" and words in inc.reason


def test_the_control_room_hears_when_todays_ai_checks_are_used_up(app):
    from accounts import SOCIETY, browser
    c = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    first = c.get("/api/checks/today").json()
    assert first["reached"] is False and first["day"] > 0
    for _ in range(first["day"]):
        repo.record_ai_call("verify")
    assert c.get("/api/checks/today").json() == {"used": first["day"], "day": first["day"], "reached": True}


# I4: zone state examples are settings, so retention keeps them; the camera page counts pictures that load

def _jpg(value=90):
    import cv2
    img = np.full((120, 160, 3), value, np.uint8)
    img[30:90, 40:120] = 255 - value
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_retention_keeps_zone_state_examples(tmp_path):
    import os
    import time
    from accounts import society_repo
    from watchful.config import Settings
    from watchful.rules.zonestate import example_key, load_states
    from watchful.store.blobs import LocalBlobStore
    from watchful.store.db import Camera
    from watchful.tuning import ZoneTuning
    from watchful.vision.zones import Zone
    from watchful.workers import run_retention
    settings = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    repo = society_repo(settings)
    gate = Zone("z1", "gate", [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://g", zones=[gate]))
    blobs = LocalBlobStore(tmp_path)
    ws = repo.workspace_id
    key = example_key(ws, cam.id, "z1", "day", time.time())
    clip = f"{ws}/{cam.id}/clips/old.mp4"
    blobs.put(key, _jpg(), "image/jpeg")
    blobs.put(clip, b"clip", "video/mp4")
    repo.add_state_example(cam.id, "z1", "open", key)
    old = time.time() - 9 * 86400  # the Free plan keeps footage 7 days
    for k in (key, clip):
        os.utime(tmp_path / "blobs" / k, (old, old))
    run_retention(settings, [repo], blobs)
    assert not (tmp_path / "blobs" / clip).exists()
    states = load_states(repo.list_state_examples(cam.id), blobs.get, [gate], ZoneTuning())
    assert states.has("gate")


def test_the_camera_page_counts_only_pictures_that_load(tmp_path):
    from accounts import SOCIETY, browser
    from test_api import StubManager
    from watchful.api import create_app
    from watchful.config import Settings
    from watchful.store.db import Camera
    from watchful.vision.zones import Zone
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                     manager=StubManager())
    c = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    gate = Zone("z1", "gate", [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://g", zones=[gate]))
    there, gone = f"{SOCIETY}/{cam.id}/states/z1/1_day.jpg", f"{SOCIETY}/{cam.id}/states/z1/2_day.jpg"
    app.state.ctx.blobs.put(there, _jpg(), "image/jpeg")
    repo.add_state_example(cam.id, "z1", "open", there)
    repo.add_state_example(cam.id, "z1", "open", gone)
    got = c.get(f"/api/cameras/{cam.id}/examples").json()
    assert [e["state"] for e in got] == ["open"] and len(got) == 1


# I5: watch hours run in the society's own time zone

NY = "America/New_York"


def _society_in(settings, zone):
    from watchful.components import make_engine, make_repo
    from watchful.store.sql import create_workspace
    return make_repo(settings, create_workspace(make_engine(settings), "Riverside", timezone=zone).id)


def test_cameras_and_scans_get_the_society_time_zone(tmp_path, monkeypatch):
    import watchful.workers as workers_
    from watchful.components import make_blobs
    from watchful.config import Settings
    from watchful.store.db import Camera
    from watchful.workers import WorkerManager
    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    repo = _society_in(s, NY)
    night = Watch(name="Someone at night", conditions=[Condition(signal="present")],
                  time_window={"start": "22:00", "end": "06:00"})
    live = repo.add_camera(Camera(None, "Gate", "webcam", None))
    upload = repo.add_camera(Camera(None, "Upload", "file", str(tmp_path / "x.mp4")))
    for cam in (live, upload):
        repo.add_watch(WatchRow(None, cam.id, "someone at night", night.model_dump(), True))
    m = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=FakeLLM([]), processes=1)
    try:
        given = {}
        monkeypatch.setattr(m.pool, "assign", lambda *a, **kw: given.update(kw) or 0)
        m.start(live.id)
        assert given["timezone"] == NY

        def scan(*a, **kw):
            given["scan"] = kw["timezone"]
            raise RuntimeError("stop here")
        monkeypatch.setattr(workers_, "scan_file", scan)
        monkeypatch.setattr(workers_, "probe_start_time", lambda url: 0.0)
        m.scans["scn_x"] = {"state": "scanning", "progress": 0.0, "camera_id": upload.id}
        m._run_scan("scn_x", upload.id)
        assert given["scan"] == NY
    finally:
        m.shutdown()


def test_a_camera_process_runs_watch_hours_in_the_society_zone(tmp_path):
    from watchful.config import Settings
    from watchful.pool import CameraJob, PoolWorker
    from watchful.store.db import Camera
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    night = Watch(name="Someone at night", conditions=[Condition(signal="present")],
                  time_window={"start": "22:00", "end": "06:00"})
    worker = PoolWorker(0, s, Tuning(), send=lambda msg: None)
    try:
        cam = Camera("cam_ny", "Gate", "webcam", None, workspace_id="ws_ny")
        worker.assign(CameraJob(cam, [("w1", night)], [], timezone=NY))
        engine = worker.cameras["cam_ny"].engine
        assert engine.tz == NY
        eleven_pm_ny = at(23, tz=NY)
        assert night.time_window.contains(eleven_pm_ny, engine.tz)
        assert not night.time_window.contains(eleven_pm_ny, TZ)  # 8:30 in the morning in India
    finally:
        worker.close()


def test_a_comp_ends_at_the_end_of_the_day_in_the_society_zone(tmp_path, capsys):
    from datetime import UTC
    from watchful.billing.comp import main
    from watchful.config import Settings
    from watchful.db import models as m
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    repo = _society_in(s, NY)
    main(["comp", repo.workspace_id, "society", "--cameras", "4", "--until", "2026-11-01"], settings=s)
    with repo.tx() as tx:
        until = tx.get(m.Workspace, repo.workspace_id).comp_until
    until = until if until.tzinfo else until.replace(tzinfo=UTC)
    assert until == datetime(2026, 11, 2, tzinfo=ZoneInfo(NY))


# I1: what a log watch writes, crossings and info notes are shown: the camera's history and a watch log

def _log_event(cam_id, ts, watch="Cabs at the gate", reasons=("a yellow plate came in",)):
    from watchful.store.db import EventRow
    return EventRow(None, cam_id, ts, "watch_log", 3, "car", None,
                    {"watch_id": "w1", "watch": watch, "reasons": list(reasons), "signals": {}}, None, None, None,
                    None)


def test_the_camera_history_shows_log_rows_and_crossings(app):
    from accounts import SOCIETY, browser
    from watchful.store.db import Camera, EventRow
    c = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://g"))
    repo.add_events([_log_event(cam.id, at(10)),
                     EventRow(None, cam.id, at(10, 5), "cross", 4, "person", "main", {"direction": "in"}, None, None,
                              None, None)])
    kinds = [e["kind"] for e in c.get(f"/api/cameras/{cam.id}/timeline").json()["events"]]
    assert kinds == ["watch_log", "cross"]


def test_the_watch_log_lists_a_day_of_log_rows_and_notes(app):
    import csv
    import io
    from sqlalchemy import select
    from accounts import SOCIETY, browser
    from watchful.alerts.incidents import open_or_touch_incident
    from watchful.db import models as m
    from watchful.store.db import Camera
    c = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://g"))
    repo.add_events([_log_event(cam.id, at(0, 30)), _log_event(cam.id, at(23, 50, day=22))])
    open_or_touch_incident(repo, cam.id, "w_note", "info", "A delivery came in.", now=at(9))
    open_or_touch_incident(repo, cam.id, "w_warn", "warning", "Gate open too long.", now=at(9, 30))
    body = c.get("/api/logs/watches?day=2026-10-23").json()
    assert [(r["kind"], r["what"]) for r in body["rows"]] == [("note", "A delivery came in."),
                                                              ("log", "a yellow plate came in")]
    assert body["rows"][1]["at"] == "2026-10-23T00:30:00+05:30" and body["rows"][1]["camera"] == "Gate"
    r = c.get("/api/logs/watches.csv?day=2026-10-23")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["Date", "Time", "Camera", "Watch", "What", "Kind"] and len(rows) == 3
    assert rows[2] == ["2026-10-23", "00:30:00", "Gate", "Cabs at the gate", "a yellow plate came in", "log"]
    with app.state.ctx.db.tx() as s:
        assert [a.data["log"] for a in s.scalars(select(m.Audit).where(m.Audit.action == "logs.export"))] == ["watches"]
    assert browser(app, "viewer@example.com", role="viewer").get("/api/logs/watches").status_code == 403
    assert browser(app, "lake@example.com", workspace_id="ws_lake").get(
        "/api/logs/watches?day=2026-10-23").json()["rows"] == []


# Also: the gate log, the staff register and the daily summary come with the plans that list them

def test_logs_the_plan_lacks_say_so_with_the_way_forward(app):
    from accounts import SOCIETY, browser, on_plan
    c = browser(app)
    for path in ("/api/logs/vehicles", "/api/logs/vehicles.csv", "/api/logs/staff", "/api/logs/staff.csv"):
        r = c.get(path)
        assert r.status_code == 403 and "comes with the Society plan" in r.json()["detail"], path
        assert "Settings, under Billing" in r.json()["detail"]
    assert c.get("/api/logs/vehicles").json()["detail"].startswith("The gate log of every vehicle comes with")
    assert c.get("/api/logs/watches").status_code == 200  # writing to the log is a watch: every plan has it
    assert "gate_ledger" not in c.get("/api/me").json()["workspace"]["features"]
    on_plan(app.state.ctx.db, SOCIETY)
    assert all(c.get(p).status_code == 200 for p in ("/api/logs/vehicles", "/api/logs/staff"))
    assert {"gate_ledger", "staff_register", "daily_summary"} <= set(c.get("/api/me").json()["workspace"]["features"])


def test_the_demo_keeps_its_logs(app):
    from watchful.billing.limits import GATE_LOG, STAFF_REGISTER, DAILY_SUMMARY, has_feature
    from watchful.db import models as m
    from datetime import UTC, datetime as dt, timedelta
    with app.state.ctx.db.tx() as s:
        ws = m.Workspace(name="Demo", comp_plan="society", comp_cameras=2, comp_until=dt.now(UTC) + timedelta(hours=2))
        s.add(ws)
        s.flush()
        assert all(has_feature(ws, f) for f in (GATE_LOG, STAFF_REGISTER, DAILY_SUMMARY))


def test_the_daily_summary_goes_only_on_a_plan_with_it(tmp_path):
    from datetime import UTC
    from accounts import on_plan
    from telegram_fakes import make_env
    from watchful.alerts.summary import queue_summaries
    env = make_env(tmp_path)
    env.link(env.a, -1001)
    nine_ist = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)
    assert queue_summaries(env.worker, nine_ist) == []
    on_plan(env.a, env.a.workspace_id)
    assert queue_summaries(env.worker, nine_ist) == [env.a.workspace_id]


# Also: queue sizes, waits and the process report interval come from Tuning

def test_queue_sizes_and_waits_come_from_tuning(tmp_path):
    import queue
    import time
    from watchful.components import make_blobs
    from watchful.config import Settings
    from watchful.pool import PoolWorker, _sender
    from watchful.tuning import load_tuning
    from watchful.workers import WorkerManager
    t = load_tuning(overrides={"resources": {"process_report_s": 0, "out_queue": 7, "important_wait_s": 0.05,
                                             "viewer_queue": 3}})
    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    sent = []
    worker = PoolWorker(0, s, t, send=sent.append)
    worker.step(0)
    worker.step(0)
    assert [m["type"] for m in sent] == ["process", "process"]
    worker.close()
    full = queue.Queue(maxsize=1)
    full.put("x")
    start = time.monotonic()
    _sender(full, t.resources.important_wait_s)({"type": "events"})
    assert time.monotonic() - start < 1.0
    m = WorkerManager(s, t, _society_in(s, TZ), make_blobs(s), llm=FakeLLM([]), processes=1)
    try:
        assert m.out_q._maxsize == 7
        sub = m.subscribe("cam")
        for i in range(5):
            sub.put({"type": "alert", "n": i})
        assert [sub.get_nowait()["n"] for _ in range(3)] == [0, 1, 2]
    finally:
        m.shutdown()

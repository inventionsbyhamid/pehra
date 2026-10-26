"""Task 6b review, fix round 1: each test fails on 3077582. Privacy first (I1 naming over long tracks, I2 Telegram's
blur after a removal, staff recognition only on entry cameras), then the gate log (I4 to I6), the register's words
(I7, in test_register.py) and the minors."""
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from accounts import SOCIETY, browser, on_plan
from watchful.agent.llm import FakeLLM
from watchful.alerts import incidents as incidents_
from watchful.api import create_app
from watchful.config import Settings
from watchful.db import models as m
from watchful.logs import ledger, readers, register
from watchful.rules.watch import Condition, Subject, Watch
from watchful.store.db import Camera, EventRow
from watchful.store.sql import open_sqlite
from watchful.tuning import FaceTuning, Tuning
from watchful.vision.faces import FaceMatch, judge
from watchful.vision.models import sample_path
from watchful.vision.plates import PlateRead
from watchful.vision.zones import Zone

T0 = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)  # 10:30 AM in Kolkata
LINE = Zone("l1", "gate line", [(0.05, 0.55), (0.95, 0.55)], "line")


@pytest.fixture
def repo(tmp_path):
    return open_sqlite(tmp_path / "w.db")


@pytest.fixture
def gate(repo):
    return repo.add_camera(Camera(None, "Main gate", "stream", "rtsp://gate", [LINE], purpose="main gate",
                                  readers=["plates"]))


def cross(cam, direction: str, at: datetime, track: int = 1, cls: str = "car") -> EventRow:
    return EventRow(None, cam.id, at.timestamp(), "cross", track, cls, "gate line", {"direction": direction}, None,
                    None, None, None)


def look(pid: str, score: float, ts: float, margin: float = 0.2) -> FaceMatch:
    return FaceMatch(pid, pid.title(), score, margin, ts)


# I1: a person who is not enrolled is never named over a long track

def test_names_need_agreeing_looks_in_a_row_within_a_window():
    cfg = FaceTuning()
    spread = [look("sunita", 0.37, t) for t in (50.0, 300.0, 590.0)]  # three weak looks over ten minutes
    assert judge(spread, cfg) is None
    alternating = [look(p, 0.40, float(t)) for t, p in enumerate(["ravi", "sunita", "ravi", "sunita", "ravi"])]
    assert judge(alternating, cfg) is None
    assert judge([look("ravi", 0.40, t) for t in (1.0, 2.0, 3.0)], cfg).person_id == "ravi"
    weak_between = [look("ravi", 0.40, 1.0), look("ravi", 0.31, 2.0), look("ravi", 0.40, 3.0)]
    assert judge(weak_between, cfg) is None
    # two look alike staff: one sure look is a name only when it is clearly ahead of the next person
    assert judge([look("ravi", 0.50, 1.0, margin=0.01)], cfg) is None
    assert judge([look("ravi", 0.50, 1.0, margin=0.10)], cfg).person_id == "ravi"


class ScriptedFaces:
    """A face index whose looks are scripted by time: a stranger who matches Sunita weakly, now and then a little
    better."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def identify(self, image, box, floor=None):
        self.calls.append(floor)
        return self.script(self.ts)


class PersonDetector:
    def set_classes(self, classes):
        pass

    def detect(self, image):
        from watchful.vision.detect import Detection
        return [Detection("person", 0.9, (100.0, 50.0, 120.0, 300.0))]


def test_a_stranger_on_a_long_track_is_never_named(tmp_path):
    """The probe: a non enrolled person matching Sunita at 0.31, with three 0.37 looks at 50, 300 and 590 s, stays
    a stranger for ten minutes on one track."""
    from watchful.ingest.sources import Frame
    from watchful.store.blobs import LocalBlobStore
    from watchful.vision.motion import MotionResult
    from watchful.vision.perceive import Needs, Perceiver
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    faces = ScriptedFaces(lambda ts: FaceMatch("stf_sunita", "Sunita", 0.37 if int(ts) in (50, 300, 590) else 0.31,
                                               0.31))
    p = Perceiver(Camera("cam", "Gate", "webcam", None), s, Tuning(), LocalBlobStore(tmp_path), faces,
                  Needs(classes={"person"}, faces=True), detector=PersonDetector())
    p.gate.update = lambda image: MotionResult(True, 1.0, [(0, 0, 10, 10)])
    image = np.full((400, 400, 3), 100, np.uint8)
    named = set()
    for second in range(0, 600):
        faces.ts = float(second)
        for o in p.perceive(Frame(image, float(second), second)).observations:
            if o.face is not None:
                named.add(o.face.name)
    assert named == set() and len(faces.calls) > 500
    assert all(len(mem.looks) <= Tuning().faces.name_frames for mem in p.memo.values())  # looks never pile up


# I2: someone removed is blurred in the very next Telegram still

def test_telegram_blur_hears_a_removal_at_once(repo):
    from watchful.alerts.telegram import _people
    ctx = SimpleNamespace(worker=SimpleNamespace(state={}), tuning=Tuning())
    sunita = register.enrol(repo, "Sunita", "maid", [[0.1] * 128], None)
    assert [e.person_id for e in _people(ctx, repo)] == [sunita.id]
    register.remove(repo, sunita.id, None)
    assert _people(ctx, repo) == []  # not the list held for alerts.faces_refresh_s
    ravi = register.enrol(repo, "Ravi", "driver", [[0.2] * 128], None)
    assert [e.person_id for e in _people(ctx, repo)] == [ravi.id]
    before = register.staff_version(repo)
    assert register.staff_version(repo) == before  # nothing changed: the held list is used


def test_a_removed_persons_face_is_blurred_in_the_next_still(tmp_path, repo):
    from watchful.alerts.telegram import still_for
    from watchful.store.blobs import LocalBlobStore
    from watchful.vision.faces import FaceIndex
    blobs = LocalBlobStore(tmp_path)
    b1, b2 = (cv2.imread(str(sample_path(n))) for n in ("basketball1.png", "basketball2.png"))
    index = FaceIndex()
    sunita = register.enrol(repo, "Sunita", "maid", [index.template(b1), index.template(cv2.flip(b1, 1))], None)
    key = f"{repo.workspace_id}/incidents/p.jpg"
    blobs.put(key, cv2.imencode(".jpg", b2)[1].tobytes(), "image/jpeg")
    inc = SimpleNamespace(id="inc_1", picture_key=key, keyframe_key=None, picture_box=None,
                          agent_json={"cls": "person"})
    ctx = SimpleNamespace(worker=SimpleNamespace(state={}, blobs=blobs), settings=Settings(session_secret="y",
                          data_dir=tmp_path, _env_file=None), tuning=Tuning())
    x, y, w, h = (int(v) for v in index._faces(b2)[0][:4])

    def face_change(jpeg: bytes) -> float:
        still = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        still = cv2.resize(still, (b2.shape[1], b2.shape[0]))
        return float(np.abs(still[y:y + h, x:x + w].astype(int) - b2[y:y + h, x:x + w].astype(int)).mean())

    kept = still_for(ctx, repo, inc)
    register.remove(repo, sunita.id, None)
    blurred = still_for(ctx, repo, inc)
    assert kept is not None and blurred is not None
    assert face_change(blurred) > face_change(kept) + 5  # her face is pixelated now, at once


# staff recognition only on entry cameras

def test_staff_recognition_is_refused_on_cameras_that_are_not_entries(tmp_path):
    from test_api import StubManager
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                     manager=StubManager())
    c = browser(app)
    pool = c.post("/api/cameras", json={"name": "Pool", "kind": "stream", "url": "rtsp://p", "purpose": "pool"}).json()
    r = c.patch(f"/api/cameras/{pool['id']}", json={"readers": ["faces"]})
    assert r.status_code == 400 and r.json()["detail"].startswith("Staff recognition is for entry cameras.")
    door = c.post("/api/cameras", json={"name": "Service gate", "kind": "stream", "url": "rtsp://s",
                                        "purpose": "service gate"}).json()
    assert c.patch(f"/api/cameras/{door['id']}", json={"readers": ["faces"]}).json()["readers"] == ["faces"]
    r = c.patch(f"/api/cameras/{door['id']}", json={"purpose": "pool"})  # a camera with it can't become a pool
    assert r.status_code == 400 and "entry cameras" in r.json()["detail"]
    # and a row that has it anyway (written before the rule) never names anyone at the pool
    stored = Camera("cam_x", "Pool", "stream", None, purpose="pool", readers=["faces"])
    assert not readers.has(stored, readers.FACES)
    assert readers.has(Camera("cam_y", "Gate", "stream", None, purpose="main gate", readers=["faces"]), readers.FACES)


# I4: a cab that may have gone out unread is not an overstay; the overstay is a watch the society can see

def test_an_unread_out_after_the_in_is_not_an_overstay(repo, gate):
    t = Tuning()
    wid = ledger.ensure_overstay_watch(repo, t)
    cab = ledger.record_passage(repo, gate.id, cross(gate, "in", T0), PlateRead("TN09BC4821", 0.9, "yellow"))
    ledger.record_passage(repo, gate.id, cross(gate, "out", T0 + timedelta(minutes=34), 2),
                          PlateRead(None, 0.4, "yellow"))  # very likely the cab, its plate not read
    late = T0 + timedelta(hours=2, minutes=7)
    assert ledger.check_stays(repo, late, t) == [] and incidents_.list_incidents(repo) == []
    other = ledger.record_passage(repo, gate.id, cross(gate, "in", late, 3), PlateRead("KA01AB2345", 0.9, "yellow"))
    ledger.record_passage(repo, gate.id, cross(gate, "out", late + timedelta(minutes=5), 4),
                          PlateRead(None, 0.4, "white"))  # an unread white plate is not this yellow cab
    assert ledger.check_stays(repo, late + timedelta(hours=2, minutes=1), t) == [(other.id, wid)]
    assert ledger.maybe_gone(repo, cab) and not ledger.maybe_gone(repo, other)


def test_turning_plate_reading_on_gives_the_overstay_watch(tmp_path):
    from test_api import StubManager
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                     manager=StubManager())
    c = browser(app)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://g"}).json()
    assert c.patch(f"/api/cameras/{cam['id']}", json={"readers": ["plates"]}).status_code == 200
    repo = app.state.ctx.repo_for(SOCIETY)
    [row] = [w for w in repo.list_watches() if ledger.is_stay_watch(Watch.model_validate(w.watch))]
    assert row.scope == "site" and row.enabled and "commercial vehicle stays inside" in row.watch["name"]
    assert row.id in [w["id"] for w in c.get(f"/api/cameras/{cam['id']}/watches").json()]  # shown with the others
    c.patch(f"/api/cameras/{cam['id']}", json={"readers": []})
    c.patch(f"/api/cameras/{cam['id']}", json={"readers": ["plates"]})
    assert len([w for w in repo.list_watches() if ledger.is_stay_watch(Watch.model_validate(w.watch))]) == 1
    assert c.patch(f"/api/watches/{row.id}", json={"enabled": False}).json()["enabled"] is False


# I5: several cabs in one overstay incident are each named

def test_each_overstaying_cab_is_named(repo, gate):
    t = Tuning()
    wid = ledger.ensure_overstay_watch(repo, t)
    plates = ["KA01AB2345", "DL3CAB1234", "TN09BC4821"]
    for n, plate in enumerate(plates):
        ledger.record_passage(repo, gate.id, cross(gate, "in", T0 + timedelta(minutes=3 * n), n),
                              PlateRead(plate, 0.9, "yellow"))
    for n in range(3):  # the job worker's minute by minute checks as each passes two hours
        ledger.check_stays(repo, T0 + timedelta(hours=2, minutes=1 + 3 * n), t)
    [inc] = incidents_.list_incidents(repo)
    assert inc.watch_id == wid and inc.count == 3
    assert inc.reason.startswith("3 vehicles have not been seen going out: ")
    assert all(p in inc.reason for p in plates)
    assert [x["plate"] for x in inc.agent_json["stays"]] == plates


# I6: a stay watch mixed with what the gate log cannot know never compiles silently

def test_a_stay_watch_with_a_zone_is_refused_with_the_nearest_alternative():
    from watchful.agent.compiler import CompileContext, Invalid, check_watch, compile_watch
    from watchful.rules.engine import validate
    parked = {"name": "Cab in visitor parking over 2 hours", "subject": {"classes": ["car"]},
              "conditions": [{"signal": "in_zone", "zones": ["visitor parking"]},
                             {"signal": "attribute", "params": {"name": "plate_colour"}, "op": "is", "value": "yellow"},
                             {"signal": "inside_s", "op": ">=", "value": 7200}],
              "severity": "warning"}
    w = Watch.model_validate(parked)
    assert any(p.startswith("inside_s comes from the gate log") for p in validate(w, {"visitor parking": "area"}))
    ctx = CompileContext(zones={"visitor parking": "area"}, camera_flags={"attributes": ["plate_text",
                                                                                         "plate_colour"]})
    with pytest.raises(Invalid):
        check_watch(parked, "cab in visitor parking over 2 hours", ctx)
    reply = json.dumps({"watch": parked, "coverage": [], "setup": [], "cannot": []})
    r = compile_watch("cab in visitor parking over 2 hours", ctx, FakeLLM([reply] * 5))
    assert r.watch is None and r.cannot
    assert "gate log" in r.cannot[0].reason and "staying in one spot" in r.cannot[0].alternative
    for extra in ({"signal": "crossed", "params": {"line": "gate"}}, {"signal": "stationary_s", "value": 60}):
        mixed = Watch(name="x", subject=Subject(classes=["car"]),
                      conditions=[Condition(signal="inside_s", value=60), Condition(**extra)])
        assert any("gate log" in p for p in validate(mixed, {"gate": "line"}))
    known = Watch(name="x", subject=Subject(classes=["person"], who="known"), conditions=[Condition(signal="inside_s")])
    assert any("who the person is" in p for p in validate(known, {}))
    plain = Watch(name="x", subject=Subject(classes=["car"]),
                  conditions=[Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="yellow"),
                              Condition(signal="inside_s", value=7200)])
    assert validate(plain, {}) == []


# minors

def test_the_photo_least_like_the_others_is_named():
    sims = {(0, 1): 0.30, (0, 2): 0.20, (1, 2): 0.10}

    def similarity(a, b):
        return sims[tuple(sorted((a[0], b[0])))]

    feats = [[0], [1], [2]]  # every photo is under enrol_same; photo 3 is the least like the others
    assert register.check_photos(feats, similarity, FaceTuning()) == 3


def test_a_plate_camera_detects_every_frame_only_when_it_runs_slow(tmp_path):
    from watchful.components import make_blobs
    from watchful.ingest.sources import Frame
    from watchful.pool import CameraJob, PoolWorker
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    worker = PoolWorker(0, s, Tuning(), [].append, blobs=make_blobs(s))
    cam = Camera("cam_gate", "Gate", "webcam", None, [LINE], workspace_id="ws_a", readers=["plates"])
    worker.assign(CameraJob(cam, [], []))
    r = worker.cameras[cam.id]
    try:
        image = np.full((360, 640, 3), 90, np.uint8)
        r.after(Frame(image, 0.0, 0), 0.0, r.pipe.process(Frame(image, 0.0, 0)))
        assert r.pipe.perceiver.stride == 1  # nothing analysed yet in the last 10 s: slow
        for i in range(1, 12):
            f = Frame(image, i * 0.05, i)
            r.after(f, 0.0, r.pipe.process(f))
        assert r.fps() >= Tuning().ledger.every_frame_below_fps and r.pipe.perceiver.stride == Tuning().detect.stride
    finally:
        worker.close()


def test_crossings_waiting_for_reads_are_logged_when_the_camera_is_removed(tmp_path):
    from watchful.components import make_blobs
    from watchful.pool import CameraJob, PoolWorker
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    sent = []
    worker = PoolWorker(0, s, Tuning(), sent.append, blobs=make_blobs(s))
    cam = Camera("cam_gate", "Gate", "webcam", None, [LINE], workspace_id="ws_a", readers=["plates"])
    worker.assign(CameraJob(cam, [], []))
    e = cross(cam, "in", T0)
    worker.cameras[cam.id].passages.pending.append((e, e.ts + 3))
    worker.remove(cam.id)
    worker.close()
    assert [m_["event"] for m_ in sent if m_["type"] == "passage"] == [e]


def test_a_scan_asks_for_enrolled_staff_again_as_it_goes(tmp_path, monkeypatch):
    from watchful.vision import scan
    from watchful.vision.faces import Enrollment
    from watchful.vision.perceive import Needs
    seen = []

    def chunk(job):
        seen.append([e.person_id for e in job.enrollments])
        return scan.ChunkResult(job.index, job.start, job.end, [], {"frames": 1})

    monkeypatch.setattr(scan, "probe_duration", lambda path: 60.0)
    monkeypatch.setattr(scan, "_run_chunk", chunk)
    monkeypatch.setattr(scan, "needs_for", lambda watches: Needs(classes={"person"}, faces=True))
    staff = [[Enrollment("stf_a", "A", [0.1])]]

    def ask():
        out = staff[0]
        staff[0] = []  # removed after the first ask
        return out

    pool = SimpleNamespace(workers=1, ex=ThreadPoolExecutor(1))
    t = Tuning.model_validate({"faces": {"reload_s": 0}, "scan": {"min_chunk_s": 10, "target_chunk_s": 15}})
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    scan.scan_file("x.mp4", Camera("cam", "Upload", "file", "x.mp4"), [("w", Watch(
        name="x", conditions=[Condition(signal="present")]))], pool, None, s, t, enrollments=ask)
    pool.ex.shutdown()
    assert seen[0] == ["stf_a"] and seen[-1] == [] and len(seen) >= 2


def test_faces_go_to_the_camera_processes_only_when_staff_changed(tmp_path, repo):
    from watchful.components import make_blobs
    from watchful.workers import WorkerManager, WorkerStatus, _Cam
    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    mgr = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=FakeLLM([]), processes=1)
    sent = []
    mgr.pool.reload = lambda ws, people: sent.append([e.person_id for e in people])
    try:
        cam = Camera("cam_1", "Gate", "webcam", None, workspace_id=repo.workspace_id)
        mgr.cams[cam.id] = _Cam(cam, WorkerStatus(running=True))
        assert mgr.sync_faces() == [repo.workspace_id]  # never sent yet
        assert mgr.sync_faces() == [] and len(sent) == 1  # nothing changed: nothing sent
        sunita = register.enrol(repo, "Sunita", "maid", [[0.1] * 128], None)
        assert mgr.sync_faces() == [repo.workspace_id] and sent[-1] == [sunita.id]
        register.remove(repo, sunita.id, None)
        assert mgr.sync_faces() == [repo.workspace_id] and sent[-1] == []
    finally:
        mgr.shutdown()


def test_the_gate_log_pages_and_the_csv_has_every_row(tmp_path):
    from test_api import StubManager
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                     manager=StubManager())
    c = browser(app)
    on_plan(app.state.ctx.db, SOCIETY)  # the gate log comes with the Society plan
    repo = app.state.ctx.repo_for(SOCIETY)
    gate = repo.add_camera(Camera(None, "Main gate", "stream", "rtsp://g", [LINE], readers=["plates"]))
    with repo.tx() as s:
        s.add_all([m.VehiclePassage(workspace_id=SOCIETY, camera_id=gate.id, at=T0 + timedelta(seconds=10 * i),
                                    direction="in" if i % 2 else "out", cls="car", plate_text=f"MH12AB{1000 + i}",
                                    plate_conf=0.9, plate_colour="white", reads_json=[], alerted_json=[])
                   for i in range(2100)])
    first = c.get("/api/logs/vehicles?day=2026-10-25").json()
    assert len(first["passages"]) == ledger.PAGE and first["next"] and first["counts"]["in"] == 1050
    second = c.get(f"/api/logs/vehicles?day=2026-10-25&after={first['next']}").json()
    assert second["passages"][0]["at"] < first["passages"][-1]["at"]
    assert not {p["id"] for p in first["passages"]} & {p["id"] for p in second["passages"]}
    rows = list(csv.reader(io.StringIO(c.get(f"/api/logs/vehicles.csv?day=2026-10-25&camera_id={gate.id}").text)))
    assert len(rows) == 2101  # every passage, past any page
    with app.state.ctx.db.tx() as s:
        [audit] = s.scalars(select(m.Audit).where(m.Audit.action == "logs.export")).all()
    assert audit.data == {"log": "vehicles", "day": "2026-10-25", "rows": 2100, "camera_id": gate.id}


@pytest.mark.parametrize("hour", [0.5, 6, 12, 23.5])
def test_the_demo_cab_overstays_whenever_the_demo_starts(tmp_path, hour):
    from watchful.logs import demo
    repo = open_sqlite(tmp_path / "w.db")
    gate = repo.add_camera(Camera(None, "Main gate", "demo", "x.mp4"))
    now = datetime(2026, 10, 25, 18, 30, tzinfo=UTC) + timedelta(hours=hour)  # midnight in Kolkata, plus hour
    with repo.tx() as s:
        demo.seed(s, repo.workspace_id, {"main_gate": gate.id}, now)
    with repo.tx() as s:
        yellow_unread = s.scalars(select(m.VehiclePassage).where(m.VehiclePassage.plate_colour == "yellow",
                                                                 m.VehiclePassage.plate_text.is_(None))).all()
    assert yellow_unread == []
    fired = ledger.check_stays(repo, now, Tuning())
    assert any(w == repo.get_meta(ledger.OVERSTAY_KEY) for _, w in fired)


def test_staff_removed_on_one_process_are_forgotten_on_every_camera(tmp_path):
    """Review Focus 5 without processes (the slow test runs them): two camera workers, as two processes would be,
    each naming Sunita; the reload forgets her on both, including a name already given and one carried by clothes."""
    from watchful.components import make_blobs
    from watchful.ingest.sources import Frame
    from watchful.pool import CameraJob, PoolWorker
    from watchful.vision.faces import FaceIndex
    from watchful.vision.motion import MotionResult
    repo = open_sqlite(tmp_path / "w.db")
    b1, b2 = (cv2.imread(str(sample_path(n))) for n in ("basketball1.png", "basketball2.png"))
    index = FaceIndex()
    register.enrol(repo, "Sunita", "maid", [index.template(b1), index.template(cv2.flip(b1, 1))], None)
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    workers, cams = [], []
    for i in range(2):
        w = PoolWorker(i, s, Tuning(), [].append, blobs=make_blobs(s))
        cam = repo.add_camera(Camera(None, f"Gate {i}", "webcam", None, purpose="main gate", readers=["faces"]))
        w.assign(CameraJob(cam, [], repo.list_enrollments()))
        w.cameras[cam.id].pipe.perceiver.gate.update = lambda image: MotionResult(True, 1.0, [(0, 0, 10, 10)])
        workers.append(w)
        cams.append(cam)

    def names(at: float) -> list[set]:
        out = []
        for w, cam in zip(workers, cams):
            tick = w.cameras[cam.id].pipe.process(Frame(b2, at, int(at))).tick
            out.append({t.known_name for t in tick.tracks if t.cls == "person"})
        return out

    try:
        for i in range(3):
            got = names(100.0 + i)
        assert got == [{"Sunita"}, {"Sunita"}]
        for w in workers:
            w.reload_faces(repo.workspace_id, [])
        assert names(104.0) == [{None}, {None}]  # the same tracks lose the name at once
        assert all(not w.cameras[c.id].pipe.reasoner.recent_known for w, c in zip(workers, cams))
        for i in range(5):
            got = names(110.0 + i)
        assert got == [{None}, {None}]
    finally:
        for w in workers:
            w.close()

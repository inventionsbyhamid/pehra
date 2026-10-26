"""The gate log: passages from line crossings on cameras with plate reading, paired by plate, commercial overstays and
stay watches through the generic attribute signal, exports, retention, and the camera side reading."""
import csv
import io
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from accounts import SOCIETY, browser
from watchful.agent.llm import FakeLLM
from watchful.alerts import incidents as incidents_
from watchful.api import create_app
from watchful.config import Settings
from watchful.db import models as m
from watchful.logs import ledger, readers
from watchful.logs.reading import Passages, PlateSampler
from watchful.rules.watch import Condition, Subject, Watch
from watchful.store.db import Camera, EventRow, WatchRow
from watchful.store.sql import open_sqlite
from watchful.tuning import Tuning
from watchful.vision.plates import CropRead, PlateRead
from watchful.vision.zones import Zone

T0 = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)  # 10:30 AM in Kolkata
LINE = Zone("l1", "gate line", [(0.05, 0.55), (0.95, 0.55)], "line")


@pytest.fixture
def repo(tmp_path):
    return open_sqlite(tmp_path / "w.db")


@pytest.fixture
def gate(repo):
    return repo.add_camera(Camera(None, "Main gate", "stream", "rtsp://gate", [LINE], readers=["plates"]))


def cross(cam, direction: str, at: datetime, track: int = 1, cls: str = "car") -> EventRow:
    return EventRow(None, cam.id, at.timestamp(), "cross", track, cls, "gate line", {"direction": direction}, None,
                    None, None, None)


def test_in_and_out_pair_by_plate(repo, gate):
    came = ledger.record_passage(repo, gate.id, cross(gate, "in", T0), PlateRead("MH12AB1234", 0.88, "white"))
    # an unread plate keeps a non zero confidence for its guess: it is still unread, and never pairs
    unread = ledger.record_passage(repo, gate.id, cross(gate, "in", T0 + timedelta(minutes=1), 2),
                                   PlateRead(None, 0.47, "white", ("MH12AB1234",)))
    other = ledger.record_passage(repo, gate.id, cross(gate, "in", T0 + timedelta(minutes=2), 3),
                                  {"text": "KA01AB2345", "confidence": 0.9, "colour": "yellow"})
    out = ledger.record_passage(repo, gate.id, cross(gate, "out", T0 + timedelta(hours=1), 4),
                                PlateRead("MH12AB1234", 0.8, "white"))
    assert out.paired_id == came.id
    unread_out = ledger.record_passage(repo, gate.id, cross(gate, "out", T0 + timedelta(hours=2), 5),
                                       PlateRead(None, 0.47, "white", ("MH12AB1234",)))
    assert unread_out.paired_id is None
    rows = {r["id"]: r for r in ledger.passages(repo, "2026-10-25")}
    assert rows[came.id]["paired_id"] == out.id and rows[came.id]["stayed_s"] == 3600
    assert rows[unread.id]["paired_id"] is None and rows[unread.id]["plate"] is None
    assert rows[other.id]["paired_id"] is None and rows[other.id]["plate_colour"] == "yellow"
    # the same plate the same way through the same camera moments later is the passage already logged
    again = ledger.record_passage(repo, gate.id, cross(gate, "out", T0 + timedelta(hours=1, seconds=5), 6),
                                  PlateRead("MH12AB1234", 0.8, "white"))
    assert again.id == out.id
    assert [p.id for p in ledger.open_stays(repo, T0 + timedelta(hours=3))] == [other.id]
    assert [r["id"] for r in ledger.passages(repo, "2026-10-25", q="ka 01")] == [other.id]


def test_commercial_overstay_opens_an_incident(repo, gate):
    t = Tuning()
    wid = ledger.ensure_overstay_watch(repo, t)  # what turning plate reading on gives a society, once
    assert wid and ledger.ensure_overstay_watch(repo, t) is None
    [row] = [w for w in repo.list_watches() if w.id == wid]
    assert row.scope == "site" and row.enabled and row.watch["name"] == "A commercial vehicle stays inside more than 2 hours"
    cab = ledger.record_passage(repo, gate.id, cross(gate, "in", T0), PlateRead("KA01AB2345", 0.9, "yellow"))
    ledger.record_passage(repo, gate.id, cross(gate, "in", T0, 2), PlateRead("MH12AB1234", 0.9, "white"))
    gone = ledger.record_passage(repo, gate.id, cross(gate, "in", T0, 3), PlateRead("DL3CAB1234", 0.9, "yellow"))
    ledger.record_passage(repo, gate.id, cross(gate, "out", T0 + timedelta(minutes=30), 4),
                          PlateRead("DL3CAB1234", 0.9, "yellow"))
    assert ledger.check_stays(repo, T0 + timedelta(hours=1), t) == []
    late = T0 + timedelta(seconds=t.ledger.overstay_s + 60)
    assert ledger.check_stays(repo, late, t) == [(cab.id, wid)]
    assert ledger.check_stays(repo, late + timedelta(minutes=5), t) == []  # once per vehicle
    [inc] = incidents_.list_incidents(repo)
    assert inc.watch_id == wid and inc.camera_id == gate.id and inc.severity == "warning"
    assert inc.reason == "KA01AB2345 came in at 10:30 AM through Main gate and has not been seen going out after " \
                         "2 hours 1 minute."
    assert gone.id not in [p for p, _ in ledger.check_stays(repo, late + timedelta(hours=1), t)]
    # a Telegram message is the incident module's job: a warning is queued only when the society wants warnings
    with repo.tx() as s:
        assert s.scalars(select(m.Job).where(m.Job.kind == incidents_.SYNC)).all() == []
    row.enabled = False  # a society switches it off like any watch
    repo.update_watch(row)
    ledger.record_passage(repo, gate.id, cross(gate, "in", late, 5), PlateRead("TN09BC4821", 0.9, "yellow"))
    assert ledger.check_stays(repo, late + timedelta(days=1), t) == []


def test_a_cab_watch_works_through_the_generic_attribute_signal(repo, gate):
    """'A cab stays more than 30 minutes' compiles to generic blocks (attribute plate_colour is yellow, inside_s at
    least 1800) and fires through the gate log, not on a camera."""
    from watchful.rules.engine import validate
    w = Watch(name="A cab stays more than 30 minutes", subject=Subject(classes=["car"]), scope="site",
              conditions=[Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="yellow"),
                          Condition(signal="inside_s", op=">=", value=1800)], severity="critical")
    assert validate(w, {}) == [] and ledger.is_stay_watch(w)
    row = repo.add_watch(WatchRow(None, gate.id, "a cab stays more than 30 minutes", w.model_dump(), True))
    noted = Watch(name="Log white cars inside an hour", subject=Subject(classes=["car"]), output="log",
                  conditions=[Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="white"),
                              Condition(signal="inside_s", op=">=", value=3600)])
    log_row = repo.add_watch(WatchRow(None, gate.id, "log white cars", noted.model_dump(), True))
    cab = ledger.record_passage(repo, gate.id, cross(gate, "in", T0), PlateRead("KA01AB2345", 0.9, "yellow"))
    car = ledger.record_passage(repo, gate.id, cross(gate, "in", T0, 2), PlateRead("MH12AB1234", 0.9, "white"))
    t = Tuning.model_validate({"ledger": {"overstay_s": 0}})
    assert ledger.check_stays(repo, T0 + timedelta(minutes=29), t) == []
    assert ledger.check_stays(repo, T0 + timedelta(minutes=31), t) == [(cab.id, row.id)]
    [inc] = incidents_.list_incidents(repo)
    assert inc.watch_id == row.id and inc.severity == "critical"
    assert ledger.check_stays(repo, T0 + timedelta(minutes=61), t) == [(car.id, log_row.id)]
    logged = repo.query_events(kinds=["watch_log"])
    assert [e.data["watch_id"] for e in logged] == [log_row.id] and len(incidents_.list_incidents(repo)) == 1


def test_stay_watches_run_on_the_gate_log_not_on_cameras(tmp_path, repo, gate):
    from watchful.workers import WorkerManager
    from watchful.components import make_blobs
    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    w = Watch(name="Cab stays", subject=Subject(classes=["car"]),
              conditions=[Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="yellow"),
                          Condition(signal="inside_s", op=">=", value=7200)])
    plain = Watch(name="Car comes in", subject=Subject(classes=["car"]),
                  conditions=[Condition(signal="crossed", params={"line": "gate line", "direction": "in"})])
    repo.add_watch(WatchRow(None, gate.id, "cab stays", w.model_dump(), True))
    repo.add_watch(WatchRow(None, gate.id, "car comes in", plain.model_dump(), True))
    mgr = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=FakeLLM([]), processes=1)
    try:
        assert [x.name for _, x in mgr._watches(gate.id)[0]] == ["Car comes in"]
    finally:
        mgr.shutdown()


class FakeReader:
    """Reads one plate on any crop, counting its calls."""

    def __init__(self, text="KA01AB2345", colour="yellow"):
        self.calls, self.text, self.colour = 0, text, colour

    def read_crop(self, crop):
        self.calls += 1
        return CropRead(self.text, 0.9 - 0.01 * self.calls, self.colour, crop[:4, :4].copy(), "plate box")

    def vote(self, reads):
        from watchful.vision.plates import vote
        return vote(reads)


class Box:
    def __init__(self, id, cls, box):
        self.id, self.cls, self.box = id, cls, box


def test_plates_are_read_near_lines_at_most_twice_a_track():
    t = Tuning()
    reader = FakeReader()
    sampler = PlateSampler(reader, [LINE], t)
    image = np.zeros((540, 960, 3), np.uint8)
    far = Box(1, "car", (400.0, 0.0, 200.0, 100.0))  # foot at 100, the line at 297, box 100 high: too far
    assert sampler.observe(image, far, 0.0) is None and reader.calls == 0
    person = Box(2, "person", (100.0, 200.0, 60.0, 100.0))
    assert sampler.observe(image, person, 0.0) is None and reader.calls == 0
    near = Box(1, "car", (400.0, 100.0, 200.0, 180.0))
    got = sampler.observe(image, near, 1.0)
    assert got == {"plate_text": "KA01AB2345", "plate_colour": "yellow"} and reader.calls == 1
    sampler.observe(image, near, 1.1)  # too soon after the last read
    for ts in (2.0, 3.0, 4.0, 5.0):
        sampler.observe(image, near, ts)
    assert reader.calls == t.ledger.reads_per_track
    small = Box(3, "car", (400.0, 250.0, 40.0, 40.0))
    sampler.observe(image, small, 6.0)
    assert reader.calls == t.ledger.reads_per_track  # too small to read
    assert sampler.forget(1) == {"plate_text": "KA01AB2345", "plate_colour": "yellow"}
    assert sampler.state(1) is not None and sampler.stats()["plate_reads"] == 2
    # a camera with no lines reads its vehicles anywhere, still at most twice each
    anywhere = PlateSampler(FakeReader(), [], t)
    anywhere.observe(image, far, 0.0)
    assert anywhere.reader.calls == 1


def test_a_crossing_waits_for_its_reads_then_becomes_a_passage(tmp_path):
    from watchful.store.blobs import LocalBlobStore
    from watchful.vision.reason import Tick
    t = Tuning()
    cam = Camera("cam_gate", "Main gate", "stream", None, [LINE], workspace_id="ws_a", readers=["plates"])
    sampler = PlateSampler(FakeReader(), [LINE], t)
    blobs = LocalBlobStore(tmp_path)
    passages = Passages(sampler, blobs, cam, t)
    image = np.full((540, 960, 3), 90, np.uint8)
    car = Box(7, "car", (400.0, 150.0, 200.0, 180.0))
    sampler.observe(image, car, 10.0)
    e = cross(cam, "in", datetime.fromtimestamp(10.0, UTC), 7)
    assert passages.on_tick(Tick(10.0, (960, 540), True, [], [e])) == []  # one read so far: wait for the second
    sampler.observe(image, car, 11.0)
    [msg] = passages.on_tick(Tick(11.0, (960, 540), True, [], []))
    assert msg["type"] == "passage" and msg["workspace_id"] == "ws_a" and msg["event"] is e
    assert msg["plate"]["text"] == "KA01AB2345" and msg["picture_key"].startswith("ws_a/cam_gate/passages/")
    assert blobs.get(msg["picture_key"])
    # a vehicle never read (too small here) still makes a passage once settle_s passed, unread
    sampler.observe(image, Box(8, "car", (400.0, 280.0, 40.0, 40.0)), 20.0)
    e2 = cross(cam, "out", datetime.fromtimestamp(20.0, UTC), 8)
    assert passages.on_tick(Tick(20.0, (960, 540), True, [], [e2])) == []
    [late] = passages.on_tick(Tick(20.0 + t.ledger.settle_s, (960, 540), True, [], []))
    assert late["plate"]["text"] is None and late["picture_key"] is None


def test_camera_readers_setting_and_turn_on(tmp_path):
    import json
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    cab = json.dumps({"watch": {"name": "Cabs", "subject": {"classes": ["car"]}, "output": "log", "verify": "",
                                "conditions": [{"signal": "attribute", "params": {"name": "plate_colour"},
                                                "op": "is", "value": "yellow"}]},
                      "coverage": [], "setup": [], "cannot": []})
    from test_api import StubManager
    app = create_app(s, llm=FakeLLM([cab, cab]), manager=StubManager())
    c = browser(app)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    assert cam["readers"] == []
    card = c.post(f"/api/cameras/{cam['id']}/watches/preview", json={"text": "log every cab"}).json()
    assert card["turn_on"] == [{"reader": "plates", "label": "Plate reading"}]
    assert "Turn on plate reading for this camera." in card["setup"]
    assert c.patch(f"/api/cameras/{cam['id']}", json={"readers": ["plates", "x"]}).status_code == 400
    r = c.patch(f"/api/cameras/{cam['id']}", json={"readers": ["plates"]})
    assert r.status_code == 200 and r.json()["readers"] == ["plates"]
    card = c.post(f"/api/cameras/{cam['id']}/watches/preview", json={"text": "log every cab"}).json()
    assert card["turn_on"] == [] and not any("plate reading" in x for x in card["setup"])
    assert readers.attributes_of(app.state.ctx.repo_for(SOCIETY).get_camera(cam["id"])) == ["plate_text",
                                                                                            "plate_colour"]


def test_gate_log_api_csv_and_roles(tmp_path):
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    from test_api import StubManager
    app = create_app(s, llm=FakeLLM([]), manager=StubManager())
    owner = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    gate = repo.add_camera(Camera(None, "Main gate", "stream", "rtsp://gate", [LINE], readers=["plates"]))
    app.state.ctx.blobs.put(f"{SOCIETY}/{gate.id}/passages/1.jpg", b"jpg", "image/jpeg")
    came = ledger.record_passage(repo, gate.id, cross(gate, "in", T0), PlateRead("MH12AB1234", 0.88, "white"),
                                 f"{SOCIETY}/{gate.id}/passages/1.jpg")
    ledger.record_passage(repo, gate.id, cross(gate, "out", T0 + timedelta(minutes=90), 2),
                          PlateRead("MH12AB1234", 0.8, "white"))
    ledger.record_passage(repo, gate.id, cross(gate, "in", T0, 3), PlateRead(None, 0.3, "unknown"))
    ledger.record_passage(repo, gate.id, cross(gate, "in", T0 - timedelta(days=1), 4),
                          PlateRead("=HYPERLINK", 0.9, "white"))
    body = owner.get("/api/logs/vehicles?day=2026-10-25").json()
    assert body["counts"] == {"in": 2, "out": 1, "unread": 1}
    first = next(p for p in body["passages"] if p["id"] == came.id)
    assert first["picture_url"] and first["stayed_s"] == 5400 and first["camera"] == "Main gate"
    assert first["at"].startswith("2026-10-25T10:30:00+05:30")
    assert [p["plate"] for p in owner.get("/api/logs/vehicles?day=2026-10-25&q=mh12").json()["passages"]] == \
        ["MH12AB1234", "MH12AB1234"]
    assert owner.get("/api/logs/vehicles?day=25/10/2026").status_code == 400
    r = owner.get("/api/logs/vehicles.csv?day=2026-10-24")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:3] == ["Date", "Time", "Direction"] and rows[1][4] == "'=HYPERLINK"  # never a formula
    with app.state.ctx.db.tx() as tx:
        audit = tx.scalars(select(m.Audit).where(m.Audit.action == "logs.export")).all()
    assert [(a.data["log"], a.data["rows"]) for a in audit] == [("vehicles", 1)]
    viewer = browser(app, "viewer@example.com", role="viewer")
    assert viewer.get("/api/logs/vehicles").status_code == 403
    assert viewer.get("/api/logs/vehicles.csv").status_code == 403
    other = browser(app, "lake@example.com", workspace_id="ws_lake", role="owner")
    assert other.get("/api/logs/vehicles?day=2026-10-25").json()["passages"] == []


def test_retention_deletes_old_passages_and_attendance(tmp_path):
    from watchful.components import make_engine
    from watchful.jobs import JobQueue, Worker
    from watchful.logs import register
    from watchful.store.blobs import LocalBlobStore
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    engine = make_engine(s)
    from watchful.db.session import migrate
    from watchful.store.sql import SqlRepository, create_workspace
    migrate(engine)
    ws = create_workspace(engine, "Green Park").id  # free plan: 7 days
    repo = SqlRepository(engine, ws)
    gate = repo.add_camera(Camera(None, "Main gate", "stream", "rtsp://gate", [LINE], readers=["plates"]))
    blobs = LocalBlobStore(tmp_path)
    now = m.utcnow()
    blobs.put(f"{ws}/{gate.id}/passages/old.jpg", b"x", "image/jpeg")
    ledger.record_passage(repo, gate.id, cross(gate, "in", now - timedelta(days=9)), PlateRead("MH12AB1234", 0.9,
                          "white"), f"{ws}/{gate.id}/passages/old.jpg")
    kept = ledger.record_passage(repo, gate.id, cross(gate, "in", now - timedelta(days=2), 2),
                                 PlateRead("MH12AB1234", 0.9, "white"))
    staff = register.enrol(repo, "Sunita", "maid", [[0.1] * 128], None)
    with repo.tx() as tx:
        for days in (9, 2):
            day = (now - timedelta(days=days)).date().isoformat()
            tx.add(m.Attendance(workspace_id=ws, staff_id=staff.id, day=day, first_seen=now, last_seen=now))
        tx.add(m.StaffFace(workspace_id=ws, staff_id=staff.id, embedding=m.pack_vec([0.2] * 128),
                           picture_key=f"{ws}/people/old.jpg"))
    blobs.put(f"{ws}/people/old.jpg", b"x", "image/jpeg")
    worker = Worker(JobQueue(engine), s, Tuning(), repo_for=lambda w: SqlRepository(engine, w), blobs=blobs)
    worker.queue.enqueue(ledger.RETENTION, {})
    worker.run_due()
    assert [p.id for p in ledger.open_stays(repo, now)] == [kept.id]
    with repo.tx() as tx:
        days = sorted(a.day for a in tx.scalars(select(m.Attendance)))
        assert days == [(now - timedelta(days=2)).date().isoformat()]
        assert tx.scalars(select(m.StaffFace.picture_key).where(m.StaffFace.picture_key.is_not(None))).all() == []
    for key in (f"{ws}/{gate.id}/passages/old.jpg", f"{ws}/people/old.jpg"):
        with pytest.raises((FileNotFoundError, KeyError, OSError)):
            blobs.get(key)


class SceneDetector:
    """Stands in for YOLOX, which does not take a drawn car for a car: it finds what the clip drew where it drew it."""

    def __init__(self):
        self.boxes: list[tuple[str, tuple]] = []

    def set_classes(self, classes):
        pass

    def detect(self, image):
        from watchful.vision.detect import Detection
        return [Detection(cls, 0.9, box) for cls, box in self.boxes]


def drive(fps: float, seconds: float, stop_s: float = 3.0, plate_text: str = "KA 01 AB 2345", seed: int = 0):
    """A drawn car with an Indian plate drives in over the gate line (taking `seconds`, stopping `stop_s` at the boom
    before the line as gate cars do), waits out of view, and drives out the same way, while someone stands on the line
    beside its path; analysed at `fps` through the camera process's own path (tracker, lines, plate reader, passages).
    Returns the passage messages and the crossings."""
    from test_plates import YELLOW, plate, vehicle
    from watchful.components import make_blobs
    from watchful.ingest.sources import Frame
    from watchful.pool import CameraJob, PoolWorker
    from watchful.vision.motion import MotionResult
    import tempfile
    tmp = tempfile.mkdtemp()
    s = Settings(session_secret="y", data_dir=tmp, _env_file=None)
    car = vehicle(plate([plate_text], bg=YELLOW), body=(200, 200, 200), size=(400, 300), plate_width=0.45, seed=seed)
    W, H, line_y = 960, 540, int(0.55 * 540)
    bg = np.full((H, W, 3), (100, 104, 108), np.uint8)
    guard = (400.0, line_y - 170.0, 60.0, 172.0)  # standing on the line at the edge of the car's path
    bg[int(guard[1]):int(guard[1] + guard[3]), 400:460] = (40, 50, 60)
    cam = Camera("cam_gate", "Main gate", "webcam", None, [LINE], workspace_id="ws_a", readers=["plates"])
    sent: list[dict] = []
    worker = PoolWorker(0, s, Tuning(), sent.append, blobs=make_blobs(s))
    worker.assign(CameraJob(cam, [], []))
    r = worker.cameras[cam.id]
    det = SceneDetector()
    r.pipe.perceiver.detector = det
    r.pipe.perceiver.gate.update = lambda image: MotionResult(True, 1.0, [(0, 0, 10, 10)])
    def leg(a: float, b: float, ka: float, kb: float, secs: float) -> list:
        n = max(2, round(secs * fps))
        return [(a + (b - a) * i / (n - 1), ka + (kb - ka) * i / (n - 1)) for i in range(n)]

    half = seconds / 2
    boom_in, boom_out = line_y - 50, line_y + 40  # where the car waits for the guard on each way
    plan = [None] * int(2 * fps)
    plan += leg(80, boom_in, 0.75, 0.85, half) + [(boom_in, 0.85)] * round(stop_s * fps)  # in: nearer the camera
    plan += leg(boom_in, 520, 0.85, 1.1, half) + [None] * int(4 * fps)
    plan += leg(520, boom_out, 1.1, 1.0, half) + [(boom_out, 1.0)] * round(stop_s * fps)  # out: away again
    plan += leg(boom_out, 80, 1.0, 0.75, half) + [None] * int(4 * fps)
    t0 = 1_761_400_000.0
    try:
        for i, step in enumerate(plan):
            img = bg.copy()
            det.boxes = [("person", guard)]
            if step is not None:
                foot, k = step
                sp = cv2.resize(car, None, fx=k, fy=k)
                sh, sw = sp.shape[:2]
                x0, y0 = 600 - sw // 2, int(foot) - sh
                ys, ye = max(0, y0), min(H, y0 + sh)
                img[ys:ye, x0:x0 + sw] = sp[ys - y0:ye - y0]
                det.boxes.append(("car", (float(x0), float(ys), float(sw), float(ye - ys))))
            frame = Frame(img, t0 + i / fps, i)
            r.after(frame, 0.0, r.pipe.process(frame))
        r._passages(r.passages.flush())
    finally:
        worker.close()
    crossings = [(e.cls, e.data.get("direction")) for m_ in sent if m_["type"] == "events" for e in m_["rows"]
                 if e.kind == "cross"]
    return [m_ for m_ in sent if m_["type"] == "passage"], crossings



def logged(repo, messages) -> list[dict]:
    gate = repo.add_camera(Camera("cam_gate", "Main gate", "webcam", None, [LINE], readers=["plates"]))
    for msg in messages:
        e = msg["event"]
        e.camera_id = gate.id
        ledger.record_passage(repo, gate.id, e, msg["plate"])
    with repo.tx() as s:
        rows = list(s.scalars(select(m.VehiclePassage).order_by(m.VehiclePassage.at)))
    return [{"direction": p.direction, "plate": p.plate_text, "colour": p.plate_colour, "paired": p.paired_id}
            for p in rows]


@pytest.mark.parametrize("fps", [5, 2, 1])
def test_a_car_in_and_out_past_someone_at_the_line(repo, fps):
    """One in and one out, paired by the plate read on the way, with a guard standing on the line (Task 5's swap
    guard must not drop the car's crossing); 1 fps is what a loaded camera process analyses (Task 3)."""
    messages, crossings = drive(fps, 6)
    assert crossings == [("car", "in"), ("car", "out")]
    rows = logged(repo, messages)
    assert [(x["direction"], x["plate"], x["colour"]) for x in rows] == [("in", "KA01AB2345", "yellow"),
                                                                         ("out", "KA01AB2345", "yellow")]
    assert all(x["paired"] for x in rows)


def test_a_car_that_does_not_stop_at_one_frame_a_second(repo):
    """Seen only once or twice on each side of the line at 1 fps: the tracker's distance pass (Task 3) keeps its id."""
    messages, crossings = drive(1, 3, stop_s=0)
    assert crossings == [("car", "in"), ("car", "out")]
    assert [x["paired"] is not None for x in logged(repo, messages)] == [True, True]

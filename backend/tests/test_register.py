"""The staff register: enrolment with consent from 3 to 5 photos (templates kept, photos not), the plan limit, first
in and last out per day in society time, the daily roll up, exports, and removal: never named again on any camera
process, attendance kept (Review Focus 5)."""
import csv
import io
import threading
import time
from datetime import UTC, datetime

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from accounts import SOCIETY, browser, society_repo
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings
from watchful.db import models as m
from watchful.logs import register
from watchful.logs.ledger import society_zone
from watchful.store.db import Camera, EventRow
from watchful.store.sql import open_sqlite
from watchful.tuning import FaceTuning, Tuning
from watchful.vision.faces import FaceIndex, FaceMatch, judge
from watchful.vision.models import sample_path


def jpg(image) -> bytes:
    return cv2.imencode(".jpg", image)[1].tobytes()


@pytest.fixture(scope="module")
def photos():
    b1, b2 = (cv2.imread(str(sample_path(n))) for n in ("basketball1.png", "basketball2.png"))
    messi = cv2.imread(str(sample_path("messi5.jpg")))
    two = np.zeros((480, 640 + messi.shape[1], 3), np.uint8)
    two[:, :640], two[:messi.shape[0], 640:] = b1, messi
    return {"b1": b1, "b1_flip": cv2.flip(b1, 1), "b2": b2, "messi": messi, "two": two,
            "none": cv2.imread(str(sample_path("home.jpg")))}


@pytest.fixture
def app(tmp_path):
    from test_api import StubManager
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    return create_app(s, llm=FakeLLM([]), manager=StubManager())


def files(photos, *names, field="photos"):
    return [(field, (f"{n}.jpg", jpg(photos[n]), "image/jpeg")) for n in names]


def test_enrolment_needs_consent(app, photos, tmp_path):
    c = browser(app)
    form = {"name": "Sunita", "role": "maid", "consent": "true"}
    r = c.post("/api/staff", data={**form, "consent": "false"}, files=files(photos, "b1", "b1_flip", "b2"))
    assert r.status_code == 400 and "consent" in r.json()["detail"]
    r = c.post("/api/staff", data=form, files=files(photos, "b1", "b2"))
    assert r.status_code == 400 and r.json()["detail"].startswith("Add 3 to 5 photos")
    assert c.post("/api/staff", data=form, files=files(photos, *["b1"] * 6)).status_code == 400
    for bad, words in (("none", "No face found in photo 3"), ("two", "Photo 3 shows more than one face"),
                       ("messi", "Photo 3 does not look like the same person")):
        r = c.post("/api/staff", data=form, files=files(photos, "b1", "b1_flip", bad))
        assert r.status_code == 400 and r.json()["detail"].startswith(words), r.json()
    assert c.post("/api/staff", data={**form, "role": "boss"}, files=files(photos, "b1", "b1_flip", "b2")) \
        .status_code == 400
    r = c.post("/api/staff", data=form, files=files(photos, "b1", "b1_flip", "b2"))
    assert r.status_code == 200 and r.json()["faces"] == 3
    staff_id = r.json()["id"]
    listed = c.get("/api/staff").json()
    assert [x["name"] for x in listed["staff"]] == ["Sunita"] and listed["staff"][0]["faces"] == 3
    assert "adult" in listed["consent_text"] and "adults" in listed["adults_only"].lower()
    assert listed["photos"] == {"min": 3, "max": 5} and "maid" in listed["roles"]
    # only templates are kept: no photo in storage, no picture key, the consent words stored with the record
    blobs_dir = tmp_path / "blobs"
    assert not blobs_dir.exists() or not [p for p in blobs_dir.rglob("*") if p.is_file()]
    repo = app.state.ctx.repo_for(SOCIETY)
    with repo.tx() as s:
        keys = s.scalars(select(m.StaffFace.picture_key).where(m.StaffFace.staff_id == staff_id)).all()
        staff = s.get(m.Staff, staff_id)
        audit = s.scalars(select(m.Audit).where(m.Audit.action == "staff.enrol")).all()
    assert keys == [None, None, None] and "Sunita is an adult" in staff.consent_text and staff.consent_by
    assert [a.data["staff"] for a in audit] == [staff_id]
    [e] = repo.list_enrollments()
    assert e.person_id == staff_id and len(e.templates()) == 3
    # the earlier route takes the same rules, with the photos under "photo"
    old = c.post("/api/people", data={"name": "Ravi", "consent": "true"}, files=files(photos, "b1", field="photo"))
    assert old.status_code == 400
    old = c.post("/api/people", data={"name": "Ravi", "role": "driver", "consent": "true"},
                 files=files(photos, "b2", "b1", "b1_flip", field="photo"))
    assert old.status_code == 200
    assert {p["name"]: p["thumbnail_url"] for p in c.get("/api/people").json()} == {"Sunita": None, "Ravi": None}
    viewer = browser(app, "viewer@example.com", role="viewer")
    supervisor = browser(app, "sup@example.com", role="supervisor")
    assert viewer.get("/api/staff").status_code == 403
    assert supervisor.get("/api/staff").status_code == 200
    assert supervisor.post("/api/staff", data=form, files=files(photos, "b1", "b1_flip", "b2")).status_code == 403
    assert supervisor.delete(f"/api/staff/{staff_id}").status_code == 403
    assert c.delete(f"/api/staff/{staff_id}").json()["ok"] is True
    assert c.delete("/api/staff/stf_nobody").status_code == 404
    assert [x["name"] for x in c.get("/api/staff").json()["staff"]] == ["Ravi"]
    assert [(x["name"], x["active"]) for x in c.get("/api/staff?removed=true").json()["staff"]] == \
        [("Ravi", True), ("Sunita", False)]


def test_the_plan_limits_enrolled_staff_in_plain_words(app, photos):
    c = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    rng = np.random.default_rng(0)
    for i in range(4):  # the free plan names up to 5
        register.enrol(repo, f"Staff {i}", "cleaner", [rng.normal(size=128).tolist()], None)
    assert c.get("/api/staff").json()["notice"] is None
    register.enrol(repo, "Staff 4", "cleaner", [rng.normal(size=128).tolist()], None)
    listed = c.get("/api/staff").json()  # the page says so before anyone fills the form
    assert (listed["count"], listed["limit"]) == (5, 5)
    assert listed["notice"].startswith("Your plan names up to 5 staff members.")
    r = c.post("/api/staff", data={"name": "Sunita", "role": "maid", "consent": "true"},
               files=files(photos, "b1", "b1_flip", "b2"))
    assert r.status_code == 403 and r.json()["detail"].startswith("Your plan names up to 5 staff members.")
    assert "-" not in r.json()["detail"] and "—" not in r.json()["detail"]
    with pytest.raises(register.Refused) as refused:
        register.enrol(repo, "Late", "cook", [[0.1] * 128], None, most=5, refusal="no more")
    assert refused.value.over_limit
    register.remove(repo, repo.list_enrollments()[0].person_id, None)  # someone left: room for one more
    assert c.post("/api/staff", data={"name": "Sunita", "role": "maid", "consent": "true"},
                  files=files(photos, "b1", "b1_flip", "b2")).status_code == 200


def test_naming_thresholds_come_from_tuning():
    cfg = FaceTuning()
    assert (cfg.name_single, cfg.name_multi, cfg.name_frames, cfg.possible_low) == (0.45, 0.363, 3, 0.30)
    sure = FaceMatch("stf_1", "Sunita", 0.46, 0.4)
    assert judge([sure], cfg) == sure
    near = FaceMatch("stf_1", "Sunita", 0.40, 0.2)
    assert judge([near], cfg) is None and judge([near, near], cfg) is None
    assert judge([near, near, near], cfg) == near  # three looks at name_multi with a margin
    close_call = FaceMatch("stf_1", "Sunita", 0.40, 0.02)  # barely ahead of someone else
    assert judge([close_call] * 3, cfg) is None
    other = FaceMatch("stf_2", "Ravi", 0.40, 0.2)
    assert judge([near, other, near], cfg) is None
    possible = FaceMatch("stf_1", "Sunita", 0.32, 0.3)  # possible, never a name
    assert judge([possible] * 10, cfg) is None


def named(cam, ts, kind="track_end", person="stf_x", duration=0.0) -> EventRow:
    data = {"person_id": person, "known_by": "face"} if person else {}
    if kind == "track_end":
        data["duration_s"] = duration
    if kind == "cross":
        data["direction"] = "in"
    return EventRow(None, cam.id, ts, kind, 1, "person", None, data, None, None, None, "Sunita")


def utc(*a) -> float:
    return datetime(*a, tzinfo=UTC).timestamp()


def test_first_in_last_out(tmp_path):
    repo = open_sqlite(tmp_path / "w.db")  # Asia/Kolkata
    gate = repo.add_camera(Camera(None, "Service gate", "stream", "rtsp://s", readers=["faces"]))
    lobby = repo.add_camera(Camera(None, "Lobby", "stream", "rtsp://l"))
    sunita = register.enrol(repo, "Sunita", "maid", [[0.1] * 128], None)
    ravi = register.enrol(repo, "Ravi", "driver", [[0.2] * 128], None)
    absent = register.enrol(repo, "Kamla", "cook", [[0.3] * 128], None)
    rows = [named(gate, utc(2026, 10, 25, 3, 0), person=sunita.id, duration=60),  # 08:29 to 08:30 IST
            named(lobby, utc(2026, 10, 25, 11, 30), "cross", person=sunita.id),  # 17:00 IST
            named(gate, utc(2026, 10, 25, 12, 0), person=sunita.id, duration=30),  # out of view at 17:30 IST
            named(gate, utc(2026, 10, 25, 19, 0), person=sunita.id),  # 00:30 IST: the next day
            named(gate, utc(2026, 10, 25, 4, 0), person="stf_someone_else"),
            named(gate, utc(2026, 10, 25, 4, 0), person=None)]
    repo.add_events(rows)
    assert register.on_events(repo, rows) == 2
    day = {r["name"]: r for r in register.attendance(repo, "2026-10-25")}
    assert day["Sunita"]["first_in"] == "2026-10-25T08:29:00+05:30"
    assert day["Sunita"]["last_out"] == "2026-10-25T17:30:00+05:30"
    assert sorted(day["Sunita"]["cameras"]) == ["Lobby", "Service gate"]
    assert day["Kamla"]["present"] is False and day["Ravi"]["present"] is False
    assert [r["name"] for r in register.attendance(repo, "2026-10-26") if r["present"]] == ["Sunita"]
    # the daily roll up works the day out again from the stored events: a missed live update is filled in
    ravi_rows = [named(gate, utc(2026, 10, 25, 2, 0), person=ravi.id, duration=120),
                 named(gate, utc(2026, 10, 25, 14, 0), person=ravi.id)]
    repo.add_events(ravi_rows)
    assert register.roll_up(repo, "2026-10-25") == 2
    assert register.roll_up(repo, "2026-10-25") == 2  # safe to repeat
    day = {r["name"]: r for r in register.attendance(repo, "2026-10-25")}
    assert (day["Ravi"]["first_in"], day["Ravi"]["last_out"]) == ("2026-10-25T07:28:00+05:30",
                                                                  "2026-10-25T19:30:00+05:30")
    assert day["Sunita"]["first_in"] == "2026-10-25T08:29:00+05:30"
    # removed: no longer listed when absent, kept with their history where they were in
    register.remove(repo, sunita.id, None)
    assert register.on_events(repo, [named(gate, utc(2026, 10, 27, 3, 0), person=sunita.id)]) == 0
    day = {r["name"]: r for r in register.attendance(repo, "2026-10-25")}
    assert day["Sunita"]["active"] is False and day["Sunita"]["first_in"] == "2026-10-25T08:29:00+05:30"
    assert "Sunita" not in {r["name"] for r in register.attendance(repo, "2026-10-27")}
    assert absent.id in {r["staff_id"] for r in register.attendance(repo, "2026-10-27")}


def test_roll_up_runs_in_society_time(tmp_path):
    from watchful.components import make_engine
    from watchful.db.session import migrate
    from watchful.jobs import JobQueue, Worker
    from watchful.store.sql import SqlRepository, create_workspace
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    engine = make_engine(s)
    migrate(engine)
    ws = create_workspace(engine, "Green Park").id
    repo = SqlRepository(engine, ws)
    staff = register.enrol(repo, "Sunita", "maid", [[0.1] * 128], None)
    gate = repo.add_camera(Camera(None, "Service gate", "stream", "rtsp://s"))
    repo.add_events([named(gate, utc(2026, 10, 25, 3, 0), person=staff.id, duration=60)])
    worker = Worker(JobQueue(engine), s, Tuning(), repo_for=lambda w: SqlRepository(engine, w))
    assert register.queue_roll_ups(worker, datetime(2026, 10, 25, 18, 45, tzinfo=UTC)) == []  # 00:15 in Kolkata
    assert register.queue_roll_ups(worker, datetime(2026, 10, 25, 19, 45, tzinfo=UTC)) == [ws]  # 01:15
    assert register.queue_roll_ups(worker, datetime(2026, 10, 25, 20, 45, tzinfo=UTC)) == []  # queued once
    worker.run_due(now=datetime(2026, 10, 25, 19, 46, tzinfo=UTC))
    [row] = register.attendance(repo, "2026-10-25")
    assert row["present"] and row["first_in"] == "2026-10-25T08:29:00+05:30"


def test_staff_register_api_and_csv(app):
    c = browser(app)
    repo = app.state.ctx.repo_for(SOCIETY)
    gate = repo.add_camera(Camera(None, "Service gate", "stream", "rtsp://s", readers=["faces"]))
    sunita = register.enrol(repo, "=cmd", "maid", [[0.1] * 128], None)
    register.on_events(repo, [named(gate, utc(2026, 10, 25, 3, 0), person=sunita.id, duration=60)])
    body = c.get("/api/logs/staff?day=2026-10-25").json()
    assert body["present"] == 1 and body["staff"][0]["first_in"] == "2026-10-25T08:29:00+05:30"
    r = c.get("/api/logs/staff.csv?day=2026-10-25")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["Date", "Name", "Role", "First in", "Last out", "Cameras", "Still enrolled"]
    assert rows[1] == ["2026-10-25", "'=cmd", "maid", "08:29:00", "08:30:00", "Service gate", "yes"]
    assert "staff_register_2026-10-25.csv" in r.headers["content-disposition"]
    with app.state.ctx.db.tx() as s:
        assert [a.data["log"] for a in s.scalars(select(m.Audit).where(m.Audit.action == "logs.export"))] == ["staff"]
    assert browser(app, "viewer@example.com", role="viewer").get("/api/logs/staff.csv").status_code == 403
    assert browser(app, "lake@example.com", workspace_id="ws_lake").get(
        "/api/logs/staff?day=2026-10-25").json()["staff"] == []


def _pusher(manager, cams, image, stop: threading.Event, on_s: float = 4.0, off_s: float = 3.0):
    """Pushes the person walking across an empty scene to every camera at about 4 frames a second for on_s, then the
    empty scene for off_s so the tracks end (a named track's name is in its track_end event), and again, each walk
    starting somewhere else so the motion gate never learns it as background."""
    h, w = image.shape[:2]
    scene = np.full((540, 960, 3), 120, np.uint8)
    empty = jpg(scene)
    i, start = 0, time.time()
    while not stop.is_set():
        elapsed = time.time() - start
        walk, into = divmod(elapsed, on_s + off_s)
        frame = empty
        if into < on_s:
            x = int((int(walk) * 97) % 200 + into * 20)
            canvas = scene.copy()
            canvas[30:30 + h, x:x + w] = image
            frame = jpg(canvas)
        for cam in cams:
            manager.push_frame(cam.id, frame, time.time())
        i += 1
        time.sleep(0.25)


def _wait(cond, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return False


def test_deleted_staff_is_never_named_again(tmp_path, photos):
    """Review Focus 5 across the shared camera processes: two cameras of one society on two processes name Sunita;
    once her enrolment is deleted she is not named again on either (at once when the app tells them, within
    faces.reload_s when nobody does), her templates are gone from every process, and her attendance stays."""
    from watchful.components import make_blobs
    from watchful.workers import WorkerManager
    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 analysis_fps=4.0, display_fps=4.0, _env_file=None)
    t = Tuning.model_validate({"faces": {"reload_s": 3.0}})
    repo = society_repo(s)
    ws = repo.workspace_id
    faces = FaceIndex()
    templates = [faces.template(photos[n]) for n in ("b1", "b1_flip")]
    sunita = register.enrol(repo, "Sunita", "maid", templates, None)
    cams = [repo.add_camera(Camera(None, f"Gate {i}", "webcam", None, readers=["faces"])) for i in range(2)]
    mgr = WorkerManager(s, t, repo, make_blobs(s), llm=FakeLLM([]), processes=2)
    stop = threading.Event()
    pusher = threading.Thread(target=_pusher, args=(mgr, cams, photos["b2"], stop), daemon=True)

    def named_since(since: float) -> set[str]:
        return {e.camera_id for e in repo.query_events(since=since, limit=5000) if e.person_label == "Sunita"}

    def people_in_processes() -> list[list[str]]:
        return [p.get("people", {}).get(ws, []) for p in mgr.processes.values()]

    try:
        for cam in cams:
            mgr.start(cam.id)
        assert {mgr.status(c.id).process for c in cams} == {0, 1}  # one camera on each process
        pusher.start()
        start = time.time()
        assert _wait(lambda: named_since(start) == {c.id for c in cams}, 120), "Sunita was never named"
        today = datetime.now(society_zone(repo)).date().isoformat()
        assert _wait(lambda: any(r["present"] for r in register.attendance(repo, today)), 30)  # live updates

        # deleted with nobody telling the camera processes: the periodic resend reaches them within reload_s
        register.remove(repo, sunita.id, None)
        gone = time.time()
        time.sleep(t.faces.reload_s + 10)  # more than one cycle of tracks ending and new ones starting
        assert named_since(gone + t.faces.reload_s + 0.5) == set()
        assert _wait(lambda: len(mgr.processes) == 2 and people_in_processes() == [[], []], 30)

        # enrolled again: named again; deleted the way the app does it: forgotten at once, names already given too
        again = register.enrol(repo, "Sunita", "maid", templates, None)
        mgr.reload_faces(ws)
        back = time.time()
        assert _wait(lambda: named_since(back) == {c.id for c in cams}, 120), \
            ([(e.camera_id[-4:], e.kind, e.person_label, round(e.ts - back, 1)) for e in
              repo.query_events(since=back, kinds=["track_start", "track_end"], limit=5000)],
             people_in_processes(), [x.person_id for x in repo.list_enrollments()], again.id,
             {c.id[-4:]: mgr.status(c.id).stats for c in cams})
        register.remove(repo, again.id, None)
        mgr.reload_faces(ws)
        gone = time.time()
        time.sleep(9)  # tracks named before end without the name, and new ones get none
        assert named_since(gone + 1.0) == set()
        with repo.tx() as tx:
            rows = tx.scalars(select(m.Attendance).where(m.Attendance.workspace_id == ws)).all()
            assert {a.staff_id for a in rows} == {sunita.id, again.id}  # attendance history stays
            assert tx.scalars(select(m.StaffFace).where(m.StaffFace.workspace_id == ws)).all() == []
    finally:
        stop.set()
        mgr.shutdown()


def test_the_demo_day_fills_both_logs_with_nobody_real(tmp_path, photos):
    from watchful.logs import demo, ledger
    from watchful.store.blobs import LocalBlobStore
    repo = open_sqlite(tmp_path / "w.db")
    gate = repo.add_camera(Camera(None, "Main gate", "demo", "x.mp4"))
    service = repo.add_camera(Camera(None, "Service gate", "demo", "y.mp4"))
    blobs = LocalBlobStore(tmp_path)
    now = datetime(2026, 10, 26, 6, 30, tzinfo=UTC)  # noon in Kolkata
    with repo.tx() as s:
        made = demo.seed(s, repo.workspace_id, {"main_gate": gate.id, "service_gate": service.id}, now, blobs)
    assert made["passages"] > 20 and made["staff"] == 6 and made["attendance"] >= 6
    assert repo.get_camera(gate.id).readers == ["plates"] and repo.get_camera(service.id).readers == ["faces"]
    today = ledger.passages(repo, "2026-10-26")
    both = ledger.passages(repo, "2026-10-25") + today
    assert today and all(datetime.fromisoformat(p["at"]) <= now for p in both)
    assert any(p["paired_id"] for p in today) and any(p["plate"] is None for p in both)
    assert blobs.get(f"{repo.workspace_id}/{gate.id}/passages/demo_0.jpg")
    fired = ledger.check_stays(repo, now, Tuning())
    assert any(w == ledger.OVERSTAY for _, w in fired)  # the cab still inside opens an incident
    present = [r for r in register.attendance(repo, "2026-10-26") if r["present"]]
    assert {r["name"] for r in present} <= {"Sunita", "Kamla", "Ravi", "Mohan", "Geeta", "Imran"} and present
    assert all(r["first_in"] <= r["last_out"] for r in present if r["last_out"])
    # the templates are random vectors: no real face is ever named or even possible
    index = FaceIndex()
    index.load(repo.list_enrollments())
    assert len(index.people()) == 6
    assert index.identify(photos["b2"], (0, 0, 640, 480), floor=Tuning().faces.possible_low) is None
    with repo.tx() as s:  # seeding again adds no second set of staff
        assert demo.seed(s, repo.workspace_id, {}, now)["staff"] == 0

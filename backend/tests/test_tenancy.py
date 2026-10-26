"""Every row belongs to one society, and a repository bound to one society never reads or changes another's."""
import json
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass

import pytest

from watchful import components
from watchful.config import Settings
from watchful.db.session import make_engine, migrate
from watchful.store.db import DEFAULT_WORKSPACE, AlertRow, Camera, EventRow, WatchRow
from watchful.store.sql import SqlRepository, create_workspace
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone


@dataclass
class Rows:
    camera: Camera
    event: EventRow
    scene: EventRow
    watch: WatchRow
    alert: AlertRow
    person_id: str


@pytest.fixture
def societies(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    migrate(engine)
    a, b = create_workspace(engine, "Green Park"), create_workspace(engine, "Lake View")
    return SqlRepository(engine, a.id, secret="s"), SqlRepository(engine, b.id, secret="s")


def fill(repo, tag: str) -> Rows:
    gate = Zone("z1", "gate", [(0, 0), (1, 1)])
    cam = repo.add_camera(Camera(None, f"{tag} gate", "stream", f"rtsp://{tag}", [gate]))
    event = EventRow(None, cam.id, 10.0, "enter", 1, "person", "gate", {}, "k.jpg", [0.1], None, f"{tag} label")
    scene = EventRow(None, cam.id, 20.0, "scene", None, None, None, {}, "s.jpg", None, None, None)
    repo.add_events([event, scene])
    watch = repo.add_watch(WatchRow(None, cam.id, f"{tag} watch", {"name": tag}, True))
    repo.add_watch(WatchRow(None, None, f"{tag} site watch", {"name": tag}, True, scope="site"))
    alert = repo.add_alert(AlertRow(None, cam.id, watch.id, time.time(), "critical", "alert", tag, None, None, [], None,
                                    f"fp-{tag}"))
    repo.add_enrollment(Enrollment(f"per_{tag}", f"{tag} staff", [0.5] * 4))
    repo.set_person_label(cam.id, "g1", tag)
    repo.set_meta("embedder", tag)
    repo.record_ai_call()
    return Rows(cam, event, scene, watch, alert, f"per_{tag}")


def test_a_society_never_sees_another_societys_rows(societies):
    a, b = societies
    rows = {"A": fill(a, "A"), "B": fill(b, "B")}
    for repo, mine, theirs in ((a, "A", "B"), (b, "B", "A")):
        own, other = rows[mine], rows[theirs]
        ws = repo.workspace_id
        assert [c.id for c in repo.list_cameras()] == [own.camera.id]
        assert {e.id for e in repo.query_events()} == {own.event.id, own.scene.id}
        assert repo.query_events(camera_id=other.camera.id) == []
        assert [e.id for e in repo.events_missing_embedding(["scene"])] == [own.scene.id]
        assert {w.text for w in repo.list_watches()} == {f"{mine} watch", f"{mine} site watch"}
        assert {w.text for w in repo.list_watches(other.camera.id)} == {f"{mine} site watch"}
        assert [x.id for x in repo.list_alerts()] == [own.alert.id] and repo.list_alerts(other.camera.id) == []
        assert not repo.alert_fingerprint_exists(f"fp-{theirs}", 60) and repo.alert_fingerprint_exists(f"fp-{mine}", 60)
        assert [e.name for e in repo.list_enrollments()] == [f"{mine} staff"]
        assert repo.person_labels(own.camera.id) == {"g1": mine} and repo.person_labels(other.camera.id) == {}
        assert repo.get_meta("embedder") == mine and repo.count_ai_calls_today() == 1
        everything = [*repo.list_cameras(), *repo.query_events(), *repo.list_watches(), *repo.list_alerts(),
                      *repo.list_enrollments()]
        assert {x.workspace_id for x in everything} == {ws}
    a.set_event_embedding(rows["A"].scene.id, [1.0, 0.0])
    b.set_event_embedding(rows["B"].scene.id, [0.0, 1.0])
    assert a.clear_embeddings() == 1
    assert b.get_event(rows["B"].scene.id).embedding == [0.0, 1.0] and b.events_missing_embedding(["scene"]) == []


def test_ids_from_another_society_are_not_found(societies):
    a, b = societies
    fill(a, "A")
    theirs = fill(b, "B")
    assert a.get_camera(theirs.camera.id) is None
    assert a.get_event(theirs.event.id) is None and a.get_alert(theirs.alert.id) is None

    a.update_camera(Camera(theirs.camera.id, "Taken", "stream", "rtsp://evil", []))
    a.update_watch(WatchRow(theirs.watch.id, theirs.camera.id, "taken", {"name": "taken"}, False))
    a.update_alert(AlertRow(theirs.alert.id, theirs.camera.id, None, 0.0, "info", "dismissed", "taken", None, None, [],
                            "down", "fp"))
    a.set_event_embedding(theirs.scene.id, [1.0])
    a.set_person_label(theirs.camera.id, "g1", "taken")
    assert a.delete_camera_events(theirs.camera.id) == 0
    a.delete_camera(theirs.camera.id)
    a.delete_enrollment(theirs.person_id)
    with suppress(Exception):
        a.add_enrollment(Enrollment(theirs.person_id, "Taken", [0.1] * 4))
    with suppress(Exception):
        a.add_camera(Camera(theirs.camera.id, "Taken", "stream", "rtsp://evil"))
    a.delete_events_before(1e12)
    a.delete_alerts_before(1e12)

    cam = b.get_camera(theirs.camera.id)
    assert cam.name == "B gate" and cam.url == "rtsp://B" and [z.name for z in cam.zones] == ["gate"]
    assert {e.id for e in b.query_events()} == {theirs.event.id, theirs.scene.id}
    assert b.events_missing_embedding(["scene"])[0].id == theirs.scene.id
    watch = next(w for w in b.list_watches() if w.id == theirs.watch.id)
    assert watch.enabled and watch.text == "B watch"
    alert = b.get_alert(theirs.alert.id)
    assert alert.status == "alert" and alert.feedback is None and alert.summary == "B"
    assert [e.name for e in b.list_enrollments()] == ["B staff"]
    assert b.person_labels(theirs.camera.id) == {"g1": "B"}


def test_make_repo_scopes_by_workspace(tmp_path):
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    engine = components.make_engine(s)
    green, lake = (components.make_repo(s, create_workspace(engine, n).id) for n in ("Green Park", "Lake View"))
    cam = green.add_camera(Camera(None, "Plaza", "demo", "/clips/plaza.mp4"))
    assert cam.workspace_id == green.workspace_id and lake.list_cameras() == []
    assert components.make_engine(s) is engine


def test_rows_under_another_societys_ids_are_refused(societies):
    a, b = societies
    theirs = fill(b, "B")
    for write in (lambda: a.add_watch(WatchRow(theirs.watch.id, None, "x", {"name": "x"}, True)),
                  lambda: a.add_alert(AlertRow(theirs.alert.id, "c", None, 1.0, "info", "alert", "x", None, None, [],
                                               None, "fp")),
                  lambda: a.add_events([EventRow(theirs.event.id, "c", 1.0, "enter", 1, "person", None, {}, None,
                                                 None, None, None)])):
        with pytest.raises(Exception):
            write()
    assert next(w for w in b.list_watches() if w.id == theirs.watch.id).text == "B watch"
    assert b.get_alert(theirs.alert.id).summary == "B" and b.get_event(theirs.event.id).zone == "gate"
    assert a.list_watches() == [] and a.list_alerts() == [] and a.query_events() == []


LEGACY_SCHEMA = """
create table cameras (id text primary key, data text not null);
create table events (id text primary key, camera_id text, ts real, kind text, zone text, cls text, data text not null,
                     embedded integer default 0);
create table watches (id text primary key, camera_id text, data text not null);
create table alerts (id text primary key, camera_id text, ts real, fingerprint text, data text not null);
create table enrollments (person_id text primary key, data text not null);
create table meta (key text primary key, value text);
create table person_labels (camera_id text, group_id text, label text, primary key (camera_id, group_id));
"""


def test_an_earlier_database_becomes_the_default_society(tmp_path):
    old = sqlite3.connect(tmp_path / "watchful.db")
    old.executescript(LEGACY_SCHEMA)
    cam = {"id": "cam_1", "name": "Plaza", "kind": "demo", "url": "/clips/plaza.mp4", "enabled": True, "tuning": {},
           "last_scan": None, "zones": [{"id": "z1", "name": "door", "polygon": [[0, 0], [1, 1]]}]}
    event = {"id": "evt_1", "camera_id": "cam_1", "ts": 5.0, "kind": "enter", "track_id": 1, "cls": "person",
             "zone": "door", "data": {}, "keyframe_key": "k.jpg", "signature": None, "embedding": [0.5],
             "person_label": None}
    watch = {"id": "wat_1", "camera_id": "cam_1", "text": "enters", "watch": {"name": "enters"}, "enabled": True}
    alert = {"id": "alt_1", "camera_id": "cam_1", "watch_id": "wat_1", "ts": 6.0, "severity": "info",
             "status": "alert", "summary": "came in", "clip_key": None, "keyframe_key": None, "trace": [],
             "feedback": None, "fingerprint": "fp"}
    with old:
        old.execute("insert into cameras values (?, ?)", ("cam_1", json.dumps(cam)))
        old.execute("insert into events (id, data) values (?, ?)", ("evt_1", json.dumps(event)))
        old.execute("insert into watches values (?, ?, ?)", ("wat_1", "cam_1", json.dumps(watch)))
        old.execute("insert into alerts (id, data) values (?, ?)", ("alt_1", json.dumps(alert)))
        old.execute("insert into meta values ('embedder', 'siglip2_b32')")
    old.close()
    repo = components.make_repo(Settings(session_secret="y", data_dir=tmp_path, _env_file=None), DEFAULT_WORKSPACE)
    got = repo.get_camera("cam_1")
    assert got.kind == "demo" and got.zones[0].name == "door" and got.workspace_id == DEFAULT_WORKSPACE
    assert repo.get_event("evt_1").embedding == [0.5] and repo.list_watches("cam_1")[0].text == "enters"
    assert repo.get_alert("alt_1").summary == "came in" and repo.get_meta("embedder") == "siglip2_b32"
    assert len(list(tmp_path.glob("watchful.legacy-*.db"))) == 1 and not list(tmp_path.glob("*.importing*"))


def test_a_failed_import_leaves_the_earlier_database_in_place(tmp_path, monkeypatch):
    from watchful.db import legacy
    old = sqlite3.connect(tmp_path / "watchful.db")
    old.executescript(LEGACY_SCHEMA)
    with old:
        old.execute("insert into cameras values ('cam_1', '{\"not\": \"a camera\"}')")
    old.close()
    before = (tmp_path / "watchful.db").read_bytes()
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    with pytest.raises(TypeError):
        components.make_engine(s)
    assert (tmp_path / "watchful.db").read_bytes() == before and legacy.is_legacy(tmp_path / "watchful.db")
    assert not list(tmp_path.glob("*.importing*")) and not list(tmp_path.glob("*.legacy-*"))


def write_legacy(path) -> bytes:
    old = sqlite3.connect(path)
    old.executescript(LEGACY_SCHEMA)
    cam = {"id": "cam_1", "name": "Plaza", "kind": "demo", "url": "/clips/plaza.mp4", "enabled": True, "tuning": {},
           "last_scan": None, "zones": []}
    with old:
        old.execute("insert into cameras values (?, ?)", ("cam_1", json.dumps(cam)))
    old.close()
    return path.read_bytes()


def test_a_crash_during_the_import_never_leaves_the_path_empty(tmp_path, monkeypatch):
    """The old file is kept under its new name first, then one rename puts the new file in its place: stopped at the
    last step, the path still holds the whole old database, and the next start imports it."""
    from pathlib import Path
    from watchful.db import legacy
    path = tmp_path / "watchful.db"
    before = write_legacy(path)
    rename = Path.replace

    def stop_at_the_swap(self, target):
        if self.name == "watchful.importing.db":
            raise KeyboardInterrupt  # the process is killed here
        return rename(self, target)

    monkeypatch.setattr(Path, "replace", stop_at_the_swap)
    with pytest.raises(KeyboardInterrupt):
        legacy.adopt_legacy(f"sqlite:///{path}", None)
    assert path.read_bytes() == before and legacy.is_legacy(path)
    (kept,) = tmp_path.glob("watchful.legacy-*.db")
    assert kept.read_bytes() == before
    monkeypatch.undo()
    repo = components.make_repo(Settings(session_secret="y", data_dir=tmp_path, _env_file=None), DEFAULT_WORKSPACE)
    assert repo.get_camera("cam_1").name == "Plaza" and not legacy.is_legacy(path)
    assert not list(tmp_path.glob("*.importing*"))


def test_two_processes_starting_together_import_once(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "watchful.db"
    write_legacy(path)
    start = ("import sys, time\n"
             "from watchful.db import legacy\n"
             "real = legacy.import_legacy\n"
             "def slow(*args):\n"
             "    time.sleep(2)\n"
             "    return real(*args)\n"
             "legacy.import_legacy = slow\n"
             "print(legacy.adopt_legacy(sys.argv[1], None))\n")
    import os
    from pathlib import Path
    import watchful
    backend = str(Path(watchful.__file__).resolve().parents[1])  # the package's own path, run from any checkout
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [backend, os.environ.get("PYTHONPATH")]))}
    procs = [subprocess.Popen([sys.executable, "-c", start, f"sqlite:///{path}"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env) for _ in range(2)]
    outs = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outs
    printed = [out.strip().splitlines()[-1] for out, _ in outs]
    assert printed.count("None") == 1, printed  # the other waited, then found the new file
    (kept,) = tmp_path.glob("watchful.legacy-*.db")
    assert str(kept) in printed and not list(tmp_path.glob("*.importing*"))
    repo = components.make_repo(Settings(session_secret="y", data_dir=tmp_path, _env_file=None), DEFAULT_WORKSPACE)
    assert [c.id for c in repo.list_cameras()] == ["cam_1"]


def test_faces_never_match_across_societies(societies, tmp_path):
    """Sunita, enrolled in Green Park, is never named on Lake View's camera, even with both cameras in one camera
    process sharing the face networks."""
    import cv2
    from watchful.components import make_blobs
    from watchful.ingest.sources import Frame
    from watchful.pool import CameraJob, PoolWorker
    from watchful.rules.watch import Condition, Subject, Watch
    from watchful.tuning import Tuning
    from watchful.vision.faces import FaceIndex
    from watchful.vision.models import sample_path
    from watchful.vision.motion import MotionResult
    a, b = societies
    a.add_enrollment(FaceIndex().enroll("per_sunita", "Sunita", cv2.imread(str(sample_path("basketball1.png"))),
                                        consent=True))
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    worker = PoolWorker(0, s, Tuning(), [].append, blobs=make_blobs(s))
    staff = Watch(name="staff seen", subject=Subject(who="known"), conditions=[Condition(signal="present")])
    cam_a = a.add_camera(Camera(None, "Green Park gate", "webcam", None))
    cam_b = b.add_camera(Camera(None, "Lake View gate", "webcam", None))
    later = cv2.imread(str(sample_path("basketball2.png")))
    try:
        worker.assign(CameraJob(cam_a, [("w1", staff)], a.list_enrollments()))
        worker.assign(CameraJob(cam_b, [("w2", staff)], b.list_enrollments()))
        names = {}
        for cam in (cam_a, cam_b):
            pipe = worker.cameras[cam.id].pipe
            pipe.perceiver.gate.update = lambda image: MotionResult(True, 1.0, [(0, 0, 10, 10)])  # always moving
            for i in range(4):  # the same picture of Sunita, a second apart, detected and tracked each time
                tick = pipe.process(Frame(later, 100.0 + i, i)).tick
            names[cam.workspace_id] = {t.known_name for t in tick.tracks if t.cls == "person"}
        assert "Sunita" in names[a.workspace_id] and names[b.workspace_id] == {None}
        faces_a = worker.cameras[cam_a.id].pipe.perceiver.faces
        faces_b = worker.cameras[cam_b.id].pipe.perceiver.faces
        assert faces_b.people() == [] and faces_a.detector is faces_b.detector
        assert worker.models.loads["faces"] == 1  # one set of face networks for both societies
        worker.reload_faces(b.workspace_id, a.list_enrollments())  # a wrong list is refused by B's index
        assert faces_b.people() == [] and faces_b.identify(later, (40, 80, 100, 250)) is None
        worker.reload_faces(a.workspace_id, [])  # forgotten in A
        assert faces_a.identify(later, (40, 80, 100, 250)) is None
    finally:
        worker.close()


def test_old_blob_keys_move_under_their_society(societies, tmp_path):
    """Keys from before societies (camera id or people/ first) move under the society's prefix once, rows and
    blobs together; another society's rows are not touched."""
    from watchful.store.blobs import LocalBlobStore
    from watchful.store.rekey import rekey_society
    a, b = societies
    blobs = LocalBlobStore(tmp_path)
    cam = a.add_camera(Camera(None, "Gate", "stream", "rtsp://x"))
    kf, face = f"{cam.id}/kf/1.jpg", "people/per_x.jpg"
    blobs.put(kf, b"keyframe", "image/jpeg")
    blobs.put(face, b"face", "image/jpeg")
    a.add_events([EventRow(None, cam.id, 1.0, "enter", 1, "person", None, {}, kf, None, None, None)])
    alert = a.add_alert(AlertRow(None, cam.id, None, 1.0, "info", "alert", "x", f"{cam.id}/clips/gone.mp4", kf, [],
                                 None, "fp-old"))
    a.add_enrollment(Enrollment("per_x", "Staff", [0.5] * 4, thumbnail_key=face))
    a.add_watch(WatchRow(None, cam.id, "same as", {"name": "x", "subject": {"same_as": {"crop_key": kf}}}, True))
    other = b.add_camera(Camera(None, "Other", "stream", "rtsp://y"))
    b.add_events([EventRow(None, other.id, 1.0, "enter", 1, "person", None, {}, f"{other.id}/kf/1.jpg", None, None,
                           None)])
    assert rekey_society(a, blobs) == 2  # the clip was already gone; its row moves all the same
    ws = a.workspace_id
    assert [e.keyframe_key for e in a.query_events()] == [f"{ws}/{kf}"]
    got = a.get_alert(alert.id)
    assert (got.clip_key, got.keyframe_key) == (f"{ws}/{cam.id}/clips/gone.mp4", f"{ws}/{kf}")
    assert [e.thumbnail_key for e in a.list_enrollments()] == [f"{ws}/{face}"]
    assert a.list_watches()[0].watch["subject"]["same_as"]["crop_key"] == f"{ws}/{kf}"
    assert blobs.get(f"{ws}/{kf}") == b"keyframe" and blobs.get(f"{ws}/{face}") == b"face"
    assert not (tmp_path / "blobs" / kf).exists()
    assert [e.keyframe_key for e in b.query_events()] == [f"{other.id}/kf/1.jpg"]
    assert rekey_society(a, blobs) == 0


def test_a_rekey_stopped_midway_finishes_on_the_next_start(societies, tmp_path):
    """Stopped after the rows moved but before the old blobs went, the next start deletes each old blob whose copy is
    there; once done, retention no longer keeps the bare people/ prefix."""
    import os
    from watchful.store.blobs import LocalBlobStore
    from watchful.store.rekey import rekey_society
    from watchful.workers import run_retention
    a, _ = societies
    blobs = LocalBlobStore(tmp_path)
    face = "people/per_x.jpg"
    blobs.put(face, b"face", "image/jpeg")
    a.add_enrollment(Enrollment("per_x", "Staff", [0.5] * 4, thumbnail_key=face))
    real = blobs.delete

    def stop(key):
        raise KeyboardInterrupt  # the app stops here

    blobs.delete = stop
    with pytest.raises(KeyboardInterrupt):
        rekey_society(a, blobs)
    blobs.delete = real
    rekey_society(a, blobs)
    ws = a.workspace_id
    assert [e.thumbnail_key for e in a.list_enrollments()] == [f"{ws}/{face}"]
    assert blobs.get(f"{ws}/{face}") == b"face" and not (tmp_path / "blobs" / face).exists()
    stray = tmp_path / "blobs" / "people" / "stray.jpg"  # a bare key no row points at
    stray.write_bytes(b"x")
    old = time.time() - 400 * 86400
    os.utime(stray, (old, old))
    run_retention(Settings(session_secret="y", data_dir=tmp_path, _env_file=None), [a], blobs)
    assert not stray.exists() and blobs.get(f"{ws}/{face}")


def test_threads_share_a_societys_face_networks_one_at_a_time():
    import threading
    import cv2
    from watchful.vision.faces import FaceIndex
    from watchful.vision.models import sample_path
    base = FaceIndex()
    one, two = base.share([]), base.share([])
    assert one.lock is two.lock is base.lock
    one.enroll("p1", "Player", cv2.imread(str(sample_path("basketball1.png"))), consent=True)
    big = cv2.imread(str(sample_path("basketball2.png")))
    small = cv2.resize(big, None, fx=0.5, fy=0.5)
    expected = {"big": one.identify(big, (40, 80, 100, 250)).person_id, "small": one.has_face(small)}
    errors = []

    def work(kind):
        for _ in range(30):
            got = one.identify(big, (40, 80, 100, 250)).person_id if kind == "big" else two.has_face(small)
            if got != expected[kind]:
                errors.append(kind)

    threads = [threading.Thread(target=work, args=(k,)) for k in ("big", "small", "big", "small")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_a_scan_writes_only_under_its_cameras_society(tmp_path):
    from watchful.vision.scan import chunk_blobs
    blobs = chunk_blobs(Settings(session_secret="y", data_dir=tmp_path, _env_file=None),
                        Camera("cam_s", "Upload", "file", None, workspace_id="ws_a"))
    blobs.put("ws_a/cam_s/kf/1.jpg", b"x", "image/jpeg")
    blobs.put("ws_b/cam_s/kf/1.jpg", b"x", "image/jpeg")
    with pytest.raises(KeyError):
        blobs.flush()
    assert (tmp_path / "blobs" / "ws_a" / "cam_s" / "kf" / "1.jpg").exists()
    assert not (tmp_path / "blobs" / "ws_b").exists()

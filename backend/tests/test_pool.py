"""Live cameras share a few camera processes: each process loads its models once for all its cameras, and a process
that dies is started again with its cameras."""
import os
import signal
import subprocess
import sys
import time

import cv2
import numpy as np
import pytest

from accounts import society_repo
from watchful.agent.llm import FakeLLM
from watchful.components import make_blobs
from watchful.config import Settings
from watchful.pool import WorkerPool
from watchful.rules.presets import preset
from watchful.store.db import Camera, WatchRow
from watchful.tuning import Tuning
from watchful.vision.detect import COCO
from watchful.vision.models import sample_path
from watchful.vision.zones import Zone
from watchful.workers import WorkerManager

LEFT = Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])


@pytest.fixture(scope="module")
def clip20():
    out = sample_path("vtest20.mp4")
    if not out.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sample_path("vtest.avi")), "-t", "20",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def wait(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return False


@pytest.fixture
def manager(tmp_path):
    made = []

    def make(processes: int = 2, **kw):
        s = Settings(session_secret="y", data_dir=tmp_path, demo_realtime=True, index_embeddings=False,
                     scan_pool_at_start=False, analysis_fps=2.0, display_fps=4.0, _env_file=None, **kw)
        repo = society_repo(s)
        m = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=FakeLLM(['{"tool": "dismiss", "args": {}}'] * 50),
                          processes=processes)
        made.append(m)
        return m, repo

    yield make
    for m in made:
        m.shutdown()


def demo_cameras(repo, clip, n: int) -> list[Camera]:
    cams = []
    for i in range(n):
        cam = repo.add_camera(Camera(None, f"Plaza {i}", "demo", str(clip), [LEFT]))
        repo.add_watch(WatchRow(None, cam.id, "someone enters the left side",
                                preset("enters", zone="left").model_dump(), True))
        cams.append(cam)
    return cams


def frames(m, cam) -> int:
    return (m.status(cam.id).stats or {}).get("frames", 0)


def test_cameras_share_processes(manager, clip20):
    m, repo = manager(processes=2)
    cams = demo_cameras(repo, clip20, 6)
    for cam in cams:
        m.start(cam.id)
    assert wait(lambda: all(frames(m, c) >= 3 for c in cams), 120)
    assert sorted(m.status(c.id).process for c in cams) == [0, 0, 0, 1, 1, 1]  # round robin
    assert wait(lambda: sorted(len(p["cameras"]) for p in m.processes.values()) == [3, 3], 30)
    reports = list(m.processes.values())
    assert len({p["pid"] for p in reports}) == 2
    assert all(p["loads"] == {"detector": 1} for p in reports), reports  # once per process, not per camera
    assert all(m.status(c.id).stats["latency_p95_ms"] is not None for c in cams)


def test_a_dead_process_is_restarted_and_its_cameras_resume(manager, clip20):
    m, repo = manager(processes=2)
    m.supervise_every_s = 0.5
    cams = demo_cameras(repo, clip20, 2)
    for cam in cams:
        m.subscribe(cam.id)  # watched, so the demo keeps playing after its recorded first loop
        m.start(cam.id)
    assert wait(lambda: all(frames(m, c) >= 2 for c in cams), 90)
    victim, other = (m.status(c.id).process for c in cams)
    assert victim != other
    pid, other_pid = m.pool.slots[victim].process.pid, m.pool.slots[other].process.pid
    os.kill(pid, signal.SIGKILL)
    killed = time.time()
    assert wait(lambda: m.pool.slots[victim].process.pid != pid and m.pool.alive(victim), 30)
    assert wait(lambda: frames(m, cams[0]) >= 2 and m.cams[cams[0].id].last_frame_at > killed + 1, 90)
    assert m.status(cams[0].id).running and m.status(cams[0].id).process == victim
    assert m.pool.slots[other].process.pid == other_pid and frames(m, cams[1]) > 0  # the other process carried on
    assert m.pool.down() == 0


def test_pool_assigns_round_robin_to_the_least_loaded():
    pool = WorkerPool(Settings(session_secret="y", _env_file=None), processes=3)
    pool._send = lambda i, cmd: None  # no processes: only the bookkeeping
    cams = [Camera(f"cam{i}", f"c{i}", "webcam", None, workspace_id="ws_a") for i in range(7)]
    placed = [pool.assign(c, [], []) for c in cams]
    assert placed == [0, 1, 2, 0, 1, 2, 0]
    pool.remove("cam1")
    pool.remove("cam4")
    assert pool.assign(Camera("cam9", "c9", "webcam", None, workspace_id="ws_a"), [], []) == 1
    assert pool.assign(cams[0], [], []) == 0  # a camera assigned again stays on its process


def test_cameras_share_one_detector_each_with_its_own_classes(tmp_path):
    from watchful.components import make_detector
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    det = make_detector(s, Tuning())
    image = cv2.imread(str(sample_path("basketball1.png")))
    people, cars = det.view({"person"}), det.view({"car"})
    found = people.detect(image)
    assert found and all(d.cls == "person" for d in found)
    assert found == make_detector(s, Tuning(), {"person"}).detect(image)  # as a detector of its own would find
    assert cars.detect(image) == [] and len(det.view(None).class_ids) == len(COCO)


def test_rows_from_a_camera_process_go_to_the_cameras_own_society(manager):
    from watchful.store.db import EventRow
    m, repo = manager(processes=1)
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x"))
    m.camera_ws[cam.id] = repo.workspace_id

    def row(kind):  # a recent moment: the retention janitor, which starts with the manager, deletes old rows
        return EventRow(None, cam.id, time.time(), kind, 1, "person", None, {"watch_id": "w1"}, None, None, None,
                        None)

    m._handle({"type": "log", "workspace_id": repo.workspace_id, "camera_id": cam.id, "row": row("watch_log"),
               "record": True})
    m._handle({"type": "events", "workspace_id": repo.workspace_id, "camera_id": cam.id, "rows": [row("enter")]})
    m._handle({"type": "events", "workspace_id": "ws_someone_else", "camera_id": cam.id, "rows": [row("exit")]})
    m._handle({"type": "log", "workspace_id": repo.workspace_id, "camera_id": cam.id, "row": row("watch_log"),
               "record": False})  # a demo after its first loop: shown live, not written
    assert sorted(e.kind for e in repo.query_events(camera_id=cam.id)) == ["enter", "watch_log"]


def test_saved_zone_state_examples_reach_the_camera(tmp_path):
    """A zone_state watch's saved example pictures load in the camera process, read from the society's blobs."""
    from watchful.pool import CameraJob, PoolWorker
    from watchful.rules.watch import Condition, Watch
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    blobs = make_blobs(s)
    gate = Zone("z1", "gate", [(0, 0), (1, 0), (1, 1), (0, 1)])
    cam = Camera("cam_g", "Gate", "webcam", None, [gate], workspace_id="ws_a")
    examples = []
    for i, (state, value) in enumerate((("open", 40), ("closed", 200))):
        key = f"ws_a/cam_g/states/z1/{i}_day.jpg"
        blobs.put(key, cv2.imencode(".jpg", np.full((64, 64, 3), value, np.uint8))[1].tobytes(), "image/jpeg")
        examples.append((f"ex{i}", "z1", state, key))
    examples.append(("ex9", "z1", "open", "ws_b/cam_x/states/z1/9_day.jpg"))  # another society's: never read
    watch = Watch(name="gate open", conditions=[Condition(signal="zone_state", zones=["gate"], op="is",
                                                          value="open")])
    worker = PoolWorker(0, s, Tuning(), [].append, blobs=blobs)
    try:
        worker.assign(CameraJob(cam, [("w1", watch)], [], examples=examples))
        states = worker.cameras["cam_g"].engine.states
        assert states.has("gate") and states.states("gate") == {"open": {"day"}, "closed": {"day"}}
    finally:
        worker.close()


DEAD_PROCESS = """
import os, signal, sys, tempfile, time
from watchful.config import Settings
from watchful.pool import WorkerPool
from watchful.store.db import Camera

if __name__ == "__main__":
    pool = WorkerPool(Settings(session_secret="y", data_dir=tempfile.mkdtemp(), _env_file=None), processes=1)
    pool.assign(Camera("cam_x", "Phone", "webcam", None, workspace_id="ws_a"), [], [])
    end = time.time() + 60
    while not pool.alive(0) and time.time() < end:
        time.sleep(0.2)
    os.kill(pool.slots[0].process.pid, signal.SIGKILL)
    while pool.alive(0):
        time.sleep(0.1)
    for _ in range(600):  # a phone at 10 fps for a minute while its process waits for a restart
        pool.push("cam_x", b"\\xff\\xd8" + os.urandom(60_000), time.time())
    pool.supervise(now=time.time() + 100)
    time.sleep(2)
    pool.close()
    print("closed", flush=True)
"""


def test_frames_for_a_dead_process_are_dropped_and_the_app_can_exit(tmp_path):
    script = tmp_path / "dead.py"
    script.write_text(DEAD_PROCESS)
    try:
        done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        pytest.fail("the app hung on exit: frames pushed to a dead camera process were still held")
    assert done.returncode == 0 and "closed" in done.stdout, done.stderr[-2000:]


def test_a_process_that_ran_long_enough_restarts_quickly_again():
    pool = WorkerPool(Settings(session_secret="y", _env_file=None), processes=1)

    class Running:
        pid, exitcode = 1, None

        def is_alive(self):
            return True

    slot = pool.slots[0]
    slot.process, slot.restarts, slot.started_at = Running(), 4, time.time() - 2 * Tuning().resources.restart_max_s
    pool.supervise()
    assert slot.restarts == 0


def health_camera_job(health=None, output="alert"):
    from watchful.pool import CameraJob
    from watchful.rules.presets import default_watches
    (w,) = default_watches()
    w = w.model_copy(update={"output": output})
    cam = Camera("cam_h", "Lobby", "webcam", None, workspace_id="ws_a", last_frame_at=time.time() - 100)
    return CameraJob(cam, [("w_health", w)], [], health=health)


def offline_worker(tmp_path, sent):
    from watchful.pool import PoolWorker
    from watchful.tuning import HealthTuning
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    return PoolWorker(0, s, Tuning(health=HealthTuning(offline_s=0.3)), sent.append, blobs=make_blobs(s))


def test_a_camera_health_alert_waits_for_no_clip(tmp_path):
    sent = []
    worker = offline_worker(tmp_path, sent)
    try:
        worker.assign(health_camera_job())
        assert wait(lambda: (worker.step(0.05), any(m["type"] == "candidate" for m in sent))[1], 10)
        (msg,) = [m for m in sent if m["type"] == "candidate"]
        c = msg["pack"].candidate
        assert c.clip_key is None and c.evidence["method"] == "camera" and msg["pack"].frames == []
        assert worker.cameras["cam_h"].clips.requests == []
    finally:
        worker.close()


def test_a_problem_that_goes_on_across_a_restart_is_not_new(tmp_path):
    """The app restarts, or a watch is edited, while the camera is still offline: its health watch has fired already."""
    sent = []
    worker = offline_worker(tmp_path, sent)
    try:
        worker.assign(health_camera_job(health="offline", output="log"))
        end = time.time() + 2
        while time.time() < end:
            worker.step(0.05)
        assert worker.cameras["cam_h"].health == "offline"
        assert [m for m in sent if m["type"] in ("log", "candidate")] == []
    finally:
        worker.close()


def test_removing_a_camera_never_stalls_the_others(tmp_path):
    sent = []
    worker = offline_worker(tmp_path, sent)
    try:
        worker.assign(health_camera_job())
        runner = worker.cameras["cam_h"]
        slow = runner.stop
        runner.stop = lambda: (time.sleep(3), slow())  # a clip still encoding, a reader still joining
        started = time.monotonic()
        worker.remove("cam_h")
        assert time.monotonic() - started < 0.5
    finally:
        worker.close()


def test_a_pushed_frame_is_decoded_only_when_read(monkeypatch):
    import watchful.ingest.sources as sources
    decoded = []
    real = sources.cv2.imdecode
    monkeypatch.setattr(sources.cv2, "imdecode", lambda *a: decoded.append(1) or real(*a))
    source = sources.PushSource(5.0)
    jpeg = cv2.imencode(".jpg", np.zeros((48, 64, 3), np.uint8))[1].tobytes()
    for i in range(10):
        source.push(jpeg, 100.0 + i)
    frame = source.read(0.5)
    assert frame is not None and frame.ts == 109.0 and frame.image.shape == (48, 64, 3)
    assert len(decoded) == 1


def test_cameras_that_need_the_image_model_share_a_process():
    """The image model is the largest thing a camera process loads: cameras that need it go where it is loaded
    already, while that process has room, instead of loading it in every process."""
    from watchful.tuning import ResourcesTuning

    def pool(room: int) -> WorkerPool:
        p = WorkerPool(Settings(session_secret="y", _env_file=None), processes=3,
                       tuning=Tuning(resources=ResourcesTuning(image_cameras_per_process=room)))
        p._send = lambda i, cmd: None  # no processes: only the bookkeeping
        return p

    def cam(name):
        return Camera(name, name, "webcam", None, workspace_id="ws_a")

    text = [("w", preset("looks_like_check"))]
    plain = [("w", preset("enters", zone="left"))]
    p = pool(2)
    where = {name: p.assign(cam(name), watches, [])
             for name, watches in [("a", plain), ("t1", text), ("b", plain), ("t2", text), ("c", plain), ("t3", text)]}
    assert where["t1"] == where["t2"]  # one process holds the model for both
    assert where["t3"] != where["t1"] and len(set(where.values())) == 3  # that one is full, so a second opens
    p = pool(8)
    plain_at, text_at = p.assign(cam("a"), plain, []), p.assign(cam("t1"), text, [])
    assert plain_at != text_at
    assert p.assign(cam("a"), text, []) == text_at  # its watches need the model now: it moves to where it is
    assert p.assign(cam("a"), text, []) == text_at and p.assign(cam("b"), plain, []) != text_at


def test_watch_texts_are_made_once_not_at_every_start(tmp_path, monkeypatch):
    """The app loads the text tower for a text it has never seen (a watch just saved); the vector is kept with the
    society, so a restart of the app or the camera starts without the tower."""
    import watchful.workers as workers
    made = []

    class Texts:
        def text(self, t):
            made.append(t)
            return np.full(4, 0.5, np.float32)

    monkeypatch.setattr(workers, "make_embedder", lambda settings: Texts())
    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    repo = society_repo(s)
    cam = repo.add_camera(Camera(None, "Gate", "webcam", None))
    repo.add_watch(WatchRow(None, cam.id, "a helmet check", preset("looks_like_check").model_dump(), True))
    for _ in range(2):  # the app, then the app started again
        m = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=FakeLLM([]), processes=1)
        m.pool.assign = lambda *a, **k: 0
        try:
            m.start(cam.id)
            m.restart(cam.id)
        finally:
            m.shutdown()
    assert sorted(made) == ["a person", "a person wearing a helmet"]  # the preset's "not" side is the plain subject

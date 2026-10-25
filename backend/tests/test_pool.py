"""Live cameras share a few camera processes: each process loads its models once for all its cameras, and a process
that dies is started again with its cameras."""
import os
import signal
import subprocess
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

    def row(kind):
        return EventRow(None, cam.id, 10.0, kind, 1, "person", None, {"watch_id": "w1"}, None, None, None, None)

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

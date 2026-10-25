"""Camera health: a camera that stops sending pictures goes offline within Tuning seconds, opens one incident (not
one per frame or check), and resolves it when pictures return (Review Focus 1)."""
import time

import cv2
import numpy as np

from accounts import society_repo
from watchful.agent.llm import FakeLLM
from watchful.api.common import camera_json
from watchful.components import make_blobs
from watchful.config import Settings
from watchful.health import HealthBook, check, holds, is_health_watch
from watchful.pool import CameraJob, PoolWorker
from watchful.rules.watch import Condition, Watch
from watchful.store.db import Camera
from watchful.tuning import HealthTuning, TamperTuning, Tuning
from watchful.vision.models import sample_path
from watchful.workers import WorkerManager


def wait(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def settings(tmp_path) -> Settings:
    return Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                    analysis_fps=2.0, display_fps=4.0, _env_file=None)


def test_health_check():
    cam = Camera("c", "Gate", "stream", None)
    cfg = HealthTuning(offline_s=30)
    assert check(cam, 100.0, None, now=120.0, cfg=cfg) == "live"
    assert check(cam, 100.0, "covered", now=120.0, cfg=cfg) == "covered"
    assert check(cam, 100.0, "covered", now=131.0, cfg=cfg) == "offline"  # no picture at all says more
    assert check(cam, None, None, now=1.0, cfg=cfg) == "offline"
    slow_nvr = Camera("c", "Gate", "stream", None, tuning={"health": {"offline_s": 120}})
    assert check(slow_nvr, 100.0, None, now=200.0, cfg=cfg) == "live"


def test_each_change_is_written_once():
    changes, written = [], []

    class Repo:
        workspace_id = "ws_a"

        def set_camera_health(self, camera_id, status=None, last_frame_at=None):
            written.append((camera_id, status))

    book = HealthBook(lambda ws: Repo(), lambda ws, cam, old, new, ts: changes.append((old, new)))
    for state in ("live", "covered", "covered", "covered", "moved", "live", "offline"):
        book.update("ws_a", "cam1", state, 1.0)
    assert changes == [(None, "live"), ("live", "covered"), ("covered", "moved"), ("moved", "live"),
                       ("live", "offline")]
    assert written == [("cam1", s) for s in ("live", "covered", "moved", "live", "offline")]
    assert book.forget("cam1") == "offline" and book.state("cam1") is None
    book.seed("cam2", "covered")  # an earlier run left it covered: coming back is a change
    assert book.update("ws_a", "cam2", "live", 2.0) and changes[-1] == ("covered", "live")


def test_a_health_watch_needs_no_ai_and_holds_by_state():
    from watchful.rules.presets import default_watches
    (w,) = default_watches()
    assert w.scope == "site" and is_health_watch(w)
    assert holds(w, "covered") and holds(w, "offline") and not holds(w, "live") and not holds(w, "moved")


def manager_with_default_watches(tmp_path, tuning: Tuning):
    from watchful.store.sql import add_default_watches
    s = settings(tmp_path)
    repo = society_repo(s)
    with repo.tx() as tx:
        add_default_watches(tx, repo.workspace_id)
    m = WorkerManager(s, tuning, repo, make_blobs(s), llm=FakeLLM([]), processes=1)
    return m, repo


def pusher(m, cam_id: str):
    good = cv2.imencode(".jpg", cv2.imread(str(sample_path("basketball1.png"))))[1].tobytes()
    flat = cv2.imencode(".jpg", np.full((480, 640, 3), 120, np.uint8))[1].tobytes()

    def send(seconds: float, covered: bool = False) -> None:
        end = time.time() + seconds
        while time.time() < end:
            m.push_frame(cam_id, flat if covered else good, time.time())
            time.sleep(0.2)
    return send


def test_offline_opens_once_and_resolves(tmp_path):
    """Review Focus 1, with every society's default watch: one alert and one incident, resolved when frames return."""
    from watchful.alerts.incidents import list_incidents
    m, repo = manager_with_default_watches(tmp_path, Tuning(health=HealthTuning(offline_s=2.0, check_every_s=0.2)))
    try:
        cam = repo.add_camera(Camera(None, "Service gate", "webcam", None))
        (watch,) = repo.list_watches(cam.id)
        m.start(cam.id)
        assert camera_json(m, repo.get_camera(cam.id))["health"] is None  # never sent a picture: not judged
        send = pusher(m, cam.id)
        send(3)
        assert wait(lambda: m.status(cam.id).health == "live" and m.status(cam.id).stats.get("frames"), 20)
        stopped = time.time()
        assert wait(lambda: list_incidents(repo, camera_id=cam.id), 15)
        # the camera process ticks the watch from its own clock; the app's judgement follows within a status report
        assert wait(lambda: m.status(cam.id).health == "offline", 5)
        assert repo.get_camera(cam.id).status == "offline"
        assert camera_json(m, repo.get_camera(cam.id))["health"] == "offline"
        time.sleep(4)  # still offline: offline ticks go on, but the watch fired when it became true, once
        (inc,) = list_incidents(repo, camera_id=cam.id)
        assert inc.watch_id == watch.id and inc.status == "open" and inc.count == 1
        assert [(a.watch_id, a.status) for a in repo.list_alerts(cam.id)] == [(watch.id, "alert")]
        assert "offline" in repo.list_alerts(cam.id)[0].summary
        send(3)
        assert wait(lambda: list_incidents(repo, camera_id=cam.id)[0].status == "resolved", 15)
        assert len(list_incidents(repo, camera_id=cam.id)) == 1 and len(repo.list_alerts(cam.id)) == 1
        got = repo.get_camera(cam.id)
        assert got.status == "live" and got.last_frame_at > stopped
    finally:
        m.shutdown()


def test_a_covered_camera_raises_exactly_one_alert(tmp_path):
    """Covered is a signal like offline: the default watch alerts once, and no separate tamper alert is raised."""
    from watchful.alerts.incidents import list_incidents
    tuning = Tuning(health=HealthTuning(check_every_s=0.2), tamper=TamperTuning(check_every_s=0.2, hold_s=0.5))
    m, repo = manager_with_default_watches(tmp_path, tuning)
    try:
        cam = repo.add_camera(Camera(None, "Lobby", "webcam", None))
        m.start(cam.id)
        send = pusher(m, cam.id)
        send(3)
        assert wait(lambda: m.status(cam.id).health == "live", 20)
        send(4, covered=True)
        assert wait(lambda: repo.list_alerts(cam.id), 10)
        send(3, covered=True)
        alerts = repo.list_alerts(cam.id)
        assert len(alerts) == 1 and "covered" in alerts[0].summary and alerts[0].status == "alert"
        assert wait(lambda: m.status(cam.id).health == "covered", 5)
        assert repo.get_camera(cam.id).status == "covered"
        send(3)
        assert wait(lambda: list_incidents(repo, camera_id=cam.id)[0].status == "resolved", 15)
        assert len(repo.list_alerts(cam.id)) == 1
    finally:
        m.shutdown()


def test_an_offline_camera_tells_its_watches(tmp_path):
    """While no picture comes from a camera seen before, the camera process ticks its rules as offline, so a camera
    health watch fires; a watch about the picture does not read the missing picture as nobody there."""
    s = settings(tmp_path)
    sent = []
    tuning = Tuning(health=HealthTuning(offline_s=0.5))
    worker = PoolWorker(0, s, tuning, sent.append, blobs=make_blobs(s))
    watch = Watch(name="camera offline", output="log", conditions=[
        Condition(signal="camera_health", op="==", value="offline")])
    nobody = Watch(name="nobody in view", output="log", conditions=[Condition(signal="count", op="<=", value=0)])
    cam = Camera("cam_w", "Lobby", "webcam", None, workspace_id="ws_a", last_frame_at=time.time() - 100)
    worker.assign(CameraJob(cam, [("w1", watch), ("w2", nobody)], []))
    try:
        assert wait(lambda: (worker.step(0.05), any(m["type"] == "log" for m in sent))[1], 10)
        log = next(m for m in sent if m["type"] == "log")
        assert log["workspace_id"] == "ws_a" and log["row"].data["watch_id"] == "w1"
        assert any(m["type"] == "status" and m["camera_id"] == "cam_w" for m in sent)
        assert {m["row"].data["watch_id"] for m in sent if m["type"] == "log"} == {"w1"}
    finally:
        worker.close()


def test_a_stale_camera_edit_keeps_health_and_last_scan(tmp_path):
    """Health, the last picture time and the last scan are written by targeted updates; an edit made from an
    earlier read of the camera leaves them as they are."""
    from watchful.store.db import SqliteRepository
    repo = SqliteRepository(tmp_path / "w.db")
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x"))
    stale = repo.get_camera(cam.id)
    repo.set_camera_health(cam.id, status="covered", last_frame_at=1234.5)
    repo.set_last_scan(cam.id, {"start_epoch": 99.0})
    stale.name = "Front gate"
    repo.update_camera(stale)
    got = repo.get_camera(cam.id)
    assert (got.name, got.status, got.last_frame_at, got.last_scan) == ("Front gate", "covered", 1234.5,
                                                                         {"start_epoch": 99.0})

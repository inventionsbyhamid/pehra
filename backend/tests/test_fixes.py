"""Gaps found while building the web app."""
import time

import cv2
import numpy as np

from accounts import browser
from test_api import StubManager, watch_reply
from watchful.agent.ask import AskContext, people_groups
from watchful.agent.llm import FakeLLM
from watchful.api import create_app
from watchful.config import Settings
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import DEFAULT_WORKSPACE, AlertRow, Camera, EventRow, SqliteRepository
from watchful.vision.track import Tracker
from watchful.workers import should_record


def app_client(tmp_path):
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    app = create_app(s, llm=FakeLLM([watch_reply()]), manager=StubManager())
    return app, browser(app)


def test_track_ids_differ_between_sessions():
    a, b = Tracker(), Tracker()
    assert abs(a._next_id - b._next_id) >= 0 and a._next_id > 1_000_000


def test_demo_records_once(tmp_path):
    repo = SqliteRepository(tmp_path / "w.db")
    cam = repo.add_camera(Camera(None, "Plaza", "demo", "x.mp4"))
    assert should_record(repo, cam)
    repo.add_events([EventRow(None, cam.id, 1.0, "enter", 1, "person", "z", {}, None, None, None, None)])
    assert not should_record(repo, cam)
    assert should_record(repo, repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x")))


def test_zone_change_clears_a_demo_timeline(tmp_path):
    app, c = app_client(tmp_path)
    repo = app.state.ctx.repo_for(DEFAULT_WORKSPACE)
    cam = repo.add_camera(Camera(None, "Plaza", "demo", "x.mp4"))
    repo.add_events([EventRow(None, cam.id, 1.0, "enter", 1, "person", "z", {}, None, None, None, None)])
    zones = [{"name": "left", "polygon": [[0, 0], [1, 0], [1, 1]]}]
    assert c.patch(f"/api/cameras/{cam.id}", json={"zones": zones}).status_code == 200
    assert repo.query_events(camera_id=cam.id) == []


def test_people_groups_list_their_tracks(tmp_path):
    repo = SqliteRepository(tmp_path / "w.db")
    repo.add_events([EventRow(None, "cam", t, k, 7, "person", None, {}, None, None, None, "Sana")
                     for t, k in ((10.0, "track_start"), (40.0, "track_end"))])
    g = people_groups(AskContext(repo, LocalBlobStore(tmp_path), "cam", "Asia/Kolkata"))[0]
    assert g["tracks"] == [{"track_id": 7, "first_ts": 10.0, "last_ts": 40.0}]


def test_get_and_delete_camera(tmp_path):
    app, c = app_client(tmp_path)
    repo = app.state.ctx.repo_for(DEFAULT_WORKSPACE)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    assert c.get(f"/api/cameras/{cam['id']}").json()["name"] == "Gate"
    repo.add_alert(AlertRow(None, cam["id"], None, 1.0, "info", "alert", "x", None, None, [], None, "f"))
    assert c.delete(f"/api/cameras/{cam['id']}").status_code == 200
    assert c.get(f"/api/cameras/{cam['id']}").status_code == 404 and repo.list_alerts(cam["id"]) == []


def test_lookout_from_an_alert(tmp_path):
    app, c = app_client(tmp_path)
    repo = app.state.ctx.repo_for(DEFAULT_WORKSPACE)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    repo.add_events([EventRow(None, cam["id"], 100.0, "track_start", 5, "person", None, {}, "k.jpg", [0.1] * 4,
                              [1.0, 0.0], None)])
    a = repo.add_alert(AlertRow(None, cam["id"], None, 103.0, "info", "alert", "x", None, None, [], None, "f",
                                track_id=5))
    r = c.post("/api/lookouts", json={"camera_ids": [cam["id"]], "alert_id": a.id})
    assert r.status_code == 200 and r.json()["watch_ids"]


def test_enrolment_photo_kept_by_retention_and_removed_on_delete(tmp_path):
    app, c = app_client(tmp_path)
    blobs = app.state.ctx.blobs
    from watchful.vision.models import sample_path
    photo = cv2.imencode(".jpg", cv2.imread(str(sample_path("basketball1.png"))))[1].tobytes()
    pid = c.post("/api/people", data={"name": "P", "consent": "true"}, files={"photo": ("p.jpg", photo)}).json()["person_id"]
    old = time.time() - 30 * 86400
    import os
    os.utime(tmp_path / "blobs" / "people" / f"{pid}.jpg", (old, old))
    blobs.delete_older_than(7)
    assert blobs.get(f"people/{pid}.jpg")
    c.delete(f"/api/people/{pid}")
    assert not (tmp_path / "blobs" / "people" / f"{pid}.jpg").exists()


def test_upload_clip_is_cut_at_the_alert_moment(tmp_path, monkeypatch):
    import watchful.api.alerts as api
    app, c = app_client(tmp_path)
    repo = app.state.ctx.repo_for(DEFAULT_WORKSPACE)
    cam = repo.add_camera(Camera(None, "Upload", "file", str(tmp_path / "v.mp4"), last_scan={"start_epoch": 1000.0}))
    repo.add_events([EventRow(None, cam.id, 1030.0, "enter", 1, "person", "z", {}, None, None, None, None)])
    a = repo.add_alert(AlertRow(None, cam.id, None, 1042.5, "info", "alert", "x", None, None, [], None, "fp9"))
    seen = {}
    monkeypatch.setattr(api, "cut_clip", lambda path, pos, s, t: seen.setdefault("pos", pos) and b"mp4" or b"mp4")
    c.get(f"/api/alerts/{a.id}/clip", follow_redirects=False)
    assert seen["pos"] == 42.5


def test_alerts_carry_a_ready_picture_url(tmp_path):
    app, c = app_client(tmp_path)
    app.state.ctx.repo_for(DEFAULT_WORKSPACE).add_alert(AlertRow(None, "cam", None, 1.0, "info", "alert", "x", None, "cam/kf/1.jpg", [], None, "f"))
    assert c.get("/api/alerts").json()[0]["keyframe_url"].startswith("/api/blobs/cam/kf/1.jpg?e=")


def test_retention_keeps_demo_cameras_and_enrolment_photos(tmp_path):
    import os
    from watchful.workers import run_retention
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    repo, blobs = SqliteRepository(tmp_path / "w.db"), LocalBlobStore(tmp_path)
    demo = repo.add_camera(Camera(None, "Plaza", "demo", "x.mp4"))
    live = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x"))
    old = time.time() - 30 * 86400
    for cam in (demo, live):
        repo.add_events([EventRow(None, cam.id, old, "enter", 1, "person", "z", {}, None, None, None, None)])
        repo.add_alert(AlertRow(None, cam.id, None, old, "info", "alert", "x", None, None, [], None, f"f{cam.id}"))
        blobs.put(f"{cam.id}/kf/1.jpg", b"x", "image/jpeg")
    blobs.put("people/p1.jpg", b"x", "image/jpeg")
    for f in (tmp_path / "blobs").rglob("*.jpg"):
        os.utime(f, (old, old))
    run_retention(s, repo, blobs)
    assert repo.query_events(camera_id=live.id) == [] and repo.list_alerts(live.id) == []
    assert repo.query_events(camera_id=demo.id) and repo.list_alerts(demo.id)
    assert blobs.get(f"{demo.id}/kf/1.jpg") and blobs.get("people/p1.jpg")
    assert not (tmp_path / "blobs" / live.id / "kf" / "1.jpg").exists()


def test_saving_zones_turns_off_watches_that_lost_their_zone(tmp_path):
    from watchful.rules.presets import preset
    from watchful.store.db import WatchRow
    app, c = app_client(tmp_path)
    repo = app.state.ctx.repo_for(DEFAULT_WORKSPACE)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"}).json()
    c.patch(f"/api/cameras/{cam['id']}", json={"zones": [{"name": "door", "polygon": [[0, 0], [1, 0], [1, 1]]}]})
    repo.add_watch(WatchRow(None, cam["id"], "x", preset("enters", zone="door").model_dump(), True))
    r = c.patch(f"/api/cameras/{cam['id']}", json={"zones": [{"name": "porch", "polygon": [[0, 0], [1, 0], [1, 1]]}]})
    assert r.json()["disabled_watches"] == ["Enters door"] and not repo.list_watches(cam["id"])[0].enabled


def test_watch_texts_are_precomputed_for_camera_processes():
    import numpy as np
    from watchful.rules.presets import preset
    from watchful.rules.watch import Subject, Watch, Condition
    from watchful.vision.embed import TextCache, watch_texts
    ws = [preset("looks_like_check", text="a person not wearing glasses", not_text="a person wearing glasses"),
          Watch(name="red", subject=Subject(looks_like="a man in red"), verify="?", conditions=[Condition(signal="present")]),
          preset("gate_left_open", zone="gate")]
    assert watch_texts(ws) == {"a person not wearing glasses", "a person wearing glasses", "a man in red",
                               "an open gate", "a closed gate"}

    class Inner:
        loads = 0

        def text(self, q):
            Inner.loads += 1
            return np.ones(2, np.float32)

    cache = TextCache(Inner(), {"a man in red": [1.0, 0.0]})
    assert list(cache.text("a man in red")) == [1.0, 0.0] and Inner.loads == 0


def test_clip_buffer_holds_compressed_frames(tmp_path):
    from watchful.ingest.clips import ClipRecorder
    from watchful.ingest.sources import Frame
    rec = ClipRecorder(LocalBlobStore(tmp_path), fps=5)
    rec.on_frame(Frame(np.zeros((540, 960, 3), np.uint8), 1.0, 1))
    assert isinstance(rec.buffer[0][1], bytes) and len(rec.buffer[0][1]) < 100_000


def test_video_without_a_readable_length_is_rejected(tmp_path, monkeypatch):
    import watchful.api.cameras as api
    app, c = app_client(tmp_path)
    monkeypatch.setattr(api, "_ffprobe", lambda path: {"streams": [{"codec_type": "video"}], "format": {"duration": "N/A"}})
    r = c.post("/api/uploads", files={"file": ("live.ts", b"0" * 1000, "video/mp2t")})
    assert r.status_code == 400 and "long" in r.json()["detail"]


def test_missing_pictures_do_not_break_search(tmp_path):
    from watchful.search import SearchQuery, search
    repo, blobs = SqliteRepository(tmp_path / "w.db"), LocalBlobStore(tmp_path)
    repo.add_events([EventRow(None, "cam", 1.0, "scene", None, None, None, {}, "gone.jpg", None, None, None)])

    class Stub:
        def text(self, q):
            return np.array([1.0, 0.0], np.float32)

        def image(self, crop):
            return np.array([1.0, 0.0], np.float32)

    assert search(SearchQuery(text="anything"), repo, blobs, Stub()).results == []


def test_changing_the_embedding_model_rebuilds_the_index(tmp_path):
    from watchful.indexer import reset_if_model_changed
    repo = SqliteRepository(tmp_path / "w.db")
    repo.add_events([EventRow(None, "cam", 1.0, "scene", None, None, None, {}, "k.jpg", None, [0.9, 0.9], None)])
    assert reset_if_model_changed(repo, "siglip2")  # vectors from an unrecorded model are not trusted
    assert repo.query_events(camera_id="cam")[0].embedding is None
    repo.set_event_embedding(repo.query_events(camera_id="cam")[0].id, [0.1, 0.2])
    assert not reset_if_model_changed(repo, "siglip2")
    assert repo.query_events(camera_id="cam")[0].embedding == [0.1, 0.2]
    assert reset_if_model_changed(repo, "siglip2_b32")
    assert repo.query_events(camera_id="cam")[0].embedding is None
    assert [e.kind for e in repo.events_missing_embedding(["scene"])] == ["scene"]

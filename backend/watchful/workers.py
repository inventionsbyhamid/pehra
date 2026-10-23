"""Runs cameras: one process per live, demo or webcam camera, and parallel scans for uploads.
Candidates come back with their evidence; the AI checks them here and alerts are stored."""
import json
import logging

import cv2
import multiprocessing as mp
import queue
import secrets
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from watchful.agent.verifier import Verdict, _fallback, verify
from watchful.components import (make_blobs, make_embedder, make_faces, make_llm, make_metrics, make_notifier,
                                 make_repo)
from watchful.config import Settings
from watchful.evidence import EvidenceBuffer, EvidencePack, evidence_context, file_pack
from watchful.ingest.clips import ClipRecorder
from watchful.ingest.sources import FileSource, PushSource, StreamSource
from watchful.notify import ApprovalNotifier
from watchful.rules.engine import RuleEngine, needs_for
from watchful.rules.watch import Watch
from watchful.store.db import AlertRow, Camera
from watchful.tuning import Tuning, load_tuning
from watchful.vision.perceive import Needs
from watchful.vision.pipeline import CameraPipeline
from watchful.vision.scan import ScanPool, scan_file

log = logging.getLogger("watchful.workers")


def should_record(repo, camera: Camera) -> bool:
    """A demo camera records its timeline once; later runs only show the live view and alerts."""
    return camera.kind != "demo" or not repo.query_events(camera_id=camera.id, limit=1)


def run_retention(settings: Settings, repo, blobs) -> None:
    """Clips, pictures, uploads, events and alerts older than retention_days are deleted, except enrolment photos
    and demo cameras, which replay consented clips for the whole judging period."""
    from pathlib import Path
    cutoff = time.time() - settings.retention_days * 86400
    demos = [c.id for c in repo.list_cameras() if c.kind == "demo"]
    blobs.delete_older_than(settings.retention_days, keep=("people/", *(f"{d}/" for d in demos)))
    repo.delete_events_before(cutoff, keep_cameras=demos)
    repo.delete_alerts_before(cutoff, keep_cameras=demos)
    uploads = Path(settings.data_dir) / "uploads"
    for f in uploads.glob("*") if uploads.is_dir() else []:
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def overlay_tracks(tick) -> list[dict]:
    """The latest tracks for the live view, boxes normalized 0..1 so the browser can draw them."""
    w, h = tick.size
    if not w or not h:
        return []
    return [{"id": s.track_id, "cls": s.cls, "label": s.known_name or s.cls, "posture": s.posture,
             "box": [round(s.box[0] / w, 4), round(s.box[1] / h, 4), round(s.box[2] / w, 4), round(s.box[3] / h, 4)]}
            for s in tick.tracks]


@dataclass
class WorkerStatus:
    running: bool = False
    source_status: str = "stopped"
    fps: float = 0.0
    last_ts: float | None = None
    loops: int = 0
    stats: dict = field(default_factory=dict)


def _source(camera: Camera, settings: Settings):
    fps, width = max(settings.display_fps, settings.analysis_fps), settings.analysis_width
    if camera.kind == "demo":
        return FileSource(camera.url, fps, loop=True, realtime=settings.demo_realtime, width=width)
    if camera.kind == "file":
        return FileSource(camera.url, fps, realtime=settings.demo_realtime, width=width)
    if camera.kind == "webcam":
        return PushSource(fps, width)
    return StreamSource(camera.url, fps, width)


def run_camera(camera: Camera, watches: list[tuple[str, Watch]], enrollments: list, settings_json: str,
               tuning_json: str, in_q, out_q, viewers, scanning, stop) -> None:
    """The camera process: source, pipeline, rules, clips and evidence."""
    settings = Settings(**json.loads(settings_json), _env_file=None)
    base = Tuning.model_validate_json(tuning_json)
    tuning = load_tuning(None, camera.tuning) if camera.tuning else base
    repo, blobs = make_repo(settings), make_blobs(settings)
    needs = needs_for([w for _, w in watches]) if watches else Needs(classes={"person"})
    faces = None
    if needs.faces and enrollments:
        faces = make_faces(settings, tuning)
        faces.load(enrollments)
    embedder = make_embedder(settings) if needs.embed else None
    pipe = CameraPipeline(camera, repo, blobs, faces, settings, tuning, needs, embedder)
    engine = RuleEngine(watches, camera.zones, tuning, settings.timezone, camera.kind, embedder)
    source = _source(camera, settings)
    webcam = isinstance(source, PushSource)
    clips = ClipRecorder(blobs, max(settings.display_fps, settings.analysis_fps), tuning.clips)
    evidence = EvidenceBuffer(tuning.evidence, camera.zones)
    pending: list[tuple[float, object]] = []
    loops, last_pos, last_status, last_done = 0, None, 0.0, float("-inf")
    tracks: list[dict] = []
    recording = should_record(repo, camera)
    frames_done, t_start = 0, time.monotonic()

    def send(msg: dict) -> None:
        try:
            out_q.put_nowait(msg)
        except queue.Full:
            pass

    while not stop.is_set():
        if camera.kind == "demo" and loops >= 1 and viewers.value == 0:
            time.sleep(0.5)
            continue
        if isinstance(source, PushSource):
            try:
                while True:
                    jpeg, ts = in_q.get_nowait()
                    source.push(jpeg, ts)
            except queue.Empty:
                pass
        frame = source.read(timeout=1.0)
        now = time.monotonic()
        if now - last_status >= 1.0:
            last_status = now
            send({"type": "status", "camera_id": camera.id, "source_status": source.status, "loops": loops,
                  "fps": frames_done / max(now - t_start, 1e-3), "stats": pipe.stats()})
        if frame is None:
            if source.status == "ended":
                pipe.finish(time.time())
                break
            continue
        if frame.pos is not None and last_pos is not None and frame.pos < last_pos:
            loops += 1
            recording = recording and camera.kind != "demo"
        last_pos = frame.pos
        clips.on_frame(frame)
        rate = settings.live_fps_during_scan if scanning.value > 0 else settings.analysis_fps
        analyse = frame.ts - last_done >= 1.0 / rate - 1e-3
        if analyse:
            last_done = frame.ts
            result = pipe.process(frame, record=recording)
            frames_done += 1
            evidence.add(frame, result.tick)
            tracks = overlay_tracks(result.tick)
            if webcam and viewers.value > 0:
                send({"type": "overlay", "camera_id": camera.id, "ts": frame.ts, "tracks": tracks})
        if viewers.value > 0 and not webcam:
            h, w = frame.image.shape[:2]
            ok, jpg = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, settings.display_quality])
            if ok:
                send({"type": "frame", "camera_id": camera.id, "jpeg": jpg.tobytes(), "ts": frame.ts,
                      "width": w, "height": h, "tracks": tracks})
        if not analyse:
            continue
        for c in engine.on_tick(camera.id, result.tick, now=frame.ts, image=frame.image):
            c.clip_key = f"{camera.id}/clips/{c.fingerprint}.mp4"
            clips.request(c.ts, c.clip_key)
            pending.append((frame.ts + tuning.evidence.after_s, c))
        for item in [p for p in pending if frame.ts >= p[0]]:
            pending.remove(item)
            send({"type": "candidate", "pack": evidence.pack(item[1])})
    clips.close()
    send({"type": "status", "camera_id": camera.id, "source_status": "stopped", "loops": loops, "fps": 0,
          "stats": pipe.stats(), "stopped": True})


class Subscription:
    """One viewer: the newest frame only, and every alert and status in order."""

    def __init__(self):
        self._frame: dict | None = None
        self._events: queue.Queue = queue.Queue(maxsize=500)
        self._cond = threading.Condition()

    def put(self, msg: dict) -> None:
        with self._cond:
            if msg.get("type") in ("frame", "overlay"):
                self._frame = msg
            else:
                try:
                    self._events.put_nowait(msg)
                except queue.Full:
                    pass
            self._cond.notify_all()

    def get(self, timeout: float | None = None) -> dict | None:
        with self._cond:
            if self._events.empty() and self._frame is None:
                self._cond.wait(timeout)
            if not self._events.empty():
                return self._events.get_nowait()
            msg, self._frame = self._frame, None
            return msg

    def get_nowait(self) -> dict:
        msg = self.get(0)
        if msg is None:
            raise queue.Empty
        return msg

    def empty(self) -> bool:
        return self._events.empty() and self._frame is None


@dataclass
class _Handle:
    process: object
    in_q: object
    viewers: object
    stop: object
    status: WorkerStatus = field(default_factory=WorkerStatus)


class WorkerManager:
    def __init__(self, settings: Settings, tuning: Tuning, repo, blobs, llm=None):
        self.settings, self.tuning, self.repo, self.blobs = settings, tuning, repo, blobs
        self.ctx = mp.get_context("spawn")
        self.out_q = self.ctx.Queue(maxsize=2000)
        self.scanning = self.ctx.Value("i", 0)
        self.handles: dict[str, _Handle] = {}
        self.subs: dict[str, list[Subscription]] = defaultdict(list)
        self.scans: dict[str, dict] = {}
        self.llm = llm or make_llm(settings, repo, "verify")
        self.verifiers = ThreadPoolExecutor(max(1, settings.ai_parallel))
        self.approvals = ApprovalNotifier(make_notifier(settings))
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._scan_pool: ScanPool | None = None
        self._embedder = None
        self._closed = False
        self.metrics = make_metrics(settings)
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._report, daemon=True).start()
        threading.Thread(target=self._janitor, daemon=True).start()
        self.indexer = None
        if settings.index_embeddings:
            from watchful.indexer import Indexer
            self.indexer = Indexer(repo, blobs, lambda: make_embedder(settings))
            self.indexer.start()

    def _janitor(self, every_s: float = 6 * 3600) -> None:
        while not self._closed:
            try:
                run_retention(self.settings, self.repo, self.blobs)
            except Exception:
                log.exception("retention cleanup failed")
            time.sleep(every_s)

    def _report(self, every_s: float = 60) -> None:
        while not self._closed:
            time.sleep(every_s)
            try:
                for cid, h in list(self.handles.items()):
                    if h.status.running:
                        st, dims = h.status.stats or {}, {"Camera": cid}
                        self.metrics.put("FramesPerSecond", h.status.fps, dims)
                        for key, name in (("ms_per_frame", "MsPerFrame"), ("frames_skipped", "FramesSkipped"),
                                          ("detector_runs", "DetectorRuns")):
                            if key in st:
                                self.metrics.put(name, st[key], dims)
                self.metrics.put("AICallsToday", self.repo.count_ai_calls_today())
                self.metrics.flush()
            except Exception:
                log.exception("metrics report failed")

    # cameras
    def _watches(self, camera_id: str) -> list[tuple[str, Watch]]:
        return [(w.id, Watch.model_validate(w.watch)) for w in self.repo.list_watches(camera_id) if w.enabled]

    def start(self, camera_id: str) -> None:
        self.stop(camera_id)
        cam = self.repo.get_camera(camera_id)
        if cam is None or not cam.enabled:
            return
        h = _Handle(None, self.ctx.Queue(maxsize=4), self.ctx.Value("i", 0), self.ctx.Event())
        h.viewers.value = len(self.subs[camera_id])
        h.process = self.ctx.Process(target=run_camera, daemon=True, args=(
            cam, self._watches(camera_id), self.repo.list_enrollments(), self.settings.model_dump_json(),
            self.tuning.model_dump_json(), h.in_q, self.out_q, h.viewers, self.scanning, h.stop))
        h.process.start()
        h.status = WorkerStatus(running=True, source_status="starting")
        self.handles[camera_id] = h

    def stop(self, camera_id: str) -> None:
        h = self.handles.get(camera_id)
        if not h or not h.process:
            return
        h.stop.set()
        h.process.join(5)
        if h.process.is_alive():
            h.process.terminate()
        h.status.running = False

    def restart(self, camera_id: str) -> None:
        if camera_id in self.handles and self.handles[camera_id].status.running:
            self.start(camera_id)

    def status(self, camera_id: str) -> WorkerStatus:
        h = self.handles.get(camera_id)
        if h and h.process and not h.process.is_alive():
            h.status.running = False
        return h.status if h else WorkerStatus()

    def subscribe(self, camera_id: str) -> Subscription:
        q = Subscription()
        self.subs[camera_id].append(q)
        h = self.handles.get(camera_id)
        if h:
            h.viewers.value += 1
        return q

    def unsubscribe(self, camera_id: str, q: Subscription) -> None:
        if q in self.subs[camera_id]:
            self.subs[camera_id].remove(q)
            h = self.handles.get(camera_id)
            if h:
                h.viewers.value = max(0, h.viewers.value - 1)

    def push_frame(self, camera_id: str, jpeg: bytes, ts: float) -> None:
        h = self.handles.get(camera_id)
        if h:
            try:
                h.in_q.put_nowait((jpeg, ts))
            except queue.Full:
                pass

    def _publish(self, camera_id: str, msg: dict) -> None:
        for q in list(self.subs.get(camera_id, [])):
            q.put(msg)

    def _listen(self) -> None:
        while not self._closed:
            try:
                msg = self.out_q.get(timeout=0.5)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return
            kind = msg.get("type")
            if kind in ("frame", "overlay"):
                self._publish(msg["camera_id"], msg)
            elif kind == "status":
                h = self.handles.get(msg["camera_id"])
                if h:
                    h.status.source_status, h.status.loops = msg["source_status"], msg["loops"]
                    h.status.fps, h.status.stats = msg["fps"], msg["stats"]
                    if msg.get("stopped"):
                        h.status.running = False
                self._publish(msg["camera_id"], msg)
            elif kind == "candidate":
                self._on_candidate(msg["pack"])

    # alerts
    def _on_candidate(self, pack: EvidencePack) -> None:
        c = pack.candidate
        with self._lock:
            if c.fingerprint in self._inflight or self.repo.alert_fingerprint_exists(
                    c.fingerprint, within_s=self.settings.alert_dedupe_days * 86400):
                return
            self._inflight.add(c.fingerprint)
        self.verifiers.submit(self._verify_and_store, pack)

    def _verify_and_store(self, pack: EvidencePack) -> str | None:
        c = pack.candidate
        try:
            verdict = verify(c, evidence_context(pack, self.repo, self.blobs), self.llm)
        except Exception:
            log.exception("verifying %s failed", c.fingerprint)
            verdict = _fallback(c, [], "the check failed")
        try:
            return self._store(pack, verdict).id
        except Exception:
            log.exception("storing the alert %s failed", c.fingerprint)
            return None
        finally:
            with self._lock:
                self._inflight.discard(c.fingerprint)

    def _store(self, pack: EvidencePack, v: Verdict) -> AlertRow:
        c = pack.candidate
        row = self.repo.add_alert(AlertRow(None, c.camera_id, c.watch_id, c.ts, c.severity, v.status,
                                           v.summary or c.watch_name, c.clip_key,
                                           c.keyframe_keys[0] if c.keyframe_keys else None,
                                           [asdict(t) for t in v.trace], None, c.fingerprint, c.track_id))
        self._publish(c.camera_id, {"type": "alert", "alert": {**asdict(row), "keyframe_url":
                                    self.blobs.url(row.keyframe_key) if row.keyframe_key else None}})
        self.metrics.put("Alerts", 1, {"Status": v.status})
        return row

    def approve(self, alert_id: str) -> bool:
        row = self.repo.get_alert(alert_id)
        if row is None:
            return False
        row.status = "approved"
        self.repo.update_alert(row)
        return self.approvals.approve(row)

    # uploads
    def _pool(self) -> ScanPool:
        if self._scan_pool is None:
            self._scan_pool = ScanPool(self.settings, self.tuning)
        return self._scan_pool

    def scan_upload(self, camera_id: str) -> str:
        scan_id = "scn_" + secrets.token_hex(6)
        self.scans[scan_id] = {"state": "scanning", "progress": 0.0, "stats": None, "alert_ids": [],
                               "camera_id": camera_id, "error": None}
        threading.Thread(target=self._run_scan, args=(scan_id, camera_id), daemon=True).start()
        return scan_id

    def scan(self, scan_id: str) -> dict | None:
        return self.scans.get(scan_id)

    def _run_scan(self, scan_id: str, camera_id: str) -> None:
        info = self.scans[scan_id]
        cam = self.repo.get_camera(camera_id)
        with self.scanning.get_lock():
            self.scanning.value += 1
        try:
            watches = self._watches(camera_id)
            needs = needs_for([w for _, w in watches]) if watches else Needs(classes={"person"})
            if needs.embed and self._embedder is None:
                self._embedder = make_embedder(self.settings)
            start_epoch = time.time()
            result = scan_file(cam.url, cam, watches, self._pool(), self.blobs, self.settings, self.tuning,
                               start_epoch=start_epoch, enrollments=self.repo.list_enrollments(),
                               embedder=self._embedder if needs.embed else None,
                               on_progress=lambda p: info.update(progress=round(p, 3)))
            self.repo.add_events(result.events)
            info.update(state="checking", stats=asdict(result.stats))
            cam.last_scan = {**asdict(result.stats), "start_epoch": start_epoch, "finished_at": time.time()}
            self.repo.update_camera(cam)
            self.metrics.put("ScanRealtimeFactor", result.stats.x_realtime)
            self.metrics.put("ScanSecondsPerMinute", result.stats.wall_s / max(result.stats.duration_s / 60, 1e-6))
            packs = [file_pack(cam.url, start_epoch, c, result.paths.get(c.track_id, []),
                               result.poses.get(c.track_id, []), self.tuning.evidence, cam.zones)
                     for c in result.candidates]
            futures = [self.verifiers.submit(self._verify_and_store, p) for p in packs]
            info["alert_ids"] = [i for i in (f.result() for f in futures) if i]
            info.update(state="done", progress=1.0)
        except Exception as e:
            log.exception("scan %s failed", scan_id)
            info.update(state="failed", error=str(e))
        finally:
            with self.scanning.get_lock():
                self.scanning.value -= 1

    def shutdown(self) -> None:
        if self.indexer:
            self.indexer.stop()
        for cid in list(self.handles):
            self.stop(cid)
        self._closed = True
        if self._scan_pool:
            self._scan_pool.close()
        self.verifiers.shutdown(wait=False, cancel_futures=True)

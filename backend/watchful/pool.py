"""Live cameras share a few worker processes: one per CPU (Settings camera_processes), each camera assigned to the
least loaded in round robin order. A process loads each model once for all its cameras: the detector (one network,
each camera with its own classes), pose, the face networks (with an index per society), the image model. It reads
every camera on a thread of its own and analyses the newest due frame of each camera in turn. Frames of several
cameras are not batched into one detector run: the YOLOX export folds a batch into wrong boxes, and on CPU a batch of
4 measured no faster per frame (127 against 131 ms).

A camera process never opens the database: events, log rows, candidates, status and health facts go to the app as
messages ({"type": "events", "workspace_id", "camera_id", "rows": [EventRow]} and so on), and the app writes them
through the camera's own society's repository (workers.WorkerManager). Blobs it writes go under the camera's society
(store/blobs.py SocietyBlobs)."""
import ctypes
import json
import logging
import os
import queue
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.components import make_blobs, make_detector, make_embedder, make_faces, make_pose
from watchful.config import Settings, available_cpus
from watchful import netguard
from watchful.evidence import EvidenceBuffer
from watchful.health import NO_PICTURE, check as health_check, holds, is_health_watch
from watchful.ingest.clips import ClipRecorder
from watchful.ingest.loop import demo_source
from watchful.ingest.sources import FileSource, PushSource, StreamSource
from watchful.rules.engine import RuleEngine, log_row, needs_for
from watchful.rules.watch import Watch
from watchful.rules.zonestate import load_states
from watchful.store.blobs import SocietyBlobs, society_key
from watchful.store.db import Camera, EventRow
from watchful.tuning import Tuning, _merge
from watchful.vision.embed import TextCache
from watchful.vision.faces import Enrollment
from watchful.vision.perceive import Needs
from watchful.vision.pipeline import CameraPipeline
from watchful.vision.reason import Tick

log = logging.getLogger("watchful.pool")

IMPORTANT = ("events", "log", "candidate", "stopped")  # never dropped when the app is slow to read
CAMERA_SIGNALS = {"camera_health", "zone_state"}  # signals that read the camera's health, not its picture


@dataclass
class CameraJob:
    """Everything a camera process needs to run one camera; the app reads it from the camera's society."""
    camera: Camera
    watches: list[tuple[str, Watch]]
    enrollments: list[Enrollment]  # the camera's society's enrolled staff, no one else's
    texts: dict[str, list[float]] = field(default_factory=dict)  # watch texts as vectors, made by the app
    examples: list[tuple] = field(default_factory=list)  # saved zone state examples (repo.list_state_examples)
    record: bool = True  # write the timeline; a demo camera records its first loop only
    viewers: int = 0
    health: str | None = None  # the app's judgement, kept across restarts: a problem still going on is not new


def overlay_tracks(tick) -> list[dict]:
    """The latest tracks for the live view, boxes normalized 0..1 so the browser can draw them."""
    w, h = tick.size
    if not w or not h:
        return []
    return [{"id": s.track_id, "cls": s.cls, "label": s.known_name or s.cls, "posture": s.posture,
             "box": [round(s.box[0] / w, 4), round(s.box[1] / h, 4), round(s.box[2] / w, 4), round(s.box[3] / h, 4)]}
            for s in tick.tracks]


def camera_tuning(base: Tuning, camera: Camera) -> Tuning:
    """The base Tuning (TUNING_FILE included) with the camera's own overrides on top."""
    return Tuning.model_validate(_merge(base.model_dump(), camera.tuning)) if camera.tuning else base


def trim_memory() -> None:
    """Hands freed heap back to the system after models load (glibc only)."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            return next(int(line.split()[1]) for line in f if line.startswith("VmRSS:")) / 1024
    except (OSError, StopIteration):
        return 0.0


def percentile(values, q: float) -> float | None:
    return round(float(np.percentile(list(values), q)), 1) if values else None


class Models:
    """The models one camera process loads once and shares between its cameras. A camera whose own Tuning changes a
    model's settings gets a model of its own for that setting. `loads` counts every load."""

    def __init__(self, settings: Settings, tuning: Tuning):
        self.settings, self.tuning = settings, tuning
        self.loads: Counter = Counter()
        self._detectors: dict[str, object] = {}
        self._poses: dict[str, object] = {}
        self._faces: dict[str, object] = {}

    def detector(self, tuning: Tuning):
        key = tuning.detect.model_dump_json()
        if key not in self._detectors:
            self.loads["detector"] += 1
            self._detectors[key] = make_detector(self.settings, tuning)
        return self._detectors[key]

    def pose(self, tuning: Tuning):
        key = tuning.pose.model_dump_json()
        if key not in self._poses:
            self.loads["pose"] += 1
            self._poses[key] = make_pose(self.settings, tuning)
        return self._poses[key]

    def faces(self, tuning: Tuning):
        key = tuning.faces.model_dump_json()
        if key not in self._faces:
            self.loads["faces"] += 1
            self._faces[key] = make_faces(self.settings, tuning)
        return self._faces[key]

    def embedder(self):
        if "embedder" not in self.loads:
            self.loads["embedder"] += 1
        return make_embedder(self.settings)  # one per process already (components._embedders)


class EventSink:
    """Where a camera's pipeline writes events: a message to the app, which stores them in the camera's society."""

    def __init__(self, send, workspace_id: str | None, camera_id: str):
        self.send, self.workspace_id, self.camera_id = send, workspace_id, camera_id
        self.recording = True

    def add_events(self, rows: list[EventRow]) -> None:
        if rows and self.recording:
            self.send({"type": "events", "workspace_id": self.workspace_id, "camera_id": self.camera_id,
                       "rows": rows})


def make_source(camera: Camera, settings: Settings):
    fps, width = max(settings.display_fps, settings.analysis_fps), settings.analysis_width
    if camera.kind == "demo":  # one decoder per clip in this process, shared by every demo camera on it
        return demo_source(camera.url, fps, width, settings.demo_realtime)
    if camera.kind == "file":
        return FileSource(camera.url, fps, realtime=settings.demo_realtime, width=width)
    if camera.kind == "webcam":
        return PushSource(fps, width)
    return StreamSource(camera.url, fps, width, guard=netguard.guard(settings.allow_private_streams))


class CameraRunner:
    """One camera inside a camera process: its source on a reader thread (which keeps the newest frame due for
    analysis, buffers clips and sends live view frames), and its pipeline, rules and evidence, run by the process's
    analysis loop."""

    def __init__(self, job: CameraJob, worker: "PoolWorker"):
        cam, s = job.camera, worker.settings
        self.camera, self.worker, self.settings, self.send = cam, worker, s, worker.send
        self.id, self.ws = cam.id, cam.workspace_id
        self.tuning = t = camera_tuning(worker.tuning, cam)
        watches = job.watches
        self.needs = needs = needs_for([w for _, w in watches]) if watches else Needs(classes={"person"})
        blobs = SocietyBlobs(worker.blobs, self.ws) if self.ws else worker.blobs
        faces = worker.faces_for(self.ws, t, job.enrollments) if needs.faces else None
        embedder = TextCache(worker.models.embedder(), job.texts) if needs.embed or needs.embedder else None
        detector = worker.models.detector(t).view()
        pose = worker.models.pose(t) if needs.pose or needs.posture else None
        self.sink = EventSink(self.send, self.ws, self.id)
        self.sink.recording = self.recording = job.record
        self.pipe = CameraPipeline(cam, self.sink, blobs, faces, s, t, needs, embedder, detector, pose)
        states = None
        if any(c.signal == "zone_state" for _, w in watches for c in w.conditions):
            states = load_states(job.examples, blobs.get, cam.zones, t.zones, embedder)
        self.engine = RuleEngine(watches, cam.zones, t, s.timezone, cam.kind, embedder, states)
        if job.health not in (None, "live"):  # still offline or covered after a restart or a watch edit: fired already
            self.engine.health = job.health
            for wid, w in watches:
                if is_health_watch(w) and holds(w, job.health):
                    self.engine.armed[(wid, None)] = False
                    self.engine.last_fired[(wid, None)] = time.time()
        self.source = make_source(cam, s)
        self.webcam = isinstance(self.source, PushSource)
        self.clips = ClipRecorder(blobs, max(s.display_fps, s.analysis_fps), t.clips, self.ws, worker.clip_writer)
        self.evidence = EvidenceBuffer(t.evidence, cam.zones, self.ws)
        self.viewers = job.viewers
        self.pending: list[tuple[float, object]] = []
        self.tracks: list[dict] = []
        self.loops, self.size = 0, (0, 0)
        self.last_frame_at = time.time()  # health counts from the start until a frame comes
        self.seen = cam.last_frame_at is not None  # a camera never set up (no frame ever) is not judged offline
        self.frame_at: float | None = None  # the newest frame, for the app
        self._t0 = time.monotonic()
        self.health = "live"
        self.done_at: deque[float] = deque()  # monotonic times of analysed frames, for fps
        self.latency_ms: deque[float] = deque(maxlen=max(1, worker.tuning.resources.latency_window))
        self.ended = False
        self._last_status = self._last_offline_tick = float("-inf")
        self._slot: tuple | None = None  # (frame, arrived): the newest frame due for analysis
        self._slot_lock = threading.Condition()
        self._last_queued = float("-inf")
        self._last_pos = None
        self._stop = threading.Event()
        # a recording read faster than real time waits for the analysis, as a scan would; live sources drop frames
        self._blocking = isinstance(self.source, FileSource) and not s.demo_realtime
        self._reader = threading.Thread(target=self._read, name=f"read-{self.id}", daemon=True)
        self._reader.start()

    # the reader thread
    def _paused(self) -> bool:
        return self.camera.kind == "demo" and (self.loops >= 1 or not self.recording) and self.viewers == 0

    def _rate(self) -> float:
        scanning = self.worker.scanning
        return self.settings.live_fps_during_scan if scanning is not None and scanning.value > 0 \
            else self.settings.analysis_fps

    def _read(self) -> None:
        try:
            while not self._stop.is_set():
                if self._paused():
                    self.last_frame_at = self.frame_at = time.time()  # resting on purpose, not offline
                    self._stop.wait(0.5)
                    continue
                frame = self.source.read(timeout=1.0)
                if frame is None:
                    if self.source.status == "ended":
                        self.ended = True
                        self.worker.wake.set()
                        return
                    continue
                arrived = time.monotonic()
                self.last_frame_at = self.frame_at = time.time()
                self.seen = True
                if frame.pos is not None and self._last_pos is not None and frame.pos < self._last_pos:
                    self.loops += 1
                    self.recording = self.recording and self.camera.kind != "demo"
                self._last_pos = frame.pos
                self.clips.on_frame(frame)
                if frame.ts - self._last_queued >= 1.0 / self._rate() - 1e-3:
                    self._last_queued = frame.ts
                    with self._slot_lock:
                        while self._blocking and self._slot is not None and not self._stop.is_set():
                            self._slot_lock.wait(0.5)
                        self._slot = (frame, arrived)
                    self.worker.wake.set()
                if self.viewers > 0 and not self.webcam:
                    h, w = frame.image.shape[:2]
                    ok, jpg = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY,
                                                                  self.settings.display_quality])
                    if ok:
                        self.send({"type": "frame", "camera_id": self.id, "jpeg": jpg.tobytes(), "ts": frame.ts,
                                   "width": w, "height": h, "tracks": self.tracks})
        except Exception:
            log.exception("reading camera %s failed", self.id)
            self.ended = True
            self.worker.wake.set()

    # the analysis loop
    def due(self) -> bool:
        return self._slot is not None

    def take(self):
        with self._slot_lock:
            item, self._slot = self._slot, None
            self._slot_lock.notify_all()
        return item

    def after(self, frame, arrived: float, result) -> None:
        """The rest of an analysed frame: evidence, the live overlay, rules, clips and candidates."""
        tick = result.tick
        self.sink.recording = self.recording
        self.size = tick.size
        self.evidence.add(frame, tick)
        self.tracks = overlay_tracks(tick)
        if self.webcam and self.viewers > 0:
            self.send({"type": "overlay", "camera_id": self.id, "ts": frame.ts, "tracks": self.tracks})
        self._judge()
        tick.health = None if self.health == "live" else self.health  # health is a signal: the rules hear it
        self._rules(tick, frame.ts, frame.image)
        for item in [p for p in self.pending if frame.ts >= p[0]]:
            self.pending.remove(item)
            self.send({"type": "candidate", "workspace_id": self.ws, "pack": self.evidence.pack(item[1])})
        now = time.monotonic()  # from the frame arriving to its rules done, waits for other cameras included
        self.done_at.append(now)
        self.latency_ms.append((now - arrived) * 1000)

    def _rules(self, tick: Tick, ts: float, image) -> None:
        """While the camera shows nothing (offline or covered), only watches about the camera itself (camera_health,
        zone_state, which turns unknown) hear the tick: to the others an empty picture would read as nobody there."""
        every = self.engine.watches
        if tick.health in NO_PICTURE:
            self.engine.watches = [(i, w) for i, w in every
                                   if any(c.signal in CAMERA_SIGNALS for c in w.conditions)]
        try:
            candidates = self.engine.on_tick(self.id, tick, now=ts, image=image)
        finally:
            self.engine.watches = every
        for c in candidates:
            if c.output == "log":
                self.send({"type": "log", "workspace_id": self.ws, "camera_id": self.id, "row": log_row(c),
                           "record": self.recording})
                continue
            if c.evidence.get("method") == "camera":  # nothing in the picture to show: no clip, no wait
                self.send({"type": "candidate", "workspace_id": self.ws, "pack": self.evidence.pack(c)})
                continue
            c.clip_key = society_key(self.ws, self.id, "clips", f"{c.fingerprint}.mp4")
            self.clips.request(c.ts, c.clip_key)
            self.pending.append((ts + self.tuning.evidence.after_s, c))

    def _judge(self) -> None:
        self.health = health_check(self.camera, self.last_frame_at, self.pipe.perceiver.tamper.problem, time.time(),
                                   self.worker.tuning.health) if self.seen else "live"

    def housekeeping(self) -> None:
        """Status for the app every Tuning resources.report_every_s, and while no frame comes, a tick every slow
        tick that says the camera is offline, so camera health watches fire and zone states turn unknown. The first
        frame back ticks without it, so they hear the camera is live again."""
        now = time.time()
        self._judge()
        if self.health == "offline" and now - self._last_offline_tick >= self.tuning.zones.slow_tick_s:
            self._last_offline_tick = now
            self._rules(Tick(now, self.size, False, [], [], health="offline"), now, None)
            for _, c in self.pending:  # no frame is coming to complete their evidence
                self.send({"type": "candidate", "workspace_id": self.ws, "pack": self.evidence.pack(c)})
            self.pending.clear()
        if now - self._last_status >= self.worker.tuning.resources.report_every_s:
            self._last_status = now
            self.send(self.status())

    def status(self, stopped: bool = False) -> dict:
        mono = time.monotonic()
        while self.done_at and self.done_at[0] < mono - 10:
            self.done_at.popleft()
        fps = len(self.done_at) / max(min(10.0, mono - self._t0), 1.0)  # analysed frames, the last 10 s
        stats = {**self.pipe.stats(), "latency_p50_ms": percentile(self.latency_ms, 50),
                 "latency_p95_ms": percentile(self.latency_ms, 95), "process": self.worker.index}
        source = "paused" if self._paused() else self.source.status
        return {"type": "status", "camera_id": self.id, "workspace_id": self.ws,
                "source_status": "stopped" if stopped else source, "loops": self.loops,
                "fps": 0.0 if stopped else round(fps, 2),
                "stats": stats, "last_frame_at": self.frame_at,
                "tamper": self.pipe.perceiver.tamper.problem, "stopped": stopped}

    def stop(self) -> None:
        self._stop.set()
        with self._slot_lock:
            self._slot_lock.notify_all()
        self._reader.join(3)  # before closing the source, which the reader may be inside
        self.source.close()
        self.clips.close()

    def finish(self) -> threading.Thread:
        """A recording reached its end: close its tracks, tell the app, and stop (on a thread of its own)."""
        self.pipe.finish(time.time())
        self.send(self.status(stopped=True))
        return self.stop_later()

    def stop_later(self) -> threading.Thread:
        """Stops off the analysis loop: joining the reader and waiting for clip encodes must not stall the process's
        other cameras."""
        thread = threading.Thread(target=self.stop, name=f"stop-{self.id}", daemon=True)
        thread.start()
        return thread


class PoolWorker:
    """The inside of one camera process, usable without a process in tests: commands in, an analysis round at a
    time."""

    def __init__(self, index: int, settings: Settings, tuning: Tuning, send, scanning=None, blobs=None):
        self.index, self.settings, self.tuning, self.send = index, settings, tuning, send
        self.scanning = scanning
        self.blobs = blobs or make_blobs(settings, tuning)
        self.models = Models(settings, tuning)
        self.clip_writer = ThreadPoolExecutor(max(1, tuning.clips.writers), thread_name_prefix="clips")
        self.cameras: dict[str, CameraRunner] = {}
        self.societies: dict[tuple[str | None, str], object] = {}  # (society, face tuning) to its face index
        self.wake = threading.Event()
        self._stopping: list[threading.Thread] = []
        self.running = True
        self._last_report = float("-inf")

    def faces_for(self, workspace_id: str | None, tuning: Tuning, enrollments: list[Enrollment]):
        """The society's face index on this process's face networks: only its own enrolled people."""
        key = (workspace_id, tuning.faces.model_dump_json())
        index = self.societies.get(key)
        if index is None:
            index = self.societies[key] = self.models.faces(tuning).share(enrollments, workspace_id)
        else:
            index.load(enrollments)
        return index

    def reload_faces(self, workspace_id: str | None, enrollments: list[Enrollment]) -> None:
        for (ws, _), index in self.societies.items():
            if ws == workspace_id:
                index.load(enrollments)

    def assign(self, job: CameraJob) -> None:
        self.remove(job.camera.id)
        try:
            self.cameras[job.camera.id] = CameraRunner(job, self)
        except Exception as e:
            log.exception("starting camera %s failed", job.camera.id)
            self.send({"type": "status", "camera_id": job.camera.id, "workspace_id": job.camera.workspace_id,
                       "source_status": "failed", "loops": 0, "fps": 0.0, "stats": {}, "last_frame_at": None,
                       "tamper": None, "stopped": False, "error": str(e)})
            return
        trim_memory()

    def remove(self, camera_id: str) -> None:
        runner = self.cameras.pop(camera_id, None)
        if runner is not None:
            self._stopping = [t for t in self._stopping if t.is_alive()] + [runner.stop_later()]
            ws = runner.ws
            if not any(r.ws == ws for r in self.cameras.values()):
                for key in [k for k in self.societies if k[0] == ws]:
                    self.societies.pop(key)

    def command(self, cmd: tuple) -> None:
        kind = cmd[0]
        if kind == "assign":
            self.assign(cmd[1])
        elif kind == "remove":
            self.remove(cmd[1])
        elif kind == "faces":
            self.reload_faces(cmd[1], cmd[2])
        elif kind == "viewers":
            if cmd[1] in self.cameras:
                self.cameras[cmd[1]].viewers = cmd[2]
        elif kind == "push":
            r = self.cameras.get(cmd[1])
            if r is not None and r.webcam:
                r.source.push(cmd[2], cmd[3])
        elif kind == "stop":
            self.running = False

    def step(self, wait_s: float = 0.05) -> int:
        """One round: every camera with a due frame is analysed once. Returns how many were."""
        due = [r for r in list(self.cameras.values()) if r.due()]
        if due:
            self._analyse(due)
        else:
            self.wake.wait(wait_s)
            self.wake.clear()
        for r in list(self.cameras.values()):
            if r.ended and not r.due():
                self.cameras.pop(r.id, None)
                self._stopping.append(r.finish())
                continue
            r.housekeeping()
        now = time.monotonic()
        if now - self._last_report >= 10:
            self._last_report = now
            self.send(self.report())
        return len(due)

    def _analyse(self, due: list[CameraRunner]) -> None:
        for r in due:
            took = r.take()
            if took is None:
                continue
            frame, arrived = took
            try:
                r.after(frame, arrived, r.pipe.process(frame, record=r.recording))
            except Exception:  # one camera's bad frame must not stop the others in this process
                log.exception("camera %s: a frame failed", r.id)

    def report(self) -> dict:
        return {"type": "process", "index": self.index, "pid": os.getpid(), "loads": dict(self.models.loads),
                "cameras": sorted(self.cameras), "rss_mb": round(rss_mb())}

    def close(self) -> None:
        for cid in list(self.cameras):
            self.remove(cid)
        for thread in self._stopping:
            thread.join()
        self.clip_writer.shutdown(wait=True)


def _sender(out_q):
    def send(msg: dict) -> None:
        try:
            if msg.get("type") in IMPORTANT or msg.get("stopped"):
                out_q.put(msg, timeout=10)
            else:
                out_q.put_nowait(msg)
        except queue.Full:
            if msg.get("type") in IMPORTANT:
                log.error("the app did not take a %s message for 10 s; dropped", msg.get("type"))
    return send


def serve(index: int, settings_json: str, tuning_json: str, cmd_q, out_q, scanning, push_q=None) -> None:
    """A camera process: commands from the app, pushed frames, analysis rounds in between."""
    settings = Settings(**json.loads(settings_json), _env_file=None)
    cv2.setNumThreads(settings.camera_threads)
    tuning = Tuning.model_validate_json(tuning_json)
    worker = PoolWorker(index, settings, tuning, _sender(out_q), scanning)
    worker.models.detector(tuning)  # the model every camera needs, loaded before the first camera arrives
    trim_memory()
    while worker.running:
        try:
            for q in (cmd_q, push_q) if push_q is not None else (cmd_q,):
                while True:
                    try:
                        worker.command(q.get_nowait())
                    except queue.Empty:
                        break
        except (EOFError, OSError):
            break
        worker.step()
    worker.close()


def _retire(*queues) -> None:
    for q in queues:
        if q is not None:
            q.cancel_join_thread()
            q.close()


@dataclass
class _Slot:
    index: int
    process: object = None
    cmd: object = None
    push: object = None  # frames for its push cameras, bounded: a slow or dead process drops them
    started_at: float = 0.0
    restarts: int = 0
    next_restart: float = 0.0
    down_since: float | None = None


class WorkerPool:
    """The app's side of the camera processes: which camera runs where (the least loaded process, in round robin
    order), assigning and removing cameras, reloading a society's faces, and starting a dead process again with its
    cameras. Processes start when their first camera arrives."""

    def __init__(self, settings: Settings, processes: int = 0, tuning: Tuning | None = None, out_q=None,
                 scanning=None, ctx=None, repo_for=None):
        import multiprocessing as mp
        self.settings, self.tuning = settings, tuning or Tuning()
        self.ctx = ctx or mp.get_context("spawn")
        self.out_q = out_q if out_q is not None else self.ctx.Queue(maxsize=2000)
        self.scanning = scanning if scanning is not None else self.ctx.Value("i", 0)
        self.repo_for = repo_for
        n = processes or settings.camera_processes or available_cpus()
        self.slots = [_Slot(i) for i in range(max(1, n))]
        self.where: dict[str, int] = {}
        self.jobs: dict[str, CameraJob] = {}
        self._next = 0
        self._lock = threading.RLock()

    @property
    def processes(self) -> int:
        return len(self.slots)

    def _pick(self) -> int:
        load = Counter(self.where.values())
        n = len(self.slots)
        order = [(self._next + k) % n for k in range(n)]
        i = min(order, key=lambda j: load[j])
        self._next = (i + 1) % n
        return i

    def _spawn(self, slot: _Slot) -> None:
        _retire(slot.cmd, slot.push)  # a dead process's queues: what they still hold must not keep the app open
        slot.cmd = self.ctx.Queue()
        slot.push = self.ctx.Queue(maxsize=max(1, self.tuning.resources.push_queue))
        slot.process = self.ctx.Process(target=serve, name=f"cameras-{slot.index}", daemon=True, args=(
            slot.index, self.settings.model_dump_json(), self.tuning.model_dump_json(), slot.cmd, self.out_q,
            self.scanning, slot.push))
        slot.process.start()
        slot.down_since, slot.started_at = None, time.time()

    def _send(self, i: int, cmd: tuple) -> None:
        slot = self.slots[i]
        if slot.process is None:
            self._spawn(slot)
        try:
            slot.cmd.put(cmd)
        except (OSError, ValueError):
            log.warning("camera process %d is not taking commands; it is started again", i)

    def assign(self, camera: Camera, watches: list[tuple[str, Watch]], enrollments: list[Enrollment],
               texts: dict | None = None, examples: list | None = None, record: bool = True,
               viewers: int = 0, health: str | None = None) -> int:
        """Runs the camera (again, with these watches) on its process, or on the least loaded one; returns which."""
        job = CameraJob(camera, watches, enrollments, texts or {}, list(examples or []), record, viewers, health)
        with self._lock:
            i = self.where.get(camera.id)
            if i is None:
                i = self.where[camera.id] = self._pick()
            self.jobs[camera.id] = job
            self._send(i, ("assign", job))
            return i

    def remove(self, camera_id: str) -> None:
        with self._lock:
            i = self.where.pop(camera_id, None)
            self.jobs.pop(camera_id, None)
            if i is not None and self.slots[i].process is not None:
                self._send(i, ("remove", camera_id))

    def reload(self, workspace_id: str, enrollments: list[Enrollment] | None = None) -> None:
        """A society's enrolled staff changed: every process with one of its cameras reloads that society's face
        index (and a restarted process gets the new list)."""
        if enrollments is None:
            enrollments = self.repo_for(workspace_id).list_enrollments()
        with self._lock:
            targets = set()
            for cid, job in self.jobs.items():
                if job.camera.workspace_id == workspace_id:
                    job.enrollments = list(enrollments)
                    targets.add(self.where[cid])
            for i in targets:
                self._send(i, ("faces", workspace_id, list(enrollments)))

    def viewers(self, camera_id: str, n: int) -> None:
        with self._lock:
            if camera_id in self.jobs:
                self.jobs[camera_id].viewers = n
                self._send(self.where[camera_id], ("viewers", camera_id, n))

    def push(self, camera_id: str, jpeg: bytes, ts: float) -> None:
        """A pushed frame for a live process, dropped when its process is down or behind (the queue is bounded)."""
        i = self.where.get(camera_id)
        if i is not None and self.slots[i].push is not None and self.alive(i):
            try:
                self.slots[i].push.put_nowait(("push", camera_id, jpeg, ts))
            except (queue.Full, OSError, ValueError):
                pass

    def alive(self, i: int) -> bool:
        p = self.slots[i].process
        return p is not None and p.is_alive()

    def down(self) -> int:
        """Processes that have cameras but are not running."""
        with self._lock:
            busy = set(self.where.values())
        return sum(1 for i in busy if not self.alive(i))

    def supervise(self, now: float | None = None, restart=None) -> list[int]:
        """Starts dead processes that have cameras again, with backoff (5 s, 10 s, 20 s, up to Tuning
        resources.restart_max_s), and gives them their cameras back: restart(camera_id) when given (the app reads
        each camera afresh), else the job each was last given. Returns the processes started."""
        now = time.time() if now is None else now
        started, cameras = [], []
        with self._lock:
            for slot in self.slots:
                cams = [cid for cid, i in self.where.items() if i == slot.index]
                if slot.process is not None and slot.process.is_alive() \
                        and now - slot.started_at >= self.tuning.resources.restart_max_s:
                    slot.restarts = 0  # it ran long enough: the next crash waits 5 s again
                if slot.process is None or slot.process.is_alive() or not cams:
                    continue
                if slot.down_since is None:
                    slot.down_since = now
                    log.warning("camera process %d stopped (exit %s) with %d cameras", slot.index,
                                slot.process.exitcode, len(cams))
                if now < slot.next_restart:
                    continue
                slot.restarts += 1
                slot.next_restart = now + min(self.tuning.resources.restart_max_s, 5 * 2 ** (slot.restarts - 1))
                self._spawn(slot)
                started.append(slot.index)
                cameras += cams
        for cid in cameras:  # outside the lock: restart takes the app's camera lock, which may wait on this one
            try:
                if restart is not None:
                    restart(cid)
                else:
                    with self._lock:
                        if cid in self.jobs:
                            self._send(self.where[cid], ("assign", self.jobs[cid]))
            except Exception:
                log.exception("giving camera %s back to its process failed", cid)
        return started

    def close(self) -> None:
        with self._lock:
            for slot in self.slots:
                if slot.process is None:
                    continue
                try:
                    slot.cmd.put(("stop",))
                except (OSError, ValueError):
                    pass
            for slot in self.slots:
                if slot.process is None:
                    continue
                slot.process.join(5)
                if slot.process.is_alive():
                    slot.process.terminate()
                _retire(slot.cmd, slot.push)

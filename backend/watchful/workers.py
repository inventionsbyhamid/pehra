"""Runs cameras: live, demo and webcam cameras share a few camera processes (pool.py), uploads are scanned in
parallel. Rows, candidates and health come back from the processes; the app writes them through each camera's own
society, checks candidates with the AI and stores alerts, and judges each camera's health."""
import hashlib
import logging
import multiprocessing as mp
import queue
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from watchful.agent.verifier import Verdict, _fallback, verify
from watchful.alerts import incidents as incidents_
from watchful.alerts.checks import CheckPool
from watchful.alerts.crops import frame_still
from watchful.components import made_embedders, make_embedder, make_llm, make_metrics, make_notifier, make_repo
from watchful.config import Settings
from watchful.evidence import EvidencePack, evidence_context, file_pack
from watchful.health import BACK, HealthBook, check as health_check, holds, is_health_watch
from watchful.notify import ApprovalNotifier
from watchful.pool import WorkerPool
from watchful.rules.engine import Candidate, log_row, needs_for
from watchful.rules.watch import Watch, select_watches
from watchful.store.blobs import SocietyBlobs
from watchful.store.db import AlertRow, Camera, EventRow
from watchful.tuning import Tuning
from watchful.vision.embed import watch_texts
from watchful.vision.perceive import Needs
from watchful.vision.scan import ScanPool, probe_start_time, scan_file

log = logging.getLogger("watchful.workers")


def should_record(repo, camera: Camera) -> bool:
    """A demo camera records its timeline once; later runs only show the live view and alerts. A demo sandbox's
    cameras never record: their day is seeded (demo.py), so a new sandbox costs no analysis until someone watches."""
    from watchful.demo_rules import in_sandbox
    if camera.kind == "demo" and in_sandbox(repo):
        return False
    return camera.kind != "demo" or not repo.query_events(camera_id=camera.id, limit=1)


def run_retention(settings: Settings, repos, blobs) -> None:
    """Clips, pictures, events and alerts older than each society's plan keeps them (billing.limits.retention_days)
    are deleted, and uploads older than retention_days, except enrolment photos and demo cameras, which replay
    consented clips for the whole judging period. `repos`: every society's repository (or one), since the blob store
    is shared."""
    from pathlib import Path
    from watchful.billing.limits import retention_days_of
    repos = repos if isinstance(repos, (list, tuple)) else [repos]
    days = {id(r): retention_days_of(r, settings.retention_days) for r in repos}
    demos = {id(r): [c.id for c in r.list_cameras() if c.kind == "demo"] for r in repos}
    from watchful.store.rekey import legacy_keys
    legacy = {id(r): legacy_keys(r) for r in repos}  # bare people/ and camera keys are kept only until rekeyed
    keep = (*(["people/"] if any(legacy.values()) else []), *(f"{r.workspace_id}/people/" for r in repos),
            *(p for r in repos for d in demos[id(r)] for p in (f"{r.workspace_id}/{d}/",
                                                                   *([f"{d}/"] if legacy[id(r)] else []))))
    owned = {id(r): (f"{r.workspace_id}/", *(f"{c.id}/" for c in r.list_cameras())) for r in repos}
    for kept in sorted(set(days.values()) or {settings.retention_days}):  # a longer plan's pictures wait for theirs
        blobs.delete_older_than(kept, keep=keep + tuple(p for r in repos if days[id(r)] > kept for p in owned[id(r)]))
    for r in repos:
        r.delete_events_before(time.time() - days[id(r)] * 86400, keep_cameras=demos[id(r)])
        r.delete_alerts_before(time.time() - days[id(r)] * 86400, keep_cameras=demos[id(r)])
    uploads = Path(settings.data_dir) / "uploads"
    for f in uploads.glob("*") if uploads.is_dir() else []:
        if f.stat().st_mtime < time.time() - settings.retention_days * 86400:
            f.unlink(missing_ok=True)


@dataclass
class WorkerStatus:
    running: bool = False
    source_status: str = "stopped"
    fps: float = 0.0
    last_ts: float | None = None
    loops: int = 0
    stats: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    health: str | None = None  # live, offline, covered, moved or blurred, as the app last judged it
    process: int | None = None  # the camera process it runs on


@dataclass
class _Cam:
    camera: Camera
    status: WorkerStatus
    last_frame_at: float | None = None  # as its process last reported, or when it was started
    tamper: str | None = None
    seen_written: float = 0.0
    started: float = field(default_factory=time.time)
    seen: bool = False  # it has sent a picture, in this run or an earlier one; a camera never seen is not judged


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


class WorkerManager:
    """Cameras of every society; each camera's rows go through its own society's repository (repo_for), known from
    start or scan_upload. A manager given one society's `repo` (tests, tools) serves that society; the app gives
    repo_for and `societies` (every society's id) instead. Camera health is a signal: a watch on it alerts like any
    other (health.py)."""

    def __init__(self, settings: Settings, tuning: Tuning, repo, blobs, llm=None, repo_for=None, societies=None,
                 processes: int | None = None):
        if repo is None and (repo_for is None or societies is None):
            raise ValueError("give one society's repo, or repo_for and societies")
        self.settings, self.tuning, self.repo, self.blobs = settings, tuning, repo, blobs
        self.home = repo.workspace_id if repo is not None else None
        self.repo_for = repo_for or (lambda ws: repo if ws == self.home else make_repo(settings, ws))
        self.societies = societies or (lambda: [self.home])
        self.camera_ws: dict[str, str] = {}  # camera id to its society, filled by start and scan_upload
        self._llms: dict[str, object] = {}
        self.ctx = mp.get_context("spawn")
        self.out_q = self.ctx.Queue(maxsize=2000)
        self.scanning = self.ctx.Value("i", 0)
        self.pool = WorkerPool(settings, processes or settings.camera_processes, tuning, self.out_q, self.scanning,
                               self.ctx, self.repo_for)
        self.cams: dict[str, _Cam] = {}
        self.processes: dict[int, dict] = {}  # each camera process's last report: pid, models loaded, RSS
        self.health = HealthBook(self.repo_for, self._health_changed)
        self._texts: dict[str, list[float]] = {}  # watch texts as vectors, made once
        self.subs: dict[str, list[Subscription]] = defaultdict(list)
        self.scans: dict[str, dict] = {}
        self.llm = llm  # tests give one; else one per society, counting against its own AI cap
        self.verifiers = CheckPool(max(1, settings.ai_parallel))  # live critical checks first (alerts/checks.py)
        self.approvals = ApprovalNotifier(make_notifier(settings))
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._scan_pool: ScanPool | None = None
        self._embedder = None
        self._closed = False
        self.metrics = make_metrics(settings)
        self.supervise_every_s = 10.0
        if settings.scan_pool_at_start:
            threading.Thread(target=lambda: self._pool().warm(), daemon=True).start()
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._supervise, daemon=True).start()
        threading.Thread(target=self._watch_health, daemon=True).start()
        threading.Thread(target=self._report, daemon=True).start()
        threading.Thread(target=self._janitor, daemon=True).start()
        self.indexer = None
        if settings.index_embeddings:
            from watchful.indexer import Indexer
            self.indexer = Indexer(self._all_repos, blobs, lambda: make_embedder(settings),
                                   busy=lambda: self.scanning.value > 0, model=settings.embedder)
            self.indexer.start()

    def _all_repos(self) -> list:
        return [self.repo_for(ws) for ws in self.societies()]

    def _janitor(self, every_s: float = 6 * 3600) -> None:
        from watchful.store.rekey import rekey_society
        for r in self._safe(self._all_repos) or []:
            self._safe(rekey_society, r, self.blobs)  # keys from before societies move under theirs, once
        while not self._closed:
            try:
                run_retention(self.settings, self._all_repos(), self.blobs)
            except Exception:
                log.exception("retention cleanup failed")
            time.sleep(every_s)

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            log.exception("%s failed", getattr(fn, "__name__", fn))
            return None

    def _report(self, every_s: float = 60) -> None:
        """Every minute: camera processes down (the alarm), and frames and milliseconds per frame per camera process
        rather than per camera, which keeps CloudWatch at a few metrics however many cameras there are."""
        while not self._closed:
            time.sleep(every_s)
            try:
                self.metrics.put("camera_workers_down", self.pool.down())
                by_process: dict[int, list[WorkerStatus]] = defaultdict(list)
                for c in list(self.cams.values()):
                    if c.status.running and c.status.process is not None:
                        by_process[c.status.process].append(c.status)
                for i, sts in sorted(by_process.items()):
                    dims = {"Process": str(i)}
                    self.metrics.put("FramesPerSecond", sum(s.fps for s in sts) / len(sts), dims)
                    ms = [s.stats["ms_per_frame"] for s in sts if "ms_per_frame" in (s.stats or {})]
                    if ms:
                        self.metrics.put("MsPerFrame", sum(ms) / len(ms), dims)
                self.metrics.put("AICallsToday", sum(r.count_ai_calls_today() for r in self._all_repos()))
                self.metrics.flush()
            except Exception:
                log.exception("metrics report failed")

    # societies
    def _society(self, camera_id: str) -> str:
        ws = self.camera_ws.get(camera_id) or self.home
        if ws is None:
            raise KeyError(f"camera {camera_id} has no society here; start it with its workspace_id")
        return ws

    def _repo(self, camera_id: str):
        return self.repo_for(self._society(camera_id))

    def _llm(self, camera_id: str):
        if self.llm is not None:
            return self.llm
        ws = self._society(camera_id)
        with self._lock:
            if ws not in self._llms:
                self._llms[ws] = make_llm(self.settings, self.repo_for(ws), "verify")
            return self._llms[ws]

    # cameras
    def _watches(self, camera_id: str, zones: list | None = None) -> tuple[list[tuple[str, Watch]], list[str]]:
        """The camera's enabled watches that can run, and a plain note for each that can't."""
        repo = self._repo(camera_id)
        zones = zones if zones is not None else repo.get_camera(camera_id).zones
        return select_watches(repo.list_watches(camera_id), camera_id, zones)

    def _camera_lock(self, camera_id: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(camera_id, threading.Lock())

    def start(self, camera_id: str, workspace_id: str | None = None) -> None:
        if workspace_id:
            self.camera_ws[camera_id] = workspace_id
        with self._camera_lock(camera_id):
            self._start(camera_id)

    def _watch_vectors(self, watches: list[tuple[str, Watch]]) -> dict[str, list[float]]:
        """Watch texts as vectors, made here once so camera processes never load the text tower."""
        wanted = watch_texts([w for _, w in watches])
        missing = wanted - self._texts.keys()
        if missing:
            emb = make_embedder(self.settings)
            for t in missing:
                self._texts[t] = emb.text(t).tolist()
        return {t: self._texts[t] for t in wanted}

    def _start(self, camera_id: str) -> None:
        repo = self._repo(camera_id)
        cam = repo.get_camera(camera_id)
        if cam is None or not cam.enabled:
            self._stop(camera_id)
            return
        watches, problems = self._watches(camera_id, cam.zones)
        examples = repo.list_state_examples(cam.id) \
            if any(c.signal == "zone_state" for _, w in watches for c in w.conditions) else []
        if cam.last_frame_at is not None:  # seen in an earlier run; a new camera's row says offline by default
            self.health.seed(cam.id, cam.status)
        process = self.pool.assign(cam, watches, repo.list_enrollments(), self._watch_vectors(watches), examples,
                                   should_record(repo, cam), len(self.subs[camera_id]), self.health.state(cam.id))
        old = self.cams.get(camera_id)
        kept = old if old is not None and old.status.running else None  # a restart keeps the offline clock
        self.cams[camera_id] = _Cam(cam, WorkerStatus(running=True, source_status="starting", problems=problems,
                                                      health=self.health.state(cam.id), process=process),
                                    last_frame_at=kept.last_frame_at if kept else time.time(),
                                    seen=cam.last_frame_at is not None or bool(kept and kept.seen))

    def stop(self, camera_id: str) -> None:
        with self._camera_lock(camera_id):
            self._stop(camera_id)

    def _stop(self, camera_id: str) -> None:
        self.pool.remove(camera_id)
        c = self.cams.get(camera_id)
        if c is not None and c.status.running:
            c.status.running = False
            c.status.source_status = "stopped"
            if self.health.forget(camera_id) not in (None, "live"):  # not watched now: nothing is left open
                self._resolve_health(self._society(camera_id), camera_id, None, time.time(),
                                     "The camera was turned off.")

    def restart(self, camera_id: str) -> None:
        c = self.cams.get(camera_id)
        if c is not None and c.status.running:
            self.start(camera_id)

    def reload_faces(self, workspace_id: str) -> None:
        """A society's enrolled staff changed: every camera process watching one of its cameras reloads its face
        index; no camera restarts."""
        self.pool.reload(workspace_id, self.repo_for(workspace_id).list_enrollments())

    def status(self, camera_id: str) -> WorkerStatus:
        c = self.cams.get(camera_id)
        if c is None:
            return WorkerStatus()
        if not c.status.running:
            c.status.health = None  # a stopped camera has no health now; its last one stays on its row
        elif not c.seen and time.time() - c.started >= self.tuning.health.never_s:
            c.status.health = "never"  # running all this while and no picture yet: the address or device is wrong
        else:
            c.status.health = self.health.state(camera_id)
        return c.status

    def subscribe(self, camera_id: str) -> Subscription:
        q = Subscription()
        self.subs[camera_id].append(q)
        self.pool.viewers(camera_id, len(self.subs[camera_id]))
        return q

    def unsubscribe(self, camera_id: str, q: Subscription) -> None:
        if q in self.subs[camera_id]:
            self.subs[camera_id].remove(q)
            self.pool.viewers(camera_id, len(self.subs[camera_id]))

    def push_frame(self, camera_id: str, jpeg: bytes, ts: float) -> None:
        self.pool.push(camera_id, jpeg, ts)

    def _publish(self, camera_id: str, msg: dict) -> None:
        for q in list(self.subs.get(camera_id, [])):
            q.put(msg)

    def _supervise(self) -> None:
        """Starts camera processes that died again, with their cameras, with backoff (pool.WorkerPool.supervise)."""
        while not self._closed:
            time.sleep(self.supervise_every_s)
            try:
                self.pool.supervise(restart=self.start)
            except Exception:
                log.exception("restarting camera processes failed")

    # health
    def _watch_health(self) -> None:
        while not self._closed:
            time.sleep(self.tuning.health.check_every_s)
            try:
                self.check_health()
                for emb in made_embedders():
                    getattr(emb, "release_text", lambda _: False)(self.tuning.resources.text_tower_idle_s)
            except Exception:
                log.exception("camera health check failed")

    def check_health(self, now: float | None = None) -> None:
        """Each running camera's health from what its process last reported, so a camera whose process died, or
        that stopped reporting, goes offline too. A change is written to its row and files or resolves an incident
        (HealthBook); a live camera's last picture time is written every Tuning health.seen_every_s."""
        now = time.time() if now is None else now
        for cid, c in list(self.cams.items()):
            if not c.status.running or not c.seen or (c.status.source_status == "starting"
                                                      and now - c.started < self.tuning.health.start_s):
                continue  # never set up yet, or its process is still starting (loading models) or starting again
            ws = self._society(cid)
            state = health_check(c.camera, c.last_frame_at, c.tamper, now, self.tuning.health)
            changed = self.health.update(ws, cid, state, now)
            c.status.health = state
            due = state != "offline" and now - c.seen_written >= self.tuning.health.seen_every_s
            if c.last_frame_at and (changed or due):
                c.seen_written = now
                self.repo_for(ws).set_camera_health(cid, last_frame_at=c.last_frame_at)

    def _health_changed(self, workspace_id: str, camera_id: str, old: str | None, new: str, ts: float) -> None:
        self._publish(camera_id, {"type": "health", "camera_id": camera_id, "health": new})
        if old not in (None, "live"):
            self._resolve_health(workspace_id, camera_id, new, ts, BACK if new == "live" else "")
        c = self.cams.get(camera_id)
        if new != "live" and c is not None and c.status.process is not None and not self.pool.alive(c.status.process):
            self._fire_health(workspace_id, camera_id, new, ts)

    def _fire_health(self, workspace_id: str, camera_id: str, state: str, ts: float) -> None:
        """A dead camera process ticks no rules, so the app fires the camera's health watches for it."""
        for wid, w in self._watches(camera_id)[0]:
            if not (is_health_watch(w) and holds(w, state)):
                continue
            fp = hashlib.sha1(f"{camera_id}|{wid}|{int(ts // max(w.cooldown_s, 1))}".encode()).hexdigest()
            c = Candidate(wid, camera_id, ts, None, "info" if w.output == "log" else w.severity,
                          {"signals": {"camera_health": state}, "reasons": [f"camera {state}"], "score": 1.0,
                           "method": "camera"}, [], fp, w.name, w.verify, output=w.output)
            if w.output == "log":
                self._handle({"type": "log", "workspace_id": workspace_id, "camera_id": camera_id, "row": log_row(c)})
            else:
                self._on_candidate(EvidencePack(c, [], [], workspace_id=workspace_id))

    def _resolve_health(self, workspace_id: str, camera_id: str, state: str | None, ts: float, note: str) -> None:
        """Resolves the camera's open incidents of camera health watches that no longer hold for `state` (None: the
        camera is no longer watched, so all of them)."""
        repo = self.repo_for(workspace_id)
        watches = dict(self._watches(camera_id)[0])
        for inc in incidents_.list_incidents(repo, incidents_.ACTIVE, camera_id):
            w = watches.get(inc.watch_id)
            if w is not None and is_health_watch(w) and (state is None or not holds(w, state)):
                self._incident(lambda: incidents_.resolve(repo, inc.id, ts, note or BACK))

    # messages from the camera processes
    def _listen(self) -> None:
        while not self._closed:
            try:
                msg = self.out_q.get(timeout=0.5)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return
            try:
                self._handle(msg)
            except Exception:  # one bad message must not stop every camera's live view
                log.exception("handling a %s message failed", msg.get("type"))

    def _from_society(self, msg: dict) -> str | None:
        """The camera's own society, when the message agrees with it; rows are written only there."""
        ws = self.camera_ws.get(msg["camera_id"]) or self.home
        if ws is None or msg.get("workspace_id") not in (None, ws):
            log.error("a message for camera %s named another society; dropped", msg["camera_id"])
            return None
        return ws

    def _handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind in ("frame", "overlay"):
            self._publish(msg["camera_id"], msg)
        elif kind == "events":
            ws = self._from_society(msg)
            rows = [r for r in msg["rows"] if r.camera_id == msg["camera_id"]]
            if ws and rows:
                self.repo_for(ws).add_events(rows)
        elif kind == "log":
            ws = self._from_society(msg)
            row: EventRow = msg["row"]
            if ws and msg.get("record", True):
                self.repo_for(ws).add_events([row])  # a log watch writes a row and never alerts
            self._publish(msg["camera_id"], {"type": "log", "camera_id": msg["camera_id"], "row": asdict(row)})
        elif kind == "status":
            self._status(msg)
        elif kind == "candidate":
            pack = msg["pack"]
            if self._from_society({"camera_id": pack.candidate.camera_id, "workspace_id": msg.get("workspace_id")}):
                self._on_candidate(pack)
        elif kind == "process":
            self.processes[msg["index"]] = msg

    def _status(self, msg: dict) -> None:
        c = self.cams.get(msg["camera_id"])
        if c is None or not c.status.running:
            return
        st = c.status
        st.source_status, st.loops, st.fps, st.stats = msg["source_status"], msg["loops"], msg["fps"], msg["stats"]
        st.last_ts = time.time()
        if msg.get("error"):
            st.problems = [*st.problems, msg["error"]]
        if msg.get("last_frame_at"):
            c.last_frame_at, c.seen = msg["last_frame_at"], True
        c.tamper = msg.get("tamper")
        if msg.get("stopped"):  # a recording ended on its own, not a crash
            st.running = False
            self.pool.remove(msg["camera_id"])
            self.health.forget(msg["camera_id"])
        self._publish(msg["camera_id"], {k: v for k, v in msg.items() if k != "workspace_id"})

    # incidents: repeats of one situation fold into one record and one Telegram message (alerts/incidents.py)
    def _incident(self, fn):
        try:
            return fn()
        except Exception:  # the alert itself is stored; an incident problem must not lose it
            log.exception("filing the incident failed")
            return None

    def _picture(self, repo, pack: EvidencePack) -> tuple[str | None, list | None]:
        """A private frame of the moment and the object's box in it, for the incident (Telegram's still is cut from
        it with faces blurred, in the job worker)."""
        c = pack.candidate
        if not pack.frames:
            return None, None
        ts, jpeg = min(pack.frames, key=lambda f: abs(f[0] - c.ts))
        box = min(pack.boxes, key=lambda b: abs(b[0] - ts))[1] if pack.boxes else None
        data, inner = frame_still(jpeg, box, self.tuning.crops)
        key = f"{repo.workspace_id}/incidents/{c.fingerprint}.jpg"
        self.blobs.put(key, data, "image/jpeg")
        return key, inner

    def _repeat_or_open(self, pack: EvidencePack) -> tuple[bool, str | None]:
        """A live candidate: one that repeats an open incident only adds to its count (no second AI check); a
        critical one opens its incident now, so Telegram hears before the check finishes. Returns (handled, the
        incident opened early)."""
        c, repo = pack.candidate, self._repo(pack.candidate.camera_id)
        t = self.tuning.alerts
        if incidents_.active_incident(repo, c.camera_id, c.watch_id, c.ts, t) is not None:
            incidents_.open_or_touch_incident(repo, c.camera_id, c.watch_id, c.severity, "",
                                              c.keyframe_keys[0] if c.keyframe_keys else None, c.ts, tuning=t)
            return True, None
        if c.severity != "critical":
            return False, None
        key, box = self._picture(repo, pack)
        return False, incidents_.open_before_check(repo, c, key, box, tuning=t).id

    # alerts
    def _on_candidate(self, pack: EvidencePack) -> None:
        c = pack.candidate
        with self._lock:
            if c.fingerprint in self._inflight or self._repo(c.camera_id).alert_fingerprint_exists(
                    c.fingerprint, within_s=self.settings.alert_dedupe_days * 86400):
                return
            self._inflight.add(c.fingerprint)
        handled, early = self._incident(lambda: self._repeat_or_open(pack)) or (False, None)
        if handled:
            with self._lock:
                self._inflight.discard(c.fingerprint)
            return
        if c.evidence.get("method") == "camera":  # nothing for the AI to check: filed now, never behind AI checks
            self._verify_and_store(pack, early, True)
            self._settle_health(pack)
            return
        self.verifiers.submit(self._verify_and_store, pack, early, True)

    def _settle_health(self, pack: EvidencePack) -> None:
        """A camera health alert filed after its camera already changed (a brief cover): resolved at once."""
        c = pack.candidate
        ws = self._society(c.camera_id)
        state = self.health.state(c.camera_id)
        if state is not None and self.health.since.get(c.camera_id, 0.0) > c.ts:
            self._resolve_health(ws, c.camera_id, state, time.time(), BACK if state == "live" else "")

    def _verify_and_store(self, pack: EvidencePack, incident_id: str | None = None, live: bool = False) -> str | None:
        c = pack.candidate
        try:
            verdict = verify(c, evidence_context(pack, self._repo(c.camera_id), self.blobs), self._llm(c.camera_id))
        except Exception:
            log.exception("verifying %s failed", c.fingerprint)
            verdict = _fallback(c, [], "the check failed")
        try:
            row = self._store(pack, verdict)
        except Exception:
            log.exception("storing the alert %s failed", c.fingerprint)
            return None
        finally:
            with self._lock:
                self._inflight.discard(c.fingerprint)
        self._incident(lambda: self._file(pack, verdict, row.id, incident_id, live))
        return row.id

    def _file(self, pack: EvidencePack, verdict: Verdict, alert_id: str, incident_id: str | None, live: bool):
        repo = self._repo(pack.candidate.camera_id)
        key, box = (None, None) if incident_id or verdict.status == "dismissed" else self._picture(repo, pack)
        return incidents_.after_check(repo, pack.candidate, verdict, alert_id, incident_id, tuning=self.tuning.alerts,
                                      notify=live, picture_key=key, picture_box=box)

    def _store(self, pack: EvidencePack, v: Verdict) -> AlertRow:
        c = pack.candidate
        row = self._repo(c.camera_id).add_alert(AlertRow(None, c.camera_id, c.watch_id, c.ts, c.severity, v.status,
                                           v.summary or c.watch_name, c.clip_key,
                                           c.keyframe_keys[0] if c.keyframe_keys else None,
                                           [asdict(t) for t in v.trace], None, c.fingerprint, c.track_id))
        self._publish(c.camera_id, {"type": "alert", "alert": {**asdict(row), "keyframe_url":
                                    self.blobs.url(row.keyframe_key) if row.keyframe_key else None}})
        self.metrics.put("Alerts", 1, {"Status": v.status})
        return row

    def approve(self, alert_id: str, workspace_id: str | None = None) -> bool:
        if not (workspace_id or self.home):
            raise ValueError("approve needs the alert's society")
        repo = self.repo_for(workspace_id or self.home)
        row = repo.get_alert(alert_id)
        if row is None:
            return False
        row.status = "approved"
        repo.update_alert(row)
        return self.approvals.approve(row)

    # uploads
    def _pool(self) -> ScanPool:
        with self._lock:
            if self._scan_pool is None:
                self._scan_pool = ScanPool(self.settings, self.tuning)
            return self._scan_pool

    def scan_upload(self, camera_id: str, workspace_id: str | None = None) -> str:
        if workspace_id:
            self.camera_ws[camera_id] = workspace_id
        scan_id = "scn_" + secrets.token_hex(6)
        self.scans[scan_id] = {"state": "scanning", "progress": 0.0, "stats": None, "alert_ids": [],
                               "camera_id": camera_id, "error": None,
                               "workspace_id": self.camera_ws.get(camera_id) or self.home}
        threading.Thread(target=self._run_scan, args=(scan_id, camera_id), daemon=True).start()
        return scan_id

    def scan(self, scan_id: str) -> dict | None:
        return self.scans.get(scan_id)

    def _run_scan(self, scan_id: str, camera_id: str) -> None:
        info = self.scans[scan_id]
        repo = self._repo(camera_id)
        cam = repo.get_camera(camera_id)
        with self.scanning.get_lock():
            self.scanning.value += 1
        try:
            watches, _ = self._watches(camera_id, cam.zones)
            needs = needs_for([w for _, w in watches]) if watches else Needs(classes={"person"})
            if needs.embed and self._embedder is None:
                self._embedder = make_embedder(self.settings)
            # the recording's own clock: its creation time, kept the same across rescans
            start_epoch = ((cam.last_scan or {}).get("start_epoch") or probe_start_time(cam.url) or time.time())
            result = scan_file(cam.url, cam, watches, self._pool(), SocietyBlobs(self.blobs, repo.workspace_id),
                               self.settings, self.tuning,
                               start_epoch=start_epoch, enrollments=repo.list_enrollments(),
                               embedder=self._embedder if needs.embed else None,
                               on_progress=lambda p: info.update(progress=round(p, 3)))
            repo.delete_camera_events(cam.id)  # a rescan replaces the earlier timeline
            repo.add_events(result.events)
            info.update(state="checking", stats=asdict(result.stats))
            repo.set_last_scan(cam.id, {**asdict(result.stats), "start_epoch": start_epoch, "finished_at": time.time()})
            self.metrics.put("ScanRealtimeFactor", result.stats.x_realtime)
            self.metrics.put("ScanSecondsPerMinute", result.stats.wall_s / max(result.stats.duration_s / 60, 1e-6))
            dedupe_s = self.settings.alert_dedupe_days * 86400 + max(0.0, time.time() - start_epoch)
            repo.add_events([log_row(c) for c in result.candidates if c.output == "log"])
            fresh = [c for c in result.candidates if c.output != "log"
                     and not repo.alert_fingerprint_exists(c.fingerprint, dedupe_s)]
            packs = [file_pack(cam.url, start_epoch, c, result.paths.get(c.track_id, []),
                               result.poses.get(c.track_id, []), self.tuning.evidence, cam.zones, repo.workspace_id)
                     for c in fresh]
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
        for cid in list(self.cams):
            self.stop(cid)
        self._closed = True
        self.pool.close()
        if self._scan_pool:
            self._scan_pool.close()
        self.verifiers.shutdown(wait=False, cancel_futures=True)

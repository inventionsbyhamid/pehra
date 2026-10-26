"""The judges' demo (sandbox lifecycle, caps and reset from packsure e97a509). "Try the demo" (api/demo.py) gives each
visitor a sandbox society of their own for Tuning limits.demo_hours: the demo cameras of data/demo.json whose clip
files exist, looping as if live, with their watches, a day of incidents (pictures cut from their own clips) and log
rows, and a short timeline of what the camera saw (so a typed watch backtests to something); the visitor can add
this laptop or a phone as a camera and type watches of their own. The seeded incidents are examples made from real
moments of the clip: no AI check ran on them, and the app labels them so. A visitor is a user without an email,
flagged demo, and the owner of their sandbox. Nobody else ever sees a sandbox.

A sandbox is comped the Society plan until it ends, for Tuning limits.demo_cameras cameras of the visitor's own (the
demo's clips are not watched cameras, billing/limits.py), and the plan sweep leaves sandboxes alone. Its AI calls
have caps of their own (SandboxLLM). When it ends its cameras stop (api/demo.py SandboxWatch), every request in it is
refused (demo_rules.py), and the daily `demo_reset` job deletes it with its visitor, device links and pictures.

A demo camera with no clip file is left out, never shown with another clip: today only OpenCV's sample vtest.avi
(Apache 2.0) is known to be safe to show, so Plaza runs on it, and Pool and the gates appear when the owner's consented
recordings land in samples/demo/.

    python -m watchful.demo cameras    which demo cameras new sandboxes get, and which clips are missing
    python -m watchful.demo reset      delete sandboxes that ended, now"""
import argparse
import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import delete, exists, func, select

from watchful.agent.llm import AILimitReached, LimitedLLM
from watchful.alerts import incidents as inc_
from watchful.alerts.crops import frame_still
from watchful.db import models as m
from watchful.demo_rules import SANDBOX, is_sandbox
from watchful.jobs import JobContext, handler
from watchful.netguard import inside, sample_roots
from watchful.rules.presets import preset
from watchful.rules.vocab import DATA
from watchful.rules.watch import Watch
from watchful.store.blobs import society_key
from watchful.store.db import Camera, EventRow, WatchRow
from watchful.store.sql import add_default_watches
from watchful.tuning import Tuning
from watchful.vision.zones import Zone

TIMELINE_CLASSES = {"person", "bicycle", "car", "motorcycle", "bus", "truck"}  # what a typed watch is likely about

log = logging.getLogger(__name__)

SPEC = DATA / "demo.json"
AI_CALL = "demo.ai_call"  # an Audit row per sandbox AI call: it outlives the sandbox, so the day's total holds
PLAN = "society"  # the plan a sandbox is comped while it lasts


@lru_cache
def spec() -> dict:
    return json.loads(SPEC.read_text())


def clip_path(settings, clip: str) -> Path | None:
    """The clip's file under one of the samples folders, or None when it is not there."""
    for root in sample_roots(settings.data_dir):
        path = Path(root) / clip
        if path.is_file() and inside(path, sample_roots(settings.data_dir)):
            return path.resolve()
    return None


@dataclass
class DemoCamera:
    key: str
    item: dict
    path: Path


def demo_cameras(settings) -> list[DemoCamera]:
    """The demo cameras whose clip file exists, in demo.json's order."""
    return [DemoCamera(c["key"], c, p) for c in spec()["cameras"] if (p := clip_path(settings, c["clip"]))]


def missing_cameras(settings) -> list[dict]:
    return [c for c in spec()["cameras"] if clip_path(settings, c["clip"]) is None]


def zones_of(item: dict) -> list[Zone]:
    return [Zone(m.new_id("zone"), z["name"], [tuple(p) for p in z["polygon"]], z.get("kind", "area"))
            for z in item.get("zones", [])]


def watch_of(entry: dict) -> Watch:
    """A demo watch: a preset with its fill, or the watch itself, with demo.json's name, severity and output."""
    w = preset(entry["preset"], **entry.get("fill", {})) if "preset" in entry else Watch.model_validate(entry["watch"])
    changes = {k: entry[k] for k in ("name", "severity", "output") if k in entry}
    return w.model_copy(update=changes) if changes else w


# pictures of seeded incidents, cut from the camera's own clip

_detector_lock = threading.Lock()
_detectors: dict[str, object] = {}


def _detector(settings, tuning: Tuning):
    from watchful.components import make_detector
    with _detector_lock:
        if "person" not in _detectors:
            _detectors["person"] = make_detector(settings, tuning, {"person"})
        return _detectors["person"]


def clip_frame(path: Path, clip_s: float) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(clip_s) * 1000)
        ok, image = cap.read()
        return image if ok else None
    finally:
        cap.release()


def _foot_in(zone: Zone, box, size) -> bool:
    x, y, w, h = box
    return zone.contains((x + w / 2, y + h), size)


@lru_cache(maxsize=64)
def _picture(settings_json: str, path: str, mtime: float, clip_s: float, zone_json: str | None,
             track: bool) -> tuple[bytes, tuple | None] | None:
    from watchful.config import Settings
    settings = Settings(**json.loads(settings_json), _env_file=None)
    tuning = Tuning()
    image = clip_frame(Path(path), clip_s)
    if image is None:
        return None
    box = None
    if track:  # the person the watch is about: in its zone, the biggest; a scene watch keeps the whole frame
        size = (image.shape[1], image.shape[0])
        people = [d.box for d in _detector(settings, tuning).detect(image)]
        if zone_json:
            z = json.loads(zone_json)
            zone = Zone("z", z["name"], [tuple(p) for p in z["polygon"]], z.get("kind", "area"))
            people = [b for b in people if _foot_in(zone, b, size)] or people
        if people:
            box = max(people, key=lambda b: b[2] * b[3])
    ok, jpg = cv2.imencode(".jpg", image)
    if not ok:
        return None
    data, inner = frame_still(jpg.tobytes(), box, tuning.crops)
    return data, tuple(inner) if inner else None


def incident_picture(settings, path: Path, clip_s: float, watch: Watch,
                     zones: list[dict]) -> tuple[bytes, list | None] | None:
    """The private picture of a seeded incident: the clip's frame at clip_s around the person the watch is about."""
    track = not any(c.signal in ("count", "zone_looks_like", "zone_state", "vanished_in_zone", "camera_health")
                    for c in watch.conditions)
    named = [z for z in zones if z["name"] in {n for c in watch.conditions for n in c.zones}]
    zone_json = json.dumps(named[0], sort_keys=True) if named else None
    got = _picture(settings.model_dump_json(), str(path), path.stat().st_mtime, float(clip_s), zone_json, track)
    if got is None:
        return None
    data, inner = got
    return data, list(inner) if inner else None


# sandboxes

def sandbox_of(db, user_id: str) -> m.Workspace | None:
    with db.tx() as s:
        return s.scalar(select(m.Workspace).join(m.Member, m.Member.workspace_id == m.Workspace.id)
                        .where(m.Member.user_id == user_id, m.Workspace.demo == SANDBOX)
                        .order_by(m.Workspace.created_at.desc()).limit(1))


def sandboxes_started_since(db, since: datetime) -> int:
    with db.tx() as s:
        return s.scalar(select(func.count()).select_from(m.Workspace)
                        .where(m.Workspace.demo == SANDBOX, m.Workspace.created_at >= since)) or 0


@dataclass
class Seeded:
    cameras: dict[str, str] = field(default_factory=dict)  # demo.json key to camera id
    watches: dict[str, list[str]] = field(default_factory=dict)  # demo.json key to its watch ids, in order


def start_sandbox(ctx, now: datetime | None = None, network: str | None = None) -> tuple[m.User, m.Workspace]:
    """A new demo visitor and their sandbox (`ctx`: the app's context), made in one transaction: the society, comped
    until it ends, with its default site watches. Its demo cameras and their day are seeded next (seed), then the app
    starts the cameras. `network` is the visitor's (an IPv4 /24 or IPv6 /48): its sandboxes share an AI budget."""
    from watchful.api.account import add_workspace
    now = now or m.utcnow()
    t = ctx.tuning.limits
    ends = now + timedelta(hours=t.demo_hours)
    with ctx.db.tx() as s:
        user = m.User(name=spec().get("visitor", "Demo visitor"), demo=True)
        s.add(user)
        s.flush()
        ws = add_workspace(s, user.id, spec().get("society", "Demo society"), ctx.settings.timezone, demo=SANDBOX,
                           created_at=now, expires_at=ends, comp_plan=PLAN, comp_cameras=t.demo_cameras,
                           comp_until=ends)
        s.add(m.Audit(workspace_id=ws.id, actor=user.id, action="demo.start",
                      data={"expires_at": ends.isoformat(), "network": network}))
    try:
        seed(ctx.repo_for(ws.id), ctx.blobs, ctx.settings, now)
    except Exception:
        log.exception("seeding demo sandbox %s failed; it is deleted", ws.id)
        _drop(ctx.db, ctx.blobs, ws.id)
        raise
    return user, ws


def seed(repo, blobs, settings, now: datetime) -> Seeded:
    """The sandbox's default site watches (the camera health watch every society starts with), its demo cameras with
    their watches, a day of incidents and log rows, and the gate log and staff register
    (seed_gate_log_and_register). Cameras are added switched on; the app starts them."""
    out = Seeded()
    if not any(r.camera_id is None for r in repo.list_watches()):
        with repo.tx() as s:
            add_default_watches(s, repo.workspace_id)
    for cam in demo_cameras(settings):
        item = cam.item
        made = repo.add_camera(Camera(None, item["name"], "demo", str(cam.path), zones_of(item),
                                      purpose=item.get("purpose", "other")))
        out.cameras[cam.key] = made.id
        rows = []
        for entry in item.get("watches", []):
            w = watch_of(entry)
            rows.append(repo.add_watch(WatchRow(None, made.id, entry.get("text") or w.name, w.model_dump(mode="json"),
                                                True)))
        out.watches[cam.key] = [r.id for r in rows]
        watches = [watch_of(e) for e in item.get("watches", [])]
        for n, spec_inc in enumerate(item.get("incidents", [])):
            _seed_incident(repo, blobs, settings, cam, made.id, rows, watches, spec_inc, n, now)
        events = [_log_row(made.id, rows, watches, r, now) for r in item.get("log_rows", [])]
        events += seeded_timeline(settings, cam, made.id, now)
        if events:
            repo.add_events(events)
    with repo.tx() as s:
        seed_gate_log_and_register(s, repo.workspace_id, out, spec(), now, blobs)
    return out


def _seed_incident(repo, blobs, settings, cam: DemoCamera, camera_id: str, rows: list[WatchRow], watches: list[Watch],
                   item: dict, n: int, now: datetime) -> None:
    watch, row = watches[item["watch"]], rows[item["watch"]]
    opened = inside_hours(watch, now - timedelta(hours=float(item["hours_ago"])), settings.timezone)
    last = opened + timedelta(minutes=2 * max(0, int(item.get("count", 1)) - 1))
    key = box = None
    got = incident_picture(settings, cam.path, item.get("clip_s", 0), watch, cam.item.get("zones", []))
    if got is not None:
        data, box = got
        key = society_key(repo.workspace_id, "incidents", f"demo_{cam.key}_{n}.jpg")
        blobs.put(key, data, "image/jpeg")
    track = box is not None
    status = item.get("status", inc_.OPEN)
    agent = {"cls": "person" if track else None, "seeded": True}  # an example: no AI check ran on it
    acted = status in (inc_.ACKNOWLEDGED, inc_.FALSE_ALARM)
    at = last + timedelta(minutes=3) if status != inc_.OPEN else None
    if status == inc_.RESOLVED:
        agent["closed_by"] = item.get("closed_by", "Resolved")
    with repo.tx() as s:
        inc = m.Incident(workspace_id=repo.workspace_id, camera_id=camera_id, watch_id=row.id,
                         severity=watch.severity, status=status, opened_at=opened, last_at=last,
                         count=int(item.get("count", 1)), reason=item.get("reason", watch.name),
                         picture_key=key, picture_box=box, agent_json=agent,
                         ack_by=item.get("by") if acted else None, ack_at=at if acted else None,
                         closed_at=at if status in (inc_.FALSE_ALARM, inc_.RESOLVED) else None)
        s.add(inc)
        s.flush()
        if acted:
            action = "incident.acknowledge" if status == inc_.ACKNOWLEDGED else "incident.false_alarm"
            s.add(m.Audit(workspace_id=repo.workspace_id, actor="demo", action=action, ts=at,
                          data={"incident": inc.id, "watch": row.id, "camera": camera_id, "name": item.get("by"),
                                "via": item.get("via", "app"), "chat": None, "changed": True}))


def inside_hours(watch: Watch, at: datetime, timezone: str) -> datetime:
    """`at`, or the latest moment before it inside the watch's hours (an after hours watch fires only then), so an
    example is never seeded at a time its watch could not fire, whenever the sandbox starts."""
    if watch.time_window is None:
        return at
    for steps in range(24 * 4):
        t = at - timedelta(minutes=15 * steps)
        if watch.time_window.contains(t.timestamp(), timezone):
            return t
    return at


def _log_row(camera_id: str, rows: list[WatchRow], watches: list[Watch], item: dict, now: datetime) -> EventRow:
    w, row = watches[item["watch"]], rows[item["watch"]]
    ts = (now - timedelta(hours=float(item["hours_ago"]))).timestamp()
    return EventRow(None, camera_id, ts, "watch_log", None, None, None,
                    {"watch_id": row.id, "watch": w.name, "reasons": item.get("reasons", [w.name]), "signals": {},
                     "seeded": True}, None, None, None, None)


def seed_gate_log_and_register(s, workspace_id: str, seeded: Seeded, demo: dict, now: datetime,
                               blobs=None) -> dict[str, int]:
    """The seeding hook for the gate log (vehicle passages) and the staff register (staff and attendance), in the
    sandbox's transaction `s` (logs/demo.py): yesterday and today at the gate with rendered plates, on the Main gate
    camera (else the Service gate; with neither, no passages, since a passage needs its camera), and made up staff
    with templates that match no face. A demo camera's "ledger" rows in demo.json, when given ({"direction",
    "hours_ago", "colour", "cls", "plate"}), replace the generated passages."""
    from watchful.logs import demo as day
    rows = [dict(r, at=now - timedelta(hours=float(r["hours_ago"])))
            for c in demo.get("cameras", []) for r in c.get("ledger", [])]
    return day.seed(s, workspace_id, seeded.cameras, now, blobs, rows=sorted(rows, key=lambda r: r["at"]) or None)


# the demo's AI

class SandboxLLM(LimitedLLM):
    """The AI for a sandbox, with the demo's own caps a day: Tuning limits.demo_ai_checks checks of flagged moments
    (one per check however many steps it takes, LimitedLLM.check) and limits.demo_ai_assist requests of help (writing
    watches, questions, search); the sandboxes started from one network within limits.demo_ai_per_network, and every
    sandbox together within limits.demo_ai_total in the last day, counted from Audit rows the demo reset keeps. The
    demo's budget is its own, so judges never use up a society's AI_DAILY_CALL_LIMIT or theirs."""

    def __init__(self, inner, repo, limit: int, task: str, used, total: int, per_network: int, everyone: int = 0):
        super().__init__(inner, repo, limit, task, used=used, total=everyone)
        self.demo_total, self.per_network = total, per_network
        with repo.tx() as s:
            started = s.scalar(select(m.Audit.data).where(m.Audit.workspace_id == repo.workspace_id,
                                                          m.Audit.action == "demo.start").limit(1))
        self.network = (started or {}).get("network")

    def admit(self) -> None:
        since = m.utcnow() - timedelta(days=1)
        recent = [m.Audit.action == AI_CALL, m.Audit.ts >= since]
        with self.repo.tx() as s:
            spent = s.scalar(select(func.count()).select_from(m.Audit).where(*recent)) or 0
            mine = s.scalar(select(func.count()).select_from(m.Audit).where(
                *recent, m.Audit.data["network"].as_string() == self.network)) or 0 if self.network else 0
        if spent >= self.demo_total or mine >= self.per_network:
            raise AILimitReached("the demo's daily AI limit is reached")
        super().admit()  # this sandbox's own cap for its kind; records the call
        with self.repo.tx() as s:
            s.add(m.Audit(workspace_id=self.repo.workspace_id, actor="system", action=AI_CALL,
                          data={"task": self.task, "network": self.network}))


def sandbox_llm(inner, repo, settings, task: str, tuning: Tuning | None = None) -> SandboxLLM | None:
    """The sandbox's AI when this repository is a sandbox's, else None."""
    if not is_sandbox(repo, repo.workspace_id):
        return None
    from watchful.billing.limits import ASSIST, CHECK_TASKS, CHECKS, ai_used
    from watchful.tuning import load_tuning
    t = (tuning or load_tuning(settings.tuning_file)).limits
    kind = CHECKS if task in CHECK_TASKS else ASSIST
    cap = t.demo_ai_checks if kind == CHECKS else t.demo_ai_assist
    return SandboxLLM(inner, repo, cap, task, lambda: ai_used(repo, kind), t.demo_ai_total, t.demo_ai_per_network,
                      settings.ai_total_daily)


def ai_limit(repo, settings, tuning: Tuning | None = None) -> int | None:
    """A sandbox's AI checks a day (for its numbers page), or None for any other society."""
    if not is_sandbox(repo, repo.workspace_id):
        return None
    from watchful.tuning import load_tuning
    return (tuning or load_tuning(settings.tuning_file)).limits.demo_ai_checks


# a short timeline of what each demo camera saw, so a watch typed in a sandbox backtests to something

class _Rows:
    def __init__(self):
        self.rows: list[EventRow] = []

    def add_events(self, rows) -> None:
        self.rows += rows


class _NoBlobs:
    """Keyframes of the timeline are not kept: the seeded events carry none."""

    def put(self, key, data, content_type) -> None:
        pass

    def get(self, key):
        raise KeyError(key)

    def url(self, key) -> str:
        return ""

    def delete(self, key) -> None:
        pass


def _timeline_file(settings, cam: DemoCamera) -> Path:
    zones = json.dumps(cam.item.get("zones", []), sort_keys=True)
    tag = f"{cam.path}|{cam.path.stat().st_mtime}|{zones}|{settings.analysis_fps}|{settings.analysis_width}"
    return Path(settings.data_dir) / "demo" / f"timeline-{hashlib.sha256(tag.encode()).hexdigest()[:16]}.json"


def clip_timeline(settings, cam: DemoCamera, tuning: Tuning | None = None) -> list[dict]:
    """One pass of the clip through the camera pipeline at the analysis rate: its events (people and vehicles coming
    and going through the camera's zones), with the clip's own seconds. Saved under DATA_DIR/demo, so it is made
    once per clip and zones."""
    from watchful.components import make_detector
    from watchful.ingest.sources import FileSource
    from watchful.vision.perceive import Needs
    from watchful.vision.pipeline import CameraPipeline
    path = _timeline_file(settings, cam)
    if path.is_file():
        return json.loads(path.read_text())
    tuning = tuning or Tuning()
    camera = Camera("cam_timeline", cam.item["name"], "file", str(cam.path), zones_of(cam.item))
    sink = _Rows()
    pipe = CameraPipeline(camera, sink, _NoBlobs(), None, settings, tuning, Needs(classes=set(TIMELINE_CLASSES)),
                          detector=make_detector(settings, tuning))
    source = FileSource(cam.path, settings.analysis_fps, width=settings.analysis_width, start_epoch=0.0)
    last = 0.0
    while (frame := source.read()) is not None:
        pipe.process(frame)
        last = frame.ts
    pipe.finish(last)
    rows = [{"t": round(e.ts, 2), "kind": e.kind, "track_id": e.track_id, "cls": e.cls, "zone": e.zone,
             "data": {k: v for k, v in (e.data or {}).items() if isinstance(v, (int, float, str, bool, type(None)))}}
            for e in sink.rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows))
    tmp.replace(path)
    return rows


def warm_timelines(settings, tuning: Tuning | None = None) -> None:
    """Makes the timeline of every demo clip that has none yet (the app does this once, in the background)."""
    for cam in demo_cameras(settings):
        try:
            clip_timeline(settings, cam, tuning)
        except Exception:
            log.exception("the demo timeline of %s could not be made", cam.path)


def seeded_timeline(settings, cam: DemoCamera, camera_id: str, now: datetime) -> list[EventRow]:
    """The clip's timeline, made earlier, placed at demo.json's timeline_hours_ago (each pass's tracks numbered
    apart); nothing when it has not been made yet."""
    path = _timeline_file(settings, cam)
    if not path.is_file():
        return []
    rows = json.loads(path.read_text())
    out = []
    for n, hours in enumerate(cam.item.get("timeline_hours_ago", [])):
        start = (now - timedelta(hours=float(hours))).timestamp()
        for r in rows:
            track = r["track_id"] + (n + 1) * 1_000_000 if r["track_id"] is not None else None
            out.append(EventRow(None, camera_id, start + r["t"], r["kind"], track, r["cls"], r["zone"],
                                {**r["data"], "seeded": True}, None, None, None, None))
    return out


# the end of a sandbox, and the daily reset

def ended_sandboxes(db, now: datetime | None = None) -> list[str]:
    with db.tx() as s:
        return list(s.scalars(select(m.Workspace.id).where(m.Workspace.demo == SANDBOX,
                                                          m.Workspace.expires_at <= (now or m.utcnow()))
                              .order_by(m.Workspace.expires_at)))


def _blob_keys(s, workspace_id: str) -> list[str]:
    """Every picture a sandbox's rows name, for stores without delete_prefix and keys from before the society
    prefix."""
    keys = []
    named = ((m.Incident, ("picture_key", "keyframe_key", "clip_key")), (m.Alert, ("clip_key", "keyframe_key")),
             (m.Event, ("keyframe_key",)), (m.StateExample, ("picture_key",)))
    for model, cols in named:
        for name in cols:
            col = getattr(model, name)
            keys += list(s.scalars(select(col).where(model.workspace_id == workspace_id, col.is_not(None))))
    return list(dict.fromkeys(keys))


def _drop(db, blobs, workspace_id: str) -> dict:
    with db.tx() as s:
        keys = _blob_keys(s, workspace_id)
    removed = 0
    if hasattr(blobs, "delete_prefix"):
        removed += blobs.delete_prefix(f"{workspace_id}/")
    for key in keys:
        try:
            blobs.delete(key)
        except (OSError, KeyError):
            pass
    with db.tx() as s:
        visitors = list(s.scalars(select(m.User.id).join(m.Member, m.Member.user_id == m.User.id)
                                  .where(m.Member.workspace_id == workspace_id, m.User.demo.is_(True))))
        s.execute(delete(m.Job).where(m.Job.status != "running",
                                      m.Job.payload["workspace_id"].as_string() == workspace_id))
        s.execute(delete(m.Workspace).where(m.Workspace.id == workspace_id))  # cameras, device links, rows: cascade
        elsewhere = exists().where(m.Member.user_id == m.User.id)
        s.execute(delete(m.User).where(m.User.id.in_(visitors), m.User.demo.is_(True), ~elsewhere))
        s.add(m.Audit(workspace_id=workspace_id, actor="system", action="demo.reset",
                      data={"blobs": max(removed, len(keys)), "visitors": len(visitors)}))
    return {"blobs": removed, "keys": len(keys), "visitors": len(visitors)}


def reset(db, blobs, now: datetime | None = None, beat=lambda: None) -> list[str]:
    """Deletes every sandbox that ended, with its visitor, cameras, device links, rows and pictures; the ids deleted.
    `beat` is called after each sandbox (the job's extend_lease)."""
    gone = []
    for ws_id in ended_sandboxes(db, now):
        try:
            _drop(db, blobs, ws_id)
            gone.append(ws_id)
        except Exception:
            log.exception("deleting demo sandbox %s failed; it goes at the next reset", ws_id)
        beat()
    return gone


@handler("demo_reset", daily=True)
def demo_reset(ctx: JobContext, payload: dict) -> None:
    gone = reset(ctx.queue, ctx.worker.blobs, ctx.now, beat=ctx.extend_lease)
    if gone:
        log.info("demo sandboxes deleted: %s", len(gone))


def main(argv: list[str] | None = None, settings=None) -> None:
    from watchful.components import make_blobs, make_db
    from watchful.config import get_settings

    args = argparse.ArgumentParser(prog="python -m watchful.demo")
    sub = args.add_subparsers(dest="command", required=True)
    sub.add_parser("cameras", help="which demo cameras new sandboxes get, and which clips are missing")
    sub.add_parser("reset", help="delete demo sandboxes that ended, now")
    opts = args.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = settings or get_settings()
    if opts.command == "cameras":
        for cam in demo_cameras(settings):
            print(f"{cam.item['name']}: {cam.path} ({cam.item.get('source', '')})")
        for item in missing_cameras(settings):
            print(f"{item['name']}: left out, no clip at samples/{item['clip']}")
        return
    print(f"deleted {len(reset(make_db(settings), make_blobs(settings)))} demo sandboxes")


if __name__ == "__main__":
    main()

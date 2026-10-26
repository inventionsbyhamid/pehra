"""The judges' demo (sandbox lifecycle, caps and reset from packsure e97a509). "Try the demo" (api/demo.py) gives each
visitor a sandbox society of their own for Tuning limits.demo_hours: the demo cameras of data/demo.json whose clip
files exist, looping as if live, with their watches, a day of incidents (pictures cut from their own clips) and log
rows; the visitor can add this laptop or a phone as a camera and type watches of their own. A visitor is a user
without an email, flagged demo, and the owner of their sandbox. Nobody else ever sees a sandbox.

A sandbox is comped the Society plan until it ends, for Tuning limits.demo_cameras cameras of the visitor's own (the
demo's clips are not watched cameras, billing/limits.py), and the plan sweep leaves sandboxes alone. Its AI calls
have a cap of their own (SandboxLLM). When it ends its cameras stop (api/demo.py SandboxWatch), every request in it is
refused (demo_rules.py), and the daily `demo_reset` job deletes it with its visitor, device links and pictures.

A demo camera with no clip file is left out, never shown with another clip: today only OpenCV's sample vtest.avi
(BSD) is known to be safe to show, so Plaza runs on it, and Pool and the gates appear when the owner's consented
recordings land in samples/demo/.

    python -m watchful.demo cameras    which demo cameras new sandboxes get, and which clips are missing
    python -m watchful.demo reset      delete sandboxes that ended, now"""
import argparse
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


def start_sandbox(ctx, now: datetime | None = None) -> tuple[m.User, m.Workspace]:
    """A new demo visitor and their sandbox (`ctx`: the app's context), made in one transaction: the society, comped
    until it ends, with its default site watches. Its demo cameras and their day are seeded next (seed), then the app
    starts the cameras."""
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
        s.add(m.Audit(workspace_id=ws.id, actor=user.id, action="demo.start", data={"expires_at": ends.isoformat()}))
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
        if events:
            repo.add_events(events)
    with repo.tx() as s:
        seed_gate_log_and_register(s, repo.workspace_id, out, spec(), now, blobs)
    return out


def _seed_incident(repo, blobs, settings, cam: DemoCamera, camera_id: str, rows: list[WatchRow], watches: list[Watch],
                   item: dict, n: int, now: datetime) -> None:
    watch, row = watches[item["watch"]], rows[item["watch"]]
    opened = now - timedelta(hours=float(item["hours_ago"]))
    last = opened + timedelta(minutes=2 * max(0, int(item.get("count", 1)) - 1))
    key = box = None
    got = incident_picture(settings, cam.path, item.get("clip_s", 0), watch, cam.item.get("zones", []))
    if got is not None:
        data, box = got
        key = society_key(repo.workspace_id, "incidents", f"demo_{cam.key}_{n}.jpg")
        blobs.put(key, data, "image/jpeg")
    track = box is not None
    status = item.get("status", inc_.OPEN)
    agent = {"verdict": "alert", "why": item.get("why", ""), "cls": "person" if track else None, "seeded": True}
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
    watches, questions, search), and every sandbox together within AI_DAILY_CALL_LIMIT in the last day, counted from
    Audit rows the demo reset keeps."""

    def __init__(self, inner, repo, limit: int, task: str, used, total: int):
        super().__init__(inner, repo, limit, task, used=used)
        self.total = total

    def admit(self) -> None:
        since = m.utcnow() - timedelta(days=1)
        with self.repo.tx() as s:
            spent = s.scalar(select(func.count()).select_from(m.Audit)
                             .where(m.Audit.action == AI_CALL, m.Audit.ts >= since)) or 0
        if spent >= self.total:
            raise AILimitReached("the demo's daily AI limit is reached")
        super().admit()  # this sandbox's own cap for its kind; records the call
        with self.repo.tx() as s:
            s.add(m.Audit(workspace_id=self.repo.workspace_id, actor="system", action=AI_CALL,
                          data={"task": self.task}))


def sandbox_llm(inner, repo, settings, task: str, tuning: Tuning | None = None) -> SandboxLLM | None:
    """The sandbox's AI when this repository is a sandbox's, else None."""
    if not is_sandbox(repo, repo.workspace_id):
        return None
    from watchful.billing.limits import ASSIST, CHECK_TASKS, CHECKS, ai_used
    from watchful.tuning import load_tuning
    t = (tuning or load_tuning(settings.tuning_file)).limits
    kind = CHECKS if task in CHECK_TASKS else ASSIST
    cap = t.demo_ai_checks if kind == CHECKS else t.demo_ai_assist
    return SandboxLLM(inner, repo, min(cap, settings.ai_daily_call_limit), task, lambda: ai_used(repo, kind),
                      settings.ai_daily_call_limit)


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

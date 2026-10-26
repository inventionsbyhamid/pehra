"""The gate log. A vehicle crossing a line on a camera with plate reading on becomes a passage (in or out, by the
line's direction) with its plate as read; nothing here knows about gates. An out pairs with the latest unpaired in of
the same plate; an unread plate (text None, whatever its confidence) never pairs.

A vehicle that came in and has not gone out is "inside": the inside_s signal (rules/signals.py) gives its seconds, so
a watch such as "a cab stays more than 2 hours" (attribute plate_colour is yellow, inside_s at least 7200) works on
any society with plate reading, through the generic attribute signal. Those watches, and the built in overstay for
commercial plates (Tuning ledger.overstay_s), are checked here every minute by the job worker, and open an incident
through alerts/incidents.py (or write a log row), once per passage and watch."""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.orm import aliased

from watchful.db import models as m
from watchful.jobs import handler, periodic
from watchful.rules.signals import REGISTRY, SignalContext
from watchful.rules.watch import Condition, Subject, Watch
from watchful.store.db import EventRow
from watchful.tuning import LedgerTuning, Tuning
from watchful.vision.reason import Tick, TrackState

log = logging.getLogger("watchful.ledger")

OVERSTAY = "overstay"  # the incidents' watch_id of the built in overstay
STAY_SIGNAL = "inside_s"
STAY_SIGNALS = {STAY_SIGNAL, "attribute", "present"}  # what a stay watch may use: the gate log knows nothing else
RETENTION = "logs.retention"
VEHICLE_WORDS = {"car": "car", "motorcycle": "two wheeler", "bus": "bus", "truck": "truck"}


@dataclass(frozen=True)
class Plate:
    text: str | None
    confidence: float = 0.0
    colour: str = "unknown"
    reads: tuple = ()


def as_plate(plate) -> Plate:
    """A PlateRead, the dict a camera process sends, or None."""
    if plate is None:
        return Plate(None)
    if isinstance(plate, dict):
        return Plate(plate.get("text"), float(plate.get("confidence") or 0.0), plate.get("colour") or "unknown",
                     tuple(plate.get("reads") or ()))
    return Plate(plate.text, float(plate.confidence), plate.colour, tuple(getattr(plate, "reads", ()) or ()))


def _when(ts) -> datetime:
    return ts if isinstance(ts, datetime) else datetime.fromtimestamp(float(ts), ZoneInfo("UTC"))


def _mine(repo):
    return m.VehiclePassage.workspace_id == repo.workspace_id


def record_passage(repo, camera_id: str, crossing_event: EventRow, plate, picture_key: str | None = None,
                   tuning: LedgerTuning | None = None) -> m.VehiclePassage:
    """A crossing (an EventRow of kind cross from a camera of this society) and its plate become a passage, paired
    when it is an out. The same plate the same way through the same camera within ledger.dedupe_s is the passage
    already logged."""
    cfg, e, p = tuning or LedgerTuning(), crossing_event, as_plate(plate)
    if e.camera_id != camera_id:
        raise ValueError("the crossing is from another camera")
    direction = (e.data or {}).get("direction")
    if direction not in ("in", "out"):
        raise ValueError("a passage goes in or out")
    at = _when(e.ts)
    with repo.tx() as s:
        if p.text is not None:
            same = s.scalar(select(m.VehiclePassage).where(
                _mine(repo), m.VehiclePassage.camera_id == camera_id, m.VehiclePassage.direction == direction,
                m.VehiclePassage.plate_text == p.text, m.VehiclePassage.at >= at - timedelta(seconds=cfg.dedupe_s),
                m.VehiclePassage.at <= at + timedelta(seconds=cfg.dedupe_s)).limit(1))
            if same is not None:
                return same
        row = m.VehiclePassage(workspace_id=repo.workspace_id, camera_id=camera_id, at=at, direction=direction,
                               cls=e.cls, plate_text=p.text, plate_conf=round(p.confidence, 3),
                               plate_colour=p.colour, picture_key=picture_key, line=e.zone, track_id=e.track_id,
                               reads_json=[r for r in p.reads], alerted_json=[])
        s.add(row)
        s.flush()
        if direction == "out":
            _pair(s, repo.workspace_id, row, cfg)
        return row


def _pair(s, workspace_id: str, out: m.VehiclePassage, cfg: LedgerTuning) -> m.VehiclePassage | None:
    if out.plate_text is None or out.paired_id is not None:
        return None
    came = s.scalar(select(m.VehiclePassage).where(
        m.VehiclePassage.workspace_id == workspace_id, m.VehiclePassage.direction == "in",
        m.VehiclePassage.plate_text == out.plate_text, m.VehiclePassage.paired_id.is_(None),
        m.VehiclePassage.at <= out.at, m.VehiclePassage.at >= out.at - timedelta(seconds=cfg.pair_window_s))
        .order_by(m.VehiclePassage.at.desc()).limit(1))
    if came is None:
        return None
    came.paired_id, out.paired_id = out.id, came.id
    return came


def pair(repo, passage_id: str, tuning: LedgerTuning | None = None) -> m.VehiclePassage | None:
    """Pairs an out with the latest unpaired in of its plate, if any; returns that in."""
    with repo.tx() as s:
        row = s.scalar(select(m.VehiclePassage).where(_mine(repo), m.VehiclePassage.id == passage_id))
        if row is None or row.direction != "out":
            return None
        return _pair(s, repo.workspace_id, row, tuning or LedgerTuning())


def open_stays(repo, now: datetime, tuning: LedgerTuning | None = None) -> list[m.VehiclePassage]:
    """Ins with a read plate, not paired, with no later passage of that plate (it went out unseen, or came in
    again), within ledger.pair_window_s: the vehicles inside now."""
    cfg = tuning or LedgerTuning()
    later = aliased(m.VehiclePassage)
    newer = exists().where(later.workspace_id == m.VehiclePassage.workspace_id,
                           later.plate_text == m.VehiclePassage.plate_text, later.at > m.VehiclePassage.at)
    with repo.tx() as s:
        return list(s.scalars(select(m.VehiclePassage).where(
            _mine(repo), m.VehiclePassage.direction == "in", m.VehiclePassage.plate_text.is_not(None),
            m.VehiclePassage.paired_id.is_(None), m.VehiclePassage.at <= now,
            m.VehiclePassage.at >= now - timedelta(seconds=cfg.pair_window_s), ~newer)
            .order_by(m.VehiclePassage.at)))


# stays: the built in overstay and watches using inside_s, checked here rather than on a camera

def overstay_watch(tuning: Tuning) -> Watch | None:
    cfg = tuning.ledger
    if cfg.overstay_s <= 0:
        return None
    return Watch(name="A commercial vehicle stays too long", subject=Subject(classes=list(cfg.classes)),
                 conditions=[Condition(signal="attribute", params={"name": "plate_colour"}, op="in",
                                       value=list(cfg.overstay_colours)),
                             Condition(signal=STAY_SIGNAL, op=">=", value=cfg.overstay_s)],
                 severity=cfg.overstay_severity, verify="")


def is_stay_watch(w: Watch) -> bool:
    """A watch about how long something has been inside: it runs here, on the gate log, not on a camera."""
    return any(c.signal == STAY_SIGNAL for c in w.conditions)


def stay_watches(repo, tuning: Tuning) -> list[tuple[str, Watch, str | None]]:
    """(watch id, watch, home camera) of the built in overstay and the society's enabled stay watches."""
    out = []
    built_in = overstay_watch(tuning)
    if built_in is not None:
        out.append((OVERSTAY, built_in, None))
    for row in repo.list_watches():
        if not row.enabled:
            continue
        try:
            w = Watch.model_validate(row.watch)
        except ValueError:
            continue
        if is_stay_watch(w):
            out.append((row.id, w, row.camera_id))
    return out


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    parts = ([f"{hours} hour{'s' * (hours != 1)}"] if hours else []) + \
        ([f"{minutes} minute{'s' * (minutes != 1)}"] if minutes or not hours else [])
    return " ".join(parts)


def _clock(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%I:%M %p").lstrip("0")


def _holds(w: Watch, p: m.VehiclePassage, seconds: float, now: datetime) -> tuple[bool, list[str]]:
    """Whether the watch holds for this vehicle inside, `seconds` after it came in."""
    if w.subject.classes and p.cls not in w.subject.classes:
        return False, []
    if w.subject.who != "anyone" or w.zones or any(c.signal not in STAY_SIGNALS for c in w.conditions):
        return False, []
    if w.expires_at and now.timestamp() > w.expires_at:
        return False, []
    attributes = {"plate_text": p.plate_text, STAY_SIGNAL: f"{seconds:.0f}"}
    if p.plate_colour and p.plate_colour != "unknown":
        attributes["plate_colour"] = p.plate_colour
    track = TrackState(0, p.cls or "car", (0.0, 0.0, 0.0, 0.0), p.at.timestamp(), now.timestamp(),
                       attributes=attributes)
    tick = Tick(now.timestamp(), (0, 0), False, [track], [])
    ctx = SignalContext(p.camera_id, {}, tick, Tuning().signals, {}, [], [track])
    from watchful.rules.engine import RuleEngine, compare
    values = []
    for c in w.conditions:
        v = REGISTRY[c.signal].fn(ctx, c, track)
        values.append((c, v, compare(v.value, c.op, c.value)))
    r = RuleEngine._combine(w, values)
    return r.passed, r.reasons


def check_stays(repo, now: datetime | None = None, tuning: Tuning | None = None) -> list[tuple[str, str]]:
    """Fires each stay watch (and the built in overstay) once for each vehicle inside it holds for: an incident for an
    alert watch, a log row for a log watch. Returns (passage id, watch id) of what fired."""
    t = tuning or Tuning()
    now = now or m.utcnow()
    watches = stay_watches(repo, t)
    if not watches:
        return []
    stays = open_stays(repo, now, t.ledger)
    if not stays:
        return []
    from watchful.alerts import incidents as incidents_
    with repo.tx() as s:
        society = s.get(m.Workspace, repo.workspace_id)
        names = dict(s.execute(select(m.Camera.id, m.Camera.name)
                               .where(m.Camera.workspace_id == repo.workspace_id)).all())
    tz = ZoneInfo(society.timezone if society else "Asia/Kolkata")
    fired = []
    for p in stays:
        seconds = (now - p.at).total_seconds()
        for wid, w, home in watches:
            if wid in (p.alerted_json or []) or not w.applies_to(p.camera_id, home or p.camera_id):
                continue
            if w.time_window and not w.time_window.contains(now.timestamp(), tz.key):
                continue
            ok, _ = _holds(w, p, seconds, now)
            if not ok:
                continue
            reason = (f"{p.plate_text} came in at {_clock(p.at, tz)} through {names.get(p.camera_id, 'a gate')} and "
                      f"has not gone out after {_duration(seconds)}.")
            if w.output == "log":
                repo.add_events([EventRow(None, p.camera_id, now.timestamp(), "watch_log", p.track_id, p.cls, None,
                                          {"watch_id": wid, "watch": w.name, "reasons": [reason],
                                           "signals": {STAY_SIGNAL: round(seconds)}, "passage_id": p.id},
                                          None, None, None, None)])
            else:
                incidents_.open_or_touch_incident(repo, p.camera_id, wid, w.severity, reason, p.picture_key, now,
                                                  tuning=t.alerts, fingerprint=f"stay|{p.id}|{wid}",
                                                  extra={"cls": p.cls, "passage_id": p.id})
            with repo.tx() as s:
                row = s.scalar(select(m.VehiclePassage).where(_mine(repo), m.VehiclePassage.id == p.id))
                if row is not None:
                    row.alerted_json = [*(row.alerted_json or []), wid]
            fired.append((p.id, wid))
    return fired


@periodic
def check_all_stays(worker, now: datetime) -> list[tuple[str, str]]:
    """Every Tuning jobs.periodic_s in the job worker: each society with a vehicle inside."""
    cfg = worker.tuning.ledger
    with worker.queue.tx() as s:
        societies = list(s.scalars(select(m.VehiclePassage.workspace_id).where(
            m.VehiclePassage.direction == "in", m.VehiclePassage.paired_id.is_(None),
            m.VehiclePassage.plate_text.is_not(None),
            m.VehiclePassage.at >= now - timedelta(seconds=cfg.pair_window_s)).distinct()))
    fired = []
    for ws in societies:
        try:
            fired += check_stays(worker.repo(ws), now, worker.tuning)
        except Exception:
            log.exception("checking vehicles inside %s failed", ws)
    return fired


# reading the log

def day_bounds(day: date | str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    day = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(day, time(0), tz)
    return start, start + timedelta(days=1)


def society_zone(repo) -> ZoneInfo:
    with repo.tx() as s:
        society = s.get(m.Workspace, repo.workspace_id)
        return ZoneInfo(society.timezone if society and society.timezone else "Asia/Kolkata")


def normalise_query(q: str | None) -> str:
    return "".join(ch for ch in (q or "").upper() if ch.isalnum())


def passages(repo, day: date | str, q: str | None = None, camera_id: str | None = None,
             limit: int = 2000) -> list[dict]:
    """One day of the gate log (the society's calendar day), newest first, each with its pair."""
    tz = society_zone(repo)
    start, end = day_bounds(day, tz)
    want = normalise_query(q)
    other = aliased(m.VehiclePassage)
    stmt = (select(m.VehiclePassage, other.at, m.Camera.name)
            .join(other, and_(other.id == m.VehiclePassage.paired_id, other.workspace_id == repo.workspace_id),
                  isouter=True)
            .join(m.Camera, and_(m.Camera.id == m.VehiclePassage.camera_id,
                                 m.Camera.workspace_id == repo.workspace_id), isouter=True)
            .where(_mine(repo), m.VehiclePassage.at >= start, m.VehiclePassage.at < end))
    if want:
        stmt = stmt.where(m.VehiclePassage.plate_text.contains(want))
    if camera_id:
        stmt = stmt.where(m.VehiclePassage.camera_id == camera_id)
    with repo.tx() as s:
        rows = s.execute(stmt.order_by(m.VehiclePassage.at.desc()).limit(limit)).all()
    out = []
    for p, paired_at, camera in rows:
        stayed = None
        if paired_at is not None:
            stayed = abs((p.at - paired_at).total_seconds())
        out.append({"id": p.id, "at": p.at.astimezone(tz).isoformat(), "direction": p.direction,
                    "vehicle": VEHICLE_WORDS.get(p.cls or "", p.cls), "cls": p.cls, "plate": p.plate_text,
                    "plate_conf": p.plate_conf, "plate_colour": p.plate_colour or "unknown",
                    "camera_id": p.camera_id, "camera": camera, "line": p.line, "picture_key": p.picture_key,
                    "paired_id": p.paired_id,
                    "paired_at": paired_at.astimezone(tz).isoformat() if paired_at is not None else None,
                    "stayed_s": round(stayed) if stayed is not None else None,
                    "alerted": list(p.alerted_json or [])})
    return out


def delete_before(repo, before: datetime) -> list[str]:
    """Passages before `before` go; returns their picture keys for the caller to delete."""
    with repo.tx() as s:
        old = [_mine(repo), m.VehiclePassage.at < before]
        keys = [k for k in s.scalars(select(m.VehiclePassage.picture_key).where(*old)) if k]
        s.execute(delete(m.VehiclePassage).where(*old))
        return keys


def counts(repo, start: datetime, end: datetime) -> dict[str, int]:
    with repo.tx() as s:
        return dict(s.execute(select(m.VehiclePassage.direction, func.count()).where(
            _mine(repo), m.VehiclePassage.at >= start, m.VehiclePassage.at < end)
            .group_by(m.VehiclePassage.direction)).all())


@handler(RETENTION, daily=True)
def _retention(ctx, payload: dict) -> None:
    """Passages and attendance go after each society's retention (billing.retention_days), with passage pictures;
    enrolment photos kept by earlier versions go now (only face templates are kept)."""
    from watchful.billing import retention_days
    from watchful.logs import register
    with ctx.queue.tx() as s:
        societies = list(s.scalars(select(m.Workspace)))
    for ws in societies:
        repo = ctx.repo(ws.id)
        before = ctx.now - timedelta(days=retention_days(ws, ctx.now))
        for key in delete_before(repo, before) + register.delete_before(repo, before) + register.drop_photos(repo):
            try:
                ctx.worker.blobs.delete(key)
            except (OSError, KeyError):
                pass


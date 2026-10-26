"""The staff register: consenting adult staff (maids, cooks, drivers, guards) enrolled from 3 to 5 photos, of which
only the face templates are kept, and their first in and last out each day, in the society's time zone.

Attendance comes from named sightings: every event of a track named by face (or by clothes after a face, the same
day) carries person_id (vision/reason.py); the app hands each batch of events here (live updates), and a daily job
rolls the day up again from the stored events (in society time, Tuning staff.rollup_hour), so a missed update is
filled in. Nobody's age is ever estimated: the form and the consent words say adults only.

Removing someone deletes their templates and keeps them, inactive, with their attendance history; every camera process
forgets them at once (workers.WorkerManager.reload_faces) and at the latest within Tuning faces.reload_s."""
import json
import logging
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update

from watchful.brand import BRAND
from watchful.db import models as m
from watchful.jobs import handler, periodic
from watchful.store.sql import upsert

log = logging.getLogger("watchful.register")

DATA = Path(__file__).resolve().parents[1] / "data" / "staff.json"
ROLL_UP = "register.roll_up"


@lru_cache
def words() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def consent_text(name: str = "This person") -> str:
    return words()["consent_text"].format(name=name.strip() or "This person", brand=BRAND.name)


class Refused(Exception):
    """Plain words for why an enrolment can't go ahead; over_limit when the plan names no more staff."""

    def __init__(self, words: str, over_limit: bool = False):
        super().__init__(words)
        self.over_limit = over_limit


def _mine(repo, model):
    return model.workspace_id == repo.workspace_id


def active_count(repo) -> int:
    with repo.tx() as s:
        return s.scalar(select(func.count()).select_from(m.Staff).where(_mine(repo, m.Staff),
                                                                         m.Staff.active.is_(True))) or 0


def check_photos(features: list[list[float]], similarity, cfg) -> int | None:
    """The 1 based number of a photo that does not look like the others (each must be at faces.enrol_same or more
    to the rest on average), or None."""
    for i, f in enumerate(features):
        others = [similarity(f, g) for j, g in enumerate(features) if j != i]
        if others and sum(others) / len(others) < cfg.enrol_same:
            return i + 1
    return None


def enrol(repo, name: str, role: str, features: list[list[float]], by: str | None, now: datetime | None = None,
          staff_id: str | None = None, most: int | None = None, refusal: str = "") -> m.Staff:
    """A new staff member with one face template per photo, consent recorded with its words. Photos are not passed
    in: the caller drops them once the templates are made. With `most` (the plan's enrolled staff), counting and
    adding are one transaction, so two enrolments at once can't pass the limit."""
    name, role = (name or "").strip(), (role or "").strip().lower()
    if not name:
        raise Refused("Give their name.")
    if role not in words()["roles"]:
        raise Refused("Pick one of the listed roles.")
    if not features:
        raise Refused("No face found in those photos.")
    now = now or m.utcnow()
    with repo.tx() as s:
        if most is not None:
            s.execute(update(m.Workspace).where(m.Workspace.id == repo.workspace_id).values(plan=m.Workspace.plan))
            n = s.scalar(select(func.count()).select_from(m.Staff).where(_mine(repo, m.Staff),
                                                                          m.Staff.active.is_(True))) or 0
            if n >= most:
                raise Refused(refusal or "Your plan names no more staff.", over_limit=True)
        staff = m.Staff(id=staff_id, workspace_id=repo.workspace_id, name=name, role=role, consent_at=now,
                        consent_by=by, consent_text=consent_text(name), active=True, created_at=now)
        s.add(staff)
        s.flush()
        for f in features:
            s.add(m.StaffFace(workspace_id=repo.workspace_id, staff_id=staff.id, embedding=m.pack_vec(f),
                              picture_key=None, created_at=now))
        return staff


def remove(repo, staff_id: str, by: str | None, now: datetime | None = None) -> tuple[m.Staff | None, list[str]]:
    """Deletes the staff member's face templates and keeps them inactive with their attendance. Returns the row and
    the keys of any old enrolment photos (from before photos were dropped) for the caller to delete."""
    with repo.tx() as s:
        staff = s.scalar(select(m.Staff).where(_mine(repo, m.Staff), m.Staff.id == staff_id))
        if staff is None:
            return None, []
        keys = [k for k in s.scalars(select(m.StaffFace.picture_key).where(
            _mine(repo, m.StaffFace), m.StaffFace.staff_id == staff_id)) if k]
        s.execute(delete(m.StaffFace).where(_mine(repo, m.StaffFace), m.StaffFace.staff_id == staff_id))
        if staff.active:
            staff.active, staff.removed_at, staff.removed_by = False, now or m.utcnow(), by
        return staff, keys


def staff_list(repo, include_removed: bool = False) -> list[dict]:
    with repo.tx() as s:
        stmt = select(m.Staff).where(_mine(repo, m.Staff))
        if not include_removed:
            stmt = stmt.where(m.Staff.active.is_(True))
        rows = list(s.scalars(stmt.order_by(m.Staff.name, m.Staff.id)))
        faces = {}
        for sid in s.scalars(select(m.StaffFace.staff_id).where(_mine(repo, m.StaffFace))):
            faces[sid] = faces.get(sid, 0) + 1
    return [{"id": r.id, "name": r.name, "role": r.role, "active": r.active, "faces": faces.get(r.id, 0),
             "consent_at": r.consent_at, "consent_text": r.consent_text, "created_at": r.created_at,
             "removed_at": r.removed_at} for r in rows]


def _zone(s, workspace_id: str) -> ZoneInfo:
    society = s.get(m.Workspace, workspace_id)
    return ZoneInfo(society.timezone if society and society.timezone else "Asia/Kolkata")


def _when(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ZoneInfo("UTC"))


def sightings(events, tz: ZoneInfo, active: set[str]) -> dict[tuple[str, str], list]:
    """(staff id, day) to [first seen, last seen, cameras] from events that name an active staff member."""
    out: dict[tuple[str, str], list] = {}
    for e in events:
        pid = (e.data or {}).get("person_id")
        if not pid or pid not in active:
            continue
        start = e.ts - float((e.data or {}).get("duration_s") or 0) if e.kind == "track_end" else e.ts
        for ts in {start, e.ts}:
            day = datetime.fromtimestamp(ts, tz).date().isoformat()
            got = out.setdefault((pid, day), [ts, ts, set()])
            got[0], got[1] = min(got[0], ts), max(got[1], ts)
            got[2].add(e.camera_id)
    return out


def _merge(s, workspace_id: str, seen: dict[tuple[str, str], list]) -> int:
    n = 0
    for (pid, day), (first, last, cams) in seen.items():
        row = s.scalar(select(m.Attendance).where(m.Attendance.workspace_id == workspace_id,
                                                  m.Attendance.staff_id == pid, m.Attendance.day == day))
        if row is None:
            upsert(s, m.Attendance, {"id": m.new_id("att"), "workspace_id": workspace_id, "staff_id": pid,
                                     "day": day, "first_in": _when(first), "last_out": _when(last),
                                     "camera_ids_json": sorted(cams)}, ["workspace_id", "staff_id", "day"])
            row = s.scalar(select(m.Attendance).where(m.Attendance.workspace_id == workspace_id,
                                                      m.Attendance.staff_id == pid, m.Attendance.day == day))
        row.first_in = min(x for x in (row.first_in, _when(first)) if x is not None)
        row.last_out = max(x for x in (row.last_out, _when(last)) if x is not None)
        row.camera_ids_json = sorted(set(row.camera_ids_json or []) | cams)
        n += 1
    return n


def on_events(repo, rows) -> int:
    """Live: events from a camera of this society update the day's first in and last out of whoever they name."""
    named = [e for e in rows if (e.data or {}).get("person_id")]
    if not named:
        return 0
    with repo.tx() as s:
        tz = _zone(s, repo.workspace_id)
        active = set(s.scalars(select(m.Staff.id).where(_mine(repo, m.Staff), m.Staff.active.is_(True),
                                                        m.Staff.id.in_({e.data["person_id"] for e in named}))))
        return _merge(s, repo.workspace_id, sightings(named, tz, active))


def roll_up(repo, day: date | str) -> int:
    """The day's attendance worked out again from the stored events that name someone, merged into the live rows
    (earliest in, latest out); safe to repeat. Returns the rows touched."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    with repo.tx() as s:
        tz = _zone(s, repo.workspace_id)
        active = set(s.scalars(select(m.Staff.id).where(_mine(repo, m.Staff), m.Staff.active.is_(True))))
    start = datetime.combine(day, time(0), tz)
    end = start + timedelta(days=1)
    with repo.tx() as s:
        rows = list(s.scalars(select(m.Event).where(
            _mine(repo, m.Event), m.Event.ts >= start.timestamp() - 86400, m.Event.ts < end.timestamp(),
            m.Event.data["person_id"].as_string().is_not(None))))
    seen = {k: v for k, v in sightings(rows, tz, active).items() if k[1] == day.isoformat()}
    with repo.tx() as s:
        return _merge(s, repo.workspace_id, seen)


def attendance(repo, day: date | str) -> list[dict]:
    """The register for one day: every active staff member (present or not) and anyone removed since who was in."""
    day = day.isoformat() if isinstance(day, date) else day
    with repo.tx() as s:
        tz = _zone(s, repo.workspace_id)
        rows = {a.staff_id: a for a in s.scalars(select(m.Attendance).where(_mine(repo, m.Attendance),
                                                                           m.Attendance.day == day))}
        staff = list(s.scalars(select(m.Staff).where(_mine(repo, m.Staff)).order_by(m.Staff.name, m.Staff.id)))
        cameras = dict(s.execute(select(m.Camera.id, m.Camera.name).where(_mine(repo, m.Camera))).all())
    out = []
    for st in staff:
        a = rows.get(st.id)
        if not st.active and a is None:
            continue
        out.append({"staff_id": st.id, "name": st.name, "role": st.role, "active": st.active,
                    "present": a is not None and a.first_in is not None,
                    "first_in": a.first_in.astimezone(tz).isoformat() if a and a.first_in else None,
                    "last_out": a.last_out.astimezone(tz).isoformat() if a and a.last_out else None,
                    "cameras": [cameras.get(c, "a removed camera") for c in (a.camera_ids_json if a else [])]})
    return out


def delete_before(repo, before: datetime) -> list[str]:
    """Attendance of days before `before` (in the society's time zone) goes; no pictures to return."""
    with repo.tx() as s:
        day = before.astimezone(_zone(s, repo.workspace_id)).date().isoformat()
        s.execute(delete(m.Attendance).where(_mine(repo, m.Attendance), m.Attendance.day < day))
    return []


def drop_photos(repo) -> list[str]:
    """Enrolment photos kept by earlier versions: their keys, unlinked from the templates, for the caller to
    delete (only templates are kept)."""
    with repo.tx() as s:
        keys = [k for k in s.scalars(select(m.StaffFace.picture_key).where(
            _mine(repo, m.StaffFace), m.StaffFace.picture_key.is_not(None))) if k]
        if keys:
            s.execute(update(m.StaffFace).where(_mine(repo, m.StaffFace)).values(picture_key=None))
        return keys


@periodic
def queue_roll_ups(worker, now: datetime) -> list[str]:
    """Queues yesterday's roll up for each society with staff, once, from rollup_hour society time until
    rollup_late_h after it."""
    t = worker.tuning.staff
    with worker.queue.tx() as s:
        societies = s.execute(select(m.Workspace.id, m.Workspace.timezone)
                              .join(m.Staff, m.Staff.workspace_id == m.Workspace.id).distinct()).all()
    queued = []
    for ws, zone in societies:
        local = now.astimezone(ZoneInfo(zone or worker.settings.timezone))
        due = datetime.combine(local.date(), time(t.rollup_hour), local.tzinfo)
        if not due <= local < due + timedelta(hours=t.rollup_late_h):
            continue
        day = (local.date() - timedelta(days=1)).isoformat()
        if worker.queue.enqueue(ROLL_UP, {"workspace_id": ws, "day": day}, now, key=f"rollup|{ws}|{day}"):
            queued.append(ws)
    return queued


@handler(ROLL_UP)
def _roll_up(ctx, payload: dict) -> None:
    roll_up(ctx.repo(payload["workspace_id"]), payload["day"])

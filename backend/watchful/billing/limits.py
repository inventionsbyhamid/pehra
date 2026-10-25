"""What a society's plan allows right now: watched cameras, how long footage is kept, AI checks a day and enrolled
staff. The words for a refusal are made here only; the pages show the text the server sends.

A watched camera is a camera with watching switched on (`enabled`) that the society connected: a stream the server
reads or a device that sends its own pictures. Uploaded videos and the demo's replayed clips are not cameras.

`entitlement` reads the stored subscription with the clock: a past due plan whose grace has run out, or a plan
cancelled for the end of a period that has passed, is the default plan already, before the daily plan sweep writes
it. A plan the operator comps (billing/comp.py) is no subscription: it holds until its date, and the bigger of the
paid plan and the comp wins (a higher plan, then more cameras)."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update

from watchful.billing.plans import Plan, catalog
from watchful.billing.provider import GRACE, PAYING
from watchful.db import models as m

WATCHED_KINDS = ("rtsp", "push")  # camera rows a society connected (store/sql.py KIND_TO_ROW)
OFF_BY_PLAN = "plan"  # cameras.off_reason of a camera the plan switched off
CHECKS, ASSIST = "checks", "assist"  # the two AI limits: checks of flagged moments, and help (watches, questions)
CHECK_TASKS = ("verify",)  # AiCall tasks that are checks; one is recorded per check (agent/llm.py LimitedLLM.check)


@dataclass(frozen=True)
class Entitlement:
    plan: Plan
    cameras: int | None  # watched cameras allowed; None: as many as agreed
    source: str  # free, paid, comp or agreed (set by hand)
    exact: bool = True  # False when a paid plan's camera count is unknown: then no camera is switched off for it


def paying(ws: m.Workspace, now: datetime) -> bool:
    """Paying, and not past the end of a period it was cancelled for."""
    return ws.subscription_status in PAYING and not (ws.subscription_ends_at and now >= ws.subscription_ends_at)


def in_grace(ws: m.Workspace, now: datetime) -> bool:
    return ws.subscription_status in GRACE and ws.grace_until is not None and now < ws.grace_until


def subscription_live(ws: m.Workspace, now: datetime | None = None) -> bool:
    """A subscription still running, perhaps cancelled for the end of its period or with a failed payment being
    retried (even past our grace): changes go to it, as a second checkout would start a second subscription."""
    now = now or m.utcnow()
    return bool(ws.subscription_id) and (ws.subscription_status in GRACE or paying(ws, now))


def comped(ws: m.Workspace, now: datetime | None = None) -> bool:
    """A plan the operator gave (billing/comp.py) is running."""
    return bool(ws.comp_plan) and ws.comp_until is not None and (now or m.utcnow()) < ws.comp_until


def _cameras(plan: Plan, count: int | None) -> int | None:
    """`count` cameras on this plan, within what it sells; the plan's own number when it is not sold per camera."""
    most = plan.limits.watched_cameras
    if not plan.per_camera or count is None:
        return most
    return max(1, count) if most is None else min(max(1, count), most)


def paid_entitlement(ws: m.Workspace, now: datetime) -> Entitlement:
    """The plan the society's subscription (or a plan set by hand) gives, without any comp."""
    cat = catalog()
    free = cat.get(cat.default)
    if not ws.plan or ws.plan == cat.default or ws.plan not in cat.plans:
        return Entitlement(free, free.limits.watched_cameras, "free")
    plan = cat.get(ws.plan)
    if ws.subscription_status is None:  # set by hand: Large sites, agreed outside checkout
        return Entitlement(plan, _cameras(plan, ws.subscription_units), "agreed")
    if paying(ws, now) or in_grace(ws, now):
        if plan.per_camera and ws.subscription_units is None:  # never guessed: new cameras as on Free, none off
            return Entitlement(plan, free.limits.watched_cameras, "paid", exact=False)
        return Entitlement(plan, _cameras(plan, ws.subscription_units), "paid")
    return Entitlement(free, free.limits.watched_cameras, "free")


def _bigger(a: Entitlement, b: Entitlement) -> bool:
    rank = catalog().rank
    more = float("inf")
    return (rank(a.plan.id), more if a.cameras is None else a.cameras) > \
        (rank(b.plan.id), more if b.cameras is None else b.cameras)


def entitlement(ws: m.Workspace, now: datetime | None = None) -> Entitlement:
    """The plan in force and the cameras it watches."""
    now = now or m.utcnow()
    paid = paid_entitlement(ws, now)
    if comped(ws, now) and ws.comp_plan in catalog().plans:
        plan = catalog().get(ws.comp_plan)
        comp = Entitlement(plan, _cameras(plan, ws.comp_cameras), "comp")
        if _bigger(comp, paid):
            return comp
    return paid


def effective_plan(ws: m.Workspace, now: datetime | None = None) -> str:
    return entitlement(ws, now).plan.id


def retention_days(ws: m.Workspace, now: datetime | None = None) -> int:
    """How long this society's footage is kept. After a plan with a longer retention ends, footage saved under it is
    kept for that plan's days (the refund policy's promise), then the new plan's days apply."""
    now = now or m.utcnow()
    days = entitlement(ws, now).plan.limits.retention_days
    cat = catalog()
    ran_out = [(ws.comp_until, ws.comp_plan)]  # ended by the clock, perhaps before the plan sweep wrote it
    if ws.subscription_status and ws.plan in cat.plans:
        ran_out += [(ws.subscription_ends_at, ws.plan), (ws.grace_until, ws.plan)]
    for end, plan in ran_out:
        if end is not None and plan in cat.plans:
            kept = cat.get(plan).limits.retention_days
            if end <= now < end + timedelta(days=kept):
                days = max(days, kept)
    if ws.retention_hold_until is not None and now < ws.retention_hold_until and ws.retention_hold_days:
        days = max(days, ws.retention_hold_days)
    return days


def hold_retention(ws: m.Workspace, before: int, now: datetime) -> None:
    """Called after a stored plan changed: when footage was kept `before` days and is now kept fewer, what was saved
    until now keeps its `before` days."""
    if retention_days(ws, now) < before:
        until = now + timedelta(days=before)
        if not (ws.retention_hold_until and ws.retention_hold_until >= until):
            ws.retention_hold_days, ws.retention_hold_until = before, until


def ai_cap(ws: m.Workspace, kind: str, default: int, now: datetime | None = None) -> int:
    """AI requests of this kind the society may make today: CHECKS (one per flagged moment checked, per watched
    camera on a per camera plan) or ASSIST (writing watches, questions, search); `default` (AI_DAILY_CALL_LIMIT)
    for a plan agreed per customer."""
    ent = entitlement(ws, now)
    per = ent.plan.limits.ai_checks_day if kind == CHECKS else ent.plan.limits.ai_assist_day
    if per is None:
        return default
    if kind == CHECKS and ent.plan.per_camera:
        return per * ent.cameras if ent.cameras is not None else default
    return per


def ai_checks_day(ws: m.Workspace, default: int, now: datetime | None = None) -> int:
    return ai_cap(ws, CHECKS, default, now)


def _workspace(s, workspace) -> m.Workspace:
    ws = s.get(m.Workspace, getattr(workspace, "id", workspace))
    if ws is None:
        raise LookupError(f"no society {workspace}")
    return ws


def workspace_of(db, workspace_id: str) -> m.Workspace:
    with db.tx() as s:
        return _workspace(s, workspace_id)


def retention_days_of(repo, default: int, now: datetime | None = None) -> int:
    """retention_days for a society's repository (or anything with tx() and workspace_id); `default` when the society
    has no row (a bare test repository)."""
    try:
        return retention_days(workspace_of(repo, repo.workspace_id), now)
    except LookupError:
        return default


def daily_ai_cap(repo, default: int, kind: str = CHECKS) -> int:
    try:
        return ai_cap(workspace_of(repo, repo.workspace_id), kind, default)
    except LookupError:
        return default


def ai_used(repo, kind: str = CHECKS) -> int:
    """This kind's AI requests since midnight in the society's time zone."""
    task = m.AiCall.task.in_(CHECK_TASKS) if kind == CHECKS else m.AiCall.task.not_in(CHECK_TASKS)
    with repo.tx() as s:
        zone = s.scalar(select(m.Workspace.timezone).where(m.Workspace.id == repo.workspace_id)) or "UTC"
        midnight = m.utcnow().astimezone(ZoneInfo(zone)).replace(hour=0, minute=0, second=0, microsecond=0)
        return s.scalar(select(func.count()).select_from(m.AiCall).where(
            m.AiCall.workspace_id == repo.workspace_id, m.AiCall.ts >= midnight, task)) or 0


# watched cameras

def _watched(s, workspace_id: str, but: str | None = None) -> int:
    where = [m.Camera.workspace_id == workspace_id, m.Camera.enabled.is_(True), m.Camera.kind.in_(WATCHED_KINDS)]
    if but:
        where.append(m.Camera.id != but)
    return s.scalar(select(func.count()).select_from(m.Camera).where(*where)) or 0


def watched_cameras(db, workspace_id: str) -> int:
    with db.tx() as s:
        return _watched(s, workspace_id)


def _lock(s, workspace_id: str) -> m.Workspace:
    """The society's row, locked for this transaction: a write first, so SQLite takes its write lock before reading
    and PostgreSQL holds the row; two requests never both take the last camera."""
    s.execute(update(m.Workspace).where(m.Workspace.id == workspace_id).values(plan=m.Workspace.plan)
              .execution_options(synchronize_session=False))
    return _workspace(s, workspace_id)


def start_watching(db, workspace_id: str, camera_id: str, now: datetime | None = None) -> str | None:
    """Switches watching on for a camera the society connected, when its plan has room: None, else the words why
    not. The count and the switch are one transaction."""
    with db.tx() as s:
        ws = _lock(s, workspace_id)
        row = s.get(m.Camera, camera_id)
        if row is None or row.workspace_id != workspace_id:
            raise LookupError(f"no camera {camera_id}")
        ent = entitlement(ws, now)
        if row.kind in WATCHED_KINDS and ent.cameras is not None and _watched(s, workspace_id, camera_id) >= \
                ent.cameras:
            return refusal(ent)
        row.enabled, row.off_reason = True, None
        return None


def stop_watching(db, workspace_id: str, camera_id: str) -> None:
    """Switches a camera off again (a change that switched it on failed after start_watching)."""
    with db.tx() as s:
        s.execute(update(m.Camera).where(m.Camera.id == camera_id, m.Camera.workspace_id == workspace_id)
                  .values(enabled=False).execution_options(synchronize_session=False))


@dataclass(frozen=True)
class SwitchedOff:
    cameras: list[tuple[str, str]] = field(default_factory=list)  # (id, name), newest last
    allowed: int = 0


def over_allowance(db, workspace_id: str, now: datetime | None = None) -> SwitchedOff:
    """Switches off the newest watched cameras beyond what the plan watches (after it lapsed or was lowered), marked
    as switched off by the plan, with an Audit row; the app stops them and tells the society. Counted first, so a
    society within its plan takes no lock."""
    with db.tx() as s:
        ent = entitlement(_workspace(s, workspace_id), now)
        if ent.cameras is None or not ent.exact or _watched(s, workspace_id) <= ent.cameras:
            return SwitchedOff()
    with db.tx() as s:
        ws = _lock(s, workspace_id)
        ent = entitlement(ws, now)
        if ent.cameras is None or not ent.exact:
            return SwitchedOff()
        rows = list(s.scalars(select(m.Camera).where(m.Camera.workspace_id == workspace_id,
                                                     m.Camera.enabled.is_(True), m.Camera.kind.in_(WATCHED_KINDS))
                              .order_by(m.Camera.created_at, m.Camera.id)))
        off = rows[ent.cameras:]
        for row in off:
            row.enabled, row.off_reason = False, OFF_BY_PLAN
        if off:
            s.add(m.Audit(workspace_id=workspace_id, actor="system", action="billing.cameras_off",
                          data={"cameras": [r.id for r in off], "names": [r.name for r in off],
                                "plan": ent.plan.id, "allowed": ent.cameras}))
        return SwitchedOff([(r.id, r.name) for r in off], ent.cameras)


def off_by_plan(db, workspace_id: str) -> list[tuple[str, str]]:
    """The society's cameras the plan switched off, oldest first: (id, name)."""
    with db.tx() as s:
        return [(r.id, r.name) for r in s.scalars(
            select(m.Camera).where(m.Camera.workspace_id == workspace_id, m.Camera.enabled.is_(False),
                                   m.Camera.off_reason == OFF_BY_PLAN).order_by(m.Camera.created_at, m.Camera.id))]


def switched_off_words(name: str, allowed: int) -> str:
    """What the society is told about a camera the plan switched off. No dashes."""
    return f"{name} is no longer watched: your plan watches {_cameras_words(allowed)}."


def is_payer(ws: m.Workspace, user) -> bool:
    """Whether this user paid for the society's plan: the member who checked out, else the provider's customer
    email. Only they may open the billing portal, which shows every subscription that customer pays for."""
    if ws.billing_user_id:
        return ws.billing_user_id == user.id
    email = (getattr(user, "email", None) or "").strip().lower()
    return bool(email) and email == (ws.billing_email or "").strip().lower()


def _cameras_words(n: int) -> str:
    return "1 camera" if n == 1 else f"{n} cameras"


def refusal(ent: Entitlement) -> str:
    """Plain words for one camera more than the plan watches, with the way forward. No dashes."""
    head = f"Your plan watches {_cameras_words(ent.cameras)}."
    up = catalog().next_up(ent.plan.id)
    if ent.source == "free" and up is not None:
        return f"{head} Switch one off or move to {up.name}."
    if ent.source == "paid" and ent.plan.per_camera and ent.cameras < (ent.plan.limits.watched_cameras or 0):
        return f"{head} Switch one off or add a camera to your plan in Billing."
    if ent.plan.limits.watched_cameras is not None and ent.cameras >= ent.plan.limits.watched_cameras:
        large = next((p for p in catalog().plans.values() if p.contact), None)
        if large is not None:
            return f"{head} Switch one off, or email us about {large.name}."
    return f"{head} Switch one off, or email us to watch more."


def staff_allowed(ws: m.Workspace, now: datetime | None = None) -> int | None:
    return entitlement(ws, now).plan.limits.enrolled_staff


def staff_refusal(ws: m.Workspace, now: datetime | None = None) -> str:
    ent = entitlement(ws, now)
    n = ent.plan.limits.enrolled_staff
    who = "1 staff member" if n == 1 else f"{n} staff members"
    up = catalog().next_up(ent.plan.id)
    more = f"or move to {up.name}" if up is not None and (up.limits.enrolled_staff or 0) > n else \
        "or email us to name more"
    return f"Your plan names up to {who}. Remove someone who has left {more}."

"""The daily summary each linked Telegram group gets at 9 AM society time (Tuning alerts.summary_hour) about the day
before: alerts and false alarms, what is still open, cameras not live, vehicles through the gates and staff who came
in. The numbers come from the database; the sentences are fixed, so nothing here calls the AI."""
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from watchful.alerts import incidents as inc_
from watchful.alerts.telegram import TelegramError, _call, _limiter
from watchful.db import models as m
from watchful.jobs import Defer, JobContext, handler, periodic

log = logging.getLogger("watchful.summary")

SUMMARY = "summary.daily"
WORDS = {"critical": ("critical", "critical"), "warning": ("warning", "warnings"), "info": ("note", "notes")}


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _name(watch: m.Watch | None) -> str:
    if watch is None:
        return "A removed watch"
    name = (watch.spec_json or {}).get("name") or watch.text or "A watch"
    return name if len(name) <= 60 else name[:57].rstrip() + "..."


def daily(repo, day: date | str) -> str:
    """The summary of one day (the society's own calendar day) for the society `repo` is bound to."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    ws = repo.workspace_id
    with repo.tx() as s:
        society = s.get(m.Workspace, ws)
        tz = ZoneInfo(society.timezone if society else "Asia/Kolkata")
        start = datetime.combine(day, time(0), tz)
        end = start + timedelta(days=1)
        rows = list(s.execute(select(m.Incident.severity, m.Incident.status, m.Incident.watch_id).where(
            m.Incident.workspace_id == ws, m.Incident.opened_at >= start, m.Incident.opened_at < end)))
        open_now = s.scalar(select(func.count()).select_from(m.Incident).where(
            m.Incident.workspace_id == ws, m.Incident.status.in_(inc_.ACTIVE))) or 0
        cars = dict(s.execute(select(m.VehiclePassage.direction, func.count()).where(
            m.VehiclePassage.workspace_id == ws, m.VehiclePassage.at >= start, m.VehiclePassage.at < end)
            .group_by(m.VehiclePassage.direction)).all())
        staff = s.scalar(select(func.count()).select_from(m.Attendance).where(
            m.Attendance.workspace_id == ws, m.Attendance.day == day.isoformat(),
            m.Attendance.first_in.is_not(None))) or 0
        watch_ids = {w for _, _, w in rows if w}
        watches = {w.id: w for w in s.scalars(select(m.Watch).where(m.Watch.workspace_id == ws,
                                                                    m.Watch.id.in_(watch_ids)))}
    cameras = [c for c in repo.list_cameras() if c.enabled]
    name = society.name if society else "your society"
    lines = [f"Daily summary for {name}, {day.strftime('%A')} {day.day} {day.strftime('%B')}"]
    if rows:
        by = {sev: sum(1 for r in rows if r[0] == sev) for sev in ("critical", "warning", "info")}
        parts = [_n(by[sev], *WORDS[sev]) for sev in by if by[sev]]
        false = sum(1 for r in rows if r[1] == inc_.FALSE_ALARM)
        lines.append(f"Alerts: {len(rows)} ({', '.join(parts)}). "
                     + (f"{_n(false, 'was a false alarm', 'were false alarms')}." if false else "No false alarms."))
    else:
        lines.append("Alerts: none.")
    lines.append(f"Open now: {open_now}.")
    down = [c for c in cameras if c.status != "live"]
    if not cameras:
        lines.append("Cameras: none added yet.")
    elif down:
        lines.append("Cameras not live now: " + ", ".join(f"{c.name} ({c.status})" for c in down) + ".")
    else:
        lines.append(f"Cameras: all {len(cameras)} live.")
    if cars:
        lines.append(f"Vehicles: {cars.get('in', 0)} in, {cars.get('out', 0)} out.")
    lines.append(f"Staff: {_n(staff, 'came in', 'came in')}." if staff else "Staff: nobody recorded.")
    noisy = []
    for watch_id in sorted(watch_ids):
        mine = [r for r in rows if r[2] == watch_id]
        false = sum(1 for r in mine if r[1] == inc_.FALSE_ALARM)
        if false:
            noisy.append(f"{_name(watches.get(watch_id))} {false} of {len(mine)}")
    if noisy:
        lines.append("False alarms by watch: " + "; ".join(noisy) + ".")
    return "\n".join(lines)


@periodic
def queue_summaries(worker, now: datetime) -> list[str]:
    """Queues yesterday's summary for each society with a linked group, once, from summary_hour society time until
    summary_late_h after it."""
    t = worker.tuning.alerts
    with worker.queue.tx() as s:
        societies = s.execute(select(m.Workspace.id, m.Workspace.timezone)
                              .join(m.TelegramLink, m.TelegramLink.workspace_id == m.Workspace.id)
                              .where(m.TelegramLink.chat_id.is_not(None)).distinct()).all()
    queued = []
    for ws, zone in societies:
        local = now.astimezone(ZoneInfo(zone or worker.settings.timezone))
        due = datetime.combine(local.date(), time(t.summary_hour), local.tzinfo)
        if not due <= local < due + timedelta(hours=t.summary_late_h):
            continue
        day = (local.date() - timedelta(days=1)).isoformat()
        if worker.queue.enqueue(SUMMARY, {"workspace_id": ws, "day": day}, now, key=f"summary|{ws}|{day}"):
            queued.append(ws)
    return queued


@handler(SUMMARY)
def _send(ctx: JobContext, payload: dict) -> None:
    repo = ctx.repo(payload["workspace_id"])
    text = daily(repo, payload["day"])
    with repo.tx() as s:
        chats = list(s.scalars(select(m.TelegramLink.chat_id).where(
            m.TelegramLink.workspace_id == repo.workspace_id, m.TelegramLink.chat_id.is_not(None))))
    if not chats:
        log.info("Telegram is not connected for %s; summary: %s", repo.workspace_id, text.replace("\n", " | "))
        return
    sent, waits = list(payload.get("sent", [])), []
    for chat in chats:
        if chat in sent:
            continue
        try:
            _, wait = _call(_limiter(ctx), chat, ctx.now, lambda: ctx.worker.channel.send(chat, text))
        except TelegramError as e:
            if not e.gone:
                raise
            log.warning("could not post the summary to chat %s: %s", chat, e.description)
            continue
        if wait:
            waits.append(wait)
        else:
            sent.append(chat)
    if waits:
        with ctx.queue.tx() as s:  # a deferred run skips the groups that already have it
            s.get(m.Job, ctx.job.id).payload = {**payload, "sent": sent}
        raise Defer(min(waits))

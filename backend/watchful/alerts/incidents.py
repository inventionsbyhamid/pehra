"""Incidents: repeats of one situation on one camera (the same watch, or the same camera health problem) grouped
into one record with a severity, a count, a status (open, acknowledged, false_alarm, resolved) and who acted.

Opening, touching or closing an incident queues its Telegram message in the same transaction; the job worker sends
and edits it (alerts/telegram.py). Critical goes at once, before the AI check finishes; a warning only when the
society turned warnings on; info never. An unacknowledged critical incident is posted again after Tuning
alerts.escalate_min, at most alerts.escalate_times times. A false alarm closes the incident for everyone; its who
and when are the label behind each watch's precision. A closed incident never takes new triggers, and an AI check
that finds a real moment is never silenced by an earlier false alarm. Every function takes the society's repository,
so an id from another society is not found."""
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select

from watchful.db import models as m
from watchful.jobs import JobQueue, handler
from watchful.tuning import AlertTuning

SEVERITIES = ("info", "warning", "critical")
OPEN, ACKNOWLEDGED, FALSE_ALARM, RESOLVED = "open", "acknowledged", "false_alarm", "resolved"
ACTIVE = (OPEN, ACKNOWLEDGED)
SYNC, ESCALATE, RETENTION, RELEASE = "telegram.sync", "incident.escalate", "incidents.retention", "incident.release"
CHECKING, NOT_CHECKED = "Checking now.", "Not checked by the AI yet."
WARNINGS_KEY = "telegram_warnings"  # Meta: "on" when the society wants warnings on Telegram too
AI_CHECK = "the AI check"  # agent_json closed_by of an incident the AI check cleared

_lock = threading.RLock()  # grouping is check then write; one writer at a time in this process


@dataclass(frozen=True)
class Actor:
    """Whoever acknowledged or closed an incident: a signed in member, a Telegram user, the AI check or the app."""
    id: str  # a user id, "telegram:<user id>", "agent" or "system"
    name: str
    via: str = "app"


def actor_for(member) -> Actor:
    """An Actor for a signed in member (auth.deps.CurrentMember), or the Actor given."""
    if isinstance(member, Actor):
        return member
    user = member.user
    return Actor(user.id, user.name or user.email or "A member", "app")


def when(now=None) -> datetime:
    """An aware UTC datetime from epoch seconds, a datetime or None (now)."""
    if now is None:
        return m.utcnow()
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=UTC)
    return datetime.fromtimestamp(float(now), UTC)


def pushable(severity: str, warnings_on: bool) -> bool:
    return severity == "critical" or (severity == "warning" and warnings_on)


def warnings_on(repo) -> bool:
    return repo.get_meta(WARNINGS_KEY) == "on"


def set_warnings(repo, on: bool) -> None:
    repo.set_meta(WARNINGS_KEY, "on" if on else "off")


def graded_severity(watch_severity: str, agent_severity: str | None) -> str:
    """The AI check's grade, except that a critical watch stays critical (only a dismissal clears it)."""
    if watch_severity == "critical" or agent_severity not in SEVERITIES:
        return watch_severity
    return agent_severity


def _mine(repo, s, incident_id: str) -> m.Incident | None:
    return s.scalar(select(m.Incident).where(m.Incident.id == incident_id,
                                             m.Incident.workspace_id == repo.workspace_id))


def get_incident(repo, incident_id: str) -> m.Incident | None:
    with repo.tx() as s:
        return _mine(repo, s, incident_id)


def list_incidents(repo, statuses: tuple[str, ...] | None = None, camera_id: str | None = None,
                   limit: int = 100) -> list[m.Incident]:
    stmt = select(m.Incident).where(m.Incident.workspace_id == repo.workspace_id)
    if statuses:
        stmt = stmt.where(m.Incident.status.in_(statuses))
    if camera_id:
        stmt = stmt.where(m.Incident.camera_id == camera_id)
    with repo.tx() as s:
        return list(s.scalars(stmt.order_by(m.Incident.last_at.desc()).limit(limit)))


def _same(column, value):
    return column.is_(None) if value is None else column == value


def active_incident(repo, camera_id: str | None, watch_id: str | None, now=None,
                    tuning: AlertTuning | None = None) -> m.Incident | None:
    """The open or acknowledged incident that a trigger at `now` would join, if any."""
    t, now = tuning or AlertTuning(), when(now)
    with repo.tx() as s:
        inc = _latest(s, repo.workspace_id, camera_id, watch_id, now, t)
        return inc if inc is not None and inc.status in ACTIVE else None


def _latest(s, workspace_id, camera_id, watch_id, now, t: AlertTuning) -> m.Incident | None:
    """The open or acknowledged incident a trigger joins; a closed one never takes new triggers."""
    return s.scalar(select(m.Incident).where(
        m.Incident.workspace_id == workspace_id, _same(m.Incident.camera_id, camera_id),
        _same(m.Incident.watch_id, watch_id), m.Incident.status.in_(ACTIVE),
        m.Incident.last_at >= now - timedelta(seconds=t.group_s)).order_by(m.Incident.last_at.desc()).limit(1))


def _closed_since(s, workspace_id, camera_id, watch_id, since: datetime, false_alarm_only: bool = False):
    """The incidents on this camera and watch closed at or after `since`: by a person as a false alarm, or cleared
    by the AI check. Counted from when they closed, so later triggers never move the window."""
    closed = [m.Incident.status == FALSE_ALARM] if false_alarm_only else \
        [or_(m.Incident.status == FALSE_ALARM, m.Incident.agent_json["closed_by"].as_string() == AI_CHECK)]
    return list(s.scalars(select(m.Incident).where(
        m.Incident.workspace_id == workspace_id, _same(m.Incident.camera_id, camera_id),
        _same(m.Incident.watch_id, watch_id), m.Incident.closed_at >= since, *closed)
        .order_by(m.Incident.closed_at.desc())))


def _quiet(s, workspace_id, camera_id, watch_id, fingerprint: str, now: datetime, t: AlertTuning):
    """A false alarm closed within Tuning alerts.false_alarm_quiet_s that already took this very trigger."""
    since = now - timedelta(seconds=t.false_alarm_quiet_s)
    return next((inc for inc in _closed_since(s, workspace_id, camera_id, watch_id, since, True)
                 if fingerprint in (inc.agent_json or {}).get("fingerprints", [])), None)


def _queue_sync(s, workspace_id: str, incident_id: str, now: datetime, urgent: bool) -> None:
    """One queued Telegram sync per incident: a burst of triggers folds into one edit. A status change pulls a
    sync that is waiting out the edit interval forward to now."""
    waiting = s.scalar(select(m.Job).where(m.Job.kind == SYNC, m.Job.status == "queued",
                                           m.Job.payload["incident_id"].as_string() == incident_id).limit(1))
    if waiting is None:
        s.add(m.Job(kind=SYNC, payload={"workspace_id": workspace_id, "incident_id": incident_id}, run_after=now,
                    created_at=now))
    elif urgent and waiting.run_after > now:
        waiting.run_after = now


def _queue_escalation(s, inc: m.Incident, t: AlertTuning) -> None:
    if inc.severity != "critical" or t.escalate_times < 1:
        return
    since = inc.opened_at
    JobQueue(s.get_bind()).enqueue(
        ESCALATE, {"workspace_id": inc.workspace_id, "incident_id": inc.id, "n": 1, "since": since.isoformat()},
        run_after=since + timedelta(minutes=t.escalate_min), key=f"escalate|{inc.id}|1", session=s)


def _notify(s, inc: m.Incident, now: datetime, t: AlertTuning, urgent: bool, escalate: bool) -> None:
    if inc.status not in ACTIVE or (inc.agent_json or {}).get("held"):
        return
    meta = s.get(m.Meta, (inc.workspace_id, WARNINGS_KEY))
    if not pushable(inc.severity, meta is not None and meta.value == "on"):
        return
    _queue_sync(s, inc.workspace_id, inc.id, now, urgent)
    if escalate:
        _queue_escalation(s, inc, t)


def _remember(inc: m.Incident, fingerprint: str | None, t: AlertTuning) -> None:
    if fingerprint:
        seen = [f for f in (inc.agent_json or {}).get("fingerprints", []) if f != fingerprint]
        inc.agent_json = {**(inc.agent_json or {}), "fingerprints": (seen + [fingerprint])[-t.fingerprints_kept:]}


def _release(inc: m.Incident, timed_out: bool) -> None:
    """A held incident goes out: its check found it real, or no verdict came within alerts.hold_max_s (then it says
    it is not checked yet)."""
    inc.agent_json = {**(inc.agent_json or {}), "held": False}
    if timed_out:
        reason = inc.reason or ""
        inc.reason = reason.replace(CHECKING, NOT_CHECKED) if CHECKING in reason else f"{reason} {NOT_CHECKED}".strip()


def _open_or_touch(s, workspace_id: str, camera_id, watch_id, severity: str, reason: str, keyframe_key, now, t,
                   notify: bool, picture_key=None, picture_box=None, extra: dict | None = None,
                   fingerprint: str | None = None, check_first: bool = False, real: bool = False) -> m.Incident:
    if severity not in SEVERITIES:
        raise ValueError(f"severity is one of {', '.join(SEVERITIES)}")
    if fingerprint and (quiet := _quiet(s, workspace_id, camera_id, watch_id, fingerprint, now, t)) is not None:
        return quiet
    inc = _latest(s, workspace_id, camera_id, watch_id, now, t)
    if inc is None:
        held = check_first and bool(_closed_since(s, workspace_id, camera_id, watch_id,
                                                  now - timedelta(seconds=t.group_s)))
        inc = m.Incident(workspace_id=workspace_id, camera_id=camera_id, watch_id=watch_id, severity=severity,
                         status=OPEN, opened_at=now, last_at=now, count=1, reason=reason or "",
                         keyframe_key=keyframe_key, picture_key=picture_key, picture_box=picture_box,
                         agent_json={**(extra or {}), **({"held": True} if held else {})})
        _remember(inc, fingerprint, t)
        s.add(inc)
        s.flush()
        if held:  # if its check never files (cancelled, crashed, stuck in a queue), it goes out anyway
            JobQueue(s.get_bind()).enqueue(RELEASE, {"workspace_id": workspace_id, "incident_id": inc.id},
                                           run_after=now + timedelta(seconds=t.hold_max_s), key=f"release|{inc.id}",
                                           session=s)
        if notify:
            _notify(s, inc, now, t, urgent=True, escalate=True)
        return inc
    inc.count += 1
    inc.last_at = max(inc.last_at, now)
    rose = SEVERITIES.index(severity) > SEVERITIES.index(inc.severity)
    if rose:
        inc.severity = severity
    if reason and not inc.reason:
        inc.reason = reason
    if keyframe_key and not inc.keyframe_key:
        inc.keyframe_key = keyframe_key
    if picture_key and not inc.picture_key:
        inc.picture_key, inc.picture_box = picture_key, picture_box
    if extra:
        inc.agent_json = {**extra, **(inc.agent_json or {})}
    _remember(inc, fingerprint, t)
    released = bool((inc.agent_json or {}).get("held")) and (
        real or (now - inc.opened_at).total_seconds() >= t.hold_max_s)
    if released:
        _release(inc, timed_out=not real)
    if notify:
        _notify(s, inc, now, t, urgent=rose or released, escalate=rose or released)
    return inc


def open_or_touch_incident(repo, camera_id: str | None, watch_id: str | None, severity: str, reason: str,
                           keyframe_key: str | None = None, now=None, *, tuning: AlertTuning | None = None,
                           notify: bool = True, picture_key: str | None = None, picture_box: list | None = None,
                           extra: dict | None = None, fingerprint: str | None = None,
                           check_first: bool = False, real: bool = False) -> m.Incident:
    """Opens an incident, or adds this trigger to the open or acknowledged one it repeats: the same camera and watch
    (a health problem passes its own watch_id, such as "camera_offline") with a trigger in the last Tuning
    alerts.group_s. A repeat raises the count and never lowers the severity. A closed incident never takes new
    triggers: only the very trigger (`fingerprint`) a false alarm closed stays quiet, for Tuning
    alerts.false_alarm_quiet_s from the tap (the closed incident is returned unchanged). `check_first` (a candidate
    before its AI check) holds a new incident off Telegram until the check says it is real, when this camera and watch
    was cleared by the AI or closed as a false alarm in the last alerts.group_s; it goes out when a real verdict lands
    on it (`real`), or as not checked yet after alerts.hold_max_s. `notify` False (a recorded upload, not a live
    camera) keeps it off Telegram. `extra` is kept in agent_json."""
    t, now = tuning or AlertTuning(), when(now)
    with _lock, repo.tx() as s:
        return _open_or_touch(s, repo.workspace_id, camera_id, watch_id, severity, reason, keyframe_key, now, t,
                              notify, picture_key, picture_box, extra, fingerprint, check_first, real)


def open_before_check(repo, candidate, picture_key: str | None = None, picture_box: list | None = None, *,
                      tuning: AlertTuning | None = None) -> m.Incident:
    """A critical candidate's incident, opened before its AI check so Telegram hears at once ("Possible: ...
    Checking now."), unless this camera and watch was just cleared or closed as a false alarm: then it waits for the
    check and goes only if the check says it is real."""
    c = candidate
    return open_or_touch_incident(repo, c.camera_id, c.watch_id, "critical", checking_text(c.watch_name),
                                  c.keyframe_keys[0] if c.keyframe_keys else None, c.ts, tuning=tuning,
                                  picture_key=picture_key, picture_box=picture_box,
                                  extra={"cls": getattr(c, "cls", None)}, fingerprint=c.fingerprint,
                                  check_first=True)


def record_verdict(repo, incident_id: str, status: str, severity: str | None, summary: str, why: str = "",
                   alert_id: str | None = None, now=None, *, tuning: AlertTuning | None = None,
                   notify: bool = True) -> m.Incident | None:
    """The AI check's verdict on an incident: its summary becomes the reason and its grade the severity (a critical
    one stays critical). A dismissal clears an incident nobody has acted on yet; the verify prompt lets the agent
    dismiss a critical moment only with visual proof that everyone is safe. A real moment is never silenced: when
    the incident was closed meanwhile (a false alarm tap, say), the verdict opens a new one and it is posted. Returns
    the incident the verdict is on."""
    t, now = tuning or AlertTuning(), when(now)
    verdict = {"verdict": status, "why": why, "alert_id": alert_id}
    with _lock, repo.tx() as s:
        inc = _mine(repo, s, incident_id)
        if inc is None:
            return None
        prior = (inc.agent_json or {}).get("verdict")
        if inc.status not in ACTIVE:
            if status == "dismissed":
                return inc
            fresh = _open_or_touch(s, inc.workspace_id, inc.camera_id, inc.watch_id,
                                   graded_severity(inc.severity, severity), summary or inc.reason, inc.keyframe_key,
                                   now, t, False, inc.picture_key, inc.picture_box,
                                   {"cls": (inc.agent_json or {}).get("cls")}, real=True)
            fresh.agent_json = {**(fresh.agent_json or {}), **verdict, "held": False}
            if summary:
                fresh.reason = summary
            if notify:
                _notify(s, fresh, now, t, urgent=True, escalate=True)
            return fresh
        if status == "dismissed" and prior == "alert":
            return inc  # another check found this situation real: a later dismissal does not clear it
        held = bool((inc.agent_json or {}).get("held"))
        inc.agent_json = {**(inc.agent_json or {}), **verdict}
        if status == "dismissed":
            if inc.status == OPEN:
                inc.status, inc.closed_at = RESOLVED, now
                inc.agent_json = {**inc.agent_json, "closed_by": AI_CHECK}
                if summary:
                    inc.reason = summary
                if notify and not held:
                    _queue_sync(s, inc.workspace_id, inc.id, now, urgent=True)
            return inc
        graded = graded_severity(inc.severity, severity)
        rose = SEVERITIES.index(graded) > SEVERITIES.index(inc.severity)
        inc.severity = graded
        if summary:
            inc.reason = summary
        if held:
            inc.agent_json = {**inc.agent_json, "held": False}
        if notify:
            _notify(s, inc, now, t, urgent=True, escalate=rose or held)
        return inc


def after_check(repo, candidate, verdict, alert_id: str | None = None, incident_id: str | None = None, now=None, *,
                tuning: AlertTuning | None = None, notify: bool = True, picture_key: str | None = None,
                picture_box: list | None = None, extra: dict | None = None) -> m.Incident | None:
    """Files a checked candidate (rules.engine.Candidate and agent.verifier.Verdict): gives the verdict to the
    incident a critical candidate opened before its check, or opens or touches one for a real moment; a dismissed one
    opens nothing."""
    if incident_id is not None:
        return record_verdict(repo, incident_id, verdict.status, getattr(verdict, "severity", None), verdict.summary,
                              getattr(verdict, "reason", ""), alert_id, now, tuning=tuning, notify=notify)
    if verdict.status == "dismissed":
        return None
    t = tuning or AlertTuning()
    severity = graded_severity(candidate.severity, getattr(verdict, "severity", None))
    with _lock, repo.tx() as s:
        inc = _open_or_touch(s, repo.workspace_id, candidate.camera_id, candidate.watch_id, severity,
                             verdict.summary or candidate.watch_name,
                             candidate.keyframe_keys[0] if candidate.keyframe_keys else None,
                             when(now if now is not None else candidate.ts), t, notify, picture_key, picture_box,
                             {"cls": getattr(candidate, "cls", None), **(extra or {})}, real=True)
        if "verdict" not in (inc.agent_json or {}):  # new, or one still checking (a held one went out above)
            inc.agent_json = {**(inc.agent_json or {}), "verdict": verdict.status,
                              "why": getattr(verdict, "reason", ""), "alert_id": alert_id}
            if verdict.summary:
                inc.reason = verdict.summary
        return inc


def checking_text(watch_name: str) -> str:
    return f"Possible: {watch_name.rstrip('.')}. {CHECKING}"


@handler(RELEASE, urgent=True)
def _release_held(ctx, payload: dict) -> None:
    """A held incident whose AI check filed nothing within alerts.hold_max_s goes out as not checked yet."""
    repo, t = ctx.repo(payload["workspace_id"]), ctx.tuning.alerts
    with _lock, repo.tx() as s:
        inc = _mine(repo, s, payload["incident_id"])
        if inc is None or inc.status != OPEN or not (inc.agent_json or {}).get("held"):
            return
        _release(inc, timed_out=True)
        _notify(s, inc, ctx.now, t, urgent=True, escalate=True)


def _act(repo, incident_id: str, member, now, action: str, allowed: tuple[str, ...], status: str, chat: str | None):
    actor, now = actor_for(member), when(now)
    with _lock, repo.tx() as s:
        inc = _mine(repo, s, incident_id)
        if inc is None:
            return None, False
        changed = inc.status in allowed
        if changed:
            inc.status, inc.ack_by, inc.ack_at = status, actor.name, now
            if status not in ACTIVE:
                inc.closed_at = now
            _queue_sync(s, inc.workspace_id, inc.id, now, urgent=True)
        s.add(m.Audit(workspace_id=repo.workspace_id, actor=actor.id, action=action, ts=now,
                      data={"incident": inc.id, "watch": inc.watch_id, "camera": inc.camera_id, "name": actor.name,
                            "via": actor.via, "chat": chat, "changed": changed}))
        return inc, changed


def acknowledge(repo, incident_id: str, member, now=None, chat: str | None = None) -> tuple[m.Incident | None, bool]:
    """Someone is on it: an open incident becomes acknowledged, with who and when, and escalation stops. Returns the
    incident and whether this call changed it (a second tap only adds to the audit log)."""
    return _act(repo, incident_id, member, now, "incident.acknowledge", (OPEN,), ACKNOWLEDGED, chat)


def false_alarm(repo, incident_id: str, member, now=None, chat: str | None = None) -> tuple[m.Incident | None, bool]:
    """Closes the incident for everyone as a false alarm, with who and when: the label behind the watch's precision.
    Every Telegram message about it loses its buttons."""
    return _act(repo, incident_id, member, now, "incident.false_alarm", ACTIVE, FALSE_ALARM, chat)


def resolve(repo, incident_id: str, now=None, note: str = "Resolved") -> m.Incident | None:
    """The situation is over (a camera is back, say); its messages are edited to say so."""
    now = when(now)
    with _lock, repo.tx() as s:
        inc = _mine(repo, s, incident_id)
        if inc is None or inc.status not in ACTIVE:
            return inc
        inc.status, inc.closed_at = RESOLVED, now
        inc.agent_json = {**(inc.agent_json or {}), "closed_by": note}
        _queue_sync(s, inc.workspace_id, inc.id, now, urgent=True)
        return inc


def precision_by_watch(repo, since=None) -> dict[str, dict]:
    """Per watch: incidents acknowledged as real, closed as false alarms, and the share of labelled ones that were
    real (None until one is labelled)."""
    stmt = (select(m.Incident.watch_id, m.Incident.status, func.count())
            .where(m.Incident.workspace_id == repo.workspace_id, m.Incident.watch_id.is_not(None))
            .group_by(m.Incident.watch_id, m.Incident.status))
    if since is not None:
        stmt = stmt.where(m.Incident.opened_at >= when(since))
    out: dict[str, dict] = {}
    with repo.tx() as s:
        for watch_id, status, n in s.execute(stmt):
            row = out.setdefault(watch_id, {OPEN: 0, ACKNOWLEDGED: 0, FALSE_ALARM: 0, RESOLVED: 0})
            row[status] = row.get(status, 0) + n
    for row in out.values():
        labelled = row[ACKNOWLEDGED] + row[FALSE_ALARM]
        row["precision"] = round(row[ACKNOWLEDGED] / labelled, 3) if labelled else None
    return out


def delete_incidents_before(repo, before) -> list[str]:
    """Deletes the society's incidents last seen before `before`, with their Telegram message records; returns the
    keys of their private pictures, for the caller to delete."""
    with repo.tx() as s:
        old = [m.Incident.workspace_id == repo.workspace_id, m.Incident.last_at < when(before)]
        keys = [k for k in s.scalars(select(m.Incident.picture_key).where(*old)) if k]
        s.execute(delete(m.Incident).where(*old))
        return keys


def retention_days(settings, workspace) -> int:
    """How long a society's records are kept: its plan's (billing), else RETENTION_DAYS."""
    try:
        from watchful.billing import retention_days as plan_days
    except ImportError:
        return settings.retention_days
    return int(plan_days(workspace))


@handler(RETENTION, daily=True)
def _retention(ctx, payload: dict) -> None:
    """Incidents (the AI's words about people, who acknowledged) and their Telegram records go after each society's
    retention, with their pictures."""
    with ctx.queue.tx() as s:
        societies = list(s.scalars(select(m.Workspace)))
    for ws in societies:
        keys = delete_incidents_before(ctx.repo(ws.id), ctx.now - timedelta(days=retention_days(ctx.settings, ws)))
        for key in keys:
            try:
                ctx.worker.blobs.delete(key)
            except (OSError, KeyError):
                pass


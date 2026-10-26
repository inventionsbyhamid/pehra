"""Incidents in the app: the list, one incident with what happened to it, acknowledging one or closing it as a false
alarm (a supervisor; who and when are kept, and its Telegram messages are edited), and each watch's precision from
those labels."""
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy import select

from watchful.alerts import incidents as incidents_
from watchful.auth.deps import CurrentMember, current_member, require_role
from watchful.db import models as m
from watchful.rules.presets import preset_names

ACTED = {"incident.acknowledge": "acknowledged", "incident.false_alarm": "false_alarm"}


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def incident_json(blobs, inc, title: str | None = None) -> dict:
    key = inc.keyframe_key or inc.picture_key
    return {"id": inc.id, "camera_id": inc.camera_id, "watch_id": inc.watch_id, "severity": inc.severity,
            "status": inc.status, "opened_at": inc.opened_at, "last_at": inc.last_at, "count": inc.count,
            "reason": inc.reason, "graded_why": (inc.agent_json or {}).get("why") or None, "ack_by": inc.ack_by,
            "ack_at": inc.ack_at, "closed_at": inc.closed_at, "on_telegram": inc.telegram_message_id is not None,
            "clip_key": inc.clip_key, "picture_url": blobs.url(key) if key else None, "title": title or inc.reason,
            "example": bool((inc.agent_json or {}).get("seeded"))}  # the demo's: made from a moment of its clip


def titles(repo) -> dict[str, str]:
    """Each watch's own words, the short name the feed shows for its incidents (a preset's watch name)."""
    presets = set(preset_names())
    return {w.id: (w.watch.get("name") if w.text in presets or not w.text.strip() else w.text) or ""
            for w in repo.list_watches()}


def timeline(repo, inc) -> list[dict]:
    """What happened, in order: opened, seen again, sent to Telegram, who acted and how it closed, then the AI
    check's verdict (it keeps no time of its own)."""
    agent = inc.agent_json or {}
    steps = [{"at": inc.opened_at, "kind": "opened", "text": inc.reason}]
    if agent.get("seeded"):
        steps.append({"at": inc.opened_at, "kind": "example"})  # the demo's: no AI check ran on it
    if inc.count > 1:
        steps.append({"at": inc.last_at, "kind": "repeated", "count": inc.count})
    with repo.tx() as s:
        sent = s.execute(select(m.TelegramMessage, m.TelegramLink.title)
                         .join(m.TelegramLink, m.TelegramLink.chat_id == m.TelegramMessage.chat_id, isouter=True)
                         .where(m.TelegramMessage.workspace_id == repo.workspace_id,
                                m.TelegramMessage.incident_id == inc.id)).all()
        acts = s.scalars(select(m.Audit).where(m.Audit.workspace_id == repo.workspace_id,
                                               m.Audit.action.in_(list(ACTED)),
                                               m.Audit.data["incident"].as_string() == inc.id)).all()
    for msg, group in sent:
        steps.append({"at": msg.sent_at, "kind": "reminder" if msg.kind == "reminder" else "telegram",
                      "group": group})
    for a in acts:
        if (a.data or {}).get("changed"):
            steps.append({"at": a.ts, "kind": ACTED[a.action], "by": a.data.get("name"), "via": a.data.get("via")})
    if inc.closed_at is not None and inc.status == incidents_.RESOLVED:
        steps.append({"at": inc.closed_at, "kind": "resolved", "by": agent.get("closed_by")})
    steps.sort(key=lambda x: incidents_.when(x["at"]))
    if agent.get("verdict"):
        steps.append({"at": None, "kind": "checked", "verdict": agent["verdict"], "why": agent.get("why") or None})
    return steps


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    def repo(member: CurrentMember):
        return ctx.repo_for(member.workspace.id)

    def as_json(r, inc) -> dict:
        return incident_json(ctx.blobs, inc, titles(r).get(inc.watch_id or ""))

    @router.get("/api/incidents")
    def incidents(status: str | None = None, camera_id: str | None = None,
                  member: CurrentMember = Depends(current_member)):
        r = repo(member)
        statuses = tuple(s for s in (status or "").split(",") if s) or None
        rows = incidents_.list_incidents(r, statuses, camera_id, limit=200)
        named = titles(r)
        return [incident_json(ctx.blobs, i, named.get(i.watch_id or "")) for i in rows]

    @router.get("/api/incidents/precision")
    def precision(member: CurrentMember = Depends(current_member)):
        return incidents_.precision_by_watch(repo(member))

    @router.get("/api/incidents/{incident_id}")
    def incident(incident_id: str, member: CurrentMember = Depends(current_member)):
        """One incident for its page; alert_id names the checked alert whose clip /api/alerts/{id}/clip plays."""
        r = repo(member)
        inc = incidents_.get_incident(r, incident_id)
        if inc is None:
            raise HTTPException(404, "No such incident.")
        alert_id = (inc.agent_json or {}).get("alert_id")
        alert = r.get_alert(alert_id) if alert_id else None
        return {**as_json(r, inc), "timeline": timeline(r, inc), "alert_id": alert.id if alert else None}

    def act(fn, incident_id: str, member: CurrentMember) -> dict:
        r = repo(member)
        inc, changed = fn(r, incident_id, member)
        if inc is None:
            raise HTTPException(404, "No such incident.")
        return {**as_json(r, inc), "changed": changed}

    @router.post("/api/incidents/{incident_id}/acknowledge")
    def acknowledge(incident_id: str, member: CurrentMember = Depends(require_role("supervisor"))):
        return act(incidents_.acknowledge, incident_id, member)

    @router.post("/api/incidents/{incident_id}/false_alarm")
    def false_alarm(incident_id: str, member: CurrentMember = Depends(require_role("supervisor"))):
        return act(incidents_.false_alarm, incident_id, member)

    return router

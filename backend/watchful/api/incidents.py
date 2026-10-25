"""Incidents in the app: the list, acknowledging one or closing it as a false alarm (a supervisor; who and when are
kept, and its Telegram messages are edited), and each watch's precision from those labels."""
from fastapi import APIRouter, Depends, FastAPI, HTTPException

from watchful.alerts import incidents as incidents_
from watchful.auth.deps import CurrentMember, current_member, require_role


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def incident_json(blobs, inc) -> dict:
    key = inc.keyframe_key or inc.picture_key
    return {"id": inc.id, "camera_id": inc.camera_id, "watch_id": inc.watch_id, "severity": inc.severity,
            "status": inc.status, "opened_at": inc.opened_at, "last_at": inc.last_at, "count": inc.count,
            "reason": inc.reason, "graded_why": (inc.agent_json or {}).get("why") or None, "ack_by": inc.ack_by,
            "ack_at": inc.ack_at, "closed_at": inc.closed_at, "on_telegram": inc.telegram_message_id is not None,
            "clip_key": inc.clip_key, "picture_url": blobs.url(key) if key else None}


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    def repo(member: CurrentMember):
        return ctx.repo_for(member.workspace.id)

    @router.get("/api/incidents")
    def incidents(status: str | None = None, camera_id: str | None = None,
                  member: CurrentMember = Depends(current_member)):
        statuses = tuple(s for s in (status or "").split(",") if s) or None
        rows = incidents_.list_incidents(repo(member), statuses, camera_id, limit=200)
        return [incident_json(ctx.blobs, i) for i in rows]

    @router.get("/api/incidents/precision")
    def precision(member: CurrentMember = Depends(current_member)):
        return incidents_.precision_by_watch(repo(member))

    def act(fn, incident_id: str, member: CurrentMember) -> dict:
        inc, changed = fn(repo(member), incident_id, member)
        if inc is None:
            raise HTTPException(404, "No such incident.")
        return {**incident_json(ctx.blobs, inc), "changed": changed}

    @router.post("/api/incidents/{incident_id}/acknowledge")
    def acknowledge(incident_id: str, member: CurrentMember = Depends(require_role("supervisor"))):
        return act(incidents_.acknowledge, incident_id, member)

    @router.post("/api/incidents/{incident_id}/false_alarm")
    def false_alarm(incident_id: str, member: CurrentMember = Depends(require_role("supervisor"))):
        return act(incidents_.false_alarm, incident_id, member)

    return router

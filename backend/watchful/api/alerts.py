"""Alerts: the list, feedback, approving or declining one, and its clip. Acting on an alert needs a supervisor."""
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from watchful.api.common import alert_json, alert_or_404
from watchful.auth.deps import CurrentMember, current_member, require_role
from watchful.store.blobs import society_key
from watchful.vision.scan import cut_clip


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    def repo(member: CurrentMember):
        return ctx.repo_for(member.workspace.id)

    def audit(member: CurrentMember, action: str, alert_id: str, **data) -> None:
        ctx.db.audit(member.workspace.id, member.user.id, action, {"alert": alert_id, **data})

    @router.get("/api/alerts")
    def alerts(camera_id: str | None = None, include_dismissed: bool = False,
               member: CurrentMember = Depends(current_member)):
        rows = repo(member).list_alerts(camera_id=camera_id, limit=200)
        return [alert_json(ctx.blobs, a) for a in rows if include_dismissed or a.status != "dismissed"]

    @router.post("/api/alerts/{alert_id}/feedback")
    def feedback(alert_id: str, value: str = Body(..., embed=True),
                 member: CurrentMember = Depends(require_role("supervisor"))):
        r = repo(member)
        a = alert_or_404(r, alert_id)
        if value not in ("up", "down"):
            raise HTTPException(400, "Feedback is up or down.")
        a.feedback = value
        r.update_alert(a)
        audit(member, "alert.feedback", a.id, value=value)
        return {"ok": True}

    @router.post("/api/alerts/{alert_id}/approve")
    def approve(alert_id: str, member: CurrentMember = Depends(require_role("supervisor"))):
        alert_or_404(repo(member), alert_id)
        audit(member, "alert.approve", alert_id)
        return {"notified": ctx.manager.approve(alert_id, member.workspace.id)}

    @router.post("/api/alerts/{alert_id}/decline")
    def decline(alert_id: str, member: CurrentMember = Depends(require_role("supervisor"))):
        r = repo(member)
        a = alert_or_404(r, alert_id)
        a.status = "declined"
        r.update_alert(a)
        audit(member, "alert.decline", a.id)
        return {"ok": True}

    @router.get("/api/alerts/{alert_id}/clip")
    def clip(alert_id: str, member: CurrentMember = Depends(current_member)):
        r = repo(member)
        a = alert_or_404(r, alert_id)
        key = a.clip_key or society_key(member.workspace.id, a.camera_id, "clips", f"{a.fingerprint}.mp4")
        try:
            ctx.blobs.get(key)
        except (OSError, KeyError):
            cam = r.get_camera(a.camera_id)
            if cam is None or cam.kind != "file":
                raise HTTPException(404, "The clip is still being written. Try again in a few seconds.")
            start = (cam.last_scan or {}).get("start_epoch")
            if start is None:
                raise HTTPException(404, "Scan this video again to make its clips.")
            data = cut_clip(cam.url, max(0.0, a.ts - start), ctx.settings, ctx.tuning)
            ctx.blobs.put(key, data, "video/mp4")
            a.clip_key = key
            r.update_alert(a)
        audit(member, "clip.view", a.id)
        return RedirectResponse(ctx.blobs.url(key))

    return router

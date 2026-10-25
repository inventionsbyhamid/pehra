"""Enrolled people (consenting adult staff): the list for supervisors, enrolling and forgetting for admins. A face
template only ever joins its own society's face index."""
import cv2
import numpy as np
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from watchful.api.common import LIVE_KINDS
from watchful.auth.deps import CurrentMember, require_role
from watchful.billing.limits import staff_allowed, staff_refusal, workspace_of
from watchful.store.db import new_id
from watchful.vision.faces import NoFaceFound


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    manager = ctx.manager

    def restart_cameras(repo) -> None:
        for cam in repo.list_cameras():
            if cam.kind in LIVE_KINDS:
                manager.restart(cam.id)

    @router.get("/api/people")
    def people(member: CurrentMember = Depends(require_role("supervisor"))):
        return [{"person_id": e.person_id, "name": e.name, "consent_ts": e.consent_ts,
                 "thumbnail_url": ctx.blobs.url(e.thumbnail_key) if e.thumbnail_key else None}
                for e in ctx.repo_for(member.workspace.id).list_enrollments()]

    @router.post("/api/people")
    async def enroll(name: str = Form(...), consent: str = Form("false"), photo: UploadFile | None = File(None),
                     event_id: str = Form(""), member: CurrentMember = Depends(require_role("admin"))):
        ws = member.workspace.id
        repo = ctx.repo_for(ws)
        if consent.lower() != "true":
            raise HTTPException(400, "Enrolling someone needs their consent.")
        society = workspace_of(ctx.db, ws)
        if (most := staff_allowed(society)) is not None and len(repo.list_enrollments()) >= most:
            raise HTTPException(403, staff_refusal(society))
        if photo is not None:
            data = await photo.read()
        elif event_id and (e := repo.get_event(event_id)) and e.keyframe_key:
            data = ctx.blobs.get(e.keyframe_key)
        else:
            raise HTTPException(400, "Add a photo or pick a sighting.")
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "That picture can't be read.")
        person_id = new_id("per")
        key = f"people/{person_id}.jpg"
        faces = await run_in_threadpool(ctx.faces_for, ws)
        try:
            enrolled = faces.enroll(person_id, name, image, consent=True, thumbnail_key=key)
        except NoFaceFound:
            raise HTTPException(400, "No face found in that picture.")
        ctx.blobs.put(key, data, "image/jpeg")
        repo.add_enrollment(enrolled)
        ctx.db.audit(ws, member.user.id, "person.enroll", {"person": person_id, "name": name})
        await run_in_threadpool(restart_cameras, repo)  # restarts join processes; keep the loop free
        return {"person_id": person_id, "name": name}

    @router.delete("/api/people/{person_id}")
    def forget(person_id: str, member: CurrentMember = Depends(require_role("admin"))):
        ws = member.workspace.id
        repo = ctx.repo_for(ws)
        if not any(e.person_id == person_id for e in repo.list_enrollments()):
            raise HTTPException(404, "No such person.")
        repo.delete_enrollment(person_id)
        ctx.forget_faces(ws)
        ctx.blobs.delete(f"people/{person_id}.jpg")
        ctx.db.audit(ws, member.user.id, "person.forget", {"person": person_id})
        restart_cameras(repo)
        return {"ok": True}

    return router

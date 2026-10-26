"""Staff (consenting adult staff, for the staff register): the list for supervisors, enrolling and removing for
admins. An enrolment needs a name, a role, the consent tick and 3 to 5 photos of the person alone; the photos become
face templates and are dropped, never stored. Nobody's age is estimated: the form and the consent words say adults
only. A face template only ever joins its own society's face index. Removing someone deletes their templates, keeps
their attendance, and every camera forgets them at once (and within Tuning faces.reload_s at the latest).

/api/people is the earlier name of the same thing, kept for the pages built on it."""
import cv2
import numpy as np
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from watchful.auth.deps import CurrentMember, require_role
from watchful.billing.limits import staff_allowed, staff_refusal, workspace_of
from watchful.logs import register
from watchful.store.blobs import in_society
from watchful.vision.faces import ManyFaces, NoFaceFound


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def _fit(image: np.ndarray, side: int = 1280) -> np.ndarray:
    h, w = image.shape[:2]
    k = side / max(h, w)
    return cv2.resize(image, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else image


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    manager = ctx.manager
    cfg = ctx.tuning.staff

    def form_words(ws: str) -> dict:
        society = workspace_of(ctx.db, ws)
        return {"roles": register.words()["roles"], "photos": {"min": cfg.min_photos, "max": cfg.max_photos},
                "consent_text": register.consent_text("This person"), "adults_only": register.words()["adults_only"],
                "limit": staff_allowed(society), "count": register.active_count(ctx.repo_for(ws))}

    @router.get("/api/staff")
    def staff(removed: bool = False, member: CurrentMember = Depends(require_role("supervisor"))):
        ws = member.workspace.id
        return {**form_words(ws), "staff": register.staff_list(ctx.repo_for(ws), include_removed=removed)}

    def templates(ws: str, photos: list[bytes]) -> list[list[float]]:
        """One face template per photo, each photo showing exactly one face, all of the same person."""
        faces = ctx.faces_for(ws)
        out = []
        for i, data in enumerate(photos, 1):
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise register.Refused(f"Photo {i} can't be read. Use a JPEG or PNG photo.")
            try:
                out.append(faces.template(_fit(image)))
            except NoFaceFound:
                raise register.Refused(f"No face found in photo {i}. Use a clear photo of their face, in good light.")
            except ManyFaces:
                raise register.Refused(f"Photo {i} shows more than one face. Use photos of this person alone.")
        odd = register.check_photos(out, faces.similarity, ctx.tuning.faces)
        if odd is not None:
            raise register.Refused(f"Photo {odd} does not look like the same person as the other photos.")
        return out

    async def enrol(member: CurrentMember, name: str, role: str, consent: str, files: list[UploadFile]) -> dict:
        ws = member.workspace.id
        repo = ctx.repo_for(ws)
        if consent.strip().lower() not in ("true", "on", "yes", "1"):
            raise HTTPException(400, "Enrolling someone needs their consent. Tick the box once they have agreed.")
        society = workspace_of(ctx.db, ws)
        most = staff_allowed(society)
        if most is not None and register.active_count(repo) >= most:
            raise HTTPException(403, staff_refusal(society))
        if not (cfg.min_photos <= len(files) <= cfg.max_photos):
            raise HTTPException(400, f"Add {cfg.min_photos} to {cfg.max_photos} photos of their face, each showing "
                                     "only them.")
        photos = []
        for f in files:
            data = await f.read(int(cfg.max_photo_mb * 1024 * 1024) + 1)
            if len(data) > cfg.max_photo_mb * 1024 * 1024:
                raise HTTPException(400, f"Each photo must be under {cfg.max_photo_mb:g} MB.")
            photos.append(data)
        try:
            features = await run_in_threadpool(templates, ws, photos)
            del photos  # only the templates are kept
            row = register.enrol(repo, name, role, features, member.user.id, most=most,
                                 refusal=staff_refusal(society))
        except register.Refused as e:
            raise HTTPException(403 if e.over_limit else 400, str(e))
        ctx.db.audit(ws, member.user.id, "staff.enrol", {"staff": row.id, "name": row.name, "role": row.role,
                                                         "photos": len(features)})
        ctx.forget_faces(ws)
        await run_in_threadpool(manager.reload_faces, ws)  # every camera process with this society's cameras
        return {"id": row.id, "person_id": row.id, "name": row.name, "role": row.role, "faces": len(features)}

    @router.post("/api/staff")
    async def add_staff(name: str = Form(""), role: str = Form("other"), consent: str = Form("false"),
                        photos: list[UploadFile] = File(default=[]),
                        member: CurrentMember = Depends(require_role("admin"))):
        return await enrol(member, name, role, consent, photos)

    def forget(staff_id: str, member: CurrentMember) -> dict:
        ws = member.workspace.id
        row, keys = register.remove(ctx.repo_for(ws), staff_id, member.user.id)
        if row is None:
            raise HTTPException(404, "No such staff member.")
        for key in keys:
            if in_society(key, ws):
                ctx.blobs.delete(key)
        ctx.forget_faces(ws)
        ctx.db.audit(ws, member.user.id, "staff.remove", {"staff": row.id, "name": row.name})
        manager.reload_faces(ws)  # gone from every camera process now, no restarts
        return {"ok": True, "id": row.id, "removed_at": row.removed_at}

    @router.delete("/api/staff/{staff_id}")
    def remove_staff(staff_id: str, member: CurrentMember = Depends(require_role("admin"))):
        return forget(staff_id, member)

    # the earlier names

    @router.get("/api/people")
    def people(member: CurrentMember = Depends(require_role("supervisor"))):
        return [{"person_id": r["id"], "name": r["name"], "role": r["role"], "faces": r["faces"],
                 "consent_ts": r["consent_at"].timestamp() if r["consent_at"] else None, "thumbnail_url": None}
                for r in register.staff_list(ctx.repo_for(member.workspace.id))]

    @router.post("/api/people")
    async def enroll(name: str = Form(""), role: str = Form("other"), consent: str = Form("false"),
                     photos: list[UploadFile] = File(default=[]), photo: list[UploadFile] = File(default=[]),
                     member: CurrentMember = Depends(require_role("admin"))):
        return await enrol(member, name, role, consent, photos + photo)

    @router.delete("/api/people/{person_id}")
    def forget_person(person_id: str, member: CurrentMember = Depends(require_role("admin"))):
        return forget(person_id, member)

    return router

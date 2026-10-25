"""Cameras, uploaded videos and their scans, device links and the numbers page. Reading needs any role; changing
cameras needs an admin.

A member adds a stream the server reads (an RTSP or HTTP address, or one built from an NVR template; netguard.py
refuses private addresses) or a camera that sends its own pictures ("webcam": this browser, a phone or laptop by
device link, or the bridge, whose address on the society's network is kept only so the bridge command can be shown).
Demo cameras come only from scripts/seed_demo.py, and the server opens local clips only inside their own folders."""
import json
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from watchful.agent.compiler import bare_age_name
from watchful.api.common import LIVE_KINDS, camera_json, camera_or_404
from watchful.auth.deps import CurrentMember, current_member, require_role
from watchful.billing.limits import daily_ai_cap, start_watching
from watchful.ingest import nvr
from watchful.ingest.push import CLOSE_GONE, CLOSE_OFF, push_parts, qr_svg
from watchful.netguard import check_url, clip_roots, inside
from watchful.rules.engine import validate
from watchful.rules.presets import purposes
from watchful.rules.watch import Watch
from watchful.rules.zonestate import example_key, is_night
from watchful.store.db import Camera, new_id
from watchful.tuning import load_tuning
from watchful.vision.zones import Zone


USER_KINDS = {"stream", "webcam"}  # what a member may add; demo cameras come only from the seed script


class NvrIn(BaseModel):
    brand: str
    host: str
    channel: int = 1
    user: str = ""
    password: str = ""
    port: int | None = None
    stream: str | None = None


class CameraIn(BaseModel):
    name: str
    kind: str = "stream"
    url: str | None = None
    nvr: NvrIn | None = None
    purpose: str | None = None


class CameraPatch(BaseModel):
    name: str | None = None
    purpose: str | None = None
    zones: list[dict] | None = None
    enabled: bool | None = None
    tuning: dict | None = None


def _purpose(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in purposes():
        raise HTTPException(400, "Pick one of the listed purposes.")
    return value


def _ffprobe(path: Path) -> dict | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json",
                        str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout or "{}")
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        return None
    return data


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    manager = ctx.manager
    hub, tokens = push_parts(ctx)

    def repo(member: CurrentMember):
        return ctx.repo_for(member.workspace.id)

    @router.get("/api/cameras")
    def cameras(member: CurrentMember = Depends(current_member)):
        return [camera_json(manager, c) for c in repo(member).list_cameras()]

    @router.get("/api/nvr")
    def nvr_brands(member: CurrentMember = Depends(current_member)):
        return {"brands": nvr.brands(), "default_stream": nvr.templates()["default_stream"]}

    @router.post("/api/cameras")
    def add_camera(body: CameraIn, member: CurrentMember = Depends(require_role("admin"))):
        if body.kind not in USER_KINDS:
            raise HTTPException(400, "A camera is a stream the server reads, or a device that sends its own pictures.")
        purpose = _purpose(body.purpose)
        url = body.url
        if body.nvr is not None:
            try:
                url = nvr.build_url(**body.nvr.model_dump())
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
        if body.kind == "stream" or url is not None:
            # a bridge camera's address is on the society's network and only the bridge reads it
            problem = check_url(url or "", allow_private=body.kind != "stream" or ctx.settings.allow_private_streams)
            if problem:
                raise HTTPException(400, problem)
        cam = repo(member).add_camera(Camera(None, body.name, body.kind, url, purpose=purpose or "other",
                                             enabled=False))
        refused = start_watching(ctx.db, member.workspace.id, cam.id)  # over the plan: added, but not watched
        cam.enabled = refused is None
        ctx.db.audit(member.workspace.id, member.user.id, "camera.add",
                     {"camera": cam.id, "name": cam.name, "kind": cam.kind, "nvr": body.nvr.brand if body.nvr else None,
                      "watched": cam.enabled})
        if body.kind == "stream" and cam.enabled:
            manager.start(cam.id, member.workspace.id)
        return {**camera_json(manager, cam), "notice": refused}

    @router.get("/api/cameras/{camera_id}")
    def get_camera(camera_id: str, member: CurrentMember = Depends(current_member)):
        return camera_json(manager, camera_or_404(repo(member), camera_id))

    @router.delete("/api/cameras/{camera_id}")
    def delete_camera(camera_id: str, member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        manager.stop(cam.id)
        pictures = [key for _, _, _, key in r.list_state_examples(cam.id)]
        tokens.revoke(member.workspace.id, cam.id)
        hub.close(cam.id, CLOSE_GONE)
        r.delete_camera(cam.id)
        for key in pictures:
            ctx.blobs.delete(key)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.delete", {"camera": cam.id, "name": cam.name})
        if cam.kind == "file" and cam.url and Path(cam.url).resolve().is_relative_to(
                (Path(ctx.settings.data_dir) / "uploads").resolve()):
            Path(cam.url).unlink(missing_ok=True)
        return {"ok": True}

    @router.patch("/api/cameras/{camera_id}")
    def patch_camera(camera_id: str, body: CameraPatch, member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        if body.enabled and not cam.enabled and (refused := start_watching(ctx.db, member.workspace.id, cam.id)):
            raise HTTPException(403, refused)
        if body.name is not None:
            cam.name = body.name
        if body.purpose is not None:
            cam.purpose = _purpose(body.purpose)
        if body.zones is not None:
            bare = next((z["name"] for z in body.zones if bare_age_name(str(z.get("name") or ""))), None)
            if bare:
                raise HTTPException(400, f"Name the place, not who uses it: {bare} could be read as an age. "
                                         "For example, kids play area.")
            cam.zones = [Zone(z.get("id") or new_id("zone"), z["name"], [tuple(p) for p in z["polygon"]],
                              z.get("kind") or "area") for z in body.zones]
            if cam.kind == "demo":
                r.delete_camera_events(cam.id)  # the next loop records the timeline with the new zones
        if body.enabled is not None:
            cam.enabled = body.enabled
        if body.tuning is not None:
            load_tuning(None, body.tuning)
            cam.tuning = body.tuning
        r.update_camera(cam)
        disabled = []
        if body.zones is not None:
            kinds = {z.name: z.kind for z in cam.zones}
            for row in r.list_watches(cam.id):
                watch = Watch.model_validate(row.watch)
                if row.enabled and watch.scope == "camera" and row.camera_id == cam.id and validate(watch, kinds):
                    row.enabled = False  # it points at a zone that no longer exists
                    r.update_watch(row)
                    disabled.append(watch.name)
        changed = sorted(k for k, v in body.model_dump(exclude_unset=True).items() if v is not None)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.edit",
                     {"camera": cam.id, "name": cam.name, "changed": changed, "enabled": cam.enabled,
                      "disabled_watches": disabled})
        if not cam.enabled:
            hub.close(cam.id, CLOSE_OFF)
        if cam.kind in LIVE_KINDS:
            manager.restart(cam.id) if cam.enabled else manager.stop(cam.id)
        return {**camera_json(manager, cam), "disabled_watches": disabled}

    @router.get("/api/cameras/{camera_id}/frame")
    def camera_frame(camera_id: str, member: CurrentMember = Depends(current_member)):
        return Response(frame(camera_or_404(repo(member), camera_id)), media_type="image/jpeg")

    def frame(cam: Camera) -> bytes:
        image = None
        if cam.kind == "webcam":
            latest = hub.latest(cam.id)
            if latest is None:
                raise HTTPException(404, "No picture from this camera yet.")
            return latest[1]
        if cam.kind in ("file", "demo") and cam.url:
            if not inside(cam.url, clip_roots(ctx.settings.data_dir)):
                raise HTTPException(404, "No picture from this camera yet.")
            cap = cv2.VideoCapture(cam.url)
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
            ok, image = cap.read()
            cap.release()
        else:
            sub = manager.subscribe(cam.id)
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    msg = sub.get(1.0)
                    if msg and msg.get("type") == "frame":
                        return msg["jpeg"]
            finally:
                manager.unsubscribe(cam.id, sub)
        if image is None:
            raise HTTPException(404, "No picture from this camera yet.")
        return cv2.imencode(".jpg", image)[1].tobytes()

    @router.post("/api/cameras/{camera_id}/zones/{zone_id}/examples")
    def add_state_example(camera_id: str, zone_id: str, state: str = Form(...), file: UploadFile | None = File(None),
                          member: CurrentMember = Depends(require_role("admin"))):
        """A picture of a zone in a named state (gate open), from an upload or the camera's picture right now."""
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        if not any(z.id == zone_id and not z.is_line for z in cam.zones):
            raise HTTPException(404, "No such zone on this camera.")
        if not state.strip():
            raise HTTPException(400, "Name the state, for example open or closed.")
        data = file.file.read() if file is not None else frame(cam)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "That picture can't be read.")
        period = "night" if is_night(image, ctx.tuning.zones) else "day"
        key = example_key(member.workspace.id, cam.id, zone_id, period, time.time())
        ctx.blobs.put(key, cv2.imencode(".jpg", image)[1].tobytes(), "image/jpeg")
        example_id = r.add_state_example(cam.id, zone_id, state, key)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.example_add",
                     {"camera": cam.id, "zone": zone_id, "state": state.strip().lower(), "example": example_id})
        if cam.kind in LIVE_KINDS:
            manager.restart(cam.id)
        return {"id": example_id, "zone_id": zone_id, "state": state.strip().lower(), "period": period,
                "url": ctx.blobs.url(key)}

    @router.get("/api/cameras/{camera_id}/examples")
    def state_examples(camera_id: str, member: CurrentMember = Depends(current_member)):
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        return [{"id": i, "zone_id": z, "state": st, "url": ctx.blobs.url(key)}
                for i, z, st, key in r.list_state_examples(cam.id)]

    @router.delete("/api/examples/{example_id}")
    def delete_state_example(example_id: str, member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        gone = r.delete_state_example(example_id)
        if gone is None:
            raise HTTPException(404, "No such picture.")
        camera_id, key = gone
        ctx.blobs.delete(key)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.example_delete",
                     {"camera": camera_id, "example": example_id})
        cam = r.get_camera(camera_id)
        if cam is not None and cam.kind in LIVE_KINDS:
            manager.restart(cam.id)  # the zone's state is learned again without that picture
        return {"ok": True}

    # device links: a laptop, phone or the bridge sends this camera's frames with a token (api/live.py /ws/cam)

    def pushed_camera(member: CurrentMember, camera_id: str) -> Camera:
        cam = camera_or_404(repo(member), camera_id)
        if cam.kind != "webcam":
            raise HTTPException(400, "Only a camera that sends its own pictures has a device link.")
        return cam

    @router.post("/api/cameras/{camera_id}/device-token")
    def issue_device_token(camera_id: str, member: CurrentMember = Depends(require_role("admin"))):
        """A new device link for this camera, shown once; the earlier link stops working and its socket closes."""
        cam = pushed_camera(member, camera_id)
        token = tokens.issue(member.workspace.id, cam.id)
        hub.close(cam.id, CLOSE_GONE)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.device_link", {"camera": cam.id, "name": cam.name})
        url = f"{ctx.settings.public_base_url.rstrip('/')}/cam/{token}"
        body = {"token": token, "url": url, "qr_svg": qr_svg(url), "qr_url": f"/cam/{token}/qr.svg",
                **(tokens.info(member.workspace.id, cam.id) or {})}
        if cam.url:
            body["bridge_camera"] = f"{token}={cam.url}"  # the bridge's --camera value, with the recorder's password
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @router.get("/api/cameras/{camera_id}/device-token")
    def device_token_info(camera_id: str, member: CurrentMember = Depends(current_member)):
        cam = pushed_camera(member, camera_id)
        info = tokens.info(member.workspace.id, cam.id)
        return {"linked": info is not None, **(info or {}), "sending": hub.sending(cam.id)}

    @router.delete("/api/cameras/{camera_id}/device-token")
    def remove_device_token(camera_id: str, member: CurrentMember = Depends(require_role("admin"))):
        cam = pushed_camera(member, camera_id)
        removed = tokens.revoke(member.workspace.id, cam.id)
        hub.close(cam.id, CLOSE_GONE)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.device_unlink", {"camera": cam.id, "name": cam.name})
        return {"ok": True, "removed": removed}

    @router.post("/api/cameras/{camera_id}/scan")
    def rescan(camera_id: str, member: CurrentMember = Depends(require_role("admin"))):
        cam = camera_or_404(repo(member), camera_id)
        if cam.kind != "file":
            raise HTTPException(400, "Only uploaded videos can be scanned again.")
        return {"scan_id": manager.scan_upload(cam.id, member.workspace.id)}

    @router.post("/api/uploads")
    async def upload(request: Request, file: UploadFile = File(...), name: str = Form(""),
                     member: CurrentMember = Depends(require_role("admin"))):
        s = ctx.settings
        folder = Path(s.data_dir) / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{new_id('vid')}{Path(file.filename or 'video.mp4').suffix.lower()[:6] or '.mp4'}"
        limit, size = s.max_upload_mb * 1024 * 1024, 0
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(400, f"Videos can be up to {s.max_upload_mb} MB.")
                out.write(chunk)
        probe = await run_in_threadpool(_ffprobe, dest)
        if probe is None:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "That file is not a video we can read.")
        try:
            duration = float(probe.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            duration = None
        if duration is None or duration <= 0 or duration > s.max_upload_minutes * 60:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"Videos can be up to {s.max_upload_minutes} minutes long, and we need to "
                                     "be able to read how long this one is.")
        cam = repo(member).add_camera(Camera(None, name or file.filename or "Upload", "file", str(dest)))
        ctx.db.audit(member.workspace.id, member.user.id, "camera.upload", {"camera": cam.id, "name": cam.name})
        return {"camera_id": cam.id, "scan_id": manager.scan_upload(cam.id, member.workspace.id)}

    def own_scan(member: CurrentMember, info: dict | None) -> bool:
        return info is not None and repo(member).get_camera(info.get("camera_id", "")) is not None

    @router.get("/api/scans/{scan_id}")
    def scan_status(scan_id: str, member: CurrentMember = Depends(current_member)):
        info = manager.scan(scan_id)
        if not own_scan(member, info):
            raise HTTPException(404, "No such scan.")
        return {k: v for k, v in info.items() if k != "workspace_id"}

    @router.get("/api/metrics")
    def metrics(member: CurrentMember = Depends(current_member)):
        r = repo(member)
        by_status: dict[str, int] = {}
        for a in r.list_alerts(limit=1000):
            by_status[a.status] = by_status.get(a.status, 0) + 1
        scans = [s for s in getattr(manager, "scans", {}).values() if s.get("stats") and own_scan(member, s)]
        return {"cameras": [camera_json(manager, c) for c in r.list_cameras()],
                "ai_calls_today": r.count_ai_calls_today(),
                "ai_daily_limit": daily_ai_cap(r, ctx.settings.ai_daily_call_limit),
                "alerts": by_status, "scans": [s["stats"] for s in scans][-20:]}

    return router

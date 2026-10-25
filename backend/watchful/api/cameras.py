"""Cameras, uploaded videos and their scans, and the numbers page. Reading needs any role; changing cameras needs an
admin."""
import json
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from watchful.api.common import LIVE_KINDS, camera_json, camera_or_404
from watchful.auth.deps import CurrentMember, current_member, require_role
from watchful.rules.engine import validate
from watchful.rules.watch import Watch
from watchful.rules.zonestate import example_key, is_night
from watchful.store.db import Camera, new_id
from watchful.tuning import load_tuning
from watchful.vision.zones import Zone


class CameraIn(BaseModel):
    name: str
    kind: str = "stream"
    url: str | None = None


class CameraPatch(BaseModel):
    name: str | None = None
    zones: list[dict] | None = None
    enabled: bool | None = None
    tuning: dict | None = None


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

    def repo(member: CurrentMember):
        return ctx.repo_for(member.workspace.id)

    @router.get("/api/cameras")
    def cameras(member: CurrentMember = Depends(current_member)):
        return [camera_json(manager, c) for c in repo(member).list_cameras()]

    @router.post("/api/cameras")
    def add_camera(body: CameraIn, member: CurrentMember = Depends(require_role("admin"))):
        if body.kind not in LIVE_KINDS:
            raise HTTPException(400, "A camera is a stream, a webcam or a demo.")
        if body.kind == "stream" and not (body.url or "").startswith(("rtsp://", "rtsps://", "http://", "https://")):
            raise HTTPException(400, "Paste an RTSP or HTTP stream address.")
        cam = repo(member).add_camera(Camera(None, body.name, body.kind, body.url))
        ctx.db.audit(member.workspace.id, member.user.id, "camera.add", {"camera": cam.id, "name": cam.name})
        if body.kind != "webcam":
            manager.start(cam.id, member.workspace.id)
        return camera_json(manager, cam)

    @router.get("/api/cameras/{camera_id}")
    def get_camera(camera_id: str, member: CurrentMember = Depends(current_member)):
        return camera_json(manager, camera_or_404(repo(member), camera_id))

    @router.delete("/api/cameras/{camera_id}")
    def delete_camera(camera_id: str, member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        manager.stop(cam.id)
        r.delete_camera(cam.id)
        ctx.db.audit(member.workspace.id, member.user.id, "camera.delete", {"camera": cam.id, "name": cam.name})
        if cam.kind == "file" and cam.url and Path(cam.url).resolve().is_relative_to(
                (Path(ctx.settings.data_dir) / "uploads").resolve()):
            Path(cam.url).unlink(missing_ok=True)
        return {"ok": True}

    @router.patch("/api/cameras/{camera_id}")
    def patch_camera(camera_id: str, body: CameraPatch, member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        if body.name is not None:
            cam.name = body.name
        if body.zones is not None:
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
            names = {z.name for z in cam.zones}
            for row in r.list_watches(cam.id):
                watch = Watch.model_validate(row.watch)
                if row.enabled and watch.scope == "camera" and row.camera_id == cam.id and validate(watch, names):
                    row.enabled = False  # it points at a zone that no longer exists
                    r.update_watch(row)
                    disabled.append(watch.name)
        if cam.kind in LIVE_KINDS:
            manager.restart(cam.id) if cam.enabled else manager.stop(cam.id)
        return {**camera_json(manager, cam), "disabled_watches": disabled}

    @router.get("/api/cameras/{camera_id}/frame")
    def camera_frame(camera_id: str, member: CurrentMember = Depends(current_member)):
        return Response(frame(camera_or_404(repo(member), camera_id)), media_type="image/jpeg")

    def frame(cam: Camera) -> bytes:
        image = None
        if cam.kind in ("file", "demo") and cam.url:
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
        key = example_key(cam.id, zone_id, period, time.time())
        ctx.blobs.put(key, cv2.imencode(".jpg", image)[1].tobytes(), "image/jpeg")
        example_id = r.add_state_example(cam.id, zone_id, state, key)
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
        if not repo(member).delete_state_example(example_id):
            raise HTTPException(404, "No such picture.")
        return {"ok": True}

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
                "ai_calls_today": r.count_ai_calls_today(), "ai_daily_limit": ctx.settings.ai_daily_call_limit,
                "alerts": by_status, "scans": [s["stats"] for s in scans][-20:]}

    return router

"""HTTP and WebSocket API. Every part comes from settings through components, so it can be swapped."""
import asyncio
import base64
import json
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocketDisconnect

from watchful.agent.ask import AskContext, ask, people_groups
from watchful.agent.compiler import compile_watch
from watchful.components import make_blobs, make_detector, make_embedder, make_faces, make_llm, make_repo
from watchful.config import Settings, get_settings
from watchful.rules.engine import validate
from watchful.rules.presets import describe, preset
from watchful.rules.watch import SameAs, Watch
from watchful.search import SearchQuery, search
from watchful.store.db import Camera, WatchRow, new_id
from watchful.tuning import load_tuning
from watchful.vision.faces import ConsentRequired, Enrollment, NoFaceFound
from watchful.vision.scan import cut_clip
from watchful.vision.zones import Zone

COOKIE = "watchful_session"
SESSION_DAYS = 14
TIMELINE_KINDS = ["enter", "exit", "falling", "fallen", "recovered", "tamper_covered", "tamper_blurred",
                  "tamper_moved", "tamper_restored"]
LIVE_KINDS = {"stream", "webcam", "demo"}
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class State:
    """Everything the routes share."""

    def __init__(self, settings: Settings, llm=None, manager=None):
        self.settings = settings
        self.tuning = load_tuning(settings.tuning_file)
        self.repo = make_repo(settings)
        self.blobs = make_blobs(settings)
        self.compile_llm = llm or make_llm(settings, self.repo, "compile")
        self.ask_llm = llm or make_llm(settings, self.repo, "ask")
        self.search_llm = llm or make_llm(settings, self.repo, "verify")
        if manager is None:
            from watchful.workers import WorkerManager
            manager = WorkerManager(settings, self.tuning, self.repo, self.blobs)
        self.manager = manager
        self._faces = None
        self._embedder = None
        self.pushers: dict[str, set[int]] = {}
        self._detector = None
        self.signer = URLSafeTimedSerializer(settings.session_secret, salt="watchful")

    @property
    def faces(self):
        if self._faces is None:
            self._faces = make_faces(self.settings, self.tuning)
            self._faces.load(self.repo.list_enrollments())
        return self._faces

    @property
    def person_detector(self):
        if self._detector is None:
            self._detector = make_detector(self.settings, self.tuning, {"person"})
        return self._detector

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = make_embedder(self.settings)
        return self._embedder


def _w(request: Request) -> State:
    return request.app.state.w


def _user(request: Request) -> str:
    token = request.cookies.get(COOKIE)
    if token:
        try:
            return _w(request).signer.loads(token, max_age=SESSION_DAYS * 86400)
        except BadSignature:
            pass
    raise HTTPException(401, "Sign in first.")


def alert_json(blobs, a) -> dict:
    """An alert with a picture link that works on any storage (local disk or presigned S3)."""
    return {**asdict(a), "keyframe_url": blobs.url(a.keyframe_key) if a.keyframe_key else None}


def _camera_json(w: State, cam: Camera) -> dict:
    st = w.manager.status(cam.id)
    return {**asdict(cam), "status": {"running": st.running, "source": st.source_status, "fps": round(st.fps, 1),
                                      "loops": st.loops, "stats": st.stats, "problems": st.problems}}


def _ffprobe(path: Path) -> dict | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json",
                        str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout or "{}")
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        return None
    return data


class LoginIn(BaseModel):
    user: str
    password: str


class CameraIn(BaseModel):
    name: str
    kind: str = "stream"
    url: str | None = None


class CameraPatch(BaseModel):
    name: str | None = None
    zones: list[dict] | None = None
    enabled: bool | None = None
    tuning: dict | None = None


class WatchIn(BaseModel):
    text: str | None = None
    preset: str | None = None
    fill: dict = {}


class AskIn(BaseModel):
    question: str
    camera_id: str | None = None


class LookoutIn(BaseModel):
    camera_ids: list[str]
    person_id: str | None = None
    description: str | None = None
    event_id: str | None = None
    alert_id: str | None = None
    hours: float = 24


def create_app(settings: Settings | None = None, llm=None, manager=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        w = app.state.w
        for cam in w.repo.list_cameras():
            if cam.enabled and cam.kind in LIVE_KINDS - {"webcam"}:
                w.manager.start(cam.id)
        yield
        w.manager.shutdown()

    app = FastAPI(title="Watchful", lifespan=lifespan)
    app.state.w = State(settings, llm, manager)
    auth = [Depends(_user)]

    @app.get("/healthz")
    def healthz():
        info = cv2.getBuildInformation()
        return {"ok": True, "opencv": cv2.__version__, "kleidicv": "KleidiCV" in info or "kleidicv" in info.lower()}

    @app.post("/api/login")
    def login(body: LoginIn, response: Response, request: Request):
        s = _w(request).settings
        if not (secrets.compare_digest(body.user, s.demo_user) and secrets.compare_digest(body.password, s.demo_password)):
            raise HTTPException(401, "That name or password is wrong.")
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, _w(request).signer.dumps(body.user), max_age=SESSION_DAYS * 86400,
                            httponly=True, samesite="lax", secure=secure)
        return {"user": body.user, "timezone": s.timezone}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me", dependencies=auth)
    def me(request: Request, user: str = Depends(_user)):
        return {"user": user, "timezone": _w(request).settings.timezone}

    # cameras
    @app.get("/api/cameras", dependencies=auth)
    def cameras(request: Request):
        w = _w(request)
        return [_camera_json(w, c) for c in w.repo.list_cameras()]

    @app.post("/api/cameras", dependencies=auth)
    def add_camera(body: CameraIn, request: Request):
        w = _w(request)
        if body.kind not in LIVE_KINDS:
            raise HTTPException(400, "A camera is a stream, a webcam or a demo.")
        if body.kind == "stream" and not (body.url or "").startswith(("rtsp://", "rtsps://", "http://", "https://")):
            raise HTTPException(400, "Paste an RTSP or HTTP stream address.")
        cam = w.repo.add_camera(Camera(None, body.name, body.kind, body.url))
        if body.kind != "webcam":
            w.manager.start(cam.id)
        return _camera_json(w, cam)

    def _camera(w: State, camera_id: str) -> Camera:
        cam = w.repo.get_camera(camera_id)
        if cam is None:
            raise HTTPException(404, "No such camera.")
        return cam

    @app.get("/api/cameras/{camera_id}", dependencies=auth)
    def get_camera(camera_id: str, request: Request):
        w = _w(request)
        return _camera_json(w, _camera(w, camera_id))

    @app.delete("/api/cameras/{camera_id}", dependencies=auth)
    def delete_camera(camera_id: str, request: Request):
        w = _w(request)
        cam = _camera(w, camera_id)
        w.manager.stop(cam.id)
        w.repo.delete_camera(cam.id)
        if cam.kind == "file" and cam.url and Path(cam.url).resolve().is_relative_to(
                (Path(w.settings.data_dir) / "uploads").resolve()):
            Path(cam.url).unlink(missing_ok=True)
        return {"ok": True}

    @app.patch("/api/cameras/{camera_id}", dependencies=auth)
    def patch_camera(camera_id: str, body: CameraPatch, request: Request):
        w = _w(request)
        cam = _camera(w, camera_id)
        if body.name is not None:
            cam.name = body.name
        if body.zones is not None:
            cam.zones = [Zone(z.get("id") or new_id("zone"), z["name"], [tuple(p) for p in z["polygon"]])
                         for z in body.zones]
            if cam.kind == "demo":
                w.repo.delete_camera_events(cam.id)  # the next loop records the timeline with the new zones
        if body.enabled is not None:
            cam.enabled = body.enabled
        if body.tuning is not None:
            load_tuning(None, body.tuning)
            cam.tuning = body.tuning
        w.repo.update_camera(cam)
        disabled = []
        if body.zones is not None:
            names = {z.name for z in cam.zones}
            for row in w.repo.list_watches(cam.id):
                watch = Watch.model_validate(row.watch)
                if row.enabled and validate(watch, names):
                    row.enabled = False  # it points at a zone that no longer exists
                    w.repo.update_watch(row)
                    disabled.append(watch.name)
        if cam.kind in LIVE_KINDS:
            w.manager.restart(cam.id) if cam.enabled else w.manager.stop(cam.id)
        return {**_camera_json(w, cam), "disabled_watches": disabled}

    @app.get("/api/cameras/{camera_id}/frame", dependencies=auth)
    def camera_frame(camera_id: str, request: Request):
        w = _w(request)
        cam = _camera(w, camera_id)
        image = None
        if cam.kind in ("file", "demo") and cam.url:
            cap = cv2.VideoCapture(cam.url)
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
            ok, image = cap.read()
            cap.release()
        else:
            sub = w.manager.subscribe(camera_id)
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    msg = sub.get(1.0)
                    if msg and msg.get("type") == "frame":
                        return Response(msg["jpeg"], media_type="image/jpeg")
            finally:
                w.manager.unsubscribe(camera_id, sub)
        if image is None:
            raise HTTPException(404, "No picture from this camera yet.")
        return Response(cv2.imencode(".jpg", image)[1].tobytes(), media_type="image/jpeg")

    @app.post("/api/cameras/{camera_id}/scan", dependencies=auth)
    def rescan(camera_id: str, request: Request):
        w = _w(request)
        cam = _camera(w, camera_id)
        if cam.kind != "file":
            raise HTTPException(400, "Only uploaded videos can be scanned again.")
        return {"scan_id": w.manager.scan_upload(cam.id)}

    # uploads
    @app.post("/api/uploads", dependencies=auth)
    async def upload(request: Request, file: UploadFile = File(...), name: str = Form("")):
        w = _w(request)
        s = w.settings
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
        cam = w.repo.add_camera(Camera(None, name or file.filename or "Upload", "file", str(dest)))
        return {"camera_id": cam.id, "scan_id": w.manager.scan_upload(cam.id)}

    @app.get("/api/scans/{scan_id}", dependencies=auth)
    def scan_status(scan_id: str, request: Request):
        info = _w(request).manager.scan(scan_id)
        if info is None:
            raise HTTPException(404, "No such scan.")
        return info

    # watches
    @app.get("/api/presets", dependencies=auth)
    def presets():
        return describe()

    @app.get("/api/cameras/{camera_id}/watches", dependencies=auth)
    def watches(camera_id: str, request: Request):
        return [asdict(x) for x in _w(request).repo.list_watches(camera_id)]

    def _save_watch(w: State, cam: Camera, text: str, watch: Watch) -> WatchRow:
        row = w.repo.add_watch(WatchRow(None, cam.id, text, watch.model_dump(), True))
        if cam.kind in LIVE_KINDS:
            w.manager.restart(cam.id)
        return row

    @app.post("/api/cameras/{camera_id}/watches", dependencies=auth)
    def add_watch(camera_id: str, body: WatchIn, request: Request):
        w = _w(request)
        cam = _camera(w, camera_id)
        if body.preset:
            try:
                watch = preset(body.preset, **body.fill)
            except (KeyError, ValueError) as e:
                raise HTTPException(400, f"That preset can't be used: {e}")
            text = body.preset
        else:
            people = {e.person_id: e.name for e in w.repo.list_enrollments()}
            result = compile_watch(body.text or "", [z.name for z in cam.zones], w.compile_llm, people)
            if result.watch is None:
                return {"watch": None, "problem": result.problem}
            watch, text = result.watch, body.text or ""
        row = _save_watch(w, cam, text, watch)
        return {"id": row.id, "watch": row.watch, "problem": None}

    @app.patch("/api/watches/{watch_id}", dependencies=auth)
    def toggle_watch(watch_id: str, request: Request, enabled: bool = Body(..., embed=True)):
        w = _w(request)
        row = next((x for x in w.repo.list_watches() if x.id == watch_id), None)
        if row is None:
            raise HTTPException(404, "No such watch.")
        row.enabled = enabled
        w.repo.update_watch(row)
        w.manager.restart(row.camera_id)
        return asdict(row)

    # alerts
    @app.get("/api/alerts", dependencies=auth)
    def alerts(request: Request, camera_id: str | None = None, include_dismissed: bool = False):
        w = _w(request)
        rows = w.repo.list_alerts(camera_id=camera_id, limit=200)
        return [alert_json(w.blobs, a) for a in rows if include_dismissed or a.status != "dismissed"]

    def _alert(w: State, alert_id: str):
        a = w.repo.get_alert(alert_id)
        if a is None:
            raise HTTPException(404, "No such alert.")
        return a

    @app.post("/api/alerts/{alert_id}/feedback", dependencies=auth)
    def feedback(alert_id: str, request: Request, value: str = Body(..., embed=True)):
        w = _w(request)
        a = _alert(w, alert_id)
        if value not in ("up", "down"):
            raise HTTPException(400, "Feedback is up or down.")
        a.feedback = value
        w.repo.update_alert(a)
        return {"ok": True}

    @app.post("/api/alerts/{alert_id}/approve", dependencies=auth)
    def approve(alert_id: str, request: Request):
        w = _w(request)
        _alert(w, alert_id)
        return {"notified": w.manager.approve(alert_id)}

    @app.post("/api/alerts/{alert_id}/decline", dependencies=auth)
    def decline(alert_id: str, request: Request):
        w = _w(request)
        a = _alert(w, alert_id)
        a.status = "declined"
        w.repo.update_alert(a)
        return {"ok": True}

    @app.get("/api/alerts/{alert_id}/clip", dependencies=auth)
    def clip(alert_id: str, request: Request):
        w = _w(request)
        a = _alert(w, alert_id)
        key = a.clip_key or f"{a.camera_id}/clips/{a.fingerprint}.mp4"
        try:
            w.blobs.get(key)
        except (OSError, KeyError):
            cam = w.repo.get_camera(a.camera_id)
            if cam is None or cam.kind != "file":
                raise HTTPException(404, "The clip is still being written. Try again in a few seconds.")
            start = (cam.last_scan or {}).get("start_epoch")
            if start is None:
                raise HTTPException(404, "Scan this video again to make its clips.")
            data = cut_clip(cam.url, max(0.0, a.ts - start), w.settings, w.tuning)
            w.blobs.put(key, data, "video/mp4")
            a.clip_key = key
            w.repo.update_alert(a)
        return RedirectResponse(w.blobs.url(key))

    # questions, timeline, people, search, lookouts
    def _ask_ctx(w: State, camera_id: str | None) -> AskContext:
        return AskContext(w.repo, w.blobs, camera_id, w.settings.timezone, w.tuning.appearance)

    @app.post("/api/ask", dependencies=auth)
    def ask_route(body: AskIn, request: Request):
        w = _w(request)
        a = ask(body.question, body.camera_id, _ask_ctx(w, body.camera_id), w.ask_llm, now=time.time())
        return {**asdict(a), "trace": [asdict(t) for t in a.trace]}

    @app.get("/api/cameras/{camera_id}/timeline", dependencies=auth)
    def timeline(camera_id: str, request: Request, since: float | None = None, until: float | None = None):
        w = _w(request)
        events = w.repo.query_events(camera_id=camera_id, kinds=TIMELINE_KINDS, since=since, until=until, limit=2000)
        people = people_groups(_ask_ctx(w, camera_id), since, until)
        for g in people:
            g["thumbnail_url"] = w.blobs.url(g.pop("thumbnail_key")) if g.get("thumbnail_key") else None
        return {"events": [{**asdict(e), "signature": None, "embedding": None,
                            "thumbnail_url": w.blobs.url(e.keyframe_key) if e.keyframe_key else None}
                           for e in events], "people": people}

    @app.post("/api/search", dependencies=auth)
    async def search_route(request: Request):
        """Find anything: words, or a photo (a photo of a person needs their consent)."""
        w = _w(request)
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            photo = await form["photo"].read() if "photo" in form else None
            q = SearchQuery(text=form.get("text") or None, photo=photo, camera_id=form.get("camera_id") or None,
                            consent=str(form.get("consent", "")).lower() == "true")
        else:
            data = await request.json()
            q = SearchQuery(text=data.get("text"), person_id=data.get("person_id"), camera_id=data.get("camera_id"),
                            since=data.get("since"), until=data.get("until"), classes=data.get("classes") or [])
        if q.text is None and q.photo is None and q.person_id is None:
            raise HTTPException(400, "Type what to look for, or add a photo.")
        try:
            r = await run_in_threadpool(search, q, w.repo, w.blobs, w.embedder, w.search_llm,
                                        w.faces if q.photo is not None or q.person_id else None, w.tuning.signals, None,
                                        w.settings.timezone, w.person_detector if q.photo is not None else None)
        except ConsentRequired:
            raise HTTPException(400, "Searching with a photo of a person needs their consent.")
        except (NoFaceFound, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"query": r.query, "problem": r.problem, "results": [asdict(h) for h in r.results]}

    @app.get("/api/people", dependencies=auth)
    def people(request: Request):
        w = _w(request)
        return [{"person_id": e.person_id, "name": e.name, "consent_ts": e.consent_ts,
                 "thumbnail_url": w.blobs.url(e.thumbnail_key) if e.thumbnail_key else None}
                for e in w.repo.list_enrollments()]

    @app.post("/api/people", dependencies=auth)
    async def enroll(request: Request, name: str = Form(...), consent: str = Form("false"),
                     photo: UploadFile | None = File(None), event_id: str = Form("")):
        w = _w(request)
        if consent.lower() != "true":
            raise HTTPException(400, "Enrolling someone needs their consent.")
        if photo is not None:
            data = await photo.read()
        elif event_id and (e := w.repo.get_event(event_id)) and e.keyframe_key:
            data = w.blobs.get(e.keyframe_key)
        else:
            raise HTTPException(400, "Add a photo or pick a sighting.")
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "That picture can't be read.")
        person_id = new_id("per")
        key = f"people/{person_id}.jpg"
        try:
            e = w.faces.enroll(person_id, name, image, consent=True, thumbnail_key=key)
        except NoFaceFound:
            raise HTTPException(400, "No face found in that picture.")
        w.blobs.put(key, data, "image/jpeg")
        w.repo.add_enrollment(e)
        for cam in w.repo.list_cameras():
            if cam.kind in LIVE_KINDS:
                await run_in_threadpool(w.manager.restart, cam.id)  # restarts join processes; keep the loop free
        return {"person_id": person_id, "name": name}

    @app.delete("/api/people/{person_id}", dependencies=auth)
    def forget(person_id: str, request: Request):
        w = _w(request)
        w.repo.delete_enrollment(person_id)
        w.faces.remove(person_id)
        w.blobs.delete(f"people/{person_id}.jpg")
        for cam in w.repo.list_cameras():
            if cam.kind in LIVE_KINDS:
                w.manager.restart(cam.id)
        return {"ok": True}

    def _alert_sighting(w: State, alert_id: str):
        """The best recorded sighting of the person an alert is about."""
        a = _alert(w, alert_id)
        if a.track_id is None:
            return None
        rows = [e for e in w.repo.query_events(camera_id=a.camera_id, since=a.ts - 3600, until=a.ts + 60, limit=5000)
                if e.track_id == a.track_id]
        return max(rows, key=lambda e: (e.embedding is not None, e.signature is not None), default=None)

    @app.post("/api/lookouts", dependencies=auth)
    def lookout(body: LookoutIn, request: Request):
        w = _w(request)
        fill: dict = {}
        if body.person_id:
            fill["who"] = body.person_id
        elif body.description:
            fill["text"] = body.description
        elif body.event_id or body.alert_id:
            e = w.repo.get_event(body.event_id) if body.event_id else _alert_sighting(w, body.alert_id)
            if e is None:
                raise HTTPException(404, "No such sighting.")
            fill["same_as"] = SameAs(event_id=e.id, signature=e.signature, embedding=e.embedding,
                                     crop_key=e.keyframe_key,
                                     expires_at=time.time() + min(max(body.hours, 1), 24) * 3600).model_dump()
        else:
            raise HTTPException(400, "Pick a person, a description or a sighting.")
        ids = []
        for cid in body.camera_ids:
            cam = _camera(w, cid)
            ids.append(_save_watch(w, cam, "lookout", preset("lookout", **fill)).id)
        return {"watch_ids": ids}

    @app.get("/api/metrics", dependencies=auth)
    def metrics(request: Request):
        w = _w(request)
        all_alerts = w.repo.list_alerts(limit=1000)
        by_status: dict[str, int] = {}
        for a in all_alerts:
            by_status[a.status] = by_status.get(a.status, 0) + 1
        scans = [s for s in getattr(w.manager, "scans", {}).values() if s.get("stats")]
        return {"cameras": [_camera_json(w, c) for c in w.repo.list_cameras()],
                "ai_calls_today": w.repo.count_ai_calls_today(), "ai_daily_limit": w.settings.ai_daily_call_limit,
                "alerts": by_status, "scans": [s["stats"] for s in scans][-20:]}

    @app.get("/blobs/{key:path}", dependencies=auth)
    def blob(key: str, request: Request):
        root = (Path(_w(request).settings.data_dir) / "blobs").resolve()
        path = (root / key).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(path)

    # live view and webcam
    @app.websocket("/ws/cameras/{camera_id}")
    async def live(ws: WebSocket, camera_id: str):
        w = ws.app.state.w
        await ws.accept()
        try:
            w.signer.loads(ws.cookies.get(COOKIE, ""), max_age=SESSION_DAYS * 86400)
        except BadSignature:
            await ws.close(code=4401)
            return
        sub = w.manager.subscribe(camera_id)
        incoming = asyncio.create_task(ws.receive())
        try:
            while True:
                if incoming.done():
                    if incoming.result().get("type") == "websocket.disconnect":
                        break
                    incoming = asyncio.create_task(ws.receive())  # ignore anything the browser sends
                msg = sub.get(0)
                if msg is None:
                    # wait without holding a server thread, and notice a closed tab at once
                    await asyncio.wait({incoming}, timeout=0.05)
                    continue
                if msg["type"] == "frame":
                    await ws.send_json({"type": "frame", "ts": msg["ts"], "width": msg.get("width"),
                                        "height": msg.get("height"), "tracks": msg.get("tracks", []),
                                        "jpeg_b64": base64.b64encode(msg["jpeg"]).decode()})
                else:
                    await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            incoming.cancel()
            w.manager.unsubscribe(camera_id, sub)

    @app.websocket("/ws/cameras/{camera_id}/push")
    async def push(ws: WebSocket, camera_id: str):
        w = ws.app.state.w
        await ws.accept()
        try:
            w.signer.loads(ws.cookies.get(COOKIE, ""), max_age=SESSION_DAYS * 86400)
        except BadSignature:
            await ws.close(code=4401)
            return
        cam = w.repo.get_camera(camera_id)
        if cam is None or cam.kind != "webcam":
            await ws.close(code=4400)
            return
        pushers = w.pushers.setdefault(camera_id, set())
        pushers.add(id(ws))
        if not w.manager.status(camera_id).running:
            await run_in_threadpool(w.manager.start, camera_id)
        try:
            while True:
                data = await ws.receive_bytes()
                w.manager.push_frame(camera_id, data, time.time())
        except (WebSocketDisconnect, KeyError, RuntimeError):
            pass
        finally:
            pushers.discard(id(ws))
            if not pushers:
                await run_in_threadpool(w.manager.stop, camera_id)  # nobody is sending this webcam any more

    dist = Path(settings.web_dist) if settings.web_dist else WEB_DIST
    if dist.is_dir():
        app.mount("/", SpaFiles(directory=dist, html=True), name="web")
    return app


class SpaFiles(StaticFiles):
    """The built web app; any other path that is not an API, socket or blob route opens the app itself."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.split("/", 1)[0] in ("api", "ws", "blobs"):
                raise
            return await super().get_response("index.html", scope)

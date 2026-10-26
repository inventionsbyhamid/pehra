"""The judges' demo routes (demo.py has the sandbox; from packsure e97a509).

POST /api/demo/login signs the browser in to a new sandbox society, no sign in needed, starts its cameras and names
the page to go to; a browser already in a live sandbox keeps it, and a member signed in to their society is signed
out only when the request says so (`sign_out`). New sandboxes are limited per address (Tuning
limits.demo_logins_per_ip in demo_login_window_s; the address is ratelimit.client_key's, an IPv6 address by its /64,
and an IPv6 /48 has demo_logins_per_48) and for everyone per day (demo_sandboxes_day). Each login also counts as a
sign in attempt (limits.login_per_min_ip).
GET /api/demo/visitor says who is looking at a public page: signed_out, member or demo.
GET /api/demo tells the demo bar when the sandbox ends and where its cameras' clips come from.
GET /api/incidents/{id}/telegram is the message the society's Telegram group gets for an incident, made by
alerts/telegram.py itself (caption, buttons and the still with faces blurred), for any society.

SandboxWatch stops the cameras of sandboxes that ended (and of ones the reset deleted) and closes their devices."""
import base64
import logging
import threading
from collections import OrderedDict
from datetime import timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from watchful import demo, demo_rules
from watchful.alerts import incidents as inc_
from watchful.alerts import telegram
from watchful.api.common import LIVE_KINDS
from watchful.auth.deps import CurrentMember, current_member, current_user
from watchful.db import models as m
from watchful.ingest.push import CLOSE_OFF, push_parts
from watchful.ratelimit import TooManyTries, client_ip, client_key, wide_key

log = logging.getLogger(__name__)

NEXT = "/app"  # where Try the demo lands: the control room
STILLS_KEPT = 64  # Telegram stills made for previews, kept in memory by incident and picture


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sign_out: bool = False  # a member signed in to their society agrees to be signed out


class Limited(Exception):
    def __init__(self, words: str, retry_after_s: int):
        super().__init__(words)
        self.words, self.retry_after_s = words, retry_after_s


def iso(value):
    return value.isoformat() if value else None


class SandboxWatch:
    """Stops the cameras of demo sandboxes that ended: switched off in the database (so the app never starts them
    again and their devices are refused), stopped, and their device sockets closed. Cameras of a society the reset
    deleted stop too."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()

    def run_once(self) -> list[str]:
        ctx, stopped = self.ctx, []
        hub, _ = push_parts(ctx)
        for ws_id in demo.ended_sandboxes(ctx.db):
            with ctx.db.tx() as s:
                cams = list(s.scalars(select(m.Camera.id).where(m.Camera.workspace_id == ws_id)))
                s.execute(update(m.Camera).where(m.Camera.workspace_id == ws_id, m.Camera.enabled.is_(True))
                          .values(enabled=False).execution_options(synchronize_session=False))
            for cid in cams:
                ctx.manager.stop(cid)
                hub.close(cid, CLOSE_OFF)
                stopped.append(cid)
        running = dict(getattr(ctx.manager, "camera_ws", {}))
        if running:
            with ctx.db.tx() as s:
                alive = set(s.scalars(select(m.Workspace.id).where(m.Workspace.id.in_(set(running.values())))))
            for cid, ws_id in running.items():
                if ws_id not in alive:
                    ctx.manager.stop(cid)
                    hub.close(cid, CLOSE_OFF)
                    stopped.append(cid)
        return list(dict.fromkeys(stopped))

    def start(self) -> None:
        """Once now, before the cameras start, then every Tuning limits.demo_check_s."""
        self._safe()
        threading.Thread(target=self._loop, daemon=True, name="sandbox-watch").start()

    def _safe(self) -> None:
        try:
            self.run_once()
        except Exception:
            log.exception("stopping the cameras of ended demo sandboxes failed")

    def _loop(self) -> None:
        while not self._stop.wait(self.ctx.tuning.limits.demo_check_s):
            self._safe()

    def stop(self) -> None:
        self._stop.set()


class Stills:
    """Telegram's still for an incident, made the way the job worker makes it (telegram.still_for, faces of anyone
    not enrolled blurred) with this app's own face models, kept for the incident's picture."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.state: dict = {}  # what telegram.still_for keeps in a worker: the face models, each society's people
        self.made: OrderedDict[tuple, bytes | None] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, repo, inc: m.Incident) -> bytes | None:
        key = (inc.id, inc.picture_key or inc.keyframe_key, tuple(inc.picture_box or ()))
        with self._lock:
            if key in self.made:
                self.made.move_to_end(key)
                return self.made[key]
        worker = SimpleNamespace(state=self.state, blobs=self.ctx.blobs)
        still = telegram.still_for(SimpleNamespace(worker=worker, settings=self.ctx.settings, tuning=self.ctx.tuning),
                                   repo, inc)
        with self._lock:
            self.made[key] = still
            while len(self.made) > STILLS_KEPT:
                self.made.popitem(last=False)
        return still


def telegram_preview(ctx, repo, inc: m.Incident, stills: Stills) -> dict:
    """The incident's Telegram message as the society's group gets it, from alerts/telegram.py's own builders."""
    tz, chats, warnings = telegram._society(repo)
    base = ctx.settings.public_base_url or ""
    camera = telegram._camera_name(repo, inc.camera_id)
    rows = telegram.buttons(inc, base)
    sent = inc.status in inc_.ACTIVE and inc_.pushable(inc.severity, warnings) and \
        not (inc.agent_json or {}).get("held")
    still = stills.get(repo, inc)
    return {
        "caption": telegram.caption(inc, camera, tz, base),
        "buttons": [[{"text": text, "url": action if action.startswith(("https://", "http://")) else None}
                     for text, action in row] for row in rows],
        "photo": "data:image/jpeg;base64," + base64.b64encode(still).decode() if still else None,
        "goes_to_telegram": inc_.pushable(inc.severity, warnings),
        "sent": sent,
        "connected": bool(chats),
        "severity": inc.severity,
    }


def mount(app: FastAPI, ctx) -> None:
    app.state.sandboxes = SandboxWatch(ctx)
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    t = ctx.tuning.limits
    stills = Stills(ctx)

    def live_sandbox(user_id: str) -> m.Workspace | None:
        ws = demo.sandbox_of(ctx.db, user_id)
        return ws if ws is not None and not demo_rules.ended(ws) else None

    def returning(request: Request) -> tuple[m.User, m.Workspace] | None:
        data = ctx.sessions.read(request)
        if data is None:
            return None
        with ctx.db.tx() as s:
            user = s.get(m.User, data.user_id)
        ws = live_sandbox(user.id) if user is not None and user.demo else None
        return (user, ws) if ws is not None else None

    def member(request: Request) -> m.User | None:
        data = ctx.sessions.read(request)
        if data is None:
            return None
        with ctx.db.tx() as s:
            user = s.get(m.User, data.user_id)
        return user if user is not None and not user.demo else None

    def start(request: Request) -> tuple[m.User, m.Workspace]:
        trust = ctx.settings.trust_cloudflare
        limiter = ctx.limits.demo
        try:
            held = [(limiter, limiter.reserve(client_key(request, trust)))]
        except TooManyTries as e:
            raise Limited(f"This address has started {t.demo_logins_per_ip} demos today, the most for one day. "
                          "Come back tomorrow.", e.retry_after_s) from None
        if wide := wide_key(client_ip(request, trust)):
            try:
                held.append((ctx.limits.demo_48, ctx.limits.demo_48.reserve(wide)))
            except TooManyTries as e:
                limiter.release(held[0][1])
                raise Limited(f"This network has started {t.demo_logins_per_48} demos today, the most for one day. "
                              "Come back tomorrow.", e.retry_after_s) from None
        if demo.sandboxes_started_since(ctx.db, m.utcnow() - timedelta(days=1)) >= t.demo_sandboxes_day:
            for bucket, hit in held:
                bucket.release(hit)
            raise Limited("The demo is full for today. Come back tomorrow.", 3600)
        user, ws = demo.start_sandbox(ctx)
        for cam in ctx.repo_for(ws.id).list_cameras():
            if cam.enabled and cam.kind in LIVE_KINDS:
                ctx.manager.start(cam.id, ws.id)  # resting until someone watches: their day is seeded
        return user, ws

    @router.post("/api/demo/login", status_code=201)
    async def login(request: Request, response: Response, body: LoginIn | None = None):
        if not ctx.settings.demo_enabled:
            raise HTTPException(404, "The demo is not offered here.")
        ctx.sessions.check_origin(request)
        try:
            ctx.limits.sign_in.reserve(client_key(request, ctx.settings.trust_cloudflare))
        except TooManyTries as e:
            return JSONResponse({"detail": "Too many sign in tries from this address. Wait a minute and try again."},
                                status_code=429,
                                headers={"Retry-After": str(e.retry_after_s), "Cache-Control": "no-store"})
        if await run_in_threadpool(member, request) is not None and not (body and body.sign_out):
            raise HTTPException(409, "You are signed in to your society. Trying the demo signs you out of it.")
        try:
            found = await run_in_threadpool(returning, request) or await run_in_threadpool(start, request)
        except Limited as e:
            return JSONResponse({"detail": e.words}, status_code=429,
                                headers={"Retry-After": str(e.retry_after_s), "Cache-Control": "no-store"})
        user, ws = found
        ctx.sessions.write(response, user.id, ws.id)
        response.headers["Cache-Control"] = "no-store"
        return {"next": NEXT, "expires_at": iso(ws.expires_at)}

    @router.get("/api/demo/visitor")
    def visitor(request: Request, response: Response):
        """Who is looking at a public page, without a 401 for a visitor signed out."""
        data = ctx.sessions.read(request)
        with ctx.db.tx() as s:
            user = s.get(m.User, data.user_id) if data else None
        response.headers["Cache-Control"] = "no-store"
        return {"kind": "signed_out" if user is None else "demo" if user.demo else "member"}

    @router.get("/api/demo")
    def info(user: m.User = Depends(current_user)):
        if not user.demo:
            raise HTTPException(404, "This is not a demo society.")
        ws = demo.sandbox_of(ctx.db, user.id)
        if ws is None:
            raise HTTPException(404, "Start the demo first.")
        with ctx.db.tx() as s:
            own = demo_rules.own_cameras(s, ws.id)
        shown = {c.item["name"]: c.item.get("source", "") for c in demo.demo_cameras(ctx.settings)}
        return {"sandbox_id": ws.id, "expires_at": iso(ws.expires_at), "ended": demo_rules.ended(ws),
                "clips": [{"camera": name, "source": source} for name, source in shown.items()],
                "own_cameras": own, "own_cameras_allowed": t.demo_cameras}

    @router.get("/api/incidents/{incident_id}/telegram")
    def preview(incident_id: str, member: CurrentMember = Depends(current_member)):
        repo = ctx.repo_for(member.workspace.id)
        inc = inc_.get_incident(repo, incident_id)
        if inc is None:
            raise HTTPException(404, "No such incident.")
        return telegram_preview(ctx, repo, inc, stills)

    return router

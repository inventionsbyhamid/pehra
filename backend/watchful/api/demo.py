"""The judges' demo routes (demo.py has the sandbox; from packsure e97a509).

POST /api/demo/login signs the browser in to a new sandbox society, no sign in needed, starts its cameras and names
the page to go to; a browser already in a live sandbox keeps it, and a member signed in to their society is signed
out only when the request says so (`sign_out`). New sandboxes are limited per address (Tuning
limits.demo_logins_per_ip; the address is ratelimit.client_key's, an IPv6 address by its /64), per IPv4 /24, per IPv6
/48 and /32, all in demo_login_window_s, and for everyone per day (demo_sandboxes_day): at that cap the oldest
sandbox nobody used for demo_idle_reclaim_h is reclaimed for the new visitor. Each login also counts as a sign in
attempt (limits.login_per_min_ip). With TURNSTILE_SECRET set, a login needs Cloudflare Turnstile's token too.
GET /api/demo/visitor says who is looking at a public page (signed_out, member, or demo while their sandbox lasts)
and whether this server offers the demo.
GET /api/demo tells the demo bar when the sandbox ends and where its cameras' clips come from.
GET /api/incidents/{id}/telegram is the message the society's Telegram group gets for an incident
(alerts/telegram.py preview, the same text, buttons and still as posting), for any society.

SandboxWatch stops the cameras of sandboxes that ended (and of ones the reset deleted), closes their devices,
forgets the parts the app kept of deleted ones, and makes the demo clips' timelines once in the background.
live_refusal caps the visitors' own cameras analysed at once: limits.demo_live_per_sandbox per sandbox,
limits.demo_live_per_network per network the sandboxes were started from, and limits.demo_live_cameras across the demo.
At that cap the sender live longest, once past limits.demo_live_max_min, makes way for the newcomer, and api/live.py
closes a visitor's sender that sends no picture for limits.demo_idle_sender_s, so nobody holds the demo's camera
slots for a day."""
import base64
import logging
import threading
import time
from collections import OrderedDict
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from watchful import demo, demo_rules
from watchful.alerts import incidents as inc_
from watchful.alerts import telegram
from watchful.api.common import LIVE_KINDS
from watchful.auth.deps import CurrentMember, current_member, current_user
from watchful.db import models as m
from watchful.ingest.push import CLOSE_BUSY, CLOSE_OFF, push_parts
from watchful.ratelimit import TooManyTries, client_ip, client_key, network_key

log = logging.getLogger(__name__)

NEXT = "/app"  # where Try the demo lands: the control room
TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sign_out: bool = False  # a member signed in to their society agrees to be signed out
    turnstile: str | None = Field(None, max_length=2048)  # Cloudflare Turnstile's token, when TURNSTILE_SECRET is set


class Limited(Exception):
    def __init__(self, words: str, retry_after_s: int):
        super().__init__(words)
        self.words, self.retry_after_s = words, retry_after_s


def iso(value):
    return value.isoformat() if value else None


def human(secret: str, token: str | None, ip: str | None) -> bool:
    """Cloudflare Turnstile says the visitor is a person (a hook: off unless TURNSTILE_SECRET is set)."""
    if not token:
        return False
    try:
        res = httpx.post(TURNSTILE, data={"secret": secret, "response": token, "remoteip": ip or ""}, timeout=10)
        return bool(res.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


def sandbox_cameras(db, workspace_ids: list[str]) -> list[tuple[str, str, bool]]:
    with db.tx() as s:
        return list(s.execute(select(m.Camera.id, m.Camera.workspace_id, m.Camera.enabled)
                              .where(m.Camera.workspace_id.in_(workspace_ids))))


MADE_WAY = "Another visitor is trying the demo, so this camera stopped after its turn. Start it again in a few minutes."


def networks(db, workspace_ids) -> dict[str, str | None]:
    """The network each sandbox was started from (its demo.start audit row), by society id."""
    with db.tx() as s:
        rows = s.execute(select(m.Audit.workspace_id, m.Audit.data).where(m.Audit.action == "demo.start",
                                                                         m.Audit.workspace_id.in_(list(workspace_ids))))
        return {ws: (data or {}).get("network") for ws, data in rows}


def live_refusal(ctx, camera) -> str | None:
    """Words refusing a visitor's own camera, else None; a real society's camera is never refused here. Refused when
    the visitor's sandbox already has limits.demo_live_per_sandbox cameras sending, or their network
    limits.demo_live_per_network, or the whole demo limits.demo_live_cameras and nobody has been live for
    limits.demo_live_max_min yet. Past that, the sender live longest is closed (MADE_WAY) and the newcomer goes in."""
    if camera.kind != "webcam" or not demo_rules.is_sandbox(ctx.db, camera.workspace_id):
        return None
    hub, _ = push_parts(ctx)
    t = ctx.tuning.limits
    with ctx.db.tx() as s:
        own = list(s.execute(select(m.Camera.id, m.Camera.workspace_id)
                             .join(m.Workspace, m.Workspace.id == m.Camera.workspace_id)
                             .where(m.Workspace.demo == demo_rules.SANDBOX, m.Camera.kind == "push",
                                    m.Camera.id != camera.id)))
    live = [(cid, ws) for cid, ws in own if hub.sending(cid)]
    if sum(1 for _, ws in live if ws == camera.workspace_id) >= t.demo_live_per_sandbox:
        return "The demo shows one of your cameras at a time. Stop the other one first, then start this one."
    nets = networks(ctx.db, {ws for _, ws in live} | {camera.workspace_id})
    here = nets.get(camera.workspace_id)
    if here and sum(1 for _, ws in live if nets.get(ws) == here) >= t.demo_live_per_network:
        return (f"Your network already has {t.demo_live_per_network} cameras live in the demo. Stop one of them "
                "first, then start this one.")
    if len(live) < t.demo_live_cameras:
        return None
    now, turn = time.time(), t.demo_live_max_min * 60
    senders = [(getattr(x, "since", now), x) for cid, _ in live for x in hub.senders(cid)]
    done = [(since, x) for since, x in senders if now - since >= turn and hasattr(x, "close")]
    if done:
        min(done, key=lambda d: d[0])[1].close(CLOSE_BUSY, MADE_WAY)  # the longest live makes way
        return None
    return (f"The demo is busy: {t.demo_live_cameras} visitors' cameras are live now. Try again in a few "
            "minutes.")


class Stills:
    """Telegram's still for an incident page, made by telegram.still with the app's own parts: the face networks
    every society shares (AppContext.face_index), one pose model and the app's one person detector. Kept for the
    incident's picture and the society's enrolled people (Tuning limits.telegram_previews_kept)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._maker = None
        self.made: OrderedDict[tuple, bytes | None] = OrderedDict()
        self._lock = threading.Lock()

    def maker(self):
        from watchful.alerts.crops import StillMaker
        from watchful.components import make_pose
        with self._lock:
            if self._maker is None:
                try:
                    self._maker = StillMaker(self.ctx.face_index(), make_pose(self.ctx.settings, self.ctx.tuning),
                                             self.ctx.person_detector)
                except Exception:
                    log.exception("the models for Telegram stills could not be loaded")
                    return None
            return self._maker

    def get(self, repo, inc: m.Incident) -> bytes | None:
        people = repo.list_enrollments()
        version = tuple(sorted(e.person_id for e in people))
        key = (repo.workspace_id, inc.id, inc.picture_key or inc.keyframe_key, tuple(inc.picture_box or ()), version)
        with self._lock:
            if key in self.made:
                self.made.move_to_end(key)
                return self.made[key]
        still = telegram.still(self.ctx.blobs, self.maker, lambda: people, inc, self.ctx.tuning.crops)
        with self._lock:
            self.made[key] = still
            while len(self.made) > self.ctx.tuning.limits.telegram_previews_kept:
                self.made.popitem(last=False)
        return still

    def forget(self, workspace_id: str) -> None:
        with self._lock:
            for key in [k for k in self.made if k[0] == workspace_id]:
                self.made.pop(key)


class SandboxWatch:
    """Stops the cameras of demo sandboxes that ended: switched off in the database (so the app never starts them
    again and their devices are refused), stopped, and their device sockets closed; only cameras still on or running
    are touched. Cameras of a society the reset deleted stop too, and the app forgets what it kept of that society."""

    def __init__(self, ctx, stills: Stills):
        self.ctx, self.stills = ctx, stills
        self._stop = threading.Event()

    def run_once(self) -> list[str]:
        ctx, stopped = self.ctx, []
        hub, _ = push_parts(ctx)
        ended = demo.ended_sandboxes(ctx.db)
        for cid, ws_id, enabled in sandbox_cameras(ctx.db, ended) if ended else []:
            running = ctx.manager.status(cid).running
            if not enabled and not running:
                continue
            if enabled:
                with ctx.db.tx() as s:
                    s.execute(update(m.Camera).where(m.Camera.id == cid, m.Camera.workspace_id == ws_id)
                              .values(enabled=False).execution_options(synchronize_session=False))
            ctx.manager.stop(cid)
            hub.close(cid, CLOSE_OFF)
            stopped.append(cid)
        running = dict(getattr(ctx.manager, "camera_ws", {}))
        known = set(running.values()) | (ctx.remembered() if hasattr(ctx, "remembered") else set())
        with ctx.db.tx() as s:
            alive = set(s.scalars(select(m.Workspace.id).where(m.Workspace.id.in_(known)))) if known else set()
        for cid, ws_id in running.items():
            if ws_id not in alive and ctx.manager.status(cid).running:
                ctx.manager.stop(cid)
                hub.close(cid, CLOSE_OFF)
                stopped.append(cid)
        for ws_id in known - alive:
            if hasattr(ctx, "forget_society"):
                ctx.forget_society(ws_id)
            self.stills.forget(ws_id)
            demo_rules.forget(ws_id)
        return list(dict.fromkeys(stopped))

    def start(self) -> None:
        """Once now, before the cameras start, then every Tuning limits.demo_check_s; the demo clips' timelines are
        made in the background (once per clip and zones)."""
        self._safe()
        threading.Thread(target=self._loop, daemon=True, name="sandbox-watch").start()
        if self.ctx.settings.demo_enabled:
            threading.Thread(target=demo.warm_timelines, args=(self.ctx.settings, self.ctx.tuning), daemon=True,
                             name="demo-timelines").start()

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


def mount(app: FastAPI, ctx) -> None:
    stills = Stills(ctx)
    app.state.sandboxes = SandboxWatch(ctx, stills)
    app.include_router(make_router(ctx, stills))


def make_router(ctx, stills: Stills) -> APIRouter:
    router = APIRouter()
    t = ctx.tuning.limits

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

    def buckets(request: Request) -> list[tuple[object, str, str]]:
        """Each limit a new sandbox counts against: (limiter, key, words when it is used up)."""
        ip = client_ip(request, ctx.settings.trust_cloudflare)
        out = [(ctx.limits.demo, client_key(request, ctx.settings.trust_cloudflare),
                f"This address has started {t.demo_logins_per_ip} demos today, the most for one day.")]
        for limiter, v4, v6, n in ((ctx.limits.demo_24, 24, None, t.demo_logins_per_24),
                                   (ctx.limits.demo_48, None, 48, t.demo_logins_per_48),
                                   (ctx.limits.demo_32, None, 32, t.demo_logins_per_32)):
            if key := network_key(ip, v4, v6):
                out.append((limiter, key, f"This network has started {n} demos today, the most for one day."))
        return out

    def reclaim() -> bool:
        """At the day's cap, the oldest live sandbox nobody used for limits.demo_idle_reclaim_h goes now."""
        cutoff = time.time() - t.demo_idle_reclaim_h * 3600
        with ctx.db.tx() as s:
            live = list(s.execute(select(m.Workspace.id, m.Workspace.created_at)
                                  .where(m.Workspace.demo == demo_rules.SANDBOX, m.Workspace.expires_at > m.utcnow())
                                  .order_by(m.Workspace.created_at)))
        for ws_id, created in live:
            if demo_rules.last_seen(ws_id, created.timestamp()) > cutoff:
                continue
            for cid, _, _ in sandbox_cameras(ctx.db, [ws_id]):
                ctx.manager.stop(cid)
                push_parts(ctx)[0].close(cid, CLOSE_OFF)
            demo._drop(ctx.db, ctx.blobs, ws_id)
            ctx.db.audit(ws_id, "system", "demo.reclaim", {"idle_h": t.demo_idle_reclaim_h})
            return True
        return False

    def start(request: Request) -> tuple[m.User, m.Workspace]:
        held = []

        def release():
            for limiter, hit in held:
                limiter.release(hit)

        for limiter, key, words in buckets(request):
            try:
                held.append((limiter, limiter.reserve(key)))
            except TooManyTries as e:
                release()
                raise Limited(f"{words} Come back tomorrow.", e.retry_after_s) from None
        if demo.sandboxes_started_since(ctx.db, m.utcnow() - timedelta(days=1)) >= t.demo_sandboxes_day \
                and not reclaim():
            release()
            raise Limited("The demo is full for today. Come back tomorrow.", 3600)
        network = network_key(client_ip(request, ctx.settings.trust_cloudflare), 24, 48)
        try:
            user, ws = demo.start_sandbox(ctx, network=network)
        except Exception:
            release()
            raise
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
        found = await run_in_threadpool(returning, request)
        if found is None and ctx.settings.turnstile_secret and not await run_in_threadpool(
                human, ctx.settings.turnstile_secret, body.turnstile if body else None,
                client_ip(request, ctx.settings.trust_cloudflare)):
            raise HTTPException(403, "Show that you are a person first, then try the demo again.")
        try:
            found = found or await run_in_threadpool(start, request)
        except Limited as e:
            return JSONResponse({"detail": e.words}, status_code=429,
                                headers={"Retry-After": str(e.retry_after_s), "Cache-Control": "no-store"})
        user, ws = found
        ctx.sessions.write(response, user.id, ws.id)
        response.headers["Cache-Control"] = "no-store"
        return {"next": NEXT, "expires_at": iso(ws.expires_at)}

    @router.get("/api/demo/visitor")
    def visitor(request: Request, response: Response):
        """Who is looking at a public page, without a 401 for a visitor signed out: a demo visitor counts as one only
        while their sandbox lasts (after, the page offers a new demo)."""
        data = ctx.sessions.read(request)
        with ctx.db.tx() as s:
            user = s.get(m.User, data.user_id) if data else None
        kind = "signed_out" if user is None else "member" if not user.demo else \
            "demo" if live_sandbox(user.id) is not None else "signed_out"
        response.headers["Cache-Control"] = "no-store"
        return {"kind": kind, "offered": ctx.settings.demo_enabled}

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
        shown = telegram.preview(repo, inc, ctx.settings.public_base_url or "", stills.get(repo, inc))
        still = shown.pop("still")
        return {**shown, "photo": "data:image/jpeg;base64," + base64.b64encode(still).decode() if still else None,
                "demo": demo_rules.in_demo(member.workspace)}

    return router

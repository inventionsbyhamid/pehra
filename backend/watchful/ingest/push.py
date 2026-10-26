"""Cameras that send their own frames: a laptop or phone at /cam/{token}, the bridge on a society computer
(bridge/pehra_bridge.py), or the app's own webcam page. They send JPEG frames over a WebSocket (api/live.py).

A device token belongs to one camera of one society. It is shown once, only its hash is stored, and issuing a new one
replaces the old. Replacing it, removing it, turning the camera off or deleting the camera closes any socket sending
for that camera at once (PushHub.close). When a camera's watches change, its senders check again at once
(PushHub.poke): a device whose blur no longer matches is closed with CLOSE_CHANGED and shows the new notice. The hub
also keeps each camera's newest frame, so a live view on another device sees the phone's picture.

Tokens travel in paths (/cam/{token}, /ws/cam/{token}); TokenPaths keeps them out of uvicorn's logs."""
import asyncio
import hashlib
import logging
import re
import secrets
import threading
from dataclasses import dataclass, field

import cv2
from sqlalchemy import delete, select

from watchful.db import models as m
from watchful.ratelimit import RateLimiter
from watchful.store.sql import upsert

TOKEN_BYTES = 24  # 32 characters, 192 bits
MAX_TOKEN_CHARS = 64

# socket close codes a device acts on (the cam page and the bridge stop for good on GONE and UNKNOWN)
CLOSE_BAD_KIND = 4400
CLOSE_SIGN_IN = 4401
CLOSE_ROLE = 4403
CLOSE_UNKNOWN = 4404
CLOSE_TAKEN_OVER = 4409  # another device started sending for this camera
CLOSE_GONE = 4410  # the link was replaced or removed, or the camera deleted
CLOSE_CHANGED = 4411  # the camera's settings changed (faces blurred or not): the device reads the notice again
CLOSE_OFF = 4412  # the camera is turned off in the control room
CLOSE_BUSY = 4429


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceTokens:
    """Device tokens in the platform database. A lookup by token crosses societies by design (the token names the
    society); everything else is by (workspace_id, camera_id)."""

    def __init__(self, db):
        self.db = db

    def issue(self, workspace_id: str, camera_id: str) -> str:
        """A new token for this camera, replacing any earlier one."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = m.utcnow()
        with self.db.tx() as s:  # one row per camera (a unique index): a second issue at once replaces, never adds
            upsert(s, m.DeviceToken, {"id": m.new_id("dev"), "workspace_id": workspace_id, "camera_id": camera_id,
                                      "token_hash": hash_token(token), "created_at": now, "rotated_at": None},
                   ["workspace_id", "camera_id"], ["token_hash"])
            s.execute(m.DeviceToken.__table__.update()
                      .where(m.DeviceToken.workspace_id == workspace_id, m.DeviceToken.camera_id == camera_id,
                             m.DeviceToken.created_at < now).values(rotated_at=now))
        return token

    def find(self, token: str) -> tuple[str, str] | None:
        """(workspace_id, camera_id) of a token in use, else None."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
            return None
        with self.db.tx() as s:
            row = s.execute(select(m.DeviceToken.workspace_id, m.DeviceToken.camera_id)
                            .where(m.DeviceToken.token_hash == hash_token(token))).first()
        return (row[0], row[1]) if row else None

    def info(self, workspace_id: str, camera_id: str) -> dict | None:
        with self.db.tx() as s:
            row = s.scalar(select(m.DeviceToken).where(m.DeviceToken.workspace_id == workspace_id,
                                                       m.DeviceToken.camera_id == camera_id).limit(1))
        if row is None:
            return None
        return {"issued_at": (row.rotated_at or row.created_at).isoformat(), "first_at": row.created_at.isoformat()}

    def revoke(self, workspace_id: str, camera_id: str) -> bool:
        with self.db.tx() as s:
            return s.execute(delete(m.DeviceToken).where(m.DeviceToken.workspace_id == workspace_id,
                                                         m.DeviceToken.camera_id == camera_id)).rowcount > 0


@dataclass(eq=False)
class Sender:
    """One open socket sending frames for a camera; `close` and `nudge` may be called from any thread. Both set
    `stop`: with a code the socket closes with it, without one it checks itself now."""
    loop: asyncio.AbstractEventLoop
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    code: int | None = None

    def nudge(self) -> None:
        """Asks the socket to check its token, membership and settings now."""
        try:
            self.loop.call_soon_threadsafe(self.stop.set)
        except RuntimeError:
            pass

    def close(self, code: int) -> None:
        def _set():
            if self.code is None:
                self.code = code
            self.stop.set()
        try:
            self.loop.call_soon_threadsafe(_set)
        except RuntimeError:
            pass  # its loop is gone, and so is the socket


class PushHub:
    """Sockets sending frames, by camera, and each such camera's newest frame for its live views. The newest sender
    of a camera wins: an earlier one is closed with CLOSE_TAKEN_OVER."""

    def __init__(self):
        self._lock = threading.Lock()
        self._senders: dict[str, list[Sender]] = {}
        self._latest: dict[str, tuple[int, bytes, float]] = {}
        self._seq = 0

    def join(self, camera_id: str, sender: Sender) -> None:
        with self._lock:
            earlier = self._senders.setdefault(camera_id, [])
            for s in earlier:
                s.close(CLOSE_TAKEN_OVER)
            earlier.append(sender)

    def leave(self, camera_id: str, sender: Sender) -> bool:
        """True when nobody sends for this camera any more."""
        with self._lock:
            senders = self._senders.get(camera_id, [])
            if sender in senders:
                senders.remove(sender)
            if senders:
                return False
            self._senders.pop(camera_id, None)
            self._latest.pop(camera_id, None)
            return True

    def close(self, camera_id: str, code: int) -> int:
        """Closes every socket sending for this camera; how many there were."""
        with self._lock:
            senders = list(self._senders.get(camera_id, []))
        for s in senders:
            s.close(code)
        return len(senders)

    def poke(self, camera_id: str) -> int:
        """Makes every socket sending for this camera check itself now (its watches changed)."""
        with self._lock:
            senders = list(self._senders.get(camera_id, []))
        for s in senders:
            s.nudge()
        return len(senders)

    def sending(self, camera_id: str) -> bool:
        with self._lock:
            return bool(self._senders.get(camera_id))

    def put(self, camera_id: str, jpeg: bytes, ts: float) -> None:
        with self._lock:
            self._seq += 1
            self._latest[camera_id] = (self._seq, jpeg, ts)

    def latest(self, camera_id: str) -> tuple[int, bytes, float] | None:
        with self._lock:
            return self._latest.get(camera_id)


def push_parts(ctx) -> tuple[PushHub, DeviceTokens]:
    """The app's one hub and token store, made on first use (api/cameras.py, api/live.py, api/device.py and
    api/watches.py share them). Unknown device tokens count against their own limit per address
    (`ctx.device_misses`), so a bridge restarting with an old token never locks the society out of sign in links."""
    if getattr(ctx, "push_hub", None) is None:
        ctx.push_hub = PushHub()
        ctx.device_tokens = DeviceTokens(ctx.db)
        ctx.device_misses = RateLimiter(ctx.tuning.limits.misses_per_min_ip, 60.0)
    return ctx.push_hub, ctx.device_tokens


TOKEN_PATH = re.compile(r"(?<=\s)(/(?:ws/)?cam/)[^/?\s\"]+")


class TokenPaths(logging.Filter):
    """Rewrites /cam/{token} and /ws/cam/{token} in a log line to /cam/- and /ws/cam/-."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        hidden = TOKEN_PATH.sub(r"\1-", message)
        if hidden != message:
            record.msg, record.args = hidden, ()
        return True


def hide_tokens_in_logs(names=("uvicorn.error", "uvicorn.access", "uvicorn")) -> None:
    """uvicorn logs accepted sockets ("WebSocket /ws/cam/... [accepted]") at INFO even with --no-access-log, and
    the access log names every path; both go to app.log and CloudWatch."""
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenPaths) for f in logger.filters):
            logger.addFilter(TokenPaths())


def needs_faces(repo, camera_id: str) -> bool:
    """True when this camera names people (the staff register reader, or a watch about enrolled staff): its frames
    must keep their faces. Every other device camera blurs faces before a frame leaves it."""
    from watchful.logs.readers import FACES, has
    from watchful.rules.engine import needs_for
    from watchful.rules.watch import Watch
    cam = repo.get_camera(camera_id)
    if cam is not None and has(cam, FACES):
        return True
    watches = []
    for row in repo.list_watches(camera_id):
        if row.enabled:
            try:
                watches.append(Watch.model_validate(row.watch))
            except ValueError:
                continue
    return bool(watches) and needs_for(watches).faces


def retention_days(workspace, default: int) -> int:
    """How long this society's recordings are kept, from its plan (billing/limits.py)."""
    from watchful.billing.limits import retention_days as kept
    try:
        return kept(workspace)
    except (AttributeError, TypeError):
        return default


def qr_svg(text: str) -> str:
    """A QR code of `text` as SVG, from OpenCV's QRCodeEncoder (medium error correction, quiet zone included)."""
    params = cv2.QRCodeEncoder.Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    modules = cv2.QRCodeEncoder.create(params).encode(text)
    n = modules.shape[0]
    path = []
    for y in range(n):
        dark = modules[y] == 0
        x = 0
        while x < n:
            if not dark[x]:
                x += 1
                continue
            start = x
            while x < n and dark[x]:
                x += 1
            path.append(f"M{start} {y}h{x - start}v1h{start - x}z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path fill="#000" d="{"".join(path)}"/></svg>')

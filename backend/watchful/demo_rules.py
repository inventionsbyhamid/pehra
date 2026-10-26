"""The rules every request in a demo sandbox follows (from packsure e97a509). Kept apart from demo.py, so the auth,
billing and camera modules use them without loading the demo itself.

sandbox   one demo visitor's society until its `expires_at`; after that every request in it is refused, its cameras
          stop (api/demo.py SandboxWatch) and the daily demo reset deletes it. Uploads, enrolling faces and
          connecting Telegram are off (anyone can open the demo, nobody has consented there, and the bot posts
          only for real societies), and a visitor adds at most Tuning limits.demo_cameras cameras of their own.
          Rules match the route's own path, so a prefix in front of the app changes nothing."""
from datetime import datetime

from sqlalchemy import func, select

from watchful.db import models as m

SANDBOX = "sandbox"
ENDED = "This demo has ended. Start a new one."
OWN_KINDS = ("rtsp", "push")  # cameras a visitor adds (store/sql.py KIND_TO_ROW); the demo's clips are "file"
SANDBOX_CAPPED = {("POST", "/api/cameras")}  # adds a camera row: limits.demo_cameras of the visitor's own at most
SANDBOX_OFF = {  # requests a sandbox refuses, by method and route
    ("POST", "/api/uploads"): "Uploading a recording is off in the demo. Sign in to scan your own recordings.",
    ("POST", "/api/people"): "Naming staff from their faces is off in the demo, as nobody here has given consent. "
                             "Sign in to enrol your own staff.",
    ("POST", "/api/telegram/connect"): "Telegram is off in the demo. Each incident shows the message a group would "
                                       "get. Sign in to connect your society's group.",
}


def ended(ws: m.Workspace, now: datetime | None = None) -> bool:
    return ws.demo == SANDBOX and ws.expires_at is not None and ws.expires_at <= (now or m.utcnow())


def own_cameras(s, workspace_id: str) -> int:
    return s.scalar(select(func.count()).select_from(m.Camera).where(m.Camera.workspace_id == workspace_id,
                                                                     m.Camera.kind.in_(OWN_KINDS))) or 0


def refusal(ws: m.Workspace, method: str, route: str, db=None, cameras: int | None = None,
            now: datetime | None = None) -> str | None:
    """Why a member's request to this route (its path template, as declared) in this society is refused, or None.
    `db` counts the cameras a capped route would add to; `cameras` is limits.demo_cameras."""
    if ws.demo != SANDBOX:
        return None
    if ended(ws, now):
        return ENDED
    if (method, route) in SANDBOX_CAPPED and db is not None and cameras is not None:
        with db.tx() as s:
            held = own_cameras(s, ws.id)
        if held >= cameras:
            words = "1 camera" if cameras == 1 else f"{cameras} cameras"
            return f"The demo takes {words} of your own. Sign in to add more."
    return SANDBOX_OFF.get((method, route))


def is_sandbox(db, workspace_id: str | None) -> bool:
    if not workspace_id:
        return False
    with db.tx() as s:
        return s.scalar(select(m.Workspace.demo).where(m.Workspace.id == workspace_id)) == SANDBOX


def in_sandbox(repo) -> bool:
    """Whether this society's repository is a demo sandbox's."""
    return is_sandbox(repo, getattr(repo, "workspace_id", None))

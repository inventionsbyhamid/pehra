"""The rules every request in a demo sandbox follows (from packsure e97a509). Kept apart from demo.py, so the auth,
billing and camera modules use them without loading the demo itself.

sandbox   one demo visitor's society until its `expires_at`; after that every request in it is refused, its cameras
          stop (api/demo.py SandboxWatch) and the daily demo reset deletes it. Anyone can open the demo, so a sandbox
          takes only the writes the demo needs (SANDBOX_ALLOWED, matched by the route's own path, so a prefix in
          front of the app changes nothing) and refuses every other write in plain words. Some allowed routes check
          their body too (the handlers call the helpers below): a camera is this device or a phone, never an address
          the server would read; no camera tuning and no readers (plate reading, the staff register); zones and lines
          only on the visitor's own cameras; search by words, not for a person."""
import time
from datetime import datetime

from sqlalchemy import func, select

from watchful.db import models as m

SANDBOX = "sandbox"
ENDED = "This demo has ended. Start a new one."
OWN_KINDS = ("rtsp", "push")  # cameras a visitor adds (store/sql.py KIND_TO_ROW); the demo's clips are "file"
SANDBOX_ALLOWED = {  # the writes a sandbox takes, by method and route
    ("POST", "/api/cameras"),  # this device or a phone (sandbox_camera)
    ("PATCH", "/api/cameras/{camera_id}"),  # name, purpose, on and off; zones on the visitor's own (sandbox_patch)
    ("DELETE", "/api/cameras/{camera_id}"),  # the visitor's own (sandbox_delete)
    ("POST", "/api/cameras/{camera_id}/device-token"),
    ("DELETE", "/api/cameras/{camera_id}/device-token"),
    ("POST", "/api/cameras/{camera_id}/watches/preview"),
    ("POST", "/api/cameras/{camera_id}/watches"),
    ("PATCH", "/api/watches/{watch_id}"),
    ("POST", "/api/incidents/{incident_id}/acknowledge"),
    ("POST", "/api/incidents/{incident_id}/false_alarm"),
    ("POST", "/api/ask"),
    ("POST", "/api/search"),  # by words (sandbox_search)
}
SANDBOX_CAPPED = {("POST", "/api/cameras")}  # adds a camera row: limits.demo_cameras of the visitor's own at most
NOT_IN_DEMO = "The demo does not do this. Sign in to do it in your own society."
SANDBOX_OFF = {  # plain words for the writes a sandbox refuses; any other gets NOT_IN_DEMO
    ("POST", "/api/uploads"): "Uploading a recording is off in the demo. Sign in to scan your own recordings.",
    ("POST", "/api/cameras/{camera_id}/scan"): "Uploading a recording is off in the demo. Sign in to scan your own "
                                               "recordings.",
    ("POST", "/api/cameras/{camera_id}/zones/{zone_id}/examples"): "Saving pictures of a zone is off in the demo. "
                                                                    "Sign in to teach your own cameras.",
    ("POST", "/api/people"): "Naming staff from their faces is off in the demo, as nobody here has given consent. "
                             "Sign in to enrol your own staff.",
    ("POST", "/api/lookouts"): "Looking out for a person is off in the demo, as nobody here has given consent. "
                               "Sign in to use it with your own staff.",
    ("POST", "/api/telegram/connect"): "Telegram is off in the demo. Each incident shows the message a group would "
                                       "get. Sign in to connect your society's group.",
    ("POST", "/api/invites"): "Invites are off in the demo. Sign in to invite your team.",
    ("PATCH", "/api/members/{user_id}"): "The demo has one member, you. Sign in to build your team.",
    ("DELETE", "/api/members/{user_id}"): "The demo has one member, you. Sign in to build your team.",
    ("POST", "/api/billing/checkout"): "This is a demo society, so there is nothing to buy here. Sign in to choose a "
                                       "plan for your own society.",
    ("POST", "/api/billing/cameras"): "This is a demo society, so there is nothing to buy here. Sign in to choose a "
                                      "plan for your own society.",
}
STREAM_OFF = "The demo takes this device or a phone as a camera. Sign in to add a camera by its address."
TUNING_OFF = "Camera tuning is off in the demo. Sign in to tune your own cameras."
DEMO_ZONES = "The demo cameras keep their zones. Add your camera to draw your own."
DEMO_CAMERA_KEPT = "The demo cameras stay. You can remove the cameras you added."
PHOTO_OFF = "Searching for a person is off in the demo, as nobody here has given consent. Search by words instead."
READERS_OFF = "Plate reading and the staff register are off in the demo. Sign in to use them with your own cameras."


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
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    if (method, route) not in SANDBOX_ALLOWED:
        return SANDBOX_OFF.get((method, route), NOT_IN_DEMO)
    if (method, route) in SANDBOX_CAPPED and db is not None and cameras is not None:
        with db.tx() as s:
            held = own_cameras(s, ws.id)
        if held >= cameras:
            words = "1 camera" if cameras == 1 else f"{cameras} cameras"
            return f"The demo takes {words} of your own. Sign in to add more."
    return None


def in_demo(ws) -> bool:
    return getattr(ws, "demo", None) == SANDBOX


def sandbox_camera(ws, kind: str) -> str | None:
    """A sandbox adds this device or a phone, never an address the server would read for a day."""
    return STREAM_OFF if in_demo(ws) and kind != "webcam" else None


def sandbox_patch(ws, camera_kind: str, zones: bool, tuning: bool, readers: bool = False) -> str | None:
    if not in_demo(ws):
        return None
    if tuning:
        return TUNING_OFF
    if readers:
        return READERS_OFF
    if zones and camera_kind != "webcam":
        return DEMO_ZONES
    return None


def sandbox_delete(ws, camera_kind: str) -> str | None:
    return DEMO_CAMERA_KEPT if in_demo(ws) and camera_kind != "webcam" else None


def sandbox_search(ws, person: bool) -> str | None:
    return PHOTO_OFF if in_demo(ws) and person else None


_KIND: dict[str, bool] = {}  # whether a society is a sandbox, which never changes for its id


def is_sandbox(db, workspace_id: str | None) -> bool:
    if not workspace_id:
        return False
    if workspace_id not in _KIND:
        with db.tx() as s:
            kind = s.scalar(select(m.Workspace.demo).where(m.Workspace.id == workspace_id))
        _KIND[workspace_id] = kind == SANDBOX  # ids are never reused, so a society made later is not this one
    return _KIND[workspace_id]


def in_sandbox(repo) -> bool:
    """Whether this society's repository is a demo sandbox's."""
    return is_sandbox(repo, getattr(repo, "workspace_id", None))


_SEEN: dict[str, float] = {}  # when each sandbox last took a request in this app (a reclaim picks an idle one)


def seen(workspace_id: str) -> None:
    _SEEN[workspace_id] = time.time()


def last_seen(workspace_id: str, default: float) -> float:
    """When the sandbox last took a request, or `default` (its start) when not since this app started."""
    return _SEEN.get(workspace_id, default)


def forget(workspace_id: str) -> None:
    _SEEN.pop(workspace_id, None)
    _KIND.pop(workspace_id, None)

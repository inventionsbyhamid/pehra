"""Small pieces the society routers share. Every lookup goes through the member's own society's repository, so an id
from another society is not found (404)."""
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException

from watchful.store.db import Camera

LIVE_KINDS = {"stream", "webcam", "demo"}


def camera_or_404(repo, camera_id: str) -> Camera:
    cam = repo.get_camera(camera_id)
    if cam is None:
        raise HTTPException(404, "No such camera.")
    return cam


def alert_or_404(repo, alert_id: str):
    a = repo.get_alert(alert_id)
    if a is None:
        raise HTTPException(404, "No such alert.")
    return a


def redact_url(url: str | None) -> str | None:
    """A stream address without its password, for pages."""
    if not url or "@" not in url:
        return url
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host  # an IPv6 address keeps its brackets
    netloc = f"{parts.username}:***@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


def alert_json(blobs, a) -> dict:
    """An alert with a picture link that works on any storage (signed local links or presigned S3)."""
    return {**asdict(a), "keyframe_url": blobs.url(a.keyframe_key) if a.keyframe_key else None}


def camera_json(manager, cam: Camera) -> dict:
    """`status` is the camera worker's state; `health` (live, offline, covered, moved or blurred) the camera's own,
    as the app judges it now, else as last written to its row; None for a camera that never sent a picture."""
    st = manager.status(cam.id)
    seen = cam.status if cam.last_frame_at is not None else None
    return {**asdict(cam), "url": redact_url(cam.url), "health": getattr(st, "health", None) or seen,
            "status": {"running": st.running, "source": st.source_status, "fps": round(st.fps, 1),
                       "loops": st.loops, "stats": st.stats, "problems": st.problems}}

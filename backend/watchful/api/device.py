"""The device page a device link opens, which turns a laptop or phone into one of the society's cameras (its socket,
/ws/cam/{token}, is in api/live.py):

* /cam/{token}: the page, its own small build (web/cam.html), never the app;
* /cam/{token}/info: the camera's name, purpose, the society, its contact, whether faces are blurred, and the send
  and blur settings;
* /cam/{token}/qr.svg: a QR code that opens the page on a phone;
* /api/cam/face-model: YuNet for the page's face blur.

The token is the only credential: unknown tokens count as guesses per address (their own limit, so a bridge with an
old token never locks the society out of sign in links), known ones against the public pages limit. Tokens travel in
these paths, so uvicorn's log lines are rewritten (ingest/push.py TokenPaths)."""
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import select

from watchful.api.headers import policy
from watchful.db import models as m
from watchful.ingest.push import hide_tokens_in_logs, needs_faces, push_parts, qr_svg, retention_days
from watchful.ratelimit import TooManyTries, client_key
from watchful.rules.presets import purposes
from watchful.vision.models import model_path

BUSY = "busy"
PAGE = "cam.html"  # web/cam.html built on its own: the relaxed policy below never covers the app's code
DEVICE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
CONTACT_KEY = "notice_contact"  # a society's own contact for camera notices (Meta), else its owner's email
GONE = "This camera link no longer works. Ask your society admin for a new one."


def lookup_for(ctx):
    """lookup(conn, token): (workspace_id, camera_id) of a device token, None when unknown, BUSY after too many
    unknown ones from that address."""
    _, tokens = push_parts(ctx)

    def lookup(conn, token: str):
        key = client_key(conn, ctx.settings.trust_cloudflare)
        try:
            guess = ctx.device_misses.reserve(key)
        except TooManyTries:
            return BUSY
        found = tokens.find(token)
        if found is not None:
            ctx.device_misses.release(guess)
        return found
    return lookup


def contact(db, repo, workspace_id: str) -> str | None:
    """Who people near a camera can ask about it: the society's notice contact, else its first owner's email."""
    if (own := repo.get_meta(CONTACT_KEY)):
        return own
    with db.tx() as s:
        return s.scalar(select(m.User.email).join(m.Member, m.Member.user_id == m.User.id)
                        .where(m.Member.workspace_id == workspace_id, m.Member.role == "owner")
                        .order_by(m.User.created_at, m.User.id).limit(1))


def mount(app: FastAPI, ctx) -> None:
    from watchful.api.app import WEB_DIST

    hide_tokens_in_logs()
    lookup = lookup_for(ctx)
    tune = ctx.tuning.push
    # OpenCV.js (WebAssembly; its bindings build functions at run time) blurs faces on the device
    device_policy = policy(ctx.settings).replace("script-src 'self'", "script-src 'self' 'unsafe-eval' "
                                                                      "'wasm-unsafe-eval'", 1)

    def device_camera(request: Request, token: str):
        found = lookup(request, token)
        if found == BUSY:
            raise HTTPException(429, "Too many tries from this address. Wait a minute.")
        try:
            if found is not None:
                ctx.limits.public.reserve(client_key(request, ctx.settings.trust_cloudflare))
        except TooManyTries:
            raise HTTPException(429, "Too many tries from this address. Wait a minute.") from None
        repo = ctx.repo_for(found[0]) if found else None
        cam = repo.get_camera(found[1]) if repo else None
        if cam is None or cam.kind != "webcam":
            raise HTTPException(404, GONE)
        return found[0], repo, cam

    @app.get("/cam/{token}", include_in_schema=False)
    def device_page(token: str):
        dist = ctx.settings.web_dist or WEB_DIST
        page = dist / PAGE
        if not page.is_file():
            raise HTTPException(503, "The camera page is not built on this server.")
        return HTMLResponse(page.read_text(), headers={**DEVICE_HEADERS, "Content-Security-Policy": device_policy})

    @app.get("/cam/{token}/info", include_in_schema=False)
    def device_info(token: str, request: Request):
        workspace_id, repo, cam = device_camera(request, token)
        with ctx.db.tx() as s:
            society = s.get(m.Workspace, workspace_id)
        purpose = purposes().get(cam.purpose or "other", {}).get("label")
        return JSONResponse({
            "camera": cam.name, "purpose": purpose, "society": society.name,
            "contact": contact(ctx.db, repo, workspace_id), "enabled": cam.enabled,
            "blur": not needs_faces(repo, cam.id),
            "retention_days": retention_days(society, ctx.settings.retention_days),
            "send": {"fps": tune.fps, "width": tune.width, "quality": tune.quality},
            "faces": {"width": tune.face_width, "score": tune.face_score, "grow": tune.face_grow,
                      "hold_ms": tune.face_hold_ms, "blocks": tune.face_blocks, "failures": tune.face_failures},
        }, headers=DEVICE_HEADERS)

    @app.get("/cam/{token}/qr.svg", include_in_schema=False)
    def device_qr(token: str, request: Request):
        device_camera(request, token)
        url = f"{ctx.settings.public_base_url.rstrip('/')}/cam/{quote(token, safe='')}"
        return Response(qr_svg(url), media_type="image/svg+xml", headers=DEVICE_HEADERS)

    @app.get("/api/cam/face-model", include_in_schema=False)
    def face_model():
        """YuNet (MIT, OpenCV Model Zoo) for the device page's face blur."""
        path = model_path("yunet", ctx.settings)
        if not path.is_file():
            raise HTTPException(404, "The face model is not on this server.")
        return FileResponse(path, media_type="application/octet-stream",
                            headers={"Cache-Control": "public, max-age=86400"})


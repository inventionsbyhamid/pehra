"""WebSockets, and the device page that turns a laptop or phone into a camera.

* /ws/cameras/{id}: a camera's live view, for any member of its society. A camera that sends its own pictures shows
  the newest one it sent (add ?frames=0 when this browser is the camera and shows its own video).
* /ws/cameras/{id}/push: this browser's webcam sending frames, for an admin of the camera's society.
* /ws/cam/{token}: a laptop, phone or the bridge sending one camera's frames with its device token (ingest/push.py).
* /cam/{token}: the device page (web/src/cam); /cam/{token}/info names its camera, /cam/{token}/qr.svg is a QR code
  that opens it on a phone.

Session sockets need the session cookie of a member and an Origin of this site (a socket is not covered by SameSite).
A device socket has no cookie: its token is the credential, and unknown tokens count as guesses per address. Every
open socket checks its membership or token again every Tuning push.recheck_s, so a removed member or a replaced link
is out within that time; replacing a link, turning a camera off or deleting it closes its sockets at once."""
import asyncio
import base64
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.websockets import WebSocketDisconnect

from watchful.api.headers import policy
from watchful.auth.deps import CurrentMember, session_member
from watchful.auth.sessions import origin_of
from watchful.db import models as m
from watchful.ingest.push import (CLOSE_BAD_KIND, CLOSE_BUSY, CLOSE_GONE, CLOSE_OFF, CLOSE_ROLE, CLOSE_SIGN_IN,
                                  CLOSE_UNKNOWN, Sender, needs_faces, push_parts, qr_svg, retention_days)
from watchful.ratelimit import TooManyTries, client_key
from watchful.vision.models import model_path

CLOSE_NOT_FOUND = CLOSE_UNKNOWN
BUSY = "busy"
JPEG = b"\xff\xd8"
DEVICE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}


def mount(app: FastAPI, ctx) -> None:
    from watchful.api.app import SHELL, WEB_DIST

    manager = ctx.manager
    hub, tokens = push_parts(ctx)
    tune = ctx.tuning.push
    # the device page runs OpenCV.js (WebAssembly; its bindings build functions at run time) to blur faces
    device_policy = policy(ctx.settings).replace("script-src 'self'", "script-src 'self' 'unsafe-eval' "
                                                                      "'wasm-unsafe-eval'", 1)

    async def member_of(ws: WebSocket) -> CurrentMember | None:
        sent = ws.headers.get("origin")
        member = await run_in_threadpool(session_member, ws)
        if member is None or (sent and origin_of(sent) != ctx.sessions.origin):
            return None
        return member

    async def camera_for(ws: WebSocket, camera_id: str, role: str = "viewer"):
        """(member, camera) when this socket's session belongs to the camera's society with at least this role;
        closes the socket and gives (None, None) if not."""
        member = await member_of(ws)
        if member is None:
            await ws.close(code=CLOSE_SIGN_IN)
            return None, None
        if not member.can(role):
            await ws.close(code=CLOSE_ROLE)
            return None, None
        cam = await run_in_threadpool(ctx.repo_for(member.workspace.id).get_camera, camera_id)
        if cam is None:
            await ws.close(code=CLOSE_NOT_FOUND)
        return member, cam

    def still(ws: WebSocket, member: CurrentMember, role: str):
        async def check() -> bool:
            again = await run_in_threadpool(session_member, ws)
            return again is not None and again.workspace.id == member.workspace.id and again.can(role)
        return check

    def lookup(conn, token: str):
        """(workspace_id, camera_id) of a device token, None when unknown, BUSY after too many unknown ones."""
        key = client_key(conn, ctx.settings.trust_cloudflare)
        try:
            guess = ctx.limits.misses.reserve(key)
        except TooManyTries:
            return BUSY
        found = tokens.find(token)
        if found is not None:
            ctx.limits.misses.release(guess)
        return found

    @app.websocket("/ws/cameras/{camera_id}")
    async def live(ws: WebSocket, camera_id: str):
        await ws.accept()
        member, cam = await camera_for(ws, camera_id)
        if cam is None:
            return
        relay = cam.kind == "webcam" and ws.query_params.get("frames") != "0"
        check = still(ws, member, "viewer")
        loop = asyncio.get_running_loop()
        next_check = loop.time() + tune.recheck_s
        sent, tracks = 0, []
        sub = manager.subscribe(camera_id)
        incoming = asyncio.create_task(ws.receive())
        try:
            while True:
                if incoming.done():
                    if incoming.result().get("type") == "websocket.disconnect":
                        break
                    incoming = asyncio.create_task(ws.receive())  # ignore anything the browser sends
                if loop.time() >= next_check:
                    next_check = loop.time() + tune.recheck_s
                    if not await check():
                        await ws.close(code=CLOSE_SIGN_IN)
                        break
                msg = sub.get(0)
                if msg is None and relay and (latest := hub.latest(camera_id)) and latest[0] != sent:
                    sent = latest[0]
                    await ws.send_json({"type": "frame", "ts": latest[2], "width": None, "height": None,
                                        "tracks": tracks, "jpeg_b64": base64.b64encode(latest[1]).decode()})
                    continue
                if msg is None:
                    # wait without holding a server thread, and notice a closed tab at once
                    await asyncio.wait({incoming}, timeout=0.05)
                    continue
                if msg["type"] == "overlay":
                    tracks = msg.get("tracks", [])
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
            manager.unsubscribe(camera_id, sub)

    async def pump(ws: WebSocket, cam, check, fail_code: int) -> None:
        """Takes JPEG frames for a camera until the device leaves, the hub closes it or `check` fails."""
        loop = asyncio.get_running_loop()
        sender = Sender(loop)
        hub.join(cam.id, sender)
        stopping = asyncio.create_task(sender.stop.wait())
        incoming = asyncio.create_task(ws.receive())
        try:
            if not manager.status(cam.id).running:
                await run_in_threadpool(manager.start, cam.id, cam.workspace_id)
            limit, gap, last = tune.max_frame_kb * 1024, 1.0 / tune.max_fps, 0.0
            next_check = loop.time() + tune.recheck_s
            while True:
                done, _ = await asyncio.wait({incoming, stopping}, timeout=max(0.0, next_check - loop.time()),
                                             return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    await ws.close(code=sender.code or CLOSE_GONE)
                    break
                if incoming in done:
                    msg = incoming.result()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    data = msg.get("bytes")
                    now = time.time()
                    if data and len(data) <= limit and data[:2] == JPEG and now - last >= gap:
                        last = now
                        manager.push_frame(cam.id, data, now)
                        hub.put(cam.id, data, now)
                    incoming = asyncio.create_task(ws.receive())
                if loop.time() >= next_check:
                    next_check = loop.time() + tune.recheck_s
                    if not await check():
                        await ws.close(code=fail_code)
                        break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            incoming.cancel()
            stopping.cancel()
            if hub.leave(cam.id, sender):
                await run_in_threadpool(manager.stop, cam.id)  # nobody is sending this camera any more

    @app.websocket("/ws/cameras/{camera_id}/push")
    async def push(ws: WebSocket, camera_id: str):
        await ws.accept()
        member, cam = await camera_for(ws, camera_id, "admin")
        if cam is None:
            return
        if cam.kind != "webcam":
            await ws.close(code=CLOSE_BAD_KIND)
            return
        if not cam.enabled:
            await ws.close(code=CLOSE_OFF)
            return
        await pump(ws, cam, still(ws, member, "admin"), CLOSE_SIGN_IN)

    @app.websocket("/ws/cam/{token}")
    async def device(ws: WebSocket, token: str):
        await ws.accept()
        found = await run_in_threadpool(lookup, ws, token)
        if found is None or found == BUSY:
            await ws.close(code=CLOSE_BUSY if found == BUSY else CLOSE_UNKNOWN)
            return
        repo = ctx.repo_for(found[0])
        cam = await run_in_threadpool(repo.get_camera, found[1])
        if cam is None or cam.kind != "webcam":
            await ws.close(code=CLOSE_GONE if cam is None else CLOSE_BAD_KIND)
            return
        if not cam.enabled:
            await ws.close(code=CLOSE_OFF)
            return
        blur = not await run_in_threadpool(needs_faces, repo, cam.id)
        await ws.send_json({"type": "hello", "camera": cam.name, "fps": tune.fps, "width": tune.width,
                            "quality": tune.quality, "blur": blur})

        async def check() -> bool:
            return await run_in_threadpool(tokens.find, token) == found
        await pump(ws, cam, check, CLOSE_GONE)

    # the device page

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
            raise HTTPException(404, "This camera link no longer works. Ask your society admin for a new one.")
        return found[0], repo, cam

    @app.get("/cam/{token}", include_in_schema=False)
    def device_page(token: str):
        dist = ctx.settings.web_dist or WEB_DIST
        shell = next((p for p in (dist / SHELL, dist / "index.html") if p.is_file()), None)
        html = shell.read_text() if shell else '<!doctype html><meta charset="utf-8"><div id="root"></div>'
        return HTMLResponse(html, headers={**DEVICE_HEADERS, "Content-Security-Policy": device_policy})

    @app.get("/cam/{token}/info", include_in_schema=False)
    def device_info(token: str, request: Request):
        workspace_id, repo, cam = device_camera(request, token)
        with ctx.db.tx() as s:
            society = s.get(m.Workspace, workspace_id)
        return JSONResponse({
            "camera": cam.name, "society": society.name, "enabled": cam.enabled,
            "blur": not needs_faces(repo, cam.id),
            "retention_days": retention_days(society, ctx.settings.retention_days),
            "send": {"fps": tune.fps, "width": tune.width, "quality": tune.quality},
            "faces": {"width": tune.face_width, "score": tune.face_score, "grow": tune.face_grow,
                      "hold_ms": tune.face_hold_ms, "blocks": tune.face_blocks},
        }, headers=DEVICE_HEADERS)

    @app.get("/cam/{token}/qr.svg", include_in_schema=False)
    def device_qr(token: str, request: Request):
        device_camera(request, token)
        url = f"{ctx.settings.public_base_url.rstrip('/')}/cam/{token}"
        return Response(qr_svg(url), media_type="image/svg+xml", headers=DEVICE_HEADERS)

    @app.get("/api/cam/face-model", include_in_schema=False)
    def face_model():
        """YuNet (MIT, OpenCV Model Zoo) for the device page's face blur."""
        path = model_path("yunet", ctx.settings)
        if not path.is_file():
            raise HTTPException(404, "The face model is not on this server.")
        return FileResponse(path, media_type="application/octet-stream",
                            headers={"Cache-Control": "public, max-age=86400"})

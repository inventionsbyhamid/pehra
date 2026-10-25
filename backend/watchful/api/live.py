"""WebSockets:

* /ws/cameras/{id}: a camera's live view, for any member of its society. A camera that sends its own pictures shows
  the newest one it sent (add ?frames=0 when this browser is the camera and shows its own video).
* /ws/cameras/{id}/push: this browser's webcam sending frames, for an admin of the camera's society.
* /ws/cam/{token}: a laptop, phone or the bridge sending one camera's frames with its device token (ingest/push.py).
  The device page and its other routes are in api/device.py, mounted from here.

Session sockets need the session cookie of a member and an Origin of this site (a socket is not covered by SameSite).
A device socket has no cookie: its token is the credential, and unknown tokens count as guesses per address. Every
sending socket checks itself as it joins (a link replaced a moment before is caught), again every Tuning
push.recheck_s, and at once when its camera's watches change (PushHub.poke): a removed member, a replaced link, or a
device whose blur no longer matches the camera (CLOSE_CHANGED) is out. Replacing a link, turning a camera off or
deleting it closes its sockets at once."""
import asyncio
import base64
import time

from fastapi import FastAPI, WebSocket
from fastapi.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from watchful.api import device as device_page
from watchful.auth.deps import CurrentMember, session_member
from watchful.auth.sessions import origin_of
from watchful.ingest.push import (CLOSE_BAD_KIND, CLOSE_BUSY, CLOSE_CHANGED, CLOSE_GONE, CLOSE_OFF, CLOSE_ROLE,
                                  CLOSE_SIGN_IN, CLOSE_UNKNOWN, Sender, needs_faces, push_parts)

CLOSE_NOT_FOUND = CLOSE_UNKNOWN
BUSY = device_page.BUSY
JPEG = b"\xff\xd8"


def mount(app: FastAPI, ctx) -> None:
    manager = ctx.manager
    hub, tokens = push_parts(ctx)
    tune = ctx.tuning.push
    lookup = device_page.lookup_for(ctx)
    device_page.mount(app, ctx)

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
        """A check: None while the member still belongs to the society with this role, else the close code."""
        async def check() -> int | None:
            again = await run_in_threadpool(session_member, ws)
            ok = again is not None and again.workspace.id == member.workspace.id and again.can(role)
            return None if ok else CLOSE_SIGN_IN
        return check

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
                    if (code := await check()) is not None:
                        await ws.close(code=code)
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

    async def pump(ws: WebSocket, cam, check) -> None:
        """Takes JPEG frames for a camera until the device leaves, the hub closes it or `check` gives a close code.
        The sender's stop event also wakes it to check at once (PushHub.poke) when no close code was set."""
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
            if (code := await check()) is not None:  # a link replaced between the lookup and joining the hub
                await ws.close(code=code)
                return
            while True:
                done, _ = await asyncio.wait({incoming, stopping}, timeout=max(0.0, next_check - loop.time()),
                                             return_when=asyncio.FIRST_COMPLETED)
                if stopping in done and sender.code is not None:
                    await ws.close(code=sender.code)
                    break
                if stopping in done:  # poked: check now
                    sender.stop.clear()
                    stopping = asyncio.create_task(sender.stop.wait())
                    next_check = loop.time()
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
                    if (code := await check()) is not None:
                        await ws.close(code=code)
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
        await pump(ws, cam, still(ws, member, "admin"))

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

        async def check() -> int | None:
            if await run_in_threadpool(tokens.find, token) != found:
                return CLOSE_GONE
            if (not await run_in_threadpool(needs_faces, repo, cam.id)) != blur:
                return CLOSE_CHANGED  # the notice the device shows is no longer true
            return None
        await pump(ws, cam, check)

"""WebSockets: a camera's live view, and a browser webcam sending its frames. Both need the session cookie of a member
of the camera's society, and an Origin of this site (a socket is not covered by SameSite)."""
import asyncio
import base64
import time

from fastapi import FastAPI, WebSocket
from fastapi.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from watchful.auth.deps import session_member
from watchful.auth.sessions import origin_of

CLOSE_SIGN_IN = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_BAD_KIND = 4400


def mount(app: FastAPI, ctx) -> None:
    manager = ctx.manager
    pushers: dict[str, set[int]] = {}

    async def camera_for(ws: WebSocket, camera_id: str):
        """The camera when this socket's session belongs to its society; closes the socket and gives None if not."""
        sent = ws.headers.get("origin")
        member = await run_in_threadpool(session_member, ws)
        if member is None or (sent and origin_of(sent) != ctx.sessions.origin):
            await ws.close(code=CLOSE_SIGN_IN)
            return None
        cam = await run_in_threadpool(ctx.repo_for(member.workspace.id).get_camera, camera_id)
        if cam is None:
            await ws.close(code=CLOSE_NOT_FOUND)
        return cam

    @app.websocket("/ws/cameras/{camera_id}")
    async def live(ws: WebSocket, camera_id: str):
        await ws.accept()
        if await camera_for(ws, camera_id) is None:
            return
        sub = manager.subscribe(camera_id)
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
            manager.unsubscribe(camera_id, sub)

    @app.websocket("/ws/cameras/{camera_id}/push")
    async def push(ws: WebSocket, camera_id: str):
        await ws.accept()
        cam = await camera_for(ws, camera_id)
        if cam is None:
            return
        if cam.kind != "webcam":
            await ws.close(code=CLOSE_BAD_KIND)
            return
        here = pushers.setdefault(camera_id, set())
        here.add(id(ws))
        if not manager.status(camera_id).running:
            await run_in_threadpool(manager.start, camera_id, cam.workspace_id)
        try:
            while True:
                data = await ws.receive_bytes()
                manager.push_frame(camera_id, data, time.time())
        except (WebSocketDisconnect, KeyError, RuntimeError):
            pass
        finally:
            here.discard(id(ws))
            if not here:
                await run_in_threadpool(manager.stop, camera_id)  # nobody is sending this webcam any more

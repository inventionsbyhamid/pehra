"""Request bodies have a size limit, so nobody can fill the server's memory or disk by sending one (a JSON body is
held in memory before a route even checks who is asking; a form's files are written to a temporary file). The limit is
checked against Content-Length before anything is read, and against the bytes as they arrive for a body sent without
one. 1 MB by default (Tuning limits.body_kb); larger only for the routes that take pictures or a video, and only with a
signed in session: staff photos (all of Tuning staff's photos), a zone's picture (limits.example_kb) and an uploaded
video (MAX_UPLOAD_MB). Caddy has its own cap in front (deploy/Caddyfile request_body)."""
import json
import re

from watchful.auth.sessions import Sessions

OVERHEAD = 1 << 20  # a form's field names, boundaries and the other fields around its files
TOO_LARGE = "That is too large to send here."


class _TooLarge(Exception):
    pass


def limits_for(settings, tuning) -> list[tuple[str, re.Pattern, int, str]]:
    """(method, path pattern, bytes, words) for the routes that take more than the default; a signed in session
    only."""
    st = tuning.staff
    photos = int(st.max_photos * st.max_photo_mb * (1 << 20)) + OVERHEAD
    return [("POST", re.compile(r"/api/(?:staff|people)"), photos,
             f"Add up to {st.max_photos} photos, each under {st.max_photo_mb:g} MB."),
            ("POST", re.compile(r"/api/cameras/[^/]+/zones/[^/]+/examples"), tuning.limits.example_kb * 1024 + OVERHEAD,
             f"That picture is larger than {tuning.limits.example_kb // 1024} MB."),
            ("POST", re.compile(r"/api/uploads"), settings.max_upload_mb * (1 << 20) + OVERHEAD,
             f"Videos can be up to {settings.max_upload_mb} MB.")]


class BodyLimit:
    """ASGI middleware refusing a request body over its route's limit with 413, before or while it is read."""

    def __init__(self, app, settings, tuning, sessions: Sessions | None = None):
        self.app, self.default = app, tuning.limits.body_kb * 1024
        self.larger = limits_for(settings, tuning)
        self.sessions = sessions

    def limit(self, scope) -> tuple[int, str]:
        method, path = scope.get("method", ""), scope.get("path", "")
        for m, pattern, size, words in self.larger:
            if method == m and pattern.fullmatch(path) and self._signed_in(scope):
                return size, words
        return self.default, TOO_LARGE

    def _signed_in(self, scope) -> bool:
        if self.sessions is None:
            return True
        from starlette.requests import Request
        return self.sessions.read(Request(scope)) is not None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit, words = self.limit(scope)
        length = dict(scope.get("headers") or []).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            return await _refuse(send, words)
        seen, over, started = 0, False, False

        async def counted():
            nonlocal seen, over
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    over = True
                    raise _TooLarge
            return message

        async def guarded(message):
            nonlocal started
            if over:
                return  # the app's own answer to a body cut short; the 413 goes instead
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not over:
                raise
        if over and not started:
            await _refuse(send, words)


async def _refuse(send, words: str) -> None:
    body = json.dumps({"detail": words}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})

"""Security headers on every HTTP answer: a Content Security Policy (scripts only from this site, never framed), HSTS
when the site is served over https, and no MIME sniffing. A route that sets its own policy keeps it (a stored HTML
evidence page is sandboxed, api/uploads.py). With STORAGE=s3 the bucket is allowed too: the station uploads clips to
it and pages show clips and stills from its signed links."""
from urllib.parse import urlsplit

from watchful.config import Settings

HSTS_S = 31536000  # a year, as the Caddyfile sends


def policy(settings: Settings) -> str:
    base = urlsplit(settings.public_base_url)
    socket = f"{'wss' if base.scheme == 'https' else 'ws'}://{base.netloc}"  # older Safari: 'self' is not ws
    bucket = ""
    if settings.storage == "s3" and settings.s3_bucket:
        bucket = f" https://{settings.s3_bucket}.s3.{settings.aws_region}.amazonaws.com " \
                 f"https://{settings.s3_bucket}.s3.amazonaws.com"
    return "; ".join([
        "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "font-src 'self'",
        f"img-src 'self' data: blob:{bucket}", f"media-src 'self' blob:{bucket}",
        f"connect-src 'self' {socket}{bucket}", "worker-src 'self' blob:", "object-src 'none'",
        "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'"])


class SecurityHeaders:
    """ASGI middleware adding the headers to HTTP answers (not to the station socket)."""

    def __init__(self, app, settings: Settings):
        self.app = app
        https = urlsplit(settings.public_base_url).scheme == "https"
        self.headers = [(b"content-security-policy", policy(settings).encode()),
                        (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                        *([(b"strict-transport-security", f"max-age={HSTS_S}".encode())] if https else [])]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def add(message):
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []), *((k, v) for k, v in self.headers if k not in have)]
            await send(message)

        await self.app(scope, receive, add)

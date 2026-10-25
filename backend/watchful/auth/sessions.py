"""Session and sign in cookies, and the origin check on writes.

The session is a signed (itsdangerous, HMAC SHA 256) HttpOnly, Secure, SameSite=Lax cookie holding the user id and
the current workspace id, valid for Tuning auth.session_days. Secure is dropped only when PUBLIC_BASE_URL is plain
http on localhost; with Secure the names carry the __Host- prefix, so no subdomain can set or shadow them. Every
write that relies on the cookie must come from our own origin (Origin, else Referer), which with SameSite=Lax stops
cross site request forgery."""
import hashlib
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from watchful.auth.providers import Flow, local_http
from watchful.brand import BRAND
from watchful.config import Settings
from watchful.tuning import AuthTuning

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass(frozen=True)
class SessionData:
    user_id: str
    workspace_id: str | None


def origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default = {"http": 80, "https": 443}[parts.scheme]
    try:
        port = parts.port or default
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.hostname}" + ("" if port == default else f":{port}")


def safe_next(value: str | None) -> str | None:
    """A path on this site to return to after sign in, never another site: one leading slash, no backslash
    (browsers read it as a slash) and no control characters (browsers drop tabs and newlines, so a tab between
    two slashes would leave "//")."""
    if not value or len(value) > 512 or not value.startswith("/") or value.startswith("//"):
        return None
    if "\\" in value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    parts = urlsplit(value)
    return None if parts.scheme or parts.netloc else value


class Sessions:
    def __init__(self, settings: Settings, auth: AuthTuning):
        self.session_s = auth.session_days * 86400
        self.flow_s = auth.sign_in_minutes * 60
        self.origin = origin_of(settings.public_base_url)
        self.secure = not local_http(settings.public_base_url)
        prefix = "__Host-" if self.secure else ""
        self.cookie = f"{prefix}watchful"
        self.flow_cookie = f"{prefix}watchful_signin"
        signer = {"digest_method": hashlib.sha256}
        self._session = URLSafeTimedSerializer(settings.session_secret, salt="watchful.session", signer_kwargs=signer)
        self._flow = URLSafeTimedSerializer(settings.session_secret, salt="watchful.signin", signer_kwargs=signer)

    def _set(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(name, value, max_age=max_age, path="/", secure=self.secure, httponly=True, samesite="lax")

    def _drop(self, response: Response, name: str) -> None:
        response.delete_cookie(name, path="/", secure=self.secure, httponly=True, samesite="lax")

    # the session

    def encode(self, user_id: str, workspace_id: str | None) -> str:
        return self._session.dumps({"u": user_id, "w": workspace_id})

    def write(self, response: Response, user_id: str, workspace_id: str | None) -> None:
        self._set(response, self.cookie, self.encode(user_id, workspace_id), self.session_s)

    def read(self, request: Request) -> SessionData | None:
        raw = request.cookies.get(self.cookie)
        if not raw:
            return None
        try:
            data = self._session.loads(raw, max_age=self.session_s)
        except BadSignature:  # includes expired
            return None
        if not isinstance(data, dict) or not isinstance(data.get("u"), str):
            return None
        workspace = data.get("w")
        return SessionData(data["u"], workspace if isinstance(workspace, str) else None)

    def clear(self, response: Response) -> None:
        self._drop(response, self.cookie)

    # one sign in attempt

    def start_flow(self, response: Response, flow: Flow) -> None:
        self._set(response, self.flow_cookie, self._flow.dumps(asdict(flow)), self.flow_s)

    def read_flow(self, request: Request) -> Flow | None:
        raw = request.cookies.get(self.flow_cookie)
        if not raw:
            return None
        try:
            return Flow(**self._flow.loads(raw, max_age=self.flow_s))
        except (BadSignature, TypeError):
            return None

    def clear_flow(self, response: Response) -> None:
        self._drop(response, self.flow_cookie)

    # cross site request forgery

    def check_origin(self, request: Request) -> None:
        """Raises 403 unless the request's Origin (or, without one, its Referer) is PUBLIC_BASE_URL's."""
        if request.method in SAFE_METHODS:
            return
        sent = request.headers.get("origin") or request.headers.get("referer")  # an Origin of "null" fails
        if not sent or self.origin is None or origin_of(sent) != self.origin:
            raise HTTPException(403, f"this request did not come from {BRAND.name}")

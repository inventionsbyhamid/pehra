"""Sign in providers. Each is a registered component (kind `auth`) behind `AuthProvider`; AUTH_PROVIDERS lists the
ones a deployment offers. The router owns the sign in flow (state, nonce, PKCE verifier, the cookie that carries them)
and checks the state before a provider sees the callback, so a provider cannot skip those checks."""
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode, urlsplit

from fastapi import Request

from watchful.config import Settings
from watchful.registry import register

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def local_http(url: str) -> bool:
    """Plain http on this machine: the only place cookies go without Secure and the fake provider may run."""
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str  # the provider's stable id for the person, kept per provider (user_identities)
    email: str  # verified by the provider: a new provider joins an existing user by this address
    name: str | None


@dataclass(frozen=True)
class Flow:
    """One sign in attempt, kept in a short lived signed cookie between start and callback."""
    provider: str
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    next: str | None = None


class AuthError(Exception):
    """Sign in did not finish. `code` is what the login page is told: signin, cancelled or setup."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AuthProvider(Protocol):
    name: str  # in the URLs: /auth/<name>/start and /auth/<name>/callback
    label: str  # on the button: "Continue with <label>"
    configured: bool  # False when its keys are missing: /api/auth/providers leaves it out

    async def start(self, request: Request, flow: Flow) -> str:
        """The URL to send the browser to."""

    async def finish(self, request: Request, flow: Flow) -> Identity:
        """The person, from the callback request; raises AuthError when anything does not check out."""


@register("auth", "fake")
class FakeAuth:
    """Signs in whoever is named, for tests and local development: /auth/fake/start?email=asha@example.com.
    Refuses to exist unless the app is served over plain http on localhost."""
    name = "fake"
    label = "Test sign in"
    configured = True

    def __init__(self, settings: Settings, **_):
        if not local_http(settings.public_base_url):
            raise ValueError("the fake sign in runs only when PUBLIC_BASE_URL is http on localhost")

    async def start(self, request: Request, flow: Flow) -> str:
        email = request.query_params.get("email", "owner@example.com")
        return f"{flow.redirect_uri}?{urlencode({'state': flow.state, 'code': email})}"

    async def finish(self, request: Request, flow: Flow) -> Identity:
        email = request.query_params.get("code", "").strip().lower()
        if "@" not in email:
            raise AuthError("signin", "no email given")
        return Identity("fake", email, email, email.split("@")[0].title())

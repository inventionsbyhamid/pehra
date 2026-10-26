"""FastAPI dependencies for every signed in route:

    def route(member: CurrentMember = Depends(current_member)): ...   # any member of the current society
    def route(member: CurrentMember = Depends(require_role("admin"))): ...

`current_user` checks the session cookie and, on writes, the request's origin; `current_member` checks that the
user still belongs to the society the cookie names, on every request, counts the request against the society's
limit (Tuning limits.api_per_min_workspace) and applies the demo's rules (demo_rules.py: a sandbox that ended
takes no requests, uploads and enrolling faces are off there). Society routes read and write through
`member.workspace.id` only (ctx.repo_for). They find the app's context at `app.state.ctx` (api/app.py)."""
import threading
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from starlette.requests import HTTPConnection

from watchful import demo_rules
from watchful.auth.sessions import SessionData, Sessions
from watchful.config import Settings
from watchful.db import models as m
from watchful.ratelimit import Limiters, TooManyTries
from watchful.tuning import Tuning, load_tuning

ROLES = {"viewer": 0, "supervisor": 1, "admin": 2, "owner": 3}  # each role may do what the ones below it may


class AuthContext:
    """What the account routes and the dependencies share: settings, Tuning, cookies, the platform database and the
    sign in providers. The database and providers are made on first use unless given."""

    def __init__(self, settings: Settings, db=None, providers: dict | None = None, tuning: Tuning | None = None):
        self.settings = settings
        self.tuning = tuning or load_tuning(settings.tuning_file)
        self.sessions = Sessions(settings, self.tuning.auth)
        self.limits = Limiters(self.tuning.limits)
        self._db = db
        self._providers = providers
        self._lock = threading.Lock()

    @property
    def db(self):
        if self._db is None:
            with self._lock:
                if self._db is None:
                    from watchful.components import make_db
                    self._db = make_db(self.settings)
        return self._db

    @property
    def providers(self) -> dict:
        if self._providers is None:
            with self._lock:
                if self._providers is None:
                    from watchful.components import make_auth_providers
                    self._providers = make_auth_providers(self.settings)
        return self._providers


@dataclass(frozen=True)
class CurrentMember:
    user: m.User
    workspace: m.Workspace
    role: str

    def can(self, role: str) -> bool:
        return ROLES.get(self.role, -1) >= ROLES[role]


def auth_context(conn: HTTPConnection) -> AuthContext:
    return conn.app.state.ctx


def current_session(request: Request) -> SessionData:
    ctx = auth_context(request)
    ctx.sessions.check_origin(request)
    data = ctx.sessions.read(request)
    if data is None:
        raise HTTPException(401, "Sign in first.")
    return data


def current_user(request: Request, session: SessionData = Depends(current_session)) -> m.User:
    with auth_context(request).db.tx() as s:
        user = s.get(m.User, session.user_id)
    if user is None:
        raise HTTPException(401, "Sign in first.")
    return user


def membership(ctx: AuthContext, user_id: str, workspace_id: str | None) -> CurrentMember | None:
    """The user's role in this society, read from the database on every call: a removed member is out at once."""
    if not workspace_id:
        return None
    with ctx.db.tx() as s:
        row = s.execute(select(m.User, m.Member, m.Workspace)
                        .join(m.Member, m.Member.user_id == m.User.id)
                        .join(m.Workspace, m.Member.workspace_id == m.Workspace.id)
                        .where(m.User.id == user_id, m.Member.workspace_id == workspace_id)).first()
    if row is None:
        return None
    user, member, workspace = row
    return CurrentMember(user, workspace, member.role)


def session_member(conn: HTTPConnection) -> CurrentMember | None:
    """The member a request's or socket's session cookie names, not counted against the limit: pictures and sockets
    use this. No origin check here; sockets check theirs in api/live.py."""
    ctx = auth_context(conn)
    data = ctx.sessions.read(conn)
    return membership(ctx, data.user_id, data.workspace_id) if data else None


def current_member(request: Request, user: m.User = Depends(current_user),
                   session: SessionData = Depends(current_session)) -> CurrentMember:
    ctx = auth_context(request)
    member = membership(ctx, user.id, session.workspace_id)
    if member is None:
        raise HTTPException(403, "no_workspace")
    try:
        ctx.limits.api.reserve(member.workspace.id)
    except TooManyTries as e:
        raise HTTPException(429, "Too many requests from this society. Wait a minute and try again.",
                            headers={"Retry-After": str(e.retry_after_s)}) from None
    route = getattr(request.scope.get("route"), "path", None) or request.url.path
    if refused := demo_rules.refusal(member.workspace, request.method, route, ctx.db, ctx.tuning.limits.demo_cameras):
        raise HTTPException(403, refused)
    if member.workspace.demo:
        demo_rules.seen(member.workspace.id)
    return member


def require_role(role: str):
    if role not in ROLES:
        raise ValueError(f"no role called {role}")

    def check(member: CurrentMember = Depends(current_member)) -> CurrentMember:
        if not member.can(role):
            raise HTTPException(403, f"Only a society {role} can do this.")
        return member

    return check

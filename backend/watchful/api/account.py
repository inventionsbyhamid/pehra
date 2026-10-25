"""Sign in, the current user, societies, members and invites. Cookie routes go through auth.deps (session, origin
check on writes, membership and role)."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from functools import lru_cache
from html import escape
from typing import Annotated, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from watchful.auth.deps import ROLES, CurrentMember, current_session, current_user, require_role
from watchful.auth.operator import link_valid, take_link
from watchful.auth.providers import AuthError, Flow, Identity
from watchful.auth.sessions import SessionData, safe_next
from watchful.brand import BRAND
from watchful.db import models as m
from watchful.ratelimit import TooManyTries, client_key

log = logging.getLogger(__name__)

MAX_TOKEN_CHARS = 128
LINK_PAGE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
             'content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
             '<title>Sign in to {brand}</title></head><body><main><h1>Sign in to {brand}</h1>{body}</main></body>'
             '</html>')

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Role = Literal["viewer", "supervisor", "admin", "owner"]


@lru_cache
def time_zones() -> frozenset[str]:
    return frozenset(available_timezones())


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceIn(Body):
    name: Name
    timezone: str | None = None  # the deployment's TIMEZONE when not given

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value):
        if value is not None and value not in time_zones():
            raise ValueError("unknown time zone")
        return value


class SwitchIn(Body):
    workspace_id: str


class RoleIn(Body):
    role: Role


class InviteIn(Body):
    role: Role = "supervisor"


class TokenIn(Body):
    token: str = Field(max_length=512)


def iso(value):
    return value.isoformat() if value else None


def user_json(user: m.User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "demo": user.demo}


def workspace_json(ws: m.Workspace) -> dict:
    return {"id": ws.id, "name": ws.name, "timezone": ws.timezone, "plan": ws.plan, "demo": ws.demo,
            "expires_at": iso(ws.expires_at)}


def invite_json(invite: m.Invite) -> dict:
    return {"id": invite.id, "role": invite.role, "expires_at": iso(invite.expires_at)}


def no_store(response: Response) -> None:
    """Answers carrying a secret (an invite link) stay out of every cache."""
    response.headers["Cache-Control"] = "no-store"


def add_workspace(s, user_id: str, name: str, timezone: str, **fields) -> m.Workspace:
    """A new society owned by this user, in the caller's transaction `s`."""
    ws = m.Workspace(name=name, timezone=timezone, **fields)
    s.add(ws)
    s.flush()  # no relationship orders the two inserts
    s.add(m.Member(workspace_id=ws.id, user_id=user_id, role="owner"))
    s.add(m.Audit(workspace_id=ws.id, actor=user_id, action="workspace.create", data={"name": ws.name}))
    return ws


def memberships(db, user_id: str) -> list[tuple[m.Workspace, str]]:
    with db.tx() as s:
        return [(ws, role) for ws, role in s.execute(
            select(m.Workspace, m.Member.role).join(m.Member, m.Member.workspace_id == m.Workspace.id)
            .where(m.Member.user_id == user_id).order_by(m.Workspace.created_at))]


def find_or_make_user(db, identity: Identity) -> tuple[m.User, bool]:
    """The user this provider's subject belongs to. A first sign in with a provider joins the user with the same
    verified email, or makes a new user; either way the subject is kept, for every provider."""
    email = identity.email.strip().lower()
    with db.tx() as s:
        link = s.get(m.UserIdentity, {"provider": identity.provider, "subject": identity.subject})
        user = s.get(m.User, link.user_id) if link else None
        created = False
        if user is None:
            user = s.scalar(select(m.User).where(m.User.email == email))
            if user is not None and s.scalar(select(m.UserIdentity.subject).where(
                    m.UserIdentity.provider == identity.provider, m.UserIdentity.user_id == user.id)) is not None:
                raise AuthError("signin", f"this email belongs to another {identity.provider} account")
            created = user is None
            if created:
                user = m.User(email=email, name=identity.name)
                s.add(user)
                s.flush()  # no relationship orders the two inserts
            s.add(m.UserIdentity(provider=identity.provider, subject=identity.subject, user_id=user.id))
        elif user.email != email and s.scalar(select(m.User.id).where(m.User.email == email)) is None:
            user.email = email  # the account's address changed at the provider
        if identity.name and not user.name:
            user.name = identity.name
    return user, created


def sign_in(db, identity: Identity, previous: SessionData | None) -> tuple[m.User, str | None]:
    """The user for this identity (made on first sign in) and the society to open: the one this browser had open,
    else the oldest the user belongs to, else none (the app asks them to name their society)."""
    for attempt in range(2):
        try:
            user, created = find_or_make_user(db, identity)
            break
        except IntegrityError:  # the same person signing in twice at once
            if attempt:
                raise
    ids = [ws.id for ws, _ in memberships(db, user.id)]
    kept = previous.workspace_id if previous and previous.user_id == user.id else None
    workspace_id = kept if kept in ids else (ids[0] if ids else None)
    db.audit(workspace_id, user.id, "sign_in", {"provider": identity.provider, "new_user": created})
    return user, workspace_id


def link_sign_in(db, user: m.User, workspace_id: str | None) -> str | None:
    """The society an operator's link opens: its own while the user still belongs to it, else the oldest."""
    ids = [ws.id for ws, _ in memberships(db, user.id)]
    opened = workspace_id if workspace_id in ids else (ids[0] if ids else None)
    db.audit(opened, user.id, "sign_in", {"provider": "operator_link", "new_user": False})
    return opened


APP = "/app"  # the control room; "/" is the public site


def landing(next_path: str | None, workspace_id: str | None) -> str:
    next_path = safe_next(next_path)  # checked when the flow started too; a redirect is worth checking twice
    if next_path and (workspace_id or next_path.startswith("/join/")):
        return next_path
    return APP  # with no society yet, the app asks for one


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    settings, sessions, auth = ctx.settings, ctx.sessions, ctx.tuning.auth
    base = settings.public_base_url.rstrip("/")
    invites = URLSafeTimedSerializer(settings.session_secret, salt="watchful.invite",
                                     signer_kwargs={"digest_method": hashlib.sha256})

    def audit(member: CurrentMember, action: str, data: dict) -> None:
        ctx.db.audit(member.workspace.id, member.user.id, action, data)

    # sign in

    def provider(name: str):
        found = ctx.providers.get(name)
        if found is None:
            raise HTTPException(404)
        return found

    @router.get("/api/auth/providers")
    def providers():
        """The sign in buttons to show, in the order AUTH_PROVIDERS lists them; one without its keys is left out."""
        return {"providers": [{"name": name, "label": getattr(p, "label", name)} for name, p in ctx.providers.items()
                              if getattr(p, "configured", True)]}

    def failed(code: str) -> RedirectResponse:
        res = RedirectResponse(f"/login?error={code}", status_code=303)
        sessions.clear_flow(res)
        return res

    def over(limiter, request: Request) -> int | None:
        """Counts a try from this address; the seconds to wait when it has used them all, else None."""
        try:
            limiter.reserve(client_key(request, settings.trust_cloudflare))
        except TooManyTries as e:
            return e.retry_after_s
        return None

    def busy(wait: int) -> RedirectResponse:
        res = failed("busy")
        res.headers["Retry-After"] = str(wait)
        return res

    @router.get("/auth/{name}/start")
    async def start(name: str, request: Request, next: str | None = None):
        chosen = provider(name)
        if (wait := over(ctx.limits.sign_in, request)) is not None:
            return busy(wait)
        flow = Flow(provider=name, state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                    code_verifier=secrets.token_urlsafe(48), redirect_uri=f"{base}/auth/{name}/callback",
                    next=safe_next(next))
        try:
            url = await chosen.start(request, flow)
        except AuthError as e:
            log.warning("sign in with %s could not start: %s", name, e)
            return failed(e.code)
        res = RedirectResponse(url, status_code=303)
        sessions.start_flow(res, flow)
        return res

    @router.get("/auth/{name}/callback")
    async def callback(name: str, request: Request):
        chosen = provider(name)
        flow = sessions.read_flow(request)
        state = request.query_params.get("state", "").encode()
        if flow is None or flow.provider != name or not hmac.compare_digest(flow.state.encode(), state):
            return failed("signin")
        if request.query_params.get("error"):
            return failed("cancelled")
        try:
            identity = await chosen.finish(request, flow)
            user, workspace_id = await run_in_threadpool(sign_in, ctx.db, identity, sessions.read(request))
        except AuthError as e:
            log.warning("sign in with %s failed: %s", name, e)
            return failed(e.code)
        return signed_in(user.id, workspace_id, flow.next)

    def signed_in(user_id: str, workspace_id: str | None, next_path: str | None = None) -> RedirectResponse:
        """The session cookie for a browser that just signed in (a provider's callback or an operator's link)."""
        res = RedirectResponse(landing(next_path, workspace_id), status_code=303)
        sessions.clear_flow(res)
        sessions.write(res, user_id, workspace_id)
        return res

    @router.get("/auth/link/{token}")
    def link_page(token: str, request: Request):
        """An operator's sign in link (auth/operator.py): a button, so a link preview cannot use it up."""
        # same-origin, not no-referrer: with no-referrer the button's POST carries Origin: null, which check_origin
        # refuses; the token still never leaves this site in a Referer
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "same-origin", "X-Robots-Tag": "noindex"}
        page = lambda body: LINK_PAGE.format(brand=escape(BRAND.name), body=body)  # noqa: E731
        valid = len(token) <= MAX_TOKEN_CHARS and link_valid(ctx.db, token)
        if (wait := over(ctx.limits.public if valid else ctx.limits.misses, request)) is not None:  # misses: guessing
            return HTMLResponse(page("<p>Too many tries from this address. Wait a minute.</p>"),
                                status_code=429, headers={**headers, "Retry-After": str(wait)})
        body = (f'<form method="post" action="/auth/link"><input type="hidden" name="token" value="{escape(token)}">'
                f'<button type="submit">Sign in to {escape(BRAND.name)}</button></form>') if valid else \
            "<p>This sign in link has expired or was already used. Ask for a new one.</p>"
        return HTMLResponse(page(body), status_code=200 if valid else 410, headers=headers)

    @router.post("/auth/link")
    async def use_link(request: Request):
        sessions.check_origin(request)
        if (wait := over(ctx.limits.sign_in, request)) is not None:
            return busy(wait)
        async with request.form(max_files=0, max_fields=1) as form:
            token = form.get("token")
        found = None
        if isinstance(token, str) and len(token) <= MAX_TOKEN_CHARS:
            found = await run_in_threadpool(take_link, ctx.db, token)
        if found is None:
            return failed("link")
        user, workspace_id = found
        workspace_id = await run_in_threadpool(link_sign_in, ctx.db, user, workspace_id)
        return signed_in(user.id, workspace_id)

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        sessions.check_origin(request)
        data = sessions.read(request)
        if data:
            ctx.db.audit(data.workspace_id, data.user_id, "sign_out")
        sessions.clear(response)

    # the user and their societies

    @router.get("/api/me")
    def me(user: m.User = Depends(current_user), session: SessionData = Depends(current_session)):
        rows = memberships(ctx.db, user.id)
        here = next(((ws, role) for ws, role in rows if ws.id == session.workspace_id), None)
        return {"user": user_json(user), "workspace": workspace_json(here[0]) if here else None,
                "role": here[1] if here else None,
                "workspaces": [{**workspace_json(ws), "role": role} for ws, role in rows],
                "timezone": here[0].timezone if here else settings.timezone}

    @router.post("/api/me/workspace")
    def switch_workspace(body: SwitchIn, response: Response, user: m.User = Depends(current_user)):
        here = next(((ws, role) for ws, role in memberships(ctx.db, user.id) if ws.id == body.workspace_id), None)
        if here is None:
            raise HTTPException(403, "You are not a member of that society.")
        sessions.write(response, user.id, here[0].id)
        return {"workspace": workspace_json(here[0]), "role": here[1]}

    @router.post("/api/workspaces", status_code=201)
    def create_workspace(body: WorkspaceIn, response: Response, user: m.User = Depends(current_user)):
        if user.demo:
            raise HTTPException(403, "The demo society is deleted after a day. Sign in to make your own.")
        with ctx.db.tx() as s:
            ws = add_workspace(s, user.id, body.name, body.timezone or settings.timezone)
        sessions.write(response, user.id, ws.id)
        return {"workspace": workspace_json(ws), "role": "owner"}

    # members and invites

    def load_invite(token: str) -> m.Invite:
        try:
            data = invites.loads(token, max_age=auth.invite_days * 86400)
        except SignatureExpired:
            raise HTTPException(410, "expired") from None
        except BadSignature:
            raise HTTPException(404, "not_found") from None
        invite_id = data.get("i") if isinstance(data, dict) else None
        with ctx.db.tx() as s:
            invite = s.get(m.Invite, invite_id) if isinstance(invite_id, str) else None
        if invite is None:
            raise HTTPException(404, "not_found")
        if invite.used_at is not None:
            raise HTTPException(410, "used")
        if invite.expires_at <= m.utcnow():
            raise HTTPException(410, "expired")
        return invite

    @router.get("/api/members")
    def members(member: CurrentMember = Depends(require_role("admin"))):
        ws = member.workspace.id
        with ctx.db.tx() as s:
            people = s.execute(select(m.User, m.Member.role).join(m.Member, m.Member.user_id == m.User.id)
                               .where(m.Member.workspace_id == ws).order_by(m.User.email)).all()
            pending = s.scalars(select(m.Invite).where(m.Invite.workspace_id == ws, m.Invite.used_at.is_(None),
                                                       m.Invite.expires_at > m.utcnow())
                                .order_by(m.Invite.created_at)).all()
        return {"members": [{"user_id": u.id, "email": u.email, "name": u.name, "role": role,
                             "you": u.id == member.user.id} for u, role in people],
                "invites": [invite_json(i) for i in pending]}

    def other_member(s, member: CurrentMember, user_id: str) -> m.Member:
        target = s.get(m.Member, {"workspace_id": member.workspace.id, "user_id": user_id})
        if target is None:
            raise HTTPException(404, "No such member.")
        return target

    def last_owner(s, workspace_id: str, target: m.Member) -> bool:
        owners = s.scalar(select(func.count()).select_from(m.Member)
                          .where(m.Member.workspace_id == workspace_id, m.Member.role == "owner"))
        return target.role == "owner" and owners <= 1

    @router.patch("/api/members/{user_id}")
    def change_role(user_id: str, body: RoleIn, member: CurrentMember = Depends(require_role("owner"))):
        with ctx.db.tx() as s:
            target = other_member(s, member, user_id)
            if body.role != "owner" and last_owner(s, member.workspace.id, target):
                raise HTTPException(409, "last_owner")
            target.role = body.role
        audit(member, "member.role", {"user": user_id, "role": body.role})
        return {"user_id": user_id, "role": body.role}

    @router.delete("/api/members/{user_id}", status_code=204)
    def remove_member(user_id: str, member: CurrentMember = Depends(require_role("owner"))):
        with ctx.db.tx() as s:
            target = other_member(s, member, user_id)
            if last_owner(s, member.workspace.id, target):
                raise HTTPException(409, "last_owner")
            s.delete(target)
        audit(member, "member.remove", {"user": user_id})

    @router.get("/api/invites")
    def list_invites(member: CurrentMember = Depends(require_role("admin"))):
        with ctx.db.tx() as s:
            pending = s.scalars(select(m.Invite).where(m.Invite.workspace_id == member.workspace.id,
                                                       m.Invite.used_at.is_(None), m.Invite.expires_at > m.utcnow())
                                .order_by(m.Invite.created_at)).all()
        return {"invites": [invite_json(i) for i in pending]}

    @router.post("/api/invites", status_code=201)
    def create_invite(body: InviteIn, response: Response, member: CurrentMember = Depends(require_role("admin"))):
        if ROLES[body.role] > ROLES[member.role]:
            raise HTTPException(403, "Only a society owner can invite someone with that role.")
        if member.workspace.demo:
            raise HTTPException(403, "Invites are off in the demo. Sign in to invite your team.")
        invite = ctx.db.add(m.Invite(workspace_id=member.workspace.id, role=body.role, created_by=member.user.id,
                                     expires_at=m.utcnow() + timedelta(days=auth.invite_days)))
        audit(member, "invite.create", {"invite": invite.id, "role": invite.role})
        no_store(response)
        return {"invite": invite_json(invite), "url": f"{base}/join/{invites.dumps({'i': invite.id})}"}

    @router.delete("/api/invites/{invite_id}", status_code=204)
    def revoke_invite(invite_id: str, member: CurrentMember = Depends(require_role("admin"))):
        with ctx.db.tx() as s:
            invite = s.scalar(select(m.Invite).where(m.Invite.id == invite_id,
                                                     m.Invite.workspace_id == member.workspace.id))
            if invite is None:
                raise HTTPException(404, "No such invite.")
            s.delete(invite)
        audit(member, "invite.revoke", {"invite": invite_id})

    @router.get("/api/invites/{token}")
    def invite_info(token: str):
        """What the join page shows before anyone accepts: the society, who invited and the role; nothing else."""
        invite = load_invite(token)
        with ctx.db.tx() as s:
            society = s.scalar(select(m.Workspace.name).where(m.Workspace.id == invite.workspace_id))
            inviter = s.get(m.User, invite.created_by) if invite.created_by else None
        return {"society": society, "invited_by": inviter and (inviter.name or inviter.email), "role": invite.role,
                "expires_at": iso(invite.expires_at)}

    @router.post("/api/invites/accept")
    def accept_invite(body: TokenIn, response: Response, user: m.User = Depends(current_user)):
        invite = load_invite(body.token)
        now = m.utcnow()
        with ctx.db.tx() as s:
            already = s.get(m.Member, {"workspace_id": invite.workspace_id, "user_id": user.id})
            if already is None:
                claimed = s.execute(update(m.Invite).where(m.Invite.id == invite.id, m.Invite.used_at.is_(None),
                                                           m.Invite.expires_at > now)
                                    .values(used_at=now, used_by=user.id)).rowcount
                if not claimed:
                    raise HTTPException(410, "used")
                s.add(m.Member(workspace_id=invite.workspace_id, user_id=user.id, role=invite.role))
            workspace = s.get(m.Workspace, invite.workspace_id)
            role = already.role if already else invite.role
        if already is None:
            ctx.db.audit(invite.workspace_id, user.id, "member.join", {"invite": invite.id, "role": role})
        sessions.write(response, user.id, invite.workspace_id)
        return {"workspace": workspace_json(workspace), "role": role}

    return router

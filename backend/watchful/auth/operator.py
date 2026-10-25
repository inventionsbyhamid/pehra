"""Sign in links the operator makes on the server, for a deployment without a sign in provider (the ioflood tunnel's
address changes, so it cannot be a provider's redirect). A link works once, for Tuning auth.signin_link_minutes; only
its token's SHA 256 is stored, and an Audit row records it. No HTTP route makes one.

    python -m watchful.auth signin-link <email> [--workspace NAME | --workspace-id ID] [--role ROLE]

The user is made when missing. With --workspace the society too, the user as owner, or the one of that name the user
already owns. With --workspace-id an existing society (such as `default`, the demo's), which the user joins with
--role (owner by default) unless already a member. Demo users are refused. Opening the link shows a button, so a link
preview cannot use it up; pressing it signs the browser in as the provider callback does (api/account.py)."""
import argparse
import hashlib
import secrets
import sys
from datetime import datetime, timedelta

from sqlalchemy import select, update

from watchful.auth.deps import ROLES
from watchful.db import models as m

MAX_NAME = 80  # as a society named in the app


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_link(db, settings, tuning, email: str, workspace: str | None = None, workspace_id: str | None = None,
              role: str = "owner", now: datetime | None = None) -> tuple[str, datetime]:
    """A new sign in link for `email` and when it stops working."""
    from watchful.api.account import add_workspace

    email, name = email.strip().lower(), (workspace or "").strip()
    if "@" not in email:
        raise ValueError(f"{email} is not an email address")
    if workspace is not None and not 0 < len(name) <= MAX_NAME:
        raise ValueError(f"a society name is 1 to {MAX_NAME} characters")
    if role not in ROLES:
        raise ValueError(f"a role is one of {', '.join(ROLES)}")
    token = secrets.token_urlsafe(32)
    until = (now or m.utcnow()) + timedelta(minutes=tuning.auth.signin_link_minutes)
    with db.tx() as s:
        user = s.scalar(select(m.User).where(m.User.email == email))
        if user is None:
            user = m.User(email=email)
            s.add(user)
            s.flush()
        if user.demo:
            raise ValueError(f"{email} is a demo visitor; demo visitors cannot sign in with a link")
        ws_id = None
        if workspace_id:
            if s.get(m.Workspace, workspace_id) is None:
                raise LookupError(f"no society with the id {workspace_id}")
            if s.get(m.Member, {"workspace_id": workspace_id, "user_id": user.id}) is None:
                s.add(m.Member(workspace_id=workspace_id, user_id=user.id, role=role))
                s.add(m.Audit(workspace_id=workspace_id, actor="operator", action="member.join",
                              data={"user": user.id, "role": role}))
            ws_id = workspace_id
        elif name:
            ws = s.scalar(select(m.Workspace).join(m.Member, m.Member.workspace_id == m.Workspace.id)
                          .where(m.Member.user_id == user.id, m.Member.role == "owner", m.Workspace.name == name,
                                 m.Workspace.demo.is_(None)).order_by(m.Workspace.created_at).limit(1))
            ws_id = (ws or add_workspace(s, user.id, name, settings.timezone)).id
        s.add(m.SignInLink(token_hash=token_hash(token), user_id=user.id, workspace_id=ws_id, expires_at=until))
        s.add(m.Audit(workspace_id=ws_id, actor="operator", action="auth.signin_link",
                      data={"user": user.id, "until": until.isoformat()}))
    return f"{settings.public_base_url.rstrip('/')}/auth/link/{token}", until


def link_valid(db, token: str, now: datetime | None = None) -> bool:
    with db.tx() as s:
        link = s.get(m.SignInLink, token_hash(token))
        return link is not None and link.used_at is None and link.expires_at > (now or m.utcnow())


def take_link(db, token: str, now: datetime | None = None) -> tuple[m.User, str | None] | None:
    """Uses the link up; the user and the society it opens, or None when it is unknown, used, expired, or for a
    demo visitor. One conditional UPDATE, so two presses at once sign in at most one browser."""
    now = now or m.utcnow()
    key = token_hash(token)
    with db.tx() as s:
        taken = s.execute(update(m.SignInLink)
                          .where(m.SignInLink.token_hash == key, m.SignInLink.used_at.is_(None),
                                 m.SignInLink.expires_at > now)
                          .values(used_at=now).execution_options(synchronize_session=False)).rowcount
        if not taken:
            return None
        link = s.get(m.SignInLink, key)
        user = s.get(m.User, link.user_id)
        return (user, link.workspace_id) if user is not None and not user.demo else None


def main(argv: list[str] | None = None, settings=None) -> None:
    from watchful.components import make_db
    from watchful.config import get_settings
    from watchful.tuning import load_tuning

    args = argparse.ArgumentParser(prog="python -m watchful.auth")
    sub = args.add_subparsers(dest="command", required=True)
    one = sub.add_parser("signin-link", help="print a single use sign in link")
    one.add_argument("email")
    which = one.add_mutually_exclusive_group()
    which.add_argument("--workspace", help="the society to open, made with this user as owner when missing")
    which.add_argument("--workspace-id", help="an existing society to open, joined with --role when not a member")
    one.add_argument("--role", default="owner", choices=list(ROLES))
    opts = args.parse_args(argv)
    settings = settings or get_settings()
    try:
        url, until = make_link(make_db(settings), settings, load_tuning(settings.tuning_file), opts.email,
                               opts.workspace, opts.workspace_id, opts.role)
    except (LookupError, ValueError) as e:
        args.exit(2, f"{e}\n")
    print(f"works once, until {until.isoformat(timespec='seconds')}", file=sys.stderr)
    print(url)

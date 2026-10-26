"""Test helpers: a user with a role in a society, and a browser signed in as them (the session cookie the app itself
would set after sign in, sent with this site's Origin)."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from watchful.db import models as m

BASE = "http://localhost:8000"  # Settings' default PUBLIC_BASE_URL
SOCIETY = "ws_test_society"  # the society tests sign in to unless they name another


def society_repo(settings, name: str = "Test society"):
    """The repository of a new society made for this test."""
    from watchful.components import make_engine, make_repo
    from watchful.store.sql import create_workspace
    return make_repo(settings, create_workspace(make_engine(settings), name).id)


def join(app, email: str, workspace_id: str = SOCIETY, role: str = "owner", name: str | None = None) -> str:
    """The user's id, made a member of the society (made too when missing) with this role."""
    ctx = app.state.ctx
    with ctx.db.tx() as s:
        if s.get(m.Workspace, workspace_id) is None:
            s.add(m.Workspace(id=workspace_id, name=name or workspace_id))
            s.flush()
        user = s.scalar(select(m.User).where(m.User.email == email))
        if user is None:
            user = m.User(email=email)
            s.add(user)
            s.flush()
        member = s.get(m.Member, {"workspace_id": workspace_id, "user_id": user.id})
        if member is None:
            s.add(m.Member(workspace_id=workspace_id, user_id=user.id, role=role))
        else:
            member.role = role
        return user.id


def session_cookie(app, user_id: str, workspace_id: str) -> tuple[str, str]:
    sessions = app.state.ctx.sessions
    return sessions.cookie, sessions.encode(user_id, workspace_id)


def browser(app, email: str = "judge@example.com", workspace_id: str = SOCIETY, role: str = "owner",
            base: str = BASE) -> TestClient:
    name, value = session_cookie(app, join(app, email, workspace_id, role), workspace_id)
    client = TestClient(app, base_url=base, headers={"origin": base})
    client.cookies.set(name, value)
    return client


def on_plan(db, workspace_id: str, plan: str = "society") -> None:
    """The society on a plan agreed by hand (no subscription), so that plan's features are in force."""
    with db.tx() as s:
        s.get(m.Workspace, workspace_id).plan = plan

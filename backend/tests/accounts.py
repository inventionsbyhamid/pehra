"""Test helpers: a user with a role in a society, and a browser signed in as them (the session cookie the app itself
would set after sign in, sent with this site's Origin)."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from watchful.db import models as m
from watchful.store.db import DEFAULT_WORKSPACE

BASE = "http://localhost:8000"  # Settings' default PUBLIC_BASE_URL


def join(app, email: str, workspace_id: str = DEFAULT_WORKSPACE, role: str = "owner",
         name: str | None = None) -> str:
    """The user's id, made a member of the society (made too when missing) with this role."""
    ctx = app.state.ctx
    if workspace_id == DEFAULT_WORKSPACE:
        ctx.repo_for(DEFAULT_WORKSPACE)
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


def browser(app, email: str = "judge@example.com", workspace_id: str = DEFAULT_WORKSPACE, role: str = "owner",
            base: str = BASE) -> TestClient:
    name, value = session_cookie(app, join(app, email, workspace_id, role), workspace_id)
    client = TestClient(app, base_url=base, headers={"origin": base})
    client.cookies.set(name, value)
    return client

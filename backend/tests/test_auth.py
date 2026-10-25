"""Sign in, sessions, roles, invites and operator links, through the real app with Google's endpoints mocked
(adapted from packsure e97a509)."""
import base64
import hashlib
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx2  # what authlib and Starlette's test client speak
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from sqlalchemy import func, select

from accounts import join, session_cookie as cookie_for
from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.api.account import landing
from watchful.api.app import create_app
from watchful.auth.sessions import safe_next
from watchful.config import Settings
from watchful.db import models as m
from watchful.db.session import alembic_config
from watchful.registry import build
from watchful.store.db import DEFAULT_WORKSPACE, AlertRow

BASE = "https://watchful.test"
CLIENT_ID = "client-1.apps.googleusercontent.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
SESSION = "__Host-watchful"


class FakeGoogle:
    """Google's token and certificate endpoints: a code the test hands out becomes a signed ID token."""

    def __init__(self, key):
        self.key = key
        self.codes = {}
        self.token_calls = []
        self.certs_auth = []

    def handler(self, request):
        if request.method == "GET" and str(request.url) == CERTS_URL:
            self.certs_auth.append(request.headers.get("authorization"))
            return httpx2.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if request.method == "POST" and str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            self.token_calls.append({**form, "authorization": request.headers.get("authorization")})
            found = self.codes.pop(form.get("code", [""])[0], None)
            if found is None:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            claims, key = found
            id_token = jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)
            return httpx2.Response(200, json={"access_token": "at", "token_type": "Bearer", "expires_in": 3599,
                                              "scope": "openid email profile", "id_token": id_token})
        return httpx2.Response(404)


@pytest.fixture(scope="module")
def google_key():
    return RSAKey.generate_key(2048, parameters={"kid": "test"})


@pytest.fixture
def env(tmp_path, google_key):
    settings = Settings(_env_file=None, session_secret="test secret", public_base_url=BASE, data_dir=tmp_path,
                        web_dist=tmp_path / "no_web", auth_providers=["google"], google_client_id=CLIENT_ID,
                        google_client_secret="google secret")
    app = create_app(settings, llm=FakeLLM([]), manager=StubManager())
    google = FakeGoogle(google_key)
    ctx = app.state.ctx
    ctx.providers["google"].transport = httpx2.MockTransport(google.handler)
    return SimpleNamespace(app=app, google=google, db=ctx.db, settings=settings, client=browser(app))


def browser(app, origin=True):
    return TestClient(app, base_url=BASE, headers={"origin": BASE} if origin else {})


def claims_for(nonce, sub, email, override=None):
    now = int(time.time())
    return {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": sub, "email": email,
            "email_verified": True, "name": email.split("@")[0].title(), "nonce": nonce, "iat": now,
            "exp": now + 3600, **(override or {})}


def sign_in(env, client, sub, email, key=None, **override):
    """The whole Google round trip; returns the callback response and the authorize URL's query."""
    start = client.get("/auth/google/start", follow_redirects=False)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    code = f"code-{sub}-{time.monotonic_ns()}"
    env.google.codes[code] = (claims_for(query["nonce"][0], sub, email, override), key or env.google.key)
    done = client.get("/auth/google/callback", params={"code": code, "state": query["state"][0]},
                      follow_redirects=False)
    return done, query, start


def owner(env, sub, email, society):
    client = browser(env.app)
    sign_in(env, client, sub, email)
    res = client.post("/api/workspaces", json={"name": society, "timezone": "Asia/Kolkata"})
    assert res.status_code == 201, res.text
    return client, res.json()["workspace"]["id"]


def session_cookie(res):
    return next((h for h in res.headers.get_list("set-cookie") if h.startswith(f"{SESSION}=")), None)


def count(db, model, *where):
    with db.tx() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def as_member(env, email, workspace_id, role):
    client = browser(env.app)
    client.cookies.set(*cookie_for(env.app, join(env.app, email, workspace_id, role), workspace_id))
    return client


def test_callback_creates_user_and_session(env):
    with env.db.engine.connect() as c:
        version = c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    assert version == ScriptDirectory.from_config(alembic_config()).get_current_head()

    client, google = env.client, env.google
    done, query, start = sign_in(env, client, "g-1", "Asha@Example.com")
    assert start.status_code in (302, 303)
    url = urlsplit(start.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert query["client_id"] == [CLIENT_ID] and query["response_type"] == ["code"]
    assert query["redirect_uri"] == [f"{BASE}/auth/google/callback"]
    assert query["code_challenge_method"] == ["S256"] and len(query["state"][0]) >= 32
    flow_cookie = start.headers["set-cookie"].lower()
    assert all(flag in flow_cookie for flag in ("httponly", "secure", "samesite=lax"))
    verifier = google.token_calls[0]["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == query["code_challenge"][0]

    assert done.status_code == 303 and done.headers["location"] == "/app"
    cookie = session_cookie(done)
    assert cookie and all(flag in cookie.lower() for flag in ("httponly", "secure", "samesite=lax", "path=/"))
    assert google.certs_auth == [None]
    with env.db.tx() as s:
        users = s.scalars(select(m.User)).all()
        assert [(u.email, u.name) for u in users] == [("asha@example.com", "Asha")]
        links = s.scalars(select(m.UserIdentity)).all()
        assert [(i.provider, i.subject, i.user_id) for i in links] == [("google", "g-1", users[0].id)]
    assert client.get("/api/auth/providers").json() == {"providers": [{"name": "google", "label": "Google"}]}

    me = client.get("/api/me").json()
    assert me["user"]["email"] == "asha@example.com" and me["workspace"] is None
    assert client.get("/api/cameras").status_code == 403  # no society yet
    made = client.post("/api/workspaces", json={"name": "Green Park", "timezone": "Asia/Kolkata"})
    assert made.status_code == 201 and made.json()["role"] == "owner"
    assert client.get("/api/me").json()["workspace"]["name"] == "Green Park"
    assert client.get("/api/cameras").json() == []
    assert client.post("/api/workspaces", json={"name": "X", "timezone": "Mars/Base"}).status_code == 422

    again, _, _ = sign_in(env, browser(env.app), "g-1", "asha@example.com")
    assert again.headers["location"] == "/app"
    assert count(env.db, m.User) == 1 and count(env.db, m.Audit, m.Audit.action == "sign_in") == 2
    with pytest.raises(ValueError):  # the fake provider never runs on a real address
        build("auth", "fake", settings=env.settings)


def test_state_mismatch_rejected(env):
    client, google = env.client, env.google
    start = client.get("/auth/google/start", follow_redirects=False)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    state = query["state"][0]
    google.codes["c1"] = (claims_for(query["nonce"][0], "g-1", "asha@example.com"), google.key)
    forged = "A" + state[1:] if state[0] != "A" else "B" + state[1:]
    bad = client.get("/auth/google/callback", params={"code": "c1", "state": forged}, follow_redirects=False)
    assert bad.status_code == 303 and bad.headers["location"].startswith("/login?error=")
    assert session_cookie(bad) is None
    assert client.get("/auth/google/callback", params={"code": "c1", "state": state},
                      follow_redirects=False).headers["location"].startswith("/login?error=")
    assert browser(env.app).get("/auth/google/callback", params={"code": "c1", "state": state},
                                follow_redirects=False).headers["location"].startswith("/login?error=")
    assert google.token_calls == [] and count(env.db, m.User) == 0
    assert client.get("/api/me").status_code == 401


def test_bad_id_token_rejected(env):
    other_key = RSAKey.generate_key(2048, parameters={"kid": "test"})
    for override, key in [({"aud": "someone-else.apps.googleusercontent.com"}, None),
                          ({"email_verified": False}, None), ({"nonce": "not the one sent"}, None), ({}, other_key)]:
        done, _, _ = sign_in(env, browser(env.app), "g-9", "mallory@example.com", key=key, **override)
        assert done.headers["location"].startswith("/login?error="), override
        assert session_cookie(done) is None
    assert count(env.db, m.User) == 0


def test_cross_origin_write_rejected(env):
    client = env.client
    sign_in(env, client, "g-1", "asha@example.com")
    body = {"name": "Green Park"}
    assert client.post("/api/workspaces", json=body, headers={"origin": "https://evil.example"}).status_code == 403
    bare = TestClient(env.app, base_url=BASE, cookies=client.cookies)
    assert bare.get("/api/me").status_code == 200  # reads need no origin
    assert bare.post("/api/workspaces", json=body).status_code == 403  # neither Origin nor Referer
    assert bare.post("/api/workspaces", json=body, headers={"origin": "null"}).status_code == 403
    assert bare.post("/api/workspaces", json=body,
                     headers={"origin": "https://watchful.test.evil.example"}).status_code == 403
    assert count(env.db, m.Workspace) == 0
    assert bare.post("/api/workspaces", json=body, headers={"referer": f"{BASE}/"}).status_code == 201
    cam = client.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://x"},
                      headers={"origin": "https://evil.example"})
    assert cam.status_code == 403
    assert client.post("/auth/logout", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/logout").status_code == 204 and client.get("/api/me").status_code == 401


@pytest.mark.parametrize("bad", ["//evil.example", "/\\evil.example", "https://evil.example", "javascript:alert(1)",
                                 "/\t/evil.example", " //evil.example", "app", ""])
def test_next_stays_on_site(env, bad):
    assert safe_next(bad) is None and landing(bad, "ws_1") == "/app" and landing(bad, None) == "/app"
    assert landing("/join/abc", None) == "/join/abc" and landing("/alerts?x=1", "ws_1") == "/alerts?x=1"
    start = env.client.get("/auth/google/start", params={"next": bad}, follow_redirects=False)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    env.google.codes["c"] = (claims_for(query["nonce"][0], "g-1", "asha@example.com"), env.google.key)
    done = env.client.get("/auth/google/callback", params={"code": "c", "state": query["state"][0]},
                          follow_redirects=False)
    assert done.headers["location"] == "/app"


def test_roles_enforced(env):
    ws = "ws_green"
    viewer = as_member(env, "viewer@example.com", ws, "viewer")
    supervisor = as_member(env, "guard@example.com", ws, "supervisor")
    admin = as_member(env, "admin@example.com", ws, "admin")
    boss = as_member(env, "owner@example.com", ws, "owner")
    repo = env.app.state.ctx.repo_for(ws)
    alert = repo.add_alert(AlertRow(None, "cam", None, 1.0, "critical", "alert", "Someone in the pool.", None, None,
                                    [], None, "fp"))
    camera = {"name": "Pool", "kind": "stream", "url": "rtsp://pool"}

    assert viewer.get("/api/cameras").status_code == 200 and viewer.get("/api/alerts").status_code == 200
    assert viewer.post("/api/cameras", json=camera).status_code == 403
    assert viewer.post(f"/api/alerts/{alert.id}/feedback", json={"value": "down"}).status_code == 403
    assert viewer.get("/api/people").status_code == 403
    assert supervisor.post(f"/api/alerts/{alert.id}/feedback", json={"value": "down"}).status_code == 200
    assert supervisor.post("/api/cameras", json=camera).status_code == 403
    assert supervisor.get("/api/members").status_code == 403
    cam = admin.post("/api/cameras", json=camera)
    assert cam.status_code == 200 and cam.json()["url"] == "rtsp://pool"
    assert admin.get("/api/members").status_code == 200
    assert admin.post("/api/invites", json={"role": "supervisor"}).status_code == 201
    assert admin.post("/api/invites", json={"role": "owner"}).status_code == 403
    viewer_id = next(p["user_id"] for p in admin.get("/api/members").json()["members"] if p["role"] == "viewer")
    assert admin.delete(f"/api/members/{viewer_id}").status_code == 403
    assert boss.patch(f"/api/members/{viewer_id}", json={"role": "supervisor"}).status_code == 200
    assert viewer.post(f"/api/alerts/{alert.id}/feedback", json={"value": "up"}).status_code == 200
    assert boss.delete(f"/api/members/{viewer_id}").status_code == 204
    assert viewer.get("/api/cameras").status_code == 403  # removed: out at once
    owner_id = next(p["user_id"] for p in boss.get("/api/members").json()["members"] if p["you"])
    assert boss.patch(f"/api/members/{owner_id}", json={"role": "admin"}).status_code == 409  # the last owner
    with env.db.tx() as s:
        actions = set(s.scalars(select(m.Audit.action).where(m.Audit.workspace_id == ws)))
    assert {"camera.add", "alert.feedback", "invite.create", "member.role", "member.remove"} <= actions


def test_invite_link_works_once(env):
    asha, ws = owner(env, "g-1", "asha@example.com", "Green Park")
    made = asha.post("/api/invites", json={"role": "supervisor"})
    assert made.status_code == 201
    url = made.json()["url"]
    assert url.startswith(f"{BASE}/join/") and made.headers["cache-control"] == "no-store"
    token = url.rsplit("/", 1)[1]

    meena = browser(env.app)
    sign_in(env, meena, "g-2", "meena@example.com")
    assert meena.get(f"/api/invites/{token}").json()["society"] == "Green Park"
    joined = meena.post("/api/invites/accept", json={"token": token})
    assert joined.status_code == 200 and joined.json()["workspace"]["id"] == ws
    assert meena.get("/api/me").json()["role"] == "supervisor"

    late = browser(env.app)
    sign_in(env, late, "g-3", "late@example.com")
    assert late.post("/api/invites/accept", json={"token": token}).status_code == 410
    assert late.post("/api/invites/accept", json={"token": token[:-3] + "abc"}).status_code == 404
    assert late.get("/api/me").json()["workspace"] is None


def test_operator_signin_link_works_once(env, capsys):
    from watchful.auth.operator import main as auth_main

    def link(*args) -> str:
        auth_main(["signin-link", *args], settings=env.settings)
        url = capsys.readouterr().out.split()[-1]
        assert url.startswith(f"{BASE}/auth/link/")
        return url

    url = link("Owner@Example.com", "--workspace", "Green Park")
    token = url.rsplit("/", 1)[1]
    with env.db.tx() as s:
        user = s.scalar(select(m.User).where(m.User.email == "owner@example.com"))
        owned = s.scalars(select(m.Workspace).join(m.Member, m.Member.workspace_id == m.Workspace.id)
                          .where(m.Member.user_id == user.id, m.Member.role == "owner")).all()
        (row,) = s.scalars(select(m.SignInLink)).all()
    assert [w.name for w in owned] == ["Green Park"]
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest() and row.workspace_id == owned[0].id
    assert link("owner@example.com", "--workspace", "Green Park") != url  # the society is reused
    assert count(env.db, m.Member, m.Member.user_id == user.id) == 1

    client = browser(env.app)
    shown = client.get(urlsplit(url).path)  # opening it shows a button; link previews cannot use it up
    assert shown.status_code == 200 and token in shown.text and session_cookie(shown) is None
    assert browser(env.app, origin=False).post("/auth/link", data={"token": token}).status_code == 403
    done = client.post("/auth/link", data={"token": token}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/app" and session_cookie(done)
    me = client.get("/api/me").json()
    assert (me["user"]["email"], me["workspace"]["id"], me["role"]) == ("owner@example.com", owned[0].id, "owner")

    other = browser(env.app)
    again = other.post("/auth/link", data={"token": token}, follow_redirects=False)  # used once already
    assert again.headers["location"] == "/login?error=link" and session_cookie(again) is None
    assert other.get(urlsplit(url).path).status_code == 410

    demo = link("judge@example.com", "--workspace-id", DEFAULT_WORKSPACE, "--role", "supervisor")
    judge = browser(env.app)
    assert judge.post("/auth/link", data={"token": demo.rsplit("/", 1)[1]}, follow_redirects=False).status_code == 303
    me = judge.get("/api/me").json()
    assert (me["workspace"]["id"], me["role"]) == (DEFAULT_WORKSPACE, "supervisor")

    with env.db.tx() as s:
        s.add(m.User(email="visitor@example.com", demo=True))
    with pytest.raises(SystemExit) as refused:
        auth_main(["signin-link", "visitor@example.com"], settings=env.settings)
    assert refused.value.code == 2
    with pytest.raises(SystemExit):
        auth_main(["signin-link", "x@example.com", "--workspace-id", "ws_nope"], settings=env.settings)


def test_settings_start_without_a_demo_password(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    s = Settings(session_secret="y", _env_file=None)
    assert not hasattr(s, "demo_password") and s.auth_providers == ["google"]
    assert Settings(session_secret="y", auth_providers="fake, google", _env_file=None).auth_providers == ["fake", "google"]


def test_client_address_trusts_cloudflare_only_when_told(monkeypatch):
    from starlette.requests import Request
    from watchful.ratelimit import client_ip

    def request(peer: str, **headers) -> Request:
        return Request({"type": "http", "client": (peer, 1234), "headers": [
            (k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]})

    sent = request("10.0.0.9", cf_connecting_ip="203.0.113.5")
    assert client_ip(sent, trust_cloudflare=True) == "203.0.113.5"
    assert client_ip(sent, trust_cloudflare=False) == "10.0.0.9"  # anyone could send that header
    assert client_ip(request("127.0.0.1", x_forwarded_for="198.51.100.1, 198.51.100.7"), False) == "198.51.100.7"
    assert client_ip(request("192.0.2.4", x_forwarded_for="198.51.100.7"), False) == "192.0.2.4"  # not from Caddy

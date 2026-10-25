"""Limits and hardening through the real app: sign in attempts per address, API requests per society, guessed link
tokens, the security headers on real answers, and the sign in link page's own referrer policy (adapted from packsure
e97a509 test_limits.py)."""
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from accounts import browser as member
from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.api.app import create_app
from watchful.config import Settings
from watchful.tuning import Tuning

BASE = "https://watchful.test"
GOOGLE = "https://accounts.google.com/"


def make_app(tmp_path, **settings):
    s = Settings(**{"_env_file": None, "session_secret": "s" * 32, "data_dir": tmp_path,
                    "web_dist": tmp_path / "no_web", "public_base_url": BASE,
                    "google_client_id": "client-1.apps.googleusercontent.com", "google_client_secret": "g",
                    **settings})
    return create_app(s, llm=FakeLLM([]), manager=StubManager())


def visitor(app, ip: str | None = None) -> TestClient:
    return TestClient(app, base_url=BASE, headers={"origin": BASE, **({"cf-connecting-ip": ip} if ip else {})})


def start(client: TestClient) -> str:
    return client.get("/auth/google/start", follow_redirects=False).headers["location"]


def test_sign_in_attempts_are_limited_per_address(tmp_path):
    app = make_app(tmp_path, trust_cloudflare=True)  # behind the Cloudflare tunnel
    per_min = Tuning().limits.login_per_min_ip
    judge = visitor(app, "2001:db8:1:2::10")
    for _ in range(per_min - 1):
        assert start(judge).startswith(GOOGLE)
    same_64 = visitor(app, "2001:db8:1:2::99")  # another device of the same visitor: one /64
    assert same_64.post("/auth/link", data={"token": "x"}, follow_redirects=False).headers["location"] == \
        "/login?error=link"
    refused = judge.get("/auth/google/start", follow_redirects=False)
    assert refused.status_code == 303 and refused.headers["location"] == "/login?error=busy"
    assert 0 < int(refused.headers["retry-after"]) <= 60
    assert same_64.post("/auth/link", data={"token": "x"}, follow_redirects=False).headers["location"] == \
        "/login?error=busy"
    assert start(visitor(app, "203.0.113.9")).startswith(GOOGLE)

    # without TRUST_CLOUDFLARE a visitor cannot pick a new address by sending the header
    (tmp_path / "direct").mkdir()
    direct = make_app(tmp_path / "direct")
    answers = [start(visitor(direct, f"198.51.100.{n}")) for n in range(per_min + 1)]
    assert answers[-2].startswith(GOOGLE) and answers[-1] == "/login?error=busy"


def test_api_requests_are_limited_per_society(tmp_path):
    app = make_app(tmp_path)
    app.state.ctx.limits.api.limit = 3
    asha = member(app, "asha@example.com", "ws_green", base=BASE)
    ravi = member(app, "ravi@example.com", "ws_lake", base=BASE)
    assert [asha.get("/api/me").status_code for _ in range(4)] == [200] * 4  # not a society route
    assert [asha.get("/api/cameras").status_code for _ in range(4)] == [200, 200, 200, 429]
    refused = asha.get("/api/alerts")
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0
    assert "minute" in refused.json()["detail"]
    assert ravi.get("/api/cameras").status_code == 200  # another society has its own count


def test_guessed_link_tokens_are_limited(tmp_path):
    app = make_app(tmp_path, trust_cloudflare=True)
    guesser = visitor(app, "49.36.10.20")
    for n in range(Tuning().limits.misses_per_min_ip):
        assert guesser.get(f"/auth/link/guess-{n}").status_code == 410
    refused = guesser.get("/auth/link/guess-x")
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0
    assert visitor(app, "49.36.10.21").get("/auth/link/guess-y").status_code == 410


def test_security_headers_on_real_answers(tmp_path):
    public = TestClient(make_app(tmp_path), base_url=BASE)
    for path in ("/healthz", "/api/me", "/auth/link/x", "/api/invites/x"):
        h = public.get(path).headers
        csp = h["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "unsafe-eval" not in csp, path
        assert h["strict-transport-security"].startswith("max-age=") and h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY" and h["referrer-policy"], path
    (tmp_path / "local").mkdir()
    local = TestClient(make_app(tmp_path / "local", public_base_url="http://localhost:8000"))
    assert "strict-transport-security" not in local.get("/healthz").headers  # plain http on localhost


def test_sign_in_link_page_keeps_its_own_referrer_policy(tmp_path, capsys):
    """The page sends Referrer-Policy: same-origin, not the site's strict-origin-when-cross-origin and not no-referrer
    (its button's POST would then carry Origin: null): a browser that sends no Origin still sends this site's Referer,
    and the token never leaves the site in one."""
    from watchful.auth.operator import main
    app = make_app(tmp_path)
    main(["signin-link", "owner@example.com", "--workspace", "Green Park"], settings=app.state.ctx.settings)
    path = urlsplit(capsys.readouterr().out.split()[-1]).path
    token = path.rsplit("/", 1)[1]
    bare = TestClient(app, base_url=BASE)  # sends no Origin of its own
    page = bare.get(path)
    assert page.status_code == 200 and page.headers["referrer-policy"] == "same-origin"
    assert page.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in page.headers[
        "content-security-policy"]

    def use(**headers):
        return bare.post("/auth/link", data={"token": token}, headers=headers, follow_redirects=False)

    assert use().status_code == 403  # neither Origin nor Referer
    assert use(origin="null").status_code == 403
    assert use(referer="https://evil.example/auth/link").status_code == 403
    done = use(referer=f"{BASE}{path}")  # what the button sends from a browser that leaves out Origin
    assert done.status_code == 303 and done.headers["location"] == "/app"
    assert bare.get("/api/me").json()["user"]["email"] == "owner@example.com"

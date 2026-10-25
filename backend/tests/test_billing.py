"""Plans per watched camera, limits and Polar billing through the real app (from packsure e97a509's webhook tests).
Webhooks are Polar's documented subscription payload (fixtures/polar_subscription_active.json, every field of the
2026-10 Subscription schema, with a unit based price for cameras), signed here with a test secret the way Polar signs
(Standard Webhooks); Polar's own SDK reads each payload too. Polar's API is never called: a stand in client records
what would have been sent, and each call is checked against the SDK's own request types."""
import base64
import copy
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from polar.v2026_10 import inputs as polar_inputs
from polar.v2026_10.errors import AlreadyCanceledSubscription
from polar.v2026_10.webhooks import PolarWebhookVerificationError
from polar.v2026_10.webhooks import validate_event as polar_validate
from sqlalchemy import func, select
from standardwebhooks.webhooks import Webhook

from accounts import browser, join
from test_api import StubManager
from watchful.agent.llm import AILimitReached, FakeLLM, LimitedLLM
from watchful.api import create_app
from watchful.billing import retention_days
from watchful.billing.comp import main as billing_main
from watchful.billing.limits import entitlement, workspace_of
from watchful.billing.plans import PLANS_FILE, catalog
from watchful.billing.state import lapse_plan, lapsed_plans, settle_plans
from watchful.components import make_billing, make_llm
from watchful.config import Settings
from watchful.db import models as m
from watchful.jobs import JobQueue, Worker
from watchful.store.db import AlertRow, Camera, EventRow
from watchful.tuning import Tuning
from watchful.vision.faces import Enrollment
from watchful.workers import run_retention

BASE = "https://society.test"
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "polar_subscription_active.json").read_text())
PRODUCTS = {"society_month": FIXTURE["data"]["product_id"], "society_year": str(uuid.uuid4())}
FREE_STOP = "Your plan watches 2 cameras. Switch one off or move to Society."
GREEN, LAKE = "ws_green_park", "ws_lake_view"


class Manager(StubManager):
    def __init__(self):
        super().__init__()
        self.stopped = []

    def stop(self, camera_id):
        self.stopped.append(camera_id)


class StandInPolar:
    """Polar's client as billing/polar.py uses it. Each call's arguments must be fields of the SDK's request type."""

    def __init__(self, orders=()):
        self.calls, self.order_rows, self.revoked = [], list(orders), set()
        self.checkouts = SimpleNamespace(create=self._checkout)
        self.subscriptions = SimpleNamespace(update=self._update, revoke=self._revoke)
        self.customer_sessions = SimpleNamespace(create=self._session)
        self.refunds = SimpleNamespace(create=self._refund)
        self.orders = SimpleNamespace(iter_list=self._orders)

    @staticmethod
    def _check(kinds, kw):
        """The arguments are the fields of one of these request types."""
        assert any(set(kw) <= set(getattr(polar_inputs, k).__annotations__) for k in kinds), (kinds, sorted(kw))

    def _checkout(self, **kw):
        self._check(["CheckoutCreate"], kw)
        self.calls.append(("checkout", kw))
        return SimpleNamespace(url="https://sandbox.polar.sh/checkout/c1")

    def _update(self, id, **kw):
        self._check(["SubscriptionUpdateUnits", "SubscriptionUpdateClear"], kw)
        self.calls.append(("update", id, kw))
        return SimpleNamespace(id=id)

    def _revoke(self, id):
        self.calls.append(("revoke", id))
        if id in self.revoked:
            raise AlreadyCanceledSubscription(409, None)
        self.revoked.add(id)

    def _session(self, **kw):
        self._check(["CustomerSessionCustomerIDCreate", "CustomerSessionCustomerExternalIDCreate"], kw)
        self.calls.append(("portal", kw))
        return SimpleNamespace(customer_portal_url="https://sandbox.polar.sh/portal/p1")

    def _refund(self, **kw):
        self._check(["RefundCreate"], kw)
        self.calls.append(("refund", kw))

    def _orders(self, **kw):
        self.calls.append(("orders", kw))
        return list(self.order_rows)


def stand_in(orders=()):
    return StandInPolar(orders)


@pytest.fixture
def env(tmp_path):
    secret = "whsec_" + base64.b64encode(os.urandom(32)).decode()
    settings = Settings(_env_file=None, session_secret="s" * 32, public_base_url=BASE, data_dir=tmp_path / "data",
                        web_dist=tmp_path / "no_web", billing="polar", polar_access_token="polar_test_token",
                        polar_webhook_secret=secret, polar_products=PRODUCTS, polar_server="sandbox")
    manager = Manager()
    app = create_app(settings, llm=FakeLLM([]), manager=manager)
    client = stand_in()
    app.state.billing._client = client
    return SimpleNamespace(app=app, ctx=app.state.ctx, db=app.state.ctx.db, secret=secret, settings=settings,
                           manager=manager, polar=TestClient(app, base_url=BASE), client=client)


def owner(env, ws=GREEN, email="asha@example.com", role="owner"):
    return browser(env.app, email, ws, role, base=BASE)


def z(when: datetime | None) -> str | None:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if when else None


def polar_event(kind: str, ws_id: str, at: datetime, *, sub: str = "sub-1", product: str = "society_month",
                status: str = "active", units: int | None = 6, units_next: int | None = None,
                cancel_at_period_end: bool = False, past_due_at: datetime | None = None, days: int = 30):
    """Polar's payload for one subscription as it stood at `at`."""
    payload = copy.deepcopy(FIXTURE)
    payload["type"], payload["timestamp"] = kind, z(at + timedelta(seconds=1))
    d = payload["data"]
    period_end = at + timedelta(days=days)
    d.update(id=sub, status=status, modified_at=z(at), product_id=PRODUCTS[product], customer_id="cus-1",
             current_period_start=z(at), current_period_end=z(period_end), cancel_at_period_end=cancel_at_period_end,
             canceled_at=z(at) if cancel_at_period_end or status == "canceled" else None,
             ends_at=z(period_end) if cancel_at_period_end else None, units=units,
             recurring_interval="year" if product.endswith("year") else "month",
             ended_at=z(at) if status == "canceled" else None, past_due_at=z(past_due_at))
    d["pending_update"] = None if units_next is None else {
        "created_at": z(at), "modified_at": None, "id": str(uuid.uuid4()), "applies_at": z(period_end),
        "product_id": None, "seats": None, "units": units_next}
    d["customer"].update(id="cus-1", external_id=ws_id)
    d["metadata"]["workspace_id"] = ws_id
    d["product"]["id"] = PRODUCTS[product]
    for price in (*d["prices"], *d["product"]["prices"]):
        price["product_id"] = PRODUCTS[product]
    return payload


def signed(secret: str | bytes, body: str, msg_id: str | None = None, when: datetime | None = None) -> dict:
    msg_id, when = msg_id or f"msg_{uuid.uuid4().hex}", when or datetime.now(UTC)
    return {"webhook-id": msg_id, "webhook-timestamp": str(int(when.timestamp())),
            "webhook-signature": Webhook(secret).sign(msg_id, when, body), "content-type": "application/json"}


def deliver(env, payload: dict, msg_id: str | None = None, **kw):
    body = json.dumps(payload)
    return env.polar.post("/api/billing/webhook", content=body, headers=signed(env.secret, body, msg_id, **kw))


def workspace(env, ws_id) -> m.Workspace:
    return workspace_of(env.db, ws_id)


def count(env, model, *where) -> int:
    with env.db.tx() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def payer_id(env, email: str) -> str:
    with env.db.tx() as s:
        return s.scalar(select(m.User.id).where(m.User.email == email))


def no_dashes(text: str) -> bool:
    return not any(c in text for c in "-‐‑‒–—―")


def add_cameras(client, n: int, kind: str = "stream") -> list[dict]:
    out = []
    for i in range(n):
        body = {"name": f"Camera {i + 1}", "kind": kind}
        if kind == "stream":
            body["url"] = f"rtsp://93.184.215.{14 + i}/live"
        res = client.post("/api/cameras", json=body)
        assert res.status_code == 200, res.text
        out.append(res.json())
    return out


def test_plans_file_is_what_the_site_promises():
    """One plans.json for the site (vite aliases @data/plans to it) and the backend, and the promises in it."""
    data = json.loads(PLANS_FILE.read_text())
    cat = catalog()
    free, society, large = (cat.get(p) for p in ("free", "society", "large"))
    assert cat.default == "free" and data["billing_unit"] == "watched_camera"
    assert (free.limits.watched_cameras, free.limits.retention_days, free.per_camera) == (2, 7, False)
    assert society.prices == {"month": 699, "year": 6990} and society.per_camera and society.sold
    assert (society.limits.watched_cameras, society.limits.retention_days) == (49, 30)
    assert large.contact and large.limits.min_cameras == society.limits.watched_cameras + 1 == 50
    assert large.limits.retention_days == 30  # agreed: as long as any plan keeps it, until agreed otherwise
    assert cat.refund_days_yearly == 7 and cat.next_up("free") is society
    for plan in data["plans"]:
        assert set(plan["limits"]) == {"watched_cameras", "min_cameras", "retention_days", "ai_checks_day",
                                       "ai_assist_day", "enrolled_staff"}, plan["id"]
    assert free.limits.ai_checks_day and society.limits.ai_checks_day and free.limits.enrolled_staff
    rate = data["usd_inr"]
    assert data["currency"] == "INR" and 50 < rate["rate"] < 200 and rate["source_url"].startswith("https://")

    site = ROOT / "web" / "src" / "site"
    assert "backend/watchful/data/plans.json" in (ROOT / "web" / "vite.config.ts").read_text()
    types = (site / "data.d.ts").read_text()
    for key in ("watched_cameras", "retention_days", "ai_checks_day", "ai_assist_day", "enrolled_staff",
                "refund_days_yearly"):
        assert f"{key}:" in types, key
    for page in ("Refunds.tsx", "Pricing.tsx", "pages.ts"):
        text = (site / page).read_text()
        assert "{refundDays}" in text and not re.search(r"\b7 days\b", text), page  # the number comes from the file


def test_free_watches_two_cameras_and_says_how_to_watch_more(env):
    a = owner(env)
    first, second = add_cameras(a, 2)
    assert first["enabled"] and second["enabled"] and first["notice"] is None
    third = add_cameras(a, 1, "webcam")[0]  # a phone or the bridge counts the same
    assert third["enabled"] is False and third["notice"] == FREE_STOP and no_dashes(FREE_STOP)
    assert third["id"] not in env.manager.started and first["id"] in env.manager.started

    refused = a.patch(f"/api/cameras/{third['id']}", json={"enabled": True, "name": "Renamed"})
    assert refused.status_code == 403 and refused.json()["detail"] == FREE_STOP
    assert env.ctx.repo_for(GREEN).get_camera(third["id"]).name == "Camera 1"  # nothing changed
    # an uploaded video is not a camera
    with env.db.tx() as s:
        s.add(m.Camera(workspace_id=GREEN, name="Upload", kind="file", enabled=True))
    assert a.patch(f"/api/cameras/{first['id']}", json={"enabled": False}).status_code == 200
    assert a.patch(f"/api/cameras/{third['id']}", json={"enabled": True}).json()["enabled"] is True
    shown = a.get("/api/billing").json()
    assert shown["plan"]["id"] == "free" and shown["cameras"] == {"watched": 2, "allowed": 2, "paid": None,
                                                                   "next": None, "most": 49}
    assert shown["retention_days"] == 7 and shown["ai_checks"]["day"] == 50 and shown["staff"]["allowed"] == 5
    assert shown["can_checkout"] and not shown["can_change_cameras"] and shown["payments"]
    assert a.get("/api/metrics").json()["ai_daily_limit"] == 50
    assert a.get("/api/me").json()["workspace"]["plan"] == "free"


def test_society_for_six_cameras_watches_six(env):
    a = owner(env)
    t = datetime.now(UTC) - timedelta(hours=1)
    assert deliver(env, polar_event("subscription.active", GREEN, t, units=6)).status_code == 200
    cams = add_cameras(a, 7)
    assert [c["enabled"] for c in cams] == [True] * 6 + [False]
    assert cams[-1]["notice"] == "Your plan watches 6 cameras. Switch one off or add a camera to your plan in Billing."
    shown = a.get("/api/billing").json()
    assert shown["plan"] == {"id": "society", "name": "Society", "per_camera": True} and shown["source"] == "paid"
    assert shown["cameras"] == {"watched": 6, "allowed": 6, "paid": 6, "next": None, "most": 49}
    assert shown["period"] == "month"
    assert shown["retention_days"] == 30 and shown["ai_checks"]["day"] == 6 * 40 and shown["staff"]["allowed"] == 300
    assert shown["renews_at"] and shown["can_change_cameras"] and not shown["can_checkout"]
    assert shown["refund_until"] is None  # monthly plans are not refunded
    assert a.get("/api/me").json()["workspace"]["plan"] == "society"

    # 49 is the most Society sells; past that it is Large sites
    assert deliver(env, polar_event("subscription.updated", GREEN, t + timedelta(minutes=1), units=60)).status_code \
        == 200
    ent = entitlement(workspace(env, GREEN))
    assert ent.cameras == 49
    add_cameras(a, 49 - 6)
    last = add_cameras(a, 1)[0]
    assert last["notice"] == "Your plan watches 49 cameras. Switch one off, or email us about Large sites."


def test_webhook_signature_and_size(env):
    payload = polar_event("subscription.active", GREEN, datetime.now(UTC))
    join(env.app, "asha@example.com", GREEN)
    body = json.dumps(payload)
    other = "whsec_" + base64.b64encode(os.urandom(32)).decode()
    stale = datetime.now(UTC) - timedelta(minutes=10)
    for content, headers in [(body, signed(other, body)),  # another secret
                             (body.replace('"units": 6', '"units": 49'), signed(env.secret, body)),  # changed after
                             (body, signed(env.secret, body, when=stale)),  # an old delivery replayed
                             (body, {**signed(env.secret, body), "webhook-signature": "v1"}),  # malformed
                             (body, {"content-type": "application/json"})]:  # not signed at all
        assert env.polar.post("/api/billing/webhook", content=content, headers=headers).status_code == 401
        with pytest.raises(PolarWebhookVerificationError):  # Polar's own SDK agrees
            polar_validate(content, headers, env.secret)
    assert workspace(env, GREEN).plan == "free" and count(env, m.BillingWebhook) == 0

    big = " " * (Tuning().limits.webhook_kb * 1024 + 1)
    assert env.polar.post("/api/billing/webhook", content=big, headers=signed(env.secret, big)).status_code == 413

    headers = signed(env.secret, body)
    read = polar_validate(body, headers, env.secret)  # Polar's SDK reads our fixture, unit price and all
    assert read.data.units == 6 and read.data.prices[0].amount_type == "unit_based"
    assert env.polar.post("/api/billing/webhook", content=body, headers=headers).status_code == 200
    assert (workspace(env, GREEN).plan, workspace(env, GREEN).subscription_units) == ("society", 6)
    # secrets made before 8 September 2026 sign with the whole string's bytes; those still verify
    later = json.dumps(polar_event("subscription.updated", GREEN, datetime.now(UTC) + timedelta(seconds=5), units=8))
    assert env.polar.post("/api/billing/webhook", content=later,
                          headers=signed(env.secret.encode(), later)).status_code == 200
    assert workspace(env, GREEN).subscription_units == 8


def test_webhooks_are_idempotent_and_end_correctly_in_any_order(env):
    join(env.app, "asha@example.com", GREEN)
    t = datetime.now(UTC) - timedelta(hours=1)
    active = polar_event("subscription.active", GREEN, t, sub="sub-a")
    canceled = polar_event("subscription.canceled", GREEN, t + timedelta(minutes=5), sub="sub-a", status="canceled")
    revoked = polar_event("subscription.revoked", GREEN, t + timedelta(minutes=5), sub="sub-a", status="canceled")
    assert deliver(env, canceled).status_code == 200
    assert deliver(env, active, msg_id="msg_active_a").status_code == 200
    writes = count(env, m.Audit, m.Audit.action == "billing.subscription")
    assert deliver(env, active, msg_id="msg_active_a").status_code == 200  # Polar retrying the same delivery
    assert count(env, m.Audit, m.Audit.action == "billing.subscription") == writes
    assert deliver(env, revoked).status_code == 200
    assert workspace(env, GREEN).plan == "free" and entitlement(workspace(env, GREEN)).cameras == 2
    assert count(env, m.BillingWebhook, m.BillingWebhook.id == "msg_active_a") == 1

    # a new subscription, revoked, then its old active replayed: an ended subscription never comes back
    b = polar_event("subscription.active", GREEN, t + timedelta(minutes=10), sub="sub-b", units=4)
    assert deliver(env, b).status_code == 200 and workspace(env, GREEN).subscription_units == 4
    assert deliver(env, polar_event("subscription.revoked", GREEN, t + timedelta(minutes=20), sub="sub-b",
                                    status="canceled")).status_code == 200
    assert deliver(env, b).status_code == 200
    assert deliver(env, polar_event("subscription.updated", GREEN, t + timedelta(minutes=30), sub="sub-b")) \
        .status_code == 200
    assert workspace(env, GREEN).plan == "free"
    # and an old subscription's end arriving late does not end the current one
    assert deliver(env, polar_event("subscription.active", GREEN, t + timedelta(minutes=40), sub="sub-c")) \
        .status_code == 200
    assert deliver(env, revoked).status_code == 200
    assert workspace(env, GREEN).plan == "society"


def test_payment_goes_to_the_society_named_at_checkout(env):
    join(env.app, "asha@example.com", GREEN)
    join(env.app, "asha@example.com", LAKE)  # one secretary for two societies: one Polar customer for both
    t = datetime.now(UTC) - timedelta(hours=1)
    bought = polar_event("subscription.active", LAKE, t, sub="sub-2", units=3)
    bought["data"]["customer"]["external_id"] = GREEN  # Polar's customer was made at Green Park's checkout
    assert deliver(env, bought).status_code == 200
    assert (workspace(env, GREEN).plan, workspace(env, LAKE).plan) == ("free", "society")
    assert workspace(env, LAKE).subscription_units == 3

    with env.db.tx() as s:
        s.get(m.Workspace, GREEN).polar_customer_id = "cus-1"
    stray = polar_event("subscription.active", LAKE, t + timedelta(minutes=5), sub="sub-3")
    stray["data"]["metadata"] = {}
    stray["data"]["customer"]["external_id"] = None
    assert deliver(env, stray).status_code == 200
    assert (workspace(env, GREEN).plan, workspace(env, LAKE).subscription_id) == ("free", "sub-2")
    with env.db.tx() as s:
        (audit,) = s.scalars(select(m.Audit).where(m.Audit.action == "billing.ambiguous_customer")).all()
    assert sorted(audit.data["workspaces"]) == sorted([GREEN, LAKE])

    gone = polar_event("subscription.active", "ws_deleted", t, sub="sub-gone")
    gone["data"]["customer"]["external_id"] = GREEN
    assert deliver(env, gone).status_code == 200 and workspace(env, GREEN).plan == "free"
    unknown = polar_event("subscription.updated", LAKE, t + timedelta(minutes=10), sub="sub-2")
    unknown["data"]["product_id"] = "prod-not-ours"
    assert deliver(env, unknown).status_code == 200 and workspace(env, LAKE).subscription_units == 3
    assert count(env, m.Audit, m.Audit.action == "billing.unknown_product") == 1
    assert count(env, m.Audit, m.Audit.action == "billing.unmatched") == 1


def test_more_cameras_fewer_cameras_cancel_and_revoke(env):
    a = owner(env)
    t = datetime.now(UTC) - timedelta(hours=1)
    assert deliver(env, polar_event("subscription.active", GREEN, t, units=6)).status_code == 200
    cams = add_cameras(a, 6)

    # more, from the app: Polar charges the rest of the period at once and its webhook raises the allowance
    assert a.post("/api/billing/cameras", json={"cameras": 8}).status_code == 200
    assert env.client.calls[-1] == ("update", "sub-1", {"units": 8, "proration_behavior": "invoice"})
    assert entitlement(workspace(env, GREEN)).cameras == 6  # until Polar says so
    assert deliver(env, polar_event("subscription.updated", GREEN, t + timedelta(minutes=1), units=8)).status_code \
        == 200
    cams += add_cameras(a, 2)
    assert all(c["enabled"] for c in cams)

    # fewer: not below what is watched; then from the next renewal, as the refund policy says
    low = a.post("/api/billing/cameras", json={"cameras": 5})
    assert low.status_code == 409 and low.json()["detail"] == "You watch 8 cameras now. Switch 3 off first, then " \
                                                             "lower the number."
    for cam in cams[5:]:
        assert a.patch(f"/api/cameras/{cam['id']}", json={"enabled": False}).status_code == 200
    res = a.post("/api/billing/cameras", json={"cameras": 5})
    assert res.status_code == 200 and res.json()["from"]
    assert env.client.calls[-1] == ("update", "sub-1", {"units": 5, "proration_behavior": "next_period"})
    assert deliver(env, polar_event("subscription.updated", GREEN, t + timedelta(minutes=2), units=8,
                                    units_next=5)).status_code == 200
    shown = a.get("/api/billing").json()
    assert shown["cameras"] == {"watched": 5, "allowed": 8, "paid": 8, "next": 5, "most": 49}
    for bad in (0, 50):
        assert a.post("/api/billing/cameras", json={"cameras": bad}).status_code == 422
    assert owner(env, email="admin@example.com", role="admin").post(
        "/api/billing/cameras", json={"cameras": 6}).status_code == 403

    # cameras switched back on after the decrease, then the renewal applies it: the newest beyond 5 go off
    assert a.patch(f"/api/cameras/{cams[5]['id']}", json={"enabled": True}).status_code == 200
    assert deliver(env, polar_event("subscription.updated", GREEN, t + timedelta(minutes=3), units=5)).status_code \
        == 200
    assert env.manager.stopped[-1:] == [cams[5]["id"]]
    assert env.ctx.repo_for(GREEN).get_camera(cams[5]["id"]).enabled is False
    with env.db.tx() as s:
        (off,) = s.scalars(select(m.Audit).where(m.Audit.action == "billing.cameras_off")).all()
    assert off.data["cameras"] == [cams[5]["id"]] and off.data["allowed"] == 5

    # cancelled for the end of the period: Society until then, Free after it even if the revoke never comes
    cancel = polar_event("subscription.canceled", GREEN, t + timedelta(minutes=4), units=5, cancel_at_period_end=True)
    assert deliver(env, cancel).status_code == 200
    shown = a.get("/api/billing").json()
    assert shown["plan"]["id"] == "society" and shown["ends_at"] and not shown["can_change_cameras"]
    ends = workspace(env, GREEN).subscription_ends_at
    assert entitlement(workspace(env, GREEN), ends - timedelta(minutes=1)).cameras == 5
    after = entitlement(workspace(env, GREEN), ends + timedelta(minutes=1))
    assert (after.plan.id, after.cameras) == ("free", 2)
    # footage saved under Society keeps its 30 days after the plan ends, then Free's 7 apply
    assert retention_days(workspace(env, GREEN), ends + timedelta(days=1)) == 30
    assert retention_days(workspace(env, GREEN), ends + timedelta(days=31)) == 7

    # uncancelled, then revoked at once: Free now, and the cameras beyond 2 go off
    assert deliver(env, polar_event("subscription.uncanceled", GREEN, t + timedelta(minutes=5), units=5)) \
        .status_code == 200
    assert workspace(env, GREEN).subscription_ends_at is None
    assert deliver(env, polar_event("subscription.revoked", GREEN, t + timedelta(minutes=6), units=5,
                                    status="canceled")).status_code == 200
    row = workspace(env, GREEN)
    assert (row.plan, row.subscription_units, entitlement(row).cameras) == ("free", None, 2)
    assert [c.enabled for c in env.ctx.repo_for(GREEN).list_cameras()].count(True) == 2
    assert retention_days(row) == 30 and row.retention_hold_days == 30  # what was saved keeps its 30 days


def test_checkout_and_portal_for_watched_cameras(env):
    a = owner(env)
    add_cameras(a, 2)
    for body, words in (({"plan": "society", "period": "month", "cameras": 1}, "You watch 2 cameras now."),
                        ({"plan": "society", "period": "month", "cameras": 50}, "Society watches 1 to 49 cameras."),
                        ({"plan": "large", "period": "month", "cameras": 60}, "That plan cannot be bought here."),
                        ({"plan": "free", "period": "month", "cameras": 2}, "That plan cannot be bought here.")):
        res = a.post("/api/billing/checkout", json=body)
        assert res.status_code == 422 and res.json()["detail"].startswith(words), res.text
    res = a.post("/api/billing/checkout", json={"plan": "society", "period": "year", "cameras": 6},
                 headers={"cf-connecting-ip": "49.36.10.1"})
    assert res.status_code == 200 and res.json() == {"url": "https://sandbox.polar.sh/checkout/c1", "portal": False}
    kind, sent = env.client.calls[-1]
    assert kind == "checkout" and sent["products"] == [PRODUCTS["society_year"]] and sent["units"] == 6
    assert (sent["min_units"], sent["max_units"]) == (2, 49)  # never fewer than are watched, never past 49
    assert sent["metadata"] == {"workspace_id": GREEN, "user_id": payer_id(env, "asha@example.com"),
                                "plan": "society", "period": "year", "cameras": 6}
    assert sent["external_customer_id"] == GREEN and sent["customer_email"] == "asha@example.com"
    assert sent["success_url"] == f"{BASE}/app/settings/billing?checkout={{CHECKOUT_ID}}"
    assert "customer_ip_address" not in sent  # TRUST_CLOUDFLARE is off, so the header is not believed
    assert owner(env, email="admin@example.com", role="admin").post(
        "/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 2}).status_code == 403
    assert a.get("/api/billing/portal").status_code == 409  # nothing to manage on Free

    assert deliver(env, polar_event("subscription.active", GREEN, datetime.now(UTC), units=6)).status_code == 200
    again = a.post("/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 6})
    assert again.json() == {"url": "https://sandbox.polar.sh/portal/p1", "portal": True}  # never a second subscription
    assert a.get("/api/billing/portal").json() == {"url": "https://sandbox.polar.sh/portal/p1"}
    assert env.client.calls[-1] == ("portal", {"customer_id": "cus-1", "return_url": f"{BASE}/app/settings/billing"})


def test_failed_renewal_gives_grace_then_free(env):
    a = owner(env)
    grace = Tuning().limits.grace_days
    now = datetime.now(UTC)
    failed = now - timedelta(days=grace - 1)
    after = failed + timedelta(days=grace, minutes=1)
    assert deliver(env, polar_event("subscription.active", GREEN, failed - timedelta(days=30), units=4)) \
        .status_code == 200
    past_due = polar_event("subscription.past_due", GREEN, failed, status="past_due", past_due_at=failed, units=4)
    assert deliver(env, past_due).status_code == 200
    assert deliver(env, past_due).status_code == 200  # delivered again: the grace does not start over
    row = workspace(env, GREEN)
    assert row.plan == "society" and abs(row.grace_until - (failed + timedelta(days=grace))) < timedelta(seconds=1)
    shown = a.get("/api/billing").json()
    assert shown["status"] == "past_due" and shown["grace_until"] and shown["cameras"]["allowed"] == 4
    assert not shown["can_change_cameras"]
    assert entitlement(row, after).plan.id == "free"
    assert settle_plans(env.db, now=failed + timedelta(days=grace - 1)) == []
    assert settle_plans(env.db, now=after) == [GREEN]
    row = workspace(env, GREEN)
    assert (row.plan, row.retention_hold_days) == ("free", 30)  # footage from the paid days keeps them
    assert retention_days(row, after) == 30
    assert count(env, m.Audit, m.Audit.workspace_id == GREEN, m.Audit.action == "billing.lapsed") == 1

    recovered = polar_event("subscription.active", GREEN, now, units=4)
    assert deliver(env, recovered).status_code == 200
    assert (workspace(env, GREEN).plan, workspace(env, GREEN).grace_until) == ("society", None)


def test_plan_sweep_keeps_a_payment_that_lands_meanwhile(env):
    join(env.app, "asha@example.com", GREEN)
    t = datetime.now(UTC) - timedelta(days=40)
    assert deliver(env, polar_event("subscription.active", GREEN, t)).status_code == 200
    failed = t + timedelta(days=30)
    assert deliver(env, polar_event("subscription.past_due", GREEN, failed, status="past_due",
                                    past_due_at=failed)).status_code == 200
    (found,) = lapsed_plans(env.db)
    assert deliver(env, polar_event("subscription.active", GREEN, datetime.now(UTC))).status_code == 200
    assert lapse_plan(env.db, found) is False
    assert workspace(env, GREEN).plan == "society" and settle_plans(env.db) == []

    # the daily job: settles plans and forgets old delivery ids
    with env.db.tx() as s:
        s.get(m.BillingWebhook, ("polar", s.scalars(select(m.BillingWebhook.id)).first())).received_at = \
            datetime.now(UTC) - timedelta(days=Tuning().limits.webhook_ids_days + 1)
    worker = Worker(JobQueue(env.db.engine), env.settings, Tuning())
    assert "plan_sweep" in worker.schedule_daily()
    worker.run_due()
    assert count(env, m.BillingWebhook) == 2


def test_a_failed_write_is_retried_not_dropped(env, monkeypatch):
    join(env.app, "asha@example.com", GREEN)
    env.db.audit(GREEN, "test", "test")
    with env.db.tx() as s:
        taken = s.scalars(select(m.Audit.id)).first()
    new_id = m.new_id
    monkeypatch.setattr(m, "new_id", lambda prefix: taken if prefix == "aud" else new_id(prefix))
    body = json.dumps(polar_event("subscription.active", GREEN, datetime.now(UTC)))
    polar = TestClient(env.app, base_url=BASE, raise_server_exceptions=False)
    assert polar.post("/api/billing/webhook", content=body,
                      headers=signed(env.secret, body, "msg_once")).status_code == 500  # Polar will retry
    assert count(env, m.BillingWebhook) == 0 and workspace(env, GREEN).plan == "free"
    monkeypatch.setattr(m, "new_id", new_id)
    assert polar.post("/api/billing/webhook", content=body,
                      headers=signed(env.secret, body, "msg_once")).status_code == 200
    assert workspace(env, GREEN).plan == "society"


def refund_payload(ws_id: str, sub: str, status: str = "refunded", amount: int = 6990 * 100 * 6) -> dict:
    now = z(datetime.now(UTC))
    customer = copy.deepcopy(FIXTURE["data"]["customer"])
    customer.update(external_id=ws_id)
    return {"type": "order.refunded", "timestamp": now, "data": {
        "id": "ord-1", "created_at": now, "modified_at": now, "status": status, "paid": True,
        "subtotal_amount": amount, "discount_amount": 0, "net_amount": amount, "tax_amount": amount * 18 // 100,
        "total_amount": amount * 118 // 100, "refunded_amount": amount, "refunded_tax_amount": amount * 18 // 100,
        "currency": "inr", "billing_reason": "subscription_create", "subscription_id": sub, "customer_id": "cus-1",
        "metadata": {}, "customer": customer}}


def test_a_yearly_plan_is_refunded_within_seven_days_and_ends(env, capsys):
    a = owner(env)
    t = datetime.now(UTC) - timedelta(days=2)
    assert deliver(env, polar_event("subscription.active", GREEN, t, product="society_year", units=6, days=365)) \
        .status_code == 200
    shown = a.get("/api/billing").json()
    assert shown["period"] == "year" and shown["refund_until"][:10] == (t + timedelta(days=7)).date().isoformat()

    order = SimpleNamespace(id="ord-1", status="paid", refundable_amount=6990 * 100 * 6,
                            refundable_tax_amount=6990 * 18 * 6, currency="inr")
    client = stand_in([order, SimpleNamespace(id="ord-0", status="refunded", refundable_amount=0,
                                              refundable_tax_amount=0, currency="inr")])
    provider = make_billing(env.settings)
    provider._client = client
    billing_main(["refund", GREEN], settings=env.settings, provider=provider)  # only says what it would do
    assert "--yes" in capsys.readouterr().out and client.calls == [] and workspace(env, GREEN).plan == "society"
    billing_main(["refund", GREEN, "--yes"], settings=env.settings, provider=provider)
    assert "refunded 1 payment" in capsys.readouterr().out
    assert [c[0] for c in client.calls] == ["orders", "refund", "revoke"]
    assert client.calls[0][1]["subscription_id"] == "sub-1"
    assert client.calls[1][1] == {"order_id": "ord-1", "reason": "customer_request", "amount": 6990 * 100 * 6,
                                  "comment": "yearly plan refunded within the refund window"}
    assert workspace(env, GREEN).plan == "free"
    # Polar's own revoke and refund webhooks arrive afterwards: no change, and the refund is written down once
    assert deliver(env, polar_event("subscription.revoked", GREEN, datetime.now(UTC), product="society_year",
                                    status="canceled")).status_code == 200
    refunded = refund_payload(GREEN, "sub-1")
    assert deliver(env, refunded, msg_id="msg_refund").status_code == 200
    assert deliver(env, refunded, msg_id="msg_refund").status_code == 200
    with env.db.tx() as s:
        (row,) = s.scalars(select(m.Audit).where(m.Audit.action == "billing.refund")).all()
    assert row.workspace_id == GREEN and row.data["full"] and row.data["amount"] == 6990 * 118 * 6
    with pytest.raises(SystemExit):  # nothing left to refund
        billing_main(["refund", GREEN, "--yes"], settings=env.settings, provider=provider)


def test_refunds_are_refused_outside_the_policy_and_credit_the_right_society(env):
    join(env.app, "asha@example.com", GREEN)
    join(env.app, "bina@example.com", LAKE)
    old = datetime.now(UTC) - timedelta(days=8)
    assert deliver(env, polar_event("subscription.active", GREEN, old, product="society_year", days=365)) \
        .status_code == 200
    assert deliver(env, polar_event("subscription.active", LAKE, datetime.now(UTC), sub="sub-lake")).status_code \
        == 200
    provider = make_billing(env.settings)
    provider._client = stand_in()
    for ws in (GREEN, LAKE):  # a year paid 8 days ago, and a monthly plan
        with pytest.raises(SystemExit) as e:
            billing_main(["refund", ws, "--yes"], settings=env.settings, provider=provider)
        assert e.value.code == 2
    assert provider._client.calls == []
    # a refund webhook names only the subscription: it is written down for the society holding it, plan unchanged
    stray = refund_payload("ws_elsewhere", "sub-lake", status="partially_refunded", amount=100)
    stray["data"]["customer"]["external_id"] = None
    assert deliver(env, stray).status_code == 200
    with env.db.tx() as s:
        (row,) = s.scalars(select(m.Audit).where(m.Audit.action == "billing.refund")).all()
    assert row.workspace_id == LAKE and not row.data["full"] and workspace(env, LAKE).plan == "society"


def test_comp_command_gives_a_plan_cameras_and_an_end_date(env, tmp_path):
    a = owner(env)
    db_url = f"sqlite:///{env.settings.data_dir / 'watchful.db'}"
    run_env = {**os.environ, "DATA_DIR": str(env.settings.data_dir), "DATABASE_URL": db_url,
               "SESSION_SECRET": "s" * 32, "PYTHONPATH": str(Path(__file__).parents[1])}
    until = (datetime.now(UTC) + timedelta(days=20)).date().isoformat()
    done = subprocess.run([sys.executable, "-m", "watchful.billing", "comp", GREEN, "society", "--cameras", "4",
                           "--until", until], env=run_env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    row = workspace(env, GREEN)
    assert (row.comp_plan, row.comp_cameras, row.plan, row.subscription_id) == ("society", 4, "free", None)
    assert row.comp_until.astimezone(UTC).date().isoformat() == until  # the end of that day in India, in UTC
    cams = add_cameras(a, 5)
    assert [c["enabled"] for c in cams] == [True] * 4 + [False]
    assert cams[-1]["notice"] == "Your plan watches 4 cameras. Switch one off, or email us to watch more."
    shown = a.get("/api/billing").json()
    assert shown["source"] == "comp" and shown["comp_until"] and shown["can_checkout"]
    assert shown["retention_days"] == 30 and shown["ai_checks"]["day"] == 160
    shown_cli = subprocess.run([sys.executable, "-m", "watchful.billing", "show", GREEN], env=run_env,
                               capture_output=True, text=True, timeout=120)
    assert "Society (comp), watches 4 of 4 cameras, footage kept 30 days" in shown_cli.stdout

    # a paid plan with more cameras wins over the comp while both run
    t = datetime.now(UTC) - timedelta(hours=1)
    assert deliver(env, polar_event("subscription.active", GREEN, t, units=6)).status_code == 200
    assert entitlement(workspace(env, GREEN)).cameras == 6
    assert deliver(env, polar_event("subscription.revoked", GREEN, t + timedelta(minutes=1), status="canceled")) \
        .status_code == 200
    assert (entitlement(workspace(env, GREEN)).source, entitlement(workspace(env, GREEN)).cameras) == ("comp", 4)

    # --end stops it; the app's plan watch switches off the cameras beyond Free's 2 and stops them
    billing_main(["comp", GREEN, "--end"], settings=env.settings)
    row = workspace(env, GREEN)
    assert (row.comp_plan, row.comp_cameras, row.comp_until) == (None, None, None)
    assert retention_days(row) == 30  # footage saved under the comp keeps its days
    env.app.state.plans.run_once()
    assert env.manager.stopped == [cams[2]["id"], cams[3]["id"]]
    assert [c.enabled for c in env.ctx.repo_for(GREEN).list_cameras()] == [True, True, False, False, False]
    with env.db.tx() as s:
        audit = s.scalars(select(m.Audit).where(m.Audit.workspace_id == GREEN, m.Audit.action.like("billing.comp%"))
                          .order_by(m.Audit.ts)).all()
    assert [(x.action, x.actor, x.data.get("cameras")) for x in audit] == [
        ("billing.comp", "operator", 4), ("billing.comp_end", "operator", 4)]

    for bad in (["comp", "ws_nothere", "society", "--cameras", "3", "--days", "3"],
                ["comp", GREEN, "gold", "--cameras", "3", "--days", "3"],
                ["comp", GREEN, "free", "--days", "3"],
                ["comp", GREEN, "society", "--days", "3"],  # per camera: say how many
                ["comp", GREEN, "society", "--cameras", "50", "--days", "3"],
                ["comp", GREEN, "society", "--cameras", "3", "--days", "0"],
                ["comp", GREEN, "society", "--cameras", "3"],
                ["comp", GREEN, "society", "--cameras", "3", "--until", "2020-01-01"],
                ["comp", GREEN, "society", "--end"]):
        with pytest.raises(SystemExit) as e:
            billing_main(bad, settings=env.settings)
        assert e.value.code == 2, bad
    billing_main(["comp", GREEN, "large", "--days", "30"], settings=env.settings)  # Large: no camera limit
    assert entitlement(workspace(env, GREEN)).cameras is None


def test_retention_follows_the_plan(env, tmp_path):
    """Each society's footage goes after its own plan's days: Free 7, Society 30."""
    join(env.app, "asha@example.com", GREEN)
    join(env.app, "bina@example.com", LAKE)
    assert deliver(env, polar_event("subscription.active", LAKE, datetime.now(UTC) - timedelta(hours=1))) \
        .status_code == 200
    assert (retention_days(workspace(env, GREEN)), retention_days(workspace(env, LAKE))) == (7, 30)
    blobs = env.ctx.blobs
    repos = [env.ctx.repo_for(GREEN), env.ctx.repo_for(LAKE)]
    keys = {}
    for repo in repos:
        cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://93.184.215.14/x"))
        for age in (10, 31):
            ts = time.time() - age * 86400
            key = f"{cam.id}/kf/{age}.jpg"
            blobs.put(key, b"jpeg", "image/jpeg")
            path = Path(env.settings.data_dir) / "blobs" / key
            if not path.exists():
                path = next(Path(env.settings.data_dir).rglob(f"{age}.jpg"))
            os.utime(path, (ts, ts))
            repo.add_events([EventRow(None, cam.id, ts, "enter", 1, "person", None, {}, key, None, None, None)])
            repo.add_alert(AlertRow(None, cam.id, None, ts, "warning", "alert", "x", None, key, [], None,
                                    f"fp{age}", 1))
            keys[repo.workspace_id, age] = key
    run_retention(env.settings, repos, blobs)

    def kept(repo, age):
        try:
            blobs.get(keys[repo.workspace_id, age])
            picture = True
        except (OSError, KeyError):
            picture = False
        events = [e.ts for e in repo.query_events(limit=10)]
        alerts = [a.summary for a in repo.list_alerts()]
        return picture, any(abs(time.time() - age * 86400 - t) < 60 for t in events), len(alerts)

    green, lake = repos
    assert kept(green, 10)[:2] == (False, False) and kept(green, 31)[:2] == (False, False)
    assert kept(lake, 10)[:2] == (True, True) and kept(lake, 31)[:2] == (False, False)
    assert len(green.list_alerts()) == 0 and len(lake.list_alerts()) == 1


def test_ai_checks_a_day_follow_the_plan(env):
    join(env.app, "asha@example.com", GREEN)
    repo = env.ctx.repo_for(GREEN)
    llm = make_llm(env.settings, repo, "verify")
    assert isinstance(llm, LimitedLLM)
    limited = LimitedLLM(FakeLLM(["ok"] * 3), repo, llm.limit)
    with env.db.tx() as s:
        s.add_all([m.AiCall(workspace_id=GREEN, task="verify") for _ in range(49)])
    assert limited.complete("s", []) == "ok"
    with pytest.raises(AILimitReached):  # Free's 50 a day
        limited.complete("s", [])
    assert deliver(env, polar_event("subscription.active", GREEN, datetime.now(UTC) - timedelta(hours=1), units=2)) \
        .status_code == 200
    assert llm.limit() == 80 and limited.complete("s", []) == "ok"  # Society: 40 a watched camera, read each call


def test_enrolled_staff_follow_the_plan(env):
    a = owner(env)
    repo = env.ctx.repo_for(GREEN)
    for i in range(5):
        repo.add_enrollment(Enrollment(f"per_{i}", f"Staff {i}", [0.1] * 128))
    res = a.post("/api/people", data={"name": "Ravi", "consent": "true"},
                 files={"photo": ("r.jpg", b"x", "image/jpeg")})
    assert res.status_code == 403
    assert res.json()["detail"] == "Your plan names up to 5 staff members. Remove someone who has left or move to " \
                                   "Society."


def test_without_polar_keys_the_fake_takes_test_payments_on_localhost_only(tmp_path):
    common = dict(_env_file=None, session_secret="s" * 32, data_dir=tmp_path / "data", web_dist=tmp_path / "no")
    assert make_billing(Settings(**common)).name == "fake"  # keys missing
    public = create_app(Settings(**common, public_base_url=BASE), llm=FakeLLM([]), manager=Manager())
    a = browser(public, "asha@example.com", GREEN, base=BASE)
    shown = a.get("/api/billing").json()
    assert shown["provider"] == "fake" and not shown["payments"] and not shown["can_checkout"]
    res = a.post("/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 3})
    assert res.status_code == 502 and res.json()["detail"] == "Payments are not set up on this server yet."
    assert workspace_of(public.state.ctx.db, GREEN).plan == "free"

    local = "http://localhost:8000"
    app = create_app(Settings(**common, public_base_url=local), llm=FakeLLM([]), manager=Manager())
    b = browser(app, "asha@example.com", GREEN, base=local)
    url = b.post("/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 3}).json()["url"]
    assert url.startswith(f"{local}/api/billing/fake/")
    back = b.get(url.removeprefix(local), follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"].startswith(f"{local}/app/settings/billing?checkout=")
    shown = b.get("/api/billing").json()
    assert shown["plan"]["id"] == "society" and shown["cameras"]["allowed"] == 3 and shown["test_mode"]
    assert b.post("/api/billing/cameras", json={"cameras": 5}).status_code == 200
    assert b.get("/api/billing").json()["cameras"]["allowed"] == 5
    end = b.get("/api/billing/portal").json()["url"]
    assert b.get(end.removeprefix(local), follow_redirects=False).status_code == 303
    assert b.get("/api/billing").json()["plan"]["id"] == "free"
    assert b.get("/api/billing/fake/not-a-token").status_code == 404


def test_demo_societies_have_nothing_to_buy(env):
    a = owner(env)
    with env.db.tx() as s:
        s.get(m.Workspace, GREEN).demo = "sandbox"
    res = a.post("/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 2})
    assert res.status_code == 403 and "demo society" in res.json()["detail"] and no_dashes(res.json()["detail"])
    assert not a.get("/api/billing").json()["can_checkout"]


# fix round 1 (task-10-review.md)

def test_a_paying_event_without_units_never_drops_the_cameras(env):
    """I1: a paying or grace snapshot without a camera count keeps the stored count; an unknown count is never 1,
    and nothing is switched off for it."""
    a = owner(env)
    t = datetime.now(UTC) - timedelta(hours=1)
    assert deliver(env, polar_event("subscription.active", GREEN, t, units=6)).status_code == 200
    cams = add_cameras(a, 6)
    for kind, status in (("subscription.updated", "active"), ("subscription.past_due", "past_due")):
        t += timedelta(minutes=1)
        assert deliver(env, polar_event(kind, GREEN, t, units=None, status=status, past_due_at=t)).status_code == 200
        assert (workspace(env, GREEN).subscription_units, entitlement(workspace(env, GREEN)).cameras) == (6, 6)
    assert env.manager.stopped == [] and all(c.enabled for c in env.ctx.repo_for(GREEN).list_cameras())
    assert count(env, m.Audit, m.Audit.action == "billing.no_units") == 2

    # a society whose first paid snapshot has no count: new cameras as on Free, and none of its cameras go off
    b = owner(env, LAKE, "bina@example.com")
    lake = add_cameras(b, 2)
    with env.db.tx() as s:
        s.add_all([m.Camera(workspace_id=LAKE, name=f"Old {i}", kind="rtsp", enabled=True) for i in range(3)])
    assert deliver(env, polar_event("subscription.active", LAKE, t, sub="sub-lake", units=None)).status_code == 200
    ent = entitlement(workspace(env, LAKE))
    assert (ent.plan.id, ent.cameras, ent.exact) == ("society", 2, False)
    assert env.app.state.plans.enforce(LAKE) == [] and env.manager.stopped == []
    assert add_cameras(b, 1)[0]["enabled"] is False and lake[0]["enabled"]


def test_cameras_the_plan_switches_off_are_told_and_marked(env):
    """I2: each camera the plan switches off gets an incident in plain words, keeps the reason the app shows, and
    the billing panel lists it; switching it on again clears the reason."""
    from watchful.alerts.incidents import list_incidents
    a = owner(env)
    t = datetime.now(UTC) - timedelta(hours=1)
    assert deliver(env, polar_event("subscription.active", GREEN, t, units=3)).status_code == 200
    cams = add_cameras(a, 3)
    a.patch(f"/api/cameras/{cams[2]['id']}", json={"name": "Pool camera"})
    assert deliver(env, polar_event("subscription.revoked", GREEN, t + timedelta(minutes=1), units=3,
                                    status="canceled")).status_code == 200
    (incident,) = list_incidents(env.ctx.repo_for(GREEN))
    assert incident.camera_id == cams[2]["id"] and incident.severity == "warning"
    assert incident.reason == "Pool camera is no longer watched: your plan watches 2 cameras."
    assert no_dashes(incident.reason)
    shown = {c["id"]: c for c in a.get("/api/cameras").json()}
    assert shown[cams[2]["id"]]["off_reason"] == "plan" and shown[cams[0]["id"]]["off_reason"] is None
    assert a.get("/api/billing").json()["switched_off"] == [{"id": cams[2]["id"], "name": "Pool camera"}]
    assert a.patch(f"/api/cameras/{cams[0]['id']}", json={"enabled": False}).status_code == 200
    assert a.patch(f"/api/cameras/{cams[2]['id']}", json={"enabled": True}).status_code == 200
    assert a.get(f"/api/cameras/{cams[2]['id']}").json()["off_reason"] is None
    assert a.get("/api/billing").json()["switched_off"] == []
    assert env.app.state.plans.enforce(GREEN) == [] and len(list_incidents(env.ctx.repo_for(GREEN))) == 1


def test_only_the_person_who_paid_opens_the_billing_portal(env):
    """I3: one Polar customer pays for two societies; the portal shows both, so a second owner of one of them gets
    plain words instead of the payer's portal."""
    asha = owner(env, GREEN, "asha@example.com")
    owner(env, LAKE, "asha@example.com")
    bina = owner(env, LAKE, "bina@example.com")
    asha_lake = browser(env.app, "asha@example.com", LAKE, "owner", base=BASE)
    asha.post("/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 2})
    assert env.client.calls[-1][1]["metadata"]["user_id"] == payer_id(env, "asha@example.com")
    t = datetime.now(UTC) - timedelta(hours=1)
    for ws, sub in ((GREEN, "sub-green"), (LAKE, "sub-lake")):
        paid = polar_event("subscription.active", ws, t, sub=sub)
        paid["data"]["metadata"]["user_id"] = payer_id(env, "asha@example.com")
        assert deliver(env, paid).status_code == 200
    assert workspace(env, LAKE).billing_user_id == payer_id(env, "asha@example.com")

    words = "Billing is managed by the person who paid. Email us at"
    for path, call in (("portal", lambda: bina.get("/api/billing/portal")),
                       ("checkout", lambda: bina.post("/api/billing/checkout",
                                                      json={"plan": "society", "period": "month", "cameras": 6}))):
        res = call()
        assert res.status_code == 403 and res.json()["detail"].startswith(words), path
    assert all(c[0] != "portal" for c in env.client.calls)
    shown = bina.get("/api/billing").json()
    assert not shown["payer"] and not shown["can_manage"]
    assert asha_lake.get("/api/billing/portal").status_code == 200 and asha_lake.get("/api/billing").json()["payer"]
    # a subscription without our metadata: the provider's customer email decides
    assert deliver(env, polar_event("subscription.active", GREEN, t + timedelta(minutes=1), sub="sub-other")) \
        .status_code == 200
    row = workspace(env, GREEN)
    assert (row.billing_user_id, row.billing_email) == (None, "asha@example.com")
    assert asha.get("/api/billing/portal").status_code == 200


def test_a_refused_camera_change_never_leaves_it_switched_on(env):
    """I4: everything in a PATCH is checked before watching is switched on."""
    a = owner(env)
    cam = add_cameras(a, 1)[0]
    assert a.patch(f"/api/cameras/{cam['id']}", json={"enabled": False}).status_code == 200
    zone = {"name": "children", "polygon": [[0, 0], [1, 0], [1, 1]]}  # a name read as an age is refused
    for body in ({"enabled": True, "zones": [zone]}, {"enabled": True, "purpose": "nonsense"}):
        assert a.patch(f"/api/cameras/{cam['id']}", json=body).status_code == 400
        assert env.ctx.repo_for(GREEN).get_camera(cam["id"]).enabled is False
    assert a.patch(f"/api/cameras/{cam['id']}", json={"enabled": True}).json()["enabled"] is True


def test_a_lowering_waiting_for_the_renewal_can_be_undone(env):
    """M3: asking for the cameras already paid for clears Polar's pending update."""
    a = owner(env)
    t = datetime.now(UTC) - timedelta(hours=1)
    assert deliver(env, polar_event("subscription.active", GREEN, t, units=8, units_next=5)).status_code == 200
    assert a.post("/api/billing/cameras", json={"cameras": 8}).status_code == 200
    assert env.client.calls[-1] == ("update", "sub-1", {"pending_update": None})
    assert deliver(env, polar_event("subscription.updated", GREEN, t + timedelta(minutes=1), units=8)).status_code \
        == 200
    assert a.post("/api/billing/cameras", json={"cameras": 8}).json() == {"ok": True, "cameras": 8, "from": None}
    assert env.client.calls[-1][0] == "update" and len([c for c in env.client.calls if c[0] == "update"]) == 1


def test_unmatched_billing_events_are_listed_for_the_operator(env, capsys):
    """M2: a purchase outside our checkout (a public product link carries no society) is listed."""
    stray = polar_event("subscription.active", "ws_unknown", datetime.now(UTC), sub="sub-stray")
    stray["data"]["metadata"] = {}
    stray["data"]["customer"].update(external_id=None, id="cus-x")
    stray["data"]["customer_id"] = "cus-x"
    assert deliver(env, stray).status_code == 200
    billing_main(["unmatched"], settings=env.settings)
    out = capsys.readouterr().out
    assert "billing.unmatched" in out and "sub-stray" in out and "cus-x" in out
    billing_main(["unmatched", "--days", "0"], settings=env.settings)
    assert "nothing unmatched" in capsys.readouterr().out


def test_one_ai_check_counts_once_and_help_has_its_own_limit(env, tmp_path):
    """M6: the plan's AI checks count verifier checks, one each however many steps; writing watches, questions and
    search count against the separate help limit."""
    from test_agent import cand, ctx, step
    from watchful.agent.verifier import verify
    from watchful.billing.limits import ASSIST, CHECKS, ai_used
    join(env.app, "asha@example.com", GREEN)
    repo = env.ctx.repo_for(GREEN)
    checks = make_llm(env.settings, repo, "verify")
    checks.inner = FakeLLM([step("crop_zoom", at_s=0), step("crop_zoom", at_s=1),
                            step("raise_alert", summary="A car drove in.")])
    assert verify(cand(), ctx(tmp_path), checks).status == "alert"
    assert (ai_used(repo, CHECKS), ai_used(repo, ASSIST)) == (1, 0)  # three steps, one check

    helper = make_llm(env.settings, repo, "compile")
    helper.inner = FakeLLM(["ok"] * 40)
    with env.db.tx() as s:
        s.add_all([m.AiCall(workspace_id=GREEN, task="verify") for _ in range(49)])
    assert helper.complete("s", []) == "ok"  # Free's 50 checks are all but used: help is counted apart
    for _ in range(29):
        helper.complete("s", [])
    with pytest.raises(AILimitReached):  # Free's 30 help requests a day
        helper.complete("s", [])
    assert (ai_used(repo, CHECKS), ai_used(repo, ASSIST)) == (50, 30)
    checks.inner = FakeLLM([step("raise_alert", summary="x")])
    assert verify(cand("critical"), ctx(tmp_path), checks).status == "unverified"  # the 51st check: not checked
    assert make_llm(env.settings, repo, "search").limit() == 30
    shown = owner(env).get("/api/billing").json()
    assert shown["ai_checks"] == {"today": 50, "day": 50} and shown["ai_help"] == {"today": 30, "day": 30}


def test_counts_is_gone_and_a_society_within_its_plan_takes_no_lock(env, monkeypatch):
    """M7 and M8."""
    from watchful.billing import limits
    assert not hasattr(limits, "counts")
    a = owner(env)
    add_cameras(a, 2)

    def no_lock(*_):
        raise AssertionError("locked a society within its plan")
    monkeypatch.setattr(limits, "_lock", no_lock)
    assert limits.over_allowance(env.db, GREEN).cameras == []
    env.app.state.plans.run_once()


def test_billing_numbers_come_from_tuning_and_the_plan(env, capsys):
    """M9: the refund's margin and the panel's polling come from Tuning, the most cameras from the plan."""
    tuning = Tuning()
    a = owner(env)
    shown = a.get("/api/billing").json()
    assert shown["poll"] == {"every_ms": int(tuning.limits.billing_poll_s * 1000),
                             "tries": tuning.limits.billing_poll_tries}
    assert shown["cameras"]["most"] == catalog().get("society").limits.watched_cameras
    start = datetime.now(UTC) - timedelta(days=1)
    assert deliver(env, polar_event("subscription.active", GREEN, start, product="society_year", days=365)) \
        .status_code == 200
    expected = workspace(env, GREEN).subscription_period_start - timedelta(seconds=tuning.limits.refund_orders_margin_s)
    provider = make_billing(env.settings)
    provider._client = stand_in()
    billing_main(["refund", GREEN, "--yes"], settings=env.settings, provider=provider)
    since = datetime.fromisoformat(provider._client.calls[0][1]["created_after"])
    assert abs(since - expected) < timedelta(seconds=1)
    assert "refunded 0 payment" in capsys.readouterr().out

"""Billing without a payment provider (BILLING=fake, and whenever Polar's keys are missing). The plan page works
everywhere; test payments only on http localhost, as the fake sign in: there checkout sends the owner to a signed
local link that makes the plan active at once, changing cameras applies at once, and the portal link ends the test
plan. Anywhere else checkout says payments are not set up, so a public server without keys never gives a plan away.

Its webhooks are Standard Webhooks signed with POLAR_WEBHOOK_SECRET (or one made from SESSION_SECRET) whose body is
a BillingEvent as JSON, so a script can drive the webhook route the way a provider would (localhost only)."""
import base64
import hashlib
import json
from dataclasses import asdict, fields
from datetime import datetime, timedelta

from itsdangerous import BadSignature as BadToken
from itsdangerous import URLSafeTimedSerializer

from watchful.auth.providers import local_http
from watchful.billing.plans import catalog
from watchful.billing.polar import BILLING_PAGE, verify_standard_webhook
from watchful.billing.provider import BadSignature, BillingError, BillingEvent, Refunded
from watchful.db import models as m
from watchful.registry import register

PERIOD_DAYS = {"month": 30, "year": 365}
NOT_HERE = "Payments are not set up on this server yet."


@register("billing", "fake")
class FakeBilling:
    test_mode = True

    def __init__(self, settings, tuning, name: str = "fake", **_):
        self.name = name
        self.payments = local_http(settings.public_base_url)
        self.link_s = tuning.limits.billing_link_s
        self.base = settings.public_base_url.rstrip("/")
        self.links = URLSafeTimedSerializer(settings.session_secret, salt="watchful.fake-billing",
                                            signer_kwargs={"digest_method": hashlib.sha256})
        self.secret = settings.polar_webhook_secret or "whsec_" + base64.b64encode(
            hashlib.sha256(f"watchful.fake-billing {settings.session_secret}".encode()).digest()).decode()

    def _local(self) -> None:
        if not self.payments:
            raise BillingError(NOT_HERE)

    def checkout(self, workspace, plan: str, period: str, cameras: int, success_url: str, **_) -> str:
        self._local()
        if period not in catalog().get(plan).prices or not catalog().get(plan).sold:
            raise BillingError(f"{plan} is not sold by the {period}")
        token = self.links.dumps({"do": "buy", "w": workspace.id, "plan": plan, "period": period, "n": cameras,
                                  "next": success_url})
        return f"{self.base}/api/billing/fake/{token}"

    def change_cameras(self, workspace, cameras: int) -> BillingEvent:
        self._local()
        now = m.utcnow()
        fewer = cameras < (workspace.subscription_units or 0)
        return BillingEvent(id=f"fake_units_{workspace.subscription_id}_{now.timestamp()}", type="subscription.updated",
                            workspace_id=workspace.id, customer_id=workspace.polar_customer_id,
                            subscription_id=workspace.subscription_id, plan=workspace.plan,
                            period=workspace.subscription_period, status=workspace.subscription_status, changed_at=now,
                            units=workspace.subscription_units if fewer else cameras,
                            units_next=cameras if fewer else None, period_start=workspace.subscription_period_start,
                            period_end=workspace.subscription_period_end)

    def portal_url(self, workspace) -> str:
        self._local()
        token = self.links.dumps({"do": "end", "w": workspace.id, "next": f"{self.base}{BILLING_PAGE}"})
        return f"{self.base}/api/billing/fake/{token}"

    def refund(self, workspace, since: datetime) -> Refunded:
        return Refunded([], 0, "inr")  # nothing was paid

    def follow(self, token: str, subscription_of=lambda workspace_id: None) -> tuple[BillingEvent, str]:
        """The event a fake link stands for, and where to go next (always our own page). Ending the test plan ends
        the society's current subscription, `subscription_of(workspace_id)`."""
        try:
            if not self.payments:
                raise BadToken("payments are off here")
            data = self.links.loads(token, max_age=self.link_s)
        except BadToken:
            raise BadSignature("this test billing link is not valid or has expired") from None
        now = m.utcnow()
        ws = data["w"]
        if data["do"] == "buy":
            sub = f"fake_sub_{now.strftime('%Y%m%d%H%M%S%f')}"
            event = BillingEvent(id=f"fake_{sub}", type="subscription.active", workspace_id=ws,
                                 customer_id=f"fake_cus_{ws}", subscription_id=sub, plan=data["plan"],
                                 period=data["period"], status="active", changed_at=now, units=data["n"],
                                 period_start=now, period_end=now + timedelta(days=PERIOD_DAYS[data["period"]]))
            return event, data["next"].replace("{CHECKOUT_ID}", sub)
        sub = subscription_of(ws) or f"fake_sub_{ws}"
        event = BillingEvent(id=f"fake_end_{sub}_{now.timestamp()}", type="subscription.revoked", workspace_id=ws,
                             customer_id=f"fake_cus_{ws}", subscription_id=sub, plan=None, period=None,
                             status="canceled", changed_at=now)
        return event, data["next"]

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> BillingEvent | None:
        if not self.payments:
            raise BadSignature("test billing webhooks are taken on localhost only")
        verify_standard_webhook(self.secret, headers, body)
        data = json.loads(body)
        known = {f.name for f in fields(BillingEvent)}
        values = {k: v for k, v in data.items() if k in known}
        for k in ("changed_at", "period_start", "period_end", "ends_at", "past_due_at"):
            if values.get(k):
                values[k] = datetime.fromisoformat(values[k])
        return BillingEvent(**values)


def event_json(event: BillingEvent) -> str:
    """A BillingEvent as the fake provider's webhook body."""
    return json.dumps({k: v.isoformat() if isinstance(v, datetime) else v for k, v in asdict(event).items()})

"""Polar (merchant of record), per watched camera, through polar-sdk pinned to API version 2026-10, with webhooks
checked by Standard Webhooks.

Each plan and period is one Polar product with a unit based price (Polar's "unit_based" amount type: the buyer picks
a quantity and pays for it up front; quantity changes are prorated). Society is ₹699 a camera a month and ₹6,990 a
camera a year, with the unit label camera and cameras and one tier bounded at the plan's watched_cameras (49), so
Polar itself sells 1 to 49. A subscription's `units` are its watched cameras. POLAR_PRODUCTS maps society_month and
society_year to the product ids; events for other products change nothing.

Each checkout names its society in the metadata (`workspace_id`), which Polar copies to the subscription, so every
subscription event carries it. The external customer id is only the first society the customer paid for (Polar keeps
one customer per email), a fallback for events without the metadata. More cameras are charged at once for the rest of
the period (proration "invoice"); fewer take effect from the next renewal ("next_period", Polar's pending update), as
the refund policy says. Sandbox or production follows POLAR_SERVER."""
import json
import logging
from datetime import datetime

from standardwebhooks.webhooks import EmptyWebhookSecretError, Webhook, WebhookVerificationError

from watchful.billing.plans import catalog, product_key
from watchful.billing.provider import BadSignature, BillingError, BillingEvent, Refunded, RefundNotice
from watchful.registry import register

log = logging.getLogger(__name__)

BILLING_PAGE = "/app/settings/billing"
API_VERSION = "2026-10"  # the webhook endpoint's API version in Polar's settings, and the SDK's
MORE, FEWER = "invoice", "next_period"  # proration when cameras are added, and when they are removed
REFUND_REASON = "customer_request"


def verify_standard_webhook(secret: str | None, headers: dict[str, str], body: bytes) -> None:
    """Polar's secrets made since 8 September 2026 are Standard Webhooks secrets (`whsec_` and base64); older ones
    sign with the UTF-8 bytes of the whole string. Either is accepted, as Polar's SDK does."""
    if not secret:
        raise BadSignature("no webhook secret is set")
    for key in (secret, secret.encode()):
        try:
            Webhook(key).verify(body, headers, json_parse=False)
            return
        # ValueError covers a secret or signature that is not base64 (binascii.Error), a body that is not UTF-8
        # (UnicodeDecodeError) and a signature without its "v1," part
        except (WebhookVerificationError, EmptyWebhookSecretError, ValueError):
            continue
    raise BadSignature("the webhook signature does not match")


def when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def configured(settings) -> bool:
    """Polar's keys are all there."""
    return bool(settings.polar_access_token and settings.polar_webhook_secret and settings.polar_products)


@register("billing", "polar")
class PolarBilling:
    test_mode = False
    payments = True

    def __init__(self, settings, tuning, name: str = "polar", client=None, **_):
        self.name = name
        self.settings = settings
        self.timeout_s = tuning.limits.billing_api_timeout_s
        self.test_mode = settings.polar_server != "production"
        self.base = settings.public_base_url.rstrip("/")
        self._client = client  # a polar.v2026_10.Polar; tests pass a stand in
        known = catalog().by_product_key()
        self.products = {k: v for k, v in settings.polar_products.items() if k in known}
        self.plans = {pid: known[key] for key, pid in self.products.items()}  # product id to (plan, period)

    def client(self):
        if self._client is None:
            if not self.settings.polar_access_token:
                raise BillingError("POLAR_ACCESS_TOKEN is not set")
            from polar.v2026_10 import Polar
            self._client = Polar(self.settings.polar_access_token, environment=self.settings.polar_server,
                                 timeout=self.timeout_s)
        return self._client

    def _call(self, what: str, fn):
        try:
            return fn()
        except BillingError:
            raise
        except Exception as e:
            log.warning("Polar %s failed: %s", what, e)
            raise BillingError(f"Polar {what} failed") from e

    def checkout(self, workspace, plan: str, period: str, cameras: int, success_url: str, *, payer_id: str,
                 watched: int = 1, email: str | None = None, customer_ip: str | None = None) -> str:
        product = self.products.get(product_key(plan, period))
        if not product:
            raise BillingError(f"no Polar product for {product_key(plan, period)}; add it to POLAR_PRODUCTS")
        most = catalog().get(plan).limits.watched_cameras
        body = {"products": [product], "units": cameras, "min_units": max(1, watched),
                "external_customer_id": workspace.id, "success_url": success_url,
                "return_url": f"{self.base}{BILLING_PAGE}",
                "metadata": {"workspace_id": workspace.id, "user_id": payer_id, "plan": plan, "period": period,
                             "cameras": cameras}}
        if most is not None:
            body["max_units"] = most
        if email and not workspace.polar_customer_id:
            body["customer_email"] = email
        if customer_ip:
            body["customer_ip_address"] = customer_ip  # Polar picks the country (and so the tax) from it
        return self._call("checkout", lambda: self.client().checkouts.create(**body)).url

    def change_cameras(self, workspace, cameras: int) -> None:
        if not workspace.subscription_id:
            raise BillingError("no subscription to change")
        sub = workspace.subscription_id
        if cameras == workspace.subscription_units and workspace.subscription_units_next is not None:
            self._call("subscription update", lambda: self.client().subscriptions.update(sub, pending_update=None))
            return None  # the lowering waiting for the renewal is dropped
        proration = MORE if cameras > (workspace.subscription_units or 0) else FEWER
        self._call("subscription update", lambda: self.client().subscriptions.update(
            sub, units=cameras, proration_behavior=proration))
        return None  # subscription.updated brings the change

    def portal_url(self, workspace) -> str:
        who = ({"customer_id": workspace.polar_customer_id} if workspace.polar_customer_id
               else {"external_customer_id": workspace.id})
        session = self._call("customer session", lambda: self.client().customer_sessions.create(
            **who, return_url=f"{self.base}{BILLING_PAGE}"))
        return session.customer_portal_url

    def refund(self, workspace, since: datetime) -> Refunded:
        sub = workspace.subscription_id
        if not sub:
            raise BillingError("no subscription to refund")
        orders = self._call("orders", lambda: list(self.client().orders.iter_list(
            subscription_id=sub, created_after=since.isoformat())))
        done, amount, currency = [], 0, "inr"
        for order in orders:
            if order.status not in ("paid", "partially_refunded") or order.refundable_amount <= 0:
                continue
            self._call("refund", lambda o=order: self.client().refunds.create(
                order_id=o.id, reason=REFUND_REASON, amount=o.refundable_amount,
                comment="yearly plan refunded within the refund window"))
            done.append(order.id)
            amount += order.refundable_amount + order.refundable_tax_amount
            currency = order.currency
        self.revoke(sub)
        return Refunded(done, amount, currency)

    def revoke(self, subscription_id: str) -> None:
        from polar.v2026_10.errors import AlreadyCanceledSubscription
        try:
            self._call("revoke", lambda: self.client().subscriptions.revoke(subscription_id))
        except BillingError as e:
            if not isinstance(e.__cause__, AlreadyCanceledSubscription):
                raise

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> BillingEvent | RefundNotice | None:
        verify_standard_webhook(self.settings.polar_webhook_secret, headers, body)
        payload = json.loads(body)
        kind = payload.get("type") or ""
        if payload.get("api_version") not in (None, API_VERSION):
            log.warning("a Polar webhook of API version %s, not %s: set the endpoint's version in Polar",
                        payload.get("api_version"), API_VERSION)
        delivery = {k.lower(): v for k, v in headers.items()}["webhook-id"]
        if kind == "order.refunded":
            return self._refund(delivery, kind, payload)
        if not kind.startswith("subscription."):
            return None  # orders, checkouts and customers: the subscription events carry all billing needs
        sub = payload["data"]
        customer = sub.get("customer") or {}
        plan, period = self.plans.get(sub.get("product_id"), (None, None))
        # checkout metadata names the society paid for; the customer's external id is only the first society that
        # customer (one email) paid for, so it is the fallback
        metadata = sub.get("metadata") or {}
        workspace_id = metadata.get("workspace_id")
        return BillingEvent(
            id=delivery, type=kind, workspace_id=workspace_id,
            customer_external_id=customer.get("external_id"), product_id=sub.get("product_id"),
            customer_id=sub.get("customer_id") or customer.get("id"), subscription_id=sub["id"],
            plan=plan, period=period or sub.get("recurring_interval"), status=sub["status"],
            changed_at=when(sub.get("modified_at")) or when(sub.get("created_at")) or when(payload["timestamp"]),
            units=sub.get("units"), units_next=(sub.get("pending_update") or {}).get("units"),
            period_start=when(sub.get("current_period_start")), period_end=when(sub.get("current_period_end")),
            ends_at=when(sub.get("ends_at")) if sub.get("cancel_at_period_end") else None,
            past_due_at=when(sub.get("past_due_at")), payer_id=metadata.get("user_id"),
            payer_email=customer.get("email"))

    @staticmethod
    def _refund(delivery: str, kind: str, payload: dict) -> RefundNotice:
        order = payload["data"]
        customer = order.get("customer") or {}
        return RefundNotice(
            id=delivery, type=kind, order_id=order["id"], subscription_id=order.get("subscription_id"),
            customer_id=order.get("customer_id") or customer.get("id"),
            customer_external_id=customer.get("external_id"),
            workspace_id=(order.get("metadata") or {}).get("workspace_id"),
            amount=int(order.get("refunded_amount") or 0) + int(order.get("refunded_tax_amount") or 0),
            currency=order.get("currency") or "inr", full=order.get("status") == "refunded",
            at=when(order.get("modified_at")) or when(payload["timestamp"]))

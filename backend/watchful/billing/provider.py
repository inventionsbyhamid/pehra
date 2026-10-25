"""What the app needs from a payment provider (Polar now; another is one more module registered as kind `billing`):
a checkout link for a number of watched cameras, a way to change that number, a billing portal link, a refund for the
operator, and verified webhooks turned into `BillingEvent`s and `RefundNotice`s. Applying them is billing/state.py's
business."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from watchful.db import models as m

# subscription statuses, in Polar's (and Stripe's) words
PAYING = {"active", "trialing"}
GRACE = {"past_due"}  # a renewal failed and is being retried
ENDED = {"canceled", "unpaid", "incomplete_expired"}  # for good: such a subscription never comes back
# which of two snapshots of one subscription stamped with the same time is the later
STATUS_RANK = {"incomplete": 0, "trialing": 1, "active": 1, "past_due": 2, "paused": 2,
               "canceled": 3, "unpaid": 3, "incomplete_expired": 3}


class BadSignature(Exception):
    """A webhook whose signature does not check out with our secret."""


class BillingError(Exception):
    """The provider could not be reached, refused the request, or is not set up."""


@dataclass(frozen=True)
class BillingEvent:
    """One subscription's state as the provider sent it. Each event is a whole snapshot, so the latest wins."""
    id: str  # the delivery's id, the same on every retry
    type: str  # the provider's event name, e.g. subscription.active
    workspace_id: str | None  # the society the checkout was for (its metadata): this one wins
    customer_id: str | None  # the provider's
    subscription_id: str
    plan: str | None  # from the product; None for a product we do not sell
    period: str | None  # month or year
    status: str
    changed_at: datetime  # when the provider last changed the subscription
    units: int | None = None  # watched cameras paid for
    units_next: int | None = None  # lowered for the next renewal
    period_start: datetime | None = None
    period_end: datetime | None = None
    ends_at: datetime | None = None  # set while it is cancelled for the end of the period
    past_due_at: datetime | None = None
    customer_external_id: str | None = None  # our id on the provider's customer: the first society it paid for
    product_id: str | None = None  # the provider's


@dataclass(frozen=True)
class RefundNotice:
    """Money returned on one order. It is written down for its society; the plan follows the subscription alone, so
    a refund that should end the plan comes with the subscription revoked (python -m watchful.billing refund)."""
    id: str  # the delivery's id
    type: str
    order_id: str
    subscription_id: str | None
    customer_id: str | None
    customer_external_id: str | None
    workspace_id: str | None  # the order's metadata, when the checkout named one
    amount: int  # refunded so far on the order, in paise
    currency: str
    full: bool  # the whole order is refunded
    at: datetime


@dataclass(frozen=True)
class Refunded:
    orders: list[str]
    amount: int  # paise, tax included
    currency: str


class BillingProvider(Protocol):
    name: str
    test_mode: bool  # no real money moves
    payments: bool  # checkout and plan changes work here

    def checkout(self, workspace: m.Workspace, plan: str, period: str, cameras: int, success_url: str, *,
                 email: str | None = None, customer_ip: str | None = None) -> str:
        """The URL to send the owner to, to pay for the plan for this many watched cameras."""
        ...

    def change_cameras(self, workspace: m.Workspace, cameras: int) -> BillingEvent | None:
        """Changes the cameras on the society's running subscription: more at once (charged for the rest of the
        period), fewer from the next renewal. An event to apply now, or None when a webhook will bring it."""
        ...

    def portal_url(self, workspace: m.Workspace) -> str:
        """A fresh link to the provider's billing portal: payment method, invoices, monthly or yearly, cancelling."""
        ...

    def refund(self, workspace: m.Workspace, since: datetime) -> Refunded:
        """Refunds in full every payment for the society's subscription since `since` and ends it now."""
        ...

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> BillingEvent | RefundNotice | None:
        """The event in a webhook, None for one billing does not use; raises BadSignature."""
        ...

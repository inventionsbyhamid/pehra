"""A society's plan from its provider's subscription events, applied once each and in any order (from packsure
e97a509, with watched cameras).

Every event is a whole snapshot of one subscription stamped with the time the provider last changed it, so the
newest snapshot wins and an older one arriving late changes nothing. Beyond that: a subscription that has ended
never comes back (a replayed `active` after `revoked` is ignored), and the end of some other subscription does not
end the society's current one. Delivery ids are kept, so a retried webhook is dropped before any of this.

The society is the one the checkout named (its metadata), even when it no longer exists; without one, the one on
the provider's customer (its external id, the first society that customer paid for), else the only society holding
that customer id. When several hold it, or none is found, nothing is applied and an Audit row says so
(billing.ambiguous_customer, billing.unmatched, or billing.unknown_product for a product not in POLAR_PRODUCTS).

Paying (active, trialing) keeps the subscription's plan and cameras; past due keeps them for `limits.grace_days` from
the failed payment; anything else is the default (Free) plan. A plan cancelled for the end of its period stops at
that end even if the provider's last webhook never arrives (billing.limits reads it so). When a change shortens how
long footage is kept, what was saved before keeps its longer days (limits.hold_retention).

A refund is written down for its society (billing.refund); it changes the plan only through its subscription."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from watchful.billing.limits import entitlement, hold_retention, paid_entitlement, retention_days
from watchful.billing.plans import catalog
from watchful.billing.provider import ENDED, GRACE, PAYING, STATUS_RANK, BillingEvent, RefundNotice
from watchful.db import models as m

log = logging.getLogger(__name__)


class _Duplicate(Exception):
    """This delivery was handled before."""


class _Ambiguous(Exception):
    """Several societies share the event's customer (an Audit row says so)."""


@dataclass(frozen=True)
class Outcome:
    result: str  # applied, duplicate, unmatched, or why it was skipped
    workspace_id: str | None = None


def _decide(ws: m.Workspace, event: BillingEvent) -> str | None:
    """Why the event is not applied, or None to apply it."""
    same = ws.subscription_id == event.subscription_id
    if event.plan is None and event.status not in ENDED:
        return "unknown_product"
    if same and ws.subscription_status in ENDED and event.status not in ENDED:
        return "ended"
    if not same and event.status in ENDED and ws.subscription_id and ws.subscription_status not in ENDED:
        return "other_subscription"
    synced = ws.billing_synced_at
    if synced is not None:
        if event.changed_at < synced:
            return "stale"
        if event.changed_at == synced and same and \
                STATUS_RANK.get(event.status, 0) < STATUS_RANK.get(ws.subscription_status, 0):
            return "stale"
    return None


def _workspace(s, event, provider: str) -> m.Workspace | None:
    if event.workspace_id:  # the checkout's own; a society deleted since is no other society's payment
        return s.get(m.Workspace, event.workspace_id, with_for_update=True)
    if event.customer_external_id and \
            (ws := s.get(m.Workspace, event.customer_external_id, with_for_update=True)) is not None:
        return ws
    if isinstance(event, RefundNotice) and event.subscription_id:
        found = s.scalars(select(m.Workspace).where(m.Workspace.subscription_id == event.subscription_id)).first()
        if found is not None:
            return found
    if not event.customer_id:
        return None
    rows = s.scalars(select(m.Workspace).where(m.Workspace.polar_customer_id == event.customer_id)
                     .with_for_update()).all()
    if len(rows) > 1:
        s.add(m.Audit(workspace_id=None, actor=provider, action="billing.ambiguous_customer",
                      data={"event": event.type, "subscription": event.subscription_id, "customer": event.customer_id,
                            "workspaces": [ws.id for ws in rows]}))
        log.warning("billing event %s names customer %s, which %d societies share; not applied", event.id,
                    event.customer_id, len(rows))
        raise _Ambiguous
    return rows[0] if rows else None


def _seen(s, event, provider: str, now: datetime) -> m.BillingWebhook:
    hook = m.BillingWebhook(provider=provider, id=event.id, type=event.type, received_at=now)
    s.add(hook)
    try:
        s.flush()
    except IntegrityError as e:
        raise _Duplicate from e
    return hook


def apply_event(db, event: BillingEvent, provider: str, grace_days: int, now: datetime | None = None) -> Outcome:
    """Applies a verified event. Any other database error is raised, so the webhook answers 5xx and the provider
    retries."""
    now = now or m.utcnow()
    try:
        with db.tx() as s:
            hook = _seen(s, event, provider, now)
            try:
                ws = _workspace(s, event, provider)
            except _Ambiguous:
                return Outcome("unmatched")
            if ws is None:
                unknown = event.plan is None and event.status not in ENDED
                s.add(m.Audit(workspace_id=None, actor=provider,
                              action="billing.unknown_product" if unknown else "billing.unmatched",
                              data={"event": event.type, "subscription": event.subscription_id,
                                    "status": event.status, "product": event.product_id,
                                    "workspace": event.workspace_id, "customer": event.customer_id}))
                log.warning("billing event %s (%s) is for no single society we know", event.id, event.type)
                return Outcome("unmatched")
            hook.workspace_id = ws.id
            skipped = _decide(ws, event)
            if skipped == "unknown_product":
                s.add(m.Audit(workspace_id=ws.id, actor=provider, action="billing.unknown_product",
                              data={"event": event.type, "product": event.product_id,
                                    "subscription": event.subscription_id, "status": event.status}))
                log.warning("billing event %s for %s: product %s is not in POLAR_PRODUCTS", event.id, ws.id,
                            event.product_id)
            if skipped:
                log.info("billing event %s (%s, %s) for %s skipped: %s", event.id, event.type, event.status, ws.id,
                         skipped)
                return Outcome(skipped, ws.id)
            before, kept = entitlement(ws, now), retention_days(ws, now)
            same = ws.subscription_id == event.subscription_id
            ws.subscription_id = event.subscription_id
            ws.subscription_status = event.status
            ws.billing_synced_at = event.changed_at
            ws.subscription_period = event.period
            ws.subscription_period_start = event.period_start
            ws.subscription_period_end = event.period_end
            ws.subscription_ends_at = event.ends_at
            ws.polar_customer_id = event.customer_id or ws.polar_customer_id
            if same:  # the payer of this subscription, as far as the snapshots tell
                ws.billing_user_id = event.payer_id or ws.billing_user_id
                ws.billing_email = event.payer_email or ws.billing_email
            else:  # another subscription: its own payer only
                ws.billing_user_id, ws.billing_email = event.payer_id, event.payer_email
            if event.status in PAYING or event.status in GRACE:
                ws.plan = event.plan
                if event.units is not None:
                    ws.subscription_units, ws.subscription_units_next = event.units, event.units_next
                else:  # a paying snapshot without cameras: never read as fewer (billing.limits counts it as unknown)
                    if not same:
                        ws.subscription_units = ws.subscription_units_next = None
                    s.add(m.Audit(workspace_id=ws.id, actor=provider, action="billing.no_units",
                                  data={"event": event.type, "subscription": event.subscription_id,
                                        "product": event.product_id, "kept": ws.subscription_units}))
                    log.warning("billing event %s for %s has no camera count; kept %s", event.id, ws.id,
                                ws.subscription_units)
                if event.status in PAYING:
                    ws.grace_until = None
                elif event.past_due_at:
                    ws.grace_until = event.past_due_at + timedelta(days=grace_days)
                elif ws.grace_until is None:
                    ws.grace_until = event.changed_at + timedelta(days=grace_days)
            else:
                ws.plan, ws.grace_until = catalog().default, None
                ws.subscription_units = ws.subscription_units_next = None
            hold_retention(ws, kept, now)
            after = entitlement(ws, now)
            s.add(m.Audit(workspace_id=ws.id, actor=provider, action="billing.subscription",
                          data={"event": event.type, "status": event.status, "subscription": event.subscription_id,
                                "plan": after.plan.id, "cameras": after.cameras, "was": before.plan.id,
                                "was_cameras": before.cameras, "period": event.period}))
            return Outcome("applied", ws.id)
    except _Duplicate:
        return Outcome("duplicate")


def apply_refund(db, notice: RefundNotice, provider: str, now: datetime | None = None) -> Outcome:
    """Writes a refund down for its society, once per delivery."""
    now = now or m.utcnow()
    try:
        with db.tx() as s:
            hook = _seen(s, notice, provider, now)
            try:
                ws = _workspace(s, notice, provider)
            except _Ambiguous:
                return Outcome("unmatched")
            data = {"order": notice.order_id, "subscription": notice.subscription_id, "amount": notice.amount,
                    "currency": notice.currency, "full": notice.full}
            if ws is None:
                s.add(m.Audit(workspace_id=None, actor=provider, action="billing.unmatched",
                              data={**data, "event": notice.type, "customer": notice.customer_id}))
                return Outcome("unmatched")
            hook.workspace_id = ws.id
            s.add(m.Audit(workspace_id=ws.id, actor=provider, action="billing.refund", data=data))
            if notice.full and notice.subscription_id == ws.subscription_id and ws.subscription_status not in ENDED:
                log.warning("order %s of %s was refunded in full while its subscription %s runs; revoke it in Polar "
                            "if the plan should end", notice.order_id, ws.id, notice.subscription_id)
            return Outcome("applied", ws.id)
    except _Duplicate:
        return Outcome("duplicate")


@dataclass(frozen=True)
class Lapse:
    """A society whose paid plan has run out, as it stood when found."""
    workspace_id: str
    plan: str
    status: str
    synced_at: datetime | None
    hold_days: int
    hold_until: datetime


def lapsed_plans(db, now: datetime | None = None) -> list[Lapse]:
    """Societies still holding a paid plan whose grace or cancelled period has run out."""
    now = now or m.utcnow()
    cat = catalog()
    with db.tx() as s:
        rows = s.scalars(select(m.Workspace).where(m.Workspace.plan != cat.default,
                                                   m.Workspace.subscription_status.is_not(None))).all()
        found = []
        for ws in rows:
            if paid_entitlement(ws, now).source != "free":
                continue
            if ws.subscription_status in PAYING and ws.subscription_ends_at:
                ended = ws.subscription_ends_at
            elif ws.subscription_status in GRACE and ws.grace_until:
                ended = ws.grace_until
            else:
                ended = now
            days = cat.get(ws.plan).limits.retention_days
            until = max(ended + timedelta(days=days), ws.retention_hold_until or ended)
            found.append(Lapse(ws.id, ws.plan, ws.subscription_status, ws.billing_synced_at,
                               max(days, ws.retention_hold_days or 0), until))
        return found


def lapse_plan(db, found: Lapse) -> bool:
    """Moves one found society to the default plan, unless a webhook changed its subscription since it was found
    (then the row no longer matches and nothing is written). One conditional UPDATE, so no lock is held."""
    synced = m.Workspace.billing_synced_at
    with db.tx() as s:
        moved = s.execute(update(m.Workspace)
                          .where(m.Workspace.id == found.workspace_id, m.Workspace.plan == found.plan,
                                 m.Workspace.subscription_status == found.status,
                                 synced.is_(None) if found.synced_at is None else synced == found.synced_at)
                          .values(plan=catalog().default, grace_until=None, subscription_units=None,
                                  subscription_units_next=None, retention_hold_days=found.hold_days,
                                  retention_hold_until=found.hold_until)
                          .execution_options(synchronize_session=False)).rowcount
        if moved:
            s.add(m.Audit(workspace_id=found.workspace_id, actor="system", action="billing.lapsed",
                          data={"was": found.plan, "status": found.status}))
    return bool(moved)


def settle_plans(db, now: datetime | None = None) -> list[str]:
    """Moves societies whose grace or cancelled period has run out to the default plan; returns their ids."""
    return [found.workspace_id for found in lapsed_plans(db, now) if lapse_plan(db, found)]

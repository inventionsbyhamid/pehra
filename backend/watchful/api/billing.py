"""Billing routes (from packsure e97a509, per watched camera): the plan's summary for the billing panel, checkout for a
number of cameras, changing the cameras on a running subscription, the billing portal, and the provider's webhook.

The webhook is public; its signature is the check (401 when it does not match). Applying an event is one short
transaction done before answering, so the plan has changed by the time the owner is back from checkout. Polar wants
an answer within 2 seconds and retries anything that is not 2xx; it disables an endpoint after 10 failures in a row,
so a verified payload we cannot read is logged and acknowledged rather than refused.

PlanWatch switches off the newest cameras beyond what a lowered or lapsed plan watches, stops them and tells the
society (an incident per camera, alerts/incidents.py): after a webhook, and every Tuning limits.plan_check_s for plans
that end by the clock or by the operator's comp command.

The billing portal shows every subscription of one Polar customer (one email may pay for two societies), so only the
person who paid opens it."""
import ipaddress
import logging
import threading
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from watchful.alerts.incidents import open_or_touch_incident
from watchful.auth.deps import CurrentMember, require_role
from watchful.billing.comp import refund_window
from watchful.billing.limits import (ASSIST, CHECKS, ai_used, comped, daily_ai_cap, entitlement, is_payer,
                                     off_by_plan, over_allowance, paying, retention_days, subscription_live,
                                     switched_off_words, watched_cameras)
from watchful.brand import BRAND
from watchful.billing.plans import catalog
from watchful.billing.provider import ENDED, GRACE, PAYING, BadSignature, BillingError, RefundNotice
from watchful.billing.state import apply_event, apply_refund
from watchful.db import models as m
from watchful.ingest.push import CLOSE_OFF, push_parts
from watchful.ratelimit import client_ip

log = logging.getLogger(__name__)

PLAN_WATCH = "plan_cameras"  # the incidents' watch id for cameras the plan switched off
NOT_PAYER = f"Billing is managed by the person who paid. Email us at {BRAND.contact_email}."


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str
    period: Literal["month", "year"]
    cameras: int


class CamerasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cameras: int


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _cameras(n: int) -> str:
    return "1 camera" if n == 1 else f"{n} cameras"


def summary(ctx, ws_id: str, provider, user=None, now: datetime | None = None) -> dict:
    """What the billing panel shows `user`: the plan, cameras watched against the allowance, cameras the plan
    switched off, dates, AI use, and what may be done."""
    now = now or m.utcnow()
    with ctx.db.tx() as s:
        ws = s.get(m.Workspace, ws_id)
    ent = entitlement(ws, now)
    plan = ent.plan
    paid = ent.source == "paid"
    status = ws.subscription_status if ws.subscription_id and ws.subscription_status not in ENDED else None
    watched = watched_cameras(ctx.db, ws_id)
    repo = ctx.repo_for(ws_id)
    refundable, _ = refund_window(ws, now)
    live = subscription_live(ws, now)
    sold = plan if plan.per_camera and plan.sold else next((p for p in catalog().sold() if p.per_camera), plan)
    payer = user is not None and is_payer(ws, user)
    limits = ctx.tuning.limits
    return {
        "plan": {"id": plan.id, "name": plan.name, "per_camera": plan.per_camera},
        "source": ent.source,
        "status": status,
        "period": ws.subscription_period if paid else None,
        "cameras": {"watched": watched, "allowed": ent.cameras,
                    "paid": ws.subscription_units if paid else None,
                    "next": ws.subscription_units_next if paid else None,
                    "most": sold.limits.watched_cameras},
        "switched_off": [{"id": i, "name": n} for i, n in off_by_plan(ctx.db, ws_id)],
        "renews_at": iso(ws.subscription_period_end) if paid and not ws.subscription_ends_at else None,
        "ends_at": iso(ws.subscription_ends_at) if paid else None,
        "grace_until": iso(ws.grace_until) if paid and status in GRACE else None,
        "comp_until": iso(ws.comp_until) if comped(ws, now) and ent.source == "comp" else None,
        "refund_until": iso(ws.subscription_period_start + timedelta(days=catalog().refund_days_yearly))
        if refundable and paid else None,
        "retention_days": retention_days(ws, now),
        "ai_checks": {"today": ai_used(repo, CHECKS), "day": daily_ai_cap(repo, ctx.settings.ai_daily_call_limit)},
        "ai_help": {"today": ai_used(repo, ASSIST),
                    "day": daily_ai_cap(repo, ctx.settings.ai_daily_call_limit, ASSIST)},
        "staff": {"enrolled": len(repo.list_enrollments()), "allowed": plan.limits.enrolled_staff},
        "can_checkout": not live and provider.payments and not ws.demo,
        "can_change_cameras": paid and paying(ws, now) and not ws.subscription_ends_at and plan.per_camera and
        provider.payments and not ws.demo and payer,
        "can_manage": bool(ws.polar_customer_id or ws.subscription_id) and provider.payments and not ws.demo and payer,
        "payer": payer,
        "payments": provider.payments,
        "test_mode": provider.test_mode,
        "provider": provider.name,
        "poll": {"every_ms": int(limits.billing_poll_s * 1000), "tries": limits.billing_poll_tries},
    }


def public_ip(request: Request, trust_cloudflare: bool) -> str | None:
    """The buyer's address (ratelimit.client_ip); Polar sets the country and tax from it, so a private or local
    address (a proxy's, a test's) is not sent."""
    ip = client_ip(request, trust_cloudflare)
    try:
        return ip if ip and ipaddress.ip_address(ip).is_global else None
    except ValueError:
        return None


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


class TooLarge(Exception):
    pass


async def capped_body(request: Request, limit: int) -> bytes:
    """The body, refused (TooLarge) as soon as it is known to pass `limit` bytes: from Content-Length, else while
    reading, so an endless body is never held in memory."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise TooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise TooLarge
    return bytes(body)


class PlanWatch:
    """Keeps every society's watched cameras within its plan."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()

    def enforce(self, workspace_id: str) -> list[str]:
        done = over_allowance(self.ctx.db, workspace_id)
        if done.cameras:
            hub, _ = push_parts(self.ctx)
            repo = self.ctx.repo_for(workspace_id)
            for camera_id, name in done.cameras:
                self.ctx.manager.stop(camera_id)
                hub.close(camera_id, CLOSE_OFF)
                try:  # the society hears of it in the app and, with warnings on, on Telegram
                    open_or_touch_incident(repo, camera_id, PLAN_WATCH, "warning",
                                           switched_off_words(name, done.allowed), tuning=self.ctx.tuning.alerts)
                except Exception:
                    log.exception("telling society %s about camera %s failed", workspace_id, camera_id)
            log.info("society %s: switched off %s beyond its plan", workspace_id,
                     ", ".join(i for i, _ in done.cameras))
        return [i for i, _ in done.cameras]

    def run_once(self) -> None:
        for ws in self.ctx.workspace_ids():
            try:
                self.enforce(ws)
            except Exception:
                log.exception("keeping society %s within its plan failed", ws)

    def start(self) -> None:
        """Once now, before the cameras start, then every Tuning limits.plan_check_s."""
        self.run_once()
        threading.Thread(target=self._loop, daemon=True, name="plan-watch").start()

    def _loop(self) -> None:
        while not self._stop.wait(self.ctx.tuning.limits.plan_check_s):
            self.run_once()

    def stop(self) -> None:
        self._stop.set()


def mount(app: FastAPI, ctx, provider=None):
    if provider is None:
        from watchful.components import make_billing
        provider = make_billing(ctx.settings, ctx.tuning)
    app.state.billing = provider
    app.state.plans = PlanWatch(ctx)
    app.include_router(make_router(ctx, provider, app.state.plans))
    return provider


def billing_member(role: str):
    """require_role, refusing demo societies: a demo has its plan without paying."""
    check = require_role(role)

    def member_of(member: CurrentMember = Depends(check)) -> CurrentMember:
        if member.workspace.demo:
            raise HTTPException(403, "This is a demo society, so there is nothing to buy here. Sign in with Google "
                                     "to choose a plan for your own society.")
        return member

    return member_of


def make_router(ctx, provider, plans: PlanWatch) -> APIRouter:
    router = APIRouter()
    base = ctx.settings.public_base_url.rstrip("/")
    grace_days = ctx.tuning.limits.grace_days
    trust_cloudflare = ctx.settings.trust_cloudflare
    webhook_bytes = ctx.tuning.limits.webhook_kb * 1024
    page = f"{base}/app/settings/billing"

    def workspace(member: CurrentMember) -> m.Workspace:
        with ctx.db.tx() as s:
            return s.get(m.Workspace, member.workspace.id)

    def keep_within(workspace_id: str | None) -> None:
        if workspace_id:
            try:
                plans.enforce(workspace_id)
            except Exception:
                log.exception("keeping society %s within its plan failed", workspace_id)

    @router.get("/api/billing")
    def billing(member: CurrentMember = Depends(require_role("admin"))):
        return summary(ctx, member.workspace.id, provider, member.user)

    def payer_only(ws: m.Workspace, member: CurrentMember) -> None:
        if not is_payer(ws, member.user):
            raise HTTPException(403, NOT_PAYER)

    @router.post("/api/billing/checkout")
    def checkout(body: CheckoutIn, request: Request, response: Response,
                 member: CurrentMember = Depends(billing_member("owner"))):
        chosen = catalog().plans.get(body.plan)
        if chosen is None or not chosen.sold or body.period not in chosen.prices:
            raise HTTPException(422, "That plan cannot be bought here.")
        most = chosen.limits.watched_cameras
        if body.cameras < 1 or (most is not None and body.cameras > most):
            raise HTTPException(422, f"{chosen.name} watches 1 to {most} cameras. For more, email us.")
        watched = watched_cameras(ctx.db, member.workspace.id)
        if body.cameras < watched:
            raise HTTPException(422, f"You watch {_cameras(watched)} now. Choose at least {watched}, or switch some "
                                     "off first.")
        ws = workspace(member)
        no_store(response)
        if subscription_live(ws):
            payer_only(ws, member)
        try:
            if subscription_live(ws):
                return {"url": provider.portal_url(ws), "portal": True}
            url = provider.checkout(ws, chosen.id, body.period, body.cameras, f"{page}?checkout={{CHECKOUT_ID}}",
                                    payer_id=member.user.id, watched=watched, email=member.user.email,
                                    customer_ip=public_ip(request, trust_cloudflare))
        except BillingError as e:
            log.warning("checkout for %s failed: %s", ws.id, e)
            raise HTTPException(502, str(e) if not provider.payments else
                                "Checkout could not be opened. Try again in a minute.") from None
        ctx.db.audit(ws.id, member.user.id, "billing.checkout",
                     {"plan": chosen.id, "period": body.period, "cameras": body.cameras})
        return {"url": url, "portal": False}

    @router.post("/api/billing/cameras")
    def change_cameras(body: CamerasIn, member: CurrentMember = Depends(billing_member("owner"))):
        ws = workspace(member)
        payer_only(ws, member)  # the card charged is the payer's: no other owner changes what it pays
        ent = entitlement(ws)
        if ent.source != "paid" or not ent.plan.per_camera or not subscription_live(ws):
            raise HTTPException(409, "There is no paid plan to change. Choose a plan first.")
        if ws.subscription_status not in PAYING or ws.subscription_ends_at:
            raise HTTPException(409, "Your plan is ending or a payment is due. Use Manage billing first.")
        most = ent.plan.limits.watched_cameras
        if body.cameras < 1 or (most is not None and body.cameras > most):
            raise HTTPException(422, f"{ent.plan.name} watches 1 to {most} cameras. For more, email us.")
        watched = watched_cameras(ctx.db, ws.id)
        if body.cameras < watched:
            off = watched - body.cameras
            raise HTTPException(409, f"You watch {_cameras(watched)} now. Switch {off} off first, then lower the "
                                     "number.")
        was = ws.subscription_units or 0
        if body.cameras == was and ws.subscription_units_next is None:
            return {"ok": True, "cameras": was, "from": None}
        try:
            event = provider.change_cameras(ws, body.cameras)
        except BillingError as e:
            log.warning("changing cameras for %s failed: %s", ws.id, e)
            raise HTTPException(502, "That change could not be made. Try again in a minute, or use Manage "
                                     "billing.") from None
        if event is not None:
            apply_event(ctx.db, event, provider.name, grace_days)
        ctx.db.audit(ws.id, member.user.id, "billing.cameras", {"from": was, "to": body.cameras})
        later = body.cameras < was
        return {"ok": True, "cameras": body.cameras, "from": iso(ws.subscription_period_end) if later else None}

    @router.get("/api/billing/portal")
    def portal(response: Response, member: CurrentMember = Depends(billing_member("owner"))):
        ws = workspace(member)
        if not (ws.polar_customer_id or ws.subscription_id):
            raise HTTPException(409, "You are on the Free plan, so there is no billing to manage yet.")
        payer_only(ws, member)
        no_store(response)
        try:
            return {"url": provider.portal_url(ws)}
        except BillingError as e:
            log.warning("billing portal for %s failed: %s", ws.id, e)
            raise HTTPException(502, "Billing could not be opened. Try again in a minute.") from None

    @router.post("/api/billing/webhook")
    async def webhook(request: Request):
        try:
            body = await capped_body(request, webhook_bytes)
        except TooLarge:
            raise HTTPException(413, "too large") from None
        try:
            event = provider.parse_webhook(dict(request.headers), body)
        except BadSignature:
            raise HTTPException(401, "bad signature") from None
        except (ValueError, KeyError, TypeError, AttributeError):
            log.exception("a verified billing webhook could not be read; acknowledged and dropped")
            return {"ok": True}
        if isinstance(event, RefundNotice):
            outcome = await run_in_threadpool(apply_refund, ctx.db, event, provider.name)
        elif event is not None:
            outcome = await run_in_threadpool(apply_event, ctx.db, event, provider.name, grace_days)
            if outcome.result == "applied":
                await run_in_threadpool(keep_within, outcome.workspace_id)
        else:
            return {"ok": True}
        log.info("billing webhook %s (%s): %s", event.id, event.type, outcome.result)
        return {"ok": True}

    if hasattr(provider, "follow"):
        @router.get("/api/billing/fake/{token}")
        def fake_link(token: str):
            """The fake provider on localhost only: a test checkout or portal link, applied at once."""
            def subscription_of(workspace_id: str) -> str | None:
                with ctx.db.tx() as s:
                    return s.scalar(select(m.Workspace.subscription_id).where(m.Workspace.id == workspace_id))

            try:
                event, next_url = provider.follow(token, subscription_of)
            except BadSignature:
                raise HTTPException(404, "not_found") from None
            keep_within(apply_event(ctx.db, event, provider.name, grace_days).workspace_id)
            return RedirectResponse(next_url, status_code=303)

    return router

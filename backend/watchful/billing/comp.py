"""The operator's billing commands. A comp gives a society a plan and a number of watched cameras without payment
(pilots, the judges' demo), with an end date and an Audit row; webhooks never touch it and billing.limits reads it.

    python -m watchful.billing comp <workspace_id> <plan> --cameras N --until 2026-11-30   (or --days N)
    python -m watchful.billing comp <workspace_id> --end        stops the society's comp now
    python -m watchful.billing show <workspace_id>              what the plan allows now
    python -m watchful.billing refund <workspace_id> [--yes]    refunds a yearly plan within the refund window in
                                                                 full and ends it; without --yes it only says what
                                                                 it would do
    python -m watchful.billing unmatched [--days N]             payments and events that reached no society (a
                                                                 purchase outside our checkout carries none)"""
import argparse
import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from watchful.billing.limits import entitlement, hold_retention, retention_days, watched_cameras
from watchful.billing.plans import catalog
from watchful.billing.provider import ENDED, BillingError, BillingEvent
from watchful.db import models as m

ACTOR = "operator"


def _workspace(s, workspace_id: str) -> m.Workspace:
    ws = s.get(m.Workspace, workspace_id, with_for_update=True)
    if ws is None:
        raise LookupError(f"no society {workspace_id}")
    return ws


def give(db, workspace_id: str, plan: str, cameras: int | None, until: datetime,
         now: datetime | None = None) -> datetime:
    """Gives `plan` for `cameras` watched cameras until `until`, replacing any comp the society had."""
    cat = catalog()
    now = now or m.utcnow()
    if plan not in cat.plans or plan == cat.default:
        raise ValueError(f"{plan} is not a plan to give; choose one of "
                         f"{', '.join(p for p in cat.plans if p != cat.default)}")
    chosen = cat.get(plan)
    most = chosen.limits.watched_cameras
    if chosen.per_camera and cameras is None and most is not None:
        raise ValueError(f"{chosen.name} is per camera: give --cameras")
    if cameras is not None and (cameras < 1 or (most is not None and cameras > most)):
        raise ValueError(f"--cameras must be 1 to {most}" if most else "--cameras must be at least 1")
    if not until > now:
        raise ValueError("the end must be after now")
    with db.tx() as s:
        ws = _workspace(s, workspace_id)
        kept = retention_days(ws, now)
        was = {"plan": ws.comp_plan, "cameras": ws.comp_cameras,
               "until": ws.comp_until.isoformat() if ws.comp_until else None} if ws.comp_plan else None
        ws.comp_plan, ws.comp_cameras, ws.comp_until = plan, cameras, until
        hold_retention(ws, kept, now)
        s.add(m.Audit(workspace_id=ws.id, actor=ACTOR, action="billing.comp",
                      data={"plan": plan, "cameras": cameras, "until": until.isoformat(), "was": was}))
    return until


def end(db, workspace_id: str, now: datetime | None = None) -> str | None:
    """Stops the society's comp now; returns the plan it had, or None when it had none."""
    now = now or m.utcnow()
    with db.tx() as s:
        ws = _workspace(s, workspace_id)
        plan, cameras, until = ws.comp_plan, ws.comp_cameras, ws.comp_until
        if plan is None:
            return None
        kept = retention_days(ws, now)
        ws.comp_plan, ws.comp_cameras, ws.comp_until = None, None, None
        hold_retention(ws, kept, now)
        s.add(m.Audit(workspace_id=ws.id, actor=ACTOR, action="billing.comp_end",
                      data={"plan": plan, "cameras": cameras, "until": until.isoformat() if until else None,
                            "ended": now.isoformat()}))
    return plan


def describe(db, workspace_id: str, now: datetime | None = None) -> str:
    now = now or m.utcnow()
    with db.tx() as s:
        ws = _workspace(s, workspace_id)
        ent, kept = entitlement(ws, now), retention_days(ws, now)
        sub = (ws.subscription_id, ws.subscription_status, ws.subscription_period, ws.subscription_units)
        comp = (ws.comp_plan, ws.comp_cameras, ws.comp_until)
    allowed = "no limit" if ent.cameras is None else ent.cameras
    return (f"{workspace_id}: {ent.plan.name} ({ent.source}), watches {watched_cameras(db, workspace_id)} of "
            f"{allowed} cameras, footage kept {kept} days\n  subscription {sub}\n  comp {comp}")


def refund_window(ws: m.Workspace, now: datetime) -> tuple[bool, str]:
    """Whether the society's yearly plan may be refunded now (the refund policy), and why not."""
    days = catalog().refund_days_yearly
    if not ws.subscription_id or ws.subscription_status in ENDED:
        return False, "it has no running subscription"
    if ws.subscription_period != "year":
        return False, "monthly plans are not refunded"
    start = ws.subscription_period_start
    if start is None or now > start + timedelta(days=days):
        return False, f"the year was paid more than {days} days ago"
    return True, ""


UNMATCHED = ("billing.unmatched", "billing.ambiguous_customer", "billing.unknown_product", "billing.no_units")


def unmatched(db, days: float, now: datetime | None = None) -> list[str]:
    """Billing events of the last `days` that need the operator: for no society we know, for a customer several
    societies share, for a product not in POLAR_PRODUCTS, or without a camera count. One line each, newest first."""
    since = (now or m.utcnow()) - timedelta(days=days)
    with db.tx() as s:
        rows = s.scalars(select(m.Audit).where(m.Audit.action.in_(UNMATCHED), m.Audit.ts >= since)
                         .order_by(m.Audit.ts.desc())).all()
        return [f"{r.ts.isoformat()} {r.action} society={r.workspace_id or '-'} " +
                " ".join(f"{k}={v}" for k, v in sorted(r.data.items()) if v not in (None, "", []))
                for r in rows]


def refund(db, provider, workspace_id: str, grace_days: int, *, confirm: bool, now: datetime | None = None,
           margin_s: float = 0.0) -> str:
    """Refunds the society's yearly payment in full and ends its plan now, within the refund window. Payments from
    `margin_s` before the year started count too (the first order is made just before the subscription)."""
    from watchful.billing.state import apply_event
    now = now or m.utcnow()
    with db.tx() as s:
        ws = _workspace(s, workspace_id)
    ok, why = refund_window(ws, now)
    if not ok:
        raise ValueError(f"{workspace_id} cannot be refunded here: {why}. Refund it in Polar's dashboard if you must.")
    if not confirm:
        return (f"{workspace_id}: would refund every payment for subscription {ws.subscription_id} since "
                f"{ws.subscription_period_start.isoformat()} and end it now. Run again with --yes to do it.")
    done = provider.refund(ws, ws.subscription_period_start - timedelta(seconds=margin_s))
    ended = BillingEvent(id=f"refund_{ws.subscription_id}_{now.timestamp()}", type="subscription.revoked",
                         workspace_id=ws.id, customer_id=ws.polar_customer_id, subscription_id=ws.subscription_id,
                         plan=None, period=ws.subscription_period, status="canceled", changed_at=now)
    apply_event(db, ended, ACTOR, grace_days, now)
    db.audit(ws.id, ACTOR, "billing.refunded", {"orders": done.orders, "amount": done.amount,
                                               "currency": done.currency, "subscription": ws.subscription_id})
    return (f"{workspace_id}: refunded {len(done.orders)} payment(s), {done.amount / 100:.2f} {done.currency.upper()}"
            f", and ended subscription {ws.subscription_id}")


def _end_of(day: str) -> datetime:
    """The end of that day in India, where the societies are."""
    from zoneinfo import ZoneInfo
    return datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(), ZoneInfo("Asia/Kolkata")) \
        .astimezone(UTC)


def main(argv: list[str] | None = None, settings=None, provider=None) -> None:
    from watchful.components import make_billing, make_db
    from watchful.config import get_settings
    from watchful.tuning import load_tuning

    args = argparse.ArgumentParser(prog="python -m watchful.billing")
    sub = args.add_subparsers(dest="command", required=True)
    comp = sub.add_parser("comp", help="give a society a plan and cameras until a date, or stop that early")
    comp.add_argument("workspace", help="the society's workspace id (ws_...)")
    comp.add_argument("plan", nargs="?", help="the plan to give")
    comp.add_argument("--cameras", type=int, help="watched cameras it includes")
    comp.add_argument("--until", help="the last day it is given, YYYY-MM-DD (India time)")
    comp.add_argument("--days", type=float, help="or how many days from now")
    comp.add_argument("--end", action="store_true", help="stop the society's comp now")
    show = sub.add_parser("show", help="what a society's plan allows now")
    show.add_argument("workspace")
    back = sub.add_parser("refund", help="refund a yearly plan within the refund window and end it")
    back.add_argument("workspace")
    back.add_argument("--yes", action="store_true", help="do it; without this it only says what it would do")
    lost = sub.add_parser("unmatched", help="billing events that reached no society, for the operator")
    lost.add_argument("--days", type=float, help="how far back (default: as long as webhook ids are kept)")
    opts = args.parse_args(argv)
    if opts.command == "comp":
        giving = opts.plan or opts.cameras is not None or opts.until or opts.days is not None
        if opts.end and giving:
            args.error("--end stands alone")
        if not opts.end and (not opts.plan or (opts.until is None) == (opts.days is None)):
            args.error("give a plan and one of --until or --days, or --end alone")
    logging.basicConfig(level=logging.INFO)
    settings = settings or get_settings()
    db = make_db(settings)
    try:
        tuning = load_tuning(settings.tuning_file)
        if opts.command == "show":
            print(describe(db, opts.workspace))
        elif opts.command == "unmatched":
            lines = unmatched(db, opts.days if opts.days is not None else tuning.limits.webhook_ids_days)
            print("\n".join(lines) if lines else "nothing unmatched")
        elif opts.command == "refund":
            provider = provider or make_billing(settings, tuning)
            print(refund(db, provider, opts.workspace, tuning.limits.grace_days, confirm=opts.yes,
                         margin_s=tuning.limits.refund_orders_margin_s))
        elif opts.end:
            plan = end(db, opts.workspace)
            print(f"{opts.workspace}: comp of {plan} ended" if plan else f"{opts.workspace} had no comp")
        else:
            until = _end_of(opts.until) if opts.until else m.utcnow() + timedelta(days=opts.days)
            give(db, opts.workspace, opts.plan, opts.cameras, until)
            cams = f", {opts.cameras} cameras" if opts.cameras else ""
            print(f"{opts.workspace}: {opts.plan}{cams} until {until.isoformat()}")
    except (LookupError, ValueError, BillingError) as e:
        args.exit(2, f"{e}\n")

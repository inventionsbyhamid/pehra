"""Daily billing upkeep (from packsure e97a509): plan_sweep writes plans whose grace or cancelled period ran out and
forgets webhook delivery ids older than `limits.webhook_ids_days`. Safe to repeat. Cameras beyond a lowered plan are
switched off by the app (api/billing.py PlanWatch), which runs them; footage follows billing.limits.retention_days in
the retention janitor (workers.run_retention)."""
import logging
from datetime import timedelta

from sqlalchemy import delete

from watchful.billing.state import settle_plans
from watchful.db import models as m
from watchful.jobs import JobContext, handler

log = logging.getLogger(__name__)


@handler("plan_sweep", daily=True)
def plan_sweep(ctx: JobContext, payload: dict) -> None:
    moved = settle_plans(ctx.queue, ctx.now)  # the job table's database is the platform's
    if moved:
        log.info("plans moved to the default after grace or cancellation: %s", ", ".join(moved))
    old = ctx.now - timedelta(days=ctx.tuning.limits.webhook_ids_days)
    with ctx.queue.tx() as s:
        s.execute(delete(m.BillingWebhook).where(m.BillingWebhook.received_at < old))

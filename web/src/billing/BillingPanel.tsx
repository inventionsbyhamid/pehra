// The society's plan and its watched cameras, for the Settings billing tab. It stands alone: its own requests
// (./api.ts), its own styles, and the public site's plan data and currency view (backend/watchful/data/plans.json).
import { useCallback, useEffect, useState } from "react";
import brand, { mailto } from "../site/brand";
import { CurrencySwitch, DollarNote, Money } from "../site/currency";
import { cameraPlan, freePlan, largePlan, merchant, refundDays } from "../site/plans";
import { billingApi, BillingError, type BillingSummary, type Period } from "./api";
import "./billing.css";


function day(iso: string | null) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

const cameras = (n: number) => (n === 1 ? "1 camera" : `${n} cameras`);
const unitPrice = (period: Period) => (period === "year" ? cameraPlan.price_year_inr : cameraPlan.price_month_inr) ?? 0;

function standing(b: BillingSummary) {
  if (b.source === "comp" && b.comp_until) return `Included for you until ${day(b.comp_until)}.`;
  if (b.status === "past_due" && b.grace_until)
    return `Your last payment did not go through. The plan keeps working until ${day(b.grace_until)}. Update your payment in Manage billing.`;
  if (b.ends_at) return `Ends on ${day(b.ends_at)}, then moves to ${freePlan.name}.`;
  if (b.renews_at) return `Renews on ${day(b.renews_at)}, ${b.period === "year" ? "yearly" : "monthly"}.`;
  return null;
}

function Meter({ used, of }: { used: number; of: number | null }) {
  if (!of) return null;
  return (
    <span className="bp-meter" aria-hidden="true">
      <span style={{ width: `${Math.min(100, (100 * used) / of)}%` }} />
    </span>
  );
}

function Sign({ plus }: { plus?: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <path d={plus ? "M3 8h10M8 3v10" : "M3 8h10"} stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Stepper({ id, value, min, max, onChange }: {
  id: string; value: number; min: number; max: number; onChange: (n: number) => void;
}) {
  return (
    <div className="bp-stepper">
      <button type="button" onClick={() => onChange(Math.max(min, value - 1))} disabled={value <= min}
        aria-label="One camera fewer">
        <Sign />
      </button>
      <input id={id} type="number" inputMode="numeric" min={min} max={max} value={value}
        onChange={(e) => onChange(Math.min(max, Math.max(min, Math.round(Number(e.target.value) || min))))} />
      <button type="button" onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max}
        aria-label="One camera more">
        <Sign plus />
      </button>
    </div>
  );
}

function Total({ n, period }: { n: number; period: Period }) {
  return (
    <p className="bp-total">
      <Money inr={unitPrice(period) * n} after={` a ${period} for ${cameras(n)}`} />
      <span className="bp-unit">
        <Money inr={unitPrice(period)} after={` a camera a ${period}`} />
      </span>
    </p>
  );
}

export default function BillingPanel({ heading = 2 }: { heading?: 1 | 2 | 3 }) {
  const H = `h${heading}` as "h1" | "h2" | "h3";
  const Sub = `h${Math.min(heading + 1, 4)}` as "h2" | "h3" | "h4";
  const [b, setB] = useState<BillingSummary | null>(null);
  const [owner, setOwner] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notAdmin, setNotAdmin] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(() => new URLSearchParams(window.location.search).has("checkout"));
  const [changing, setChanging] = useState(false);
  const [want, setWant] = useState(1);
  const [buy, setBuy] = useState(2);
  const [period, setPeriod] = useState<Period>("month");

  const load = useCallback(async () => {
    try {
      const next = await billingApi.summary();
      setB(next);
      return next;
    } catch (e) {
      if (e instanceof BillingError && e.status === 403) setNotAdmin(true);
      else setError(e instanceof BillingError ? e.message : "Billing could not be loaded. Reload the page to try again.");
      return null;
    }
  }, []);

  useEffect(() => {
    void load().then((next) => {
      if (next) setBuy(Math.max(next.cameras.watched, freePlan.limits.watched_cameras ?? 2));
    });
    billingApi.role().then((role) => setOwner(role === "owner"), () => setOwner(false));
  }, [load]);

  // back from checkout: the plan changes once the payment is confirmed, usually within seconds
  const poll = b?.poll;
  useEffect(() => {
    if (!waiting || !poll) return;
    let tries = 0;
    const timer = window.setInterval(async () => {
      tries += 1;
      const next = await load();
      if ((next && next.source === "paid") || tries >= poll.tries) {
        window.clearInterval(timer);
        setWaiting(false);
        const url = new URL(window.location.href);
        url.searchParams.delete("checkout");
        window.history.replaceState(window.history.state, "", url);
      }
    }, poll.every_ms);
    return () => window.clearInterval(timer);
  }, [waiting, poll, load]);

  async function leave(get: () => Promise<{ url: string }>) {
    setBusy(true);
    setError(null);
    try {
      window.location.assign((await get()).url);
    } catch (e) {
      setError(e instanceof BillingError ? e.message : "That did not open. Try again in a minute.");
      setBusy(false);
    }
  }

  async function saveCameras() {
    if (!b) return;
    setBusy(true);
    setError(null);
    try {
      const done = await billingApi.changeCameras(want);
      const undo = want === b.cameras.paid;
      setChanging(false);
      setNote(
        undo
          ? `Your plan keeps ${cameras(want)}.`
          : done.from
            ? `From ${day(done.from)} your plan watches ${cameras(want)}.`
            : `Your plan now pays for ${cameras(want)}. It shows here once ${merchant} confirms, usually within seconds.`,
      );
      for (let i = 0; i < b.poll.tries; i += 1) {
        const next = await load();
        if (!next || (undo ? next.cameras.next === null : next.cameras.paid === want || next.cameras.next === want))
          break;
        await new Promise((r) => window.setTimeout(r, b.poll.every_ms));
      }
    } catch (e) {
      setError(e instanceof BillingError ? e.message : "That change could not be made. Try again in a minute.");
    } finally {
      setBusy(false);
    }
  }

  if (!b) {
    return (
      <section className="bp" aria-labelledby="bp-title">
        <H id="bp-title" className="bp-title">Billing</H>
        {notAdmin ? (
          <p className="bp-meta">Billing is looked after by the society's admins. Ask one of them about the plan.</p>
        ) : error ? (
          <p className="bp-error" role="alert">{error}</p>
        ) : (
          <p role="status">Loading</p>
        )}
      </section>
    );
  }

  const line = standing(b);
  const allowed = b.cameras.allowed;
  const minBuy = Math.max(1, b.cameras.watched);
  const most = b.cameras.most ?? Math.max(minBuy, b.cameras.paid ?? minBuy);
  const off = b.switched_off.map((c) => c.name);

  return (
    <section className="bp" aria-labelledby="bp-title">
      <H id="bp-title" className="bp-title">Billing</H>
      {b.test_mode && b.payments && <p className="bp-note">Test billing. No real payment is taken.</p>}
      {waiting && <p className="bp-note" role="status">Thanks. Your plan changes as soon as {merchant} confirms the payment.</p>}
      {note && <p className="bp-note" role="status">{note}</p>}
      {error && <p className="bp-error" role="alert">{error}</p>}

      <div className="bp-card">
        <p className="bp-label">Your plan</p>
        <p className="bp-plan">{b.plan.name}</p>
        {line && <p className={b.status === "past_due" ? "bp-error" : "bp-meta"}>{line}</p>}
        <dl className="bp-facts">
          <div>
            <dt>Cameras watched</dt>
            <dd>
              {allowed === null ? b.cameras.watched : `${b.cameras.watched} of ${allowed}`}
              <Meter used={b.cameras.watched} of={allowed} />
            </dd>
          </div>
          <div>
            <dt>Footage kept</dt>
            <dd>{b.retention_days} days</dd>
          </div>
          <div>
            <dt>AI checks today</dt>
            <dd>
              {b.ai_checks.today} of {b.ai_checks.day}
              <Meter used={b.ai_checks.today} of={b.ai_checks.day} />
            </dd>
          </div>
          <div>
            <dt>Writing watches and questions today</dt>
            <dd>
              {b.ai_help.today} of {b.ai_help.day}
              <Meter used={b.ai_help.today} of={b.ai_help.day} />
            </dd>
          </div>
          <div>
            <dt>Staff named</dt>
            <dd>{b.staff.allowed === null ? b.staff.enrolled : `${b.staff.enrolled} of ${b.staff.allowed}`}</dd>
          </div>
        </dl>
        {b.cameras.next !== null && b.cameras.next !== b.cameras.paid && (
          <p className="bp-meta">
            {b.renews_at ? `From ${day(b.renews_at)}` : "From your next renewal"} your plan watches{" "}
            {cameras(b.cameras.next)}.
          </p>
        )}
        {b.ai_checks.today >= b.ai_checks.day && (
          <p className="bp-meta">
            Today's AI checks are used up. Until midnight, critical moments still alert you, marked not checked by the
            AI.
          </p>
        )}
        {off.length > 0 && (
          <p className="bp-meta">
            Switched off by the plan: {off.join(", ")}. Add cameras to your plan, or switch another camera off, then
            switch {off.length === 1 ? "it" : "them"} on again.
          </p>
        )}
        {owner && b.source === "paid" && !b.payer && (
          <p className="bp-hint">Billing is managed by the person who paid. Ask them, or email us.</p>
        )}
        {allowed !== null && b.cameras.watched >= allowed && (
          <p className="bp-meta">
            Every camera your plan includes is watched. Cameras you add stay switched off until you switch one off or
            add more.
          </p>
        )}

        {owner && changing && (
          <div className="bp-change">
            <label className="bp-label" htmlFor="bp-want">Cameras on your plan</label>
            <Stepper id="bp-want" value={want} min={minBuy} max={most} onChange={setWant} />
            <Total n={want} period={b.period ?? "month"} />
            <p className="bp-hint">
              More cameras are charged now for the rest of this period. Fewer apply from your next renewal; switch off
              the cameras you no longer want watched first.
            </p>
            {b.cameras.next !== null && b.cameras.paid !== null && (
              <p className="bp-hint">Save {b.cameras.paid} to keep your cameras and cancel the lowering.</p>
            )}
            <div className="bp-actions">
              <button type="button" className="bp-btn" disabled={busy || (want === b.cameras.paid && b.cameras.next === null)} onClick={() => void saveCameras()}>
                Save
              </button>
              <button type="button" className="bp-btn bp-btn-line" disabled={busy} onClick={() => setChanging(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {owner && !changing && (b.can_change_cameras || b.can_manage) && (
          <div className="bp-actions">
            {b.can_change_cameras && (
              <button type="button" className="bp-btn" disabled={busy}
                onClick={() => { setWant(b.cameras.paid ?? minBuy); setChanging(true); setNote(null); }}>
                Change cameras
              </button>
            )}
            {b.can_manage && (
              <button type="button" className="bp-btn bp-btn-line" disabled={busy} onClick={() => void leave(billingApi.portal)}>
                Manage billing
              </button>
            )}
          </div>
        )}
        {owner && b.can_manage && !changing && (
          <p className="bp-hint">Monthly or yearly, your card or UPI, invoices and cancelling are in Manage billing.</p>
        )}
      </div>

      {b.can_checkout && (
        <div className="bp-card">
          <Sub className="bp-sub">{b.source === "free" ? `Move to ${cameraPlan.name}` : `Choose ${cameraPlan.name}`}</Sub>
          <p className="bp-meta">
            {cameraPlan.summary} Footage kept {cameraPlan.limits.retention_days} days, the gate log, the staff register
            and the daily summary.
          </p>
          <div className="bp-period" role="group" aria-label="Pay">
            {(["month", "year"] as Period[]).map((p) => (
              <button key={p} type="button" className="bp-toggle" aria-pressed={period === p} onClick={() => setPeriod(p)}>
                {p === "month" ? "Monthly" : "Yearly"}
              </button>
            ))}
          </div>
          <label className="bp-label" htmlFor="bp-buy">Cameras to watch</label>
          <Stepper id="bp-buy" value={buy} min={minBuy} max={most} onChange={setBuy} />
          <Total n={buy} period={period} />
          {owner ? (
            <button type="button" className="bp-btn" disabled={busy}
              onClick={() => void leave(() => billingApi.checkout(cameraPlan.id, period, buy))}>
              Choose {cameraPlan.name}
            </button>
          ) : (
            <p className="bp-hint">Only the society's owner can change the plan.</p>
          )}
          <p className="bp-hint">
            {largePlan.limits.min_cameras ?? most + 1} or more cameras? <a href={mailto(`${brand.name} for ${largePlan.name.toLowerCase()}`)}>Email us</a>{" "}
            about {largePlan.name}.
          </p>
        </div>
      )}
      {!b.payments && b.source === "free" && (
        <p className="bp-hint">
          Payments are not set up on this server yet. <a href={mailto(`${brand.name} plan`)}>Email us</a> to move to{" "}
          {cameraPlan.name}.
        </p>
      )}

      <div className="bp-foot">
        <div className="bp-currency">
          <CurrencySwitch />
          <DollarNote className="bp-hint" />
        </div>
        {b.refund_until && (
          <p className="bp-hint">You can ask for a full refund of this year until {day(b.refund_until)}.</p>
        )}
        <p className="bp-hint">
          Prices are in rupees and exclude GST. {merchant} is our merchant of record: it runs checkout, adds GST where
          it applies and sends your invoice. Yearly plans can be refunded within {refundDays} days of payment.{" "}
          <a href="/refunds">Refunds and cancellation</a>
        </p>
      </div>
    </section>
  );
}

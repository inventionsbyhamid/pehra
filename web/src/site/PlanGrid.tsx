import type { Plan } from "@data/plans";
import { Link } from "react-router-dom";
import { StartFree } from "./actions";
import { Tick } from "./art";
import brand, { mailto } from "./brand";
import { CurrencySwitch, DollarNote, Money } from "./currency";
import { cameraPlan, camerasText, featureNames, monthsFreeYearly, plans, retentionText } from "./plans";

const unit = (plan: Plan) => (plan.per_camera ? " a camera" : "");

function Price({ plan }: { plan: Plan }) {
  const amount = plan.price_month_inr;
  if (plan.contact || amount === null) return <p className="plan-price">Talk to us</p>;
  return (
    <p className="plan-price">
      <Money inr={amount} after={`${unit(plan)} a month`} />
    </p>
  );
}

function Year({ plan }: { plan: Plan }) {
  const amount = plan.price_year_inr;
  if (!amount) return <>{plan.contact ? "Priced for your site" : "No card needed"}</>;
  const free = monthsFreeYearly(plan);
  return (
    <Money inr={amount} before="or " after={`${unit(plan)} a year${free > 0 ? `, ${free} months free` : ""}`} />
  );
}

function Action({ plan }: { plan: Plan }) {
  if (plan.contact)
    return (
      <a className="cta cta-line" href={mailto(`${brand.name} for ${plan.name.toLowerCase()}`)}>
        Email us
      </a>
    );
  if (!plan.price_month_inr) return <StartFree className="cta" />;
  return <StartFree className="cta cta-line" to={`/login?plan=${plan.id}`} label={`Choose ${plan.name}`} />;
}

export default function PlanGrid({ level = 3 }: { level?: 2 | 3 }) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <>
      <div className="plans-currency">
        <CurrencySwitch />
        <DollarNote className="plans-note" />
      </div>
      <ul className="plans">
        {plans.map((plan) => (
          <li className={`plan${plan === cameraPlan ? " plan-main" : ""}`} key={plan.id}>
            <Heading className="plan-name">{plan.name}</Heading>
            <p className="plan-summary">{plan.summary}</p>
            <Price plan={plan} />
            <p className="plan-year">
              <Year plan={plan} />
            </p>
            <ul className="plan-limits">
              <li>{camerasText(plan)}</li>
              <li>{retentionText(plan)}</li>
            </ul>
            <ul className="plan-features" aria-label={`${plan.name} includes`}>
              {plan.features.map((id) => (
                <li key={id}>
                  <Tick />
                  <span>{featureNames[id] ?? id}</span>
                </li>
              ))}
            </ul>
            <Action plan={plan} />
          </li>
        ))}
      </ul>
    </>
  );
}

// a worked example in rupees (or about dollars) next to what one guard costs, with its source
export function PriceExample({ cameras = 8 }: { cameras?: number }) {
  const month = cameraPlan.price_month_inr! * cameras;
  return (
    <p className="price-example">
      For example, {cameras} watched cameras on {cameraPlan.name} cost <Money inr={month} after=" a month." /> One security guard
      from a Bengaluru agency costs ₹16,000 to ₹28,000 a month (
      <a href="https://m.indiamart.com/kavithaenterpriseskarnataka/security-services.html">agency listings</a>).
    </p>
  );
}

export function PlanLinks({ pricing = false }: { pricing?: boolean }) {
  const free = monthsFreeYearly(cameraPlan);
  return (
    <p className="plans-after">
      {free > 0 && <span>Pay yearly and get {free} months free.</span>}
      {pricing && (
        <Link to="/pricing" className="text-link">
          Billing, tax and cancelling
        </Link>
      )}
      <Link to="/refunds" className="text-link">
        Refunds and cancellation
      </Link>
    </p>
  );
}

import data, { type Plan } from "@data/plans";

export const plans = data.plans;
export const featureNames = data.features;
export const merchant = data.merchant_of_record;
export const usdInr = data.usd_inr;

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function rupees(amount: number) {
  return `₹${inr.format(amount)}`;
}

// whole dollars for every price
export function dollars(rupeeAmount: number) {
  return `$${usd.format(Math.round(rupeeAmount / usdInr.rate))}`;
}

export const rateDay = new Date(`${usdInr.date}T00:00:00Z`).toLocaleDateString("en-IN", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

export function monthsFreeYearly(plan: Plan) {
  if (!plan.price_month_inr || !plan.price_year_inr) return 0;
  return Math.round((plan.price_month_inr * 12 - plan.price_year_inr) / plan.price_month_inr);
}

export function camerasText(plan: Plan) {
  const { watched_cameras: most, min_cameras: least } = plan.limits;
  if (most === null) return `${least ?? "Many"} or more cameras`;
  if (plan.per_camera) return `1 to ${most} watched cameras`;
  return most === 1 ? "1 watched camera" : `${most} watched cameras`;
}

export function retentionText(plan: Plan) {
  const days = plan.limits.retention_days;
  return days === null ? "Footage kept as long as agreed" : `Footage kept ${days} days`;
}

export const freePlan = plans.find((p) => p.price_month_inr === 0)!;
export const cameraPlan = plans.find((p) => p.per_camera && !p.contact && p.price_month_inr)!;
export const largePlan = plans.find((p) => p.contact)!;

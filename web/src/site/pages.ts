import brand from "./brand";
import { cameraPlan, freePlan, refundDays, rupees } from "./plans";

export interface PageMeta {
  path: string;
  title: string;
  description: string;
  noindex?: boolean;
}

const name = brand.name;

export const PAGES: PageMeta[] = [
  {
    path: "/",
    title: `${name}: an AI control room for your society's cameras`,
    description: brand.description,
  },
  {
    path: "/pricing",
    title: `Pricing per watched camera | ${name}`,
    description: `Free for ${freePlan.limits.watched_cameras} watched cameras. ${cameraPlan.name}: ${rupees(cameraPlan.price_month_inr!)} a watched camera a month, or ${rupees(cameraPlan.price_year_inr!)} a year, with ${cameraPlan.limits.retention_days} days of footage. Large sites talk to us. Prices exclude GST.`,
  },
  {
    path: "/terms",
    title: `Terms of service | ${name}`,
    description: `The terms for using ${name}: what it does and does not promise, the society's role as data fiduciary and ours as processor, staff consent, plans and cancelling.`,
  },
  {
    path: "/privacy",
    title: `Privacy notice | ${name}`,
    description: `What ${name} stores from your cameras, where it is kept and for how long, how faces are handled, which AI service sees flagged moments, and whom to contact.`,
  },
  {
    path: "/refunds",
    title: `Refunds and cancellation | ${name}`,
    description: `Monthly plans can be cancelled any time. Yearly plans can be refunded within ${refundDays} days of payment. How watched cameras, cancelling and refunds work at ${name}.`,
  },
  {
    path: "/login",
    title: `Sign in | ${name}`,
    description: `Sign in to your society's control room on ${name}, or try the demo without signing in.`,
    noindex: true,
  },
];

export function pageMeta(path: string) {
  const clean = path.length > 1 ? path.replace(/\/+$/, "") : path;
  return PAGES.find((p) => p.path === clean);
}

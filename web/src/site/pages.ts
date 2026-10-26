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
    path: "/docs",
    title: `Documentation | ${name}`,
    description: `How ${name} works and how to use it: getting started, what OpenCV and the AI do, privacy and safety, self hosting with Docker, and the AWS deployment.`,
  },
  {
    path: "/docs/getting-started",
    title: `Getting started | ${name} docs`,
    description: `Sign in or try the demo, add your society's cameras, draw zones and lines, write a watch in plain words and connect your Telegram group.`,
  },
  {
    path: "/docs/how-it-works",
    title: `How it works | ${name} docs`,
    description: `How ${name} turns camera pictures into graded alerts: the OpenCV pipeline on every frame, watches compiled into signals, and the AI check with OpenCV tools.`,
  },
  {
    path: "/docs/privacy-and-safety",
    title: `Privacy and safety | ${name} docs`,
    description: `Children are never enrolled, identified or aged. Faces only for consenting adult staff at entries, blurred pictures on Telegram, and footage deleted on schedule.`,
  },
  {
    path: "/docs/self-hosting",
    title: `Self hosting | ${name} docs`,
    description: `Run ${name} on your own Linux machine with Docker Compose: what it needs, the settings to write, the first sign in, Telegram, backups and upgrades.`,
  },
  {
    path: "/docs/aws",
    title: `AWS deployment | ${name} docs`,
    description: `The AWS services ${name} uses in Mumbai, from EC2 Graviton with COOL to S3, Bedrock, SSM, CloudWatch, SNS and CloudFormation, and how to deploy the stack.`,
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

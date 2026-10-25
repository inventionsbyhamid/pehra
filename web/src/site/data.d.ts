// brand.json at the repo root and backend/watchful/data/plans.json, aliased in vite.config.ts
declare module "@data/brand" {
  export interface Brand {
    name: string;
    tagline: string;
    description: string;
    domain: string;
    contact_email: string;
    operator: string;
    country: string;
    theme_color: string;
  }
  const brand: Brand;
  export default brand;
}

declare module "@data/plans" {
  export interface PlanLimits {
    watched_cameras: number | null; // null means agreed per customer
    min_cameras: number | null;
    retention_days: number | null; // null means agreed per customer
    ai_checks_day: number | null; // AI checks of flagged moments a day (per watched camera on a per camera plan)
    ai_assist_day: number | null; // AI help a day: writing watches, questions and search (null: agreed)
    enrolled_staff: number | null; // null means agreed per customer
  }
  export interface Plan {
    id: string;
    name: string;
    summary: string;
    price_month_inr: number | null; // per watched camera when per_camera
    price_year_inr: number | null;
    per_camera: boolean;
    contact: boolean;
    limits: PlanLimits;
    features: string[];
  }
  export interface UsdInr {
    rate: number; // rupees for one US dollar
    date: string;
    source: string;
    source_url: string;
    checked: string;
  }
  export interface PlansFile {
    currency: string;
    prices_include_tax: boolean;
    merchant_of_record: string;
    usd_inr: UsdInr; // approximate dollar amounts for visitors outside India; charges stay in rupees
    default_plan: string;
    billing_unit: string;
    refund_days_yearly: number; // a yearly plan is refunded in full within this many days of paying
    features: Record<string, string>;
    plans: Plan[];
  }
  const plans: PlansFile;
  export default plans;
}

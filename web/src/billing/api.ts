// The billing routes (backend/watchful/api/billing.py), kept apart from the app's api.ts so the panel stands alone.

export type Period = "month" | "year";

export interface BillingSummary {
  plan: { id: string; name: string; per_camera: boolean };
  source: "free" | "paid" | "comp" | "agreed";
  status: string | null;
  period: Period | null;
  cameras: { watched: number; allowed: number | null; paid: number | null; next: number | null };
  renews_at: string | null;
  ends_at: string | null;
  grace_until: string | null;
  comp_until: string | null;
  refund_until: string | null;
  retention_days: number;
  ai_checks: { today: number; day: number };
  staff: { enrolled: number; allowed: number | null };
  can_checkout: boolean;
  can_change_cameras: boolean;
  can_manage: boolean;
  payments: boolean;
  test_mode: boolean;
  provider: string;
}

export class BillingError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "content-type": "application/json" };
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new BillingError(0, "Billing can't be reached. Check your connection and try again.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    // our own words for refusals and for a provider that could not be reached (502); anything else is generic
    const ours = typeof detail === "string" && (res.status < 500 || res.status === 502);
    throw new BillingError(res.status, ours ? (detail as string) : "That did not work. Try again in a minute.");
  }
  return data as T;
}

export const billingApi = {
  summary: () => call<BillingSummary>("GET", "/api/billing"),
  role: () => call<{ role: string | null }>("GET", "/api/me").then((me) => me.role),
  checkout: (plan: string, period: Period, cameras: number) =>
    call<{ url: string; portal: boolean }>("POST", "/api/billing/checkout", { plan, period, cameras }),
  changeCameras: (cameras: number) =>
    call<{ ok: boolean; cameras: number; from: string | null }>("POST", "/api/billing/cameras", { cameras }),
  portal: () => call<{ url: string }>("GET", "/api/billing/portal"),
};

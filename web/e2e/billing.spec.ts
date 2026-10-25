import { expect, test, type Page, type Route } from "@playwright/test";
import { preview, type PreviewServer } from "vite";

// The billing panel where the app mounts it (Settings, Billing), from the built dist/ with the API answered here, so
// each state of a society's plan can be shown without a payment provider. Run `npm run build` first.
const PORT = 4181;
const BASE = `http://127.0.0.1:${PORT}`;
const APP = new URL("../dist/app.html", import.meta.url).pathname;

const ME = {
  user: { id: "usr_1", email: "asha@example.com", name: "Asha" },
  workspace: { id: "ws_1", name: "Green Park", timezone: "Asia/Kolkata", plan: "society" },
  role: "owner",
  timezone: "Asia/Kolkata",
};

const SOCIETY = {
  plan: { id: "society", name: "Society", per_camera: true }, source: "paid", status: "active", period: "month",
  cameras: { watched: 5, allowed: 8, paid: 8, next: 5, most: 12 }, switched_off: [],
  renews_at: null, ends_at: null, grace_until: null, comp_until: null, refund_until: null, retention_days: 30,
  ai_checks: { today: 3, day: 320 }, ai_help: { today: 1, day: 200 }, staff: { enrolled: 2, allowed: 300 },
  can_checkout: false, can_change_cameras: true, can_manage: true, payer: true, payments: true, test_mode: false,
  provider: "polar", poll: { every_ms: 100, tries: 2 },
};

let server: PreviewServer;

test.beforeAll(async () => {
  server = await preview({ preview: { host: "127.0.0.1", port: PORT, strictPort: true }, logLevel: "silent" });
});

test.afterAll(async () => {
  await server?.close();
});

async function openBilling(page: Page, billing: (route: Route) => unknown, me = ME) {
  await page.route("**/api/**", (route) => route.fulfill({ json: route.request().method() === "GET" ? [] : {} }));
  await page.route("**/api/me", (route) => route.fulfill({ json: me }));
  await page.route("**/api/purposes", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/billing", billing);
  await page.route(`${BASE}/app/**`, (route) => route.fulfill({ path: APP, contentType: "text/html" }));
  await page.goto(`${BASE}/app/settings/billing`);
  await expect(page.locator(".bp-title")).toBeVisible();
}

test("a lowering waiting for the renewal reads plainly and can be undone", async ({ page }) => {
  const sent: unknown[] = [];
  await openBilling(page, (route) => route.fulfill({ json: SOCIETY }));
  await page.route("**/api/billing/cameras", async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({ json: { ok: true, cameras: 8, from: null } });
  });
  const panel = page.locator(".bp");
  await expect(panel).toContainText("From your next renewal your plan watches 5 cameras.");
  expect(await panel.innerText()).not.toMatch(/From\s+your plan/);
  await page.getByRole("button", { name: "Change cameras" }).click();
  const input = page.locator("#bp-want");
  await expect(input).toHaveValue("8");
  await expect(input).toHaveAttribute("max", "12"); // the most cameras come from the server's plan
  await page.getByRole("button", { name: "Save" }).click();
  await expect(panel).toContainText("Your plan keeps 8 cameras.");
  expect(sent).toEqual([{ cameras: 8 }]);
});

test("someone billing refuses sees plain words, not an error", async ({ page }) => {
  await openBilling(page, (route) => route.fulfill({ status: 403, json: { detail: "Only a society admin can do this." } }),
    { ...ME, role: "admin" });
  const panel = page.locator(".bp");
  await expect(panel).toContainText("Billing is looked after by the society's admins.");
  await expect(panel.locator("[role=alert]")).toHaveCount(0);
  await expect(panel).not.toContainText("Only a society admin");
});

test("cameras the plan switched off are listed, and the page fits a phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const off = { ...SOCIETY, cameras: { ...SOCIETY.cameras, next: null }, switched_off: [{ id: "cam_1", name: "Pool" }] };
  await openBilling(page, (route) => route.fulfill({ json: off }));
  const panel = page.locator(".bp");
  await expect(panel).toContainText("Switched off by the plan: Pool.");
  const text = await panel.innerText();
  expect(text).not.toMatch(/[—–]/);
  expect(text).not.toMatch(/[\p{L}][-‐][\p{L}]/u);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("the control room says which cameras the plan switched off", async ({ page }) => {
  const camera = (id: string, name: string, offReason: string | null) => ({
    id, name, kind: "stream", url: null, zones: [], enabled: false, tuning: {}, purpose: "gate", off_reason: offReason,
    health: null, status: { running: false, source: "stopped", fps: 0, loops: 0, stats: {}, problems: [] },
  });
  await page.route("**/api/**", (route) => route.fulfill({ json: route.request().method() === "GET" ? [] : {} }));
  await page.route("**/api/me", (route) => route.fulfill({ json: ME }));
  await page.route("**/api/purposes", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/cameras", (route) =>
    route.fulfill({ json: [camera("cam_1", "Pool", "plan"), camera("cam_2", "Gate", null)] }));
  await page.route(`${BASE}/app**`, (route) => route.fulfill({ path: APP, contentType: "text/html" }));
  await page.goto(`${BASE}/app`);
  await expect(page.locator("li", { hasText: "Pool" })).toContainText("Switched off by the plan");
  await expect(page.locator("li", { hasText: "Gate" })).toContainText("Paused");
});

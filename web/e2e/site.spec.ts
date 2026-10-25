import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { preview, type PreviewServer } from "vite";

// Serves the built dist/ the way a static host does, so these tests check the prerendered HTML.
const PORT = 4179;
const BASE = `http://127.0.0.1:${PORT}`;
const ROUTES = ["/", "/pricing", "/terms", "/privacy", "/refunds", "/login"];

const read = (path: string) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const BRAND = read("../../brand.json");
const PLANS = read("../../backend/watchful/data/plans.json");
const SITE = `https://${BRAND.domain}`;
const RATE: number = PLANS.usd_inr.rate;
const plan = (id: string) => PLANS.plans.find((p: { id: string }) => p.id === id);
const SOCIETY = plan("society");
const rupees = (n: number) => `₹${n.toLocaleString("en-IN")}`;
const dollars = (n: number) => `$${Math.round(n / RATE).toLocaleString("en-US")}`;
const NOTE = "Charged in rupees; dollar amounts are approximate";

let server: PreviewServer;

test.beforeAll(async () => {
  server = await preview({ preview: { host: "127.0.0.1", port: PORT, strictPort: true }, logLevel: "silent" });
});

test.afterAll(async () => {
  await server?.close();
});

async function seriousProblems(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  return serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

async function allText(page: Page) {
  const body = (await page.locator("body").textContent()) ?? "";
  const metas = await page
    .locator('meta[name="description"], meta[property^="og:"], meta[name^="twitter:"]')
    .evaluateAll((all) => all.map((m) => m.getAttribute("content") ?? ""));
  return [await page.title(), ...metas.filter((m) => !m.startsWith("http")), body].join("\n");
}

function expectNoDashes(text: string, route: string) {
  expect(text, `${route} has an em or en dash`).not.toMatch(/[—–]/);
  expect(text, `${route} has a spaced hyphen`).not.toMatch(/ - /);
  expect(text, `${route} has a hyphenated word`).not.toMatch(/[\p{L}\p{N}]-[\p{L}\p{N}]/u);
}

function pngSize(buf: Buffer) {
  expect(buf.subarray(1, 4).toString("latin1")).toBe("PNG");
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

test.describe("without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  for (const route of ROUTES) {
    test(`${route} is prerendered with its own title and tags`, async ({ page }) => {
      const res = await page.goto(BASE + route);
      expect(res?.status()).toBe(200);
      await expect(page.locator("#root")).toHaveAttribute("data-prerendered", route);
      await expect(page.locator("h1")).toHaveCount(1);
      await expect(page.locator("h1")).toBeVisible();
      await expect(page).toHaveTitle(new RegExp(BRAND.name));
      const head = (sel: string) => page.locator(`head ${sel}`);
      await expect(head('meta[name="description"]')).toHaveCount(1);
      await expect(head('meta[name="description"]')).toHaveAttribute("content", /.{60,}/);
      await expect(head('link[rel="canonical"]')).toHaveAttribute("href", SITE + route);
      await expect(head('meta[name="theme-color"]')).toHaveAttribute("content", BRAND.theme_color);
      await expect(head('link[rel="icon"]')).toHaveAttribute("href", "/favicon.svg");
      await expect(head('meta[property="og:site_name"]')).toHaveAttribute("content", BRAND.name);
      await expect(head('meta[property="og:title"]')).toHaveAttribute("content", await page.title());
      await expect(head('meta[property="og:url"]')).toHaveAttribute("content", SITE + route);
      await expect(head('meta[property="og:image"]')).toHaveAttribute("content", `${SITE}/og.png`);
      await expect(head('meta[property="og:image:width"]')).toHaveAttribute("content", "1200");
      await expect(head('meta[property="og:image:height"]')).toHaveAttribute("content", "630");
      await expect(head('meta[property="og:image:alt"]')).toHaveAttribute("content", new RegExp(BRAND.tagline));
      await expect(head('meta[name="twitter:card"]')).toHaveAttribute("content", "summary_large_image");
      await expect(head('meta[name="twitter:title"]')).toHaveAttribute("content", await page.title());
      await expect(head('meta[name="twitter:image"]')).toHaveAttribute("content", `${SITE}/og.png`);
      await expect(page.locator("footer")).toContainText(BRAND.operator);
      await expect(page.locator(`footer a[href="mailto:${BRAND.contact_email}"]`)).toBeVisible();
      expectNoDashes(await allText(page), route);
    });
  }

  test("each page has a different title and description", async ({ page }) => {
    const titles = new Set<string>();
    const descriptions = new Set<string>();
    for (const route of ROUTES) {
      await page.goto(BASE + route);
      titles.add(await page.title());
      descriptions.add((await page.locator('meta[name="description"]').getAttribute("content")) ?? "");
    }
    expect(titles.size).toBe(ROUTES.length);
    expect(descriptions.size).toBe(ROUTES.length);
  });

  test("the sign in page is kept out of search, the public pages are not", async ({ page }) => {
    await page.goto(BASE + "/login");
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
    await page.goto(BASE + "/");
    await expect(page.locator('meta[name="robots"]')).toHaveCount(0);
  });

  test("the logo, favicon, robots and Open Graph picture are served", async ({ request }) => {
    for (const path of ["/logo.svg", "/favicon.svg"]) {
      const res = await request.get(BASE + path);
      expect(res.status(), path).toBe(200);
      const svg = await res.text();
      expect(svg, path).toContain("<svg");
      expect(svg, path).not.toMatch(/Gradient|filter/i);
    }
    expect(await (await request.get(BASE + "/logo.svg")).text()).toContain(BRAND.theme_color);
    const og = await request.get(BASE + "/og.png");
    expect(og.status()).toBe(200);
    expect(pngSize(await og.body())).toEqual([1200, 630]);
    const robots = await (await request.get(BASE + "/robots.txt")).text();
    expect(robots).toContain(`Sitemap: ${SITE}/sitemap.xml`);
    const sitemap = await (await request.get(BASE + "/sitemap.xml")).text();
    for (const route of ["/", "/pricing", "/privacy", "/terms", "/refunds"]) expect(sitemap).toContain(`<loc>${SITE}${route}</loc>`);
    expect(sitemap).not.toContain("/login");
  });

  test("pricing shows rupee prices from the plans file", async ({ page }) => {
    await page.goto(BASE + "/pricing");
    const main = page.locator("main");
    await expect(main).toContainText(`${rupees(SOCIETY.price_month_inr)} a camera a month`);
    await expect(main).toContainText(`${rupees(SOCIETY.price_year_inr)} a camera a year`);
    await expect(main).toContainText("₹0");
    await expect(main).toContainText(`${plan("free").limits.watched_cameras} watched cameras`);
    await expect(main).toContainText(`Footage kept ${plan("free").limits.retention_days} days`);
    await expect(main).toContainText(`Footage kept ${SOCIETY.limits.retention_days} days`);
    await expect(main).toContainText(/talk to us/i);
    await expect(main).toContainText(/GST/);
    await expect(main).toContainText(PLANS.merchant_of_record);
    // dollars only with JavaScript, and no switch that cannot work
    expect(await main.innerText()).not.toContain("$");
    await expect(page.getByRole("group", { name: "Show prices in" })).toBeHidden();
    await expect(main.getByRole("link", { name: "Refunds and cancellation" }).first()).toHaveAttribute("href", "/refunds");
  });

  test("the landing tells the society story with sources, the product, privacy and pricing", async ({ page }) => {
    await page.goto(BASE + "/");
    await expect(page.locator("h1")).toHaveText(BRAND.tagline);
    const main = page.locator("main");
    // the control room, a Telegram alert and the gate log, drawn rather than screenshots
    await expect(page.locator("#control-room")).toContainText(/pool/i);
    await expect(page.locator("#control-room")).toContainText(/offline/i);
    await expect(page.locator("#telegram-alert")).toContainText(/acknowledge/i);
    await expect(page.locator("#telegram-alert")).toContainText(/false alarm/i);
    await expect(page.locator("#gate-log")).toContainText(/yellow/i);
    await expect(page.locator("#watch-card")).toContainText(/cannot watch/i);
    // every pain carries a link to where it comes from
    const pains = page.locator("#pains li");
    expect(await pains.count()).toBeGreaterThanOrEqual(4);
    for (const pain of await pains.all()) await expect(pain.locator('a[href^="https://"]').first()).toBeVisible();
    await expect(main).toContainText(/150 of 350/);
    for (const what of [/plain words/i, /telegram/i, /gate log/i, /staff register/i, /camera health/i, /control room/i])
      await expect(page.locator("#features")).toContainText(what);
    await expect(page.locator("#privacy")).toContainText(/never children/i);
    await expect(page.locator("#privacy")).toContainText(/blurred/i);
    await expect(page.locator("#pricing")).toContainText(rupees(SOCIETY.price_month_inr));
    await expect(main).toContainText(/not a guarantee/i);
    await expect(main.getByRole("link", { name: "Try the demo" }).first()).toHaveAttribute("href", "/demo");
  });

  test("legal pages say what they must", async ({ page }) => {
    await page.goto(BASE + "/privacy");
    const main = page.locator("main");
    for (const words of [
      /data fiduciary/i,
      /data processor/i,
      /adult staff/i,
      /never .*children/i,
      /blurred/i,
      /behind sign in/i,
      /notice at each camera zone/i,
      /grievance/i,
      /OpenRouter/,
      /Bedrock/,
      /Mumbai/,
      /test server/i,
    ])
      await expect(main).toContainText(words);
    await expect(main).toContainText(`Free: footage kept ${plan("free").limits.retention_days} days`);
    await expect(main).toContainText(`Society: footage kept ${SOCIETY.limits.retention_days} days`);
    await expect(main).toContainText(BRAND.operator);
    await expect(main.locator(`a[href^="mailto:${BRAND.contact_email}"]`).first()).toBeVisible();
    // the test server build never claims what only AWS does
    await expect(main).not.toContainText("encrypted in storage");

    await page.goto(BASE + "/terms");
    for (const words of [/data fiduciary/i, /data processor/i, /consent/i, /children/i, /not a guarantee/i, /grievance/i])
      await expect(main).toContainText(words);
    await expect(main).toContainText(`To close your account, email ${BRAND.contact_email}`);

    await page.goto(BASE + "/refunds");
    await expect(main).toContainText(/7 days/);
    await expect(main).toContainText(/watched cameras/i);
    await expect(main).toContainText(/rupees/);
  });

  test("questions open without JavaScript", async ({ page }) => {
    await page.goto(BASE + "/");
    const first = page.locator("#questions details").first();
    await first.locator("summary").click();
    await expect(first).toHaveAttribute("open", "");
  });
});

test.describe("with JavaScript", () => {
  // this static server has no API: answer who is looking and how sign in works
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/demo/visitor", (route) => route.fulfill({ json: { kind: "signed_out" } }));
    await page.route("**/api/auth/providers", (route) =>
      route.fulfill({ json: { providers: [{ name: "google", label: "Google" }] } }),
    );
  });

  for (const [why, answer] of [
    ["no way to sign in on the server", (route: import("@playwright/test").Route) => route.fulfill({ json: { providers: [] } })],
    ["a server that cannot say how to sign in", (route: import("@playwright/test").Route) => route.fulfill({ status: 404, body: "" })],
  ] as const) {
    test(`with ${why}, every sign up button offers the demo, never a dead end`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route("**/api/auth/providers", answer);
      await page.goto(BASE + "/");
      const demo = page.locator(".hero-actions").getByRole("link", { name: "Try the demo" });
      await expect(demo).toBeVisible();
      await expect(demo).toHaveAttribute("href", "/demo");
      await expect(page.getByRole("link", { name: /^(Start free|Sign in|Choose )/ })).toHaveCount(0);
      await page.goto(BASE + "/pricing");
      await expect(page.locator(".plans").getByRole("link", { name: "Try the demo" }).first()).toBeVisible();
      await expect(page.getByRole("link", { name: /^(Start free|Sign in|Choose )/ })).toHaveCount(0);
      await page.goto(BASE + "/login");
      const main = page.locator("main");
      await expect(main.getByRole("link", { name: "Try the demo" })).toBeVisible();
      await expect(main.getByRole("link", { name: /Continue with/ })).toHaveCount(0);
      expect(errors).toEqual([]);
    });
  }

  test("with a sign in provider, the sign up buttons lead to sign in", async ({ page }) => {
    await page.goto(BASE + "/");
    await expect(page.locator(".hero-actions").getByRole("link", { name: "Start free" })).toHaveAttribute("href", "/login");
    await expect(page.locator(".hero-actions").getByRole("link", { name: "Try the demo" })).toBeVisible();
    await page.goto(BASE + "/pricing");
    await expect(page.locator(".plans").getByRole("link", { name: "Start free" })).toBeVisible();
    await expect(page.locator(".plans").getByRole("link", { name: `Choose ${SOCIETY.name}` })).toHaveAttribute(
      "href",
      `/login?plan=${SOCIETY.id}`,
    );
    await page.goto(BASE + "/login");
    await expect(page.getByRole("link", { name: "Continue with Google" })).toHaveAttribute("href", "/auth/google/start");
    await expect(page.getByText("New here? Signing in creates your society on the Free plan.")).toBeVisible();
    await page.goto(BASE + "/login?error=setup");
    await expect(page.locator("main").getByRole("alert")).toContainText("not set up on this server yet");
    await expect(page.locator("main").getByRole("link", { name: "Try the demo" })).toBeVisible();
  });

  test("a signed in member is offered the control room", async ({ page }) => {
    await page.route("**/api/demo/visitor", (route) => route.fulfill({ json: { kind: "member" } }));
    await page.goto(BASE + "/");
    await expect(page.locator("header").getByRole("link", { name: "Open the control room" })).toHaveAttribute("href", "/app");
    await expect(page.locator("header").getByRole("link", { name: "Sign in" })).toHaveCount(0);
  });

  test("the app takes over and site links navigate", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(BASE + "/");
    await page.locator("header").getByRole("link", { name: "Pricing" }).click();
    await expect(page).toHaveURL(/\/pricing$/);
    await expect(page.locator("h1")).toContainText(/pric/i);
    await expect(page).toHaveTitle(/Pricing/);
    await page.locator("footer").getByRole("link", { name: "Privacy" }).click();
    await expect(page.locator("h1")).toContainText(/privacy/i);
    await expect(page).toHaveTitle(/Privacy/);
    await page.locator("header").getByRole("link", { name: `${BRAND.name} home` }).click();
    await expect(page.locator("h1")).toHaveText(BRAND.tagline);
    expect(errors).toEqual([]);
  });

  test("prerendered pages hydrate without errors, with or without a trailing slash", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    for (const route of [...ROUTES, "/pricing/", "/terms/"]) {
      await page.goto(BASE + route);
      await page.locator("a").first().waitFor();
    }
    await page.goto(BASE + "/pricing/");
    await expect(page.locator("header").getByRole("link", { name: "Pricing" })).toHaveAttribute("aria-current", "page");
    expect(errors).toEqual([]);
  });

  test("a route that is not a site page renders the app, not the landing page", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 401, json: { detail: "sign in" } }));
    await page.goto(BASE + "/app");
    await expect(page.locator("#control-room")).toHaveCount(0);
    await expect(page.locator("h1").first()).not.toHaveText(BRAND.tagline);
  });

  for (const route of ROUTES) {
    test(`${route} has no serious accessibility problems`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto(BASE + route);
      await page.locator("details").evaluateAll((all) => all.forEach((d) => ((d as HTMLDetailsElement).open = true)));
      expect(await seriousProblems(page)).toEqual([]);
    });
  }

  test.describe("in India", () => {
    test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

    test("prices show in rupees", async ({ page }) => {
      await page.goto(BASE + "/pricing");
      await expect(page.locator("html")).toHaveAttribute("data-currency", "inr");
      await expect(page.getByRole("button", { name: "rupees" })).toHaveAttribute("aria-pressed", "true");
      const text = await page.locator("main").innerText();
      expect(text).toContain(`${rupees(SOCIETY.price_month_inr)} a camera a month`);
      expect(text).not.toMatch(/\$\d/);
      expect(text).not.toContain(NOTE);
    });
  });

  test.describe("with an Indian locale outside India's time zone", () => {
    test.use({ timezoneId: "Europe/London", locale: "en-IN" });

    test("prices show in rupees", async ({ page }) => {
      await page.goto(BASE + "/");
      await expect(page.locator("html")).toHaveAttribute("data-currency", "inr");
      expect(await page.locator("#pricing").innerText()).toContain(rupees(SOCIETY.price_month_inr));
    });
  });

  test.describe("abroad", () => {
    test.use({ timezoneId: "America/New_York", locale: "en-US" });

    test("prices show as about dollars, charged in rupees, next to the refund policy", async ({ page }) => {
      for (const [route, where] of [
        ["/pricing", "main"],
        ["/", "#pricing"],
      ]) {
        await page.goto(BASE + route);
        await expect(page.locator("html")).toHaveAttribute("data-currency", "usd");
        const text = await page.locator(where).innerText();
        expect(text, route).toContain(`about ${dollars(SOCIETY.price_month_inr)} a camera a month`);
        expect(text, route).toContain(`or about ${dollars(SOCIETY.price_year_inr)} a camera a year`);
        expect(text, route).toContain("$0 a month");
        expect(text, route).toContain(NOTE);
        expect(text, route).not.toContain(rupees(SOCIETY.price_month_inr));
        await expect(page.locator(where).getByRole("link", { name: "Refunds and cancellation" }).first()).toBeVisible();
      }
    });

    test("the ₹ / $ switch works by keyboard, is remembered and has no serious accessibility problems", async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto(BASE + "/pricing");
      const inr = page.getByRole("button", { name: "rupees" });
      const usd = page.getByRole("button", { name: "US dollars" });
      await expect(usd).toHaveAttribute("aria-pressed", "true");
      expect(await seriousProblems(page)).toEqual([]);

      await inr.focus();
      await page.keyboard.press("Enter");
      await expect(inr).toHaveAttribute("aria-pressed", "true");
      await expect(usd).toHaveAttribute("aria-pressed", "false");
      let text = await page.locator("main").innerText();
      expect(text).toContain(`${rupees(SOCIETY.price_month_inr)} a camera a month`);
      expect(text).not.toContain(NOTE);
      expect(await seriousProblems(page)).toEqual([]);

      await page.reload(); // the choice beats the time zone
      await expect(page.locator("html")).toHaveAttribute("data-currency", "inr");
      await expect(inr).toHaveAttribute("aria-pressed", "true");
      await inr.focus();
      await page.keyboard.press("Tab");
      await expect(usd).toBeFocused();
      await page.keyboard.press("Space");
      await expect(usd).toHaveAttribute("aria-pressed", "true");
      text = await page.locator("main").innerText();
      expect(text).toContain(`about ${dollars(SOCIETY.price_month_inr)} a camera a month`);
      expect(text).toContain(NOTE);
    });
  });

  test("phone width has no sideways scroll", async ({ page }) => {
    for (const width of [390, 360]) {
      await page.setViewportSize({ width, height: 844 });
      for (const route of ROUTES) {
        await page.goto(BASE + route);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${route} at ${width}px`).toBeLessThanOrEqual(0);
      }
    }
  });
});

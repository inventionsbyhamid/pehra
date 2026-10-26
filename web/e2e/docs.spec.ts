import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { preview, type PreviewServer } from "vite";

// The documentation pages under /docs, served from the built dist/ the way a static host does.
const PORT = 4181;
const BASE = `http://127.0.0.1:${PORT}`;
const DOCS = ["/docs", "/docs/getting-started", "/docs/how-it-works", "/docs/privacy-and-safety", "/docs/self-hosting", "/docs/aws"];
const BRAND = JSON.parse(readFileSync(new URL("../../brand.json", import.meta.url), "utf8"));
const SITE = `https://${BRAND.domain}`;
const SHOTS = process.env.DOCS_SHOTS ?? "test-results/docs-shots";

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

// the words people read: commands, file names and identifiers in code are left out
async function prose(page: Page) {
  const body = await page.locator("body").evaluate((b) => {
    const copy = b.cloneNode(true) as HTMLElement;
    copy.querySelectorAll("pre, code").forEach((n) => n.remove());
    return copy.textContent ?? "";
  });
  const metas = await page
    .locator('meta[name="description"], meta[property="og:title"], meta[property="og:description"]')
    .evaluateAll((all) => all.map((m) => m.getAttribute("content") ?? ""));
  return [await page.title(), ...metas, body].join("\n");
}

test.describe("without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  for (const route of DOCS) {
    test(`${route} is prerendered with its own title, tags and plain words`, async ({ page }) => {
      const res = await page.goto(BASE + route);
      expect(res?.status()).toBe(200);
      await expect(page.locator("#root")).toHaveAttribute("data-prerendered", route);
      await expect(page.locator("h1")).toHaveCount(1);
      await expect(page).toHaveTitle(new RegExp(BRAND.name));
      await expect(page.locator('head meta[name="description"]')).toHaveAttribute("content", /.{80,}/);
      await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute("href", SITE + route);
      await expect(page.locator('head meta[property="og:url"]')).toHaveAttribute("content", SITE + route);
      await expect(page.locator("nav[aria-label=Documentation] a")).toHaveCount(DOCS.length);
      await expect(page.locator(`nav[aria-label=Documentation] a[href="${route}"]`)).toHaveAttribute("aria-current", "page");
      const text = await prose(page);
      expect(text, `${route} has an em or en dash`).not.toMatch(/[—–]/);
      expect(text, `${route} has a spaced hyphen`).not.toMatch(/ - /);
      expect(text, `${route} has a hyphenated word`).not.toMatch(/[\p{L}\p{N}]-[\p{L}\p{N}]/u);
      expect(text).not.toMatch(/trycloudflare/);
    });
  }

  test("each docs page has a different title and description, and all are in the sitemap", async ({ page, request }) => {
    const titles = new Set<string>();
    const descriptions = new Set<string>();
    for (const route of DOCS) {
      await page.goto(BASE + route);
      titles.add(await page.title());
      descriptions.add((await page.locator('meta[name="description"]').getAttribute("content")) ?? "");
    }
    expect(titles.size).toBe(DOCS.length);
    expect(descriptions.size).toBe(DOCS.length);
    const sitemap = await (await request.get(BASE + "/sitemap.xml")).text();
    for (const route of DOCS) expect(sitemap).toContain(`<loc>${SITE}${route}</loc>`);
  });

  test("the landing page links to the docs, and the docs show the diagrams and the real steps", async ({ page, request }) => {
    await page.goto(BASE + "/");
    await expect(page.locator('footer a[href="/docs"]')).toBeVisible();
    for (const [route, pictures] of [
      ["/docs/how-it-works", ["overview", "architecture", "agent workflow"]],
      ["/docs/aws", ["architecture"]],
    ] as const) {
      await page.goto(BASE + route);
      const imgs = page.locator("main figure img");
      await expect(imgs).toHaveCount(pictures.length);
      for (const [i, picture] of pictures.entries()) {
        const img = imgs.nth(i);
        await expect(img).toHaveAttribute("alt", new RegExp(`${picture}: .*OpenCV`));
        const svg = await request.get(BASE + ((await img.getAttribute("src")) ?? ""));
        expect(svg.status()).toBe(200);
        expect(await svg.text()).not.toMatch(/Gradient|filter/i);
      }
    }
    await page.goto(BASE + "/docs/how-it-works");
    await expect(page.locator("main")).toContainText("cv2.KalmanFilter");
    await page.goto(BASE + "/docs/self-hosting");
    const main = page.locator("main");
    for (const words of ["docker compose up -d --build", "SESSION_SECRET", "DATA_KEY", "signin-link", "pehra-data"])
      await expect(main).toContainText(words);
    await expect(main).toContainText(/\d[\d,]* MB/); // measured, from eval/results
    await page.goto(BASE + "/docs/privacy-and-safety");
    await expect(page.locator("main")).toContainText(/never enrols, identifies or estimates the age of children/);
    await expect(page.locator("main")).toContainText(/first seen and last seen/);
  });
});

test.describe("with JavaScript", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/demo/visitor", (route) => route.fulfill({ json: { kind: "signed_out" } }));
    await page.route("**/api/auth/providers", (route) =>
      route.fulfill({ json: { providers: [{ name: "google", label: "Google" }] } }),
    );
  });

  for (const [label, width, height] of [
    ["desktop", 1280, 900],
    ["phone", 390, 844],
  ] as const) {
    test(`on a ${label}, every docs page is axe clean, fits the width, and is photographed`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ reducedMotion: "reduce" });
      for (const route of DOCS) {
        await page.goto(BASE + route);
        await page.locator("main h1").waitFor();
        expect(await seriousProblems(page), route).toEqual([]);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${route} at ${width}px`).toBeLessThanOrEqual(0);
        const name = route.slice(1).replace(/\//g, "_");
        await page.screenshot({ path: `${SHOTS}/${label}-${name}.png`, fullPage: true });
      }
      expect(errors).toEqual([]);
    });
  }

  test("the docs pages are reached and moved through by keyboard", async ({ page }) => {
    await page.goto(BASE + "/");
    await page.locator("header").getByRole("link", { name: "Docs" }).click();
    await expect(page).toHaveURL(/\/docs$/);
    await expect(page.locator("h1")).toHaveText("Documentation");
    const nav = page.locator("nav[aria-label=Documentation]");
    await nav.getByRole("link", { name: "Self hosting" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/docs\/self-hosting$/);
    await expect(page).toHaveTitle(/Self hosting/);
    await expect(nav.getByRole("link", { name: "Self hosting" })).toHaveAttribute("aria-current", "page");
    const block = page.locator("pre.docs-code").first();
    await block.focus();
    await expect(block).toBeFocused();
  });
});

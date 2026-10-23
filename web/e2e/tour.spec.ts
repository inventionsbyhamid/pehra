import { expect, test, type Page } from "@playwright/test";

// Screenshots of every page, for review and the report. Runs only when SHOTS_DIR is set.
const DIR = process.env.SHOTS_DIR;
const USER = process.env.DEMO_USER ?? "judge";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const SAMPLE = process.env.SAMPLE_VIDEO ?? "/app/data/samples/vtest20.mp4";
const ONLY = process.env.SHOTS_ONLY?.split(",");

test.skip(!DIR, "set SHOTS_DIR to take screenshots");
test.use({ timezoneId: process.env.SHOTS_TZ ?? "Asia/Kolkata", colorScheme: process.env.SHOTS_SCHEME === "dark" ? "dark" : "light" });
const PREFIX = process.env.SHOTS_SCHEME === "dark" ? "dark-" : "";

async function shot(page: Page, name: string, fullPage = true) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${PREFIX}${name}.png`, fullPage });
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Name").fill(USER);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cameras", level: 1 })).toBeVisible();
}

async function demoCamera(page: Page): Promise<string> {
  const cams = await (await page.request.get("/api/cameras")).json();
  return cams.find((c: { kind: string }) => c.kind === "demo").id;
}

const want = (name: string) => !ONLY || ONLY.includes(name);

for (const [label, viewport] of [["desktop", { width: 1440, height: 900 }], ["phone", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    test.use({ viewport, deviceScaleFactor: label === "phone" ? 2 : 1 });

    test(`pages ${label}`, async ({ page }) => {
      test.setTimeout(300_000);
      if (want("login")) {
        await page.goto("/");
        await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
        await shot(page, `${label}-01-login`);
      }
      await signIn(page);
      const cam = await demoCamera(page);
      if (want("cameras")) {
        await page.waitForTimeout(1500);
        await shot(page, `${label}-02-cameras`);
      }
      if (want("camera")) {
        await page.goto(`/cameras/${cam}`);
        await page.locator("canvas").waitFor({ state: "visible", timeout: 30_000 }).catch(() => {});
        await page.waitForTimeout(4000);
        await shot(page, `${label}-03-camera`, label === "phone");
        if (label === "desktop") await shot(page, `${label}-03b-camera-full`);
      }
      if (want("zones")) {
        await page.goto(`/cameras/${cam}`);
        await page.getByRole("button", { name: /zones/ }).first().click();
        await page.waitForTimeout(1500);
        const area = page.getByRole("application");
        const box = (await area.boundingBox())!;
        for (const [x, y] of [[0.55, 0.35], [0.95, 0.35], [0.95, 0.95], [0.6, 0.95]]) {
          await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
        }
        await shot(page, `${label}-04-zones-drawing`);
        await page.getByRole("button", { name: "Cancel" }).click();
      }
      if (want("alerts")) {
        await page.goto("/alerts");
        await page.waitForTimeout(2500);
        await shot(page, `${label}-05-alerts`, label === "phone");
      }
      if (want("alert-open")) {
        await page.goto("/alerts");
        await page.locator(".alert-row").first().click();
        await page.waitForTimeout(2500);
        await shot(page, `${label}-05b-alert-open`);
      }
      if (want("timeline")) {
        await page.goto(`/cameras/${cam}/timeline`);
        await page.waitForTimeout(2500);
        await shot(page, `${label}-06-timeline`, false);
      }
      if (want("people")) {
        await page.goto("/people");
        await shot(page, `${label}-07-people`);
      }
      if (want("find")) {
        await page.goto("/find");
        await shot(page, `${label}-08-find`);
      }
      if (want("numbers")) {
        await page.goto("/numbers");
        await page.waitForTimeout(1500);
        await shot(page, `${label}-09-numbers`);
      }
      if (want("terms")) {
        await page.goto("/terms");
        await shot(page, `${label}-10-terms`, false);
      }
      if (want("ask")) {
        await page.goto("/ask");
        await shot(page, `${label}-11-ask-empty`);
      }
    });
  });
}

test("flows desktop", async ({ page }) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);
  const cam = await demoCamera(page);

  if (want("ask-answer")) {
    await page.goto("/ask");
    await page.getByLabel("Your question").fill("when did people enter the left side");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await page.locator(".answer").first().waitFor({ timeout: 90_000 });
    await page.locator(".answer details summary").first().click().catch(() => {});
    await shot(page, "desktop-12-ask-answer");
  }
  if (want("watch")) {
    await page.goto(`/cameras/${cam}`);
    await page.getByLabel("Tell Watchful what to look out for").fill("tell me if more than 3 people are on the left side at once");
    await page.getByRole("button", { name: "Add watch" }).click();
    await page.locator("[data-testid=compiled-watch], .notice-warn").first().waitFor({ timeout: 60_000 });
    await page.locator(".watches").screenshot({ path: `${DIR}/desktop-13-watch-compiled.png` });
  }
  if (want("preset")) {
    await page.goto(`/cameras/${cam}`);
    await page.getByText("Start from a ready made watch").click();
    await page.getByRole("button", { name: "Possible intruder" }).click();
    await page.locator(".watches").screenshot({ path: `${DIR}/desktop-14-preset.png` });
  }
  if (want("alert-detail")) {
    await page.goto("/alerts");
    await page.locator(".alert-row").first().click();
    await page.waitForTimeout(2500);
    await shot(page, "desktop-15-alert-detail");
  }
  if (want("upload")) {
    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles(SAMPLE);
    await page.getByLabel(/^Name/).fill("Plaza recording");
    await page.getByRole("button", { name: "Upload and scan" }).click();
    await page.getByTestId("scan-line").waitFor({ timeout: 120_000 });
    await page.getByText(/alerts? raised/).waitFor({ timeout: 120_000 });
    await page.locator(".add-camera").screenshot({ path: `${DIR}/desktop-16-upload-done.png` });
  }
  if (want("webcam")) {
    await page.goto("/");
    await page.getByRole("tab", { name: "This browser's webcam" }).click();
    await page.getByLabel("Name").fill("Desk webcam");
    await page.getByRole("button", { name: "Add webcam" }).click();
    await page.locator("canvas").waitFor({ state: "visible", timeout: 45_000 }).catch(() => {});
    await page.waitForTimeout(5000);
    await shot(page, "desktop-17-webcam", false);
  }
  // states the demo footage rarely produces, rendered from a rewritten alerts response
  for (const [state, name] of [["awaiting_human", "desktop-19-decision-mocked"], ["unverified", "desktop-20-unverified-mocked"]] as const) {
    if (!want(state)) continue;
    await page.route("**/api/alerts?*", async (route) => {
      const res = await route.fetch();
      const list = await res.json();
      if (list[0]) {
        list[0] = { ...list[0], status: state, severity: "critical", feedback: null };
        if (state === "awaiting_human") {
          list[0].trace = [...list[0].trace.slice(0, -1), { step: 3, tool: "ask_human", args: { question: "Should someone check on this person now?" }, why: "Serious, so a person decides", result: "", ms: 800 }];
        } else {
          list[0].trace = [];
        }
      }
      await route.fulfill({ response: res, json: list });
    });
    await page.goto("/alerts");
    await page.locator(".alert-row").first().click();
    await page.waitForTimeout(2000);
    await shot(page, name, false);
    await page.unroute("**/api/alerts?*");
  }
  if (want("find-result")) {
    await page.goto("/find");
    await page.getByLabel("Find anything").fill("a person in a dark jacket");
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/search"), { timeout: 90_000 }).catch(() => {}),
      page.getByRole("button", { name: "Search", exact: true }).click(),
    ]);
    await page.waitForTimeout(1500);
    await shot(page, "desktop-18-find-results");
  }
});

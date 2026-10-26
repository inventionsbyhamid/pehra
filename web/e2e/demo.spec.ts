import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// The judges' demo against a server built from this tree: from the landing page's Try the demo to an acknowledged
// incident with its Telegram message in under two minutes, then the judge's own camera with a watch they type
// (the server's real AI) firing on it. Chromium's fake camera plays OpenCV's messi5.jpg sample, a person. Every
// screen: no serious accessibility problem, no dashes, no sideways scroll at 1280 and 390 px; SHOTS_DIR keeps a
// screenshot of each.
const PYTHON = process.env.PYTHON ?? "python";
const SAMPLES = process.env.SAMPLES_DIR ?? path.resolve("../data/samples");
const SHOTS = process.env.SHOTS_DIR;
const BRAND = JSON.parse(readFileSync(new URL("../../brand.json", import.meta.url), "utf8"));
const WIDE = { width: 1280, height: 860 };
const PHONE = { width: 390, height: 844 };
const work = mkdtempSync(path.join(tmpdir(), "demo-e2e-"));
const video = path.join(work, "person.y4m");

execFileSync(PYTHON, ["-c", `
import sys, cv2
img = cv2.resize(cv2.imread(sys.argv[1]), (640, 400))
yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
with open(sys.argv[2], "wb") as f:
    f.write(b"YUV4MPEG2 W640 H400 F10:1 Ip A1:1 C420jpeg\\n")
    for _ in range(30):
        f.write(b"FRAME\\n" + yuv)
`, path.join(SAMPLES, "messi5.jpg"), video]);

test.use({
  timezoneId: "Asia/Kolkata",
  viewport: WIDE,
  launchOptions: {
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-video-capture=${video}`],
  },
});

async function seriousProblems(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

async function check(page: Page, name: string) {
  for (const size of [WIDE, PHONE]) {
    await page.setViewportSize(size);
    await page.waitForTimeout(250);
    expect(await seriousProblems(page), `${name} at ${size.width}`).toEqual([]);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(sideways, `${name} scrolls sideways at ${size.width}`).toBeLessThanOrEqual(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${size.width}.png`, fullPage: true });
  }
  const text = (await page.locator("body").innerText()) + (await page.title());
  expect(text, `${name} has an em or en dash`).not.toMatch(/[—–]/);
  expect(text, `${name} has a hyphenated word`).not.toMatch(/[\p{L}][-‐][\p{L}]/u);
  await page.setViewportSize(WIDE);
}

test("a judge goes from Try the demo to an acknowledged incident and its Telegram message in under two minutes", async ({ page }) => {
  test.setTimeout(180_000);
  const started = Date.now();
  await page.goto("/");
  await page.locator(".hero-actions").getByRole("link", { name: "Try the demo" }).click();

  // the control room of a demo society of their own, with a short demo bar
  await expect(page.getByRole("heading", { name: "Control room", level: 1 })).toBeVisible({ timeout: 30_000 });
  const bar = page.getByRole("region", { name: "Demo" });
  await expect(bar).toContainText("Demo society.");
  await expect(bar.getByRole("button", { name: "Add your camera" })).toBeVisible();
  await bar.getByRole("button", { name: "About the cameras" }).click();
  await expect(bar).toContainText("OpenCV's sample video vtest.avi, Apache 2.0 licence.");
  await expect(bar).toContainText("Incidents before you arrived are examples made from real moments of the clip.");

  // live cameras: the Plaza tile shows a real picture and its page plays frames
  const tile = page.getByRole("list", { name: "Cameras" }).getByRole("link", { name: /Plaza/ });
  await expect(tile.locator("img")).toBeVisible({ timeout: 30_000 });
  expect(await tile.locator("img").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  const feed = page.getByRole("list", { name: "Incidents" });
  await expect(feed.getByRole("listitem").first()).toContainText("Critical");
  await expect(feed.getByText("Example", { exact: true }).first()).toBeVisible();
  await check(page, "demo-01-control-room");
  await tile.click();
  const live = page.getByRole("img", { name: /Live camera picture/ });
  await expect.poll(async () => Number(await live.getAttribute("data-received") ?? 0), { timeout: 30_000 }).toBeGreaterThan(0);
  await check(page, "demo-02-camera");

  // the open critical incident, its picture, and the Telegram message the society's group gets
  await page.getByRole("link", { name: "Control room" }).first().click();
  await feed.getByRole("listitem").first().getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Someone walks into the left walkway");
  await expect(page.getByRole("img", { name: /The moment .* saw it on Plaza/ })).toBeVisible();
  const message = page.getByRole("figure", { name: "The Telegram message" });
  await expect(message).toContainText(/Critical, Plaza, \d{1,2}:\d\d (AM|PM)/);
  await expect(message).toContainText("Not acknowledged yet.");
  await expect(message.getByRole("list", { name: "Its buttons" })).toContainText("Acknowledge");
  await expect(message.getByRole("img")).toBeVisible();
  await expect.poll(() => message.getByRole("img").evaluate((img: HTMLImageElement) => (img.complete ? img.naturalWidth : 0)))
    .toBeGreaterThan(0);
  await check(page, "demo-03-incident");

  // acknowledged: the page and the group's message both say who and when
  await page.getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Acknowledged" })).toBeVisible();
  await expect(message).toContainText("Acknowledged by Demo visitor at");
  await expect(message.getByRole("list", { name: "Its buttons" })).not.toContainText("Acknowledge");
  const took = Date.now() - started;
  console.log(`landing to an acknowledged incident: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(120_000);
  await check(page, "demo-04-acknowledged");
});

test("add your camera: this device joins, a typed watch is saved and fires; a phone joins by QR code", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "Control room", level: 1 })).toBeVisible({ timeout: 30_000 });
  const bar = page.getByRole("region", { name: "Demo" });

  // a phone by QR code: the device link of a camera of its own
  await bar.getByRole("button", { name: "Add your camera" }).click();
  const dialog = page.getByRole("dialog", { name: "Add your camera" });
  await dialog.getByText("A phone", { exact: true }).click();
  await dialog.getByRole("button", { name: "Add it" }).click();
  const qr = dialog.getByRole("img", { name: /QR code/ });
  await expect(qr).toBeVisible();
  // the picture comes from the server: wait for it to load (through a tunnel it can lag the dialog)
  await expect.poll(() => qr.evaluate((img: HTMLImageElement) => (img.complete ? img.naturalWidth : 0)),
    { timeout: 30_000 }).toBeGreaterThan(0);
  await expect(dialog.getByLabel("The phone's camera page")).toHaveValue(/\/cam\//);
  await check(page, "demo-05-phone-qr");
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(dialog).toBeHidden();

  // this device: the camera page sends this browser's camera
  await bar.getByRole("button", { name: "Add your camera" }).click();
  await dialog.getByText("This device", { exact: true }).click();
  await dialog.getByRole("button", { name: "Add it" }).click();
  await expect(page.getByRole("heading", { name: "My camera", level: 1 })).toBeVisible();
  await expect(page.getByText(/\d+ sent\./)).toBeVisible({ timeout: 30_000 });
  // both of the visitor's cameras are in: the bar stops offering more
  await expect(bar.getByRole("button", { name: "Add your camera" })).toHaveCount(0);

  // a watch in their own words, its card, saved
  await page.getByRole("link", { name: "Add a watch" }).first().click();
  const box = page.getByLabel(`What should ${BRAND.name} watch for?`);
  await box.fill("someone in front of the camera");
  await page.getByRole("button", { name: "Check it" }).click();
  const card = page.getByTestId("watch-card");
  await expect(card.getByText("Will watch", { exact: true })).toBeVisible({ timeout: 120_000 });
  await card.getByRole("radio", { name: "Critical" }).check();
  await check(page, "demo-06-watch-card");
  await card.getByRole("button", { name: "Save watch" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Watch saved" })).toBeVisible();

  // this device kept sending while the watch was added (no second press); it fires on the person in front of the
  // camera: a critical incident on this camera
  await expect(page.getByRole("button", { name: "Stop this device" })).toBeVisible();
  await expect(page.getByText(/\d+ sent\./)).toBeVisible({ timeout: 30_000 });
  const mine = async () => {
    const cams = await (await page.request.get("/api/cameras")).json();
    const cam = cams.find((c: { name: string }) => c.name === "My camera");
    const list = await (await page.request.get(`/api/incidents?camera_id=${cam.id}`)).json();
    return list.length;
  };
  await expect.poll(mine, { timeout: 90_000, intervals: [2000] }).toBeGreaterThan(0);
  await page.getByRole("link", { name: "Control room" }).first().click();
  await expect(page.getByRole("list", { name: "Incidents" })).toContainText("My camera");
  await check(page, "demo-07-own-incident");
});

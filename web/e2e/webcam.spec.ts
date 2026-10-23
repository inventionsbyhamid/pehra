import { expect, test } from "@playwright/test";

const USER = process.env.DEMO_USER ?? "judge";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const NAME = "Test webcam";

// Chromium runs with a fake camera (see playwright.config.ts), so this covers the whole loop:
// getUserMedia, JPEG frames over /ws/cameras/{id}/push, analysis, annotated frames back over /ws/cameras/{id}.
test("webcam frames go to the server and come back analysed", async ({ page }) => {
  test.setTimeout(120_000);
  expect(PASSWORD, "set DEMO_PASSWORD").not.toBe("");
  await page.goto("/");
  await page.getByLabel("Name").fill(USER);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cameras", level: 1 })).toBeVisible();

  const cams = await (await page.request.get("/api/cameras")).json();
  let cam = cams.find((c: { kind: string; name: string }) => c.kind === "webcam" && c.name === NAME);
  if (!cam) cam = await (await page.request.post("/api/cameras", { data: { name: NAME, kind: "webcam" } })).json();

  const pushed = page.waitForEvent("websocket", (ws) => ws.url().endsWith(`/ws/cameras/${cam.id}/push`));
  await page.goto(`/cameras/${cam.id}`);
  await page.getByRole("button", { name: "Start webcam" }).click();
  await pushed;

  await expect(page.getByRole("img", { name: /Live camera picture/ })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/\d+ sent\./)).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => {
    const all = await (await page.request.get("/api/cameras")).json();
    return all.find((c: { id: string }) => c.id === cam.id)?.status.stats.frames ?? 0;
  }, { timeout: 30_000 }).toBeGreaterThan(10);
  await expect(page.getByText("The connection to Watchful dropped")).toHaveCount(0);
  await expect(page.locator(".cam-facts")).toContainText(/Live/);

  await page.getByRole("button", { name: "Stop webcam" }).click();
  await expect(page.getByRole("button", { name: "Start webcam" })).toBeVisible();
});

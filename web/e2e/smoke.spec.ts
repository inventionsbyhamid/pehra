import { expect, test, type Page } from "@playwright/test";

const USER = process.env.DEMO_USER ?? "judge";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Name").fill(USER);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cameras", level: 1 })).toBeVisible();
}

test("sign in, add a watch, see alerts and ask about the footage", async ({ page }) => {
  test.setTimeout(300_000);
  expect(PASSWORD, "set DEMO_PASSWORD").not.toBe("");

  await signIn(page);

  const wall = page.getByRole("list", { name: "Cameras" });
  const demo = wall.getByRole("listitem").filter({ hasText: "Demo camera" }).first();
  await expect(demo).toBeVisible();
  await demo.getByRole("heading").getByRole("link").click();

  const cameraId = page.url().split("/cameras/")[1]!.split(/[?#/]/)[0]!;

  // the demo camera plays in real time at about 5 fps; wait for people to enter a zone
  await expect.poll(async () => {
    const r = await page.request.get(`/api/cameras/${cameraId}/timeline`);
    const t = await r.json();
    return t.events.filter((e: { kind: string }) => e.kind === "enter").length;
  }, { timeout: 120_000, intervals: [3000] }).toBeGreaterThan(0);

  await page.getByLabel("Tell Watchful what to look out for").fill("tell me when someone enters the left side");
  await page.getByRole("button", { name: "Add watch" }).click();
  const compiled = page.getByTestId("compiled-watch");
  await expect(compiled).toBeVisible({ timeout: 60_000 });
  await expect(compiled).toContainText("left");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /Alerts/ }).click();
  await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ask" }).click();
  await page.getByLabel("Your question").fill("when did people enter the left side");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const items = page.getByTestId("answer-items").getByRole("listitem");
  await expect(items.first()).toBeVisible({ timeout: 90_000 });
  expect(await items.count()).toBeGreaterThan(0);
});

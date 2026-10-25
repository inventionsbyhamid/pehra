import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { signIn } from "./signin";

const BRAND = JSON.parse(readFileSync(new URL("../../brand.json", import.meta.url), "utf8"));

// The demo society with the real AI: open the demo camera from the wall, add a watch in words, and ask what happened.
test("sign in, add a watch in words and ask about the footage", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);

  const wall = page.getByRole("list", { name: "Cameras" });
  const cams = await (await page.request.get("/api/cameras")).json();
  const demo = cams.find((c: { kind: string; enabled: boolean }) => c.kind === "demo" && c.enabled);
  expect(demo, "needs a demo camera").toBeTruthy();
  await wall.getByRole("link", { name: new RegExp(demo.name.replace(/[()]/g, "\\$&")) }).first().click();
  await expect(page.getByRole("heading", { name: demo.name, level: 1 })).toBeVisible();

  // the demo camera plays in real time at about 5 fps; wait for people to enter a zone
  await expect.poll(async () => {
    const t = await (await page.request.get(`/api/cameras/${demo.id}/timeline`)).json();
    return t.events.filter((e: { kind: string }) => e.kind === "enter").length;
  }, { timeout: 120_000, intervals: [3000] }).toBeGreaterThan(0);

  await page.getByRole("link", { name: "Add a watch" }).click();
  await page.getByLabel(`What should ${BRAND.name} watch for?`).fill("tell me when someone enters the left walkway");
  await page.getByRole("button", { name: "Check it" }).click();
  const card = page.getByTestId("watch-card");
  await expect(card).toBeVisible({ timeout: 120_000 });
  await expect(card).toContainText("left walkway");
  await expect(card.getByText("Last 24 hours", { exact: true })).toBeVisible();

  await page.goto(`/app/cameras/${demo.id}`);
  await page.getByLabel("Ask about this camera's footage").fill("when did people enter the left walkway");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const items = page.getByTestId("answer-items").getByRole("listitem");
  await expect(items.first()).toBeVisible({ timeout: 90_000 });
});

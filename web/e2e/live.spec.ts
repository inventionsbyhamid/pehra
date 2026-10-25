import { expect, test } from "@playwright/test";
import { signIn } from "./signin";


// The server sends plain frames with tracks; the browser draws the boxes and labels itself.
test("live view draws boxes from the tracks sent with each frame", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  const cams = await (await page.request.get("/api/cameras")).json();
  const demo = cams.find((c: { kind: string }) => c.kind === "demo");
  expect(demo, "needs a demo camera").toBeTruthy();

  let withTracks = 0;
  let frames = 0;
  let malformed = 0;
  page.on("websocket", (ws) => {
    if (!ws.url().endsWith(`/ws/cameras/${demo.id}`)) return;
    ws.on("framereceived", ({ payload }) => {
      if (typeof payload !== "string" || !payload.startsWith('{"type":"frame"')) return;
      const msg = JSON.parse(payload);
      frames += 1;
      if (!(msg.width > 0 && msg.height > 0 && Array.isArray(msg.tracks))) malformed += 1;
      else if (msg.tracks.length > 0) withTracks += 1;
    });
  });

  await page.goto(`/cameras/${demo.id}`);
  const live = page.getByRole("img", { name: /Live camera picture/ });
  await expect.poll(() => withTracks, { timeout: 60_000 }).toBeGreaterThan(0);
  await expect.poll(async () => Number(await live.getAttribute("data-boxes") ?? 0), { timeout: 30_000 }).toBeGreaterThan(0);
  expect(frames).toBeGreaterThan(0);
  expect(malformed).toBe(0);
});

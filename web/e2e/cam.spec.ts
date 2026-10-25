import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test, type WebSocketRoute } from "@playwright/test";
import { signIn } from "./signin";

// A device link turns a browser into a camera with no sign in. Chromium's fake camera plays a photo with a face
// (OpenCV's messi5.jpg sample), so the test sees faces covered on the device before a frame is sent, and checks with
// YuNet on the server's copy that no face is left. PYTHON (with OpenCV) and SAMPLES_DIR say where those are.
const PYTHON = process.env.PYTHON ?? "python";
const SAMPLES = process.env.SAMPLES_DIR ?? path.resolve("../data/samples");
const MODEL = process.env.FACE_MODEL ?? path.resolve("../models/face_detection_yunet_2023mar.onnx");
const work = mkdtempSync(path.join(tmpdir(), "cam-e2e-"));
const video = path.join(work, "face.y4m");

function python(code: string, ...args: string[]): string {
  return execFileSync(PYTHON, ["-c", code, ...args], { encoding: "utf8" }).trim();
}

// a few seconds of the photo as raw video, the format Chromium's fake camera reads
python(`
import sys, cv2
img = cv2.resize(cv2.imread(sys.argv[1]), (640, 400))
yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
with open(sys.argv[2], "wb") as f:
    f.write(b"YUV4MPEG2 W640 H400 F10:1 Ip A1:1 C420jpeg\\n")
    for _ in range(30):
        f.write(b"FRAME\\n" + yuv)
`, path.join(SAMPLES, "messi5.jpg"), video);

const FACES = `
import sys, cv2
img = cv2.imread(sys.argv[1])
det = cv2.FaceDetectorYN.create(sys.argv[2], "", (img.shape[1], img.shape[0]), 0.8)
faces = det.detect(img)[1]
print(0 if faces is None else len(faces))
`;

test.use({
  launchOptions: {
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-video-capture=${video}`],
  },
});

test("a device link turns this browser into a camera, faces covered before sending", async ({ page, browser }) => {
  test.setTimeout(240_000);
  await signIn(page);
  const origin = new URL(page.url()).origin;
  const cam = await (await page.request.post("/api/cameras", {
    data: { name: "Pool", kind: "webcam", purpose: "pool" }, headers: { origin },
  })).json();
  const link = await (await page.request.post(`/api/cameras/${cam.id}/device-token`, { headers: { origin } })).json();
  expect(link.url).toContain(`/cam/${link.token}`);

  // the device has no session at all: the link is its only credential
  const device = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const phone = await device.newPage();
  const sockets: WebSocketRoute[] = [];
  await phone.routeWebSocket(/\/ws\/cam\//, (ws) => {
    sockets.push(ws);
    if (sockets.length === 2) return ws.close({ code: 1011, reason: "network down" }); // the first retry fails too
    ws.connectToServer();
  });
  await phone.goto(new URL(link.url).pathname);
  await expect(phone.getByRole("heading", { name: "This device is now the Pool camera" })).toBeVisible();
  await expect(phone.getByRole("note")).toContainText("Faces are blurred on this device before any picture is sent.");
  await expect(phone.getByRole("note")).toContainText("Recordings are kept for 7 days");
  expect(await phone.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await phone.screenshot({ path: path.join(test.info().outputDir, "cam-ready-390.png"), fullPage: true });
  await phone.getByRole("button", { name: "Start the camera" }).click();
  const status = phone.getByRole("status");
  await expect(status).toContainText("Sending to the control room.", { timeout: 120_000 });
  const canvas = phone.getByRole("img", { name: "What this camera sends, with faces covered" });
  await expect.poll(async () => Number(await canvas.getAttribute("data-sent")), { timeout: 30_000 }).toBeGreaterThan(5);
  expect(Number(await canvas.getAttribute("data-covered"))).toBeGreaterThan(0);
  await phone.screenshot({ path: path.join(test.info().outputDir, "cam-live-390.png"), fullPage: true });

  // the control room gets the picture the phone sent, and YuNet finds no face left in it
  const frame = await page.request.get(`/api/cameras/${cam.id}/frame`);
  expect(frame.ok()).toBeTruthy();
  const got = path.join(work, "got.jpg");
  writeFileSync(got, await frame.body());
  expect(Number(python(FACES, path.join(SAMPLES, "messi5.jpg"), MODEL))).toBeGreaterThan(0);
  expect(Number(python(FACES, got, MODEL))).toBe(0);
  expect((await (await page.request.get(`/api/cameras/${cam.id}/device-token`)).json()).sending).toBe(true);

  // the camera's page in the control room shows the phone's picture
  await page.goto(`/cameras/${cam.id}`);
  const live = page.getByRole("img", { name: /Live camera picture/ });
  await expect(live).toHaveAttribute("data-mode", "frames");
  await expect.poll(async () => Number(await live.getAttribute("data-received") ?? 0), { timeout: 30_000 }).toBeGreaterThan(0);

  // a dropped connection: the page says so, retries, and carries on sending
  await sockets[0].close({ code: 1011, reason: "network dropped" });
  await expect(status).toContainText("Lost the connection. Trying again.", { timeout: 20_000 });
  await expect(status).toContainText("Sending to the control room.", { timeout: 30_000 });
  expect(sockets.length).toBeGreaterThanOrEqual(3);
  const before = Number(await canvas.getAttribute("data-sent"));
  await expect.poll(async () => Number(await canvas.getAttribute("data-sent")), { timeout: 20_000 }).toBeGreaterThan(before);

  // a new link replaces this one: the device stops and says why
  await page.request.post(`/api/cameras/${cam.id}/device-token`, { headers: { origin } });
  await expect(status).toContainText("This camera link no longer works.", { timeout: 20_000 });
  await phone.screenshot({ path: path.join(test.info().outputDir, "cam-gone-390.png"), fullPage: true });

  await page.request.delete(`/api/cameras/${cam.id}`, { headers: { origin } });
  await device.close();
});

test("an unknown device link says so plainly", async ({ page }) => {
  await page.goto("/cam/this-link-was-never-issued");
  await expect(page.getByRole("alert")).toHaveText("This camera link no longer works. Ask your society admin for a new one.");
});

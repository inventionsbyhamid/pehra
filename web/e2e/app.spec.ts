import { execFileSync, execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./signin";

// The signed in app against a server built from this tree: each run signs in as a new owner of a new society, so
// it starts empty. Incidents are opened the way the camera manager opens them (alerts/incidents.py), with PYTHON on
// the server's machine; typing a watch uses the server's real AI. SHOTS_DIR keeps a screenshot of every screen at
// 1280 and 390 px.
const PYTHON = process.env.PYTHON ?? "python";
const AUTH = process.env.AUTH_CMD ?? "python -m watchful.auth";
const SHOTS = process.env.SHOTS_DIR;
const BRAND = JSON.parse(readFileSync(new URL("../../brand.json", import.meta.url), "utf8"));
const run = Date.now().toString(36);
const WIDE = { width: 1280, height: 860 };
const PHONE = { width: 390, height: 844 };

test.use({ timezoneId: "Asia/Kolkata", viewport: WIDE });

const OPEN_INCIDENT = `
import sys
from watchful.alerts.incidents import open_or_touch_incident
from watchful.components import make_repo
from watchful.config import get_settings
ws, cam, severity, reason = sys.argv[1:5]
print(open_or_touch_incident(make_repo(get_settings(), ws), cam, None, severity, reason).id)
`;

function openIncident(workspace: string, camera: string, severity: string, reason: string): string {
  return execFileSync(PYTHON, ["-c", OPEN_INCIDENT, workspace, camera, severity, reason], { encoding: "utf8" }).trim().split("\n").pop()!;
}

// a new society for each test, named for it, with this test's owner signed in
let made = 0;
async function signInFresh(page: Page) {
  const tag = `${run}${test.info().workerIndex}x${++made}`;
  const society = `Green Park ${tag}`;
  await signIn(page, `${AUTH} signin-link owner${tag}@example.com --workspace "${society}"`);
  const me = await (await page.request.get("/api/me")).json();
  return { origin: new URL(page.url()).origin, workspace: me.workspace.id as string, society };
}

async function addCamera(page: Page, origin: string, name: string, purpose: string) {
  const res = await page.request.post("/api/cameras", { data: { name, kind: "webcam", purpose }, headers: { origin } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).id as string;
}

async function seriousProblems(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

function expectNoDashes(text: string, where: string) {
  expect(text, `${where} has an em or en dash`).not.toMatch(/[—–]/);
  expect(text, `${where} has a hyphenated word`).not.toMatch(/[\p{L}][-‐][\p{L}]/u);
}

// Every screen: no serious accessibility problems, no dashes, no sideways scroll at phone width, and a screenshot
// at both widths when SHOTS_DIR is set.
async function check(page: Page, name: string) {
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  for (const size of [WIDE, PHONE]) {
    await page.setViewportSize(size);
    await page.waitForTimeout(300);
    expect(await seriousProblems(page), `${name} at ${size.width}`).toEqual([]);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(sideways, `${name} scrolls sideways at ${size.width}`).toBeLessThanOrEqual(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${size.width}.png`, fullPage: true });
  }
  expectNoDashes((await page.locator("main").innerText()) + (await page.title()), name);
  await page.setViewportSize(WIDE);
}

test("the control room shows the camera wall and one feed of incidents; an incident is acknowledged", async ({ page }) => {
  const { origin, workspace } = await signInFresh(page);
  await check(page, "01-control-room-empty");
  await expect(page.getByText("No cameras yet")).toBeVisible();
  await expect(page.getByText("All quiet")).toBeVisible();

  const pool = await addCamera(page, origin, "Pool", "pool");
  const gate = await addCamera(page, origin, "Main gate", "main gate");
  const critical = openIncident(workspace, pool, "critical", "Someone in the pool after hours");
  const warning = openIncident(workspace, gate, "warning", "A vehicle waited at the gate for 6 minutes");

  await page.goto("/app");
  await expect(page.getByRole("heading", { name: "Control room", level: 1 })).toBeVisible();
  const wall = page.getByRole("list", { name: "Cameras" });
  await expect(wall.getByRole("listitem")).toHaveCount(2);
  await expect(wall.getByRole("link", { name: /Pool/ })).toContainText("Alert");
  const feed = page.getByRole("list", { name: "Incidents" });
  await expect(feed.getByRole("listitem")).toHaveCount(2);
  await expect(feed.getByRole("listitem").first()).toContainText("Someone in the pool after hours");
  await expect(feed.getByRole("listitem").first()).toContainText("Critical");
  await expect(feed.getByRole("listitem").first()).toContainText(/Pool, \d{1,2}:\d\d (AM|PM)/);
  await expect(page.getByText("2 open")).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/Ask|Find|Numbers/);
  await check(page, "02-control-room");

  // the Telegram message's link opens the same page
  await page.goto(`/incidents/${critical}`);
  await expect(page).toHaveURL(new RegExp(`/app/incidents/${critical}$`));
  await expect(page.getByRole("heading", { name: "Someone in the pool after hours", level: 1 })).toBeVisible();
  const steps = page.getByRole("list", { name: "What happened" });
  await expect(steps).toContainText("First seen on Pool");
  await check(page, "03-incident-open");

  await page.getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Acknowledged by/ })).toBeVisible();
  await expect(steps).toContainText(/Acknowledged by .* in the app/);
  await expect(page.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);
  const got = await (await page.request.get(`/api/incidents/${critical}`)).json();
  expect(got.status).toBe("acknowledged");
  await check(page, "04-incident-acknowledged");

  await page.goto(`/app/incidents/${warning}`);
  await page.getByRole("button", { name: "False alarm" }).click();
  await expect(page.getByRole("status").filter({ hasText: /^False alarm/ })).toBeVisible();
  await page.goto("/app");
  await expect(feed.getByRole("listitem").nth(1)).toContainText("False alarm");
  await expect(page.getByText("Last 24 hours")).toBeVisible();
});

test("add a camera in three steps with this device and see it live", async ({ page }) => {
  test.setTimeout(240_000);
  await signInFresh(page);
  await page.getByRole("link", { name: "Add a camera" }).click();
  await expect(page.getByRole("heading", { name: "How is this camera connected?" })).toBeVisible();
  await expect(page.getByRole("radio", { name: /This device/ })).toBeChecked();
  await check(page, "05-add-camera-how");
  await page.getByRole("button", { name: "Next" }).click();

  await expect(page.getByRole("heading", { name: "Name it and say what it watches" })).toBeVisible();
  await page.getByLabel("Name").fill("Lobby desk");
  await page.getByRole("radio", { name: "Lobby" }).check();
  await check(page, "06-add-camera-name");
  await page.getByRole("button", { name: "Add camera" }).click();

  await expect(page.getByRole("heading", { name: "Keep the suggested watches" })).toBeVisible();
  const ideas = page.getByRole("checkbox");
  expect(await ideas.count()).toBeGreaterThan(0);
  await check(page, "07-add-camera-keep");
  for (const box of await ideas.all()) await box.uncheck();
  await page.getByRole("button", { name: "Open Lobby desk" }).click();

  await expect(page.getByRole("heading", { name: "Lobby desk", level: 1 })).toBeVisible();
  await expect(page.locator(".page-sub")).toContainText("Lobby");
  const live = page.getByRole("img", { name: /Live camera picture/ });
  await expect(live).toHaveAttribute("data-mode", "overlay");
  await expect(page.getByText(/\d+ sent\./)).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => Number(await live.getAttribute("data-received") ?? 0), { timeout: 90_000 }).toBeGreaterThan(0);
  await expect(page.locator(".page-sub")).toContainText("Live", { timeout: 30_000 });
  await check(page, "08-camera-live");
  await page.getByRole("button", { name: "Stop this device" }).click();
  await expect(page.getByRole("button", { name: "Use this device" })).toBeVisible();
});

test("type a watch, see what it will and can't watch with its backtest, and save it", async ({ page }) => {
  test.setTimeout(300_000);
  const { origin } = await signInFresh(page);
  const pool = await addCamera(page, origin, "Pool", "pool");
  await page.goto(`/app/cameras/${pool}`);
  // every society starts with the camera health watch, on every camera
  const health = page.locator(".watch-list > li").filter({ hasText: "Every camera" });
  await expect(health).toHaveCount(1);
  await check(page, "09-camera-health-watch");
  await page.getByRole("link", { name: "Add a watch" }).click();

  await expect(page.getByRole("heading", { name: "Add a watch", level: 1 })).toBeVisible();
  const box = page.getByLabel(`What should ${BRAND.name} watch for?`);
  await expect(box).toBeFocused();
  await expect(page.getByRole("heading", { name: "Ideas for a pool camera" })).toBeVisible();
  await check(page, "10-watch-editor");

  // words it can only partly watch: the card says what it cannot, and offers the nearest instead
  await box.fill("tell me when a child is alone at the pool");
  await page.getByRole("button", { name: "Check it" }).click();
  const card = page.getByTestId("watch-card");
  await expect(card).toBeVisible({ timeout: 120_000 });
  await expect(card.getByText("Can't watch", { exact: true })).toBeVisible();
  await check(page, "11-watch-card-cannot");

  await box.fill("someone in the water after 10 PM");
  await page.getByRole("button", { name: "Check it" }).click();
  await expect(card.getByText("Will watch", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(card.getByText("Last 24 hours", { exact: true })).toBeVisible();
  await expect(card).toContainText(/(Would have fired|About) \d+/);
  await card.getByRole("radio", { name: "Critical" }).check();
  await expect(card.getByText("Sent to your Telegram group at once")).toBeVisible();
  await check(page, "12-watch-card");
  await card.getByRole("button", { name: "Save watch" }).click();

  await expect(page).toHaveURL(new RegExp(`/app/cameras/${pool}$`));
  await expect(page.getByRole("status").filter({ hasText: "Watch saved" })).toBeVisible();
  const watch = page.locator(".watch-list > li").filter({ hasText: "Someone in the water after 10 PM" });
  await expect(watch).toContainText("Critical");
  const rows = await (await page.request.get(`/api/cameras/${pool}/watches`)).json();
  expect(rows.filter((r: { scope: string }) => r.scope === "camera").map((r: { text: string; severity: string }) => [r.text, r.severity]))
    .toEqual([["someone in the water after 10 PM", "critical"]]);
  await check(page, "13-camera-with-watch");

  // the zone editor opens only from a setup task the watch needs, with the zone's name ready
  const draw = watch.getByRole("button", { name: "Draw the zone" });
  if (await draw.count()) {
    const task = watch.locator(".todo li").filter({ hasText: "Draw a zone called" }).first();
    const name = (await task.innerText()).match(/called (.+?) on/)![1]!;
    await draw.click();
    await expect(page.getByText(`Draw the ${name} zone. A watch needs it.`)).toBeVisible();
    const area = page.getByRole("application");
    const b = (await area.boundingBox())!;
    for (const [x, y] of [[0.2, 0.4], [0.8, 0.4], [0.8, 0.9], [0.2, 0.9], [0.2, 0.4]]) {
      await page.mouse.click(b.x + b.width * x, b.y + b.height * y);
    }
    await expect(page.getByLabel("Name this zone")).toHaveValue(name);
    await check(page, "13b-zone-editor");
    await page.getByRole("button", { name: "Keep it" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: /^Saved\.$/ })).toBeVisible();
    await expect(task).toHaveClass(/done/);
    const cam = await (await page.request.get(`/api/cameras/${pool}`)).json();
    expect(cam.zones.map((z: { name: string }) => z.name)).toEqual([name]);
  }
});

test("the demo camera shows a live still on the wall and plays on its page", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  const cams = await (await page.request.get("/api/cameras")).json();
  const demo = cams.find((c: { kind: string; enabled: boolean }) => c.kind === "demo" && c.enabled);
  test.skip(!demo, "needs the seeded demo camera");
  const tile = page.getByRole("list", { name: "Cameras" }).getByRole("link", { name: new RegExp(demo.name.replace(/[()]/g, "\\$&")) });
  await expect(tile.locator("img")).toBeVisible({ timeout: 60_000 });
  expect(await tile.locator("img").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await check(page, "23-demo-control-room");
  await tile.click();
  const live = page.getByRole("img", { name: /Live camera picture/ });
  await expect(live).toHaveAttribute("data-mode", "frames");
  await expect.poll(async () => Number(await live.getAttribute("data-received") ?? 0), { timeout: 60_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(1500);
  await check(page, "24-demo-camera");
  await page.getByRole("link", { name: "Who came and went" }).click();
  await expect(page.getByRole("heading", { name: "Who came and went", level: 1 })).toBeVisible();
  await check(page, "25-history");
});

test("settings, people and the printable notice", async ({ page }) => {
  const { origin, society: SOCIETY } = await signInFresh(page);
  await addCamera(page, origin, "Pool", "pool");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Society", level: 1 })).toBeVisible();
  await expect(page.locator("main")).toContainText(SOCIETY);
  await check(page, "14-settings-society");

  const sub = page.getByRole("navigation", { name: "Settings" });
  await sub.getByRole("link", { name: "Team" }).click();
  await expect(page.getByRole("table", { name: "What each role can do" })).toContainText("Supervisor");
  await page.getByRole("button", { name: "Make an invite link" }).click();
  await expect(page.getByLabel("Invite link")).toHaveValue(/\/join\//);
  await check(page, "15-settings-team");

  await sub.getByRole("link", { name: "Telegram" }).click();
  await expect(page.getByRole("heading", { name: "Telegram", level: 1 })).toBeVisible();
  await check(page, "16-settings-telegram");

  await expect(page.getByText("Anyone in a connected group can press Acknowledge or False alarm")).toBeVisible();
  await expect(page.getByText(/posted in the group again 2 minutes later, and every 2 minutes after that, up to 3 times/)).toBeVisible();

  await sub.getByRole("link", { name: "Privacy" }).click();
  await expect(page.getByText(/kept for \d+ days/)).toBeVisible();
  const contact = page.getByRole("textbox", { name: "Whom to ask about the cameras" });
  await expect(page.getByText(/Until you set it, they show owner.*@example.com/)).toBeVisible();
  await contact.fill("The society office, 98200 00000");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved." })).toBeVisible();
  expect((await (await page.request.get("/api/notice-contact")).json()).contact).toBe("The society office, 98200 00000");
  await check(page, "17-settings-privacy");
  await page.getByRole("link", { name: "Print the notices" }).click();
  const notice = page.getByRole("article", { name: "Notice for Pool" });
  await expect(notice).toContainText(`Pool, ${SOCIETY}`);
  await expect(notice).toContainText("The society office, 98200 00000");
  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".app-header")).toBeHidden();
  await page.emulateMedia({ media: "screen" });
  await check(page, "18-notice");

  await page.goto("/app/settings/billing");
  await expect(page.getByRole("heading", { name: "Billing", level: 1 })).toBeVisible();
  await expect(page.getByText(/₹699/).first()).toBeVisible();
  await check(page, "19-settings-billing");
  await page.goto("/app/settings/numbers");
  await expect(page.getByRole("heading", { name: "Numbers", level: 1 })).toBeVisible();
  await check(page, "20-settings-numbers");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "People" }).click();
  await expect(page.getByRole("heading", { name: "People", level: 1 })).toBeVisible();
  await check(page, "21-people");
});

test("signed out, the incident link asks to sign in first", async ({ page }) => {
  await page.goto("/incidents/inc_from_telegram");
  await expect(page.getByRole("heading", { name: `Sign in to ${BRAND.name}` })).toBeVisible();
  await check(page, "22-signed-out");
});

test("a viewer sees the room and incidents without the controls", async ({ page }) => {
  await signIn(page, `${AUTH} signin-link viewer${run}@example.com --workspace-id default --role viewer`);
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "People" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Add a camera" })).toHaveCount(0);
  const cams = await (await page.request.get("/api/cameras")).json();
  if (cams[0]) {
    await page.goto(`/app/cameras/${cams[0].id}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(cams[0].name);
    await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Add a watch" })).toHaveCount(0);
    await expect(page.getByRole("checkbox")).toHaveCount(0);
  }
  if (cams[0]) {
    const id = openIncident("default", cams[0].id, "warning", "Someone waited near the gate for 6 minutes");
    await page.goto(`/app/incidents/${id}`);
    await expect(page.getByText("A supervisor can acknowledge this or mark it a false alarm.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);
    await check(page, "26-incident-viewer");
  }
  await nav.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("navigation", { name: "Settings" }).getByRole("link", { name: "Numbers" })).toHaveCount(0);
  await page.goto("/app/watches/new?camera=" + (cams[0]?.id ?? ""));
  await expect(page.getByText("Only a society admin can add watches.")).toBeVisible();
});

test("someone new to the app is asked to name their society first", async ({ page }) => {
  const url = execSync(`${AUTH} signin-link new${run}@example.com`, { encoding: "utf8" }).trim().split("\n").pop()!;
  await page.goto(new URL(url).pathname);
  await page.getByRole("button", { name: /^Sign in to / }).click();
  await expect(page.getByRole("heading", { name: "Name your society to begin." })).toBeVisible();
  await check(page, "27-welcome");
});

test("the other ways in: a phone by QR, a recorder, the bridge with its line once, and a recording", async ({ page }) => {
  test.setTimeout(300_000);
  await signInFresh(page);
  const how = async (way: RegExp) => {
    await page.goto("/app/cameras/new");
    await page.getByRole("radio", { name: way }).check();
    await page.getByRole("button", { name: "Next" }).click();
  };

  await page.goto("/app/cameras/new");
  for (const way of ["This device", "A phone", "A recorder", "Through the bridge", "A camera address", "Upload a recording"]) {
    await expect(page.getByRole("radio", { name: new RegExp(`^${way}`) })).toBeVisible();
  }

  // a phone: the QR code loads and points at the device page
  await how(/^A phone/);
  await page.getByLabel("Name").fill("Pool phone");
  await page.getByRole("radio", { name: "Pool" }).check();
  await page.getByRole("button", { name: "Add camera" }).click();
  await expect(page.getByRole("heading", { name: "Connect the phone", level: 1 })).toBeVisible();
  const qr = page.getByRole("img", { name: /QR code/ });
  await expect.poll(() => qr.evaluate((img: HTMLImageElement) => (img.complete ? img.naturalWidth : 0)), { timeout: 30_000 }).toBeGreaterThan(0);
  await expect(page.getByLabel("The phone's camera page")).toHaveValue(/\/cam\//);
  await check(page, "30-add-phone");

  // a recorder the server reads: the address is built from the brand's template and shown without its password
  await how(/^A recorder/);
  await expect(page.getByRole("radio", { name: "Hikvision" })).toBeChecked();
  await page.getByLabel("Name").fill("Gate recorder");
  await page.getByRole("radio", { name: "Main gate" }).check();
  await page.getByLabel("Recorder address").fill("gate.example.com");
  await page.getByLabel("Camera number").fill("2");
  await page.getByLabel("User").fill("viewer");
  await page.getByLabel("Password").fill("s3cret");
  await check(page, "31-add-recorder");
  await page.getByRole("button", { name: "Add camera" }).click();
  await expect(page.getByRole("heading", { name: "Keep the suggested watches" })).toBeVisible();
  const cams = await (await page.request.get("/api/cameras")).json();
  const rec = cams.find((c: { name: string }) => c.name === "Gate recorder");
  expect([rec.kind, rec.url]).toEqual(["stream", "rtsp://viewer:***@gate.example.com:554/Streaming/Channels/202"]);

  // through the bridge: the cameras.txt line is shown once, with the bridge's steps and files
  await how(/^Through the bridge/);
  await page.getByRole("radio", { name: "CP Plus" }).check();
  await page.getByLabel("Name").fill("Lobby via bridge");
  await page.getByRole("radio", { name: "Lobby" }).check();
  await page.getByLabel("Recorder address").fill("192.168.1.64");
  await page.getByLabel("User").fill("admin");
  await page.getByLabel("Password").fill("pw");
  await page.getByRole("button", { name: "Add camera" }).click();
  await expect(page.getByRole("heading", { name: "Connect the bridge", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Line for cameras.txt")).toHaveValue(/^[\w-]+=rtsp:\/\/admin:pw@192\.168\.1\.64:554\/cam\/realmonitor\?channel=1&subtype=1$/);
  await expect(page.getByLabel("Control room address for the bridge")).toHaveValue(/^--server http/);
  const readme = await page.request.get(await page.getByRole("link", { name: "the bridge steps" }).getAttribute("href") ?? "");
  expect(readme.ok() && (await readme.text()).includes("Through the bridge")).toBeTruthy();
  expect((await page.request.get("/bridge/camera-bridge.zip")).headers()["content-type"]).toBe("application/zip");
  await check(page, "32-add-bridge");

  // a recording: uploaded with its progress, scanned, then opened
  await how(/^Upload a recording/);
  await expect(page.getByRole("heading", { name: "Pick the recording" })).toBeVisible();
  await page.getByLabel("Video file").setInputFiles(`${process.env.SAMPLES_DIR ?? "../data/samples"}/vtest20.mp4`);
  await page.getByLabel("Name (optional)").fill("Plaza clip");
  await page.getByRole("button", { name: "Upload and scan" }).click();
  await expect(page.getByRole("heading", { name: "Scanning the recording" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Scanned \d+:\d\d of video in \d+ s\./)).toBeVisible({ timeout: 240_000 });
  await check(page, "33-add-recording");
  await page.getByRole("button", { name: "Open Plaza clip" }).click();
  await expect(page.getByRole("heading", { name: "Plaza clip", level: 1 })).toBeVisible();
});

test("plate reading is a camera setting, and removing a camera says what goes with it", async ({ page }) => {
  const { origin } = await signInFresh(page);
  const gate = await addCamera(page, origin, "Main gate", "main gate");
  await page.goto(`/app/cameras/${gate}`);
  await page.getByRole("button", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("checkbox", { name: /Plate reading/ }).check();
  await check(page, "34-camera-edit-readers");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect.poll(async () => (await (await page.request.get(`/api/cameras/${gate}`)).json()).readers).toEqual(["plates"]);

  await page.getByRole("button", { name: "Edit" }).click();
  await expect(dialog.getByRole("checkbox", { name: /Plate reading/ })).toBeChecked();
  await dialog.getByRole("button", { name: "Connect a phone" }).click();
  const link = page.getByRole("dialog", { name: "Connect a phone?" });
  await expect(link).toContainText("Any device on the old one stops at once.");
  await link.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Edit" }).click();
  await dialog.getByRole("button", { name: "Remove this camera" }).click();
  await expect(dialog).toContainText("Its watches, incidents and gate log entries, and what it saw, are deleted with it.");
  await check(page, "35-camera-remove");
});

test("a watch that needs plate reading turns it on from its card", async ({ page }) => {
  test.setTimeout(300_000);
  const { origin } = await signInFresh(page);
  const gate = await addCamera(page, origin, "Main gate", "main gate");
  await page.goto(`/app/watches/new?camera=${gate}`);
  await page.getByLabel(`What should ${BRAND.name} watch for?`).fill("tell me when a cab with a yellow plate comes in");
  await page.getByRole("button", { name: "Check it" }).click();
  const card = page.getByTestId("watch-card");
  await expect(card).toBeVisible({ timeout: 120_000 });
  const turnOn = card.getByRole("button", { name: "Turn on plate reading" });
  await expect(turnOn).toBeVisible();
  await check(page, "36-watch-card-plate-reading");
  await turnOn.click();
  await expect(turnOn).toHaveCount(0);
  await expect(card.locator(".task-done")).toContainText("Turn on plate reading");
  expect((await (await page.request.get(`/api/cameras/${gate}`)).json()).readers).toEqual(["plates"]);
});

test("this device keeps sending while a watch is added, and says so on other pages", async ({ page }) => {
  test.setTimeout(240_000);
  const { origin } = await signInFresh(page);
  const desk = await addCamera(page, origin, "Front desk", "lobby");
  const pushes: string[] = [];
  page.on("websocket", (ws) => ws.url().endsWith(`/ws/cameras/${desk}/push`) && pushes.push(ws.url()));
  await page.goto(`/app/cameras/${desk}?webcam=start`);
  const sent = page.getByText(/(\d[\d,]*) sent\./);
  await expect(sent).toBeVisible({ timeout: 30_000 });

  await page.getByRole("link", { name: "Add a watch" }).click();
  const bar = page.getByRole("region", { name: "This device" });
  await expect(bar).toContainText("This device is sending pictures for Front desk.");
  await check(page, "37-device-bar");
  await page.getByRole("link", { name: "Control room" }).first().click();
  await expect(bar).toBeVisible();
  const before = await (await page.request.get(`/api/cameras/${desk}`)).json();
  await page.waitForTimeout(3000);
  const after = await (await page.request.get(`/api/cameras/${desk}`)).json();
  expect(after.status.stats.frames ?? 0).toBeGreaterThan(before.status.stats.frames ?? 0);

  await bar.getByRole("link", { name: "Open Front desk" }).click();
  await expect(page.getByRole("button", { name: "Stop this device" })).toBeVisible();
  await expect(bar).toHaveCount(0);
  expect(pushes).toHaveLength(1); // one socket all along: never stopped, never pressed again
  await page.getByRole("button", { name: "Stop this device" }).click();
  await expect(page.getByRole("button", { name: "Use this device" })).toBeVisible();
});

test("a starting camera says Starting, and a refused address says what to do", async ({ page }) => {
  const { origin } = await signInFresh(page);
  const id = await addCamera(page, origin, "Gate", "main gate");
  const real = await (await page.request.get(`/api/cameras/${id}`)).json();
  const as = (source: string, running = true) => ({ ...real, kind: "stream", health: null, status: { ...real.status, running, source } });
  await page.route("**/api/cameras", (r) => r.fulfill({ json: [as("starting")] }));
  await page.goto("/app");
  const wall = page.locator(".control-wall");
  await expect(wall.locator(".section-head")).toContainText("Starting");
  await expect(wall).not.toContainText("0 of 1 live");

  await page.route(`**/api/cameras/${id}`, (r) => r.fulfill({ json: as("refused") }));
  await page.goto(`/app/cameras/${id}`);
  await expect(page.locator(".page-sub")).toContainText("Address refused");
  await expect(page.getByText("Its address points inside a private network, so this server won't read it.")).toBeVisible();
  await check(page, "38-camera-refused");
});

import { execFileSync } from "node:child_process";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./signin";

// The gate log, the staff register and enrolling staff, against a server built from this tree. Each test signs in as
// the owner of a new society. The logs are filled the way the judges' demo fills them (logs/demo.py: two days of
// passages with rendered plates, six made up staff), and the overstay check is run once, with PYTHON on the server's
// machine. SHOTS_DIR keeps a screenshot of each screen at 1280 and 390 px.
const PYTHON = process.env.PYTHON ?? "python";
const AUTH = process.env.AUTH_CMD ?? "python -m watchful.auth";
const SAMPLES = process.env.SAMPLES_DIR ?? "../data/samples";
const SHOTS = process.env.SHOTS_DIR;
const run = Date.now().toString(36);
const WIDE = { width: 1280, height: 860 };
const PHONE = { width: 390, height: 844 };

test.use({ timezoneId: "Asia/Kolkata", viewport: WIDE });

const FILL_LOGS = `
import sys
from datetime import UTC, datetime
from watchful.components import make_blobs, make_db, make_repo
from sqlalchemy import select
from watchful.config import get_settings
from watchful.db import models as m
from watchful.logs import ledger
from watchful.logs.demo import seed
ws, gate = sys.argv[1:3]
settings = get_settings()
db = make_db(settings)
with db.tx() as s:
    made = seed(s, ws, {"main_gate": gate}, datetime.now(UTC), blobs=make_blobs(settings))
with db.tx() as s:  # the cab still inside is the newest passage; the seed leaves every ninth plate unread
    last = s.scalars(select(m.VehiclePassage).where(m.VehiclePassage.workspace_id == ws)
                     .order_by(m.VehiclePassage.at.desc()).limit(1)).first()
    if last is not None and last.plate_text is None:
        last.plate_text, last.reads_json = "KA05CB7788", ["KA05CB7788", "KA05CB7788"]
ledger.check_stays(make_repo(settings, ws))
print(made["passages"], made["staff"])
`;

// the gate log and the staff register come with the Society plan: a new society is on Free until comped here
const PLAN = `
import sys
from datetime import UTC, datetime, timedelta
from watchful.billing.comp import end, give
from watchful.components import make_db
from watchful.config import get_settings
ws, plan = sys.argv[1:3]
db = make_db(get_settings())
end(db, ws) if plan == "free" else give(db, ws, plan, 4, datetime.now(UTC) + timedelta(days=1))
`;

function onPlan(workspace: string, plan: "society" | "free") {
  execFileSync(PYTHON, ["-c", PLAN, workspace, plan], { encoding: "utf8" });
}

// a row a log watch wrote and a note (an incident at the info level), the way the camera processes and the manager
// write them
const WATCH_LOG = `
import sys, time
from watchful.alerts.incidents import open_or_touch_incident
from watchful.components import make_repo
from watchful.config import get_settings
from watchful.store.db import EventRow
ws, cam = sys.argv[1:3]
repo = make_repo(get_settings(), ws)
now = time.time()
repo.add_events([EventRow(None, cam, now - 600, "watch_log", 3, "car", None,
                          {"watch_id": "w1", "watch": "Cabs at the gate", "reasons": ["a yellow plate came in"]},
                          None, None, None, None),
                 EventRow(None, cam, now - 300, "cross", 4, "person", "gate line", {"direction": "in"}, None, None,
                          None, None)])
open_or_touch_incident(repo, cam, "w_note", "info", "A delivery came in", now=now - 120)
`;

const FLIP = `
import sys, cv2
cv2.imwrite(sys.argv[2], cv2.flip(cv2.imread(sys.argv[1]), 1))
`;

let made = 0;
async function signInFresh(page: Page) {
  const tag = `${run}${test.info().workerIndex}l${++made}`;
  await signIn(page, `${AUTH} signin-link logs${tag}@example.com --workspace "Lake View ${tag}"`);
  const me = await (await page.request.get("/api/me")).json();
  return { origin: new URL(page.url()).origin, workspace: me.workspace.id as string };
}

async function addCamera(page: Page, origin: string, name: string, purpose: string) {
  const res = await page.request.post("/api/cameras", { data: { name, kind: "webcam", purpose }, headers: { origin } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).id as string;
}

async function check(page: Page, name: string) {
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  for (const size of [WIDE, PHONE]) {
    await page.setViewportSize(size);
    await page.waitForTimeout(300);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
    expect(serious, `${name} at ${size.width}`).toEqual([]);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(sideways, `${name} scrolls sideways at ${size.width}`).toBeLessThanOrEqual(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${size.width}.png`, fullPage: true });
  }
  const text = (await page.locator("main").innerText()) + (await page.title());
  expect(text, `${name} has an em or en dash`).not.toMatch(/[—–]/);
  expect(text, `${name} has a hyphenated word`).not.toMatch(/[\p{L}][-‐][\p{L}]/u);
  await page.setViewportSize(WIDE);
}

test("the gate log: a day of passages, plates in plain words, overstay, search, the picture and CSV", async ({ page }) => {
  const { origin, workspace } = await signInFresh(page);
  await page.goto("/app/logs");
  await expect(page).toHaveURL(/\/app\/logs\/vehicles$/);
  await expect(page.getByText("The gate log of every vehicle comes with the Society plan")).toBeVisible();
  await expect(page.getByRole("link", { name: "See plans and billing" })).toHaveAttribute("href", "/app/settings/billing");
  expect((await page.request.get("/api/logs/vehicles")).status()).toBe(403);
  await check(page, "40-gate-log-not-in-plan");

  onPlan(workspace, "society");
  await page.reload();
  await expect(page.getByText("Plate reading is not on yet")).toBeVisible();
  await check(page, "40-gate-log-empty");

  const gate = await addCamera(page, origin, "Main gate", "main gate");
  const [passages] = execFileSync(PYTHON, ["-c", FILL_LOGS, workspace, gate], { encoding: "utf8" }).trim().split("\n").pop()!.split(" ");
  expect(Number(passages)).toBeGreaterThan(10);
  await page.reload();
  const table = page.getByRole("table", { name: /Vehicles in and out, Today/ });
  await expect(table).toBeVisible();
  const counts = (await (await page.request.get("/api/logs/vehicles")).json()).counts;
  await expect(page.locator(".log-summary")).toContainText(`${counts.in} in, ${counts.out} out`);
  await expect(table).toContainText("Yellow plate, commercial or cab");
  await expect(table).toContainText("White plate, private");
  const overstay = table.getByRole("row").filter({ hasText: "Overstay" });
  await expect(overstay).toHaveCount(1);
  await expect(overstay).toContainText(/Still inside, 2 h \d+ min/);
  await check(page, "41-gate-log");

  // detail behind a tap: the passage's picture and its pair
  const plate = (await overstay.getByRole("button").innerText()).trim();
  await overstay.getByRole("button").click();
  const dialog = page.getByRole("dialog", { name: plate });
  const pic = dialog.getByRole("img");
  await expect.poll(() => pic.evaluate((img: HTMLImageElement) => (img.complete ? img.naturalWidth : 0))).toBeGreaterThan(0);
  await expect(dialog).toContainText("Stayed longer than a watch about stays allows");
  await check(page, "42-passage");
  await dialog.getByRole("button", { name: "Close" }).click();

  // search by part of a plate, ignoring spaces and case
  await page.getByLabel("Search a plate").fill(plate.slice(0, 6).toLowerCase());
  await expect(table.getByRole("row")).toHaveCount(2); // the head and the one cab
  await page.getByLabel("Search a plate").fill("");

  const csv = page.getByRole("link", { name: "Download CSV" });
  const download = await page.request.get((await csv.getAttribute("href"))!);
  const lines = (await download.text()).trim().split("\n");
  expect(lines[0]).toContain("Date,Time,Direction,Vehicle,Plate");
  expect(lines.length - 1).toBe(counts.in + counts.out);

  // yesterday: a full day, and the day after it is today again
  await page.getByRole("button", { name: "Day before" }).click();
  await expect(page.getByRole("table", { name: /Vehicles in and out, Yesterday/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Day after" })).toBeEnabled();
  await page.getByRole("button", { name: "Day after" }).click();
  await expect(page.getByRole("button", { name: "Day after" })).toBeDisabled();

  // a phone: cards, not a table that scrolls sideways
  await page.setViewportSize(PHONE);
  await expect(table).toBeHidden();
  const cards = page.getByRole("list", { name: /Vehicles in and out/ });
  await expect(cards.getByRole("listitem").first()).toBeVisible();
  await cards.getByRole("button", { name: new RegExp(plate) }).click();
  await expect(page.getByRole("dialog", { name: plate })).toBeVisible();
  await page.setViewportSize(WIDE);
});

test("the staff register: first seen and last seen per person, search, CSV and the plan's limit", async ({ page }) => {
  const { origin, workspace } = await signInFresh(page);
  await page.goto("/app/logs/staff");
  await expect(page.getByText("The staff register from consented faces comes with the Society plan")).toBeVisible();
  onPlan(workspace, "society");
  await page.reload();
  await expect(page.getByText("Nobody is enrolled yet")).toBeVisible();
  await expect(page.getByRole("link", { name: "Go to People" })).toBeVisible();

  const gate = await addCamera(page, origin, "Service gate", "service gate");
  execFileSync(PYTHON, ["-c", FILL_LOGS, workspace, gate], { encoding: "utf8" });
  await page.reload();
  const table = page.getByRole("table", { name: /Staff first seen and last seen, Today/ });
  await expect(table.getByRole("row")).toHaveCount(7);
  const sunita = table.getByRole("row").filter({ hasText: "Sunita" });
  await expect(sunita).toContainText("Maid");
  await expect(sunita).toContainText(/\d{1,2}:\d\d (AM|PM)/);
  await expect(page.locator(".log-summary")).toContainText(/\d of 6 staff seen/);
  await expect(table.getByRole("columnheader")).toHaveText(["Name", "First seen", "Last seen", "Seen on"]);
  await check(page, "43-staff-register");

  await page.getByLabel("Search a name").fill("sun");
  await expect(table.getByRole("row")).toHaveCount(2);
  const download = await page.request.get((await page.getByRole("link", { name: "Download CSV" }).getAttribute("href"))!);
  expect((await download.text()).split("\n")[0]).toMatch(/^Date,Name,Role,/);

  // six made up staff are over the free plan's five: People says so plainly instead of the form
  onPlan(workspace, "free");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "People" }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Your plan names up to 5 staff members\./ })).toBeVisible();
  await expect(page.getByLabel("Name")).toHaveCount(0);
});

test("the watch log: rows log watches wrote and notes, on every plan, and on the camera's history", async ({ page }) => {
  const { origin, workspace } = await signInFresh(page);
  await page.goto("/app/logs/watches");
  await expect(page.getByText("Nothing in the watch log on this day")).toBeVisible();
  const gate = await addCamera(page, origin, "Main gate", "main gate");
  execFileSync(PYTHON, ["-c", WATCH_LOG, workspace, gate], { encoding: "utf8" });
  await page.reload();
  const table = page.getByRole("table", { name: /The watch log, Today/ });
  await expect(table.getByRole("row")).toHaveCount(3);
  await expect(table).toContainText("Cabs at the gate");
  await expect(table).toContainText("A yellow plate came in");
  await expect(table.getByRole("link", { name: "A delivery came in" })).toBeVisible();
  await expect(page.locator(".log-summary")).toContainText("1 row from log watches, 1 note");
  await check(page, "47-watch-log");
  const download = await page.request.get((await page.getByRole("link", { name: "Download CSV" }).getAttribute("href"))!);
  expect((await download.text()).split(/\r?\n/)[0]).toBe("Date,Time,Camera,Watch,What,Kind");

  await page.goto(`/app/cameras/${gate}/history`);
  const events = page.locator(".events");
  await expect(events).toContainText("Cabs at the gate: a yellow plate came in");
  await expect(events).toContainText("Someone crossed gate line going in");
  await expect(events.getByRole("link", { name: "A delivery came in" })).toBeVisible();
  await check(page, "48-camera-history");
});

test("enrol a staff member from 3 photos with consent, and remove them on exit", async ({ page }) => {
  test.setTimeout(180_000);
  await signInFresh(page);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "People" }).click();
  await expect(page.getByRole("heading", { name: "People", level: 1 })).toBeVisible();
  await expect(page.getByText("Adults only, with their consent.")).toBeVisible();
  await expect(page.getByText("No photo is kept.")).toBeVisible();
  await expect(page.getByText("Nobody is enrolled yet")).toBeVisible();

  const flipped = `${test.info().outputDir}/basketball1_flip.png`;
  execFileSync("mkdir", ["-p", test.info().outputDir]);
  execFileSync(PYTHON, ["-c", FLIP, `${SAMPLES}/basketball1.png`, flipped]);
  await page.getByLabel("Name").fill("Sunita");
  await page.getByLabel("Role").selectOption("maid");
  const enrol = page.getByRole("button", { name: "Enrol" });
  await page.getByLabel("Photos of their face").setInputFiles([`${SAMPLES}/basketball1.png`, `${SAMPLES}/basketball2.png`]);
  await expect(page.getByRole("list", { name: "Chosen photos" }).getByRole("listitem")).toHaveCount(2);
  await expect(enrol).toBeDisabled(); // two are not enough
  await page.getByLabel("Photos of their face").setInputFiles([flipped]);
  await expect(page.getByText("Sunita is an adult, 18 or over, and agreed")).toBeVisible();
  await expect(enrol).toBeDisabled(); // nor without the tick
  await page.getByRole("checkbox", { name: "Sunita agreed to this, and it is true." }).check();
  await check(page, "44-people-enrol");
  await enrol.click();
  await expect(page.getByRole("status").filter({ hasText: "Sunita is enrolled." })).toBeVisible({ timeout: 60_000 });
  const staff = await (await page.request.get("/api/staff")).json();
  expect(staff.staff.map((s: { name: string; role: string; faces: number }) => [s.name, s.role, s.faces])).toEqual([["Sunita", "maid", 3]]);
  await expect(page.getByRole("list", { name: "Chosen photos" })).toHaveCount(0);
  await check(page, "45-people");

  await page.getByRole("button", { name: "Remove Sunita" }).click();
  const dialog = page.getByRole("dialog", { name: "Remove Sunita?" });
  await expect(dialog).toContainText("Sunita will not be recognised again, on any camera.");
  await check(page, "46-people-remove");
  await dialog.getByRole("button", { name: "Remove Sunita" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Sunita is removed and will not be recognised again." })).toBeVisible();
  expect((await (await page.request.get("/api/staff")).json()).staff).toEqual([]);
});

test("the invite page shows the society and the role and joins only on a click", async ({ page, browser }) => {
  const { origin } = await signInFresh(page);
  const made = await (await page.request.post("/api/invites", { data: { role: "supervisor" }, headers: { origin } })).json();
  const guest = await (await browser.newContext({ viewport: WIDE })).newPage();
  const url = execFileSync("sh", ["-c", `${AUTH} signin-link guest${run}${made.invite.id.slice(-4)}@example.com`], { encoding: "utf8" }).trim().split("\n").pop()!;
  await guest.goto(new URL(url).pathname);
  await guest.getByRole("button", { name: /^Sign in to / }).click();
  await guest.goto(new URL(made.url).pathname);
  const society = (await (await page.request.get("/api/me")).json()).workspace.name;
  await expect(guest.getByRole("heading", { name: `Join ${society}`, level: 1 })).toBeVisible();
  await expect(guest.getByText("Supervisor", { exact: true })).toBeVisible();
  await expect(guest.getByText("Nothing changes until you press Join.")).toBeVisible();
  await check(guest, "47-invite");
  await guest.getByRole("button", { name: `Join ${society}` }).click();
  await expect(guest.getByRole("heading", { name: "Control room", level: 1 })).toBeVisible();
  expect((await (await guest.request.get("/api/me")).json()).role).toBe("supervisor");
});

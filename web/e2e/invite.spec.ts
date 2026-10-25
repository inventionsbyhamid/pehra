import { expect, test } from "@playwright/test";
import { signIn } from "./signin";

// Two new people each run: an owner who makes an invite, and someone with a society of their own who opens the
// link. AUTH_CMD runs the operator's command where the server's database is, as SIGNIN_LINK_CMD does.
const AUTH = process.env.AUTH_CMD ?? "python -m watchful.auth";
const run = Date.now().toString(36);
const OWNER = `invite-owner-${run}@example.com`;
const link = (email: string, society: string) => `${AUTH} signin-link ${email} --workspace "${society}"`;

test("an invite shows the society and joins only on a click", async ({ page, browser }) => {
  await signIn(page, link(OWNER, "Green Park"));
  const origin = new URL(page.url()).origin;
  const made = await (await page.request.post("/api/invites", { data: { role: "viewer" }, headers: { origin } })).json();
  const path = new URL(made.url).pathname;

  const guest = await (await browser.newContext()).newPage();
  await signIn(guest, link(`invitee-${run}@example.com`, "Invitee home"));
  const society = async () => (await (await guest.request.get("/api/me")).json()).workspace.name;
  expect(await society()).toBe("Invitee home");

  await guest.goto(path);
  await expect(guest.getByRole("heading", { name: "Join Green Park" })).toBeVisible();
  await expect(guest.getByText(`${OWNER} invited you to join as a viewer`)).toBeVisible();
  expect(await society()).toBe("Invitee home"); // opening the link joins nothing

  await guest.getByRole("button", { name: "Join Green Park" }).click();
  await expect(guest.getByRole("heading", { name: "Cameras", level: 1 })).toBeVisible();
  const me = await (await guest.request.get("/api/me")).json();
  expect([me.workspace.name, me.role]).toEqual(["Green Park", "viewer"]);

  await guest.goto(path);
  await expect(guest.getByRole("alert")).toHaveText("This invite was already used. Ask for a new one.");
  await expect(guest.getByRole("button", { name: /^Join / })).toHaveCount(0);
});

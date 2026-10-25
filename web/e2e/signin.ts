import { execSync } from "node:child_process";
import { expect, type Page } from "@playwright/test";

// Signs in with a single use link from the operator's command on the server (python -m watchful.auth signin-link).
// SIGNIN_LINK_CMD runs where the server's database is (for a remote server, wrap it in ssh); by default it opens the
// demo society as its owner.
const CMD = process.env.SIGNIN_LINK_CMD
  ?? "python -m watchful.auth signin-link judge@example.com --workspace-id default";

export async function signIn(page: Page) {
  const url = execSync(CMD, { encoding: "utf8" }).trim().split("\n").pop()!;
  await page.goto(new URL(url).pathname);
  await page.getByRole("button", { name: /^Sign in to / }).click();
  await expect(page.getByRole("heading", { name: "Cameras", level: 1 })).toBeVisible();
}

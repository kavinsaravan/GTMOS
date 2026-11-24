import { expect, test } from "@playwright/test";

const CID = process.env.E2E_CAMPAIGN_ID ?? "cmp_6aebf01cc887";

test("new campaign screen renders and validates", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Find your next customers." })).toBeVisible();
  await page.getByRole("button", { name: "Generate Customer Profile" }).click();
  // Native validation blocks submit: still on the intake screen.
  await expect(page).toHaveURL(/\/$/);
});

test("ICP screen shows editable cards and confidence", async ({ page }) => {
  await page.goto(`/campaigns/${CID}/icp`);
  await expect(page.getByText("Buyer titles")).toBeVisible();
  await expect(page.getByText(/% confidence/).first()).toBeVisible();
});

test("leads table sorts by fit and links to detail", async ({ page }) => {
  await page.goto(`/campaigns/${CID}/leads`);
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  const scores = await rows.locator("td:nth-child(2) span.w-7").allTextContents();
  const nums = scores.map(Number);
  expect(nums).toEqual([...nums].sort((a, b) => b - a));
  await rows.first().getByRole("link").click();
  await expect(page).toHaveURL(/\/leads\/lead_/);
  await expect(page.getByText("Why it matches")).toBeVisible();
  await expect(page.getByText("Outreach draft")).toBeVisible();
  await expect(page.getByRole("button", { name: /send/i })).toHaveCount(0); // no send button in the MVP
});

test("command center shows kanban, stats and timeline", async ({ page }) => {
  await page.goto(`/campaigns/${CID}/dashboard`);
  for (const col of ["Matched", "Contacted", "Replied", "Meeting Booked", "Closed-Lost"]) {
    await expect(page.getByRole("region", { name: col })).toBeVisible();
  }
  await expect(page.getByText("Confirmed sends")).toBeVisible();
  await expect(page.getByText("Activity timeline")).toBeVisible();
});

test("API token never reaches the browser", async ({ page }) => {
  const seen: string[] = [];
  page.on("request", (r) => seen.push(JSON.stringify(r.headers())));
  await page.goto(`/campaigns/${CID}/leads`);
  await page.waitForLoadState("networkidle");
  expect(seen.join("")).not.toContain("x-gtmos-token");
});

test("every lead detail page renders without a client error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`/campaigns/${CID}/leads`);
  await page.locator("tbody tr").first().waitFor();
  const hrefs = await page.locator("tbody tr a").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).getAttribute("href")!));
  expect(hrefs.length).toBeGreaterThan(0);
  for (const href of hrefs) {
    await page.goto(href);
    await expect(page.getByText("Outreach draft")).toBeVisible();
  }
  expect(errors).toEqual([]);
});

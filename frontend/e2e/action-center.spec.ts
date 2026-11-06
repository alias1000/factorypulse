import { expect, test, type Page } from "@playwright/test";
import { apiLogin, loginAs } from "./helpers";

/** Pick an option of a Radix select identified by its trigger test id. */
async function choose(page: Page, testId: string, option: string | RegExp) {
  await page.getByTestId(testId).click();
  await page.getByRole("option", { name: option }).click();
}

test.describe("Action Center", () => {
  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (e) => { throw e; });
  });

  test("overview shows real KPIs, alerts and overdue items", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/actions");
    await expect(page.getByTestId("ac-kpi-open-value")).toHaveText(/^\d+$/);
    const summary = await (await page.context().request.get("/api/action-center/summary")).json();
    await expect(page.getByTestId("ac-kpi-open-value")).toHaveText(String(summary.kpis.open_total));
    await expect(page.getByTestId("ac-kpi-overdue-value")).toHaveText(String(summary.kpis.overdue_actions));
    expect(summary.kpis.overdue_actions).toBeGreaterThan(0);
    await expect(page.getByTestId("ac-alerts").locator("[data-rule=critical_unassigned]")).toBeVisible();
    // alerts come from the scheduled worker job; the dashboard only shows when it last ran
    await expect(page.getByTestId("alert-evaluation")).toHaveAttribute("data-state", "ok");
    await page.getByTestId("evaluate-now").click();
    await expect(page.getByText("Alert rules evaluated")).toBeVisible();
    await expect(page.getByTestId("ac-overdue-actions")).toBeVisible();
    // severity filter narrows the KPI
    await choose(page, "acf-severity", "Critical");
    await expect(page.getByTestId("ac-kpi-open-value")).toHaveText(String(summary.kpis.critical_open));
  });

  test("admin creates an investigation manually and assigns it to the analyst", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/actions/investigations/new");
    await page.getByLabel("Title *").fill("E2E: Vibration at M-04 spindle");
    await page.getByLabel("Description").fill("Operator reported vibration; cause unknown.");
    await choose(page, "inv-category", "Equipment failure");
    await choose(page, "inv-severity", "High");
    await choose(page, "inv-line", /^L1/);
    await choose(page, "inv-machine", /^M-04/);
    await choose(page, "inv-owner", "Paul Analyst");
    await page.getByTestId("create-submit").click();
    await expect(page.getByTestId("inv-title")).toHaveText("E2E: Vibration at M-04 spindle");
    await expect(page.getByTestId("inv-number")).toHaveText(/INV-\d{4}-\d{4}/);
    await page.getByTestId("tab-timeline").click();
    await expect(page.getByTestId("timeline").locator("[data-event=created]")).toBeVisible();
    await expect(page.getByTestId("timeline").locator("[data-event=assigned]")).toContainText("Paul Analyst");
    await page.goto("/actions/investigations");
    await page.getByTestId("inv-search").fill("Vibration at M-04");
    await expect(page.getByTestId("inv-total")).toHaveText("1 investigations");
  });

  test("analyst: anomaly → investigation → actions → verification → resolved", async ({ page }) => {
    await loginAs(page, "analyst");
    await page.goto("/insights");
    const rows = page.locator("[data-testid=anomaly-table] tbody tr");
    await expect(rows.first()).toBeVisible();
    const n = await rows.count();
    for (let i = 0; i < n; i++) {
      if ((await rows.nth(i).getByTestId("anomaly-investigation-link").count()) === 0) { await rows.nth(i).click(); break; }
    }
    await page.getByTestId("create-investigation").click();
    await expect(page.getByTestId("anomaly-evidence")).toBeVisible();
    const anomalyId = new URL(page.url()).searchParams.get("anomaly")!;
    // prefilled, reviewable – nothing saved yet
    await expect(page.getByLabel("Title *")).not.toHaveValue("");
    await expect(page.getByLabel("Description")).toHaveValue(/cause has not been established/);
    await choose(page, "inv-owner", "Paul Analyst");
    await page.getByTestId("create-submit").click();
    await expect(page.getByTestId("anomaly-snapshot")).toBeVisible();
    const invUrl = page.url();

    // duplicate prevention
    await page.goto(`/actions/investigations/new?anomaly=${encodeURIComponent(anomalyId)}`);
    await expect(page.getByTestId("duplicate-anomaly")).toBeVisible();

    await page.goto(invUrl);
    await page.getByTestId("to-in_progress").click();
    await page.getByTestId("transition-ok").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("[data-status=in_progress]").first()).toBeVisible();
    // verification is blocked until actions exist and are finished
    await expect(page.getByTestId("to-pending_verification")).toBeDisabled();

    await page.getByTestId("tab-actions").click();
    await page.getByTestId("add-action").click();
    await page.getByLabel("Action *").fill("Check feed override log");
    await choose(page, "act-owner", "Paul Analyst");
    await page.getByTestId("action-save").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const card = page.locator("[data-testid^=action-]").filter({ hasText: "Check feed override log" }).first();
    await card.getByRole("button", { name: "Start" }).click();
    await page.getByTestId("action-transition-ok").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(card.locator("[data-status=in_progress]")).toBeVisible();
    await card.getByRole("button", { name: "Complete" }).click();
    await expect(page.getByTestId("action-transition-ok")).toBeDisabled(); // notes required
    await page.getByTestId("action-notes").fill("Override reset to 100 %; documented.");
    await page.getByTestId("action-transition-ok").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(card.locator("[data-status=done]")).toBeVisible();
    await card.getByRole("button", { name: "Verify effectiveness" }).click();
    await choose(page, "verify-result", "Partially effective");
    await page.getByTestId("action-transition-ok").click();
    await expect(card.locator("[data-status=verified]")).toBeVisible();

    await expect(page.getByTestId("to-pending_verification")).toBeEnabled();
    await page.getByTestId("to-pending_verification").click();
    await page.getByTestId("transition-ok").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByTestId("to-resolved").click();
    await page.getByTestId("transition-resolution").fill("short");
    await expect(page.getByTestId("transition-ok")).toBeDisabled();
    await page.getByTestId("transition-resolution").fill("Feed override reset; monitoring shows no repeat so far (observed).");
    await page.getByTestId("transition-ok").click();
    await expect(page.locator("[data-status=resolved]").first()).toBeVisible();
    // analysts cannot close – admin-only transition is not offered
    await expect(page.getByTestId("to-closed")).toHaveCount(0);

    await page.getByTestId("tab-timeline").click();
    for (const ev of ["created", "status_changed", "action_created", "action_status_changed", "action_verified"]) {
      await expect(page.getByTestId("timeline").locator(`[data-event=${ev}]`).first()).toBeVisible();
    }
  });

  test("comments, evidence upload/validation/download and KPI comparison", async ({ page }) => {
    await loginAs(page, "admin");
    const list = await (await page.context().request.get("/api/investigations?search=INV-2026-0005")).json();
    await page.goto(`/actions/investigations/${list.rows[0].id}`);
    await page.getByTestId("comment-input").fill("E2E comment: checked lubrication schedule.");
    await page.getByTestId("comment-post").click();
    await expect(page.getByTestId("comments")).toContainText("E2E comment");

    await page.getByTestId("tab-evidence").click();
    await page.getByTestId("evidence-file").setInputFiles({ name: "readings.csv", mimeType: "text/csv", buffer: Buffer.from("date;value\n2026-10-01;12\n") });
    await page.getByTestId("evidence-upload").click();
    await expect(page.getByTestId("evidence-list")).toContainText("readings.csv", { timeout: 20_000 });
    const scanner = (await (await page.context().request.get("/api/settings")).json()).system.evidence_scanner;
    if (scanner === "none") {
      // no scanner: quarantined until an administrator checks and releases the file
      const row = page.getByTestId("evidence-list").locator("li").filter({ hasText: "readings.csv" });
      await expect(row.locator("[data-scan=not_scanned]")).toBeVisible();
      await row.getByRole("button", { name: "Release" }).click();
      await page.getByTestId("prompt-text").fill("Checked on an isolated workstation");
      await page.getByTestId("prompt-ok").click();
      await expect(row.getByText("Released by admin")).toBeVisible();
    } else {
      await expect(page.getByTestId("evidence-list").locator("[data-scan=clean]")).toHaveCount(1);
      // EICAR test string: the scanner quarantines it – no download button for anybody
      await page.getByTestId("evidence-file").setInputFiles({ name: "eicar.csv", mimeType: "text/csv",
        buffer: Buffer.from("X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*") });
      await page.getByTestId("evidence-upload").click();
      const infected = page.getByTestId("evidence-list").locator("li").filter({ hasText: "eicar.csv" });
      await expect(infected.locator("[data-scan=infected]")).toBeVisible({ timeout: 20_000 });
      await expect(infected.getByRole("button", { name: "Download" })).toHaveCount(0);
    }
    // a disguised executable is rejected
    await page.getByTestId("evidence-file").setInputFiles({ name: "photo.png", mimeType: "image/png", buffer: Buffer.from("MZ\x90\x00not a png") });
    await page.getByTestId("evidence-upload").click();
    await expect(page.getByTestId("evidence-form").getByRole("alert")).toBeVisible();
    const dl = page.waitForEvent("download");
    await page.getByTestId("evidence-list").getByRole("button", { name: "Download" }).first().click();
    expect((await dl).suggestedFilename()).toBe("readings.csv");

    await page.getByTestId("tab-kpi").click();
    await expect(page.getByTestId("kpi-table")).toBeVisible();
    await expect(page.getByText("Observed change – not proof of cause")).toBeVisible();
    // explicit periods inside the synthetic demo data (Jul–Sep 2026): the default periods follow the
    // investigation dates, which are relative to "today" and leave the demo data range as time passes
    await page.getByTestId("kpi-bs").fill("2026-09-01");
    await page.getByTestId("kpi-be").fill("2026-09-14");
    await page.getByTestId("kpi-cs").fill("2026-09-15");
    await page.getByTestId("kpi-ce").fill("2026-09-28");
    await page.getByTestId("kpi-calc").click();
    await expect(page.getByTestId("kpi-table").locator("[data-metric=oee]")).toContainText("%");
    // a period without data → N/A, not invented numbers
    await page.getByTestId("kpi-cs").fill("2030-01-01");
    await page.getByTestId("kpi-ce").fill("2030-01-14");
    await page.getByTestId("kpi-calc").click();
    await expect(page.getByTestId("kpi-insufficient")).toBeVisible();
    await expect(page.getByTestId("kpi-table").locator("[data-metric=oee]")).toContainText("N/A");
    await page.getByTestId("kpi-record").click();
    await page.getByTestId("tab-timeline").click();
    await expect(page.getByTestId("timeline").locator("[data-event=kpi_comparison_recorded]")).toBeVisible();
    await expect(page.getByTestId("timeline").locator("[data-event=attachment_added]").first()).toBeVisible();
  });

  test("my actions lists the analyst's overdue work", async ({ page }) => {
    await loginAs(page, "analyst", "de");
    await page.goto("/actions/mine");
    await expect(page.getByRole("heading", { level: 1, name: "Meine Maßnahmen" })).toBeVisible();
    await expect(page.getByTestId("mine-overdue")).toContainText("überfällig");
  });

  test("viewer is read-only in the UI and rejected by the API", async ({ page }) => {
    await loginAs(page, "viewer");
    const req = page.context().request;
    const list = await (await req.get("/api/investigations?search=INV-2026-0004")).json();
    const id = list.rows[0].id;
    await page.goto(`/actions/investigations/${id}`);
    await expect(page.getByTestId("workflow")).toContainText("No status change available");
    await expect(page.getByTestId("edit-inv")).toHaveCount(0);
    await expect(page.getByTestId("comment-input")).toHaveCount(0);
    await page.getByTestId("tab-actions").click();
    await expect(page.getByTestId("add-action")).toHaveCount(0);
    await page.goto("/actions");
    await expect(page.getByTestId("ac-new")).toHaveCount(0);
    await page.goto("/actions/investigations/new");
    await expect(page.getByTestId("no-access")).toBeVisible();
    await page.goto("/settings");
    await expect(page.getByLabel("Critical still open after (h)")).toBeDisabled();
    await expect(page.getByTestId("rules-save")).toHaveCount(0);

    // direct API calls are rejected regardless of the UI
    const csrf = await apiLogin(req, "viewer");
    const h = { headers: { "X-CSRF-Token": csrf } };
    expect((await req.post("/api/investigations", { ...h, data: { title: "x y z", category: "other", severity: "low", line_id: list.rows[0].line.id } })).status()).toBe(403);
    expect((await req.post(`/api/investigations/${id}/comments`, { ...h, data: { body: "hi" } })).status()).toBe(403);
    expect((await req.post(`/api/investigations/${id}/transition`, { ...h, data: { to_status: "closed" } })).status()).toBe(403);
    expect((await req.put("/api/action-center/settings", { ...h, data: {} })).status()).toBe(403);
  });

  test("analyst cannot use admin-only transitions or settings via the API; anonymous gets 401", async ({ page, playwright, baseURL }) => {
    const req = page.context().request;
    const csrf = await apiLogin(req, "analyst");
    const h = { headers: { "X-CSRF-Token": csrf } };
    const list = await (await req.get("/api/investigations?search=INV-2026-0002")).json(); // resolved
    const res = await req.post(`/api/investigations/${list.rows[0].id}/transition`, { ...h, data: { to_status: "closed", resolution_summary: "Closing attempt by analyst" } });
    expect(res.status()).toBe(403);
    const open = await (await req.get("/api/investigations?search=INV-2026-0006")).json(); // open
    const bad = await req.post(`/api/investigations/${open.rows[0].id}/transition`, { ...h, data: { to_status: "resolved", resolution_summary: "x".repeat(20) } });
    expect(bad.status()).toBe(422);
    expect((await bad.json()).code).toBe("invalid_transition");
    expect((await req.put("/api/action-center/settings", { ...h, data: {} })).status()).toBe(403);

    const anon = await playwright.request.newContext({ baseURL });
    expect((await anon.get("/api/investigations")).status()).toBe(401);
    expect((await anon.get("/api/action-center/summary")).status()).toBe(401);
    await anon.dispose();
  });

  test("admin edits alert rules", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/settings");
    const input = page.getByLabel("Pending verification longer than (days)");
    await input.fill("10");
    await page.getByTestId("rules-save").click();
    await expect(page.getByText("Settings saved")).toBeVisible();
    const rules = await (await page.context().request.get("/api/action-center/settings")).json();
    expect(rules.pending_verification_days).toBe(10);
    await input.fill("7");
    await page.getByTestId("rules-save").click();
  });
});

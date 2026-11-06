import { expect, test } from "@playwright/test";
import { apiLogin, creds, loginAs } from "./helpers";

test.describe("authentication", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("fp.lang", "en"));
  });

  test("anonymous visitors see the login page and wrong passwords are rejected", async ({ page }) => {
    await page.goto("/production");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await page.getByLabel("E-mail").fill(creds("viewer").email);
    await page.getByLabel("Password").fill("definitely-wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText("Invalid e-mail or password");
  });

  test("login, user menu and logout", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("E-mail").fill(creds("analyst").email);
    await page.getByLabel("Password").fill(creds("analyst").password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Overview Dashboard" })).toBeVisible();
    await expect(page.getByTestId("user-name")).toBeVisible();
    await page.getByTestId("logout").click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    // the old session no longer works
    expect((await page.context().request.get("/api/kpis")).status()).toBe(401);
  });

  test("new users must replace their temporary password", async ({ page, request }) => {
    const csrf = await apiLogin(request, "admin");
    const email = `e2e-${Date.now()}@example.com`;
    const res = await request.post("/api/users", {
      headers: { "X-CSRF-Token": csrf },
      data: { email, display_name: "E2E Neu", role: "viewer", temporary_password: "Vorlaeufiges-Kennwort-1" },
    });
    expect(res.ok()).toBeTruthy();
    await page.goto("/");
    await page.getByLabel("E-mail").fill(email);
    await page.getByLabel("Password").fill("Vorlaeufiges-Kennwort-1");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Set a new password" })).toBeVisible();
    await page.getByLabel("Current password").fill("Vorlaeufiges-Kennwort-1");
    await page.getByLabel("New password", { exact: true }).fill("Ein-Langes-Neues-Kennwort-2026");
    await page.getByLabel("Repeat new password").fill("Ein-Langes-Neues-Kennwort-2026");
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Overview Dashboard" })).toBeVisible();
    // clean up: deactivate the temporary account (users are never hard-deleted)
    const id = (await res.json()).id;
    const off = await request.patch(`/api/users/${id}`, { headers: { "X-CSRF-Token": csrf }, data: { is_active: false } });
    expect(off.ok()).toBeTruthy();
  });
});

test.describe("role-based access", () => {
  test("viewer: no import/admin navigation, guarded routes and read-only reports", async ({ page }) => {
    await loginAs(page, "viewer");
    await page.goto("/");
    const nav = page.getByRole("navigation");
    await expect(nav.getByRole("link", { name: "Overview" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Data Import" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Administration" })).toHaveCount(0);
    await page.goto("/import");
    await expect(page.getByTestId("no-access")).toBeVisible();
    await page.goto("/admin");
    await expect(page.getByTestId("no-access")).toBeVisible();
    await page.goto("/reports");
    await expect(page.getByTestId("generate-report")).toBeDisabled();
    // the API enforces the same rule
    const csrf = await apiLogin(page.context().request, "viewer");
    const r = await page.context().request.post("/api/reports", {
      headers: { "X-CSRF-Token": csrf }, data: { report_type: "daily", format: "xlsx", start_date: "2026-09-01" } });
    expect(r.status()).toBe(403);
  });

  test("analyst: import allowed, administration not", async ({ page }) => {
    await loginAs(page, "analyst");
    await page.goto("/import");
    await expect(page.getByTestId("dropzone")).toBeVisible();
    await page.goto("/admin");
    await expect(page.getByTestId("no-access")).toBeVisible();
  });
});

test.describe("administration", () => {
  test.beforeEach(async ({ page }) => { await loginAs(page, "admin"); });

  test("users, system status and audit trail", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.getByTestId("users-table")).toContainText(creds("viewer").email);
    await page.getByTestId("tab-system").click();
    await expect(page.getByTestId("system-checks")).toContainText("migrations");
    await expect(page.getByTestId("system-checks")).toContainText("evidence_quarantine");
    // v0.3.3: scanner health card with explicit check and file reconciliation
    const card = page.getByTestId("evidence-scanner-card");
    await expect(card).toBeVisible();
    await expect(card.getByTestId("evidence-retries_exhausted")).toHaveCount(1);
    await expect(card).toContainText("Retries exhausted");
    const scanner = (await (await page.context().request.get("/api/settings")).json()).system.evidence_scanner;
    if (scanner === "none") {
      await expect(card).toContainText("No malware scanner configured");
      await expect(card.getByTestId("scanner-check")).toHaveCount(0);
    } else {
      await card.getByTestId("scanner-check").click();
      await expect(page.getByText("Scanner check finished")).toBeVisible({ timeout: 30_000 });
      await expect(card.getByTestId("scanner-health")).toHaveText("Healthy");
      await expect(page.getByTestId("system-checks").locator("tr", { hasText: "evidence_scanner" }).first()).toContainText("healthy");
    }
    await card.getByTestId("evidence-reconcile").click();
    await expect(page.getByText("File reconciliation finished")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("tab-audit").click();
    await expect(page.getByTestId("audit-table")).toContainText("auth.login");
  });

  test("backup can be created; destructive actions need the password", async ({ page }) => {
    await page.goto("/admin");
    await page.getByTestId("tab-backups").click();
    await page.getByLabel("Note (optional)").fill("e2e");
    await page.getByTestId("create-backup").click();
    await expect(page.getByTestId("backup-table")).toContainText("e2e");
    // wrong password on demo reload is rejected server-side and shown in the dialog
    await page.getByRole("tab", { name: "Data tools" }).click();
    await page.getByTestId("reload-demo").click();
    await page.getByTestId("confirm-input").fill("RELOAD");
    await page.getByTestId("confirm-password").fill("wrong-password-123");
    await page.getByTestId("confirm-ok").click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Password incorrect");
  });
});

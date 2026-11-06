// Phase 1 screenshots (login, admin, role-based UI). Credentials from E2E_* env vars.
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:8080";
const out = process.argv[3] ?? "../docs/screenshots";
const pw = process.env.E2E_PASSWORD;
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });

async function ctxFor(role, lang = "en") {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((l) => localStorage.setItem("fp.lang", l), lang);
  if (role) {
    const r = await ctx.request.post(base + "/api/auth/login", { data: { email: process.env[`E2E_${role.toUpperCase()}_EMAIL`], password: pw } });
    if (!r.ok()) throw new Error(`login ${role}: ${r.status()}`);
  }
  return ctx;
}
async function shot(page, name, full = false) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: full });
  console.log("captured", name);
}

let ctx = await ctxFor(null, "de");
let page = await ctx.newPage();
await page.goto(base + "/");
await page.getByLabel("E-Mail").fill("schichtleitung@musterwerk.example");
await shot(page, "p1_login_de");
await ctx.close();

ctx = await ctxFor("admin");
page = await ctx.newPage();
await page.goto(base + "/admin");
await page.getByTestId("users-table").waitFor();
await shot(page, "p1_admin_users", true);
await page.getByTestId("tab-backups").click();
await page.getByTestId("backup-table").waitFor();
await shot(page, "p1_admin_backups", true);
await page.getByTestId("restore-backup").first().click();
await page.getByTestId("confirm-input").fill("RESTORE");
await shot(page, "p1_restore_confirm");
await page.keyboard.press("Escape");
await page.getByTestId("tab-system").click();
await page.getByTestId("system-checks").waitFor();
await shot(page, "p1_admin_system", true);
await page.getByTestId("tab-audit").click();
await page.getByTestId("audit-table").waitFor();
await shot(page, "p1_admin_audit");
await ctx.close();

ctx = await ctxFor("viewer");
page = await ctx.newPage();
await page.goto(base + "/reports");
await page.getByTestId("generate-report").waitFor();
await shot(page, "p1_viewer_reports");
await page.goto(base + "/import");
await page.getByTestId("no-access").waitFor();
await shot(page, "p1_viewer_no_access");
await ctx.close();
await browser.close();

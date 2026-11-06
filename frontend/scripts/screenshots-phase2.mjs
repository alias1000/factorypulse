// Phase 2 (Action Center) screenshots. Credentials from E2E_* env vars. Read-only except nothing is saved:
// dialogs are opened and cancelled, the anomaly form is only prefilled.
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:8080";
const out = process.argv[3] ?? "../docs/screenshots";
const pw = process.env.E2E_PASSWORD;
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });

async function ctxFor(role, lang = "en", width = 1440) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  await ctx.addInitScript((l) => localStorage.setItem("fp.lang", l), lang);
  const r = await ctx.request.post(base + "/api/auth/login", { data: { email: process.env[`E2E_${role.toUpperCase()}_EMAIL`], password: pw } });
  if (!r.ok()) throw new Error(`login ${role}: ${r.status()}`);
  return ctx;
}
async function shot(page, name, full = false) {
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: full });
  console.log("captured", name);
}
async function invId(ctx, number) {
  const r = await ctx.request.get(`${base}/api/investigations?search=${number}`);
  return (await r.json()).rows[0].id;
}

let ctx = await ctxFor("admin");
let page = await ctx.newPage();
await page.goto(base + "/actions");
await page.getByTestId("ac-kpis").waitFor();
await page.getByTestId("ac-priority").waitFor();
await shot(page, "p2_ac_overview", true);
await page.goto(base + "/actions/investigations");
await page.getByTestId("inv-table").waitFor();
await shot(page, "p2_ac_list", true);

const id4 = await invId(ctx, "INV-2026-0004");
await page.goto(`${base}/actions/investigations/${id4}`);
await page.getByTestId("inv-title").waitFor();
await shot(page, "p2_ac_detail_overview", true);
await page.getByTestId("tab-actions").click();
await shot(page, "p2_ac_detail_actions", true);
await page.getByTestId("tab-timeline").click();
await page.getByTestId("timeline").waitFor();
await shot(page, "p2_ac_detail_timeline", true);
await page.getByTestId("tab-evidence").click();
await shot(page, "p2_ac_detail_evidence");

const id1 = await invId(ctx, "INV-2026-0001");
await page.goto(`${base}/actions/investigations/${id1}`);
await page.getByTestId("tab-kpi").click();
await page.getByTestId("kpi-table").waitFor();
await shot(page, "p2_ac_detail_kpi", true);

const id3 = await invId(ctx, "INV-2026-0003");
await page.goto(`${base}/actions/investigations/${id3}`);
await page.getByTestId("to-resolved").click();
await shot(page, "p2_ac_transition_dialog");
await page.keyboard.press("Escape");

await page.goto(base + "/actions/investigations/new");
await page.getByTestId("create-form").waitFor();
await shot(page, "p2_ac_create_manual");

await page.goto(base + "/insights");
await page.getByTestId("anomaly-table").waitFor();
const rows = page.locator("[data-testid=anomaly-table] tbody tr");
const n = await rows.count();
for (let i = 0; i < n; i++) {
  if ((await rows.nth(i).getByTestId("anomaly-investigation-link").count()) === 0) { await rows.nth(i).click(); break; }
}
await page.getByTestId("create-investigation").waitFor();
await shot(page, "p2_insights_create_button");
await page.getByTestId("create-investigation").click();
await page.getByTestId("anomaly-evidence").waitFor();
await shot(page, "p2_ac_create_from_anomaly", true);

await page.goto(base + "/settings");
await page.getByTestId("rules-card").waitFor();
await page.getByTestId("rules-card").scrollIntoViewIfNeeded();
await shot(page, "p2_settings_rules");
await ctx.close();

ctx = await ctxFor("analyst", "de");
page = await ctx.newPage();
await page.goto(base + "/actions/mine");
await page.waitForSelector("[data-testid^=mine-], [data-testid=no-access]");
await shot(page, "p2_ac_my_actions_de", true);
await page.goto(base + "/actions");
await page.getByTestId("ac-kpis").waitFor();
await shot(page, "p2_ac_overview_de");
await ctx.close();

ctx = await ctxFor("viewer");
page = await ctx.newPage();
await page.goto(`${base}/actions/investigations/${id4}`);
await page.getByTestId("inv-title").waitFor();
await shot(page, "p2_ac_viewer_readonly");
await ctx.close();

ctx = await ctxFor("admin", "en", 390);
page = await ctx.newPage();
await page.goto(`${base}/actions`);
await page.getByTestId("ac-kpis").waitFor();
await shot(page, "p2_ac_mobile");
await ctx.close();
await browser.close();

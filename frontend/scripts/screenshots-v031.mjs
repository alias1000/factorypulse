// v0.3.1 screenshots: alert-worker freshness and evidence scan/quarantine states.
//   node scripts/screenshots-v031.mjs <base> <outdir> <mode: scanned|quarantine>
// Writes test evidence into the (demo) database – never run against a production system.
import { chromium } from "@playwright/test";

const [base = "http://127.0.0.1:8080", out = "../docs/screenshots", mode = "scanned"] = process.argv.slice(2);
const pw = process.env.E2E_PASSWORD;
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => localStorage.setItem("fp.lang", "en"));
const login = await ctx.request.post(base + "/api/auth/login", { data: { email: process.env.E2E_ADMIN_EMAIL, password: pw } });
const csrf = (await login.json()).csrf_token;
const page = await ctx.newPage();
const shot = async (name, full = false) => { await page.waitForTimeout(900); await page.screenshot({ path: `${out}/${name}.png`, fullPage: full }); console.log("captured", name); };

const list = await (await ctx.request.get(base + "/api/investigations?search=INV-2026-0005")).json();
const id = list.rows[0].id;
const PDF = Buffer.from("%PDF-1.4\n%evidence\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n");
const up = (name, buf) => ctx.request.post(`${base}/api/investigations/${id}/attachments`, {
  headers: { "X-CSRF-Token": csrf }, multipart: { file: { name, mimeType: "application/pdf", buffer: buf } } });

if (mode === "scanned") {
  await page.goto(base + "/actions");
  await page.getByTestId("alert-evaluation").waitFor();
  await page.getByTestId("ac-alerts").scrollIntoViewIfNeeded();
  await shot("p31_alert_worker_status");
  await up("maintenance-report.pdf", PDF);
  await up("invoice-from-email.pdf", Buffer.concat([PDF, Buffer.from("X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")]));
  await page.goto(`${base}/actions/investigations/${id}`);
  await page.getByTestId("tab-evidence").click();
  await page.locator("[data-scan=infected]").first().waitFor();
  await shot("p31_evidence_scanned");
} else {
  await up("photo-of-leak.pdf", PDF);
  await page.goto(`${base}/actions/investigations/${id}`);
  await page.getByTestId("tab-evidence").click();
  await page.locator("[data-scan=not_scanned]").first().waitFor();
  await shot("p31_evidence_quarantine");
  await page.locator("[data-testid^=release-]").first().click();
  await page.getByTestId("prompt-text").fill("Checked on an isolated workstation with the company antivirus");
  await shot("p31_evidence_release_dialog");
}
await browser.close();

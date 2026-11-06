// Capture screenshots of all pages: node scripts/screenshots.mjs [baseUrl] [outDir] [lang]
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:5173";
const out = process.argv[3] ?? "../docs/screenshots";
const lang = process.argv[4] ?? "en";
const pages = ["/", "/production", "/quality", "/downtime", "/insights", "/reports", "/import", "/settings", "/admin"];
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript((l) => localStorage.setItem("fp.lang", l), lang);
// sign in (credentials from E2E_ADMIN_EMAIL / E2E_PASSWORD)
const login = await ctx.request.post(base + "/api/auth/login", {
  data: { email: process.env.E2E_ADMIN_EMAIL, password: process.env.E2E_ADMIN_PASSWORD ?? process.env.E2E_PASSWORD },
});
if (!login.ok()) throw new Error(`login failed: ${login.status()}`);
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));
for (const p of pages) {
  await page.goto(base + p, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const name = p === "/" ? "overview" : p.slice(1);
  await page.screenshot({ path: `${out}/${name}${lang === "de" ? "_de" : ""}.png`, fullPage: true });
  console.log("captured", name);
}
if (errors.length) console.log("console errors:", errors);
await browser.close();

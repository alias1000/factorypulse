// Browser smoke test against an installed stack (HTTPS LAN mode). Read-mostly: posts one comment.
//   node scripts/browser-smoke-lan.mjs https://factorypulse.werk.local <email> <password> [screenshot-dir]
import { chromium } from "@playwright/test";

const [base, email, password, out = "."] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
// the internal CA is not in Chromium's trust store in CI; certificate validation is checked separately
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => localStorage.setItem("fp.lang", "de"));
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const step = (m) => console.log("✓", m);

await page.goto(base + "/");
await page.getByLabel("E-Mail").fill(email);
await page.getByLabel("Passwort").fill(password);
await page.getByRole("button", { name: "Anmelden" }).click();
await page.getByTestId("user-name").waitFor();
step("login über HTTPS (Secure-Cookie wird vom Browser zurückgesendet)");
const cookies = await ctx.cookies();
const sess = cookies.find((c) => c.name === "fp_session");
if (!sess?.secure || !sess.httpOnly || sess.sameSite !== "Strict") throw new Error("cookie flags " + JSON.stringify(sess));
step("Cookie-Flags Secure/HttpOnly/SameSite=Strict");

await page.goto(base + "/actions");
await page.getByTestId("ac-kpis").waitFor();
await page.getByTestId("alert-evaluation").waitFor();
await page.screenshot({ path: `${out}/lan_action_center.png`, fullPage: true });
step("Maßnahmenzentrum mit zeitgesteuerter Regelauswertung: " + (await page.getByTestId("alert-evaluation").getAttribute("data-state")));

await page.goto(base + "/actions/investigations");
await page.getByTestId("inv-search").fill("pruefstrasse");
await page.locator("[data-testid^=inv-row-]").first().click();
await page.getByTestId("comment-input").fill("Browser-Test über HTTPS (CSRF-Token im Header)");
await page.getByTestId("comment-post").click();
await page.getByTestId("comments").getByText("Browser-Test über HTTPS").waitFor();
step("Kommentar gespeichert (CSRF-geschützter POST durch den Reverse Proxy)");
await page.getByTestId("tab-evidence").click();
await page.locator("[data-scan=infected]").waitFor();
await page.locator("[data-scan=clean]").first().waitFor();
await page.screenshot({ path: `${out}/lan_evidence_scan.png`, fullPage: true });
step("Nachweise: ClamAV-Ergebnis sauber/infiziert sichtbar");
if (errors.length) throw new Error("page errors: " + errors.join("; "));
await browser.close();
console.log("browser smoke test passed");

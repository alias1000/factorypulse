// Screenshots of interactive flows (import validation, anomaly detail).
import { chromium } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const base = process.argv[2] ?? "http://127.0.0.1:5173";
const out = process.argv[3] ?? path.join(here, "../../docs/screenshots");
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.addInitScript(() => localStorage.setItem("fp.lang", "en"));
const login = await page.context().request.post(base + "/api/auth/login", {
  data: { email: process.env.E2E_ADMIN_EMAIL, password: process.env.E2E_ADMIN_PASSWORD ?? process.env.E2E_PASSWORD },
});
if (!login.ok()) throw new Error(`login failed: ${login.status()}`);

await page.goto(base + "/import");
await page.getByTestId("file-input").setInputFiles(path.join(here, "../../sample_data/sample_import_with_errors.csv"));
await page.getByTestId("validate").click();
await page.getByTestId("validation-summary").waitFor();
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/import_validation.png`, fullPage: true });

await page.goto(base + "/insights");
await page.getByRole("button", { name: "Full range" }).click();
await page.getByTestId("ground-truth").waitFor();
await page.getByTestId("anomaly-table").getByRole("row").nth(2).click();
await page.getByRole("dialog").waitFor();
await page.waitForTimeout(1200);
await page.screenshot({ path: `${out}/insights_detail.png` });
await browser.close();
console.log("done");

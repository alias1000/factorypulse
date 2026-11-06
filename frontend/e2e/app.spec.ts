import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loginAs } from "./helpers";

const SAMPLES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../sample_data");

async function setLang(page: Page, lang: "en" | "de") {
  await page.addInitScript((l) => localStorage.setItem("fp.lang", l), lang);
}

test.beforeEach(async ({ page }) => {
  await loginAs(page, "admin");
  page.on("pageerror", (e) => { throw e; });
});

test("all navigation links render a working page", async ({ page }) => {
  await page.goto("/");
  const nav = [
    ["Overview", "Overview Dashboard"], ["Production Analytics", "Production Analytics"],
    ["Quality Control", "Quality Control"], ["Downtime Analysis", "Downtime Analysis"],
    ["AI Insights", "AI Insights"], ["Action Center", "Action Center"], ["My Actions", "My Actions"],
    ["Reports", "Reports"], ["Data Import", "Data Import"], ["Settings", "Settings"],
    ["Administration", "Administration"],
  ];
  for (const [link, heading] of nav) {
    await page.getByRole("navigation").getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }
});

test("overview KPIs load and react to the line filter", async ({ page }) => {
  await page.goto("/");
  const total = page.getByTestId("kpi-total_production-value");
  await expect(total).not.toHaveText("N/A");
  await expect(page.getByTestId("kpi-oee-value")).toContainText("%");
  const before = await total.textContent();
  await page.getByTestId("filter-line").click();
  await page.getByRole("option", { name: /^L1/ }).click();
  await expect(total).not.toHaveText(before!);
  await page.getByTestId("filter-reset").click();
  await expect(total).toHaveText(before!);
});

test("German interface", async ({ page }) => {
  await setLang(page, "de");
  await page.goto("/quality");
  await expect(page.getByRole("heading", { level: 1, name: "Qualitätskontrolle" })).toBeVisible();
  await page.getByRole("button", { name: "en", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Quality Control" })).toBeVisible();
});

test("valid German CSV import appears in dashboards and can be reverted", async ({ page }) => {
  await page.goto("/import");
  await page.getByTestId("file-input").setInputFiles(path.join(SAMPLES, "sample_import_valid_de.csv"));
  await expect(page.getByText("Column mapping")).toBeVisible();
  await page.getByTestId("validate").click();
  const summary = page.getByTestId("validation-summary");
  await expect(summary).toBeVisible();
  await expect(summary.getByText("Rows with errors").locator("..")).toContainText("0");
  await page.getByTestId("commit").click();
  await expect(page.getByTestId("import-success")).toContainText("192 rows imported");

  // imported week is visible on the overview (filters were extended to the full data range)
  await page.getByRole("link", { name: "Open dashboard" }).click();
  await page.getByTestId("filter-start").fill("2026-10-01");
  await page.getByTestId("filter-end").fill("2026-10-07");
  await expect(page.getByTestId("kpi-total_production-value")).not.toHaveText("N/A");

  // revert
  await page.goto("/import");
  const history = page.getByTestId("import-history");
  await history.getByRole("button", { name: "Revert" }).first().click();
  await page.getByTestId("confirm-input").fill("REVERT");
  await page.getByTestId("confirm-ok").click();
  await expect(history.getByText("reverted").first()).toBeVisible();
});

test("invalid file shows understandable errors and blocks import", async ({ page }) => {
  await page.goto("/import");
  await page.getByTestId("file-input").setInputFiles(path.join(SAMPLES, "sample_import_with_errors.csv"));
  await page.getByTestId("validate").click();
  const issues = page.getByTestId("issue-table");
  await expect(issues).toContainText("Negative values are not allowed");
  await expect(issues).toContainText("Good + defective ≠ actual quantity");
  await expect(issues).toContainText("Duplicate row in file");
  await expect(page.getByTestId("commit")).toBeDisabled();
  await page.getByTestId("valid-only").check();
  await expect(page.getByTestId("commit")).toBeEnabled();
});

test("anomaly detection finds the injected anomalies", async ({ page }) => {
  await page.goto("/insights");
  await page.getByRole("button", { name: "Full range" }).click();
  const gt = page.getByTestId("ground-truth");
  await expect(gt).toContainText("4/4 detected");
  await page.getByTestId("anomaly-table").getByRole("row").nth(1).click();
  await expect(page.getByRole("dialog")).toContainText("cause has not been determined");
});

test("PDF and Excel reports download", async ({ page }) => {
  await page.goto("/reports");
  await page.getByTestId("report-type-monthly").click();
  let dl = page.waitForEvent("download");
  await page.getByTestId("generate-report").click();
  expect((await dl).suggestedFilename()).toMatch(/monthly_.*\.pdf$/);
  await page.getByTestId("report-type-downtime").click();
  await page.getByTestId("format-xlsx").click();
  dl = page.waitForEvent("download");
  await page.getByTestId("generate-report").click();
  expect((await dl).suggestedFilename()).toMatch(/downtime_.*\.xlsx$/);
  await expect(page.getByTestId("report-history")).toContainText("Downtime Report");
});

test("downtime page highlights the machine with unusual downtime", async ({ page }) => {
  await page.goto("/downtime");
  await page.getByRole("button", { name: "Full range" }).click();
  await expect(page.getByTestId("high-downtime-alert")).toContainText("M-10");
  await page.getByTestId("downtime-search").fill("mechanical");
  await expect(page.getByRole("cell", { name: "Mechanical failure" }).first()).toBeVisible();
});

test("production table export works", async ({ page }) => {
  await page.goto("/production");
  await page.getByTestId("dim-machine").click();
  await expect(page.getByTestId("production-table")).toContainText("M-01");
  const dl = page.waitForEvent("download");
  await page.getByTestId("export-xlsx").click();
  expect((await dl).suggestedFilename()).toMatch(/production_by_machine.*\.xlsx$/);
});

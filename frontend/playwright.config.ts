import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against a running stack (frontend + API + database).
 * E2E_BASE_URL defaults to the Vite dev server; for Docker Compose use http://localhost:8080.
 * WARNING: the suite reloads the synthetic demo dataset (all data is replaced).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
    acceptDownloads: true,
    trace: "retain-on-failure",
  },
});

import type { APIRequestContext, Page } from "@playwright/test";

/**
 * Credentials come from the environment (never hardcoded):
 *   E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD, E2E_ANALYST_EMAIL / ..., E2E_VIEWER_EMAIL / ...
 * Create the accounts once with `python -m app.cli create-user ... --no-force-change`.
 */
export type Role = "admin" | "analyst" | "viewer";

export function creds(role: Role) {
  const email = process.env[`E2E_${role.toUpperCase()}_EMAIL`];
  const password = process.env[`E2E_${role.toUpperCase()}_PASSWORD`] ?? process.env.E2E_PASSWORD;
  if (!email || !password) throw new Error(`Set E2E_${role.toUpperCase()}_EMAIL and a password env var for E2E tests`);
  return { email, password };
}

/** Log in through the API; the session cookie is shared with the page's browser context. */
export async function apiLogin(request: APIRequestContext, role: Role): Promise<string> {
  const res = await request.post("/api/auth/login", { data: creds(role) });
  if (!res.ok()) throw new Error(`login failed for ${role}: ${res.status()} ${await res.text()}`);
  return (await res.json()).csrf_token as string;
}

export async function loginAs(page: Page, role: Role, lang: "en" | "de" = "en") {
  await page.addInitScript((l) => localStorage.setItem("fp.lang", l), lang);
  await apiLogin(page.context().request, role);
}

import { request, type FullConfig } from "@playwright/test";
import { apiLogin, creds } from "./helpers";

export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL!;
  const ctx = await request.newContext({ baseURL });
  const csrf = await apiLogin(ctx, "admin");
  const res = await ctx.post("/api/admin/demo/load", {
    headers: { "X-CSRF-Token": csrf },
    data: { password: creds("admin").password, confirm: "RELOAD" },
  });
  if (!res.ok()) throw new Error(`Demo reload failed: ${res.status()} ${await res.text()}`);
  await ctx.dispose();
}

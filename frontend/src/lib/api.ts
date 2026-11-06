import type { Filters } from "./filters";

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: unknown;
  constructor(status: number, message: string, details?: unknown, code?: string) {
    super(message);
    this.status = status;
    this.details = details;
    this.code = code;
  }
}

/** CSRF token of the current session (set by the AuthProvider after /api/auth/me or login). */
let csrfToken: string | null = null;
export const setCsrfToken = (t: string | null) => { csrfToken = t; };

/** Fired when the API answers 401 – the AuthProvider then shows the login screen. */
export const UNAUTHORIZED_EVENT = "fp:unauthorized";

async function parseError(res: Response): Promise<ApiError> {
  let message = `${res.status} ${res.statusText}`;
  let details: unknown;
  let code: string | undefined;
  try {
    const body = await res.json();
    details = body;
    code = typeof body?.code === "string" ? body.code : undefined;
    const d = body?.detail;
    if (typeof d === "string") message = d;
    else if (d && typeof d === "object" && "message" in d) {
      message = String((d as { message: string }).message);
      details = d;
    }
    if (Array.isArray(body?.errors) && body.errors.length) {
      message = body.errors.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`).join("; ");
    }
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401 && !res.url.endsWith("/api/auth/login")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  return new ApiError(res.status, message, details, code);
}

function withCsrf(init?: RequestInit): RequestInit {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (method !== "GET" && method !== "HEAD" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  return { ...init, headers, credentials: "same-origin" };
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, withCsrf(init));
  } catch {
    throw new ApiError(0, "Backend not reachable. Is the API server running?");
  }
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

export const postJson = <T,>(path: string, body: unknown, method = "POST") =>
  api<T>(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export function filterParams(f: Filters, extra?: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  if (f.startDate) p.set("start_date", f.startDate);
  if (f.endDate) p.set("end_date", f.endDate);
  if (f.lineId) p.set("line_id", String(f.lineId));
  if (f.machineId) p.set("machine_id", String(f.machineId));
  if (f.shiftId) p.set("shift_id", String(f.shiftId));
  if (f.productId) p.set("product_id", String(f.productId));
  for (const [k, v] of Object.entries(extra ?? {})) if (v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function filterBody(f: Filters) {
  return {
    start_date: f.startDate || null,
    end_date: f.endDate || null,
    line_ids: f.lineId ? [f.lineId] : [],
    machine_ids: f.machineId ? [f.machineId] : [],
    shift_ids: f.shiftId ? [f.shiftId] : [],
    product_ids: f.productId ? [f.productId] : [],
  };
}

/** Download a file from the API through a temporary object URL (keeps errors visible). */
export async function downloadFile(path: string, fallbackName: string) {
  const res = await fetch(path, withCsrf());
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = m?.[1] ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

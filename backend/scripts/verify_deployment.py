#!/usr/bin/env python3
"""Post-deployment verification for FactoryPulse AI (standard library only).

    FP_VERIFY_EMAIL=admin@firma.de FP_VERIFY_PASSWORD=... python3 scripts/verify_deployment.py https://factorypulse.firma.de

Without credentials only the unauthenticated checks run. Exit code 0 = all checks passed, 1 = failures.
The script creates and deletes one small Excel report (if the account may generate reports) and
changes nothing else.
"""
from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' – ' + detail if detail else ''}")
    return ok


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf: str | None = None

    def req(self, method: str, path: str, body: dict | None = None, csrf: bool = True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(r, timeout=60) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except urllib.error.URLError as e:
            return 0, {}, str(e.reason).encode()


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FP_VERIFY_URL", "http://localhost:8080")
    print(f"Verifying {base}\n")
    c = Client(base)

    st, _, body = c.req("GET", "/api/health")
    check("API liveness /api/health", st == 200, body.decode()[:80])
    st, _, body = c.req("GET", "/api/health/ready")
    ready = json.loads(body or b"{}") if st in (200, 503) else {}
    check("API readiness /api/health/ready", st == 200 and ready.get("status") == "ready",
          ", ".join(f"{k}={v}" for k, v in ready.get("checks", {}).items()))

    st, headers, body = c.req("GET", "/")
    h = {k.lower(): v for k, v in headers.items()}
    check("Frontend serves index.html", st == 200 and b"<div id=\"root\">" in body)
    check("Security headers (CSP, X-Frame-Options, nosniff)",
          "content-security-policy" in h and h.get("x-frame-options") == "DENY" and h.get("x-content-type-options") == "nosniff")
    st, _, _ = c.req("GET", "/settings")
    check("SPA deep link /settings", st == 200)

    st, _, _ = c.req("GET", "/api/kpis")
    check("Unauthenticated API access is denied", st == 401, f"HTTP {st}")
    st, _, _ = c.req("POST", "/api/admin/backups", {}, csrf=False)
    check("Unauthenticated admin access is denied", st == 401, f"HTTP {st}")
    st, _, _ = c.req("GET", "/api/investigations")
    check("Unauthenticated Action Center access is denied", st == 401, f"HTTP {st}")

    email = os.environ.get("FP_VERIFY_EMAIL")
    password = os.environ.get("FP_VERIFY_PASSWORD")
    if email and not password and sys.stdin.isatty():
        password = getpass.getpass(f"Password for {email}: ")
    if email and password:
        st, headers, body = c.req("POST", "/api/auth/login", {"email": email, "password": password})
        if check("Login", st == 200, f"HTTP {st}"):
            me = json.loads(body)
            c.csrf = me["csrf_token"]
            cookie = next((v for k, v in headers.items() if k.lower() == "set-cookie"), "").lower()
            secure_needed = base.startswith("https://")
            check("Session cookie flags", "httponly" in cookie and "samesite=strict" in cookie
                  and ("secure" in cookie or not secure_needed),
                  "HttpOnly, SameSite=Strict" + (", Secure" if "secure" in cookie else " (no Secure flag: plain HTTP)"))
            perms = set(me["permissions"])
            if me["user"]["must_change_password"]:
                check("Account usable", False, "password change pending – log in via the UI first")
            else:
                st, _, body = c.req("GET", "/api/meta")
                meta = json.loads(body) if st == 200 else {}
                check("Master data /api/meta", st == 200,
                      f"{meta.get('company', {}).get('name')} · {meta.get('data_range', {}).get('record_count')} records")
                st, _, body = c.req("GET", "/api/kpis")
                check("KPI engine /api/kpis", st == 200 and "oee" in json.loads(body or b"{}"))
                if "action.read" in perms:
                    st, _, body = c.req("GET", "/api/action-center/summary")
                    k = json.loads(body).get("kpis", {}) if st == 200 else {}
                    check("Action Center /api/action-center/summary", st == 200 and "open_total" in k,
                          f"{k.get('open_total')} open · {k.get('overdue_actions')} overdue actions")
                st, _, _ = c.req("POST", "/api/anomalies/detect", {}, csrf=False)
                check("CSRF protection active", st == 403, f"HTTP {st}")
                if "report.generate" in perms and meta.get("data_range", {}).get("end"):
                    st, _, body = c.req("POST", "/api/reports", {"report_type": "daily", "format": "xlsx",
                                                                 "start_date": meta["data_range"]["end"]})
                    if check("Report generation", st == 200, f"HTTP {st}"):
                        rid = json.loads(body)["id"]
                        st, _, data = c.req("GET", f"/api/reports/{rid}/download")
                        check("Report download", st == 200 and data[:2] == b"PK", f"{len(data)} bytes")
                        if "report.delete" in perms:
                            c.req("DELETE", f"/api/reports/{rid}")
                if "system.read" in perms:
                    st, _, body = c.req("GET", "/api/admin/system")
                    if st == 200:
                        for ch in json.loads(body)["checks"]:
                            if ch["status"] != "ok":
                                print(f"       note: {ch['name']} = {ch['status']} ({ch['detail']})")
                    # v0.3.3: actual scanner availability from the persisted explicit health check (read-only)
                    st, _, body = c.req("GET", "/api/admin/evidence/status")
                    if st == 200:
                        ev = json.loads(body)
                        h, q, sto = ev.get("health") or {}, ev["quarantine"], ev["storage"]
                        if ev["scanner"] == "none":
                            print("       note: no malware scanner configured – evidence stays quarantined until released")
                        else:
                            check("Evidence scanner health (explicit check)",
                                  h.get("status") == "healthy" and not ev.get("health_stale"),
                                  f"{ev['scanner_label']}: {h.get('status', 'never checked')}"
                                  + (f" since {h.get('unavailable_since')} – {h.get('last_error')}" if h.get("status") == "unavailable" else "")
                                  + (" (stale – is the worker running?)" if ev.get("health_stale") else ""))
                        check("Evidence file storage consistent", not (sto["ops_failed"] or sto["misplaced"] or sto["missing"]),
                              ", ".join(f"{k} {v}" for k, v in sto.items()))
                        print(f"       quarantine: pending {q['pending']}, retrying {q['retrying']}, retries exhausted "
                              f"{q['retries_exhausted']}, not scanned {q['not_scanned']}, infected {q['infected']}")
            st, _, _ = c.req("POST", "/api/auth/logout", {})
            st2, _, _ = c.req("GET", "/api/kpis")
            check("Logout invalidates the session", st == 200 and st2 == 401)
    else:
        print("\n(no FP_VERIFY_EMAIL/FP_VERIFY_PASSWORD – authenticated checks skipped)")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

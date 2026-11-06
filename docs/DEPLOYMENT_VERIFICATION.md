# Deployment Verification – v0.3.1

Date: 2026-10-18. Everything below was executed, not assumed. Raw outputs are reproduced verbatim.

## Environment and deviations (read first)

| Item | Production default | Used for this verification | Why |
|---|---|---|---|
| Docker Engine / Compose | ≥ 24 / ≥ 2.24 | Docker 29.8.2, Compose v5.5.1 | – |
| Base images | `python:3.12-slim`, `node:22-alpine`, `nginx:1.27-alpine`, `postgres:16-alpine`, `clamav/clamav:1.4` | **Stand-ins built locally** from an Ubuntu 24.04 `debootstrap` root file system: Python 3.12, Node 22.22, nginx 1.24, PostgreSQL 16.15 (entrypoint emulating `POSTGRES_USER/PASSWORD/DB`), ClamAV 1.5.4 | Docker Hub, `public.ecr.aws` and `mirror.gcr.io` are blocked (HTTP 403) in the verification sandbox. The project's own Dockerfiles and compose files were used unchanged; only the base-image variables (`FP_*_IMAGE`) and `FP_BUILD_PGDG=false` (apt.postgresql.org is blocked too) were set – exactly the mechanism documented for air-gapped plants. |
| ClamAV signatures | official database via freshclam | one **local test signature** for the EICAR string | `database.clamav.net` is blocked. Detection of real malware therefore was **not** tested; the scanner integration (protocol, quarantine, retries) was. |
| TLS certificate | company PKI | internal CA from `ops/generate-tls-cert.sh` (ECDSA P-256), host `factorypulse.werk.local` → 127.0.0.1, ports 8443/8081 | no company PKI in the sandbox |

**Not verified:** the official upstream images themselves, nginx 1.27 specifically (configuration validated with
nginx 1.24 – the TLS config uses only directives available in both), real-world malware detection, browser trust of the
internal CA via Windows Group Policy, and operation over several days (log rotation, certificate renewal).

## 1. Fresh installation in HTTPS LAN mode – 51/51 checks

`docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build` on empty volumes with
`FP_ENVIRONMENT=production`, `FP_EVIDENCE_SCANNER=clamd`, `COMPOSE_PROFILES=clamav`. The test client
(`requests`, certificate validated against `tls/ca.crt`) then exercised migrations, login, roles, imports,
Action Center, scanner, worker, reports, backup and restore:

```
Verifying https://factorypulse.werk.local:8443

[PASS] API reachable through the reverse proxy – {"status":"ok","version":"0.3.1"}
[PASS] schema at head (migration 0004)
[PASS] new production database contains NO synthetic data – records,investigations,synthetic companies,demo users = 0,0,0,0
[PASS] startup log states that demo seeding is disabled
[PASS] plain HTTP redirects to the public HTTPS URL – 301 → https://factorypulse.werk.local:8443/settings
[PASS] HSTS header on HTTPS
[PASS] TLSv1.1 is rejected
[PASS] certificate validates against the internal CA (hostname + chain) – TLSv1.3 TLS_AES_256_GCM_SHA384
[PASS] bootstrap administrator can sign in – HTTP 200
[PASS] session cookie: HttpOnly, SameSite=Strict, Secure – fp_session=Oul9VJpy3zLfN0jDVyHQ3OOwfx59AxMav7qUf_VRw7Y; HttpOnly; Max-Age=43200; Path=/api; SameSite=strict; Secure
[PASS] temporary password: API blocked until changed
[PASS] password change – {"user":{"id":1,"email":"admin@werk.example","display_name":"Administrator","role":"admin","is_activ
[PASS] CSRF: state-changing request without token is rejected
[PASS] CSRF: forged token is rejected
[PASS] anonymous API access is rejected
[PASS] browser cookie is marked secure
[PASS] audit log records the real client IP (X-Forwarded-For spoofing ignored) – logged IP 172.18.0.1
[PASS] admin creates analyst account – {"id":2,"email":"analyst@werk.example","display_name":"Analyst Test","role":"analyst","is_active":true,"must_change_pass
[PASS] analyst signs in with own password
[PASS] admin creates viewer account – {"id":3,"email":"viewer@werk.example","display_name":"Viewer Test","role":"viewer","is_active":true,"must_change_passwor
[PASS] viewer signs in with own password
[PASS] viewer: imports forbidden
[PASS] viewer: cannot create investigations
[PASS] analyst: user administration forbidden
[PASS] analyst: backups forbidden
[PASS] file import sample_import_valid_de.csv – 192 valid rows, commit HTTP 200: {"job":{"id":1,"original_filename":"sample_import_valid_de.csv","file_type":"csv","file_size":19056,"status":"committed"
[PASS] file import sample_import_valid_en.xlsx – 192 valid rows, commit HTTP 200: {"job":{"id":2,"original_filename":"sample_import_valid_en.xlsx","file_type":"xlsx","file_size":17187,"status":"committe
[PASS] invalid import rows are reported – 9 rows with errors
[PASS] imported production records stored – 384 records
[PASS] KPI engine on imported data – OEE 0.7386089688599817
[PASS] analyst creates investigation – {"id":1,"number":"INV-2026-0001","title":"Spindelüberhitzung an Prüfstraße","category":"equipment_fa
[PASS] German search (transliteration)
[PASS] corrective action created – {"id":1,"code":"ACT-00001","title":"Lager prüfen","description":"","owner":null,
[PASS] evidence scanned clean by ClamAV container – clean via clamd@clamav:3310
[PASS] viewer downloads clean evidence
[PASS] EICAR evidence detected and quarantined – infected
[PASS] infected evidence not downloadable (even admin)
[PASS] infected file physically in /data/evidence/quarantine
[PASS] worker container evaluated the alert rules (overdue action raised without any dashboard trigger) – state=ok evaluated_at=2026-10-18T15:39:29.778432+00:00
[PASS] job history recorded by the worker – 4 runs; worker=1e49bf77342e:1
[PASS] dashboard requests do not change alerts
[PASS] monthly report (PDF, German) – 101721 bytes
[PASS] monthly report (XLSX, German) – 24111 bytes
[PASS] readiness: ready – database=ok, migrations=ok, storage:uploads=ok, storage:reports=ok, storage:evidence=ok, storage:evidence/quarantine=ok, storage:backups=ok, disk_space=ok, pdf_engine=ok, backup_tools=ok, last_backup=warn, alert_worker=ok, evidence_scanner=ok, evidence_infected=warn, administrator=ok, secure_cookies=ok, demo_tools=ok
[PASS] readiness: alert worker ok – last run 2026-10-18T15:39:29.778432+00:00 (every 60 s)
[PASS] readiness: evidence scanner configured – clamd scanner configured
[PASS] readiness: backup tools match server – PostgreSQL 16; pg_dump 16, pg_restore 16
[PASS] backup created (pg_dump inside the backend container) – {"format_version":1,"app_version":"0.3.1","build":"dev","alembic_revision":"0004","created_at":"2026-10-18T15:39:37.311558+00:00","created_by":"admin@
[PASS] restore – {"restored":"fp-backup-20261018-153937-26c968","backup_created_at":"2026-10-18T15:39:37.311558+00:00","safety_backup":"fp-backup-20261018-153937-654da
[PASS] restore: later investigation gone, earlier data intact
[PASS] restore: evidence files (incl. quarantine) restored

51/51 checks passed
```

The `last_backup=warn` and `evidence_infected=warn` entries in the readiness line are expected (no backup yet at that
moment; the EICAR test file).

## 2. Shipped deployment check `ops/verify.sh` – 17/17

```
[PASS] API liveness /api/health – {"status":"ok","version":"0.3.1"}
[PASS] API readiness /api/health/ready – database=ok, migrations=ok, storage:uploads=ok, storage:reports=ok, storage:evidence=ok, storage:evidence/quarantine=ok, storage:backups=ok, disk_space=ok, pdf_engine=ok, backup_tools=ok, last_backup=ok, alert_worker=ok, evidence_scanner=ok, evidence_infected=warn, administrator=ok, secure_cookies=ok, demo_tools=ok
[PASS] Frontend serves index.html
[PASS] Security headers (CSP, X-Frame-Options, nosniff)
[PASS] SPA deep link /settings
[PASS] Unauthenticated API access is denied – HTTP 401
[PASS] Unauthenticated admin access is denied – HTTP 401
[PASS] Unauthenticated Action Center access is denied – HTTP 401
[PASS] Login – HTTP 200
[PASS] Session cookie flags – HttpOnly, SameSite=Strict, Secure
[PASS] Master data /api/meta – My Manufacturing Company · 384 records
[PASS] KPI engine /api/kpis
[PASS] Action Center /api/action-center/summary – 1 open · 1 overdue actions
[PASS] CSRF protection active – HTTP 403
[PASS] Report generation – HTTP 200
[PASS] Report download – 19866 bytes
       note: evidence_infected = warn (1 evidence file(s) flagged as infected (quarantined))
[PASS] Logout invalidates the session

17/17 checks passed
```

## 3. Browser over HTTPS (Chromium, `frontend/scripts/browser-smoke-lan.mjs`)

```
✓ login über HTTPS (Secure-Cookie wird vom Browser zurückgesendet)
✓ Cookie-Flags Secure/HttpOnly/SameSite=Strict
✓ Maßnahmenzentrum mit zeitgesteuerter Regelauswertung: ok
✓ Kommentar gespeichert (CSRF-geschützter POST durch den Reverse Proxy)
✓ Nachweise: ClamAV-Ergebnis sauber/infiziert sichtbar
browser smoke test passed
```

Screenshots: `docs/screenshots/p31_lan_https_action_center_de.png`, `p31_lan_https_evidence_clamav_de.png`.

## 4. Three concurrent workers (`--scale worker=3`, 150 s)

```
worker (container)  status     runs
1e49bf77342e:1      succeeded  3
8388d040d8f7:1      succeeded  3
8de4d4d85ddd:1      skipped    1      ← advisory lock held by another worker
8de4d4d85ddd:1      succeeded  2
duplicate open alerts (rule, subject): none
```

## 5. Upgrade of an existing v0.3.0 installation (existing records preserved)

The v0.3.0 release (git tag `v0.3.0`) was started with Docker on empty volumes. As v0.3.0 did by default, it seeded the
synthetic demo data (the finding fixed in this release). An administrator was created with the CLI, and one investigation
plus one evidence file were added through the API. The v0.3.1 images were then started on the **same volumes**:

```
before (v0.3.0): records=2460 investigations=9 attachments=1 audit=8 users=4 rev=0003
after  (v0.3.1): records=2460 investigations=9 attachments=1 audit=9 users=4 rev=0004
legacy evidence: vor-upgrade.pdf | not_scanned (after migration) → clean (worker, clamd@clamav:3310)
search_text backfilled for all investigations (0 rows NULL)
```

The extra audit row is the scan event. While v0.3.0 was being set up, a related defect was found and fixed: the demo
placeholder accounts prevented the `FP_INITIAL_ADMIN_*` bootstrap on seeded systems.

## 6. Plain-HTTP mode

`docker compose up -d` (single workstation, `http://localhost:8090`). The stack starts, migrations run and the new
production database contains no synthetic data. A non-browser HTTP client cannot keep the session there, because the
cookie is `Secure`. That is intended: browsers treat `http://localhost` as a secure context, and every other machine
must use the HTTPS mode. The browser login on `http://localhost` was exercised by the E2E suite (native nginx with the
same `http.conf`).

## 7. Migration 0004 on a copy of the development database

`upgrade 0003→0004` → `alembic check` (no drift) → `downgrade 0004→0003` → insert a legacy attachment → `upgrade` →
the legacy attachment is `not_scanned`. Investigations, production records and the audit log were unchanged.

## 8. v0.3.3 – scanner outage and recovery on the running development stack

Development stack (PostgreSQL 16, API, separate worker with `FP_ALERT_INTERVAL_SECONDS=60`, nginx, ClamAV 1.5.4 `clamd`
with the local EICAR test signature). Synthetic demo data only. The script stopped `clamd`, uploaded a file, waited two
worker cycles, started `clamd` again and waited for the next cycle:

```
01:38:28 before: evidence_scanner ok – clamd@127.0.0.1:13310 healthy (ClamAV 1.5.4)
01:38:30 clamd stopped
01:38:30 manual check: unavailable – scanner unreachable: ConnectionRefusedError: [Errno 111] Connection refused
01:38:30 upload during outage: pending, quarantined
01:39:35 after worker cycle 1: warn – UNAVAILABLE since 01:38:30 (3 consecutive failure(s))
01:40:40 after worker cycle 2: warn – UNAVAILABLE since 01:38:30 (4 consecutive failure(s))
01:40:40 quarantine: pending 1, retrying 0, retries exhausted 0 (max 5 attempts)
01:40:48 clamd started
01:41:39 after recovery: ok – healthy | file: clean, not quarantined
01:41:39 evidence_storage ok – ops pending 0, ops failed 0, misplaced 0, missing 0, orphaned 0
```

Migration `0005` on a copy of the development database: one attachment was set to the v0.3.2 outage state (`failed`,
5 attempts, `scanner unreachable: …`) → `upgrade 0004→0005` → that file is `pending`, 0 attempts and still quarantined,
and the infected file is unchanged → `alembic check` (no drift) → `downgrade 0005→0004` → `upgrade` → `alembic check`.
The development database itself was then upgraded in place; its data was preserved.

`scripts/verify_deployment.py` against the development stack: 19/19 checks, including the new
"Evidence scanner health (explicit check)" and "Evidence file storage consistent" checks.

The Docker images were **not** rebuilt and re-run for v0.3.3. Sections 1–7 describe the v0.3.1 Docker verification.

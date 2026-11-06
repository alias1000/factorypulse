# Changelog

## 0.3.3 – Evidence scanner reliability (2026-10-19)

Fixes two reliability findings of the independent review. No new business features (Financial Impact has not been
started). Migration `0005` is additive and preserves existing data.

### Issue 1 – the scanner outage warning disappeared on its own
**Cause.** The readiness check `evidence_scanner_reachable` was derived from the *last scan run*: "every scan of this run
failed". When the quarantined files had used up `FP_EVIDENCE_SCAN_MAX_ATTEMPTS` (the outage itself consumed the attempts),
the next run processed zero files, so "all scans failed" was false and the warning vanished while the scanner was still
missing. "No files pending" was mistaken for "scanner healthy".

**Fix.**
- **Explicit, persisted scanner health check** (`evidence_scan.check_health`, new job `evidence.health`, every worker cycle):
  - clamd: `PING` → `PONG`, `VERSION`, scan of a clean sample (must be `OK`) and of the harmless EICAR test string (must be
    `FOUND`).
  - command: scan of a clean and an EICAR test file (exit 0 / exit 1); the test files are removed afterwards.
  - `FP_EVIDENCE_SCANNER_SELFTEST_EICAR=false` skips the EICAR part.
  - The result is stored in the new table `scanner_health`: status `healthy | unavailable | unknown`, `unavailable_since`,
    consecutive failures, last error, last success. State changes are logged and audited
    (`evidence.scanner_unavailable`, `evidence.scanner_recovered`).
- **Only a successful health check clears an outage.** A scan run, with or without work, never sets the scanner healthy.
- **A scanner outage no longer consumes a file's attempts.** Scan results are now `clean | infected | error | unavailable`:
  - `unavailable` (connection refused, DNS, command missing or not executable, connection dropped without an answer): the file stays
    `pending` and keeps its attempt count, and the outage is recorded.
  - `error` (the scanner answered but could not scan *this* file, e.g. exit code 2, size limit, scan timeout): `failed`, attempt +1, as before.
- **While the scanner is unavailable** the scan job is skipped (details `skipped_scanner_unavailable: true`), uploads are not
  scanned, and manual rescans return 503 `scanner_unavailable`. All files stay quarantined.
- **Readiness** (`/api/health/ready`, Administration → System):
  - `evidence_scanner` is now based on the persisted health. It is `ok` only after a fresh successful check. It warns
    "UNAVAILABLE since …, n consecutive failure(s): error", warns before the first check, and warns if the last check is
    older than 3 intervals.
  - `evidence_scanner_reachable` was removed.
  - New `evidence_quarantine`: pending / retrying / **retries exhausted** / not scanned. It warns while files have
    exhausted retries, or while files wait during an outage.
  - New `evidence_storage` (see Issue 2).
- **Administration → System → "Evidence malware scanner"**:
  - Shows health, outage start, failure count, last error, quarantine and storage counts.
  - *Check scanner now* runs the health check. On success it scans the backlog immediately.
  - *Reconcile files* runs the file reconciliation.
  - New endpoints: `GET /api/admin/evidence/status`, `POST /api/admin/evidence/scanner/check`, `POST /api/admin/evidence/reconcile`.
- **Evidence list:**
  - Shows "retries exhausted (n attempts)" for such files.
  - The API returns `retries_exhausted` and `scan_attempts`.
  - An administrator rescan starts a fresh set of attempts (audited `evidence.rescanned`).
- **Migration `0005`:**
  - Resets files that v0.3.1/0.3.2 had marked `failed` *only because of a scanner outage* to `pending` with 0 attempts. They
    are matched by their recorded detail: `scanner unreachable…`, `FileNotFoundError…`, `PermissionError…`,
    `TimeoutExpired…`, `OSError…`, or "FP_EVIDENCE_SCAN_COMMAND is not set".
  - The detail is prefixed with `[reset by migration 0005: scanner outage]`. The files stay quarantined.
  - Released files and real scan errors are not touched.

### Issue 2 – file moves were not part of the database transaction
**Cause.** On a clean result or an administrator release, `_move()` moved the file *before* the commit. If the commit failed
or the process died, the file could sit in `evidence/` while the database still said "unverified", or the other way round.
Uploads were written straight into the quarantine directory, and a failed commit left an unreferenced file behind.

**Strategy** (`app/services/evidence_store.py`):
1. **The database alone decides access.** Ordinary users get a file only when the database holds a persisted clean result or an
   administrator release. The directory a file is in grants nothing. **Every download is checked against the SHA-256 recorded at
   upload**; a mismatch returns 409 `integrity` and is logged.
2. **Atomic uploads.** The upload is written to `evidence/.staging/<name>.part`, fsynced, then renamed (`os.replace`) into
   `evidence/quarantine/`. If the database commit fails the file is deleted again.
3. **Transactional outbox.** A state change that implies a different location (clean result, release) inserts an
   `evidence_file_ops` row **in the same transaction**. The file is moved only **after** the commit, so a failed commit moves
   nothing.
4. **Idempotent placement.** `place()` moves a file to the location implied by the *current* database state. It verifies the SHA-256
   first, uses an atomic rename and fsyncs the directories, and can be repeated any number of times. A failed move stays
   `failed` and is retried.
5. **Reconciliation** (new job `evidence.reconcile`, every worker cycle, also without a scanner; also after every restore):
   - processes the outbox;
   - moves misplaced files;
   - reports missing files and checksum mismatches;
   - moves unreferenced files older than `FP_EVIDENCE_ORPHAN_GRACE_SECONDS` (default 1 h) to `evidence/orphaned/` (kept
     for review, never served, included in backups);
   - removes stale staging files.
   - Readiness `evidence_storage` warns on failed moves, misplaced files or missing files.
- **Backups:**
  - The quarantine is archived before `evidence/`, so a file released while the backup runs is still archived.
  - `evidence/orphaned/` is included.
  - The restore result contains `evidence_reconciliation`.
- The job runner now allows SAVEPOINTs inside job bodies (they do not end the job's transaction). Real commits are still
  rejected.
- Demo reset / delete-all also removes the outbox rows (SQLite does not enforce `ON DELETE CASCADE`).

### Tests
- New `tests/test_evidence_reliability.py` (20 tests):
  - **Issue 1:**
    - the exact review reproduction: missing scanner command, upload, more scan/worker runs than
      `FP_EVIDENCE_SCAN_MAX_ATTEMPTS`, the warning stays, then the command is installed and one worker cycle recovers;
    - zero-work runs never clear an outage;
    - no scanner call during an outage;
    - exhausted-retry counting and admin rescan;
    - command and clamd health checks (PING/VERSION/EICAR; a scanner that does not detect EICAR is unhealthy; daemon stopped);
    - stale health;
    - admin endpoints and permissions.
  - **Issue 2:**
    - database commit failure on upload (no file, no row), on release (nothing moved, no access) and on the scan job (result
      and outbox rolled back);
    - worker crash during a scan (nothing committed, recovery on the next run);
    - crash after commit but before the move (served from the database state, then placed by reconciliation, which is idempotent);
    - file move failure (disk full simulated: release committed, move `failed`, readiness warns, retried, then succeeds);
    - unverified file in `evidence/` not served;
    - tampered file not served;
    - orphan and stale-staging clean-up with grace period;
    - migration 0005 data fix (downgrade → legacy rows → upgrade);
    - restore with a queued move;
    - a global invariant check (ordinary users can download only DB-verified files).
- Updated tests:
  - `test_evidence_scanning.py`: outage semantics, FakeClamd PING/VERSION, health checks against real clamd/clamscan.
  - The autouse fixture `_reset_scanner_health` keeps tests order-independent.
- Made two older tests independent of ordering at the level of individual tests:
  - `test_template_and_history` asserted on the unfiltered audit log.
  - `test_status_transitions` counted audit rows by an entity id that SQLite reuses after a demo reload.
- E2E:
  - The admin test covers the scanner card: health, *Check scanner now*, *Reconcile files*.
  - The KPI-comparison step now uses explicit periods inside the synthetic data range (Jul–Sep 2026). Its default periods
    follow the "today"-relative demo investigation dates and had left the data range on 2026-10-19, so it failed independently
    of this release.

### Verification (actual results, 2026-10-19)
| Check | Result |
|---|---|
| Backend, PostgreSQL 16, incl. real ClamAV (`FP_TEST_CLAMD`, `FP_TEST_CLAMSCAN`), backups/restore | **216 passed** |
| Backend, SQLite, normal order | 207 passed, 9 skipped (PostgreSQL-only: backups, advisory locks, query plans; real-ClamAV opt-ins) |
| Backend, SQLite, **reverse test order** (every test, not only files) | 207 passed, 9 skipped |
| Changed test files run alone | all passed |
| Frontend: `tsc`, Vitest | no type errors; 20 passed |
| Playwright E2E with ClamAV (`clamd`) | 24 passed |
| Playwright E2E without scanner | 24 passed |
| Migration 0005 on a copy of the dev DB (upgrade, data fix, `alembic check`, downgrade, upgrade) | passed |
| Live scanner outage → recovery on the running stack (clamd stopped for 2 worker cycles) | warning persisted, then cleared after recovery; file scanned clean |
| `scripts/verify_deployment.py`, `python -m app.cli check` | 19/19; all checks OK |

Not repeated for v0.3.3: building and running the Docker images (`docker compose config` validated only).

### Known limitations
- File moves are atomic only within one filesystem (evidence and quarantine on the same volume, as shipped). Network
  filesystems were not tested.
- Reconciliation and the `evidence_storage` readiness check stat every evidence file. That is fine for thousands of
  files, but they are not designed for millions.
- `evidence/orphaned/` is never cleaned automatically. Review it and delete files manually.
- The health check proves that the scanner works and detects EICAR. It does not measure detection quality.
- Health is per scanner endpoint. Changing `FP_CLAMD_HOST`/`PORT` or the command starts from "not checked yet" (a warning)
  until the worker's next check.
- A scan *timeout* on a single file is treated as a per-file error (attempt +1), not as an outage.
- Readiness reports scanner outages as `warn`, not `fail`. The API stays "ready" because evidence remains safely
  quarantined.

## 0.3.2 – Review follow-up (2026-10-19)

Two findings from the independent review of v0.3.1. No functional changes and no migration.

- **Tests are independent of execution order.**
  - `test_clock_timezone.py` failed when run alone because it relied on master data loaded by an earlier test module.
    The new fixture `master_data` loads the synthetic demo data only if the test database has no production lines.
  - `test_readiness_reports_worker_state` failed in reverse order: it now measures relative to the last successful
    evaluation and signs in at the frozen time.
  - Verified three ways:

    | Run | Result |
    |---|---|
    | Every test file alone (fresh process each) | all passed |
    | Whole suite in reverse file order (SQLite) | 188 passed, 8 skipped |
    | Normal order (PostgreSQL, incl. real ClamAV) | 196 passed |
- **Background jobs are fully transactional, and this is enforced.**
  - The evidence-scan job called `db.commit()` inside its error path, which contradicted the documented
    "one transaction per attempt" rule.
  - A scanner outage is now a recorded *outcome*: the file stays quarantined, the attempt is counted, and
    `scanner_unreachable: true` appears in the job details. It is committed once by the job runner; the job no longer
    "fails" for an unreachable scanner.
  - New readiness warning `evidence_scanner_reachable`.
  - The runner now **rejects any commit inside a job body** (`JobBodyCommitError`, run recorded as failed, all writes
    rolled back). New tests: `test_job_bodies_cannot_commit`, `test_scanner_outage_is_recorded_transactionally_and_reported`.


## 0.3.1 – Production hardening & reliability (2026-10-18)

This release fixes findings of an independent source-code review. It adds no new business features (Phase 3,
Financial Impact, has not been started). Migration `0004` is additive. Existing records were preserved in a
verified upgrade of a v0.3.0 Docker installation.

### 1. Production demo safety
- **Fixed:** an empty production database received the synthetic demo dataset on first start, because compose defaulted
  `FP_SEED_DEMO_ON_STARTUP=true`.
  - Seeding is now **automatic only for `FP_ENVIRONMENT=development|demo`**.
  - Production seeds only with an explicit `FP_SEED_DEMO_ON_STARTUP=true`, which logs a warning.
  - Docker Compose passes unset variables as an empty string, which now means "automatic" instead of failing.
  - Demo tools follow the same rule: on for `demo`, off in production.
- **Fixed:** on a seeded demo system, the demo placeholder accounts blocked the `FP_INITIAL_ADMIN_*` bootstrap, so the
  system had no administrator. Found during the upgrade test.
- Tests: the real application (migrations + start-up) runs in a separate process on a brand-new database.
  - Production default, blank value, `false` and unknown environment: 0 records, 0 investigations, 0 synthetic
    companies, 0 demo users.
  - `demo` environment: data is seeded and the administrator is created.
  - Explicit opt-in: honoured, with a warning in the log.

### 2. Timezone reliability
- `app/clock.py` is the **single injectable time source**.
  - All business dates (today, overdue, default due dates, investigation year, "resolved this month", weekly trend,
    report and export time stamps) come from UTC via `FP_TIMEZONE`, never from the host clock.
  - Local day boundaries are converted to UTC instants.
- **Fixed:** on SQLite, date filters were shifted by the UTC offset, because a local-time parameter was compared with UTC
  values stored without an offset.
- **Fixed:** "resolved this month" had no upper bound.
- Tests no longer use `date.today()`.
  - New fixture `frozen_clock`.
  - Tests at UTC midnight (overdue switches at 00:00 Berlin, not 00:00 UTC), at the year boundary (INV-2027 at
    00:30 CET on 1 January) and on both daylight-saving transitions (23 h and 25 h days, DST-correct date filters,
    hour-based rules use elapsed time across the fall-back).
  - The whole suite was also run with the host in `TZ=Pacific/Kiritimati`.

### 3. Alert processing
- **Dashboard requests are read-only.**
  - `GET /api/action-center/summary` no longer evaluates rules or runs anomaly detection.
  - It returns `alert_evaluation` (last run, `ok|stale|never|failed`).
- New **worker process** `python -m app.worker` (compose service `worker`).
  - Evaluates the rules every `FP_ALERT_INTERVAL_SECONDS` (default 300) and re-scans quarantined evidence.
  - Waits for the migrations of the API container.
  - Shuts down cleanly on SIGTERM.
- Job framework (`app/services/jobs.py`, table `job_runs`):
  - One transaction per attempt; a failure is rolled back completely.
  - Exponential backoff, logged at ERROR, CRITICAL after the last attempt; the worker survives and retries next cycle.
  - Runs of killed processes are marked `abandoned`; history is pruned after 30 days.
- **Concurrency:** a PostgreSQL advisory lock per job and company means a second run is recorded as `skipped`.
  The partial unique index on open alerts stays as the last line of defence. Verified with 6 parallel evaluations
  in tests and 3 worker containers in Docker: no duplicates.
- Deduplication, acknowledgement and automatic resolution are unchanged and covered by tests. The repeated-anomaly rule
  keeps its state when it is not part of a run.
- New endpoints `POST /api/action-center/alerts/evaluate` (admin, *Evaluate now*, audited) and
  `GET /api/action-center/jobs` (admin).
- New readiness check `alert_worker`.
- UI shows when the rules were last evaluated, plus stale/failed hints.

### 4. Database scalability
- The investigation list (search, every filter, every sort order, count, page) runs **in SQL**, with a deterministic
  tie-breaker. Before, every investigation was loaded and sorted in Python.
- The dashboard summary uses SQL aggregates: grouped counts, overdue actions joined in SQL, 8 priority issues and
  10 overdue actions via `LIMIT`, and averages in PostgreSQL.
- German search: new column `investigations.search_text`.
  - NFKC + full case folding, plus an ä→ae / ö→oe / ü→ue variant.
  - Maintained by an ORM hook and backfilled by the migration.
  - Matched with `LIKE`, with `%`, `_` and `\` escaped.
  - Works identically on PostgreSQL (independent of the database locale) and SQLite.
  - Examples: `ÖLFILM` = `ölfilm`, `oelfilm` finds `Ölfilm`, `STRASSE` finds `Straße`, decomposed umlauts.
- New indexes:
  - `(company_id, due_date)`, `(company_id, updated_at)`, `(company_id, owner_id, status)`,
    `(company_id, line_id, status)`, `(company_id, resolved_at)`
  - `pg_trgm` GIN index on `search_text` (created only if the extension can be installed)
  - indexes for `job_runs` and the attachment scan status
- Test with **50,000 synthetic investigations**, PostgreSQL 16:

  | Query | Time |
  |---|---|
  | List, any filter, sort or page | 25–80 ms |
  | Dashboard summary | 0.43 s |

  - Paging returns every row exactly once.
  - The query plans use the trigram and company/created indexes.
  - SQLite: list 15–115 ms, summary 1.2 s.

### 5. Deployment
- **HTTPS for LAN installations:** `docker-compose.lan.yml` (`FP_TLS=true`).
  - TLS 1.2/1.3, AEAD cipher suites, HSTS.
  - HTTP → HTTPS redirect to `FP_PUBLIC_URL`; ports 443/80 only.
  - The nginx entrypoint refuses to start without a certificate.
  - `ops/generate-tls-cert.sh` creates an internal ECDSA CA and a server certificate with SAN (DNS names / IP addresses).
- **Fixed (security):** the bundled nginx appended to client-supplied `X-Forwarded-For` headers and uvicorn
  (`--forwarded-allow-ips *`) trusted the left-most entry. Clients could therefore forge the IP in the audit log and
  evade the per-IP login limit. nginx now overwrites the header (verified with a forged header).
- nginx configuration split into `frontend/nginx/{app,http,tls,security-headers}.conf`.
  - CSP extended with `frame-ancestors`, `base-uri` and `form-action`.
  - Security headers are repeated in locations that set their own headers.
- Compose:
  - new `worker` service and optional `clamav` profile; images tagged `factorypulse/*:0.3.1`
  - only the API container migrates (`FP_RUN_MIGRATIONS=false` for the worker)
  - configurable base images (`FP_PYTHON_IMAGE`, `FP_NODE_IMAGE`, `FP_NGINX_IMAGE`, `FP_POSTGRES_IMAGE`,
    `FP_CLAMAV_IMAGE`) and `FP_BUILD_PGDG` for internal registry mirrors
- New guide **[docs/INSTALLATION.md](docs/INSTALLATION.md)**: secure installation for a German manufacturing customer
  (BetrVG § 87 and GDPR checklist, certificate, scanner, hardening, backups, updates).
- **Verified with Docker** ([docs/DEPLOYMENT_VERIFICATION.md](docs/DEPLOYMENT_VERIFICATION.md)), using locally built
  Ubuntu 24.04 base-image stand-ins because registries are blocked in the sandbox:

  | Check | Result |
  |---|---|
  | Fresh HTTPS installation: migrations, login, password change, roles, imports (CSV + XLSX), Action Center, ClamAV, worker, reports, backup/restore, Secure cookie, CSRF through the proxy, TLS 1.1 rejected | 51/51 |
  | `ops/verify.sh` | 17/17 |
  | Browser smoke test over HTTPS | passed |
  | Three concurrent workers | no duplicate alerts |
  | Upgrade of a v0.3.0 installation | all records preserved |
- `ops/verify.sh` trusts `tls/ca.crt` automatically; `verify_deployment.py` checks the Action Center as well.

### 6. Evidence security
- Configurable **malware scanning**: `FP_EVIDENCE_SCANNER=none|clamd|command`.
  - `clamd`: ClamAV INSTREAM over TCP.
  - `command`: any CLI, exit 0 = clean, 1 = infected.
- **Quarantine:** uploads are written to `/data/evidence/quarantine`; clean files move to `/data/evidence`.
  - Infected files are never downloadable, not even for administrators.
  - Failed scans are retried by the worker (`FP_EVIDENCE_SCAN_MAX_ATTEMPTS`).
  - Without a scanner, files stay `not_scanned`. Viewers and analysts cannot download them. Administrators can download
    only after confirming the risk (audited), or **release** a file with a written justification (audited, timeline).
  - Scan results are recorded in the timeline and the audit log.
  - Readiness checks: `evidence_scanner`, `evidence_infected`.
- Existing v0.3.0 evidence is treated as unscanned after the upgrade. With a scanner, the worker scans it automatically.
- **Fixed:** backups did not contain quarantined files (they were not recursive). The quarantine directory is now part
  of backup, restore validation, demo reset and delete-all.
- UI: scan-status badges, quarantine notes, risk-confirmation download, release dialog (DE/EN).
- Tested with a fake scanner, a fake clamd server, and a **real ClamAV 1.5.4** (clamd and clamscan, local EICAR test
  signature).

### 7. Regression testing

| Suite | Result |
|---|---|
| Backend, PostgreSQL 16 | **195 passed**, incl. the optional real-ClamAV tests |
| Backend, SQLite, host `TZ=Pacific/Kiritimati` | **187 passed, 8 skipped** (PostgreSQL-only backups, advisory locks, query plans; real-ClamAV opt-ins) |
| Frontend unit (Vitest) | **19/19** |
| E2E (Playwright, all roles) | **24/24**, run three times with the ClamAV scanner; the Action Center E2E also passed without a scanner (quarantine / release path) |

- Backup and restore are verified in the test suite (incl. quarantined evidence) and in Docker.
- One E2E run failed once while the backend suite ran in parallel on the same machine (an evidence scan exceeded the
  5 s assertion timeout under load). The timeout for scan results was raised to 20 s; idle and loaded reruns passed.
- New regression test files:
  - `test_demo_seeding_policy.py`
  - `test_clock_timezone.py`
  - `test_alert_jobs.py`
  - `test_investigation_scale.py`
  - `test_evidence_scanning.py`
  - `test_deployment_config.py`


## 0.3.0 – Phase 2: Operational Action Center (2026-10-17)

### Added

**Action Center / Maßnahmenzentrum** (new navigation group *Operations*)
- **Overview:**
  - KPI cards: open, critical, overdue actions, in progress, resolved this month, average resolution time.
  - Charts: by severity, line and category; opened vs. resolved per week; overdue actions by responsible person.
  - Lists of priority issues and overdue actions; rule alerts with acknowledge.
  - Filters for date, line, machine, severity, status, owner and category.
  - All values are computed from stored records.
- **Investigations:**
  - IDs `INV-YYYY-NNNN`. Fields: category, severity, line, optional machine, owner, creator, due date, resolution summary, and created, status-changed, resolved and closed timestamps.
  - Categories: equipment failure, production loss, quality issue, excessive downtime, process deviation, safety observation (administrative record only), other.
  - List with full-text search, server-side sorting, filters and pagination.
- **Workflow** Open → In progress → Pending verification → Resolved → Closed / Reopened, validated on the server:
  - Verification requires completed actions.
  - Resolving or closing requires a documented outcome.
  - Reopening requires a reason.
  - Closing and reopening are admin-only.
  - Every transition is recorded in the timeline and the audit log.
- **Corrective actions:**
  - Multiple per investigation, each with its own owner, due date and priority.
  - Status open / in progress / done / verified / cancelled.
  - Completion notes are required; effectiveness verification (effective / partially / not effective) by the investigation owner or an admin.
  - *My Actions* page grouped into overdue, due within 7 days, later, awaiting verification and finished.
- **Activity timeline:** creation, assignment, status, deadline and field changes, comments, actions, evidence, verification and recorded KPI comparisons.
- **Evidence:**
  - PDF/PNG/JPEG/CSV/XLSX with a size limit (`FP_MAX_EVIDENCE_MB`), extension + magic-byte + text checks and SHA-256.
  - Private storage and authenticated download only.
  - Soft delete with a mandatory reason.
- **Anomaly → investigation:**
  - A *Create investigation* button in AI Insights.
  - The server re-detects the anomaly and returns an editable proposal; nothing is saved until the user confirms.
  - An immutable evidence snapshot (values, z-score, baseline, explanation, detection settings) is stored.
  - Duplicates are prevented (unique per anomaly and enforced in the database).
  - The text states explicitly that the cause is not established.
  - Linked anomalies show their investigation number.
- **Before/after KPI comparison:**
  - Metrics: OEE, availability, performance, quality, defect rate, unplanned downtime and output.
  - Choose baseline and comparison periods, machine or line scope, and shift/product filters.
  - Shows before, after, absolute difference, % change and coverage, plus a daily chart. Counts are compared per production day.
  - Shows N/A when a period has fewer than 3 production days.
  - Labelled as *observed change – not proof of cause*. A result can be recorded in the timeline.
- **Configurable rules** (Settings, admin): critical without owner, critical open too long, pending verification too long, investigation and action overdue (with grace period), repeated anomalies on one machine, and default due days per severity. Alerts are persisted, deduplicated per condition, carry first-seen/last-seen timestamps in the company timezone and resolve automatically. There are no e-mail or SMS notifications.
- **Permissions:**
  - New permissions `action.read` (viewer), `action.write` (analyst) and `action.manage` (admin).
  - Object-level checks (owner/creator/admin) are enforced in the API.
  - The UI shows only the controls the user may use.
- **Demo data:** 8 synthetic investigations covering every status, linked to the injected anomalies, with overdue actions, a reopened case, a safety observation and an unassigned critical issue. Demo reload, delete-all and backup/restore include the new records and evidence files.
- **Database:** additive migration `0003_action_center` (7 new tables; no changes to existing tables).
- **Documentation:** API, README, SECURITY and OPERATIONS updated; Phase 2 screenshots `docs/screenshots/p2_*.png`.

### Changed
- `scripts/verify_deployment.py` checks the Action Center (17 checks). `GET /api/settings` reports `max_evidence_mb`. `POST /api/anomalies/detect` adds `investigation` to linked anomalies.

### Fixed
- The PDF/Excel **quality report title** read "Quality"/"Qualität" instead of "Quality Report"/"Qualitätsbericht": report titles and KPI labels shared one dictionary key (present since 0.1.0).
- Investigation search is Unicode-aware on every database, e.g. "Ölfilm" on SQLite.

### Tests
- Backend: 22 new tests (128 total on PostgreSQL; 124 + 4 PostgreSQL-only skips on SQLite). Frontend unit: 4 new (16 total). E2E: 8 new Action Center scenarios (24 total).

## 0.2.0 – Phase 1: Security & reliability (2026-10-17)

### Added

**Authentication**
- Local user accounts with Argon2id password hashing.
- Server-side sessions in HttpOnly, SameSite=Strict and Secure cookies, with an idle timeout (120 min) and an absolute lifetime (12 h).
- A CSRF token is required on every state-changing request.
- Account lockout after 5 failures, plus a per-IP login rate limit in both the API and nginx.
- Forced password change for temporary passwords; a password policy with a blocklist.
- Change-password, logout and log-out-everywhere endpoints.
- Migration `0002`: tables `users` and `user_sessions`; new audit columns `user_id` and `user_email`.

**Role-based access control**
- Roles viewer, analyst and admin, mapped to 15 named permissions.
- Every API route is protected, and two automated tests enforce this (static permission check and anonymous call to every route).

**User administration** (Administration page and API)
- Create users, change roles, deactivate or reactivate, unlock, reset passwords.
- Protection against removing the last administrator and against self-lockout.
- Sessions are revoked on role change or deactivation.

**Destructive-action guards**
- Demo reload, delete all data, restore and backup deletion require the admin role, a typed confirmation word and password re-entry, all checked on the server.
- Import revert is admin-only and requires `REVERT`.
- Demo/data tools are **disabled in production** by default (`FP_ENABLE_DEMO_TOOLS`).

**Backup & restore**
- Backups contain a `pg_dump` of the database plus uploaded files and reports, with a SHA-256 manifest and retention.
- Restore verifies checksums and schema compatibility, takes an automatic safety backup, restores in one transaction, migrates to the current schema and revokes all sessions.
- Available in the UI, the CLI (`python -m app.cli backup|list-backups|restore`) and host scripts (`ops/backup.sh` with off-site copy and verification, `ops/restore.sh`).

**Deployment verification**
- `GET /api/health/ready` (public, 503 when not ready) and `GET /api/admin/system` (details). Checks: database, schema revision, storage, disk, PDF engine, backup tools, last backup, admin presence, production settings.
- `ops/verify.sh` and `backend/scripts/verify_deployment.py`: a 15-step smoke test, including security headers, the 401 deny, cookie flags, CSRF, report generation and logout.

**Operations CLI**
- `create-user`, `reset-password` (passwords are prompted or read from stdin, never passed as arguments), `list-users`, `backup`, `list-backups`, `restore`, `check`.

**Audit trail**
- Every entry records the acting user, IP address and request ID.
- New events: sign-in success/failure, lockout, logout, password changes, user management, backups, re-authentication failures.
- The Administration → Audit log view is filterable by event.

**Request tracing**
- `X-Request-ID` on every response, in error bodies and in the access log.

**UI**
- Login page (DE/EN), forced password change screen, and a user menu (change password, sign out).
- Navigation and actions adapt to the user's role, with a clear "no access" page for routes the role can't use.
- Session-expired handling.
- Confirmation dialogs with password re-entry.
- New Administration page with tabs: Users & roles, Backups, System status, Data tools, Audit log.

**Documentation**
- `docs/OPERATIONS.md` added.
- `docs/SECURITY.md` and `docs/API.md` rewritten.
- README updated.

### Changed
- `POST /api/demo/load` → `POST /api/admin/demo/load` and `DELETE /api/data` → `POST /api/admin/data/delete`. Both now take `{password, confirm}`.
- `POST /api/imports/{id}/revert` now requires the body `{"confirm": "REVERT"}`.
- Error responses are uniform: `{"detail": "...", "code": "..."}`.
- Data tools and the audit log moved from Settings to Administration. Settings are read-only for non-admins.
- Report timestamps use the configured timezone (`FP_TIMEZONE`, default Europe/Berlin) instead of the server's timezone.
- Docker:
  - The backend image installs `postgresql-client-16` from PGDG for backups.
  - The health check uses readiness.
  - New environment variables for security, timezone, retention and the initial admin.
  - nginx: login rate limit, longer timeout for backup routes, `server_tokens off`, security headers repeated for `/assets`.
- Routers are imported explicitly. Previously an import error inside a router module silently removed its routes.
- Version 0.2.0.

### Fixed
- Reloading demo data or deleting all data left orphaned upload and report files on disk. They are now removed.
- Alembic's logging configuration disabled the application loggers after migrations run in-process.
- nginx `add_header` in `/assets` dropped the server-level security headers for static assets.

## 0.1.0 – MVP (2026-10-17)
Initial release: CSV/XLSX import pipeline, KPI engine, dashboards, quality and downtime analysis, statistical anomaly detection, PDF/Excel reports, synthetic demo data, DE/EN.

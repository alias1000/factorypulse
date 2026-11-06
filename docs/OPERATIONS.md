# Operations Guide

This guide covers installation, user administration, backup and restore, deployment verification, upgrades and troubleshooting. Commands assume the Docker Compose deployment in the repository root.

## 1. First installation

**Customer installations (several users in the plant LAN): follow [INSTALLATION.md](INSTALLATION.md)** – HTTPS,
certificate, malware scanner, works-council and GDPR checklist.

Single workstation or demo system:

```bash
cp .env.example .env
# edit .env:
#   POSTGRES_PASSWORD            long random value
#   FP_INITIAL_ADMIN_EMAIL       first administrator
#   FP_INITIAL_ADMIN_PASSWORD    ≥ 12 characters (temporary – must be changed at first sign-in)
#   FP_ENVIRONMENT               production (default) | demo (loads the SYNTHETIC demo dataset once)
#   FP_SEED_DEMO_ON_STARTUP      leave empty (automatic: never in production)
#   FP_ENABLE_DEMO_TOOLS         leave empty (automatic: off in production, on in demo)
docker compose up -d --build
ops/verify.sh                         # see section 4
```

Services: `db`, `backend` (API, applies migrations), **`worker`** (scheduled jobs, v0.3.1), `frontend` (nginx) and
optionally `clamav` (`COMPOSE_PROFILES=clamav`).

Once the administrator has signed in and changed the password, **remove `FP_INITIAL_ADMIN_PASSWORD` from `.env`**. It is only used when no user exists yet.

Alternatively, create the first administrator interactively. The password is prompted for and never passed as an argument:

```bash
docker compose exec backend python -m app.cli create-user --email admin@firma.de --name "Max Mustermann" --role admin
```

**TLS.** Multi-user installations run in HTTPS mode: `docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d`
(nginx terminates TLS 1.2/1.3, sends HSTS and redirects port 80 to `FP_PUBLIC_URL`; certificate in `./tls`, see
`ops/generate-tls-cert.sh`). Session cookies always carry the `Secure` flag: browsers accept it on `http://localhost`,
but **not** over plain HTTP from other machines – plain HTTP is for a single workstation only. If another reverse
proxy/load balancer sits in front of FactoryPulse, configure `set_real_ip_from` in `frontend/nginx/app.conf`:
the bundled nginx overwrites `X-Forwarded-For` with the connecting address so clients cannot spoof their IP in
the audit log.

## 2. Users and roles

| Role | Typical person | Can |
|---|---|---|
| Viewer | shift lead, management | dashboards, analyses, anomaly detection, download reports and exports |
| Analyst | production engineer, controller | + import data, generate and delete reports |
| Admin | IT / plant manager | + users, settings, import reverts, backups/restore, data tools, audit log, system status |

Users are managed under **Administration → Users & roles** or with the CLI (`create-user`, `reset-password`, `list-users`).

- **New and reset passwords** are temporary: the user must set a new one at the next sign-in.
- **Deactivate, don't delete.** Users are never deleted, so the audit trail stays complete. Deactivating a user ends all of their sessions immediately, and so does a role change.
- **Lockout.** After 5 failed sign-ins an account is locked for 15 minutes. An administrator can unlock it earlier.
- **Last administrator.** The last active administrator cannot be demoted or deactivated, and administrators cannot demote or deactivate themselves.
- **Session lifetime.** Sessions end after 120 minutes of inactivity and after 12 hours at the latest (`FP_SESSION_IDLE_MINUTES`, `FP_SESSION_ABSOLUTE_HOURS`).

## 3. Backup and restore

### What a backup contains

| File | Content |
|---|---|
| `database.dump` | full PostgreSQL database in `pg_dump` custom format. Session data is excluded, so a backup never contains valid sign-in sessions. |
| `files.tar.gz` | uploaded import files, generated reports and Action Center evidence files (`evidence/`, the quarantine `evidence/quarantine/` and, since v0.3.3, `evidence/orphaned/`) |
| `manifest.json` | application version, schema revision, creator, timestamp, size and SHA-256 checksum of each file |

Backups are stored in the `app-data` volume under `/data/backups`. The newest `FP_BACKUP_RETENTION` backups are kept (default 14).

### Creating backups

- **On demand:** Administration → Backups → *Create backup now*, or `docker compose exec backend python -m app.cli backup`.
- **Scheduled with off-site copy (recommended):** `ops/backup.sh` creates a backup, copies it to `FP_BACKUP_COPY_DIR` on the host (default `./backups-offsite`), verifies the checksums and keeps the newest 30 copies. Run it daily from cron:

  ```cron
  15 2 * * *  /opt/factorypulse/ops/backup.sh >> /var/log/factorypulse-backup.log 2>&1
  ```

  Then copy `backups-offsite/` to a second location: NAS, tape, or another site.
- **Monitoring:** the readiness check reports `last_backup = warn` when no backup is newer than 7 days.

### Restoring

- **In the UI:** Administration → Backups → *Restore*. You must type `RESTORE` and your password.
- **From the host,** e.g. an off-site copy: `ops/restore.sh backups-offsite/fp-backup-YYYYMMDD-HHMMSS-xxxxxx`

A restore runs these steps:

1. Verifies the SHA-256 checksums and checks that the backup's schema revision is known to this application version. A backup from a *newer* version is refused.
2. Creates an automatic **safety backup** of the current state (type "safety (before restore)").
3. Restores the database with `pg_restore --clean --single-transaction`. Either everything is restored or nothing is.
4. Migrates the restored database to the current schema (`alembic upgrade head`).
5. Replaces uploaded files and reports.
6. Ends **all** sessions, so every user must sign in again. A `backup.restored` entry is written to the audit log.

> Users, roles and passwords are restored to the state of the backup.

**Test your restores.** Restore a recent backup into a separate staging installation at least once a quarter. A backup that has never been restored is not a proven backup.

### Version requirement

`pg_dump`/`pg_restore` must have the same major version as the PostgreSQL server (16). The backend image installs `postgresql-client-16` from the official PGDG repository (or, with `FP_BUILD_PGDG=false`, from the base distribution if it ships version 16). Administration → System status shows the detected versions.

## 4. Deployment verification

Run this after every installation, upgrade or configuration change:

```bash
FP_VERIFY_EMAIL=admin@firma.de ops/verify.sh https://factorypulse.firma.de
```

`ops/verify.sh` runs:

1. `app.cli check`, which covers database, schema revision, writable storage, free disk space, PDF engine, backup tools, last backup, presence of an administrator, and production settings (Secure cookies, demo tools disabled).
2. `backend/scripts/verify_deployment.py`, which checks:
   - liveness and readiness
   - frontend delivery and security headers
   - that unauthenticated access is denied
   - login and cookie flags
   - master data and the KPI engine
   - Action Center summary (and that it is denied without a session)
   - CSRF protection
   - report generation and download (one small report is created and deleted again)
   - logout

   It exits non-zero on any failure.

Endpoints for monitoring:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/health` | public | liveness |
| `GET /api/health/ready` | public | readiness. Returns 503 when not ready; shows check names and status only, never details. Used by the Docker health check. |
| `GET /api/admin/system` | admin | all checks with details, version and build |

## 5. Action Center (v0.3)

- **Alert rules** (Settings → *Action Center rules*, admin only): thresholds for critical investigations without an owner, critical investigations open too long, verification pending too long, overdue actions (with a grace period), and repeated anomalies on the same machine, plus default due dates per severity.
- **Scheduled evaluation (v0.3.1):** the rules are evaluated by the **`worker`** service every `FP_ALERT_INTERVAL_SECONDS` (default 300) – *not* by dashboard requests, which are read-only. Each condition creates **one** alert, updated in place (no duplicates, acknowledgements are kept) and closed automatically when the condition no longer holds. Administrators can trigger *Evaluate now* on the Action Center overview. The overview shows when the rules were last evaluated; a stale or failed evaluation is highlighted and reported by the readiness check `alert_worker`.
- **Job history and failures:** every job attempt is stored in `job_runs` (`GET /api/action-center/jobs`, admin): status, attempt, duration, worker, error. Failed attempts are rolled back completely and retried with exponential backoff (`FP_JOB_MAX_ATTEMPTS`, `FP_JOB_RETRY_BASE_SECONDS`); a run that still fails is logged at CRITICAL level and the next cycle tries again. Job bodies may not commit themselves (enforced). An unreachable malware scanner is not a job failure and not a property of the file: affected files stay quarantined and `pending` without using up scan attempts, and the outage is recorded in the scanner health (see below). Several workers may run (`docker compose up -d --scale worker=2`): PostgreSQL advisory locks ensure a job runs only once at a time (the other run is recorded as `skipped`). Runs interrupted by a killed container are marked `abandoned`; history older than `FP_JOB_HISTORY_DAYS` (30) is pruned.
- **Evidence storage and quarantine:** `/data/evidence` (verified files) and `/data/evidence/quarantine` (unverified) in the `app-data` volume, limited by `FP_MAX_EVIDENCE_MB` (default 10). Uploads go to quarantine first; the configured scanner (`FP_EVIDENCE_SCANNER=clamd|command|none`, see [INSTALLATION.md](INSTALLATION.md#5-malware-scanning-of-evidence-files)) releases clean files. Infected files are never downloadable; unscanned files only for administrators (with risk confirmation, audited) or after an administrator *release* with justification. Make sure nginx `client_max_body_size` is at least as large as the upload limits. Removed evidence is soft-deleted: the file is kept for audit until the investigation data is deleted.
- **Scanner health (v0.3.3):** the worker runs the job `evidence.health` every cycle – an *active* check (clamd: `PING`, `VERSION`, scan of a clean and of the harmless EICAR test sample; command: scan of a clean and an EICAR test file in `evidence/.staging`). The result is persisted in `scanner_health`. The readiness check `evidence_scanner` reports `UNAVAILABLE since …, n consecutive failure(s), last error` and **stays a warning until a health check succeeds again** – a scan run that has nothing to do never clears it. While the scanner is unavailable the scan job is skipped and uploads are not scanned (they stay quarantined). Administrators see the state under *Administration → System → Evidence malware scanner* and can run *Check scanner now* (after a successful check the backlog is scanned immediately). Disable the EICAR part only if the scanner must not see the test string: `FP_EVIDENCE_SCANNER_SELFTEST_EICAR=false`.
- **Quarantine statistics (v0.3.3):** readiness `evidence_quarantine` = pending / retrying / **retries exhausted** / not scanned. Only per-file scanner errors (the scanner answered but could not scan this file) count as attempts (`FP_EVIDENCE_SCAN_MAX_ATTEMPTS`, default 5). Files with exhausted retries stay quarantined (warning); an administrator can *rescan* them (fresh attempts) or check and *release* them.
- **Evidence file consistency (v0.3.3):** access is decided only by the database (clean result or release), and every download is checked against the SHA-256 recorded at upload (409 `integrity` on mismatch). File moves are queued in `evidence_file_ops` in the same transaction as the state change and executed after the commit; the job `evidence.reconcile` (every cycle, also without scanner) retries failed moves, moves misplaced files, moves files without a database record older than `FP_EVIDENCE_ORPHAN_GRACE_SECONDS` (1 h) from the evidence directories to `evidence/orphaned/` (kept for review, never served) and removes stale upload staging files. Readiness `evidence_storage` warns on failed moves, misplaced or missing files; *Reconcile files* runs it on demand. A restore runs it automatically.
- **Upgrade to 0.3.3:** migration `0005` is additive (two new tables). It resets evidence that v0.3.1/0.3.2 had marked `failed` *only because the scanner was unreachable* (detail starts with `scanner unreachable`, `FileNotFoundError`, `PermissionError`, `TimeoutExpired`, `OSError` or "FP_EVIDENCE_SCAN_COMMAND is not set") to `pending` with 0 attempts; the files stay quarantined and are scanned once the scanner is healthy. Released files are not touched.
- **Upgrade from 0.2.x:** migration `0003_action_center` is additive. It only creates new tables and does not change existing data. Take a backup first, as with every upgrade.
- **Demo data:** the synthetic demo creates 8 investigations in every status. They are linked to the injected anomalies and assigned to three placeholder analysts (`*@demo.factorypulse.invalid`) with random, unusable passwords, so nobody can sign in as them.

## 6. Upgrades

1. `ops/backup.sh "before upgrade to vX.Y"`
2. `git pull && docker compose up -d --build` (LAN: add `-f docker-compose.lan.yml`). Migrations run automatically when the `backend` container starts (`docker-entrypoint.sh`); the `worker` waits until the schema is current (`FP_RUN_MIGRATIONS=false`).
3. `ops/verify.sh`
4. If something is wrong, restore the backup from step 1 with the previous application version.

## 7. Logs and troubleshooting

- **Logs:** `docker compose logs -f backend worker`. Every request is logged with method, path, status, duration, user and a **request ID**. The request ID is also returned to the browser in the `X-Request-ID` header and in error responses (`request_id`). Quote it in support requests.
- **Audit trail:** Administration → Audit log, filterable by prefix, e.g. `auth.`, `import.`, `backup.`, `user.`, `investigation.`, `action.`.
- **Locked out of all admin accounts:**

  ```bash
  docker compose exec backend python -m app.cli reset-password --email admin@firma.de
  ```

- **Readiness `migrations = fail`:** the database schema doesn't match the code. Check the container start log for migration errors.

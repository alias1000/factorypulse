# Secure Installation – German Manufacturing Customer (on-premises LAN)

This procedure installs FactoryPulse AI v0.3.1 on one server in the plant network and makes it available to
several users over **HTTPS**. It is written for the customer's IT department or a service partner. The run book
takes about 1–2 hours, plus the organisational steps in section 1.

> Scope: single company, single server, Docker Compose. FactoryPulse is **not** a multi-tenant system and **not**
> a certified safety-management system (safety observations in the Action Center are administrative records).

---

## 1. Before the installation (organisational)

| Topic | What to clarify | Why |
|---|---|---|
| **Works council (Betriebsrat)** | FactoryPulse records which user signed in, imported, commented, uploaded evidence and changed investigations (audit log, timeline). Under **§ 87 (1) no. 6 BetrVG** technical systems that *can* monitor employee behaviour or performance are subject to co-determination. Agree a works agreement (Betriebsvereinbarung) or obtain consent **before go-live**. | Legal requirement in companies with a works council |
| **Data protection (DSGVO/GDPR)** | Record the processing in the *Verzeichnis von Verarbeitungstätigkeiten* (Art. 30): user accounts (name, e-mail), audit log (IP address, actions), comments/evidence (may contain personal data). Define retention periods. No data leaves the server (no external AI, CDN or telemetry). | Art. 5, 30, 32 DSGVO |
| **Data owner / admins** | Name at least **two** administrators (deputy rule). Admins can see all data and restore backups. | Avoid lock-out, four-eyes principle |
| **Network** | Host name (e.g. `factorypulse.werk.local`) and a fixed IP address in the office/IT network (not the OT/machine network). DNS entry in the company DNS. | TLS certificate needs a stable name |
| **Certificate** | Certificate from the company PKI (preferred, e.g. AD Certificate Services) **or** use the included script to create an internal CA (section 4). | Browsers must trust the certificate |
| **Malware scanning** | Decide: ClamAV container (included, needs internet access for signature updates via proxy) **or** existing scanner via command line **or** no scanner (then every uploaded evidence file stays in quarantine until an administrator releases it). | Evidence files come from many PCs |
| **Backups** | Target for the off-site copy (NAS, second site), daily time window, who tests restores (at least quarterly). | Recovery after hardware failure / ransomware |

## 2. Server requirements

- Linux server (Ubuntu 24.04 LTS / Debian 12 / RHEL 9), 4 vCPU, 8 GB RAM, 50 GB SSD (+ backup space).
- Docker Engine ≥ 24 with the Compose plugin **≥ 2.24**.
- Time synchronisation (chrony/systemd-timesyncd). FactoryPulse stores UTC and shows Europe/Berlin
  (`FP_TIMEZONE`); a wrong server clock makes deadlines and overdue alerts wrong.
- Host firewall: allow **443/tcp** (and 80/tcp for the redirect) from the client networks only; SSH only from the admin network. The database, the API and clamd are *not* published.
- **Registry access:** the images are built from `python:3.12-slim`, `node:22-alpine`, `nginx:1.27-alpine`, `postgres:16-alpine` and `clamav/clamav`. In air-gapped plants mirror them into an internal registry and set `FP_PYTHON_IMAGE`, `FP_NODE_IMAGE`, `FP_NGINX_IMAGE`, `FP_POSTGRES_IMAGE`, `FP_CLAMAV_IMAGE` in `.env`. If the internal mirror has no PGDG apt repository and the distribution provides `postgresql-client-16`, set `FP_BUILD_PGDG=false`.

## 3. Install the software

```bash
sudo mkdir -p /opt/factorypulse && cd /opt/factorypulse
sudo unzip factorypulse-ai-v0.3.1.zip && cd factorypulse-ai
sudo cp .env.example .env && sudo chmod 600 .env
```

Edit `.env` (all values are examples):

```ini
FP_ENVIRONMENT=production            # never "demo" on a customer system
POSTGRES_PASSWORD=<40 random characters>        # openssl rand -base64 30
FP_PUBLIC_URL=https://factorypulse.werk.local
FP_CORS_ORIGINS=https://factorypulse.werk.local
FP_INITIAL_ADMIN_EMAIL=it-admin@werk.example
FP_INITIAL_ADMIN_PASSWORD=<temporary, ≥ 12 characters, must not contain the e-mail name>
FP_SEED_DEMO_ON_STARTUP=             # leave EMPTY: production never receives synthetic demo data
FP_ENABLE_DEMO_TOOLS=                # leave EMPTY: reload-demo / delete-all are disabled in production
FP_TIMEZONE=Europe/Berlin
# malware scanning (section 5)
FP_EVIDENCE_SCANNER=clamd
COMPOSE_PROFILES=clamav
```

## 4. HTTPS certificate

**Option A – company PKI (recommended).** Request a server certificate with SAN = `factorypulse.werk.local`
(+ short name / IP address if users type those). Store it as PEM:

```bash
sudo mkdir -p tls && sudo cp server.crt server.key tls/ && sudo chmod 600 tls/server.key
```

`server.crt` must contain the full chain (server certificate first, then intermediate certificates).

**Option B – internal CA created by the script.**

```bash
sudo ops/generate-tls-cert.sh factorypulse.werk.local factorypulse 192.168.10.20
```

Distribute `tls/ca.crt` to all client PCs (Windows: Group Policy → *Computer Configuration → Policies →
Windows Settings → Security Settings → Public Key Policies → Trusted Root Certification Authorities*; Firefox:
`security.enterprise_roots.enabled=true`). **Move `tls/ca.key` off the server** (safe / password manager); it is
only needed to issue a new server certificate (validity 825 days – put the renewal date in the maintenance calendar).

## 5. Malware scanning of evidence files

| `FP_EVIDENCE_SCANNER` | Behaviour |
|---|---|
| `clamd` + `COMPOSE_PROFILES=clamav` | Starts the `clamav` container. Every upload is scanned on the server before anyone else can download it. The container needs outbound HTTPS to `database.clamav.net` (via the company proxy) for daily signature updates. |
| `command` | Uses an existing scanner on the server, e.g. `FP_EVIDENCE_SCAN_COMMAND="clamdscan --no-summary --fdpass"`; exit code 0 = clean, 1 = infected, others = error. The command must exist inside the backend image. |
| `none` | No scanner. Uploads are stored **in quarantine** (`not_scanned`); only administrators can download them (after confirming the risk) or release them with a written justification. |

Infected files are never downloadable. Failed scans are retried by the worker. Since v0.3.3 the worker checks the
scanner actively every cycle (for `clamd`: PING, VERSION and a scan of the harmless EICAR test string, which must be
detected). An outage is shown in *Administration → System* and by the readiness check "evidence_scanner" until a
check succeeds again; "evidence_quarantine" counts files whose scan retries are exhausted.

## 6. Start

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build
sudo docker compose -f docker-compose.yml -f docker-compose.lan.yml ps     # db, backend, worker, frontend, clamav: Up
```

What runs:

| Service | Purpose | Exposed |
|---|---|---|
| `frontend` | nginx: HTTPS (TLS 1.2/1.3, HSTS), HTTP→HTTPS redirect, static UI, reverse proxy to the API | 443, 80 |
| `backend` | FastAPI; applies database migrations at start | internal only |
| `worker` | scheduled jobs: Action Center alert rules (every 5 min), scanner health check, evidence re-scans, evidence file reconciliation; job history in *Action Center → jobs API* | internal only |
| `db` | PostgreSQL 16, volume `db-data` | internal only |
| `clamav` | ClamAV daemon (optional profile) | internal only |

## 7. First sign-in and hardening checklist

1. Open `https://factorypulse.werk.local` – the browser must show a valid certificate (no warning).
2. Sign in with `FP_INITIAL_ADMIN_EMAIL`; set a new personal password.
3. **Remove `FP_INITIAL_ADMIN_PASSWORD` from `.env`** and restart (`docker compose … up -d`).
4. Create the second administrator and the named user accounts (Administration → Users & roles). Use personal
   accounts only – no shared logins (audit trail, BetrVG agreement).
5. Run the deployment verification (17 checks, read-only apart from one test report):

   ```bash
   FP_VERIFY_EMAIL=it-admin@werk.example ops/verify.sh https://factorypulse.werk.local
   ```

   Expected: `17/17 checks passed`. Warnings for `last_backup` disappear after the first backup.
6. Administration → System: all checks `ok` (`alert_worker` turns `ok` within 5 minutes after the start).
7. Import the first production data (Data import) and check the dashboards with the production manager.

Security properties that are active automatically: Argon2id password hashing, session cookies with
`Secure; HttpOnly; SameSite=Strict`, CSRF tokens on every change, account lock-out after 5 failures, login rate
limiting, role checks on every API route, security headers (CSP, X-Frame-Options, HSTS), private file storage
without public URLs, audit log with the real client IP (X-Forwarded-For from clients is ignored by the proxy).

## 8. Backups (mandatory before go-live)

```bash
sudo crontab -e
15 2 * * *  cd /opt/factorypulse/factorypulse-ai && FP_BACKUP_COPY_DIR=/mnt/nas/factorypulse ops/backup.sh >> /var/log/factorypulse-backup.log 2>&1
```

A backup contains the database (without sessions), import files, reports and all evidence files incl. the
quarantine directory, with SHA-256 checksums. **Test a restore** on a second machine before go-live and then at
least quarterly (`docs/OPERATIONS.md`, section 3). Backups are not encrypted by the application – store the
off-site copies on encrypted storage.

## 9. Updates

1. `ops/backup.sh "before update to vX.Y"`
2. Replace the application directory with the new release (keep `.env` and `tls/`).
3. `docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build` – migrations run automatically and
   are additive (existing records are preserved; v0.3.0 → v0.3.1 was verified with existing data).
4. `ops/verify.sh https://factorypulse.werk.local`

**Upgrading from v0.3.0:** v0.3.0 loaded the synthetic demo dataset into an empty database by default. If the
banner "Synthetic demo data" is visible on a customer system, an administrator must remove the demo data
(temporarily set `FP_ENABLE_DEMO_TOOLS=true`, Administration → Data → *Delete all data*, then remove the setting)
**before** importing real data. Existing evidence files from v0.3.0 are quarantined (`not_scanned`) after the
update; with a scanner configured the worker scans them automatically, otherwise an administrator releases them.

## 10. Operation

- Logs: `docker compose logs -f backend worker` (every request has a request ID).
- Monitoring: `GET https://factorypulse.werk.local/api/health/ready` (HTTP 503 when not ready) from the company
  monitoring (PRTG, Checkmk, Zabbix).
- Patch the host OS monthly; rebuild the images with each FactoryPulse release (`--pull` for new base images).

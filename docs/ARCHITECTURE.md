# Architecture

```
 Browser (React SPA, DE/EN)
        │  HTTPS (TLS terminated by your reverse proxy)
        ▼
 ┌──────────────┐  /api/*   ┌───────────────────────────────┐      ┌──────────────┐
 │ nginx        │──────────▶│ FastAPI (uvicorn)              │─────▶│ PostgreSQL 16│
 │ static build │           │  api/  → services/ → models    │ SQL  │  (Alembic)   │
 │ CSP headers  │           │  pandas · numpy · scikit-learn │      └──────────────┘
 └──────────────┘           │  WeasyPrint · openpyxl         │
                            └──────────────┬────────────────┘
                                           │ private volume /data
                                           ▼
                                uploads/ (uuid names) · reports/
```

There are no external services: no paid APIs, no external AI calls, no email provider.

## Repository layout

```
backend/
  app/
    main.py              FastAPI app, middleware (security headers), error handlers, startup seeding
    config.py            settings from FP_* environment variables
    db.py, models.py     SQLAlchemy 2 models (normalized schema)
    api/                 routers: analytics, imports, exports, anomalies, reports, admin(settings/demo/audit)
    services/
      kpi.py             KPI engine (pure functions, unit tested)
      data_access.py     filter model + DataFrame loaders
      analytics.py       breakdowns, Pareto, period comparison, high-downtime detection
      importer.py        parse → map → validate → commit pipeline
      anomaly.py         statistical anomaly detection
      reports/           builder (data), pdf (WeasyPrint+Jinja2+matplotlib SVG), excel (openpyxl charts), i18n
      demo_data.py       deterministic synthetic dataset + injected anomalies
      sanitize.py        spreadsheet formula-injection protection
      settings_store.py  per-company settings + audit log
    templates/report.html
  alembic/               migrations 0001–0005 (0004: job runs, search document, evidence quarantine; 0005: scanner health, file-op outbox)
  scripts/               sample file generator
  tests/                 pytest suite (schema created via Alembic)
frontend/
  src/
    lib/                 api client, react-query hooks, filters context, i18n (DE/EN), formatting
    components/          layout, filter bar, KPI card, data table, charts helpers, shadcn-style ui/
    pages/               Overview, Production, Quality, Downtime, Insights, Reports, Import, Settings
  e2e/                   Playwright tests
  nginx/                 http.conf (localhost/demo), tls.conf (LAN HTTPS), shared app.conf + security headers, entrypoint
  Dockerfile
sample_data/             synthetic import examples
docs/                    this documentation, OpenAPI spec, screenshots
docker-compose.yml
```

## Data model

```
companies 1─┬─* production_lines 1─* machines
            ├─* products
            ├─* shifts
            ├─* production_records *─1 line / machine / shift / product / import_job
            │        1─* downtime_events   (reason, is_planned, minutes)
            │        1─* defect_events     (category, quantity)
            ├─* import_jobs   (file hash, mapping, validation summary, status)
            ├─* report_jobs   (type, period, filters, file)
            ├─* app_settings  (JSON per key)
            └─* audit_log
```

- **Natural key:** `production_records` is unique on (company, date, machine, shift, product). This underpins duplicate protection.
- **CHECK constraints:** non-negative quantities and times; `good + defective = actual` when both are present; positive ideal cycle time.
- **Indexes:** company+date, line+date, machine+date, shift, product, and all foreign keys.
- **Event tables:** downtime and defect events break a record down by reason or category. Writers (importer, demo generator) keep event sums equal to the record totals.
- **Tenant readiness:** `company_id` is on every business table, and every query filters by it. Authentication and per-tenant authorization do **not** exist yet.

## Request flow examples

- **Dashboards:** filters → `load_records()` (one SQL query with joins) → pandas → `kpi.compute_kpis` / `kpi.aggregate` → JSON. Typical response time is under 300 ms for 2,500 records. Aggregation runs in memory, which is appropriate up to a few hundred thousand records. Beyond that, pre-aggregated tables or SQL-side aggregation would be the next step.
- **Import:**
  1. `upload` stores the file under a random name and records its SHA-256, then returns a preview and suggested mapping.
  2. `validate` re-reads the file and returns a report; nothing is written.
  3. `commit` re-validates on the server (client results are never trusted), then creates master data, records and events in **one transaction**. On any exception it rolls back and marks the job `failed`.
  4. `revert` deletes the import's records.
- **Reports:**
  1. `builder.build` collects KPIs, the comparison, breakdowns, Paretos and anomalies (with 60 days of baseline history).
  2. `pdf.render_pdf` (Jinja2 → HTML with inline SVG charts → WeasyPrint) or `excel.render_xlsx` renders the file.
  3. The file is written to the private report directory and a `report_jobs` row is created; downloads go through the API.

## Frontend

- React 18 + TypeScript + Vite, Tailwind CSS, shadcn/ui-style components on Radix primitives, Recharts, Lucide icons.
- TanStack Query for fetching, with loading, error and empty states. Global filters live in context; the default is the last 30 days of available data.
- Lightweight i18n context. TypeScript enforces that the German dictionary covers every key. Numbers and dates are formatted with `Intl` for de-DE / en-GB.
- Pages are code-split. The font (Inter) is self-hosted, so no Google Fonts requests are made, which matters for GDPR compliance.

## Design decisions

- **Rates over raw counts for anomalies:** days with different numbers of shifts stay comparable.
- **Robust statistics (median/MAD):** earlier anomalies do not distort the baseline.
- **Synchronous report generation:** a report takes 1–4 s. A job table already exists, so a background worker or scheduler can be added later without schema changes.
- **pandas over SQL aggregation:** formulas stay readable and testable in one place (`kpi.py`).


## v0.3.1 runtime components

```
browser ──HTTPS──> frontend (nginx: TLS, HSTS, CSP, overwrites X-Forwarded-For)
                     └─/api─> backend (FastAPI, read/write API; migrations at start)
                                   │                          ▲
                                   ▼                          │ same image
                               PostgreSQL 16  <──────────── worker (python -m app.worker)
                                   ▲                         ├─ alerts.evaluate  every FP_ALERT_INTERVAL_SECONDS
                                   │                         ├─ evidence.health   (explicit scanner check, persisted)
                                   │                         ├─ evidence.scan     (quarantine backlog; skipped while unavailable)
                                   │                         └─ evidence.reconcile (file-move outbox, misplaced/orphaned files)
                               clamav (optional, clamd INSTREAM on the internal network)
```

- **Clock:** `app/clock.py` is the only source of "now". Business dates ("today", overdue, month boundaries)
  are derived from UTC via `FP_TIMEZONE`; day bounds are converted to UTC instants (DST-correct, SQLite-safe).
  Tests freeze or move the clock.
- **Jobs:** `app/services/jobs.py` – one transaction per attempt, `job_runs` log, exponential backoff, PostgreSQL
  advisory lock per (job, company). Dashboard requests never write alerts.
- **Search:** `investigations.search_text` = NFKC + casefold + ä/ö/ü transliteration of number, title and
  description (maintained by an ORM hook); `LIKE` with escaped wildcards, `pg_trgm` GIN index on PostgreSQL.
- **Evidence:** `/data/evidence/.staging` → (atomic rename) `/data/evidence/quarantine` → scanner → `/data/evidence`.
  Access rules and scanner health in `app/services/evidence_scan.py`; crash-safe file placement (transactional
  outbox `evidence_file_ops`, idempotent `place()`, reconciliation, SHA-256 integrity) in
  `app/services/evidence_store.py`. The database decides access; the directory is only storage.

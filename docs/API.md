# API Reference

The base path is `/api`. Interactive documentation (Swagger) is available at `/api/docs`, ReDoc at `/api/redoc`, and the machine-readable spec at `/api/openapi.json` (a copy is in [openapi.json](openapi.json)).

**Conventions**

- Ratios are decimal fractions (`0.853` = 85.3 %). `null` means *not computable from the available data*.
- Dates use the ISO format `YYYY-MM-DD`.
- Errors return `{"detail": "..."}`. Validation errors (422) return `{"detail": "Invalid request", "errors": [{"field", "message"}]}`.
- **Authentication:** `POST /api/auth/login` sets an HttpOnly session cookie and returns a `csrf_token`. Send it as the `X-CSRF-Token` header on every POST/PUT/PATCH/DELETE. Errors: 401 `not_authenticated`, 403 `forbidden` / `csrf_failed` / `password_change_required` / `reauth_failed`. Error bodies look like `{"detail": "...", "code": "..."}`, and unexpected errors include a `request_id`.
- The **required permission** of each endpoint is listed below (viewer < analyst < admin).

**Common filter query parameters** (on all analytics and export endpoints):
`start_date`, `end_date`, `line_id`, `machine_id`, `shift_id`, `product_id`. The `*_id` parameters may be repeated, e.g. `?line_id=1&line_id=2`.

## Authentication & users
| Method | Path | Permission | Description |
|---|---|---|---|
| POST | `/api/auth/login` | public | `{email, password}` → user, permissions, `csrf_token`; sets cookie `fp_session` |
| POST | `/api/auth/logout` | session | ends the current session |
| POST | `/api/auth/logout-all` | session | ends all sessions of the user |
| GET | `/api/auth/me` | session | current user, permissions, CSRF token, session expiry |
| POST | `/api/auth/change-password` | session | `{current_password, new_password}`; revokes other sessions |
| GET / POST | `/api/users` | `user.manage` (admin) | list users / create a user with a temporary password |
| PATCH | `/api/users/{id}` | admin | `{display_name?, role?, is_active?, unlock?}` |
| POST | `/api/users/{id}/reset-password` | admin | `{temporary_password}` |

## Administration (admin)
| Method | Path | Description |
|---|---|---|
| GET | `/api/health/ready` | public readiness (503 when not ready) |
| GET | `/api/admin/system` | detailed checks, version, build (v0.3.3 evidence checks: `evidence_scanner` from the persisted health check, `evidence_quarantine`, `evidence_infected`, `evidence_storage`) |
| GET | `/api/admin/evidence/status` | v0.3.3: scanner label, persisted health (`status` healthy/unavailable/unknown, `unavailable_since`, `consecutive_failures`, `last_error`), `health_stale`, quarantine counts incl. `retries_exhausted`, storage consistency (`ops_pending`, `ops_failed`, `misplaced`, `missing`, `orphaned`), last health/scan/reconcile runs |
| POST | `/api/admin/evidence/scanner/check` | v0.3.3: run the explicit scanner health check now; if healthy, scan the backlog and reconcile files (audited `evidence.scanner_checked`) |
| POST | `/api/admin/evidence/reconcile` | v0.3.3: reconcile evidence files with the database now (audited `evidence.reconciled`) |
| GET / POST | `/api/admin/backups` | list / create (`{note?}`) |
| GET | `/api/admin/backups/{name}/download` | `.tar` of the backup directory |
| POST | `/api/admin/backups/{name}/restore` | `{password, confirm: "RESTORE"}` – safety backup first, signs everybody out |
| POST | `/api/admin/backups/{name}/delete` | `{password, confirm: "DELETE"}` |
| POST | `/api/admin/demo/load` | `{password, confirm: "RELOAD"}` – only with `FP_ENABLE_DEMO_TOOLS` |
| POST | `/api/admin/data/delete` | `{password, confirm: "DELETE"}` – only with `FP_ENABLE_DEMO_TOOLS` |
| GET | `/api/audit-log?limit=&event=` | audit trail incl. user e-mail and client IP |

Permissions of the remaining endpoints: analytics, exports, meta, anomaly detection, report list and download, and settings read require `viewer`; import read and write and report generation and deletion require `analyst`; settings write and import revert (`{confirm: "REVERT"}`) require `admin`.

## System & master data
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness and database check |
| GET | `/api/meta` | Company (incl. `is_synthetic`), lines, machines, shifts, products, available data range |

## Analytics
| Method | Path | Description |
|---|---|---|
| GET | `/api/kpis` | KPI summary. Each KPI is `{value, unit, records_used, records_total, coverage}`; `oee.components` holds A/P/Q |
| GET | `/api/overview` | KPIs + `daily`, `by_line`, `by_shift`, `downtime_by_reason` |
| GET | `/api/production/breakdown?dimension=day\|week\|line\|machine\|shift\|product` | Grouped KPI table |
| GET | `/api/production/comparison` | Selected period vs. the preceding period of equal length |
| GET | `/api/quality` | Quality KPIs, daily trend, breakdowns, defect `pareto` |
| GET | `/api/downtime` | Downtime KPIs, `pareto`, `by_machine` (with `is_high_downtime`), line/shift, daily trends |
| GET | `/api/downtime/events?search=&sort=date\|minutes\|reason\|machine\|line\|shift&order=asc\|desc&page=&page_size=` | Paginated events |

Example:
```bash
curl 'http://localhost:8080/api/kpis?start_date=2026-09-01&end_date=2026-09-30&line_id=1'
```
```json
{"oee": {"value": 0.7405, "unit": "ratio", "records_used": 820, "records_total": 820, "coverage": 1.0,
         "components": {"availability": 0.906, "performance": 0.829, "quality": 0.985}}, "...": "..."}
```

## Data import
| Method | Path | Description |
|---|---|---|
| GET | `/api/imports/fields` | Target field catalogue |
| GET | `/api/imports/template?format=xlsx\|csv` | Template file |
| POST | `/api/imports/upload` (multipart `file`, optional `sheet`, `header_row`) | Stores the file privately and returns `job`, `columns`, `preview`, `suggested_mapping`, `sheets`, `already_imported` |
| POST | `/api/imports/{id}/validate` (body: `ImportOptions`) | Validation report; no data written |
| POST | `/api/imports/{id}/commit` (body: `{options, import_valid_rows_only}`) | Transactional import. Returns 422 with a summary if errors exist and `import_valid_rows_only` is false; 409 if the identical file was already imported |
| POST | `/api/imports/{id}/revert` | Deletes the records created by the import |
| GET | `/api/imports`, `/api/imports/{id}` | History and details (audit trail) |

`ImportOptions`:
```json
{"mapping": {"date": "Datum", "line": "Linie", "machine": "Maschine", "shift": "Schicht", "product": "Artikelnummer",
             "actual_qty": "Istmenge", "good_qty": "Gutteile", "defective_qty": "Ausschuss",
             "planned_production_minutes": "Planbelegungszeit", "operating_minutes": "Laufzeit",
             "downtime_minutes": "Stillstandszeit", "downtime_reason": "Stillstandsgrund", "ideal_cycle_time": "Taktzeit"},
 "sheet": null, "header_row": 1, "number_format": "auto", "ideal_cycle_time_unit": "seconds",
 "skip_existing_duplicates": true}
```

Validation issue codes: `missing_required`, `invalid_number`, `invalid_integer`, `negative_value`, `invalid_date`, `quantity_mismatch`, `good_exceeds_actual`, `time_inconsistent`, `operating_exceeds_planned`, `minutes_exceed_day`, `planned_downtime_exceeds_scheduled`, `nonpositive_cycle_time`, `duplicate_in_file`, `duplicate_existing`, `machine_line_conflict`, `text_too_long`. Warnings: `derived_value`, `performance_implausible`, `reason_without_downtime`, `downtime_without_reason`, `output_without_runtime`.

## Exports (formula-injection safe)
| Method | Path |
|---|---|
| GET | `/api/exports/production?dimension=…&format=csv\|xlsx` |
| GET | `/api/exports/downtime-events?format=csv\|xlsx` |
| GET | `/api/exports/quality?format=csv\|xlsx` |

CSV files are UTF-8 with BOM and use `;` as the separator, so they open directly in German Excel.

## Anomaly detection
| Method | Path | Description |
|---|---|---|
| POST | `/api/anomalies/detect` body `{filters: {start_date, end_date, line_ids, machine_ids, shift_ids, product_ids}, config?: AnomalyConfig}` | Returns `status` (`ok`/`insufficient_data`), `message`, `anomalies[]`, `stats`, and `ground_truth_validation` (synthetic data only) |
| GET | `/api/anomalies/series?level=machine\|line&entity_id=&metric=` | Daily values with baseline and threshold |

Each anomaly contains `date, level, entity_code, entity_name, line_code, metric, observed, baseline, baseline_spread, baseline_days, z_score, severity, method, explanation, explanation_de, recorded_context[], status="detected_unconfirmed"`.

## Reports
| Method | Path | Description |
|---|---|---|
| POST | `/api/reports` body `{report_type: daily\|weekly\|monthly\|quality\|downtime, format: pdf\|xlsx, start_date, end_date?, filters, language: de\|en}` | Generates the report synchronously and returns the job. Daily/weekly/monthly use `start_date` as the reference date; quality/downtime require `end_date` (max. 366 days) |
| GET | `/api/reports` | History |
| GET | `/api/reports/{id}/download` | File download |
| DELETE | `/api/reports/{id}` | Deletes the job and its file |

## Settings
| Method | Path | Description |
|---|---|---|
| GET | `/api/settings` (viewer) | `company_name`, `default_language`, `anomaly_config`, read-only system info |
| PUT | `/api/settings` (admin) | update the values above |

## Action Center (v0.3)

Permissions: `action.read` (viewer+), `action.write` (analyst+), `action.manage` (admin). Object-level rules are enforced on the server in addition to the role:

- **Editing an investigation** (PATCH, transitions to `in_progress` / `pending_verification` / `resolved` / `open`) is allowed for an admin, or for an analyst who owns or created it.
- **Closing, reopening, assigning other users and changing rules** are admin-only.
- **Comments, evidence, new actions and KPI recordings** are allowed for any analyst/admin while the investigation is not closed.
- **Action status** can be changed by the action owner, the investigation editors or an admin. **Verification** is limited to the investigation owner or an admin.

Error codes: `infected`, `quarantined` (403), `acknowledge_required`, `already_verified`, `no_scanner`, `already_scanned` (409), `invalid_transition`, `actions_required`, `actions_open`, `resolution_required`, `reason_required`, `notes_required`, `verification_required`, `invalid_assignee`, `machine_line_mismatch` (422), `closed` (409), `duplicate_anomaly` (409, includes `investigation_id`, `number`), `forbidden` (403), `file_type` / `file_content` (415), `file_size` (413), `overlapping_periods` / `invalid_period` (422).

### Investigations
| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/api/investigations` | read | Filtering, sorting and pagination run in the database (v0.3.1; tested with 50,000 investigations). `search` matches ID, title and description case-insensitively with Unicode normalisation (NFKC + case folding: `ÖLFILM` = `ölfilm`, `Straße` = `STRASSE`) and German transliteration (`oelfilm` finds `Ölfilm`); `%` and `_` are literal characters. `status` (repeatable), `severity`, `category`, `line_id`, `machine_id`, `owner_id`, `unassigned`, `overdue`, `open_only`, `start_date`/`end_date` (creation date), `sort` (`number, created_at, due_date, severity, status, updated_at, title`), `order`, `page`, `page_size` (≤ 200) → `{total, page, page_size, rows}` |
| POST | `/api/investigations` | write | `{title, description, category, severity, line_id, machine_id?, owner_id?, due_date?, anomaly_id?, anomaly_config?}` → 201. If `anomaly_id` is given, the server re-detects the anomaly, takes line/machine from it, stores an immutable evidence snapshot and rejects duplicates. Analysts may only assign themselves. The due date defaults to the rule for the severity. |
| POST | `/api/investigations/anomaly-prefill` | write | `{anomaly_id, config?, lang}` → `{duplicate}` or `{anomaly, proposal}`. Nothing is saved. |
| GET | `/api/investigations/{id}` | read | Detail incl. `allowed_transitions`, `permissions`, `actions`, `anomaly_links[].snapshot` |
| PATCH | `/api/investigations/{id}` | write + editor | `title, description, category, severity, machine_id / clear_machine, owner_id / clear_owner, due_date, resolution_summary`. An analyst can take ownership of an unassigned investigation. |
| POST | `/api/investigations/{id}/transition` | write + editor (admin for `closed`/`reopened`) | `{to_status, note?, resolution_summary?}` |
| GET/POST | `/api/investigations/{id}/comments` | read / write | `{body}`; comments are immutable |
| GET | `/api/investigations/{id}/timeline` | read | Ordered events: `created, assigned, status_changed, due_date_changed, updated, comment, action_created, action_updated, action_status_changed, action_verified, attachment_added, attachment_removed, attachment_scanned, attachment_released, kpi_comparison_recorded` |

Workflow: `open → in_progress | closed`, `in_progress → pending_verification | open | closed`, `pending_verification → resolved | in_progress`, `resolved → closed | reopened`, `closed → reopened`, `reopened → in_progress | closed`. `pending_verification` needs ≥ 1 action and none open/in progress. `resolved` / `closed` need a resolution summary (≥ 10 characters). `reopened` needs a note.

### Corrective actions
| Method | Path | Permission | Description |
|---|---|---|---|
| GET/POST | `/api/investigations/{id}/actions` | read / write | `{title, description?, owner_id?, due_date?, priority}`. Assigning someone other than yourself requires editor rights. |
| PATCH | `/api/actions/{id}` | write | Field edits (editor) and/or `status` with `completion_notes` (required for `done`), `verification_result` (`effective, partially_effective, not_effective`, required for `verified`), `verification_notes` |
| GET | `/api/actions/mine?include_finished=` | read | Actions assigned to the caller, grouped `overdue, due_soon (≤ 7 days), later, awaiting_verification, finished` |

Action workflow: `open → in_progress | done | cancelled`, `in_progress → open | done | cancelled`, `done → verified | in_progress`, `verified → in_progress` (admin), `cancelled → open` (admin).

### Evidence
| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/api/investigations/{id}/attachments` | read | Active attachments (filename, type, size, SHA-256, uploader) plus the malware-scan state: `scan_status` (`pending`, `clean`, `infected`, `failed`, `not_scanned`), `scanner`, `scanned_at`, `released_at`, `quarantined`, and what the **current** user may do: `downloadable`, `requires_acknowledgement`, `can_release`; since v0.3.3 also `scan_attempts` and `retries_exhausted` (the scanner could not scan the file within `FP_EVIDENCE_SCAN_MAX_ATTEMPTS`). `scan_detail` and `release_note` are only returned to administrators. |
| POST | `/api/investigations/{id}/attachments` | write | multipart `file` + `description?`. PDF, PNG, JPEG, CSV or XLSX only, max. `FP_MAX_EVIDENCE_MB` (default 10). The extension, magic bytes and CSV text content are checked. The file is stored in quarantine (atomically) and scanned immediately when a scanner is configured and not known to be unavailable; the response contains the scan result. If the scanner is unreachable the file stays `pending` (no attempt used). |
| GET | `/api/attachments/{id}/download` | read | Authenticated download (`Content-Disposition: attachment`, `nosniff`, CSP `sandbox`). Files are never served from a public URL. `infected` → 403 `infected` for everybody. Unverified files (not `clean` and not released) → 403 `quarantined` for viewers/analysts; administrators get 409 `acknowledge_required` unless `?acknowledge_risk=true` (the download is then audited as `evidence.unverified_download`). v0.3.3: the file content must match the recorded SHA-256, otherwise 409 `integrity`. |
| POST | `/api/attachments/{id}/release` | admin | `{note}` (≥ 10 characters): releases an unverified, non-infected file from quarantine after manual checking (audited, timeline event `attachment_released`). Infected files → 403. |
| POST | `/api/attachments/{id}/rescan` | admin | Scans a quarantined file again now with a fresh set of attempts (audited `evidence.rescanned`; 409 `no_scanner` without a configured scanner, 503 `scanner_unavailable` while the scanner health is unavailable). |
| POST | `/api/attachments/{id}/delete` | write (uploader or admin) | `{reason}`. Soft delete: the record and file are kept for audit, and the removal appears in the timeline. |

### KPI comparison (observed, not causal)
| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/api/investigations/{id}/kpi-comparison` | read | `baseline_start, baseline_end, comparison_start, comparison_end` (default: 14 days up to the anomaly/creation date vs. 14 days after the last completed action), `scope=machine\|line`, `shift_id`, `product_id`. Returns per metric (`oee, availability, performance, quality, defect_rate, unplanned_downtime_minutes, total_production`) `before, after, abs_diff, pct_change, direction, coverage_*`. Counts are compared per production day. If either period has < 3 production days, `sufficient_data=false` and values are `null`. |
| POST | `/api/investigations/{id}/kpi-comparison` | write | Same parameters (JSON). Records the result in the timeline. |

### Overview, alerts and rules
| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/api/action-center/summary` | read | Filters `start_date, end_date, line_id, machine_id, severity, status, owner_id, category`, `include_repeated`. Returns KPIs, distributions, a 12-week trend, overdue actions by owner, priority issues, overdue actions and the open rule alerts, computed with SQL aggregates. **Read-only** (v0.3.1): the alerts are written by the scheduled worker job; `alert_evaluation` = `{evaluated_at, state: ok\|stale\|never\|failed, interval_seconds, last_error}` tells how current they are. "Resolved this month" uses the calendar month in `FP_TIMEZONE`. |
| POST | `/api/action-center/alerts/evaluate` | admin | Runs the alert evaluation job now (same lock and job log as the worker). Returns `{run, evaluation}`; audited. |
| GET | `/api/action-center/jobs?limit=` | admin | Recent background job runs: `job, status (running/succeeded/failed/skipped/abandoned), trigger, attempt, max_attempts, worker, started_at, finished_at, duration_ms, error, details`. |
| POST | `/api/action-center/alerts/{id}/acknowledge` | write | Marks an alert as seen. It stays until the condition is resolved. |
| GET / PUT | `/api/action-center/settings` | read / admin | `critical_unassigned_hours, critical_open_hours, pending_verification_days, action_overdue_grace_days, repeated_anomaly_count, repeated_anomaly_window_days, default_due_days{critical,high,medium,low}` |
| GET | `/api/action-center/assignees` | read | Active analysts and admins |
| GET | `/api/action-center/anomaly-links?ids=` | read | Investigations linked to anomaly IDs. `POST /api/anomalies/detect` also adds `investigation` to linked anomalies. |

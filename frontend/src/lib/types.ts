export interface Metric {
  value: number | null;
  records_used: number;
  records_total: number;
  unit: "units" | "minutes" | "ratio";
  coverage: number | null;
  components?: { availability: number | null; performance: number | null; quality: number | null };
}

export type KpiKey =
  | "total_production" | "planned_production" | "target_achievement" | "good_units" | "defective_units"
  | "defect_rate" | "quality" | "unplanned_downtime_minutes" | "planned_downtime_minutes"
  | "planned_production_minutes" | "operating_minutes" | "availability" | "performance" | "oee";

export type Kpis = Record<KpiKey, Metric> & { record_count: number };

export interface Entity { id: number; code: string; name: string }
export interface MachineEntity extends Entity { line_id: number }
export interface ShiftEntity extends Entity { start_time: string | null; end_time: string | null }

export interface Meta {
  company: { id: number; name: string; is_synthetic: boolean };
  data_range: { start: string | null; end: string | null; record_count: number };
  lines: Entity[];
  machines: MachineEntity[];
  shifts: ShiftEntity[];
  products: Entity[];
}

export interface AggRow {
  date?: string; week?: string;
  line_id?: number; line_code?: string; line_name?: string;
  machine_id?: number; machine_code?: string; machine_name?: string;
  shift_id?: number; shift_code?: string; shift_name?: string;
  product_id?: number; product_code?: string; product_name?: string;
  records: number;
  actual_qty: number | null; planned_qty: number | null; good_qty: number | null; defective_qty: number | null;
  unplanned_downtime_minutes: number | null; planned_production_minutes: number | null; operating_minutes: number | null;
  target_achievement: number | null; defect_rate: number | null; quality: number | null;
  availability: number | null; performance: number | null; oee: number | null;
  units_per_operating_hour: number | null;
}

export interface ReasonRow { reason: string; minutes: number; events: number }
export interface ParetoItem { category: string; value: number; share: number; cumulative_share: number }

export interface Overview {
  kpis: Kpis;
  daily: AggRow[];
  by_line: AggRow[];
  by_shift: AggRow[];
  downtime_by_reason: ReasonRow[];
}

export interface ComparisonRow {
  metric: string; unit: string; current: number | null; previous: number | null;
  delta: number | null; relative_change: number | null;
}
export interface Comparison {
  current_period: { start: string; end: string; records: number };
  previous_period: { start: string; end: string; records: number };
  metrics: ComparisonRow[];
}

export interface QualitySummary {
  kpis: Pick<Kpis, "total_production" | "good_units" | "defective_units" | "defect_rate" | "quality">;
  daily: AggRow[];
  by_machine: AggRow[]; by_line: AggRow[]; by_product: AggRow[]; by_shift: AggRow[];
  pareto: ParetoItem[];
  categorized_defects: number;
  uncategorized_defects: number;
}

export interface DowntimeMachineRow extends AggRow {
  downtime_ratio: number | null; is_high_downtime: boolean; fleet_median_ratio: number | null; upper_fence: number | null;
}
export interface DowntimeSummary {
  kpis: Pick<Kpis, "unplanned_downtime_minutes" | "planned_downtime_minutes" | "planned_production_minutes" | "operating_minutes" | "availability">;
  event_count: number;
  by_reason: ReasonRow[];
  pareto: ParetoItem[];
  by_machine: DowntimeMachineRow[];
  by_line: (AggRow & { downtime_ratio: number | null })[];
  by_shift: (AggRow & { downtime_ratio: number | null })[];
  daily: { date: string; unplanned_downtime_minutes: number | null; planned_production_minutes: number | null; downtime_ratio: number | null; availability: number | null }[];
  daily_by_reason: { date: string; reason: string; minutes: number }[];
}

export interface DowntimeEventRow {
  id: number; date: string; line_code: string; machine_code: string; machine_name: string;
  shift_code: string; product_code: string; reason: string; is_planned: boolean; minutes: number;
}
export interface Paged<T> { total: number; page: number; page_size: number; rows: T[] }

export type AnomalyMetric = "defect_rate" | "downtime_ratio" | "target_achievement" | "performance";
export type Severity = "critical" | "high" | "medium" | "low";
export interface RecordedContext {
  kind: "downtime_reasons" | "defect_categories"; unit: "min" | "units";
  items: { name: string; value: number }[]; text: string;
}
export interface Anomaly {
  id: string; date: string; level: "machine" | "line"; entity_id: number; entity_code: string; entity_name: string;
  line_code: string; metric: AnomalyMetric; metric_label: string; category: string;
  observed: number; baseline: number; baseline_spread: number; baseline_days: number; z_score: number;
  severity: Severity; method: string; explanation: string; explanation_de?: string; recorded_context: RecordedContext[];
  status: string;
  /** Present when an Action Center investigation is already linked to this anomaly. */
  investigation?: { id: number; number: string; status: string };
}
export interface AnomalyConfig {
  window: number; min_history: number; z_threshold: number; high_z: number; critical_z: number;
  metrics: AnomalyMetric[]; levels: ("machine" | "line")[];
  min_abs_change: Record<AnomalyMetric, number>;
  use_isolation_forest: boolean; isolation_forest_contamination: number;
}
export interface GroundTruth {
  date: string; entity: string; level: string; metric: string; description: string;
  detected: boolean; matched_metrics: string[]; best_severity: Severity | null;
}
export interface DetectionResult {
  status: "ok" | "insufficient_data";
  message: string | null;
  anomalies: Anomaly[];
  stats: { production_days?: number; scored_points?: number; by_severity?: Record<Severity, number>; total?: number };
  config?: AnomalyConfig;
  method_note?: string;
  ground_truth_validation?: GroundTruth[];
}
export interface SeriesPoint { date: string; value: number | null; baseline: number | null; threshold: number | null; z_score: number | null }

export interface ImportField { key: string; label: string; type: string; required: boolean; description: string }
export interface ImportJob {
  id: number; original_filename: string; file_type: string; file_size: number; status: string; sheet_name: string | null;
  total_rows: number | null; valid_rows: number | null; error_rows: number | null; imported_rows: number | null;
  skipped_rows: number | null; created_at: string | null; committed_at: string | null;
}
export interface UploadResponse {
  job: ImportJob; columns: string[]; preview: Record<string, string | null>[]; row_count: number;
  sheets: string[]; sheet: string | null; delimiter: string | null; encoding: string | null;
  suggested_mapping: Record<string, string | null>; fields: ImportField[]; already_imported: ImportJob | null;
}
export interface ImportIssue {
  row: number; field: string | null; column: string | null; value: string | null;
  severity: "error" | "warning"; code: string; message: string;
}
export interface ValidationSummary {
  total_rows: number; valid_rows: number; error_rows: number; warning_rows: number; duplicate_existing_rows: number;
  error_count: number; warning_count: number; issue_counts: Record<string, number>;
  missing_by_field: Record<string, number>;
  new_master_data: { lines: string[]; machines: string[]; shifts: string[]; products: string[] };
  date_range: { start: string | null; end: string | null };
  issues: ImportIssue[]; issues_truncated: boolean;
  already_imported: ImportJob | null; can_commit: boolean;
}
export interface ImportOptions {
  mapping: Record<string, string | null>;
  sheet?: string | null;
  header_row: number;
  number_format: "auto" | "de" | "en";
  ideal_cycle_time_unit: "seconds" | "minutes";
  skip_existing_duplicates: boolean;
}

export type ReportType = "daily" | "weekly" | "monthly" | "quality" | "downtime";
export interface ReportJob {
  id: number; report_type: ReportType; format: "pdf" | "xlsx"; period_start: string; period_end: string;
  filters: Record<string, unknown> | null; language: "en" | "de"; status: string; download_name: string | null;
  file_size: number | null; error: string | null; created_at: string | null; completed_at: string | null;
}

export interface SettingsPayload {
  company_name: string; company_is_synthetic: boolean; default_language: "de" | "en";
  anomaly_config: AnomalyConfig;
  system: { environment: string; max_upload_mb: number; max_evidence_mb?: number; evidence_scanner?: "none" | "clamd" | "command"; max_import_rows: number; allowed_file_types: string[]; external_ai_apis: boolean; database: string; demo_tools_enabled: boolean; timezone: string; version: string; build: string };
}
export interface AuditEntry { id: number; event: string; entity_type: string | null; entity_id: number | null; details: Record<string, unknown> | null; created_at: string | null }

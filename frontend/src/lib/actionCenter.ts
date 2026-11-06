import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type InvStatus = "open" | "in_progress" | "pending_verification" | "resolved" | "closed" | "reopened";
export type Severity = "low" | "medium" | "high" | "critical";
export type Category = "equipment_failure" | "production_loss" | "quality_issue" | "excessive_downtime"
  | "process_deviation" | "safety_observation" | "other";
export type ActionStatus = "open" | "in_progress" | "done" | "verified" | "cancelled";
export type VerificationResult = "effective" | "partially_effective" | "not_effective";

export const STATUSES: InvStatus[] = ["open", "in_progress", "pending_verification", "resolved", "closed", "reopened"];
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];
export const CATEGORIES: Category[] = ["equipment_failure", "production_loss", "quality_issue", "excessive_downtime",
  "process_deviation", "safety_observation", "other"];
export const OPEN_STATUSES: InvStatus[] = ["open", "in_progress", "pending_verification", "reopened"];

export interface UserRef { id: number; display_name: string; email: string; role: string; is_active: boolean }
export interface AnomalyLinkSummary {
  anomaly_key: string; date: string; level: "machine" | "line"; entity_code: string; metric: string;
  severity: Severity | null; observed: number | null; baseline: number | null;
  snapshot?: Record<string, unknown> & { explanation?: string; explanation_de?: string; z_score?: number; baseline_days?: number;
    detection_config?: Record<string, unknown>; captured_at?: string; recorded_context?: { kind: string; unit: string; items: { name: string; value: number }[] }[] };
  linked_at?: string;
}
export interface ActionItem {
  id: number; code: string; title: string; description: string; owner: UserRef | null; due_date: string | null;
  priority: Severity; status: ActionStatus; overdue: boolean; days_overdue: number | null;
  completed_at: string | null; completion_notes: string | null; verification_result: VerificationResult | null;
  verification_notes: string | null; verified_at: string | null; verified_by: UserRef | null;
  created_at: string; updated_at: string;
  investigation: { id: number; number: string; title: string; status: InvStatus; severity: Severity; line_code: string; machine_code: string | null };
  allowed_transitions: ActionStatus[]; can_edit: boolean;
}
export interface Investigation {
  id: number; number: string; title: string; category: Category; severity: Severity; status: InvStatus;
  source: "manual" | "anomaly"; line: { id: number; code: string; name: string };
  machine: { id: number; code: string; name: string } | null; owner: UserRef | null; created_by: UserRef | null;
  created_by_label: string; due_date: string | null; overdue: boolean; days_overdue: number | null;
  created_at: string; updated_at: string; status_changed_at: string; resolved_at: string | null; closed_at: string | null;
  reopen_count: number; is_synthetic: boolean;
  actions_summary: { total: number; active: number; overdue: number; done: number };
  anomaly: AnomalyLinkSummary | null;
}
export interface InvestigationDetail extends Investigation {
  description: string; resolution_summary: string | null; allowed_transitions: InvStatus[];
  permissions: { can_edit: boolean; can_contribute: boolean; can_assign_any: boolean; can_manage: boolean };
  actions: ActionItem[]; anomaly_links: AnomalyLinkSummary[];
}
export interface TimelineEvent { id: number; type: string; actor: string; actor_id: number | null; data: Record<string, unknown> | null; created_at: string }
export type ScanStatus = "pending" | "clean" | "infected" | "failed" | "not_scanned";
export interface Attachment {
  id: number; filename: string; content_type: string; size: number; sha256: string; description: string | null; uploaded_by: string;
  uploaded_by_id: number | null; created_at: string;
  scan_status: ScanStatus; scanner: string | null; scanned_at: string | null; scan_detail: string | null;
  released_at: string | null; release_note: string | null; quarantined: boolean; downloadable: boolean;
  requires_acknowledgement: boolean; can_release: boolean;
  /** v0.3.3: the scanner could not scan this file within FP_EVIDENCE_SCAN_MAX_ATTEMPTS attempts */
  retries_exhausted: boolean; scan_attempts: number;
}
export interface AlertEvaluation { evaluated_at: string | null; state: "ok" | "stale" | "never" | "failed"; interval_seconds: number; last_error: string | null }
export interface Alert {
  id: number; rule: string; severity: Severity; investigation_id: number | null; action_id: number | null; machine_id: number | null;
  details: Record<string, unknown>; first_detected_at: string; last_seen_at: string; acknowledged_at: string | null;
}
export interface Summary {
  generated_at: string; timezone: string; today: string;
  kpis: { open_total: number; critical_open: number; overdue_actions: number; overdue_investigations: number; in_progress: number;
    pending_verification: number; resolved_this_month: number; avg_resolution_hours: number | null; resolved_sample: number };
  by_severity: { severity: Severity; count: number }[];
  by_line: { line_code: string; count: number }[];
  by_category: { category: Category; count: number }[];
  by_status: { status: InvStatus; count: number }[];
  trend: { week: string; week_start: string; opened: number; resolved: number; open_at_end: number }[];
  overdue_by_owner: { owner: string; count: number }[];
  priority_issues: Investigation[];
  overdue_actions_list: ActionItem[];
  alerts: Alert[];
  alert_evaluation: AlertEvaluation;
}
export interface ComparisonMetric {
  metric: string; kind: "ratio" | "minutes" | "units"; before: number | null; after: number | null;
  coverage_before: number | null; coverage_after: number | null; records_before: number; records_after: number;
  lower_is_better: boolean; before_per_day?: number | null; after_per_day?: number | null;
  abs_diff: number | null; pct_change: number | null; direction: "better" | "worse" | "unchanged" | null;
}
export interface Comparison {
  metrics: ComparisonMetric[]; production_days_before: number; production_days_after: number; sufficient_data: boolean;
  min_production_days: number; disclaimer: string; baseline: { start: string; end: string }; comparison: { start: string; end: string };
  defaults: Record<string, string>; scope: "machine" | "line"; scope_label: string;
  series_before: Record<string, number | string | null>[]; series_after: Record<string, number | string | null>[];
}
export interface MyActions { today: string; timezone: string; overdue: ActionItem[]; due_soon: ActionItem[]; later: ActionItem[]; awaiting_verification: ActionItem[]; finished: ActionItem[] }
export interface Rules {
  critical_unassigned_hours: number; critical_open_hours: number; pending_verification_days: number; action_overdue_grace_days: number;
  repeated_anomaly_count: number; repeated_anomaly_window_days: number; default_due_days: Record<Severity, number>; timezone: string;
}

export interface AcFilters {
  startDate: string; endDate: string; lineId: number | null; machineId: number | null; severity: Severity | "";
  status: InvStatus | ""; ownerId: number | null; category: Category | "";
}
export const EMPTY_AC_FILTERS: AcFilters = { startDate: "", endDate: "", lineId: null, machineId: null, severity: "", status: "", ownerId: null, category: "" };

export function acParams(f: AcFilters, extra: Record<string, string | number | boolean | undefined> = {}): string {
  const p = new URLSearchParams();
  if (f.startDate) p.set("start_date", f.startDate);
  if (f.endDate) p.set("end_date", f.endDate);
  if (f.lineId) p.set("line_id", String(f.lineId));
  if (f.machineId) p.set("machine_id", String(f.machineId));
  if (f.severity) p.set("severity", f.severity);
  if (f.status) p.set("status", f.status);
  if (f.ownerId) p.set("owner_id", String(f.ownerId));
  if (f.category) p.set("category", f.category);
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== "" && v !== false) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const useAcSummary = (f: AcFilters) =>
  useQuery({ queryKey: ["ac-summary", f], queryFn: () => api<Summary>(`/api/action-center/summary${acParams(f)}`), placeholderData: keepPreviousData });

export const useInvestigations = (f: AcFilters, opts: { search: string; sort: string; order: string; page: number; pageSize: number; overdue: boolean; unassigned: boolean }) =>
  useQuery({
    queryKey: ["investigations", f, opts],
    queryFn: () => api<{ total: number; page: number; page_size: number; rows: Investigation[] }>(`/api/investigations${acParams(f, {
      search: opts.search || undefined, sort: opts.sort, order: opts.order, page: opts.page, page_size: opts.pageSize,
      overdue: opts.overdue || undefined, unassigned: opts.unassigned || undefined,
    })}`),
    placeholderData: keepPreviousData,
  });

export const useInvestigation = (id: number) =>
  useQuery({ queryKey: ["investigation", id], queryFn: () => api<InvestigationDetail>(`/api/investigations/${id}`) });
export const useTimeline = (id: number) =>
  useQuery({ queryKey: ["investigation", id, "timeline"], queryFn: () => api<TimelineEvent[]>(`/api/investigations/${id}/timeline`) });
export const useAttachments = (id: number) =>
  useQuery({ queryKey: ["investigation", id, "attachments"], queryFn: () => api<Attachment[]>(`/api/investigations/${id}/attachments`) });
export const useAssignees = () =>
  useQuery({ queryKey: ["assignees"], queryFn: () => api<UserRef[]>("/api/action-center/assignees"), staleTime: 60_000 });
export const useMyActions = (includeFinished: boolean) =>
  useQuery({ queryKey: ["my-actions", includeFinished], queryFn: () => api<MyActions>(`/api/actions/mine${includeFinished ? "?include_finished=true" : ""}`) });
export const useRules = () => useQuery({ queryKey: ["ac-rules"], queryFn: () => api<Rules>("/api/action-center/settings") });

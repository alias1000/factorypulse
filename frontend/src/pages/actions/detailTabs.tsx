import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, FileDown, FileText, Loader2, Minus, Paperclip, Plus, RotateCcw, Save, ShieldAlert, ShieldCheck, ShieldX, Trash2, Upload,
} from "lucide-react";
import { ActionCard, ActionFormDialog, useAcInvalidate } from "@/components/acActions";
import { FieldSelect } from "@/components/ac";
import { PromptDialog } from "@/components/PromptDialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useAuth } from "@/lib/auth";
import { ChartTooltip } from "@/components/ChartTooltip";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AXIS, COLORS, GRID } from "@/components/charts";
import { api, downloadFile, postJson } from "@/lib/api";
import { useAttachments, useTimeline, type Attachment, type Comparison, type ComparisonMetric, type InvestigationDetail, type ScanStatus, type TimelineEvent } from "@/lib/actionCenter";
import { useI18n, type Lang, type TKey } from "@/lib/i18n";
import { useMeta, useSettings } from "@/lib/queries";
import { fmtBytes, fmtDate, fmtDateTime, fmtNumber, fmtPct, fmtPp, fmtRel } from "@/lib/format";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- corrective actions */

export function ActionsTab({ inv }: { inv: InvestigationDetail }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const canAdd = inv.permissions.can_contribute && inv.status !== "resolved";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{t("ac.actions.illustrative")}</p>
        {canAdd && <Button size="sm" onClick={() => setOpen(true)} data-testid="add-action"><Plus /> {t("ac.actions.add")}</Button>}
      </div>
      {inv.actions.length === 0 ? <EmptyState title={t("ac.actions.empty")} hint="" /> :
        inv.actions.map((a) => <ActionCard key={a.id} a={a} canAssignOthers={inv.permissions.can_edit} />)}
      <ActionFormDialog open={open} onOpenChange={setOpen} investigationId={inv.id} canAssignOthers={inv.permissions.can_edit} />
    </div>
  );
}

/* ---------------------------------------------------------------- timeline */

function eventDetail(e: TimelineEvent, t: (k: TKey, v?: Record<string, string | number>) => string, lang: Lang): string | null {
  const d = (e.data ?? {}) as Record<string, unknown>;
  const st = (s: unknown) => (s ? t(`ac.status.${s}` as TKey) : "–");
  const ast = (s: unknown) => (s ? t(`ac.actionStatus.${s}` as TKey) : "–");
  switch (e.type) {
    case "created": return `${t(`ac.source.${d.source}` as TKey)} · ${t(`severity.${d.severity}` as TKey)} · ${t(`ac.category.${d.category}` as TKey)}`;
    case "assigned": return `${d.from ?? t("ac.unassigned")} → ${d.to ?? t("ac.unassigned")}`;
    case "status_changed": return `${st(d.from)} → ${st(d.to)}${d.note ? ` · „${d.note}“` : ""}${d.resolution_summary ? ` · ${d.resolution_summary}` : ""}`;
    case "due_date_changed": return `${fmtDate(d.from as string, lang)} → ${fmtDate(d.to as string, lang)}`;
    case "updated": return Object.keys(d).map((k) => t(`ac.field.${k}` as TKey)).join(", ");
    case "comment": return String(d.body ?? "");
    case "action_created": return `${d.title}${d.owner ? ` · ${d.owner}` : ""}${d.due_date ? ` · ${fmtDate(d.due_date as string, lang)}` : ""}`;
    case "action_updated": return `${d.title} · ${Object.keys((d.changes as object) ?? {}).join(", ")}`;
    case "action_status_changed":
    case "action_verified":
      return `${d.title}: ${ast(d.from)} → ${ast(d.to)}${d.verification_result ? ` · ${t(`ac.verification.${d.verification_result}` as TKey)}` : ""}${d.notes ? ` · ${d.notes}` : ""}`;
    case "attachment_added": return `${d.filename} (${fmtBytes(d.size as number)})`;
    case "attachment_removed": return `${d.filename} · ${d.reason}`;
    case "attachment_scanned": return `${d.filename}: ${t(`ac.evidence.scan.${d.result}` as TKey)} (${d.scanner})`;
    case "attachment_released": return `${d.filename}${d.note ? ` – ${d.note}` : ""}`;
    case "kpi_comparison_recorded": {
      const b = d.baseline as { start: string; end: string }; const c = d.comparison as { start: string; end: string };
      return `${d.scope_label}: ${fmtDate(b?.start, lang)}–${fmtDate(b?.end, lang)} vs. ${fmtDate(c?.start, lang)}–${fmtDate(c?.end, lang)}${d.sufficient_data ? "" : " · N/A"}`;
    }
    default: return null;
  }
}

export function TimelineTab({ inv }: { inv: InvestigationDetail }) {
  const { t, lang } = useI18n();
  const q = useTimeline(inv.id);
  if (q.isLoading) return <LoadingState />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const rows = [...(q.data ?? [])].reverse();
  if (rows.length === 0) return <EmptyState title={t("ac.timeline.empty")} hint="" />;
  return (
    <ol className="relative ml-2 border-l border-slate-200" data-testid="timeline">
      {rows.map((e) => {
        const detail = eventDetail(e, t, lang);
        return (
          <li key={e.id} className="mb-4 ml-4" data-event={e.type}>
            <span className={cn("absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-white",
              e.type === "status_changed" ? "bg-violet-500" : e.type.startsWith("action") ? "bg-teal-500" : e.type === "attachment_scanned" && (e.data as { result?: string } | null)?.result === "infected" ? "bg-red-600" : e.type.startsWith("attachment") ? "bg-amber-500" : e.type === "comment" ? "bg-blue-500" : "bg-slate-400")} aria-hidden />
            <div className="text-sm"><span className="font-medium text-slate-800">{e.actor}</span> <span className="text-slate-600">{t(`ac.ev.${e.type}` as TKey)}</span></div>
            {detail && <p className={cn("mt-0.5 text-sm text-slate-700", e.type === "comment" && "whitespace-pre-line rounded-md bg-slate-50 p-2")}>{detail}</p>}
            <time className="text-[11px] text-muted-foreground" dateTime={e.created_at}>{fmtDateTime(e.created_at, lang)}</time>
          </li>
        );
      })}
    </ol>
  );
}

/* ---------------------------------------------------------------- evidence */

const SCAN_STYLE: Record<ScanStatus, string> = {
  clean: "bg-emerald-50 text-emerald-700", infected: "bg-red-600 text-white", failed: "bg-amber-50 text-amber-800",
  pending: "bg-slate-100 text-slate-600", not_scanned: "bg-amber-50 text-amber-800",
};

export function ScanBadge({ a }: { a: Attachment }) {
  const { t } = useI18n();
  const released = !!a.released_at && a.scan_status !== "clean" && a.scan_status !== "infected";
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold",
      released ? "bg-emerald-50 text-emerald-700" : SCAN_STYLE[a.scan_status])} data-testid={`scan-${a.id}`} data-scan={a.scan_status}>
      {a.scan_status === "infected" ? <ShieldX className="h-3 w-3" /> : released || a.scan_status === "clean" ? <ShieldCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
      {released ? t("ac.evidence.scan.released") : t(`ac.evidence.scan.${a.scan_status}` as TKey)}
    </span>
  );
}

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.csv,.xlsx";

export function EvidenceTab({ inv }: { inv: InvestigationDetail }) {
  const { t, lang } = useI18n();
  const { me } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const invalidate = useAcInvalidate();
  const q = useAttachments(inv.id);
  const settings = useSettings();
  const maxMb = settings.data?.system.max_evidence_mb ?? 10;
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [desc, setDesc] = useState("");
  const [error, setError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      if (desc.trim()) fd.append("description", desc.trim());
      return api(`/api/investigations/${inv.id}/attachments`, { method: "POST", body: fd });
    },
    onSuccess: () => {
      toast.success(t("ac.evidence.uploaded"));
      setFile(null); setDesc(""); setError(null);
      if (fileRef.current) fileRef.current.value = "";
      qc.invalidateQueries({ queryKey: ["investigation", inv.id] });
      invalidate(inv.id);
    },
    onError: (e) => setError((e as Error).message),
  });
  const remove = async (id: number, reason: string) => {
    await postJson(`/api/attachments/${id}/delete`, { reason });
    toast.success(t("ac.detail.saved"));
    qc.invalidateQueries({ queryKey: ["investigation", inv.id] });
  };
  const tooBig = file && file.size > maxMb * 1024 * 1024;
  return (
    <div className="space-y-4">
      {inv.permissions.can_contribute && (
        <form className="rounded-lg border border-dashed p-3" onSubmit={(e) => { e.preventDefault(); setError(null); upload.mutate(); }} data-testid="evidence-form">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="ev-file">{t("ac.evidence.upload")}</Label>
              <input id="ev-file" ref={fileRef} type="file" accept={ACCEPT} className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm"
                onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); }} data-testid="evidence-file" />
            </div>
            <div className="flex min-w-[220px] flex-1 flex-col gap-1">
              <Label htmlFor="ev-desc">{t("ac.evidence.description")}</Label>
              <Input id="ev-desc" value={desc} maxLength={500} onChange={(e) => setDesc(e.target.value)} />
            </div>
            <Button type="submit" disabled={!file || !!tooBig || upload.isPending} data-testid="evidence-upload">
              {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />} {t("ac.evidence.upload")}
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">{t("ac.evidence.allowed", { mb: maxMb })}</p>
          {(error || tooBig) && <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error ?? `> ${maxMb} MB`}</div>}
        </form>
      )}
      {q.isLoading ? <LoadingState /> : q.isError ? <ErrorState error={q.error} /> : (q.data ?? []).length === 0 ? (
        <EmptyState title={t("ac.evidence.empty")} hint="" icon={Paperclip} />
      ) : (
        <ul className="divide-y rounded-md border" data-testid="evidence-list">
          {q.data!.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
              <FileText className="h-5 w-5 shrink-0 text-slate-400" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-slate-800">{a.filename}</div>
                <div className="text-[11px] text-muted-foreground">
                  {fmtBytes(a.size)} · {a.uploaded_by} · {fmtDateTime(a.created_at, lang)} · SHA-256 {a.sha256.slice(0, 12)}…
                </div>
                {a.description && <div className="text-xs text-slate-600">{a.description}</div>}
                {a.quarantined && <div className="text-[11px] text-amber-800" data-testid={`quarantine-note-${a.id}`}>{a.retries_exhausted ? t("ac.evidence.note.exhausted", { n: a.scan_attempts }) : t(`ac.evidence.note.${a.scan_status}` as TKey)}</div>}
                {a.scan_detail && a.scan_status !== "clean" && <div className="text-[11px] text-muted-foreground">{a.scanner}: {a.scan_detail}</div>}
                {a.released_at && <div className="text-[11px] text-emerald-800">{t("ac.evidence.releasedAt", { time: fmtDateTime(a.released_at, lang) })}{a.release_note ? ` – ${a.release_note}` : ""}</div>}
              </div>
              <ScanBadge a={a} />
              {a.downloadable && !a.requires_acknowledgement && (
                <Button size="sm" variant="outline" onClick={() => downloadFile(`/api/attachments/${a.id}/download`, a.filename).catch((e) => toast.error(e.message))}
                  data-testid={`download-${a.id}`}>
                  <FileDown /> {t("common.download")}
                </Button>
              )}
              {a.requires_acknowledgement && (
                <ConfirmDialog title={t("ac.evidence.riskTitle")} description={`${a.filename} – ${t("ac.evidence.riskDesc")}`}
                  confirmLabel={t("ac.evidence.downloadAnyway")} destructive
                  onConfirm={() => downloadFile(`/api/attachments/${a.id}/download?acknowledge_risk=true`, a.filename)}
                  trigger={(open) => <Button size="sm" variant="outline" onClick={open} data-testid={`download-${a.id}`}><ShieldAlert /> {t("common.download")}</Button>} />
              )}
              {a.can_release && (
                <PromptDialog title={t("ac.evidence.releaseTitle")} description={`${a.filename} – ${t("ac.evidence.releaseDesc")}`}
                  label={t("ac.evidence.releaseNote")} minLength={10} confirmLabel={t("ac.evidence.release")}
                  onConfirm={(note) => postJson(`/api/attachments/${a.id}/release`, { note }).then(() => qc.invalidateQueries({ queryKey: ["investigation", inv.id] }))}
                  trigger={(open) => <Button size="sm" variant="ghost" onClick={open} data-testid={`release-${a.id}`}><ShieldCheck /> {t("ac.evidence.release")}</Button>} />
              )}
              {inv.permissions.can_contribute && (inv.permissions.can_manage || a.uploaded_by_id === me?.user.id) && (
                <PromptDialog
                  title={t("ac.evidence.removeTitle")} description={`${a.filename} – ${t("ac.evidence.removeDesc")}`}
                  label={t("ac.evidence.reason")} minLength={3} confirmLabel={t("ac.evidence.remove")} destructive
                  onConfirm={(reason) => remove(a.id, reason)}
                  trigger={(open) => <Button size="sm" variant="ghost" onClick={open} aria-label={`${t("ac.evidence.remove")} ${a.filename}`}><Trash2 /></Button>}
                />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- KPI comparison */

const METRIC_LABEL: Record<string, TKey> = {
  oee: "kpi.oee", availability: "kpi.availability", performance: "kpi.performance", quality: "kpi.quality",
  defect_rate: "kpi.defect_rate", unplanned_downtime_minutes: "ac.kpi.unplanned_downtime_minutes", total_production: "ac.kpi.total_production",
};
const SERIES_KEY: Record<string, string> = { total_production: "actual_qty" };

function fmtVal(m: ComparisonMetric, v: number | null | undefined, lang: Lang) {
  if (m.kind === "ratio") return fmtPct(v, lang);
  return `${fmtNumber(v, lang, m.kind === "minutes" ? 0 : 0)}${v == null ? "" : m.kind === "minutes" ? " min" : ""}`;
}

interface CmpParams { baseline_start: string; baseline_end: string; comparison_start: string; comparison_end: string; scope: string; shift_id: string; product_id: string }

export function KpiTab({ inv }: { inv: InvestigationDetail }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const meta = useMeta();
  const empty: CmpParams = { baseline_start: "", baseline_end: "", comparison_start: "", comparison_end: "", scope: inv.machine ? "machine" : "line", shift_id: "", product_id: "" };
  const [form, setForm] = useState<CmpParams>(empty);
  const [applied, setApplied] = useState<CmpParams>(empty);
  const [metric, setMetric] = useState("oee");
  const set = (p: Partial<CmpParams>) => setForm((f) => ({ ...f, ...p }));
  const qs = (p: CmpParams) => {
    const s = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => v && s.set(k, v));
    return s.toString();
  };
  const q = useQuery({
    queryKey: ["investigation", inv.id, "kpi", applied],
    queryFn: () => api<Comparison>(`/api/investigations/${inv.id}/kpi-comparison?${qs(applied)}`),
    retry: false,
  });
  // populate the form with the periods the server actually used (suggested defaults on first load)
  useEffect(() => {
    if (q.data && !form.baseline_start) {
      setForm((f) => ({ ...f, baseline_start: q.data.baseline.start, baseline_end: q.data.baseline.end, comparison_start: q.data.comparison.start, comparison_end: q.data.comparison.end }));
    }
  }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const record = useMutation({
    mutationFn: () => postJson(`/api/investigations/${inv.id}/kpi-comparison`, Object.fromEntries(Object.entries(applied).filter(([, v]) => v).map(([k, v]) => [k, k.endsWith("_id") ? Number(v) : v]))),
    onSuccess: () => { toast.success(t("ac.kpi.recorded")); qc.invalidateQueries({ queryKey: ["investigation", inv.id, "timeline"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  const d = q.data;
  const invalid = [form.baseline_start, form.baseline_end, form.comparison_start, form.comparison_end].some((x) => !x)
    || form.baseline_end < form.baseline_start || form.comparison_end < form.comparison_start;
  const m = d?.metrics.find((x) => x.metric === metric);
  const sk = SERIES_KEY[metric] ?? metric;
  const n = Math.max(d?.series_before.length ?? 0, d?.series_after.length ?? 0);
  const chart = Array.from({ length: n }, (_, i) => ({
    day: i + 1,
    before: (d?.series_before[i]?.[sk] as number | null | undefined) ?? null,
    after: (d?.series_after[i]?.[sk] as number | null | undefined) ?? null,
  }));
  const dirIcon = (dir: ComparisonMetric["direction"]) =>
    dir === "better" ? <ArrowUpRight className="h-3.5 w-3.5 text-emerald-600" /> : dir === "worse" ? <ArrowDownRight className="h-3.5 w-3.5 text-red-600" /> : dir === "unchanged" ? <Minus className="h-3.5 w-3.5 text-slate-400" /> : null;

  return (
    <div className="space-y-4">
      <div className="flex gap-3 rounded-lg border border-blue-200 bg-blue-50/70 p-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-blue-700" />
        <div><div className="text-sm font-semibold text-blue-900">{t("ac.kpi.disclaimerTitle")}</div><p className="text-xs text-blue-900/80">{t("ac.kpi.disclaimer")}</p></div>
      </div>
      <form className="rounded-lg border bg-white p-3" onSubmit={(e) => { e.preventDefault(); setApplied(form); }} data-testid="kpi-form">
        <div className="flex flex-wrap items-end gap-3">
          <fieldset className="flex flex-wrap items-end gap-2 rounded-md border border-slate-200 p-2">
            <legend className="px-1 text-[11px] font-semibold uppercase text-slate-500">{t("ac.kpi.baseline")}</legend>
            <Input type="date" aria-label={`${t("ac.kpi.baseline")} ${t("common.from")}`} className="w-[145px]" value={form.baseline_start} onChange={(e) => set({ baseline_start: e.target.value })} data-testid="kpi-bs" />
            <Input type="date" aria-label={`${t("ac.kpi.baseline")} ${t("common.to")}`} className="w-[145px]" value={form.baseline_end} onChange={(e) => set({ baseline_end: e.target.value })} data-testid="kpi-be" />
          </fieldset>
          <fieldset className="flex flex-wrap items-end gap-2 rounded-md border border-slate-200 p-2">
            <legend className="px-1 text-[11px] font-semibold uppercase text-slate-500">{t("ac.kpi.comparison")}</legend>
            <Input type="date" aria-label={`${t("ac.kpi.comparison")} ${t("common.from")}`} className="w-[145px]" value={form.comparison_start} onChange={(e) => set({ comparison_start: e.target.value })} data-testid="kpi-cs" />
            <Input type="date" aria-label={`${t("ac.kpi.comparison")} ${t("common.to")}`} className="w-[145px]" value={form.comparison_end} onChange={(e) => set({ comparison_end: e.target.value })} data-testid="kpi-ce" />
          </fieldset>
          <FieldSelect id="kpi-scope" label={t("ac.kpi.scope")} value={form.scope} onChange={(v) => set({ scope: v })}
            options={[...(inv.machine ? [{ value: "machine", label: `${t("common.machine")} ${inv.machine.code}` }] : []), { value: "line", label: `${t("common.line")} ${inv.line.code}` }]} />
          <FieldSelect id="kpi-shift" label={t("common.shift")} value={form.shift_id} onChange={(v) => set({ shift_id: v })} allLabel={t("ac.kpi.allShifts")}
            options={(meta.data?.shifts ?? []).map((s) => ({ value: String(s.id), label: s.name }))} />
          <FieldSelect id="kpi-product" label={t("common.product")} value={form.product_id} onChange={(v) => set({ product_id: v })} allLabel={t("ac.kpi.allProducts")}
            options={(meta.data?.products ?? []).map((p) => ({ value: String(p.id), label: `${p.code} · ${p.name}` }))} />
          <Button type="submit" disabled={invalid} data-testid="kpi-calc"><Activity /> {t("ac.kpi.calculate")}</Button>
          {d && (
            <Button type="button" variant="ghost" onClick={() => {
              const dd = { ...empty, baseline_start: d.defaults.baseline_start, baseline_end: d.defaults.baseline_end, comparison_start: d.defaults.comparison_start, comparison_end: d.defaults.comparison_end };
              setForm(dd); setApplied(dd);
            }}><RotateCcw /> {t("ac.kpi.defaults")}</Button>
          )}
        </div>
      </form>

      {q.isLoading ? <LoadingState /> : q.isError ? <ErrorState error={q.error} /> : d && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span data-testid="kpi-days">{d.scope_label} · {fmtDate(d.baseline.start, lang)}–{fmtDate(d.baseline.end, lang)} vs. {fmtDate(d.comparison.start, lang)}–{fmtDate(d.comparison.end, lang)} · {t("ac.kpi.days", { b: d.production_days_before, a: d.production_days_after })}</span>
            {inv.permissions.can_contribute && (
              <Button size="sm" variant="outline" onClick={() => record.mutate()} disabled={record.isPending} data-testid="kpi-record">
                {record.isPending ? <Loader2 className="animate-spin" /> : <Save />} {t("ac.kpi.record")}
              </Button>
            )}
          </div>
          {!d.sufficient_data && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" role="status" data-testid="kpi-insufficient">{t("ac.kpi.insufficient", { n: d.min_production_days })}</div>
          )}
          <div className="overflow-auto rounded-md border">
            <Table data-testid="kpi-table">
              <TableHeader><TableRow>
                <TableHead>{t("ac.kpi.metric")}</TableHead>
                <TableHead className="text-right">{t("ac.kpi.before")}</TableHead>
                <TableHead className="text-right">{t("ac.kpi.after")}</TableHead>
                <TableHead className="text-right">{t("ac.kpi.diff")}</TableHead>
                <TableHead className="text-right">{t("ac.kpi.pct")}</TableHead>
                <TableHead className="text-right">{t("ac.kpi.coverage")}</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {d.metrics.map((r) => (
                  <TableRow key={r.metric} className={cn("cursor-pointer", r.metric === metric && "bg-slate-50")} onClick={() => setMetric(r.metric)} data-metric={r.metric}>
                    <TableCell className="font-medium">{t(METRIC_LABEL[r.metric])}{r.kind !== "ratio" && <span className="block text-[11px] font-normal text-muted-foreground">{t("ac.kpi.perDay")}: {fmtVal(r, r.before_per_day, lang)} → {fmtVal(r, r.after_per_day, lang)}</span>}</TableCell>
                    <TableCell className="tabular text-right">{fmtVal(r, r.before, lang)}</TableCell>
                    <TableCell className="tabular text-right">{fmtVal(r, r.after, lang)}</TableCell>
                    <TableCell className="tabular whitespace-nowrap text-right">
                      <span className="inline-flex items-center gap-1">{dirIcon(r.direction)}{r.kind === "ratio" ? fmtPp(r.abs_diff, lang) : r.abs_diff == null ? "N/A" : `${r.abs_diff > 0 ? "+" : ""}${fmtNumber(r.abs_diff, lang, 1)}${r.kind === "minutes" ? " min" : ""}/d`}</span>
                      {r.direction && <span className="sr-only"> {t(`ac.kpi.${r.direction}` as TKey)}</span>}
                    </TableCell>
                    <TableCell className="tabular text-right">{fmtRel(r.pct_change, lang)}</TableCell>
                    <TableCell className="tabular whitespace-nowrap text-right text-xs text-muted-foreground">{fmtPct(r.coverage_before, lang, 0)} / {fmtPct(r.coverage_after, lang, 0)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {m && (
            <Card>
              <CardHeader><CardTitle>{t("ac.kpi.chart", { metric: t(METRIC_LABEL[metric]) })}</CardTitle><CardDescription>{t("ac.kpi.dayIndex")}</CardDescription></CardHeader>
              <CardContent>
                {chart.length === 0 ? <EmptyState /> : (
                  <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={chart} margin={{ left: 0, right: 8, top: 8 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="day" {...AXIS} />
                      <YAxis {...AXIS} width={56} tickFormatter={(v) => (m.kind === "ratio" ? fmtPct(v, lang, 0) : fmtNumber(v, lang))} />
                      <Tooltip content={<ChartTooltip labelFormatter={(l) => `${t("ac.kpi.dayIndex")} ${l}`} valueFormatter={(v) => fmtVal(m, v, lang)} />} />
                      <Legend />
                      <Line dataKey="before" name={t("ac.kpi.before")} stroke={COLORS.slate} strokeWidth={2} dot={false} connectNulls={false} />
                      <Line dataKey="after" name={t("ac.kpi.after")} stroke={COLORS.teal} strokeWidth={2} dot={false} connectNulls={false} />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

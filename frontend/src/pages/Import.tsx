import { useCallback, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, CheckCircle2, Download, FileSpreadsheet, Loader2, RotateCcw, ShieldCheck, Upload, XCircle,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, downloadFile, postJson } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { useI18n, type TKey } from "@/lib/i18n";
import { useImports, useSettings } from "@/lib/queries";
import { fmtBytes, fmtDate, fmtDateTime, fmtNumber } from "@/lib/format";
import type { ImportJob, ImportOptions, Meta, UploadResponse, ValidationSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type Step = "upload" | "map" | "done";
interface CommitResult { imported_rows: number; skipped_rows: number; created_master_data: Record<string, string[]> }

function Steps({ step, validated }: { step: Step; validated: boolean }) {
  const { t } = useI18n();
  const items: [string, boolean, boolean][] = [
    [t("import.step.upload"), step !== "upload", step === "upload"],
    [t("import.step.map"), step === "done" || validated, step === "map" && !validated],
    [t("import.step.validate"), step === "done", step === "map" && validated],
    [t("import.step.done"), step === "done", false],
  ];
  return (
    <ol className="mb-5 flex flex-wrap items-center gap-2 text-sm" aria-label="Import steps">
      {items.map(([label, done, active], i) => (
        <li key={label} className="flex items-center gap-2">
          <span className={cn("flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold",
            done ? "bg-teal-600 text-white" : active ? "bg-blue-600 text-white" : "bg-slate-200 text-slate-500")} aria-current={active ? "step" : undefined}>
            {done ? "✓" : i + 1}
          </span>
          <span className={cn(active ? "font-semibold text-slate-900" : "text-slate-500")}>{label}</span>
          {i < items.length - 1 && <span className="mx-1 h-px w-8 bg-slate-300" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}

function Dropzone({ onFile, busy, maxMb }: { onFile: (f: File) => void; busy: boolean; maxMb: number }) {
  const { t } = useI18n();
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      role="button" tabIndex={0} aria-label={t("import.drop")}
      onClick={() => !busy && input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f && !busy) onFile(f); }}
      className={cn("flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed bg-white px-6 py-14 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        over ? "border-blue-500 bg-blue-50" : "border-slate-300 hover:border-blue-400 hover:bg-slate-50")}
      data-testid="dropzone"
    >
      {busy ? <Loader2 className="h-10 w-10 animate-spin text-blue-600" /> : <Upload className="h-10 w-10 text-blue-600" />}
      <div className="text-base font-semibold text-slate-800">{busy ? t("import.uploading") : t("import.drop")}</div>
      <div className="max-w-lg text-xs text-muted-foreground">{t("import.dropHint", { mb: maxMb })}</div>
      <input ref={input} type="file" accept=".csv,.xlsx" className="hidden" data-testid="file-input"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </div>
  );
}

function SummaryTile({ label, value, tone }: { label: string; value: number; tone: "slate" | "green" | "red" | "amber" | "blue" }) {
  const c = { slate: "text-slate-900", green: "text-emerald-600", red: "text-red-600", amber: "text-amber-600", blue: "text-blue-600" }[tone];
  return (
    <div className="rounded-md border bg-white p-3">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cn("tabular text-2xl font-semibold", c)}>{value.toLocaleString()}</div>
    </div>
  );
}

function History() {
  const { t, lang } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const jobs = useImports();
  const revert = useMutation({
    mutationFn: (id: number) => postJson<{ deleted_records: number }>(`/api/imports/${id}/revert`, { confirm: "REVERT" }),
    onSuccess: (r) => {
      toast.success(`${r.deleted_records} ${t("common.records")} – ${t("import.status.reverted")}`);
      qc.invalidateQueries();
    },
    onError: (e) => toast.error((e as Error).message),
  });
  const statusVariant = (s: string) => (s === "committed" ? "success" : s === "failed" ? "danger" : s === "reverted" ? "secondary" : "default") as "success";
  return (
    <Card className="mt-6">
      <CardHeader><CardTitle>{t("import.history")}</CardTitle><CardDescription>{t("import.historyDesc")}</CardDescription></CardHeader>
      <CardContent>
        {jobs.isLoading ? <LoadingState /> : jobs.isError ? <ErrorState error={jobs.error} /> : !jobs.data?.length ? <EmptyState title="—" hint="" /> : (
          <div className="overflow-auto rounded-md border">
            <Table data-testid="import-history">
              <TableHeader><TableRow>
                <TableHead>#</TableHead><TableHead>{t("import.file")}</TableHead><TableHead>{t("common.status")}</TableHead>
                <TableHead className="text-right">{t("import.rows")}</TableHead><TableHead className="text-right">{t("import.valid")}</TableHead>
                <TableHead className="text-right">{t("import.errors")}</TableHead><TableHead>{t("reports.created")}</TableHead><TableHead />
              </TableRow></TableHeader>
              <TableBody>
                {jobs.data.map((j: ImportJob) => (
                  <TableRow key={j.id}>
                    <TableCell className="tabular text-xs text-muted-foreground">{j.id}</TableCell>
                    <TableCell><span className="font-medium">{j.original_filename}</span> <span className="text-xs text-muted-foreground">{fmtBytes(j.file_size)}</span></TableCell>
                    <TableCell><Badge variant={statusVariant(j.status)}>{t(`import.status.${j.status}` as TKey)}</Badge></TableCell>
                    <TableCell className="tabular text-right">{fmtNumber(j.total_rows, lang)}</TableCell>
                    <TableCell className="tabular text-right">{j.status === "committed" ? fmtNumber(j.imported_rows, lang) : fmtNumber(j.valid_rows, lang)}</TableCell>
                    <TableCell className="tabular text-right">{fmtNumber(j.error_rows, lang)}</TableCell>
                    <TableCell className="tabular text-xs">{fmtDateTime(j.committed_at ?? j.created_at, lang)}</TableCell>
                    <TableCell className="text-right">
                      {j.status === "committed" && can("import.revert") && (
                        <ConfirmDialog
                          requireText="REVERT"
                          title={t("import.revert")}
                          description={t("import.revertConfirm", { n: j.imported_rows ?? 0, id: j.id })}
                          confirmLabel={t("import.revert")}
                          destructive
                          onConfirm={() => revert.mutateAsync(j.id)}
                          trigger={(open) => (
                            <Button size="sm" variant="ghost" disabled={revert.isPending} onClick={open}>
                              <RotateCcw /> {t("import.revert")}
                            </Button>
                          )}
                        />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function ImportPage() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const { setFilters } = useFilters();
  const settings = useSettings();
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [up, setUp] = useState<UploadResponse | null>(null);
  const [opts, setOpts] = useState<ImportOptions | null>(null);
  const [summary, setSummary] = useState<ValidationSummary | null>(null);
  const [validOnly, setValidOnly] = useState(false);
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [result, setResult] = useState<CommitResult | null>(null);

  const upload = useMutation({
    mutationFn: async ({ f, sheet, headerRow }: { f: File; sheet?: string | null; headerRow?: number }) => {
      const fd = new FormData();
      fd.append("file", f);
      const p = new URLSearchParams();
      if (sheet) p.set("sheet", sheet);
      if (headerRow) p.set("header_row", String(headerRow));
      return api<UploadResponse>(`/api/imports/upload?${p}`, { method: "POST", body: fd });
    },
    onSuccess: (r, vars) => {
      setUp(r);
      setOpts((o) => ({
        mapping: r.suggested_mapping, sheet: r.sheet, header_row: vars.headerRow ?? 1,
        number_format: o?.number_format ?? "auto", ideal_cycle_time_unit: o?.ideal_cycle_time_unit ?? "seconds",
        skip_existing_duplicates: o?.skip_existing_duplicates ?? true,
      }));
      setSummary(null);
      setStep("map");
      qc.invalidateQueries({ queryKey: ["imports"] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const validate = useMutation({
    mutationFn: () => postJson<ValidationSummary>(`/api/imports/${up!.job.id}/validate`, opts),
    onSuccess: (s) => { setSummary(s); setValidOnly(false); qc.invalidateQueries({ queryKey: ["imports"] }); },
    onError: (e) => toast.error((e as Error).message),
  });

  const commit = useMutation({
    mutationFn: () => postJson<CommitResult>(`/api/imports/${up!.job.id}/commit`, { options: opts, import_valid_rows_only: validOnly }),
    onSuccess: async (r) => {
      setResult(r);
      setStep("done");
      await qc.invalidateQueries();
      const meta = await qc.fetchQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta") });
      setFilters({ startDate: meta.data_range.start ?? "", endDate: meta.data_range.end ?? "" });
    },
    onError: (e) => {
      const err = e as ApiError;
      const s = (err.details as { summary?: ValidationSummary } | undefined)?.summary;
      if (s) setSummary({ ...s, already_imported: summary?.already_imported ?? null, can_commit: false });
      toast.error(err.message);
    },
  });

  const onFile = useCallback((f: File) => { setFile(f); upload.mutate({ f }); }, [upload]);
  const reset = () => { setStep("upload"); setUp(null); setOpts(null); setSummary(null); setResult(null); setFile(null); };
  const setMap = (key: string, col: string | null) => { setOpts((o) => o && { ...o, mapping: { ...o.mapping, [key]: col } }); setSummary(null); };
  const setOpt = (patch: Partial<ImportOptions>) => { setOpts((o) => o && { ...o, ...patch }); setSummary(null); };
  const maxMb = settings.data?.system.max_upload_mb ?? 10;
  const requiredMissing = up?.fields.filter((f) => f.required && !opts?.mapping[f.key]) ?? [];
  const issues = (summary?.issues ?? []).filter((i) => !onlyErrors || i.severity === "error");
  const blocked = !!summary && (summary.error_rows > 0 && !validOnly);
  const canCommit = !!summary && summary.can_commit && !blocked && summary.valid_rows > 0;

  return (
    <div>
      <PageHeader title={t("import.title")} subtitle={t("import.subtitle")}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => downloadFile("/api/imports/template?format=xlsx", "template.xlsx").catch((e) => toast.error(e.message))}>
              <Download /> {t("import.template")} (XLSX)
            </Button>
            <Button variant="outline" size="sm" onClick={() => downloadFile("/api/imports/template?format=csv", "template.csv").catch((e) => toast.error(e.message))}>
              <Download /> CSV
            </Button>
          </>
        } />
      <Steps step={step} validated={!!summary} />

      {step === "upload" && (
        <>
          <Dropzone onFile={onFile} busy={upload.isPending} maxMb={maxMb} />
          <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground"><FileSpreadsheet className="h-3.5 w-3.5" /> {t("import.samples")}</p>
        </>
      )}

      {step === "map" && up && opts && (
        <div className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-4 p-4">
              <div>
                <div className="text-xs text-muted-foreground">{t("import.file")}</div>
                <div className="flex items-center gap-2 font-semibold"><FileSpreadsheet className="h-4 w-4 text-teal-600" />{up.job.original_filename}
                  <span className="text-xs font-normal text-muted-foreground">{fmtBytes(up.job.file_size)} · {fmtNumber(up.row_count, lang)} {t("import.rows")}{up.delimiter ? ` · "${up.delimiter}"` : ""}</span></div>
              </div>
              {up.sheets.length > 0 && (
                <div className="flex w-48 flex-col gap-1">
                  <Label>{t("import.sheet")}</Label>
                  <Select value={opts.sheet ?? up.sheet ?? ""} onValueChange={(v) => file && upload.mutate({ f: file, sheet: v, headerRow: opts.header_row })}>
                    <SelectTrigger aria-label={t("import.sheet")}><SelectValue /></SelectTrigger>
                    <SelectContent>{up.sheets.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              )}
              <div className="flex w-28 flex-col gap-1">
                <Label htmlFor="hdr">{t("import.headerRow")}</Label>
                <Input id="hdr" type="number" min={1} max={50} value={opts.header_row}
                  onChange={(e) => { const n = Math.max(1, Math.min(50, Number(e.target.value) || 1)); if (file) upload.mutate({ f: file, sheet: opts.sheet, headerRow: n }); }} />
              </div>
              <div className="flex w-48 flex-col gap-1">
                <Label>{t("import.numberFormat")}</Label>
                <Select value={opts.number_format} onValueChange={(v) => setOpt({ number_format: v as ImportOptions["number_format"] })}>
                  <SelectTrigger aria-label={t("import.numberFormat")}><SelectValue /></SelectTrigger>
                  <SelectContent>{(["auto", "de", "en"] as const).map((n) => <SelectItem key={n} value={n}>{t(`import.numberFormat.${n}` as TKey)}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex w-48 flex-col gap-1">
                <Label>{t("import.ictUnit")}</Label>
                <Select value={opts.ideal_cycle_time_unit} onValueChange={(v) => setOpt({ ideal_cycle_time_unit: v as ImportOptions["ideal_cycle_time_unit"] })}>
                  <SelectTrigger aria-label={t("import.ictUnit")}><SelectValue /></SelectTrigger>
                  <SelectContent>{(["seconds", "minutes"] as const).map((n) => <SelectItem key={n} value={n}>{t(`import.ictUnit.${n}` as TKey)}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex items-center gap-2 pb-2">
                <Switch id="skip" checked={opts.skip_existing_duplicates} onCheckedChange={(v) => setOpt({ skip_existing_duplicates: v })} />
                <Label htmlFor="skip">{t("import.skipExisting")}</Label>
              </div>
              <Button variant="ghost" className="ml-auto" onClick={reset}><ArrowLeft /> {t("import.back")}</Button>
            </CardContent>
          </Card>

          {up.already_imported && (
            <div className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="alert">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {t("import.alreadyImported", { id: up.already_imported.id, date: fmtDateTime(up.already_imported.committed_at, lang) })}
            </div>
          )}

          <Card>
            <CardHeader><CardTitle>{t("import.preview")}</CardTitle></CardHeader>
            <CardContent>
              <div className="max-h-[280px] overflow-auto rounded-md border text-xs">
                <Table>
                  <TableHeader className="sticky top-0"><TableRow>{up.columns.map((c) => <TableHead key={c} className="whitespace-nowrap">{c}</TableHead>)}</TableRow></TableHeader>
                  <TableBody>
                    {up.preview.map((r, i) => (
                      <TableRow key={i}>{up.columns.map((c) => <TableCell key={c} className={cn("whitespace-nowrap py-1.5", r[c] == null && "bg-amber-50 text-amber-600")}>{r[c] ?? "∅"}</TableCell>)}</TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>{t("import.mapping")}</CardTitle><CardDescription>{t("import.mappingDesc")}</CardDescription></CardHeader>
            <CardContent>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="mapping">
                {up.fields.map((f) => {
                  const v = opts.mapping[f.key] ?? null;
                  const auto = v && up.suggested_mapping[f.key] === v;
                  return (
                    <div key={f.key} className={cn("rounded-md border p-2.5", f.required && !v && "border-red-300 bg-red-50/40")}>
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <Label className="text-[13px] text-slate-800">{f.label}{f.required && <span className="text-red-600"> *</span>}</Label>
                        {auto && <Badge variant="teal">{t("import.autoDetected")}</Badge>}
                      </div>
                      <Select value={v ?? "__none"} onValueChange={(c) => setMap(f.key, c === "__none" ? null : c)}>
                        <SelectTrigger aria-label={f.label} data-testid={`map-${f.key}`}><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none">{t("import.notMapped")}</SelectItem>
                          {up.columns.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <div className="mt-1 text-[11px] text-muted-foreground">{f.description}</div>
                    </div>
                  );
                })}
              </div>
              <div className="mt-4 flex items-center gap-3">
                <Button onClick={() => validate.mutate()} disabled={validate.isPending || requiredMissing.length > 0} data-testid="validate">
                  {validate.isPending ? <><Loader2 className="animate-spin" /> {t("import.validating")}</> : <><ShieldCheck /> {t("import.validate")}</>}
                </Button>
                {requiredMissing.length > 0 && <span className="text-xs text-red-600">* {requiredMissing.map((f) => f.label).join(", ")}</span>}
              </div>
            </CardContent>
          </Card>

          {summary && (
            <Card data-testid="validation-summary">
              <CardHeader>
                <CardTitle>{t("import.summary")}</CardTitle>
                {summary.date_range.start && <CardDescription>{t("import.dateRange")}: {fmtDate(summary.date_range.start, lang)} – {fmtDate(summary.date_range.end, lang)}</CardDescription>}
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                  <SummaryTile label={t("import.total")} value={summary.total_rows} tone="slate" />
                  <SummaryTile label={t("import.valid")} value={summary.valid_rows} tone="green" />
                  <SummaryTile label={t("import.errors")} value={summary.error_rows} tone="red" />
                  <SummaryTile label={t("import.warnings")} value={summary.warning_rows} tone="amber" />
                  <SummaryTile label={t("import.duplicates")} value={summary.duplicate_existing_rows} tone="blue" />
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <div className="mb-1 text-xs font-semibold text-slate-600">{t("import.missing")}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(summary.missing_by_field).map(([k, n]) => (
                        <Badge key={k} variant={n > 0 ? "warning" : "secondary"}>{up.fields.find((f) => f.key === k)?.label ?? k}: {n}</Badge>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1 text-xs font-semibold text-slate-600">{t("import.newMaster")}</div>
                    <div className="text-xs text-slate-700">
                      {(["lines", "machines", "shifts", "products"] as const).map((k) => summary.new_master_data[k].length > 0 && (
                        <div key={k}><span className="font-medium">{k}:</span> {summary.new_master_data[k].join(", ")}</div>
                      ))}
                      {Object.values(summary.new_master_data).every((v) => !v.length) && <span className="text-muted-foreground">—</span>}
                    </div>
                  </div>
                </div>

                {summary.issues.length > 0 && (
                  <div>
                    <div className="mb-2 flex items-center gap-3">
                      <div className="text-sm font-semibold">{t("import.issues")} ({summary.error_count + summary.warning_count})</div>
                      <div className="flex items-center gap-2"><Switch id="only-err" checked={onlyErrors} onCheckedChange={setOnlyErrors} /><Label htmlFor="only-err">{t("import.onlyErrors")}</Label></div>
                    </div>
                    <div className="max-h-[340px] overflow-auto rounded-md border">
                      <Table data-testid="issue-table">
                        <TableHeader className="sticky top-0"><TableRow>
                          <TableHead className="w-16">{t("import.row")}</TableHead><TableHead className="w-10" /><TableHead>{t("import.column")}</TableHead>
                          <TableHead>{t("import.value")}</TableHead><TableHead>{t("import.message")}</TableHead>
                        </TableRow></TableHeader>
                        <TableBody>
                          {issues.slice(0, 500).map((i, n) => (
                            <TableRow key={n} className={i.severity === "error" ? "bg-red-50/40" : undefined}>
                              <TableCell className="tabular font-medium">{i.row}</TableCell>
                              <TableCell>{i.severity === "error" ? <XCircle className="h-4 w-4 text-red-600" aria-label="error" /> : <AlertTriangle className="h-4 w-4 text-amber-500" aria-label="warning" />}</TableCell>
                              <TableCell className="text-xs">{i.column ?? "—"}</TableCell>
                              <TableCell className="font-mono text-xs">{i.value ?? "∅"}</TableCell>
                              <TableCell className="text-xs">
                                <span className="font-medium">{t(`code.${i.code}` as TKey)}</span>
                                <span className="block text-muted-foreground">{i.message}</span>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                )}

                {summary.already_imported && (
                  <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="alert">
                    {t("import.alreadyImported", { id: summary.already_imported.id, date: fmtDateTime(summary.already_imported.committed_at, lang) })}
                  </div>
                )}
                {summary.error_rows > 0 && summary.valid_rows > 0 && !summary.already_imported && (
                  <label className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50/60 p-3 text-sm">
                    <input type="checkbox" className="mt-0.5 h-4 w-4 accent-amber-600" checked={validOnly} onChange={(e) => setValidOnly(e.target.checked)} data-testid="valid-only" />
                    {t("import.importValidOnly", { n: summary.valid_rows })}
                  </label>
                )}
                {blocked && !summary.already_imported && <p className="text-sm text-red-700">{t("import.blocked")}</p>}
                <Button size="lg" variant="accent" onClick={() => commit.mutate()} disabled={!canCommit || commit.isPending} data-testid="commit">
                  {commit.isPending ? <><Loader2 className="animate-spin" /> {t("import.committing")}</> : <><Upload /> {t("import.commit", { n: summary.valid_rows })}</>}
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {step === "done" && result && (
        <Card className="border-emerald-200" data-testid="import-success">
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <CheckCircle2 className="h-12 w-12 text-emerald-600" />
            <div className="text-xl font-semibold">{t("import.success")}</div>
            <p className="text-sm text-muted-foreground">{t("import.successDesc", { n: fmtNumber(result.imported_rows, lang), s: fmtNumber(result.skipped_rows, lang) })}</p>
            {Object.values(result.created_master_data).some((v) => v.length) && (
              <div className="text-xs text-slate-600">
                {t("import.created")}: {Object.entries(result.created_master_data).filter(([, v]) => v.length).map(([k, v]) => `${k}: ${v.join(", ")}`).join(" · ")}
              </div>
            )}
            <div className="mt-2 flex gap-2">
              <Button asChild><Link to="/">{t("import.goDashboard")}</Link></Button>
              <Button variant="outline" onClick={reset}>{t("import.another")}</Button>
            </div>
          </CardContent>
        </Card>
      )}

      <History />
    </div>
  );
}

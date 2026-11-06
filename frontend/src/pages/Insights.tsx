import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { Link, useNavigate } from "react-router-dom";
import { BrainCircuit, CheckCircle2, ClipboardList, ClipboardPlus, Info, Loader2, Play, Save, ShieldAlert, XCircle } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { FilterBar } from "@/components/FilterBar";
import { AnomalyConfigForm, validateConfig } from "@/components/AnomalyConfigForm";
import { ChartTooltip } from "@/components/ChartTooltip";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AXIS, COLORS, GRID } from "@/components/charts";
import { api, filterBody, filterParams, postJson } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import { useI18n, type TKey } from "@/lib/i18n";
import { useSettings } from "@/lib/queries";
import { fmtDate, fmtNumber, fmtPct, fmtShortDate } from "@/lib/format";
import type { Anomaly, AnomalyConfig, DetectionResult, SeriesPoint, Severity } from "@/lib/types";

const SEV: Severity[] = ["critical", "high", "medium", "low"];

function SeverityBadge({ s }: { s: Severity }) {
  const { t } = useI18n();
  return <Badge variant={s}>{t(`severity.${s}` as TKey)}</Badge>;
}

function InvestigationButton({ a, cfg }: { a: Anomaly; cfg: AnomalyConfig | null }) {
  const { t } = useI18n();
  const { can } = useAuth();
  const nav = useNavigate();
  if (a.investigation) {
    return (
      <Button size="sm" variant="outline" asChild data-testid="open-investigation">
        <Link to={`/actions/investigations/${a.investigation.id}`}><ClipboardList /> {t("ac.create.linked", { number: a.investigation.number })}</Link>
      </Button>
    );
  }
  if (!can("action.write")) return null;
  return (
    <Button size="sm" data-testid="create-investigation"
      onClick={() => nav(`/actions/investigations/new?anomaly=${encodeURIComponent(a.id)}`, { state: { config: cfg } })}>
      <ClipboardPlus /> {t("ac.create.fromInsights")}
    </Button>
  );
}

function AnomalyDetail({ a, onClose, cfg }: { a: Anomaly | null; onClose: () => void; cfg: AnomalyConfig | null }) {
  const { t, lang, tv } = useI18n();
  const { filters } = useFilters();
  const series = useQuery({
    queryKey: ["series", a?.level, a?.entity_id, a?.metric, filters.startDate, filters.endDate],
    queryFn: () => api<{ points: SeriesPoint[] }>(`/api/anomalies/series${filterParams({ ...filters, lineId: null, machineId: null }, {
      level: a!.level, entity_id: a!.entity_id, metric: a!.metric,
    })}`),
    enabled: !!a,
  });
  const pts = (series.data?.points ?? []).map((p) => ({ ...p, label: fmtShortDate(p.date, lang) }));
  const hit = pts.find((p) => p.date === a?.date);
  return (
    <Dialog open={!!a} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl">
        {a && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">{t(`metric.${a.metric}` as TKey)} · {a.entity_code} <SeverityBadge s={a.severity} /></DialogTitle>
              <DialogDescription>{fmtDate(a.date, lang)} · {a.entity_name} · {t("insights.unconfirmed")}</DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[[t("insights.observed"), fmtPct(a.observed, lang)], [t("insights.baseline"), fmtPct(a.baseline, lang)],
                [t("insights.zScore"), fmtNumber(a.z_score, lang, 1)], [t("insights.method"), a.method === "isolation_forest" ? "Isolation Forest" : "Robust z-score"]].map(([k, v]) => (
                <div key={k} className="rounded-md border bg-slate-50 p-2.5">
                  <div className="text-[11px] uppercase tracking-wide text-slate-500">{k}</div>
                  <div className="tabular text-base font-semibold">{v}</div>
                </div>
              ))}
            </div>
            <div className="h-[260px]">
              {series.isLoading ? <LoadingState /> : series.isError ? <ErrorState error={series.error} /> : (
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={pts} margin={{ left: 0, right: 8, top: 8 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="label" {...AXIS} minTickGap={20} />
                    <YAxis {...AXIS} tickFormatter={(v) => fmtPct(v, lang, 0)} width={48} />
                    <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtPct(v, lang)} />} />
                    <Legend />
                    <Line dataKey="value" name={t("insights.value")} stroke={COLORS.blue} strokeWidth={1.8} dot={false} />
                    <Line dataKey="baseline" name={t("insights.baseline")} stroke={COLORS.slate} strokeDasharray="4 3" dot={false} />
                    <Line dataKey="threshold" name={t("insights.threshold")} stroke={COLORS.red} strokeDasharray="2 3" dot={false} strokeWidth={1} />
                    {hit && hit.value != null && <ReferenceDot x={hit.label} y={hit.value} r={6} fill={COLORS.red} stroke="white" />}
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
            <p className="text-sm leading-relaxed text-slate-700">{lang === "de" && a.explanation_de ? a.explanation_de : a.explanation}</p>
            <div className="flex justify-end"><InvestigationButton a={a} cfg={cfg} /></div>
            {a.recorded_context.length > 0 && (
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                <div className="mb-1 font-semibold text-slate-700">{t("insights.context")}</div>
                {a.recorded_context.map((c) => (
                  <div key={c.kind} className="text-slate-600">
                    <span className="font-medium">{c.kind === "downtime_reasons" ? t("downtime.reason") : t("quality.category")}:</span>{" "}
                    {c.items.map((it) => `${tv(it.name)} (${fmtNumber(it.value, lang)}${c.unit === "min" ? " min" : ""})`).join(", ")}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function InsightsPage() {
  const { t, lang } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const { filters, ready } = useFilters();
  const settings = useSettings();
  const [cfg, setCfg] = useState<AnomalyConfig | null>(null);
  const [result, setResult] = useState<DetectionResult | null>(null);
  const [selected, setSelected] = useState<Anomaly | null>(null);
  const [sevFilter, setSevFilter] = useState<string>("all");
  const [metricFilter, setMetricFilter] = useState<string>("all");

  useEffect(() => {
    if (settings.data && !cfg) setCfg(settings.data.anomaly_config);
  }, [settings.data, cfg]);

  const detect = useMutation({
    mutationFn: (c: AnomalyConfig) => postJson<DetectionResult>("/api/anomalies/detect", { filters: filterBody(filters), config: c }),
    onSuccess: setResult,
    onError: (e) => toast.error((e as Error).message),
  });
  const save = useMutation({
    mutationFn: (c: AnomalyConfig) => postJson("/api/settings", { anomaly_config: c }, "PUT"),
    onSuccess: () => { toast.success(t("insights.saved")); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: (e) => toast.error((e as Error).message),
  });

  // run automatically with the saved configuration whenever filters change
  const filterKey = JSON.stringify(filterBody(filters));
  useEffect(() => {
    if (ready && cfg && !validateConfig(cfg)) detect.mutate(cfg);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, filterKey, !!cfg]);

  const cfgError = cfg ? validateConfig(cfg) : null;
  const rows = useMemo(() => (result?.anomalies ?? []).filter((a) =>
    (sevFilter === "all" || a.severity === sevFilter) && (metricFilter === "all" || a.metric === metricFilter)), [result, sevFilter, metricFilter]);

  return (
    <div>
      <PageHeader title={t("insights.title")} subtitle={t("insights.subtitle")} />
      <div className="mb-5 flex gap-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-blue-700" />
        <div>
          <div className="text-sm font-semibold text-blue-900">{t("insights.disclaimerTitle")}</div>
          <p className="mt-0.5 text-sm text-blue-900/80">{t("insights.disclaimer")}</p>
        </div>
      </div>
      <FilterBar show={["line", "machine", "shift", "product"]} />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-1">
          <CardHeader><CardTitle>{t("insights.config")}</CardTitle></CardHeader>
          <CardContent>
            {!cfg ? <LoadingState /> : (
              <>
                <AnomalyConfigForm value={cfg} onChange={setCfg} />
                {cfgError && <p className="mt-3 text-xs font-medium text-red-600" role="alert">{cfgError}</p>}
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button onClick={() => detect.mutate(cfg)} disabled={!!cfgError || detect.isPending} data-testid="run-detection">
                    {detect.isPending ? <Loader2 className="animate-spin" /> : <Play />} {t("insights.run")}
                  </Button>
                  <Button variant="outline" onClick={() => save.mutate(cfg)} disabled={!can("settings.write") || !!cfgError || save.isPending}>
                    <Save /> {t("insights.saveDefault")}
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4 xl:col-span-2">
          {result?.ground_truth_validation && result.ground_truth_validation.length > 0 && (
            <Card data-testid="ground-truth">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {t("insights.validation")}
                  <Badge variant={result.ground_truth_validation.every((g) => g.detected) ? "success" : "warning"}>
                    {result.ground_truth_validation.filter((g) => g.detected).length}/{result.ground_truth_validation.length} {t("insights.found")}
                  </Badge>
                </CardTitle>
                <CardDescription>{t("insights.validationDesc")}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2 md:grid-cols-2">
                {result.ground_truth_validation.map((g) => (
                  <div key={g.date + g.entity} className="flex items-start gap-2 rounded-md border p-2.5 text-xs">
                    {g.detected ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" /> : <XCircle className="h-4 w-4 shrink-0 text-red-600" />}
                    <div>
                      <div className="font-semibold">{fmtDate(g.date, lang)} · {g.entity} {g.best_severity && <SeverityBadge s={g.best_severity} />}</div>
                      <div className="text-muted-foreground">{g.description}</div>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
              <div>
                <CardTitle className="flex items-center gap-2"><BrainCircuit className="h-4 w-4 text-teal-600" />{t("insights.results")}</CardTitle>
                {result?.status === "ok" && (
                  <CardDescription className="mt-1">{t("insights.resultsDesc", { n: result.stats.total ?? 0, days: result.stats.production_days ?? 0 })}</CardDescription>
                )}
              </div>
              {result?.status === "ok" && (
                <div className="flex flex-wrap gap-2">
                  {SEV.map((s) => (result.stats.by_severity?.[s] ?? 0) > 0 && (
                    <Badge key={s} variant={s}>{t(`severity.${s}` as TKey)}: {result.stats.by_severity?.[s]}</Badge>
                  ))}
                </div>
              )}
            </CardHeader>
            <CardContent>
              {detect.isError && !result ? <ErrorState error={detect.error} onRetry={() => cfg && detect.mutate(cfg)} /> :
               !result ? <LoadingState /> :
               result.status === "insufficient_data" ? (
                <div className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-4" data-testid="insufficient">
                  <Info className="h-5 w-5 shrink-0 text-amber-700" />
                  <div><div className="text-sm font-semibold text-amber-900">{t("insights.insufficient")}</div><p className="text-sm text-amber-800">{result.message}</p></div>
                </div>
              ) : result.anomalies.length === 0 ? <EmptyState title={t("insights.none")} hint="" /> : (
                <>
                  <div className="mb-3 flex flex-wrap gap-2">
                    <Select value={sevFilter} onValueChange={setSevFilter}>
                      <SelectTrigger className="w-44" aria-label={t("insights.severity")}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">{t("insights.allSeverities")}</SelectItem>
                        {SEV.map((s) => <SelectItem key={s} value={s}>{t(`severity.${s}` as TKey)}</SelectItem>)}
                      </SelectContent>
                    </Select>
                    <Select value={metricFilter} onValueChange={setMetricFilter}>
                      <SelectTrigger className="w-56" aria-label={t("insights.metric")}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">{t("insights.allMetrics")}</SelectItem>
                        {(["defect_rate", "downtime_ratio", "target_achievement", "performance"] as const).map((m) => (
                          <SelectItem key={m} value={m}>{t(`metric.${m}` as TKey)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {detect.isPending && <Loader2 className="ml-2 h-4 w-4 animate-spin self-center text-muted-foreground" />}
                  </div>
                  <div className="max-h-[560px] overflow-auto rounded-md border">
                    <Table data-testid="anomaly-table">
                      <TableHeader className="sticky top-0 z-10">
                        <TableRow>
                          <TableHead>{t("insights.severity")}</TableHead><TableHead>{t("common.date")}</TableHead>
                          <TableHead>{t("insights.entity")}</TableHead><TableHead>{t("insights.metric")}</TableHead>
                          <TableHead className="text-right">{t("insights.observed")}</TableHead>
                          <TableHead className="text-right">{t("insights.baseline")}</TableHead>
                          <TableHead className="text-right">{t("insights.zScore")}</TableHead>
                          <TableHead>{t("nav.actionCenter")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {rows.map((a) => (
                          <TableRow key={a.id} className="cursor-pointer" onClick={() => setSelected(a)} tabIndex={0}
                            onKeyDown={(e) => e.key === "Enter" && setSelected(a)}>
                            <TableCell><SeverityBadge s={a.severity} /></TableCell>
                            <TableCell className="tabular whitespace-nowrap">{fmtDate(a.date, lang)}</TableCell>
                            <TableCell><span className="font-medium">{a.entity_code}</span> <span className="text-xs text-muted-foreground">{a.level === "line" ? t("common.line") : a.line_code}</span></TableCell>
                            <TableCell>{t(`metric.${a.metric}` as TKey)}</TableCell>
                            <TableCell className="tabular whitespace-nowrap text-right font-medium">{fmtPct(a.observed, lang)}</TableCell>
                            <TableCell className="tabular whitespace-nowrap text-right text-muted-foreground">{fmtPct(a.baseline, lang)}</TableCell>
                            <TableCell className="tabular text-right">{fmtNumber(a.z_score, lang, 1)}</TableCell>
                            <TableCell className="whitespace-nowrap">
                              {a.investigation && (
                                <Link to={`/actions/investigations/${a.investigation.id}`} onClick={(e) => e.stopPropagation()} className="text-xs font-medium text-primary hover:underline" data-testid="anomaly-investigation-link">
                                  {a.investigation.number}
                                </Link>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
      <AnomalyDetail a={selected} onClose={() => setSelected(null)} cfg={cfg} />
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlarmClock, BellRing, Loader2, RefreshCw, CheckCircle2, ClipboardList, Flame, Hourglass, Plus, Timer, Wrench } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { KpiCard } from "@/components/KpiCard";
import { ChartCard } from "@/components/ChartCard";
import { ChartTooltip } from "@/components/ChartTooltip";
import { AcFilterBar, OverdueBadge, OwnerLabel, SevBadge, StatusBadge } from "@/components/ac";
import { EmptyState, ErrorState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AXIS, COLORS, GRID, SERIES } from "@/components/charts";
import { postJson } from "@/lib/api";
import { EMPTY_AC_FILTERS, useAcSummary, type AcFilters, type Alert, type AlertEvaluation } from "@/lib/actionCenter";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { fmtDate, fmtDateTime, fmtNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

const SEV_COLOR: Record<string, string> = { critical: COLORS.red, high: "#ea580c", medium: "#ca8a04", low: "#0284c7" };

export function alertText(a: Alert, t: (k: TKey, v?: Record<string, string | number>) => string, lang: "de" | "en"): string {
  const d = a.details as Record<string, string | number | null>;
  const vars: Record<string, string | number> = {
    number: String(d.number ?? ""), title: String(d.title ?? ""), owner: String(d.owner ?? t("ac.unassigned")),
    age: fmtNumber(Number(d.age_hours ?? 0), lang, 0), threshold: String(d.threshold_hours ?? d.threshold_days ?? d.threshold ?? ""),
    days: fmtNumber(Number(d.days ?? d.days_overdue ?? 0), lang, 0), due: fmtDate(d.due_date as string, lang),
    machine: String(d.machine_code ?? ""), count: String(d.anomaly_days ?? ""), window: String(d.window_days ?? ""),
  };
  return t(`ac.alert.${a.rule}` as TKey, vars);
}

function AlertsCard({ alerts, evaluation, tz }: { alerts: Alert[]; evaluation?: AlertEvaluation; tz: string }) {
  const { t, lang } = useI18n();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const ack = useMutation({
    mutationFn: (id: number) => postJson(`/api/action-center/alerts/${id}/acknowledge`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ac-summary"] }),
    onError: (e) => toast.error((e as Error).message),
  });
  const evaluate = useMutation({
    mutationFn: () => postJson<{ run: { status: string; error: string | null } }>("/api/action-center/alerts/evaluate", {}),
    onSuccess: (r) => {
      if (r.run.status === "failed") toast.error(r.run.error ?? "failed"); else toast.success(t("ac.alerts.evaluated"));
      qc.invalidateQueries({ queryKey: ["ac-summary"] });
    },
    onError: (e) => toast.error((e as Error).message),
  });
  const state = evaluation?.state ?? "never";
  const minutes = Math.round((evaluation?.interval_seconds ?? 300) / 60);
  return (
    <Card data-testid="ac-alerts">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2"><BellRing className="h-4 w-4 text-amber-600" />{t("ac.alerts")}</CardTitle>
          <CardDescription className="mt-1" data-testid="alert-evaluation" data-state={state}>
            {state === "never" ? t("ac.alerts.never") : t("ac.alertsDesc", { time: fmtDateTime(evaluation?.evaluated_at, lang), tz, minutes })}
          </CardDescription>
          {(state === "stale" || state === "failed") && (
            <p className="mt-1 text-xs font-medium text-amber-700" role="status">{state === "stale" ? t("ac.alerts.stale") : t("ac.alerts.failed")}</p>
          )}
        </div>
        {can("action.manage") && (
          <Button size="sm" variant="outline" onClick={() => evaluate.mutate()} disabled={evaluate.isPending} data-testid="evaluate-now">
            {evaluate.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} {t("ac.alerts.evaluateNow")}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {alerts.length === 0 ? <p className="text-sm text-muted-foreground">{t("ac.alerts.none")}</p> : (
          <ul className="space-y-2">
            {alerts.map((a) => (
              <li key={a.id} className={cn("flex flex-wrap items-start gap-2 rounded-md border p-2.5 text-sm", a.acknowledged_at && "opacity-60")} data-rule={a.rule}>
                <SevBadge s={a.severity} />
                <div className="min-w-0 flex-1">
                  {a.investigation_id ? (
                    <Link to={`/actions/investigations/${a.investigation_id}`} className="text-slate-800 hover:text-primary hover:underline">{alertText(a, t, lang)}</Link>
                  ) : <span className="text-slate-800">{alertText(a, t, lang)}</span>}
                  <div className="text-[11px] text-muted-foreground">
                    {t("ac.alert.firstDetected", { time: fmtDateTime(a.first_detected_at, lang) })}
                    {a.acknowledged_at && <> · {t("ac.acknowledged")} {fmtDateTime(a.acknowledged_at, lang)}</>}
                  </div>
                </div>
                {!a.acknowledged_at && can("action.write") && (
                  <Button size="sm" variant="outline" onClick={() => ack.mutate(a.id)} disabled={ack.isPending}>{t("ac.acknowledge")}</Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export default function ActionCenterOverview() {
  const { t, lang } = useI18n();
  const { can } = useAuth();
  const [filters, setFilters] = useState<AcFilters>(EMPTY_AC_FILTERS);
  const q = useAcSummary(filters);
  const d = q.data;
  const k = d?.kpis;
  const val = (v: number | undefined) => (v === undefined ? "…" : fmtNumber(v, lang));
  const avg = k?.avg_resolution_hours;
  const avgTxt = avg == null ? "N/A" : avg >= 48 ? `${fmtNumber(avg / 24, lang, 1)} d` : `${fmtNumber(avg, lang, 1)} h`;

  return (
    <div>
      <PageHeader title={t("ac.title")} subtitle={t("ac.subtitle")} actions={
        <>
          <Button variant="outline" asChild><Link to="/actions/investigations"><ClipboardList /> {t("ac.tab.list")}</Link></Button>
          <Button variant="outline" asChild><Link to="/actions/mine"><Wrench /> {t("ac.tab.mine")}</Link></Button>
          {can("action.write") && <Button asChild><Link to="/actions/investigations/new" data-testid="ac-new"><Plus /> {t("ac.new")}</Link></Button>}
        </>
      } />
      <AcFilterBar value={filters} onChange={setFilters} />
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" data-testid="ac-kpis">
            <KpiCard label={t("ac.kpi.open")} value={val(k?.open_total)} icon={ClipboardList} tone="blue" testId="ac-kpi-open"
              sub={k ? t("ac.kpi.overdueInv", { n: k.overdue_investigations }) : undefined} />
            <KpiCard label={t("ac.kpi.critical")} value={val(k?.critical_open)} icon={Flame} tone="red" testId="ac-kpi-critical" />
            <KpiCard label={t("ac.kpi.overdueActions")} value={val(k?.overdue_actions)} icon={AlarmClock} tone="amber" testId="ac-kpi-overdue" />
            <KpiCard label={t("ac.kpi.inProgress")} value={val(k?.in_progress)} icon={Hourglass} tone="violet" testId="ac-kpi-progress"
              sub={k ? `${t("ac.status.pending_verification")}: ${k.pending_verification}` : undefined} />
            <KpiCard label={t("ac.kpi.resolvedMonth")} value={val(k?.resolved_this_month)} icon={CheckCircle2} tone="teal" testId="ac-kpi-resolved" />
            <KpiCard label={t("ac.kpi.avgResolution")} value={k ? avgTxt : "…"} icon={Timer} tone="slate" testId="ac-kpi-avg"
              sub={k ? t("ac.kpi.avgResolutionSub", { n: k.resolved_sample }) : undefined} />
          </div>

          <div className="mb-4 grid gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2"><AlertsCard alerts={d?.alerts ?? []} evaluation={d?.alert_evaluation} tz={d?.timezone ?? ""} /></div>
            <ChartCard title={t("ac.chart.bySeverity")} loading={q.isLoading} empty={!!d && d.by_severity.every((x) => x.count === 0)} height={220}>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={(d?.by_severity ?? []).map((x) => ({ ...x, label: t(`severity.${x.severity}` as TKey) }))} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" allowDecimals={false} {...AXIS} />
                  <YAxis type="category" dataKey="label" width={80} {...AXIS} />
                  <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtNumber(v, lang)} />} />
                  <Bar dataKey="count" name={t("ac.kpi.open")} radius={[0, 4, 4, 0]}>
                    {(d?.by_severity ?? []).map((x) => <Cell key={x.severity} fill={SEV_COLOR[x.severity]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <div className="mb-4 grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            <ChartCard title={t("ac.chart.trend")} description={t("ac.chart.trendDesc")} loading={q.isLoading} empty={!!d && d.trend.length === 0} className="xl:col-span-2">
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={d?.trend ?? []} margin={{ left: 0, right: 8, top: 8 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="week" {...AXIS} />
                  <YAxis allowDecimals={false} width={32} {...AXIS} />
                  <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtNumber(v, lang)} />} />
                  <Legend />
                  <Bar dataKey="opened" name={t("ac.chart.opened")} fill={COLORS.blue} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="resolved" name={t("ac.chart.resolved")} fill={COLORS.teal} radius={[3, 3, 0, 0]} />
                  <Line dataKey="open_at_end" name={t("ac.chart.openAtEnd")} stroke={COLORS.amber} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title={t("ac.chart.overdueByOwner")} loading={q.isLoading} empty={false}>
              {(d?.overdue_by_owner ?? []).length === 0 ? <EmptyState title={t("ac.chart.none")} hint="" icon={CheckCircle2} /> : (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={d!.overdue_by_owner} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid stroke={GRID} horizontal={false} />
                    <XAxis type="number" allowDecimals={false} {...AXIS} />
                    <YAxis type="category" dataKey="owner" width={140} {...AXIS} tick={{ fontSize: 11 }} />
                    <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtNumber(v, lang)} />} />
                    <Bar dataKey="count" name={t("ac.kpi.overdueActions")} fill={COLORS.red} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
            <ChartCard title={t("ac.chart.byLine")} loading={q.isLoading} empty={!!d && d.by_line.length === 0}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={d?.by_line ?? []} margin={{ left: 0, right: 8, top: 8 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="line_code" {...AXIS} />
                  <YAxis allowDecimals={false} width={32} {...AXIS} />
                  <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtNumber(v, lang)} />} />
                  <Bar dataKey="count" name={t("ac.kpi.open")} fill={COLORS.blue} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title={t("ac.chart.byCategory")} loading={q.isLoading} empty={!!d && d.by_category.length === 0} className="xl:col-span-2">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={(d?.by_category ?? []).map((x) => ({ ...x, label: t(`ac.category.${x.category}` as TKey) }))} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" allowDecimals={false} {...AXIS} />
                  <YAxis type="category" dataKey="label" width={150} {...AXIS} tick={{ fontSize: 11 }} />
                  <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtNumber(v, lang)} />} />
                  <Bar dataKey="count" name={t("ac.kpi.open")} radius={[0, 4, 4, 0]}>
                    {(d?.by_category ?? []).map((x, i) => <Cell key={x.category} fill={SERIES[i % SERIES.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <div className="grid gap-4 2xl:grid-cols-5">
            <Card className="2xl:col-span-3">
              <CardHeader><CardTitle>{t("ac.priority")}</CardTitle><CardDescription>{t("ac.priorityDesc")}</CardDescription></CardHeader>
              <CardContent>
                {(d?.priority_issues ?? []).length === 0 ? <EmptyState title={t("ac.list.empty")} hint="" /> : (
                  <div className="overflow-auto rounded-md border">
                    <Table data-testid="ac-priority">
                      <TableHeader><TableRow>
                        <TableHead>{t("ac.field.number")}</TableHead><TableHead>{t("ac.field.title")}</TableHead>
                        <TableHead>{t("ac.field.severity")}</TableHead><TableHead>{t("ac.field.status")}</TableHead>
                        <TableHead>{t("ac.field.owner")}</TableHead><TableHead>{t("ac.field.due")}</TableHead>
                      </TableRow></TableHeader>
                      <TableBody>
                        {d!.priority_issues.map((i) => (
                          <TableRow key={i.id}>
                            <TableCell className="whitespace-nowrap font-medium"><Link className="text-primary hover:underline" to={`/actions/investigations/${i.id}`}>{i.number}</Link></TableCell>
                            <TableCell className="max-w-[240px] truncate" title={i.title}>{i.title}</TableCell>
                            <TableCell><SevBadge s={i.severity} /></TableCell>
                            <TableCell><StatusBadge s={i.status} /></TableCell>
                            <TableCell className="text-xs"><OwnerLabel u={i.owner} /></TableCell>
                            <TableCell className="whitespace-nowrap text-xs">{fmtDate(i.due_date, lang)} <OverdueBadge days={i.overdue ? i.days_overdue : null} /></TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className="2xl:col-span-2">
              <CardHeader><CardTitle>{t("ac.overdueActions")}</CardTitle></CardHeader>
              <CardContent>
                {(d?.overdue_actions_list ?? []).length === 0 ? <EmptyState title={t("ac.chart.none")} hint="" icon={CheckCircle2} /> : (
                  <div className="overflow-auto rounded-md border">
                    <Table data-testid="ac-overdue-actions">
                      <TableHeader><TableRow>
                        <TableHead>{t("ac.actions.title")}</TableHead><TableHead>{t("ac.field.number")}</TableHead>
                        <TableHead>{t("ac.actions.owner")}</TableHead><TableHead>{t("ac.field.due")}</TableHead>
                      </TableRow></TableHeader>
                      <TableBody>
                        {d!.overdue_actions_list.map((a) => (
                          <TableRow key={a.id}>
                            <TableCell className="max-w-[260px]"><div className="truncate" title={a.title}>{a.title}</div><div className="text-[11px] text-muted-foreground">{a.code}</div></TableCell>
                            <TableCell className="whitespace-nowrap"><Link className="text-primary hover:underline" to={`/actions/investigations/${a.investigation.id}`}>{a.investigation.number}</Link></TableCell>
                            <TableCell className="text-xs"><OwnerLabel u={a.owner} /></TableCell>
                            <TableCell className="whitespace-nowrap text-xs">{fmtDate(a.due_date, lang)} <OverdueBadge days={a.days_overdue} /></TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

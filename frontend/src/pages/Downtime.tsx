import { useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, Clock, Hash, Search, Timer, Wrench, Zap } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { FilterBar } from "@/components/FilterBar";
import { ChartCard } from "@/components/ChartCard";
import { ChartTooltip } from "@/components/ChartTooltip";
import { ExportButtons } from "@/components/ExportButtons";
import { KpiCard } from "@/components/KpiCard";
import { ErrorState } from "@/components/States";
import { ParetoChart } from "./Quality";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AXIS, COLORS, GRID, SERIES } from "@/components/charts";
import { filterParams } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { useI18n } from "@/lib/i18n";
import { useDowntime, useDowntimeEvents } from "@/lib/queries";
import { fmtCompact, fmtDate, fmtNumber, fmtPct, fmtShortDate } from "@/lib/format";

function EventsTable() {
  const { t, lang, tv } = useI18n();
  const { filters, ready } = useFilters();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState({ key: "date", dir: "desc" });
  const [page, setPage] = useState(1);
  const pageSize = 15;
  const q = useDowntimeEvents(filters, { search, sort: sort.key, order: sort.dir, page, pageSize }, ready);
  const pages = Math.max(1, Math.ceil((q.data?.total ?? 0) / pageSize));
  const cols: { key: string; label: string; numeric?: boolean }[] = [
    { key: "date", label: t("common.date") }, { key: "line", label: t("common.line") },
    { key: "machine", label: t("common.machine") }, { key: "shift", label: t("common.shift") },
    { key: "reason", label: t("downtime.reason") }, { key: "minutes", label: t("common.minutes"), numeric: true },
  ];
  const toggle = (k: string) => { setPage(1); setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "desc" })); };
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
          <Input className="pl-8" value={search} placeholder={t("common.search")} aria-label={t("common.search")}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }} data-testid="downtime-search" />
        </div>
        <span className="ml-auto text-xs text-muted-foreground">{t("common.rows", { n: fmtNumber(q.data?.total ?? 0, lang) })}</span>
      </div>
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div className="overflow-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                {cols.map((c) => (
                  <TableHead key={c.key} className={c.numeric ? "text-right" : undefined} aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className="inline-flex items-center gap-1 hover:text-slate-900" onClick={() => toggle(c.key)}>
                      {c.label}{sort.key === c.key ? (sort.dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />) : <ArrowUpDown className="h-3 w-3 opacity-30" />}
                    </button>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {!q.data ? (
                <TableRow><TableCell colSpan={6}><Skeleton className="h-40" /></TableCell></TableRow>
              ) : q.data.rows.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">{t("common.noData")}</TableCell></TableRow>
              ) : q.data.rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="tabular">{fmtDate(r.date, lang)}</TableCell>
                  <TableCell>{r.line_code}</TableCell>
                  <TableCell><span className="font-medium">{r.machine_code}</span> <span className="text-xs text-muted-foreground">{r.machine_name}</span></TableCell>
                  <TableCell>{r.shift_code}</TableCell>
                  <TableCell>{tv(r.reason)}</TableCell>
                  <TableCell className="tabular text-right font-medium">{fmtNumber(r.minutes, lang)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="mt-3 flex items-center justify-end gap-2 text-xs">
        <span className="text-muted-foreground">{t("common.page", { page, pages })}</span>
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>{t("common.previous")}</Button>
        <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>{t("common.next")}</Button>
      </div>
    </div>
  );
}

export default function DowntimePage() {
  const { t, lang, tv } = useI18n();
  const { filters, ready } = useFilters();
  const q = useDowntime(filters, ready);
  const d = q.data;
  const empty = !!d && !d.kpis.planned_production_minutes.value;
  const flagged = (d?.by_machine ?? []).filter((m) => m.is_high_downtime);
  const pareto = (d?.pareto ?? []).map((p) => ({ ...p, category: tv(p.category) }));

  // stacked daily trend: top 5 reasons + "other"
  const { trend, reasons } = useMemo(() => {
    if (!d) return { trend: [], reasons: [] as string[] };
    const top = d.by_reason.slice(0, 5).map((r) => r.reason);
    const map = new Map<string, Record<string, number | string>>();
    for (const r of d.daily_by_reason) {
      const row = map.get(r.date) ?? { date: r.date, label: fmtShortDate(r.date, lang) };
      const key = top.includes(r.reason) ? r.reason : "__other";
      row[key] = ((row[key] as number) ?? 0) + r.minutes;
      map.set(r.date, row);
    }
    for (const day of d.daily) if (!map.has(day.date)) map.set(day.date, { date: day.date, label: fmtShortDate(day.date, lang) });
    const rows = [...map.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    return { trend: rows, reasons: d.by_reason.length > 5 ? [...top, "__other"] : top };
  }, [d, lang]);

  const ud = d?.kpis.unplanned_downtime_minutes.value ?? null;
  return (
    <div>
      <PageHeader title={t("downtime.title")} subtitle={t("downtime.subtitle")}
        actions={<ExportButtons path={(fmt) => `/api/exports/downtime-events${filterParams(filters, { format: fmt })}`} />} />
      <FilterBar />
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !d ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[108px]" />)}</div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <KpiCard label={t("kpi.unplanned_downtime_minutes")} value={ud == null ? "N/A" : `${fmtNumber(ud, lang)} min`} metric={d.kpis.unplanned_downtime_minutes}
            formula={t("formula.unplanned_downtime_minutes")} sub={ud == null ? undefined : `≈ ${fmtNumber(ud / 60, lang, 1)} h`} icon={Clock} tone="amber" testId="kpi-downtime" />
          <KpiCard label={t("kpi.planned_downtime_minutes")} value={d.kpis.planned_downtime_minutes.value == null ? "N/A" : `${fmtNumber(d.kpis.planned_downtime_minutes.value, lang)} min`} metric={d.kpis.planned_downtime_minutes} icon={Wrench} tone="slate" />
          <KpiCard label={t("kpi.availability")} value={fmtPct(d.kpis.availability.value, lang)} metric={d.kpis.availability} formula={t("formula.availability")} icon={Zap} tone="blue" />
          <KpiCard label={t("downtime.events")} value={fmtNumber(d.event_count, lang)} icon={Hash} tone="violet" />
          <KpiCard label={t("downtime.avgPerEvent")} value={d.event_count && ud != null ? `${fmtNumber(ud / d.event_count, lang, 1)} min` : "N/A"} icon={Timer} tone="teal" />
        </div>
      )}

      {flagged.length > 0 && (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3" role="status" data-testid="high-downtime-alert">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
          <div>
            <div className="text-sm font-semibold text-red-800">
              {t("downtime.highAlert", { machines: flagged.map((m) => `${m.machine_code} (${fmtPct(m.downtime_ratio, lang)})`).join(", ") })}
            </div>
            <div className="text-xs text-red-700">{t("downtime.highAlertDesc")}</div>
          </div>
        </div>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <ChartCard title={t("downtime.pareto")} description={t("downtime.paretoDesc")} loading={!d} empty={!pareto.length} height={300}>
          <ParetoChart items={pareto} valueLabel={t("common.minutes")} valueFormatter={(v) => `${fmtNumber(v, lang)} min`} color={COLORS.amber} />
        </ChartCard>
        <ChartCard title={t("downtime.byMachine")} description={t("downtime.byMachineDesc")} loading={!d} empty={empty} height={300}>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={d?.by_machine ?? []} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="machine_code" {...AXIS} />
              <YAxis {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <Tooltip content={<ChartTooltip valueFormatter={(v) => `${fmtNumber(v, lang)} min`} />} />
              <Bar dataKey="unplanned_downtime_minutes" name={t("common.minutes")} radius={[3, 3, 0, 0]}>
                {(d?.by_machine ?? []).map((m) => <Cell key={m.machine_code} fill={m.is_high_downtime ? COLORS.red : COLORS.amber} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard className="xl:col-span-2" title={t("downtime.trend")} description={t("downtime.trendDesc")} loading={!d} empty={empty} height={280}>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={trend} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={24} />
              <YAxis {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <Tooltip content={<ChartTooltip valueFormatter={(v) => `${fmtNumber(v ?? 0, lang)} min`} />} />
              <Legend />
              {reasons.map((r, i) => (
                <Area key={r} type="monotone" dataKey={r} stackId="1" name={r === "__other" ? t("downtime.other") : tv(r)}
                  stroke={SERIES[i % SERIES.length]} fill={SERIES[i % SERIES.length]} fillOpacity={0.55} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {(["by_line", "by_shift"] as const).map((key) => (
          <Card key={key}>
            <CardHeader><CardTitle>{t(key === "by_line" ? "downtime.byLine" : "downtime.byShift")}</CardTitle></CardHeader>
            <CardContent>
              {!d ? <Skeleton className="h-32" /> : (
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>{t(key === "by_line" ? "common.line" : "common.shift")}</TableHead>
                    <TableHead className="text-right">{t("common.minutes")}</TableHead>
                    <TableHead className="text-right">{t("downtime.share")}</TableHead>
                    <TableHead className="text-right">{t("kpi.availability")}</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {d[key].map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-medium">{key === "by_line" ? `${r.line_code} · ${r.line_name}` : `${r.shift_code} · ${r.shift_name}`}</TableCell>
                        <TableCell className="tabular text-right">{fmtNumber(r.unplanned_downtime_minutes, lang)}</TableCell>
                        <TableCell className="tabular text-right">{fmtPct(r.downtime_ratio, lang)}</TableCell>
                        <TableCell className="tabular text-right">{fmtPct(r.availability, lang)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("downtime.byMachine")}</CardTitle><CardDescription>{t("downtime.byMachineDesc")}</CardDescription></CardHeader>
        <CardContent>
          {!d ? <Skeleton className="h-40" /> : (
            <Table>
              <TableHeader><TableRow>
                <TableHead>{t("common.machine")}</TableHead><TableHead>{t("common.line")}</TableHead>
                <TableHead className="text-right">{t("common.minutes")}</TableHead><TableHead className="text-right">{t("downtime.share")}</TableHead>
                <TableHead className="text-right">{t("kpi.availability")}</TableHead><TableHead />
              </TableRow></TableHeader>
              <TableBody>
                {d.by_machine.map((m) => (
                  <TableRow key={m.machine_code} className={m.is_high_downtime ? "bg-red-50/60" : undefined}>
                    <TableCell className="font-medium">{m.machine_code} <span className="text-xs font-normal text-muted-foreground">{m.machine_name}</span></TableCell>
                    <TableCell>{m.line_code}</TableCell>
                    <TableCell className="tabular text-right">{fmtNumber(m.unplanned_downtime_minutes, lang)}</TableCell>
                    <TableCell className="tabular text-right">{fmtPct(m.downtime_ratio, lang)}</TableCell>
                    <TableCell className="tabular text-right">{fmtPct(m.availability, lang)}</TableCell>
                    <TableCell>{m.is_high_downtime && <Badge variant="danger"><AlertTriangle className="h-3 w-3" />{t("downtime.highBadge")}</Badge>}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("downtime.table")}</CardTitle><CardDescription>{t("downtime.tableDesc")}</CardDescription></CardHeader>
        <CardContent><EventsTable /></CardContent>
      </Card>
    </div>
  );
}

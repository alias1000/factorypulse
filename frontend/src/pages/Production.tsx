import { useMemo, useState } from "react";
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { FilterBar } from "@/components/FilterBar";
import { ChartCard } from "@/components/ChartCard";
import { ChartTooltip } from "@/components/ChartTooltip";
import { DataTable, type Column } from "@/components/DataTable";
import { ExportButtons } from "@/components/ExportButtons";
import { ErrorState } from "@/components/States";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AXIS, COLORS, GRID } from "@/components/charts";
import { filterParams } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { useI18n, type TKey } from "@/lib/i18n";
import { useBreakdown, useComparison } from "@/lib/queries";
import { fmtCompact, fmtDate, fmtNumber, fmtPct, fmtPp, fmtRel, fmtShortDate, isNum } from "@/lib/format";
import type { AggRow, ComparisonRow } from "@/lib/types";
import { cn } from "@/lib/utils";

type Dim = "day" | "week" | "shift" | "machine" | "line" | "product";
const DIMS: Dim[] = ["day", "week", "shift", "machine", "line", "product"];
const HIGHER_IS_BETTER: Record<string, boolean> = {
  total_production: true, planned_production: true, target_achievement: true, defect_rate: false, quality: true,
  availability: true, performance: true, oee: true, unplanned_downtime_minutes: false, units_per_operating_hour: true,
};

function labelOf(r: AggRow, dim: Dim, lang: "de" | "en"): string {
  switch (dim) {
    case "day": return fmtDate(r.date!, lang);
    case "week": return r.week!;
    case "shift": return `${r.shift_code} · ${r.shift_name}`;
    case "machine": return r.machine_code!;
    case "line": return r.line_code!;
    case "product": return r.product_code!;
  }
}

function ComparisonTile({ row }: { row: ComparisonRow }) {
  const { t, lang } = useI18n();
  const isRatio = row.unit === "ratio";
  const fmt = (v: number | null) => (isRatio ? fmtPct(v, lang) : row.metric === "units_per_operating_hour" ? fmtNumber(v, lang, 1) : fmtNumber(v, lang));
  const delta = isRatio ? row.delta : row.relative_change;
  const good = isNum(delta) && delta !== 0 ? (delta > 0) === HIGHER_IS_BETTER[row.metric] : null;
  const Icon = !isNum(delta) || delta === 0 ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <div className="rounded-md border bg-white p-3">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{t(`kpi.${row.metric}` as TKey)}</div>
      <div className="tabular mt-0.5 text-lg font-semibold">{fmt(row.current)}</div>
      <div className="mt-0.5 flex items-center gap-1 text-xs">
        <span className={cn("inline-flex items-center gap-0.5 font-medium", good === null ? "text-slate-500" : good ? "text-emerald-600" : "text-red-600")}>
          <Icon className="h-3.5 w-3.5" aria-hidden />
          {isRatio ? fmtPp(delta, lang) : fmtRel(delta, lang)}
        </span>
        <span className="text-muted-foreground">vs. {fmt(row.previous)}</span>
      </div>
    </div>
  );
}

export default function ProductionPage() {
  const { t, lang } = useI18n();
  const { filters, ready } = useFilters();
  const [dim, setDim] = useState<Dim>("day");
  const [belowTarget, setBelowTarget] = useState(false);
  const q = useBreakdown(filters, dim, ready);
  const weekly = useBreakdown(filters, "week", ready);
  const cmp = useComparison(filters, ready);
  const rows = q.data?.rows ?? [];
  const chartRows = rows.map((r) => ({ ...r, label: dim === "day" ? fmtShortDate(r.date!, lang) : labelOf(r, dim, lang) }));
  const tableRows = belowTarget ? rows.filter((r) => isNum(r.target_achievement) && r.target_achievement < 1) : rows;
  const dimLabel = t(`production.dim.${dim}` as TKey);

  const columns = useMemo<Column<AggRow>[]>(() => {
    const pct = (k: keyof AggRow) => (r: AggRow) => fmtPct(r[k] as number | null, lang);
    const num = (k: keyof AggRow, d = 0) => (r: AggRow) => fmtNumber(r[k] as number | null, lang, d);
    const first: Column<AggRow> = {
      key: "label", header: dimLabel,
      value: (r) => (dim === "day" ? r.date : labelOf(r, dim, lang)),
      render: (r) => (
        <span className="font-medium text-slate-800">
          {labelOf(r, dim, lang)}
          {dim === "machine" && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{r.machine_name}</span>}
          {dim === "line" && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{r.line_name}</span>}
          {dim === "product" && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{r.product_name}</span>}
        </span>
      ),
    };
    const ratioCell = (k: keyof AggRow, good: (v: number) => boolean) => (r: AggRow) => {
      const v = r[k] as number | null;
      return <span className={cn(isNum(v) && !good(v) && "font-medium text-red-600")}>{fmtPct(v, lang)}</span>;
    };
    return [
      first,
      { key: "planned_qty", header: t("common.planned"), numeric: true, value: (r) => r.planned_qty, render: num("planned_qty") },
      { key: "actual_qty", header: t("common.actual"), numeric: true, value: (r) => r.actual_qty, render: num("actual_qty") },
      { key: "target_achievement", header: t("kpi.target_achievement"), numeric: true, value: (r) => r.target_achievement, render: ratioCell("target_achievement", (v) => v >= 1) },
      { key: "defect_rate", header: t("kpi.defect_rate"), numeric: true, value: (r) => r.defect_rate, render: pct("defect_rate") },
      { key: "unplanned_downtime_minutes", header: t("kpi.unplanned_downtime_minutes"), numeric: true, value: (r) => r.unplanned_downtime_minutes, render: num("unplanned_downtime_minutes") },
      { key: "availability", header: t("kpi.availability"), numeric: true, value: (r) => r.availability, render: pct("availability") },
      { key: "performance", header: t("kpi.performance"), numeric: true, value: (r) => r.performance, render: pct("performance") },
      { key: "quality", header: t("kpi.quality"), numeric: true, value: (r) => r.quality, render: pct("quality") },
      { key: "oee", header: "OEE", numeric: true, value: (r) => r.oee, render: (r) => <span className="font-semibold">{fmtPct(r.oee, lang)}</span> },
      { key: "units_per_operating_hour", header: t("kpi.units_per_operating_hour"), numeric: true, value: (r) => r.units_per_operating_hour, render: num("units_per_operating_hour", 1) },
    ];
  }, [dim, lang, t, dimLabel]);

  return (
    <div>
      <PageHeader title={t("production.title")} subtitle={t("production.subtitle")} />
      <FilterBar />

      <Card className="mb-5">
        <CardHeader>
          <CardTitle>{t("production.comparison")}</CardTitle>
          <CardDescription>
            {cmp.data
              ? t("production.comparisonDesc", { prev: `${fmtDate(cmp.data.previous_period.start, lang)} – ${fmtDate(cmp.data.previous_period.end, lang)}` })
              : "…"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {cmp.isError ? <ErrorState error={cmp.error} onRetry={() => cmp.refetch()} /> : !cmp.data ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-20" />)}</div>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5" data-testid="comparison">
              {cmp.data.metrics.filter((m) => m.metric !== "planned_production").map((m) => <ComparisonTile key={m.metric} row={m} />)}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium text-slate-600">{t("production.groupBy")}</span>
        <Tabs value={dim} onValueChange={(v) => setDim(v as Dim)}>
          <TabsList>
            {DIMS.map((d) => <TabsTrigger key={d} value={d} data-testid={`dim-${d}`}>{t(`production.dim.${d}` as TKey)}</TabsTrigger>)}
          </TabsList>
        </Tabs>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard className="xl:col-span-2" title={t("production.chart", { dim: dimLabel })} loading={q.isLoading || !ready} error={q.isError ? q.error : undefined} onRetry={() => q.refetch()} empty={!rows.length} height={300}>
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartRows} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={16} />
              <YAxis yAxisId="u" {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <YAxis yAxisId="r" orientation="right" {...AXIS} tickFormatter={(v) => fmtPct(v, lang, 0)} width={48} domain={[0, "auto"]} />
              <Tooltip content={<ChartTooltip valueFormatter={(v, k) => (k === "target_achievement" ? fmtPct(v, lang) : fmtNumber(v, lang))} />} />
              <Legend />
              <Bar yAxisId="u" dataKey="planned_qty" name={t("common.planned")} fill={COLORS.blueLight} radius={[2, 2, 0, 0]} />
              <Bar yAxisId="u" dataKey="actual_qty" name={t("common.actual")} fill={COLORS.blue} radius={[2, 2, 0, 0]} />
              <Line yAxisId="r" dataKey="target_achievement" name={t("kpi.target_achievement")} stroke={COLORS.amber} strokeWidth={2} dot={dim !== "day"} />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title={t("production.productivity")} description={t("production.productivityDesc")} loading={weekly.isLoading || !ready} empty={!weekly.data?.rows.length} height={300}>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={weekly.data?.rows ?? []} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="week" {...AXIS} tickFormatter={(w: string) => w.slice(5)} />
              <YAxis yAxisId="u" {...AXIS} width={40} tickFormatter={(v) => fmtNumber(v, lang)} />
              <YAxis yAxisId="r" orientation="right" {...AXIS} domain={[0, 1]} tickFormatter={(v) => fmtPct(v, lang, 0)} width={44} />
              <Tooltip content={<ChartTooltip valueFormatter={(v, k) => (k === "oee" ? fmtPct(v, lang) : fmtNumber(v, lang, 1))} />} />
              <Legend />
              <Line yAxisId="u" dataKey="units_per_operating_hour" name={t("kpi.units_per_operating_hour")} stroke={COLORS.violet} strokeWidth={2} />
              <Line yAxisId="r" dataKey="oee" name="OEE" stroke={COLORS.teal} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <Card className="mt-4">
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div>
            <CardTitle>{t("production.table")} · {dimLabel}</CardTitle>
            <CardDescription className="mt-1">{t("production.tableDesc")}</CardDescription>
          </div>
          <ExportButtons path={(fmt) => `/api/exports/production${filterParams(filters, { dimension: dim, format: fmt })}`} />
        </CardHeader>
        <CardContent>
          {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
            <DataTable
              testId="production-table"
              rows={tableRows}
              columns={columns}
              rowKey={(r, i) => `${labelOf(r, dim, lang)}-${i}`}
              initialSort={{ key: "label", dir: dim === "day" ? "desc" : "asc" }}
              toolbar={
                <div className="flex items-center gap-2">
                  <Switch id="below" checked={belowTarget} onCheckedChange={setBelowTarget} />
                  <Label htmlFor="below">{t("common.belowTarget")}</Label>
                </div>
              }
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

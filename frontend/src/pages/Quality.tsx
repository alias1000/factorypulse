import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Info } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { FilterBar } from "@/components/FilterBar";
import { ChartCard } from "@/components/ChartCard";
import { ChartTooltip } from "@/components/ChartTooltip";
import { DataTable } from "@/components/DataTable";
import { ExportButtons } from "@/components/ExportButtons";
import { ErrorState } from "@/components/States";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AXIS, COLORS, GRID } from "@/components/charts";
import { KpiGrid } from "./Overview";
import { filterParams } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { useI18n, type TKey } from "@/lib/i18n";
import { useQuality } from "@/lib/queries";
import { fmtCompact, fmtNumber, fmtPct, fmtShortDate } from "@/lib/format";
import type { AggRow, Kpis, ParetoItem } from "@/lib/types";

type Dim = "machine" | "line" | "product" | "shift";

export function ParetoChart({ items, valueLabel, valueFormatter, color = COLORS.blue }: {
  items: ParetoItem[]; valueLabel: string; valueFormatter: (v: number | null | undefined) => string; color?: string;
}) {
  const { lang, t } = useI18n();
  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={items} margin={{ left: 24, right: 8, top: 8, bottom: 30 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="category" {...AXIS} interval={0} angle={-25} textAnchor="end" height={70} tickFormatter={(v: string) => (v.length > 18 ? `${v.slice(0, 17)}…` : v)} />
        <YAxis yAxisId="v" {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
        <YAxis yAxisId="c" orientation="right" {...AXIS} domain={[0, 1]} tickFormatter={(v) => fmtPct(v, lang, 0)} width={44} />
        <Tooltip content={<ChartTooltip valueFormatter={(v, k) => (k === "cumulative_share" ? fmtPct(v, lang) : valueFormatter(v))} />} />
        <Legend verticalAlign="top" height={28} />
        <ReferenceLine yAxisId="c" y={0.8} stroke={COLORS.slate} strokeDasharray="4 4" />
        <Bar yAxisId="v" dataKey="value" name={valueLabel} fill={color} radius={[3, 3, 0, 0]} />
        <Line yAxisId="c" dataKey="cumulative_share" name={t("quality.cumulative")} stroke={COLORS.teal} strokeWidth={2} dot={{ r: 3 }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export default function QualityPage() {
  const { t, lang, tv } = useI18n();
  const { filters, ready } = useFilters();
  const q = useQuality(filters, ready);
  const [dim, setDim] = useState<Dim>("machine");
  const d = q.data;
  const empty = !!d && !d.kpis.total_production.value;
  const daily = (d?.daily ?? []).map((r) => ({ ...r, label: fmtShortDate(r.date!, lang) }));
  const pareto = (d?.pareto ?? []).map((p) => ({ ...p, category: tv(p.category) }));
  const dimRows: AggRow[] = d ? { machine: d.by_machine, line: d.by_line, product: d.by_product, shift: d.by_shift }[dim] : [];
  const lbl = (r: AggRow) => (dim === "machine" ? r.machine_code : dim === "line" ? r.line_code : dim === "product" ? r.product_code : r.shift_code) ?? "";
  const avg = d?.kpis.defect_rate.value ?? null;

  return (
    <div>
      <PageHeader title={t("quality.title")} subtitle={t("quality.subtitle")}
        actions={<ExportButtons path={(fmt) => `/api/exports/quality${filterParams(filters, { format: fmt })}`} />} />
      <FilterBar />
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !d ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[108px]" />)}</div>
      ) : (
        <KpiGrid kpis={d.kpis as Kpis} keys={["total_production", "good_units", "defective_units", "defect_rate", "quality"]} className="md:grid-cols-3 xl:grid-cols-5 2xl:grid-cols-5" />
      )}

      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <ChartCard title={t("quality.trend")} description={t("quality.trendDesc")} loading={!d} empty={empty} height={300}>
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={daily} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={24} />
              <YAxis yAxisId="u" {...AXIS} width={40} tickFormatter={(v) => fmtCompact(v, lang)} />
              <YAxis yAxisId="r" orientation="right" {...AXIS} width={52} tickFormatter={(v) => fmtPct(v, lang, 1)} />
              <Tooltip content={<ChartTooltip valueFormatter={(v, k) => (k === "defect_rate" ? fmtPct(v, lang, 2) : fmtNumber(v, lang))} />} />
              <Legend />
              <Bar yAxisId="u" dataKey="defective_qty" name={t("kpi.defective_units")} fill={COLORS.redLight} />
              <Line yAxisId="r" dataKey="defect_rate" name={t("kpi.defect_rate")} stroke={COLORS.red} strokeWidth={2} dot={false} />
              {avg != null && <ReferenceLine yAxisId="r" y={avg} stroke={COLORS.slate} strokeDasharray="4 4" />}
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title={t("quality.pareto")} description={t("quality.paretoDesc")} loading={!d} empty={!pareto.length} height={300}>
          <div data-testid="quality-pareto">
            <ParetoChart items={pareto} valueLabel={t("quality.count")} valueFormatter={(v) => fmtNumber(v, lang)} color={COLORS.red} />
          </div>
          {!!d?.uncategorized_defects && (
            <p className="mt-2 flex items-center gap-1 text-xs text-amber-700"><Info className="h-3.5 w-3.5" />
              {t("quality.uncategorized", { n: fmtNumber(d.uncategorized_defects, lang) })}</p>
          )}
        </ChartCard>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
            <CardTitle>{t(`quality.by${dim[0].toUpperCase()}${dim.slice(1)}` as TKey)}</CardTitle>
            <Tabs value={dim} onValueChange={(v) => setDim(v as Dim)}>
              <TabsList>
                {(["machine", "line", "product", "shift"] as Dim[]).map((x) => (
                  <TabsTrigger key={x} value={x}>{t(`production.dim.${x}` as TKey)}</TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </CardHeader>
          <CardContent>
            {!d ? <Skeleton className="h-[300px]" /> : (
              <ResponsiveContainer width="100%" height={Math.max(220, dimRows.length * 26 + 40)}>
                <BarChart data={dimRows.map((r) => ({ ...r, label: lbl(r) }))} layout="vertical" margin={{ left: 8, right: 24 }}>
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" {...AXIS} tickFormatter={(v) => fmtPct(v, lang, 1)} />
                  <YAxis type="category" dataKey="label" {...AXIS} width={70} />
                  <Tooltip content={<ChartTooltip valueFormatter={(v) => fmtPct(v, lang, 2)} />} />
                  {avg != null && <ReferenceLine x={avg} stroke={COLORS.navy} strokeDasharray="4 4" label={{ value: "Ø", position: "top", fontSize: 11 }} />}
                  <Bar dataKey="defect_rate" name={t("kpi.defect_rate")} radius={[0, 3, 3, 0]}>
                    {dimRows.map((r, i) => <Cell key={i} fill={avg != null && (r.defect_rate ?? 0) > avg * 1.2 ? COLORS.red : COLORS.blue} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>{t("quality.pareto")}</CardTitle>
            <CardDescription>{t("quality.category")} · {t("quality.count")} · {t("quality.share")}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable searchable={false} rows={pareto} rowKey={(r) => r.category} initialSort={{ key: "value", dir: "desc" }}
              columns={[
                { key: "category", header: t("quality.category"), value: (r) => r.category },
                { key: "value", header: t("quality.count"), numeric: true, value: (r) => r.value, render: (r) => fmtNumber(r.value, lang) },
                { key: "share", header: t("quality.share"), numeric: true, value: (r) => r.share, render: (r) => fmtPct(r.share, lang) },
                { key: "cumulative_share", header: t("quality.cumulative"), numeric: true, value: (r) => r.cumulative_share, render: (r) => fmtPct(r.cumulative_share, lang) },
              ]} />
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t(`quality.by${dim[0].toUpperCase()}${dim.slice(1)}` as TKey)} · {t("production.table")}</CardTitle></CardHeader>
        <CardContent>
          <DataTable rows={dimRows} rowKey={(r, i) => `${lbl(r)}-${i}`} initialSort={{ key: "defect_rate", dir: "desc" }}
            columns={[
              { key: "label", header: t(`production.dim.${dim}` as TKey), value: (r) => lbl(r),
                render: (r) => <span className="font-medium">{lbl(r)} <span className="text-xs font-normal text-muted-foreground">{r.machine_name ?? r.line_name ?? r.product_name ?? r.shift_name}</span></span> },
              { key: "actual_qty", header: t("common.actual"), numeric: true, value: (r) => r.actual_qty, render: (r) => fmtNumber(r.actual_qty, lang) },
              { key: "good_qty", header: t("kpi.good_units"), numeric: true, value: (r) => r.good_qty, render: (r) => fmtNumber(r.good_qty, lang) },
              { key: "defective_qty", header: t("kpi.defective_units"), numeric: true, value: (r) => r.defective_qty, render: (r) => fmtNumber(r.defective_qty, lang) },
              { key: "defect_rate", header: t("kpi.defect_rate"), numeric: true, value: (r) => r.defect_rate, render: (r) => fmtPct(r.defect_rate, lang, 2) },
              { key: "quality", header: t("kpi.quality"), numeric: true, value: (r) => r.quality, render: (r) => fmtPct(r.quality, lang, 2) },
            ]} />
        </CardContent>
      </Card>
    </div>
  );
}

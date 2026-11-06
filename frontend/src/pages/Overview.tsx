import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import {
  AlertOctagon, CheckCircle2, Clock, Factory, Gauge, Package, Percent, Target, TrendingUp, Wrench, Zap,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { ChartCard } from "@/components/ChartCard";
import { ChartTooltip } from "@/components/ChartTooltip";
import { ErrorState } from "@/components/States";
import { Skeleton } from "@/components/ui/skeleton";
import { AXIS, COLORS, GRID } from "@/components/charts";
import { useFilters } from "@/lib/filters";
import { useI18n } from "@/lib/i18n";
import { useOverview } from "@/lib/queries";
import { fmtCompact, fmtNumber, fmtPct, fmtShortDate, fmtDate } from "@/lib/format";
import type { KpiKey, Kpis } from "@/lib/types";
import { cn } from "@/lib/utils";

export function KpiGrid({ kpis, keys, className }: { kpis: Kpis; keys: KpiKey[]; className?: string }) {
  const { t, lang } = useI18n();
  const ICONS: Partial<Record<KpiKey, [typeof Gauge, "blue" | "teal" | "amber" | "red" | "slate" | "violet"]>> = {
    total_production: [Factory, "blue"], planned_production: [Package, "slate"], target_achievement: [Target, "violet"],
    good_units: [CheckCircle2, "teal"], defective_units: [AlertOctagon, "red"], defect_rate: [Percent, "red"],
    unplanned_downtime_minutes: [Clock, "amber"], availability: [Zap, "blue"], performance: [TrendingUp, "violet"],
    quality: [CheckCircle2, "teal"], oee: [Gauge, "teal"], planned_downtime_minutes: [Wrench, "slate"],
    operating_minutes: [Clock, "teal"], planned_production_minutes: [Clock, "slate"],
  };
  const value = (k: KpiKey) => {
    const m = kpis[k];
    if (m.unit === "ratio") return fmtPct(m.value, lang);
    if (m.unit === "minutes") return `${fmtNumber(m.value, lang)} ${m.value == null ? "" : "min"}`.trim();
    return fmtNumber(m.value, lang);
  };
  const sub = (k: KpiKey): string | undefined => {
    if (k === "unplanned_downtime_minutes" && kpis.planned_downtime_minutes)
      return t("kpi.downtimeSub", { planned: fmtNumber(kpis.planned_downtime_minutes.value, lang) });
    if (k === "oee" && kpis.oee.components && kpis.oee.value != null) {
      const c = kpis.oee.components;
      return t("kpi.oeeSub", { a: fmtPct(c.availability, lang, 0), p: fmtPct(c.performance, lang, 0), q: fmtPct(c.quality, lang, 0) });
    }
    return undefined;
  };
  return (
    <div className={cn("grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-6 [&>div>*]:h-full", className)}>
      {keys.map((k) => {
        const [icon, tone] = ICONS[k] ?? [Gauge, "blue"];
        const fk = `formula.${k}` as Parameters<typeof t>[0];
        return (
          <div key={k} className={k === "oee" ? "col-span-2 md:col-span-1 xl:col-span-2" : undefined}>
          <KpiCard label={t(`kpi.${k}` as Parameters<typeof t>[0])} value={value(k)} metric={kpis[k]}
            formula={t(fk) !== fk ? t(fk) : undefined} sub={sub(k)} icon={icon} tone={tone} emphasis={k === "oee"} testId={`kpi-${k}`} />
          </div>
        );
      })}
    </div>
  );
}

const OVERVIEW_KPIS: KpiKey[] = [
  "total_production", "planned_production", "target_achievement", "good_units", "defective_units", "defect_rate",
  "unplanned_downtime_minutes", "availability", "performance", "quality", "oee",
];

export default function OverviewPage() {
  const { t, lang, tv } = useI18n();
  const { filters, ready } = useFilters();
  const q = useOverview(filters, ready);
  const d = q.data;
  const empty = !!d && d.kpis.record_count === 0;
  const daily = (d?.daily ?? []).map((r) => ({ ...r, label: fmtShortDate(r.date!, lang) }));
  const fmtUnits = (v: number | null | undefined) => fmtNumber(v, lang);
  const fmtRatio = (v: number | null | undefined) => fmtPct(v, lang);
  const lblDate = (l: string) => l;

  return (
    <div>
      <PageHeader title={t("overview.title")} subtitle={t("overview.subtitle")} />
      <FilterBar />
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !d ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">{Array.from({ length: 11 }).map((_, i) => <Skeleton key={i} className="h-[108px]" />)}</div>
      ) : (
        <KpiGrid kpis={d.kpis} keys={OVERVIEW_KPIS} />
      )}

      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <ChartCard title={t("overview.dailyTrend")} description={t("overview.dailyTrendDesc")} loading={q.isLoading || !ready} error={q.isError ? q.error : undefined} empty={empty}>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={daily} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={24} />
              <YAxis {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <Tooltip content={<ChartTooltip valueFormatter={fmtUnits} labelFormatter={lblDate} />} />
              <Legend />
              <Area type="monotone" dataKey="good_qty" name={t("common.good")} stackId="1" stroke={COLORS.teal} fill={COLORS.tealLight} fillOpacity={0.5} />
              <Area type="monotone" dataKey="defective_qty" name={t("common.defective")} stackId="1" stroke={COLORS.red} fill={COLORS.redLight} fillOpacity={0.8} />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t("overview.plannedVsActual")} description={t("overview.plannedVsActualDesc")} loading={q.isLoading || !ready} empty={empty}>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={daily} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={24} />
              <YAxis {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <Tooltip content={<ChartTooltip valueFormatter={fmtUnits} />} />
              <Legend />
              <Bar dataKey="actual_qty" name={t("common.actual")} fill={COLORS.blue} radius={[2, 2, 0, 0]} />
              <Line dataKey="planned_qty" name={t("common.planned")} stroke={COLORS.navy} strokeWidth={2} dot={false} type="stepAfter" />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t("overview.defectTrend")} description={t("overview.defectTrendDesc")} loading={q.isLoading || !ready} empty={empty}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={daily} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={24} />
              <YAxis {...AXIS} tickFormatter={(v) => fmtPct(v, lang, 1)} width={56} />
              <Tooltip content={<ChartTooltip valueFormatter={fmtRatio} />} />
              <Line dataKey="defect_rate" name={t("kpi.defect_rate")} stroke={COLORS.red} strokeWidth={2} dot={false} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t("overview.downtimeByCategory")} description={t("overview.downtimeByCategoryDesc")} loading={q.isLoading || !ready} empty={empty || !d?.downtime_by_reason.length}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={(d?.downtime_by_reason ?? []).map((r) => ({ ...r, label: tv(r.reason) }))} layout="vertical" margin={{ left: 8, right: 16 }}>
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} />
              <YAxis type="category" dataKey="label" {...AXIS} width={140} />
              <Tooltip content={<ChartTooltip valueFormatter={(v) => `${fmtNumber(v, lang)} min`} />} />
              <Bar dataKey="minutes" name={t("common.minutes")} fill={COLORS.amber} radius={[0, 3, 3, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t("overview.byLine")} description={t("overview.byLineDesc")} loading={q.isLoading || !ready} empty={empty}>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={d?.by_line ?? []} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="line_code" {...AXIS} />
              <YAxis yAxisId="u" {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <YAxis yAxisId="r" orientation="right" {...AXIS} domain={[0, 1]} tickFormatter={(v) => fmtPct(v, lang, 0)} width={54} />
              <Tooltip content={<ChartTooltip valueFormatter={(v, k) => (k === "oee" ? fmtRatio(v) : fmtUnits(v))} labelFormatter={(l) => d?.by_line.find((x) => x.line_code === l)?.line_name ?? l} />} />
              <Legend />
              <Bar yAxisId="u" dataKey="planned_qty" name={t("common.planned")} fill={COLORS.blueLight} radius={[2, 2, 0, 0]} />
              <Bar yAxisId="u" dataKey="actual_qty" name={t("common.actual")} fill={COLORS.blue} radius={[2, 2, 0, 0]} />
              <Line yAxisId="r" dataKey="oee" name="OEE" stroke={COLORS.teal} strokeWidth={2} />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t("overview.byShift")} description={t("overview.byShiftDesc")} loading={q.isLoading || !ready} empty={empty}>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={(d?.by_shift ?? []).map((s) => ({ ...s, label: `${s.shift_code} · ${s.shift_name}` }))} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" {...AXIS} />
              <YAxis yAxisId="u" {...AXIS} tickFormatter={(v) => fmtCompact(v, lang)} width={48} />
              <YAxis yAxisId="r" orientation="right" {...AXIS} domain={[0, 1]} tickFormatter={(v) => fmtPct(v, lang, 0)} width={54} />
              <Tooltip content={<ChartTooltip valueFormatter={(v, k) => (k === "oee" ? fmtRatio(v) : fmtUnits(v))} />} />
              <Legend />
              <Bar yAxisId="u" dataKey="actual_qty" name={t("common.actual")} fill={COLORS.navy} radius={[2, 2, 0, 0]} />
              <Line yAxisId="r" dataKey="oee" name="OEE" stroke={COLORS.teal} strokeWidth={2} />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
      {d && d.kpis.record_count > 0 && (
        <p className="mt-4 text-[11px] text-muted-foreground">
          {d.kpis.record_count} {t("common.records")} · {fmtDate(filters.startDate, lang)} – {fmtDate(filters.endDate, lang)} · {t("formula.oee")}
        </p>
      )}
    </div>
  );
}

import { CalendarRange, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useFilters } from "@/lib/filters";
import { useI18n } from "@/lib/i18n";
import { useMeta } from "@/lib/queries";
import { fmtDate } from "@/lib/format";

type FilterKey = "line" | "machine" | "shift" | "product";

function EntitySelect({ id, label, value, onChange, options, allLabel }: {
  id: string; label: string; value: number | null; onChange: (v: number | null) => void;
  options: { id: number; code: string; name: string }[]; allLabel: string;
}) {
  return (
    <div className="flex min-w-[150px] flex-1 flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value ? String(value) : "all"} onValueChange={(v) => onChange(v === "all" ? null : Number(v))}>
        <SelectTrigger id={id} aria-label={label} data-testid={`filter-${id}`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{allLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o.id} value={String(o.id)}>
              {o.code === o.name ? o.code : `${o.code} · ${o.name}`}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function FilterBar({ show = ["line", "machine", "shift", "product"] }: { show?: FilterKey[] }) {
  const { t, lang } = useI18n();
  const { filters, setFilters, reset } = useFilters();
  const meta = useMeta();
  const m = meta.data;
  const machines = (m?.machines ?? []).filter((x) => !filters.lineId || x.line_id === filters.lineId);
  const range = m?.data_range;
  const invalid = filters.startDate && filters.endDate && filters.endDate < filters.startDate;

  return (
    <div className="mb-5 rounded-lg border bg-white p-3 shadow-sm" role="search" aria-label="Filters">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="f-start">{t("common.from")}</Label>
          <Input id="f-start" type="date" className="w-[150px]" value={filters.startDate}
            min={range?.start ?? undefined} max={filters.endDate || undefined}
            onChange={(e) => setFilters({ startDate: e.target.value })} data-testid="filter-start" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="f-end">{t("common.to")}</Label>
          <Input id="f-end" type="date" className="w-[150px]" value={filters.endDate}
            min={filters.startDate || undefined}
            onChange={(e) => setFilters({ endDate: e.target.value })} data-testid="filter-end" />
        </div>
        {show.includes("line") && (
          <EntitySelect id="line" label={t("common.line")} value={filters.lineId} allLabel={t("common.all")}
            options={m?.lines ?? []} onChange={(v) => setFilters({ lineId: v })} />
        )}
        {show.includes("machine") && (
          <EntitySelect id="machine" label={t("common.machine")} value={filters.machineId} allLabel={t("common.all")}
            options={machines} onChange={(v) => setFilters({ machineId: v })} />
        )}
        {show.includes("shift") && (
          <EntitySelect id="shift" label={t("common.shift")} value={filters.shiftId} allLabel={t("common.all")}
            options={m?.shifts ?? []} onChange={(v) => setFilters({ shiftId: v })} />
        )}
        {show.includes("product") && (
          <EntitySelect id="product" label={t("common.product")} value={filters.productId} allLabel={t("common.all")}
            options={m?.products ?? []} onChange={(v) => setFilters({ productId: v })} />
        )}
        <div className="flex gap-2">
          <Button variant="outline" size="default" onClick={() => setFilters({ startDate: range?.start ?? "", endDate: range?.end ?? "" })} title={t("common.fullRange")}>
            <CalendarRange /> <span className="hidden xl:inline">{t("common.fullRange")}</span>
          </Button>
          <Button variant="ghost" onClick={reset} title={t("common.reset")} data-testid="filter-reset">
            <RotateCcw /> <span className="hidden xl:inline">{t("common.reset")}</span>
          </Button>
        </div>
      </div>
      {range?.start && (
        <div className="mt-2 text-[11px] text-muted-foreground">
          {t("common.dataUntil", { start: fmtDate(range.start, lang), end: fmtDate(range.end, lang) })}
          {invalid && <span className="ml-3 font-medium text-red-600">⚠ {t("common.to")} &lt; {t("common.from")}</span>}
        </div>
      )}
    </div>
  );
}

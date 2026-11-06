import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useI18n, type TKey } from "@/lib/i18n";
import type { AnomalyConfig, AnomalyMetric } from "@/lib/types";

const METRICS: AnomalyMetric[] = ["defect_rate", "downtime_ratio", "target_achievement", "performance"];

function NumberField({ id, label, value, onChange, step = 0.5, min, max }: {
  id: string; label: string; value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" step={step} min={min} max={max} value={Number.isFinite(value) ? value : ""}
        onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))} />
    </div>
  );
}

export function validateConfig(c: AnomalyConfig): string | null {
  const nums = [c.z_threshold, c.high_z, c.critical_z, c.window, c.min_history, c.isolation_forest_contamination];
  if (nums.some((n) => !Number.isFinite(n))) return "All numeric settings are required.";
  if (!(c.z_threshold <= c.high_z && c.high_z <= c.critical_z)) return "z threshold ≤ high ≤ critical required.";
  if (c.min_history > c.window) return "Minimum history must not exceed the baseline window.";
  if (!c.metrics.length) return "Select at least one metric.";
  if (!c.levels.length) return "Select at least one level.";
  return null;
}

export function AnomalyConfigForm({ value, onChange, idPrefix = "ac" }: { value: AnomalyConfig; onChange: (c: AnomalyConfig) => void; idPrefix?: string }) {
  const { t } = useI18n();
  const set = (patch: Partial<AnomalyConfig>) => onChange({ ...value, ...patch });
  const toggle = <T,>(arr: T[], x: T) => (arr.includes(x) ? arr.filter((y) => y !== x) : [...arr, x]);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <NumberField id={`${idPrefix}-z`} label={t("insights.zThreshold")} value={value.z_threshold} onChange={(v) => set({ z_threshold: v })} min={1.5} />
        <NumberField id={`${idPrefix}-high`} label={t("insights.highZ")} value={value.high_z} onChange={(v) => set({ high_z: v })} min={1.5} />
        <NumberField id={`${idPrefix}-crit`} label={t("insights.criticalZ")} value={value.critical_z} onChange={(v) => set({ critical_z: v })} min={1.5} />
        <NumberField id={`${idPrefix}-win`} label={t("insights.window")} value={value.window} onChange={(v) => set({ window: v })} step={1} min={5} max={120} />
        <NumberField id={`${idPrefix}-hist`} label={t("insights.minHistory")} value={value.min_history} onChange={(v) => set({ min_history: v })} step={1} min={5} max={120} />
      </div>
      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-slate-600">{t("insights.metrics")}</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {METRICS.map((m) => (
            <label key={m} className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 accent-blue-600" checked={value.metrics.includes(m)} onChange={() => set({ metrics: toggle(value.metrics, m) })} />
              {t(`metric.${m}` as TKey)}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-slate-600">{t("insights.levels")}</legend>
        <div className="flex flex-wrap gap-4">
          {(["machine", "line"] as const).map((l) => (
            <label key={l} className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 accent-blue-600" checked={value.levels.includes(l)} onChange={() => set({ levels: toggle(value.levels, l) })} />
              {t(`insights.level.${l}` as TKey)}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex items-center gap-2">
          <Switch id={`${idPrefix}-if`} checked={value.use_isolation_forest} onCheckedChange={(v) => set({ use_isolation_forest: v })} />
          <Label htmlFor={`${idPrefix}-if`}>{t("insights.isolationForest")}</Label>
        </div>
        {value.use_isolation_forest && (
          <div className="w-40">
            <NumberField id={`${idPrefix}-cont`} label={t("insights.contamination")} value={value.isolation_forest_contamination}
              onChange={(v) => set({ isolation_forest_contamination: v })} step={0.005} min={0.001} max={0.2} />
          </div>
        )}
      </div>
    </div>
  );
}

/** Shared building blocks for the Action Center pages. */
import { RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  CATEGORIES, EMPTY_AC_FILTERS, SEVERITIES, STATUSES, useAssignees,
  type AcFilters, type ActionStatus, type Category, type InvStatus, type Severity, type UserRef,
} from "@/lib/actionCenter";
import { useI18n, type TKey } from "@/lib/i18n";
import { useMeta } from "@/lib/queries";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<InvStatus, string> = {
  open: "bg-blue-50 text-blue-700",
  in_progress: "bg-violet-50 text-violet-700",
  pending_verification: "bg-amber-50 text-amber-800",
  resolved: "bg-emerald-50 text-emerald-700",
  closed: "bg-slate-100 text-slate-600",
  reopened: "bg-rose-50 text-rose-700",
};
const ACTION_STYLE: Record<ActionStatus, string> = {
  open: "bg-blue-50 text-blue-700",
  in_progress: "bg-violet-50 text-violet-700",
  done: "bg-amber-50 text-amber-800",
  verified: "bg-emerald-50 text-emerald-700",
  cancelled: "bg-slate-100 text-slate-500 line-through",
};

export function StatusBadge({ s }: { s: InvStatus }) {
  const { t } = useI18n();
  return <span className={cn("inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold", STATUS_STYLE[s])} data-status={s}>{t(`ac.status.${s}` as TKey)}</span>;
}
export function ActionStatusBadge({ s }: { s: ActionStatus }) {
  const { t } = useI18n();
  return <span className={cn("inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold", ACTION_STYLE[s])} data-status={s}>{t(`ac.actionStatus.${s}` as TKey)}</span>;
}
export function SevBadge({ s }: { s: Severity }) {
  const { t } = useI18n();
  return <Badge variant={s}>{t(`severity.${s}` as TKey)}</Badge>;
}
export function OwnerLabel({ u }: { u: UserRef | null }) {
  const { t } = useI18n();
  if (!u) return <span className="text-amber-700">{t("ac.unassigned")}</span>;
  return <span className={cn(!u.is_active && "text-slate-400 line-through")}>{u.display_name}</span>;
}
export function OverdueBadge({ days }: { days: number | null }) {
  const { t } = useI18n();
  if (days == null) return null;
  return <Badge variant="danger" data-testid="overdue-badge">{t("ac.overdueDays", { n: days })}</Badge>;
}

/** Generic labelled select for enumerations/entities; "" / null represent "all". */
export function FieldSelect({ id, label, value, onChange, options, allLabel, className, disabled }: {
  id: string; label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[]; allLabel?: string; className?: string; disabled?: boolean;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1 sm:min-w-[140px]", className)}>
      <Label htmlFor={id}>{label}</Label>
      <Select value={value || "__all"} onValueChange={(v) => { if (v === "") return; /* Radix may emit "" when options change */ const nv = v === "__all" ? "" : v; if (nv !== value) onChange(nv); }} disabled={disabled}>
        <SelectTrigger id={id} aria-label={label} data-testid={id}><SelectValue /></SelectTrigger>
        <SelectContent>
          {allLabel && <SelectItem value="__all">{allLabel}</SelectItem>}
          {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}

export function AcFilterBar({ value, onChange, hideStatus }: { value: AcFilters; onChange: (f: AcFilters) => void; hideStatus?: boolean }) {
  const { t } = useI18n();
  const meta = useMeta();
  const users = useAssignees();
  const machines = (meta.data?.machines ?? []).filter((m) => !value.lineId || m.line_id === value.lineId);
  const set = (p: Partial<AcFilters>) => onChange({ ...value, ...p });
  const invalid = value.startDate && value.endDate && value.endDate < value.startDate;
  return (
    <div className="mb-5 rounded-lg border bg-white p-3 shadow-sm" role="search" aria-label="Filters">
      <div className="grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap">
        <div className="flex flex-col gap-1">
          <Label htmlFor="acf-start">{t("ac.filter.createdFrom")}</Label>
          <Input id="acf-start" type="date" className="sm:w-[150px]" value={value.startDate} onChange={(e) => set({ startDate: e.target.value })} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="acf-end">{t("ac.filter.createdTo")}</Label>
          <Input id="acf-end" type="date" className="sm:w-[150px]" value={value.endDate} onChange={(e) => set({ endDate: e.target.value })} />
        </div>
        <FieldSelect id="acf-line" label={t("common.line")} allLabel={t("ac.filter.allLines")} value={value.lineId ? String(value.lineId) : ""}
          onChange={(v) => set({ lineId: v ? Number(v) : null, machineId: null })}
          options={(meta.data?.lines ?? []).map((l) => ({ value: String(l.id), label: l.code === l.name ? l.code : `${l.code} · ${l.name}` }))} />
        <FieldSelect id="acf-machine" label={t("common.machine")} allLabel={t("ac.filter.allMachines")} value={value.machineId ? String(value.machineId) : ""}
          onChange={(v) => set({ machineId: v ? Number(v) : null })}
          options={machines.map((m) => ({ value: String(m.id), label: `${m.code} · ${m.name}` }))} />
        <FieldSelect id="acf-severity" label={t("ac.field.severity")} allLabel={t("ac.filter.allSeverities")} value={value.severity}
          onChange={(v) => set({ severity: v as Severity | "" })}
          options={SEVERITIES.map((s) => ({ value: s, label: t(`severity.${s}` as TKey) }))} />
        {!hideStatus && (
          <FieldSelect id="acf-status" label={t("ac.field.status")} allLabel={t("ac.filter.allStatuses")} value={value.status}
            onChange={(v) => set({ status: v as InvStatus | "" })}
            options={STATUSES.map((s) => ({ value: s, label: t(`ac.status.${s}` as TKey) }))} />
        )}
        <FieldSelect id="acf-owner" label={t("ac.field.owner")} allLabel={t("ac.filter.allOwners")} value={value.ownerId ? String(value.ownerId) : ""}
          onChange={(v) => set({ ownerId: v ? Number(v) : null })}
          options={(users.data ?? []).map((u) => ({ value: String(u.id), label: u.display_name }))} />
        <FieldSelect id="acf-category" label={t("ac.field.category")} allLabel={t("ac.filter.allCategories")} value={value.category}
          onChange={(v) => set({ category: v as Category | "" })}
          options={CATEGORIES.map((c) => ({ value: c, label: t(`ac.category.${c}` as TKey) }))} />
        <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_AC_FILTERS)}><RotateCcw /> {t("common.reset")}</Button>
      </div>
      {invalid && <p className="mt-2 text-xs font-medium text-red-600" role="alert">⚠ {t("ac.filter.createdTo")} &lt; {t("ac.filter.createdFrom")}</p>}
    </div>
  );
}

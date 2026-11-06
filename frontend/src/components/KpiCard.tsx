import { Info } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useI18n } from "@/lib/i18n";
import { isNum } from "@/lib/format";
import type { Metric } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  value: string;
  metric?: Metric;
  formula?: string;
  sub?: string;
  icon?: LucideIcon;
  tone?: "blue" | "teal" | "amber" | "red" | "slate" | "violet";
  emphasis?: boolean;
  testId?: string;
}

const TONES = {
  blue: "bg-blue-50 text-blue-600",
  teal: "bg-teal-50 text-teal-600",
  amber: "bg-amber-50 text-amber-600",
  red: "bg-red-50 text-red-600",
  slate: "bg-slate-100 text-slate-600",
  violet: "bg-violet-50 text-violet-600",
};

export function KpiCard({ label, value, metric, formula, sub, icon: Icon, tone = "blue", emphasis, testId }: Props) {
  const { t } = useI18n();
  const na = metric ? !isNum(metric.value) : value === "N/A";
  const partial = metric && metric.coverage !== null && metric.coverage < 0.999 && metric.records_used > 0;
  return (
    <Card className={cn("relative overflow-hidden p-4", emphasis && "border-teal-200 bg-gradient-to-br from-white to-teal-50/60")} data-testid={testId}>
      {emphasis && <div className="absolute inset-y-0 left-0 w-1 bg-teal-500" aria-hidden />}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
          {label}
          {(formula || na) && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button type="button" className="text-slate-400 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`${label}: ${t("common.formula")}`}>
                  <Info className="h-3.5 w-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {formula && <div><span className="font-semibold">{t("common.formula")}:</span> {formula}</div>}
                {na && <div className="mt-1 text-amber-200">{t("common.naHint")}</div>}
              </TooltipContent>
            </Tooltip>
          )}
        </div>
        {Icon && (
          <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-md", TONES[tone])}>
            <Icon className="h-4 w-4" aria-hidden />
          </div>
        )}
      </div>
      <div className={cn("tabular mt-1 text-2xl font-semibold tracking-tight", na ? "text-slate-400" : "text-slate-900")} data-testid={testId ? `${testId}-value` : undefined}>
        {value}
      </div>
      <div className="mt-0.5 min-h-[16px] text-[11px] text-muted-foreground">
        {sub}
        {partial && (
          <span className="block text-amber-700">{t("common.coverage", { used: metric!.records_used, total: metric!.records_total })}</span>
        )}
      </div>
    </Card>
  );
}

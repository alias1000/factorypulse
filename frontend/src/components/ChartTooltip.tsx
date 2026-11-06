import type { TooltipProps } from "recharts";

type Formatter = (v: number | null | undefined, name: string) => string;

/** Compact, consistent tooltip for Recharts. */
export function ChartTooltip({ active, payload, label, valueFormatter, labelFormatter }: TooltipProps<number, string> & {
  valueFormatter: Formatter; labelFormatter?: (l: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-semibold text-slate-800">{labelFormatter ? labelFormatter(String(label)) : label}</div>
      {payload.map((p) => (
        <div key={String(p.dataKey)} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 text-slate-600">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />
            {p.name}
          </span>
          <span className="tabular font-medium text-slate-900">{valueFormatter(p.value as number, String(p.dataKey))}</span>
        </div>
      ))}
    </div>
  );
}

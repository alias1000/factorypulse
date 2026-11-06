import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface Column<T> {
  key: string;
  header: string;
  value: (row: T) => string | number | null | undefined;
  render?: (row: T) => ReactNode;
  numeric?: boolean;
  className?: string;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  initialSort?: { key: string; dir: "asc" | "desc" };
  searchable?: boolean;
  toolbar?: ReactNode;
  rowClassName?: (row: T) => string | undefined;
  maxHeight?: number;
  testId?: string;
  rowKey: (row: T, i: number) => string | number;
}

/** Client-side sortable + searchable table (used for aggregated tables of moderate size). */
export function DataTable<T>({ rows, columns, initialSort, searchable = true, toolbar, rowClassName, maxHeight = 520, testId, rowKey }: Props<T>) {
  const { t } = useI18n();
  const [sort, setSort] = useState(initialSort ?? { key: columns[0].key, dir: "asc" as "asc" | "desc" });
  const [q, setQ] = useState("");

  const view = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key) ?? columns[0];
    const needle = q.trim().toLowerCase();
    const filtered = needle
      ? rows.filter((r) => columns.some((c) => String(c.value(r) ?? "").toLowerCase().includes(needle)))
      : rows;
    return [...filtered].sort((a, b) => {
      const va = col.value(a);
      const vb = col.value(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1; // N/A always last
      if (vb == null) return -1;
      const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), undefined, { numeric: true });
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [rows, columns, sort, q]);

  const toggle = (key: string) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  return (
    <div data-testid={testId}>
      {(searchable || toolbar) && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          {searchable && (
            <div className="relative w-full max-w-xs">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("common.search")} className="pl-8" aria-label={t("common.search")} />
            </div>
          )}
          {toolbar}
          <span className="ml-auto text-xs text-muted-foreground">{t("common.rows", { n: view.length })}</span>
        </div>
      )}
      <div className="overflow-auto rounded-md border" style={{ maxHeight }}>
        <Table>
          <TableHeader className="sticky top-0 z-10">
            <TableRow>
              {columns.map((c) => (
                <TableHead key={c.key} className={cn(c.numeric && "text-right", c.className)} aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" onClick={() => toggle(c.key)} className={cn("inline-flex items-center gap-1 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", c.numeric && "flex-row-reverse")}>
                    {c.header}
                    {sort.key === c.key ? (sort.dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />) : <ArrowUpDown className="h-3 w-3 opacity-30" />}
                  </button>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {view.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="py-8 text-center text-sm text-muted-foreground">{t("common.noData")}</TableCell>
              </TableRow>
            ) : (
              view.map((r, i) => (
                <TableRow key={rowKey(r, i)} className={rowClassName?.(r)}>
                  {columns.map((c) => (
                    <TableCell key={c.key} className={cn("tabular", c.numeric && "whitespace-nowrap text-right", c.className)}>
                      {c.render ? c.render(r) : (c.value(r) ?? "N/A")}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

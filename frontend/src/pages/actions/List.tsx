import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, BrainCircuit, ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { AcFilterBar, OverdueBadge, OwnerLabel, SevBadge, StatusBadge } from "@/components/ac";
import { ErrorState, LoadingState } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EMPTY_AC_FILTERS, useInvestigations, type AcFilters, type InvStatus, type Severity } from "@/lib/actionCenter";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { fmtDate } from "@/lib/format";

type SortKey = "number" | "created_at" | "due_date" | "severity" | "status" | "updated_at" | "title";

export default function InvestigationList() {
  const { t, lang } = useI18n();
  const { can } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [filters, setFilters] = useState<AcFilters>({
    ...EMPTY_AC_FILTERS,
    status: (params.get("status") as InvStatus) || "",
    severity: (params.get("severity") as Severity) || "",
  });
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "created_at", dir: "desc" });
  const [page, setPage] = useState(1);
  const [overdue, setOverdue] = useState(params.get("overdue") === "1");
  const [unassigned, setUnassigned] = useState(false);
  const pageSize = 25;

  useEffect(() => { const h = setTimeout(() => setDebounced(search), 300); return () => clearTimeout(h); }, [search]);
  useEffect(() => setPage(1), [filters, debounced, sort, overdue, unassigned]);

  const q = useInvestigations(filters, { search: debounced, sort: sort.key, order: sort.dir, page, pageSize, overdue, unassigned });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / pageSize)) : 1;
  const toggle = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "number" || key === "title" ? "asc" : "desc" }));
  const H = ({ k, label }: { k: SortKey; label: string }) => (
    <TableHead aria-sort={sort.key === k ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={() => toggle(k)} className="inline-flex items-center gap-1 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {label}{sort.key === k ? (sort.dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />) : <ArrowUpDown className="h-3 w-3 opacity-30" />}
      </button>
    </TableHead>
  );

  return (
    <div>
      <PageHeader title={t("ac.tab.list")} subtitle={t("ac.subtitle")} actions={
        can("action.write") && <Button asChild><Link to="/actions/investigations/new" data-testid="ac-new"><Plus /> {t("ac.new")}</Link></Button>
      } />
      <AcFilterBar value={filters} onChange={setFilters} />
      <Card>
        <CardContent className="pt-5">
          <div className="mb-3 flex flex-wrap items-center gap-4">
            <div className="relative w-full max-w-sm">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("ac.list.search")} className="pl-8" aria-label={t("ac.list.search")} data-testid="inv-search" />
            </div>
            <div className="flex items-center gap-2">
              <Switch id="only-overdue" checked={overdue} onCheckedChange={setOverdue} />
              <Label htmlFor="only-overdue">{t("ac.list.onlyOverdue")}</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch id="only-unassigned" checked={unassigned} onCheckedChange={setUnassigned} />
              <Label htmlFor="only-unassigned">{t("ac.list.onlyUnassigned")}</Label>
            </div>
            {q.data && <span className="ml-auto text-xs text-muted-foreground" data-testid="inv-total">{t("ac.list.total", { n: q.data.total })}</span>}
          </div>
          {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <LoadingState /> : (
            <>
              <div className="overflow-auto rounded-md border">
                <Table data-testid="inv-table">
                  <TableHeader>
                    <TableRow>
                      <H k="number" label={t("ac.field.number")} />
                      <H k="title" label={t("ac.field.title")} />
                      <TableHead>{t("ac.field.line")}</TableHead>
                      <H k="severity" label={t("ac.field.severity")} />
                      <H k="status" label={t("ac.field.status")} />
                      <TableHead>{t("ac.field.owner")}</TableHead>
                      <H k="due_date" label={t("ac.field.due")} />
                      <TableHead>{t("ac.field.actions")}</TableHead>
                      <H k="created_at" label={t("ac.field.created")} />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {q.data.rows.length === 0 ? (
                      <TableRow><TableCell colSpan={9} className="py-10 text-center text-sm text-muted-foreground">{t("ac.list.empty")}</TableCell></TableRow>
                    ) : q.data.rows.map((i) => (
                      <TableRow key={i.id} className="cursor-pointer" tabIndex={0} onClick={() => nav(`/actions/investigations/${i.id}`)}
                        onKeyDown={(e) => e.key === "Enter" && nav(`/actions/investigations/${i.id}`)} data-testid={`inv-row-${i.number}`}>
                        <TableCell className="whitespace-nowrap font-medium text-primary">{i.number}</TableCell>
                        <TableCell className="max-w-[320px]">
                          <div className="truncate font-medium text-slate-800" title={i.title}>{i.title}</div>
                          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                            {t(`ac.category.${i.category}` as TKey)}
                            {i.source === "anomaly" && <span className="inline-flex items-center gap-0.5 text-teal-700"><BrainCircuit className="h-3 w-3" />{t("ac.source.anomaly")}</span>}
                            {i.is_synthetic && <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{t("ac.synthetic")}</Badge>}
                          </div>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">{i.line.code}{i.machine ? ` · ${i.machine.code}` : ""}</TableCell>
                        <TableCell><SevBadge s={i.severity} /></TableCell>
                        <TableCell><StatusBadge s={i.status} /></TableCell>
                        <TableCell className="text-xs"><OwnerLabel u={i.owner} /></TableCell>
                        <TableCell className="whitespace-nowrap text-xs">{fmtDate(i.due_date, lang)} <OverdueBadge days={i.overdue ? i.days_overdue : null} /></TableCell>
                        <TableCell className="whitespace-nowrap text-xs tabular">
                          {i.actions_summary.total === 0 ? "–" : `${i.actions_summary.done}/${i.actions_summary.total}`}
                          {i.actions_summary.overdue > 0 && <span className="ml-1 font-semibold text-red-600">({i.actions_summary.overdue}!)</span>}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{fmtDate(i.created_at, lang)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="mt-3 flex items-center justify-end gap-2 text-xs">
                <span className="text-muted-foreground">{t("common.page", { page, pages })}</span>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label={t("common.previous")}><ChevronLeft /></Button>
                <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label={t("common.next")}><ChevronRight /></Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

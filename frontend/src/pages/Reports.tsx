import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Download, FileSpreadsheet, FileText, Loader2, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, downloadFile, postJson } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n, type Lang, type TKey } from "@/lib/i18n";
import { useMeta, useReports, useSettings } from "@/lib/queries";
import { fmtBytes, fmtDate, fmtDateTime } from "@/lib/format";
import type { ReportJob, ReportType } from "@/lib/types";
import { cn } from "@/lib/utils";

const TYPES: ReportType[] = ["daily", "weekly", "monthly", "quality", "downtime"];
const RANGE_TYPES: ReportType[] = ["quality", "downtime"];

function EntitySelect({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { id: number; code: string; name: string }[] }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger aria-label={label}><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("common.all")}</SelectItem>
          {options.map((o) => <SelectItem key={o.id} value={String(o.id)}>{o.code === o.name ? o.code : `${o.code} · ${o.name}`}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}

export default function ReportsPage() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const { can } = useAuth();
  const meta = useMeta();
  const settings = useSettings();
  const reports = useReports();
  const [type, setType] = useState<ReportType>("monthly");
  const [format, setFormat] = useState<"pdf" | "xlsx">("pdf");
  const [rLang, setRLang] = useState<Lang>(lang);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [line, setLine] = useState("all");
  const [machine, setMachine] = useState("all");
  const [shift, setShift] = useState("all");
  const [product, setProduct] = useState("all");

  const range = meta.data?.data_range;
  useEffect(() => {
    if (range?.end && !start) {
      setStart(range.end);
      setEnd(range.end);
    }
  }, [range, start]);
  useEffect(() => { if (settings.data) setRLang(settings.data.default_language); }, [settings.data]);
  useEffect(() => {
    if (RANGE_TYPES.includes(type) && range?.start && start === end) setStart(range.start);
  }, [type]); // eslint-disable-line react-hooks/exhaustive-deps

  const gen = useMutation({
    mutationFn: async () => {
      const ids = (v: string) => (v === "all" ? [] : [Number(v)]);
      const job = await postJson<ReportJob>("/api/reports", {
        report_type: type, format, start_date: start, end_date: RANGE_TYPES.includes(type) ? end : null, language: rLang,
        filters: { line_ids: ids(line), machine_ids: ids(machine), shift_ids: ids(shift), product_ids: ids(product) },
      });
      await downloadFile(`/api/reports/${job.id}/download`, job.download_name ?? `report.${format}`);
      return job;
    },
    onSuccess: (job) => { toast.success(t("reports.success", { name: job.download_name ?? "" })); qc.invalidateQueries({ queryKey: ["reports"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  const del = useMutation({
    mutationFn: (id: number) => api(`/api/reports/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reports"] }),
    onError: (e) => toast.error((e as Error).message),
  });

  const machines = (meta.data?.machines ?? []).filter((m) => line === "all" || m.line_id === Number(line));
  const invalid = !start || (RANGE_TYPES.includes(type) && (!end || end < start));

  return (
    <div>
      <PageHeader title={t("reports.title")} subtitle={t("reports.subtitle")} />
      <div className="grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-2">
          <CardHeader><CardTitle>{t("reports.new")}</CardTitle><CardDescription>{t("reports.contents")}</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div role="radiogroup" aria-label={t("reports.type")} className="grid gap-2">
              {TYPES.map((rt) => (
                <button key={rt} type="button" role="radio" aria-checked={type === rt} onClick={() => setType(rt)} data-testid={`report-type-${rt}`}
                  className={cn("rounded-md border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    type === rt ? "border-blue-500 bg-blue-50/60 ring-1 ring-blue-500" : "hover:bg-slate-50")}>
                  <div className="text-sm font-semibold text-slate-800">{t(`reports.type.${rt}` as TKey)}</div>
                  <div className="text-xs text-muted-foreground">{t(`reports.desc.${rt}` as TKey)}</div>
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="r-start">{RANGE_TYPES.includes(type) ? t("reports.startDate") : t("reports.referenceDate")}</Label>
                <Input id="r-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              </div>
              {RANGE_TYPES.includes(type) && (
                <div className="flex flex-col gap-1">
                  <Label htmlFor="r-end">{t("reports.endDate")}</Label>
                  <Input id="r-end" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <EntitySelect label={t("common.line")} value={line} onChange={(v) => { setLine(v); setMachine("all"); }} options={meta.data?.lines ?? []} />
              <EntitySelect label={t("common.machine")} value={machine} onChange={setMachine} options={machines} />
              <EntitySelect label={t("common.shift")} value={shift} onChange={setShift} options={meta.data?.shifts ?? []} />
              <EntitySelect label={t("common.product")} value={product} onChange={setProduct} options={meta.data?.products ?? []} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <Label>{t("reports.format")}</Label>
                <div className="flex gap-2" role="radiogroup" aria-label={t("reports.format")}>
                  {(["pdf", "xlsx"] as const).map((f) => (
                    <Button key={f} type="button" role="radio" aria-checked={format === f} variant={format === f ? "default" : "outline"} className="flex-1" onClick={() => setFormat(f)} data-testid={`format-${f}`}>
                      {f === "pdf" ? <FileText /> : <FileSpreadsheet />} {f === "pdf" ? "PDF" : "Excel"}
                    </Button>
                  ))}
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <Label>{t("reports.language")}</Label>
                <Select value={rLang} onValueChange={(v) => setRLang(v as Lang)}>
                  <SelectTrigger aria-label={t("reports.language")}><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="de">Deutsch</SelectItem><SelectItem value="en">English</SelectItem></SelectContent>
                </Select>
              </div>
            </div>
            <Button className="w-full" size="lg" onClick={() => gen.mutate()} disabled={!can("report.generate") || invalid || gen.isPending} data-testid="generate-report">
              {gen.isPending ? <><Loader2 className="animate-spin" /> {t("reports.generating")}</> : <><Download /> {t("reports.generate")}</>}
            </Button>
            {!can("report.generate") && <p className="text-xs font-medium text-amber-700">{t("auth.readOnlyHint")}</p>}
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground"><CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" />{t("reports.scheduledNote")}</p>
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader><CardTitle>{t("reports.history")}</CardTitle><CardDescription>{t("reports.historyDesc")}</CardDescription></CardHeader>
          <CardContent>
            {reports.isLoading ? <LoadingState /> : reports.isError ? <ErrorState error={reports.error} onRetry={() => reports.refetch()} /> :
             !reports.data?.length ? <EmptyState title={t("reports.empty")} hint="" icon={FileText} /> : (
              <div className="overflow-auto rounded-md border">
                <Table data-testid="report-history">
                  <TableHeader><TableRow>
                    <TableHead>{t("reports.type")}</TableHead><TableHead>{t("reports.period")}</TableHead>
                    <TableHead>{t("reports.format")}</TableHead><TableHead>{t("reports.created")}</TableHead>
                    <TableHead className="text-right">{t("reports.size")}</TableHead><TableHead className="text-right">{t("common.actions")}</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {reports.data.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell className="font-medium">{t(`reports.type.${r.report_type}` as TKey)}</TableCell>
                        <TableCell className="tabular text-xs">{r.period_start === r.period_end ? fmtDate(r.period_start, lang) : `${fmtDate(r.period_start, lang)} – ${fmtDate(r.period_end, lang)}`}</TableCell>
                        <TableCell className="whitespace-nowrap"><Badge variant={r.format === "pdf" ? "danger" : "success"}>{r.format.toUpperCase()}</Badge> <span className="text-xs uppercase text-muted-foreground">{r.language}</span></TableCell>
                        <TableCell className="tabular text-xs">{fmtDateTime(r.created_at, lang)}</TableCell>
                        <TableCell className="tabular whitespace-nowrap text-right text-xs">{fmtBytes(r.file_size)}</TableCell>
                        <TableCell className="text-right">
                          {r.status === "completed" ? (
                            <div className="flex justify-end gap-1">
                              <Button size="icon" variant="ghost" aria-label={t("common.download")} title={t("common.download")}
                                onClick={() => downloadFile(`/api/reports/${r.id}/download`, r.download_name ?? "report").catch((e) => toast.error(e.message))}>
                                <Download />
                              </Button>
                              {can("report.delete") && (
                                <Button size="icon" variant="ghost" aria-label={t("common.delete")} title={t("common.delete")} onClick={() => del.mutate(r.id)}>
                                  <Trash2 className="text-red-600" />
                                </Button>
                              )}
                            </div>
                          ) : <Badge variant="danger">{r.status}</Badge>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

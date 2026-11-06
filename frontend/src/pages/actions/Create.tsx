import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, ChevronLeft, Info, Loader2, ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { FieldSelect, SevBadge } from "@/components/ac";
import { textareaCls } from "@/components/acActions";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, postJson } from "@/lib/api";
import { CATEGORIES, SEVERITIES, useAssignees, useRules, type Category, type InvestigationDetail, type Severity } from "@/lib/actionCenter";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { useMeta } from "@/lib/queries";
import { fmtDate, fmtNumber, fmtPct } from "@/lib/format";
import type { Anomaly, AnomalyConfig } from "@/lib/types";

interface Proposal { title: string; description: string; category: Category; severity: Severity; line_id: number; machine_id: number | null; due_date: string }
interface Prefill { duplicate: { id: number; number: string; title: string; status: string } | null; anomaly?: Anomaly; proposal?: Proposal }

interface Form { title: string; description: string; category: Category | ""; severity: Severity | ""; lineId: string; machineId: string; ownerId: string; dueDate: string }
const EMPTY: Form = { title: "", description: "", category: "", severity: "", lineId: "", machineId: "", ownerId: "", dueDate: "" };


export default function CreateInvestigation() {
  const { t, lang } = useI18n();
  const { me } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const location = useLocation();
  const anomalyId = params.get("anomaly");
  const config = (location.state as { config?: AnomalyConfig } | null)?.config;
  const meta = useMeta();
  const users = useAssignees();
  const rules = useRules();
  const isAdmin = me?.user.role === "admin";
  const [form, setForm] = useState<Form>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [dup, setDup] = useState<{ id: number; number: string } | null>(null);
  const set = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));

  const prefill = useQuery({
    queryKey: ["anomaly-prefill", anomalyId, lang],
    queryFn: () => postJson<Prefill>("/api/investigations/anomaly-prefill", { anomaly_id: anomalyId, config, lang }),
    enabled: !!anomalyId, retry: false, staleTime: Infinity,
  });
  useEffect(() => {
    const p = prefill.data?.proposal;
    if (p) setForm({ title: p.title, description: p.description, category: p.category, severity: p.severity, lineId: String(p.line_id),
      machineId: p.machine_id ? String(p.machine_id) : "", ownerId: "", dueDate: p.due_date });
  }, [prefill.data]);

  const create = useMutation({
    mutationFn: () => postJson<InvestigationDetail>("/api/investigations", {
      title: form.title.trim(), description: form.description.trim(), category: form.category, severity: form.severity,
      line_id: Number(form.lineId), machine_id: form.machineId ? Number(form.machineId) : null,
      owner_id: form.ownerId ? Number(form.ownerId) : null, due_date: form.dueDate || null,
      anomaly_id: anomalyId || null, anomaly_config: anomalyId ? config ?? null : null,
    }),
    onSuccess: (inv) => {
      toast.success(t("ac.create.created", { number: inv.number }));
      qc.invalidateQueries({ queryKey: ["investigations"] });
      qc.invalidateQueries({ queryKey: ["ac-summary"] });
      nav(`/actions/investigations/${inv.id}`);
    },
    onError: (e) => {
      const err = e as ApiError;
      const d = err.details as { investigation_id?: number; number?: string } | undefined;
      if (err.code === "duplicate_anomaly" && d?.investigation_id) setDup({ id: d.investigation_id, number: d.number ?? `#${d.investigation_id}` });
      setError(err.message);
    },
  });

  const machines = (meta.data?.machines ?? []).filter((m) => String(m.line_id) === form.lineId);
  const ownerOptions = (users.data ?? []).filter((u) => isAdmin || u.id === me?.user.id);
  const valid = form.title.trim().length >= 3 && form.category && form.severity && form.lineId;
  const dueDays = form.severity && rules.data ? rules.data.default_due_days[form.severity] : null;
  const a = prefill.data?.anomaly;
  const fromAnomaly = !!anomalyId;

  const body = () => {
    if (fromAnomaly && prefill.isLoading) return <LoadingState label={t("ac.create.anomalyLoading")} />;
    if (fromAnomaly && prefill.isError) return <ErrorState error={prefill.error} />;
    const duplicate = prefill.data?.duplicate ?? (dup ? { ...dup } : null);
    if (fromAnomaly && duplicate) {
      return (
        <div className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-4" data-testid="duplicate-anomaly">
          <Info className="h-5 w-5 shrink-0 text-amber-700" />
          <div>
            <p className="text-sm font-semibold text-amber-900">{t("ac.create.duplicate", { number: duplicate.number })}</p>
            <Button className="mt-2" size="sm" asChild><Link to={`/actions/investigations/${duplicate.id}`}>{t("ac.create.openExisting", { number: duplicate.number })}</Link></Button>
          </div>
        </div>
      );
    }
    return (
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setError(null); create.mutate(); }} data-testid="create-form">
        {fromAnomaly && <p className="rounded-md bg-blue-50 p-2.5 text-sm text-blue-900">{t("ac.create.review")}</p>}
        <div className="flex flex-col gap-1">
          <Label htmlFor="inv-title">{t("ac.field.title")} *</Label>
          <Input id="inv-title" value={form.title} maxLength={200} onChange={(e) => set({ title: e.target.value })} required />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="inv-desc">{t("ac.field.description")}</Label>
          <textarea id="inv-desc" className={textareaCls} rows={6} maxLength={10000} value={form.description} onChange={(e) => set({ description: e.target.value })} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <FieldSelect id="inv-category" label={`${t("ac.field.category")} *`} value={form.category} onChange={(v) => set({ category: v as Category })}
            options={CATEGORIES.map((c) => ({ value: c, label: t(`ac.category.${c}` as TKey) }))} allLabel="—" />
          <FieldSelect id="inv-severity" label={`${t("ac.field.severity")} *`} value={form.severity} onChange={(v) => set({ severity: v as Severity })}
            options={SEVERITIES.map((s) => ({ value: s, label: t(`severity.${s}` as TKey) }))} allLabel="—" />
          <FieldSelect id="inv-line" label={`${t("ac.field.line")} *`} value={form.lineId} onChange={(v) => set(v === form.lineId ? {} : { lineId: v, machineId: "" })} disabled={fromAnomaly}
            options={(meta.data?.lines ?? []).map((l) => ({ value: String(l.id), label: l.code === l.name ? l.code : `${l.code} · ${l.name}` }))} allLabel="—" />
          <FieldSelect id="inv-machine" label={t("ac.field.machine")} value={form.machineId} onChange={(v) => set({ machineId: v })} disabled={fromAnomaly || !form.lineId}
            options={machines.map((m) => ({ value: String(m.id), label: `${m.code} · ${m.name}` }))} allLabel={t("ac.noMachine")} />
          <div>
            <FieldSelect id="inv-owner" label={t("ac.field.owner")} value={form.ownerId} onChange={(v) => set({ ownerId: v })}
              options={ownerOptions.map((u) => ({ value: String(u.id), label: u.display_name }))} allLabel={t("ac.unassigned")} />
            {!isAdmin && <p className="mt-1 text-[11px] text-muted-foreground">{t("ac.create.ownerHintAnalyst")}</p>}
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="inv-due">{t("ac.field.due")}</Label>
            <Input id="inv-due" type="date" value={form.dueDate} onChange={(e) => set({ dueDate: e.target.value })} />
            {dueDays != null && !form.dueDate && <p className="text-[11px] text-muted-foreground">{t("ac.create.dueHint", { days: dueDays })}</p>}
          </div>
        </div>
        {form.category === "safety_observation" && (
          <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-900"><ShieldAlert className="h-4 w-4 shrink-0" />{t("ac.detail.safetyNote")}</p>
        )}
        {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
        <div className="flex gap-2">
          <Button type="submit" disabled={!valid || create.isPending} data-testid="create-submit">
            {create.isPending && <Loader2 className="animate-spin" />} {t("ac.create.submit")}
          </Button>
          <Button type="button" variant="outline" onClick={() => nav(-1)}>{t("common.cancel")}</Button>
        </div>
      </form>
    );
  };

  return (
    <div>
      <Link to="/actions/investigations" className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"><ChevronLeft className="h-3.5 w-3.5" />{t("ac.detail.back")}</Link>
      <PageHeader title={t("ac.create.title")} subtitle={fromAnomaly ? t("ac.create.fromAnomaly") : t("ac.create.manual")} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2"><CardContent className="pt-5">{body()}</CardContent></Card>
        {fromAnomaly && a && (
          <Card data-testid="anomaly-evidence">
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><BrainCircuit className="h-4 w-4 text-teal-600" />{t("ac.detail.anomaly")}</CardTitle>
              <CardDescription>{t("ac.detail.anomalyNote")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center gap-2 font-medium">{t(`metric.${a.metric}` as TKey)} · {a.entity_code} <SevBadge s={a.severity} /></div>
              <dl className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded border bg-slate-50 p-2"><dt className="text-slate-500">{t("ac.detail.detectedOn")}</dt><dd className="font-semibold">{fmtDate(a.date, lang)}</dd></div>
                <div className="rounded border bg-slate-50 p-2"><dt className="text-slate-500">{t("ac.detail.zscore")}</dt><dd className="font-semibold tabular">{fmtNumber(a.z_score, lang, 1)}</dd></div>
                <div className="rounded border bg-slate-50 p-2"><dt className="text-slate-500">{t("ac.detail.observed")}</dt><dd className="font-semibold tabular">{fmtPct(a.observed, lang)}</dd></div>
                <div className="rounded border bg-slate-50 p-2"><dt className="text-slate-500">{t("ac.detail.baseline")}</dt><dd className="font-semibold tabular">{fmtPct(a.baseline, lang)}</dd></div>
              </dl>
              <p className="text-xs leading-relaxed text-slate-600">{lang === "de" && a.explanation_de ? a.explanation_de : a.explanation}</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

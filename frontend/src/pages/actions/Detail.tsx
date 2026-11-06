import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, ChevronLeft, Loader2, Lock, MessageSquare, Pencil, ShieldAlert, UserCheck } from "lucide-react";
import { FieldSelect, OverdueBadge, OwnerLabel, SevBadge, StatusBadge } from "@/components/ac";
import { textareaCls, useAcInvalidate } from "@/components/acActions";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, postJson } from "@/lib/api";
import { CATEGORIES, SEVERITIES, useAssignees, useInvestigation, type Category, type InvestigationDetail, type InvStatus, type Severity } from "@/lib/actionCenter";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { useMeta } from "@/lib/queries";
import { fmtDate, fmtDateTime, fmtNumber, fmtPct } from "@/lib/format";
import { ActionsTab, EvidenceTab, KpiTab, TimelineTab } from "./detailTabs";

/* ---------------------------------------------------------------- workflow */

function TransitionDialog({ inv, to, onClose }: { inv: InvestigationDetail; to: InvStatus | null; onClose: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const invalidate = useAcInvalidate();
  const [note, setNote] = useState("");
  const [resolution, setResolution] = useState(inv.resolution_summary ?? "");
  const [error, setError] = useState<string | null>(null);
  const needsResolution = to === "resolved" || to === "closed";
  const needsNote = to === "reopened";
  const m = useMutation({
    mutationFn: () => postJson(`/api/investigations/${inv.id}/transition`, {
      to_status: to, note: note.trim() || null, resolution_summary: needsResolution ? resolution.trim() : null,
    }),
    onSuccess: () => { toast.success(t("ac.transition.done", { status: t(`ac.status.${to}` as TKey) })); invalidate(inv.id); close(); },
    onError: (e) => setError((e as Error).message),
  });
  const close = () => { setNote(""); setError(null); onClose(); };
  const ok = (!needsResolution || resolution.trim().length >= 10) && (!needsNote || note.trim().length > 0);
  return (
    <Dialog open={!!to} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-w-lg">
        {to && (
          <>
            <DialogHeader>
              <DialogTitle>{t(`ac.to.${to}` as TKey)}</DialogTitle>
              <DialogDescription>{inv.number} · <StatusBadge s={inv.status} /> → <StatusBadge s={to} /></DialogDescription>
            </DialogHeader>
            {needsResolution && (
              <div className="flex flex-col gap-1">
                <Label htmlFor="tr-resolution">{t("ac.transition.resolution")}</Label>
                <textarea id="tr-resolution" className={textareaCls} rows={4} maxLength={10000} value={resolution} onChange={(e) => setResolution(e.target.value)} data-testid="transition-resolution" />
              </div>
            )}
            <div className="flex flex-col gap-1">
              <Label htmlFor="tr-note">{needsNote ? t("ac.transition.reason") : t("ac.transition.note")}</Label>
              <textarea id="tr-note" className={textareaCls} rows={2} maxLength={5000} value={note} onChange={(e) => setNote(e.target.value)} data-testid="transition-note" />
            </div>
            {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={close}>{t("common.cancel")}</Button>
              <Button onClick={() => m.mutate()} disabled={!ok || m.isPending} data-testid="transition-ok">
                {m.isPending && <Loader2 className="animate-spin" />} {t(`ac.to.${to}` as TKey)}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- edit */

function EditDialog({ inv, open, onOpenChange }: { inv: InvestigationDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const invalidate = useAcInvalidate();
  const meta = useMeta();
  const users = useAssignees();
  const init = () => ({
    title: inv.title, description: inv.description, category: inv.category, severity: inv.severity,
    machineId: inv.machine ? String(inv.machine.id) : "", ownerId: inv.owner ? String(inv.owner.id) : "", dueDate: inv.due_date ?? "",
  });
  const [v, setV] = useState(init);
  const [error, setError] = useState<string | null>(null);
  const set = (p: Partial<ReturnType<typeof init>>) => setV((x) => ({ ...x, ...p }));
  const anomalyLinked = inv.anomaly_links.length > 0;
  const m = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { title: v.title.trim(), description: v.description.trim(), category: v.category, severity: v.severity };
      if (v.dueDate && v.dueDate !== inv.due_date) body.due_date = v.dueDate;
      if (!anomalyLinked) { if (v.machineId) body.machine_id = Number(v.machineId); else if (inv.machine) body.clear_machine = true; }
      if (inv.permissions.can_assign_any) { if (v.ownerId) body.owner_id = Number(v.ownerId); else if (inv.owner) body.clear_owner = true; }
      return postJson(`/api/investigations/${inv.id}`, body, "PATCH");
    },
    onSuccess: () => { toast.success(t("ac.detail.saved")); invalidate(inv.id); onOpenChange(false); },
    onError: (e) => setError((e as Error).message),
  });
  const machines = (meta.data?.machines ?? []).filter((x) => x.line_id === inv.line.id);
  return (
    <Dialog open={open} onOpenChange={(o) => { if (o) { setV(init()); setError(null); } onOpenChange(o); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>{t("ac.detail.edit")} · {inv.number}</DialogTitle><DialogDescription>{inv.line.code}</DialogDescription></DialogHeader>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); setError(null); m.mutate(); }} data-testid="edit-form">
          <div className="flex flex-col gap-1">
            <Label htmlFor="ed-title">{t("ac.field.title")}</Label>
            <Input id="ed-title" value={v.title} maxLength={200} onChange={(e) => set({ title: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="ed-desc">{t("ac.field.description")}</Label>
            <textarea id="ed-desc" className={textareaCls} rows={5} maxLength={10000} value={v.description} onChange={(e) => set({ description: e.target.value })} />
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <FieldSelect id="ed-category" label={t("ac.field.category")} value={v.category} onChange={(x) => set({ category: x as Category })}
              options={CATEGORIES.map((c) => ({ value: c, label: t(`ac.category.${c}` as TKey) }))} />
            <FieldSelect id="ed-severity" label={t("ac.field.severity")} value={v.severity} onChange={(x) => set({ severity: x as Severity })}
              options={SEVERITIES.map((s) => ({ value: s, label: t(`severity.${s}` as TKey) }))} />
            <div className="flex flex-col gap-1">
              <Label htmlFor="ed-due">{t("ac.field.due")}</Label>
              <Input id="ed-due" type="date" value={v.dueDate} onChange={(e) => set({ dueDate: e.target.value })} />
            </div>
            <FieldSelect id="ed-machine" label={t("ac.field.machine")} value={v.machineId} onChange={(x) => set({ machineId: x })} disabled={anomalyLinked}
              allLabel={t("ac.noMachine")} options={machines.map((x) => ({ value: String(x.id), label: `${x.code} · ${x.name}` }))} />
            {inv.permissions.can_assign_any && (
              <FieldSelect id="ed-owner" label={t("ac.field.owner")} value={v.ownerId} onChange={(x) => set({ ownerId: x })} allLabel={t("ac.unassigned")}
                options={(users.data ?? []).map((u) => ({ value: String(u.id), label: u.display_name }))} />
            )}
          </div>
          {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={v.title.trim().length < 3 || m.isPending} data-testid="edit-save">{m.isPending && <Loader2 className="animate-spin" />} {t("common.save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- overview tab */

interface Comment { id: number; author: string; author_id: number | null; body: string; created_at: string }

function Comments({ inv }: { inv: InvestigationDetail }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["investigation", inv.id, "comments"], queryFn: () => api<Comment[]>(`/api/investigations/${inv.id}/comments`) });
  const [body, setBody] = useState("");
  const m = useMutation({
    mutationFn: () => postJson(`/api/investigations/${inv.id}/comments`, { body: body.trim() }),
    onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["investigation", inv.id] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><MessageSquare className="h-4 w-4 text-blue-600" />{t("ac.detail.comments")}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        {q.isLoading ? <LoadingState /> : (q.data ?? []).length === 0 ? <p className="text-sm text-muted-foreground">–</p> : (
          <ul className="space-y-2" data-testid="comments">
            {q.data!.map((c) => (
              <li key={c.id} className="rounded-md bg-slate-50 p-2.5 text-sm">
                <div className="mb-0.5 text-[11px] text-muted-foreground"><span className="font-semibold text-slate-700">{c.author}</span> · {fmtDateTime(c.created_at, lang)}</div>
                <p className="whitespace-pre-line text-slate-800">{c.body}</p>
              </li>
            ))}
          </ul>
        )}
        {inv.permissions.can_contribute && (
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
            <label htmlFor="comment-body" className="sr-only">{t("ac.detail.commentPlaceholder")}</label>
            <textarea id="comment-body" className={textareaCls} rows={3} maxLength={5000} placeholder={t("ac.detail.commentPlaceholder")} value={body} onChange={(e) => setBody(e.target.value)} data-testid="comment-input" />
            <Button size="sm" type="submit" disabled={!body.trim() || m.isPending} data-testid="comment-post">{m.isPending && <Loader2 className="animate-spin" />} {t("ac.detail.post")}</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function OverviewTab({ inv }: { inv: InvestigationDetail }) {
  const { t, lang } = useI18n();
  const link = inv.anomaly_links[0];
  const snap = link?.snapshot;
  const rows: [string, React.ReactNode][] = [
    [t("ac.field.category"), t(`ac.category.${inv.category}` as TKey)],
    [t("ac.field.line"), `${inv.line.code} · ${inv.line.name}`],
    [t("ac.field.machine"), inv.machine ? `${inv.machine.code} · ${inv.machine.name}` : t("ac.noMachine")],
    [t("ac.field.owner"), <OwnerLabel key="o" u={inv.owner} />],
    [t("ac.field.createdBy"), inv.created_by_label],
    [t("ac.field.created"), fmtDateTime(inv.created_at, lang)],
    [t("ac.field.due"), <span key="d">{fmtDate(inv.due_date, lang)} <OverdueBadge days={inv.overdue ? inv.days_overdue : null} /></span>],
    [t("ac.field.source"), t(`ac.source.${inv.source}` as TKey)],
    ...(inv.resolved_at ? [[t("ac.field.resolvedAt"), fmtDateTime(inv.resolved_at, lang)] as [string, string]] : []),
    ...(inv.closed_at ? [[t("ac.field.closedAt"), fmtDateTime(inv.closed_at, lang)] as [string, string]] : []),
  ];
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-2">
        <Card>
          <CardHeader><CardTitle>{t("ac.field.description")}</CardTitle></CardHeader>
          <CardContent>
            <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700" data-testid="inv-description">{inv.description || "–"}</p>
            {inv.resolution_summary && (
              <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-3">
                <div className="text-xs font-semibold uppercase text-emerald-800">{t("ac.field.resolution")}</div>
                <p className="whitespace-pre-line text-sm text-emerald-900">{inv.resolution_summary}</p>
              </div>
            )}
          </CardContent>
        </Card>
        {link && snap && (
          <Card data-testid="anomaly-snapshot">
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><BrainCircuit className="h-4 w-4 text-teal-600" />{t("ac.detail.anomaly")}</CardTitle>
              <CardDescription>{t("ac.detail.anomalyNote")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex flex-wrap items-center gap-2 font-medium">
                {t(`metric.${link.metric}` as TKey)} · {link.entity_code} {link.severity && <SevBadge s={link.severity} />}
                <span className="text-xs font-normal text-muted-foreground">{link.anomaly_key}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[[t("ac.detail.detectedOn"), fmtDate(link.date, lang)], [t("ac.detail.observed"), fmtPct(link.observed, lang)],
                  [t("ac.detail.baseline"), fmtPct(link.baseline, lang)], [t("ac.detail.zscore"), fmtNumber(snap.z_score ?? null, lang, 1)]].map(([k, val]) => (
                  <div key={k} className="rounded-md border bg-slate-50 p-2"><div className="text-[11px] uppercase text-slate-500">{k}</div><div className="tabular font-semibold">{val}</div></div>
                ))}
              </div>
              <p className="text-xs leading-relaxed text-slate-600">{lang === "de" && snap.explanation_de ? snap.explanation_de : snap.explanation}</p>
              <div className="text-[11px] text-muted-foreground">{fmtDateTime(snap.captured_at ?? link.linked_at, lang)}</div>
              <Button size="sm" variant="outline" asChild><Link to="/insights">{t("ac.detail.openInInsights")}</Link></Button>
            </CardContent>
          </Card>
        )}
        <Comments inv={inv} />
      </div>
      <Card className="self-start">
        <CardContent className="pt-5">
          <dl className="space-y-2 text-sm">
            {rows.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[120px_1fr] gap-2 border-b pb-2 last:border-0">
                <dt className="text-muted-foreground">{k}</dt><dd className="text-slate-800">{v}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- page */

export default function InvestigationDetailPage() {
  const { id } = useParams();
  const invId = Number(id);
  const { t } = useI18n();
  const { me } = useAuth();
  const toast = useToast();
  const invalidate = useAcInvalidate();
  const q = useInvestigation(invId);
  const [to, setTo] = useState<InvStatus | null>(null);
  const [edit, setEdit] = useState(false);
  const [tab, setTab] = useState("overview");
  const take = useMutation({
    mutationFn: () => postJson(`/api/investigations/${invId}`, { owner_id: me!.user.id }, "PATCH"),
    onSuccess: () => { toast.success(t("ac.detail.saved")); invalidate(invId); },
    onError: (e) => toast.error((e as Error).message),
  });
  if (!Number.isFinite(invId)) return <ErrorState error={new Error("Invalid id")} />;
  if (q.isLoading) return <LoadingState />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const inv = q.data!;
  const p = inv.permissions;
  const canTake = !inv.owner && p.can_contribute && !p.can_assign_any;

  return (
    <div>
      <Link to="/actions/investigations" className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"><ChevronLeft className="h-3.5 w-3.5" />{t("ac.detail.back")}</Link>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold text-slate-500" data-testid="inv-number">{inv.number}</span>
            <StatusBadge s={inv.status} /><SevBadge s={inv.severity} />
            {inv.reopen_count > 0 && <Badge variant="outline">↺ {inv.reopen_count}</Badge>}
            {inv.is_synthetic && <Badge variant="secondary">{t("ac.synthetic")}</Badge>}
          </div>
          <h1 className="page-title mt-1" data-testid="inv-title">{inv.title}</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {canTake && <Button variant="outline" onClick={() => take.mutate()} disabled={take.isPending} data-testid="take-ownership"><UserCheck /> {t("ac.detail.takeOwnership")}</Button>}
          {p.can_edit && inv.status !== "closed" && <Button variant="outline" onClick={() => setEdit(true)} data-testid="edit-inv"><Pencil /> {t("ac.detail.edit")}</Button>}
        </div>
      </div>

      <Card className="mb-4" data-testid="workflow">
        <CardContent className="flex flex-wrap items-center gap-2 py-3">
          <span className="mr-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{t("ac.detail.workflow")}</span>
          {inv.allowed_transitions.length === 0 ? <span className="text-sm text-muted-foreground">{t("ac.detail.noTransitions")}</span> :
            inv.allowed_transitions.map((s) => {
              const blocked = s === "pending_verification" && (inv.actions.length === 0 || inv.actions.some((a) => a.status === "open" || a.status === "in_progress"));
              return (
                <Button key={s} size="sm" variant={s === "closed" ? "outline" : "default"} onClick={() => setTo(s)} data-testid={`to-${s}`}
                  disabled={blocked} title={blocked ? t("ac.transition.needsActions") : undefined}>{t(`ac.to.${s}` as TKey)}</Button>
              );
            })}
          {inv.allowed_transitions.includes("pending_verification") && (inv.actions.length === 0 || inv.actions.some((a) => a.status === "open" || a.status === "in_progress")) && (
            <span className="text-xs text-muted-foreground" data-testid="needs-actions-hint">{t("ac.transition.needsActions")}</span>
          )}
        </CardContent>
      </Card>

      {inv.status === "closed" && <p className="mb-4 flex items-center gap-2 rounded-md border bg-slate-50 p-3 text-sm text-slate-700"><Lock className="h-4 w-4" />{t("ac.detail.closedReadOnly")}</p>}
      {inv.status !== "closed" && !p.can_edit && p.can_contribute && <p className="mb-4 rounded-md border bg-slate-50 p-3 text-xs text-slate-600">{t("ac.detail.readOnly")}</p>}
      {inv.category === "safety_observation" && <p className="mb-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><ShieldAlert className="h-4 w-4 shrink-0" />{t("ac.detail.safetyNote")}</p>}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4 h-auto flex-wrap">
          <TabsTrigger value="overview">{t("ac.detail.tab.overview")}</TabsTrigger>
          <TabsTrigger value="actions" data-testid="tab-actions">{t("ac.detail.tab.actions")} ({inv.actions.length})</TabsTrigger>
          <TabsTrigger value="timeline" data-testid="tab-timeline">{t("ac.detail.tab.timeline")}</TabsTrigger>
          <TabsTrigger value="evidence" data-testid="tab-evidence">{t("ac.detail.tab.evidence")}</TabsTrigger>
          <TabsTrigger value="kpi" data-testid="tab-kpi">{t("ac.detail.tab.kpi")}</TabsTrigger>
        </TabsList>
        <TabsContent value="overview"><OverviewTab inv={inv} /></TabsContent>
        <TabsContent value="actions"><ActionsTab inv={inv} /></TabsContent>
        <TabsContent value="timeline"><TimelineTab inv={inv} /></TabsContent>
        <TabsContent value="evidence"><EvidenceTab inv={inv} /></TabsContent>
        <TabsContent value="kpi"><KpiTab inv={inv} /></TabsContent>
      </Tabs>

      <TransitionDialog inv={inv} to={to} onClose={() => setTo(null)} />
      {p.can_edit && <EditDialog inv={inv} open={edit} onOpenChange={setEdit} />}
    </div>
  );
}

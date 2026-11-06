/** Corrective-action widgets shared by the investigation detail and "My Actions". */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Loader2, Pencil, UserRound } from "lucide-react";
import { ActionStatusBadge, FieldSelect, OverdueBadge, OwnerLabel, SevBadge } from "@/components/ac";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { postJson } from "@/lib/api";
import { SEVERITIES, useAssignees, type ActionItem, type ActionStatus, type Severity, type VerificationResult } from "@/lib/actionCenter";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export const textareaCls = "flex w-full rounded-md border border-input bg-white px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

/** Invalidate everything that shows investigations or actions. */
export function useAcInvalidate() {
  const qc = useQueryClient();
  return (invId?: number) => {
    qc.invalidateQueries({ queryKey: ["ac-summary"] });
    qc.invalidateQueries({ queryKey: ["investigations"] });
    qc.invalidateQueries({ queryKey: ["my-actions"] });
    if (invId) qc.invalidateQueries({ queryKey: ["investigation", invId] });
  };
}

const TRANSITION_LABEL: Record<ActionStatus, TKey> = {
  open: "ac.actions.backToOpen", in_progress: "ac.actions.start", done: "ac.actions.complete",
  verified: "ac.actions.verify", cancelled: "ac.actions.cancel",
};

function transitionLabel(from: ActionStatus, to: ActionStatus): TKey {
  if (to === "in_progress" && (from === "done" || from === "verified")) return "ac.actions.reopen";
  if (to === "open" && from === "cancelled") return "ac.actions.reopen";
  return TRANSITION_LABEL[to];
}

function TransitionDialog({ action, to, onClose }: { action: ActionItem; to: ActionStatus | null; onClose: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const invalidate = useAcInvalidate();
  const [notes, setNotes] = useState("");
  const [result, setResult] = useState<VerificationResult | "">("");
  const [error, setError] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => postJson<ActionItem>(`/api/actions/${action.id}`, {
      status: to,
      completion_notes: to === "done" ? notes.trim() : undefined,
      verification_result: to === "verified" ? result : undefined,
      verification_notes: to === "verified" ? notes.trim() || undefined : undefined,
    }, "PATCH"),
    onSuccess: () => { toast.success(t("ac.detail.saved")); invalidate(action.investigation.id); close(); },
    onError: (e) => setError((e as Error).message),
  });
  const close = () => { setNotes(""); setResult(""); setError(null); onClose(); };
  const ok = to === "done" ? notes.trim().length > 0 : to === "verified" ? !!result : true;
  return (
    <Dialog open={!!to} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-w-md">
        {to && (
          <>
            <DialogHeader>
              <DialogTitle>{t(transitionLabel(action.status, to))}</DialogTitle>
              <DialogDescription>{action.code} · {action.title}</DialogDescription>
            </DialogHeader>
            {to === "verified" && (
              <FieldSelect id="verify-result" label={t("ac.actions.verificationResult")} value={result} onChange={(v) => setResult(v as VerificationResult)} allLabel="—"
                options={(["effective", "partially_effective", "not_effective"] as const).map((r) => ({ value: r, label: t(`ac.verification.${r}` as TKey) }))} />
            )}
            {(to === "done" || to === "verified") && (
              <div className="flex flex-col gap-1">
                <Label htmlFor="act-notes">{to === "done" ? t("ac.actions.completionNotes") : t("ac.actions.verificationNotes")}</Label>
                <textarea id="act-notes" className={textareaCls} rows={4} maxLength={5000} value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="action-notes" />
              </div>
            )}
            {to === "verified" && <p className="text-xs text-muted-foreground">{t("ac.kpi.disclaimer")}</p>}
            {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={close}>{t("common.cancel")}</Button>
              <Button onClick={() => m.mutate()} disabled={!ok || m.isPending} variant={to === "cancelled" ? "destructive" : "default"} data-testid="action-transition-ok">
                {m.isPending && <Loader2 className="animate-spin" />} {t(transitionLabel(action.status, to))}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export interface ActionFormValue { title: string; description: string; ownerId: string; dueDate: string; priority: Severity }

/** Create (investigationId given) or edit (action given) a corrective action. */
export function ActionFormDialog({ open, onOpenChange, investigationId, action, canAssignOthers }: {
  open: boolean; onOpenChange: (o: boolean) => void; investigationId: number; action?: ActionItem; canAssignOthers: boolean;
}) {
  const { t } = useI18n();
  const { me } = useAuth();
  const toast = useToast();
  const invalidate = useAcInvalidate();
  const users = useAssignees();
  const init = (): ActionFormValue => action
    ? { title: action.title, description: action.description, ownerId: action.owner ? String(action.owner.id) : "", dueDate: action.due_date ?? "", priority: action.priority }
    : { title: "", description: "", ownerId: "", dueDate: "", priority: "medium" };
  const [v, setV] = useState<ActionFormValue>(init);
  const [error, setError] = useState<string | null>(null);
  const set = (p: Partial<ActionFormValue>) => setV((x) => ({ ...x, ...p }));
  const m = useMutation({
    mutationFn: () => {
      const body = { title: v.title.trim(), description: v.description.trim(), owner_id: v.ownerId ? Number(v.ownerId) : null,
        due_date: v.dueDate || null, priority: v.priority };
      return action
        ? postJson<ActionItem>(`/api/actions/${action.id}`, { ...body, owner_id: body.owner_id ?? undefined, due_date: body.due_date ?? undefined }, "PATCH")
        : postJson<ActionItem>(`/api/investigations/${investigationId}/actions`, body);
    },
    onSuccess: () => { toast.success(action ? t("ac.detail.saved") : t("ac.actions.created")); invalidate(investigationId); onOpenChange(false); },
    onError: (e) => setError((e as Error).message),
  });
  const owners = (users.data ?? []).filter((u) => canAssignOthers || u.id === me?.user.id);
  return (
    <Dialog open={open} onOpenChange={(o) => { if (o) { setV(init()); setError(null); } onOpenChange(o); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{action ? `${t("ac.detail.edit")} · ${action.code}` : t("ac.actions.add")}</DialogTitle>
          <DialogDescription>{t("ac.actions.illustrative")}</DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); setError(null); m.mutate(); }} data-testid="action-form">
          <div className="flex flex-col gap-1">
            <Label htmlFor="act-title">{t("ac.actions.title")} *</Label>
            <Input id="act-title" value={v.title} maxLength={300} onChange={(e) => set({ title: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="act-desc">{t("ac.field.description")}</Label>
            <textarea id="act-desc" className={textareaCls} rows={3} maxLength={5000} value={v.description} onChange={(e) => set({ description: e.target.value })} />
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <FieldSelect id="act-owner" label={t("ac.actions.owner")} value={v.ownerId} onChange={(x) => set({ ownerId: x })} allLabel={action ? undefined : t("ac.unassigned")}
              options={owners.map((u) => ({ value: String(u.id), label: u.display_name }))} />
            <div className="flex flex-col gap-1">
              <Label htmlFor="act-due">{t("ac.field.due")}</Label>
              <Input id="act-due" type="date" value={v.dueDate} onChange={(e) => set({ dueDate: e.target.value })} />
            </div>
            <FieldSelect id="act-priority" label={t("ac.field.priority")} value={v.priority} onChange={(x) => set({ priority: x as Severity })}
              options={SEVERITIES.map((s) => ({ value: s, label: t(`severity.${s}` as TKey) }))} />
          </div>
          {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={v.title.trim().length < 3 || m.isPending} data-testid="action-save">
              {m.isPending && <Loader2 className="animate-spin" />} {t("common.save")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ActionCard({ a, showInvestigation, canAssignOthers }: { a: ActionItem; showInvestigation?: boolean; canAssignOthers?: boolean }) {
  const { t, lang } = useI18n();
  const [to, setTo] = useState<ActionStatus | null>(null);
  const [edit, setEdit] = useState(false);
  return (
    <div className={cn("rounded-lg border bg-white p-3", a.overdue && "border-red-200 bg-red-50/30")} data-testid={`action-${a.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium text-muted-foreground">{a.code}</span>
            <ActionStatusBadge s={a.status} /> <SevBadge s={a.priority} /> <OverdueBadge days={a.days_overdue} />
          </div>
          <div className="mt-1 font-medium text-slate-800">{a.title}</div>
          {a.description && <p className="mt-0.5 whitespace-pre-line text-sm text-slate-600">{a.description}</p>}
          {showInvestigation && (
            <Link className="mt-1 inline-block text-xs text-primary hover:underline" to={`/actions/investigations/${a.investigation.id}`}>
              {a.investigation.number} · {a.investigation.title} ({a.investigation.line_code}{a.investigation.machine_code ? ` · ${a.investigation.machine_code}` : ""})
            </Link>
          )}
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
            <span className="inline-flex items-center gap-1"><UserRound className="h-3.5 w-3.5" /><OwnerLabel u={a.owner} /></span>
            <span className="inline-flex items-center gap-1"><CalendarClock className="h-3.5 w-3.5" />{fmtDate(a.due_date, lang)}</span>
            {a.completed_at && <span>{t("ac.actions.completedAt", { time: fmtDateTime(a.completed_at, lang) })}</span>}
          </div>
          {a.completion_notes && <p className="mt-1.5 rounded bg-slate-50 p-2 text-xs text-slate-700">{a.completion_notes}</p>}
          {a.verification_result && (
            <p className="mt-1.5 rounded bg-emerald-50 p-2 text-xs text-emerald-900">
              <span className="font-semibold">{t(`ac.verification.${a.verification_result}` as TKey)}</span>
              {a.verified_by && <> · {t("ac.actions.verifiedBy", { name: a.verified_by.display_name })} {fmtDateTime(a.verified_at, lang)}</>}
              {a.verification_notes && <span className="block">{a.verification_notes}</span>}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {a.allowed_transitions.map((s) => (
            <Button key={s} size="sm" variant={s === "cancelled" ? "ghost" : "outline"} onClick={() => setTo(s)} data-testid={`action-${a.id}-to-${s}`}>
              {t(transitionLabel(a.status, s))}
            </Button>
          ))}
          {a.can_edit && !showInvestigation && (
            <Button size="sm" variant="ghost" onClick={() => setEdit(true)} aria-label={t("ac.detail.edit")}><Pencil /></Button>
          )}
        </div>
      </div>
      <TransitionDialog action={a} to={to} onClose={() => setTo(null)} />
      {a.can_edit && <ActionFormDialog open={edit} onOpenChange={setEdit} investigationId={a.investigation.id} action={a} canAssignOthers={canAssignOthers ?? true} />}
    </div>
  );
}

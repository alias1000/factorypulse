import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BellRing, Loader2, Save } from "lucide-react";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { postJson } from "@/lib/api";
import { SEVERITIES, useRules, type Rules } from "@/lib/actionCenter";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";

const FIELDS: { key: keyof Omit<Rules, "default_due_days" | "timezone">; min: number; max: number; step: number }[] = [
  { key: "critical_unassigned_hours", min: 0, max: 720, step: 0.5 },
  { key: "critical_open_hours", min: 1, max: 8760, step: 1 },
  { key: "pending_verification_days", min: 1, max: 365, step: 1 },
  { key: "action_overdue_grace_days", min: 0, max: 90, step: 1 },
  { key: "repeated_anomaly_count", min: 2, max: 50, step: 1 },
  { key: "repeated_anomaly_window_days", min: 7, max: 365, step: 1 },
];

/** Action Center alert thresholds; editable by administrators only (enforced by the API). */
export function RulesCard() {
  const { t } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useRules();
  const [v, setV] = useState<Rules | null>(null);
  useEffect(() => { if (q.data) setV(q.data); }, [q.data]);
  const canEdit = can("action.manage");
  const save = useMutation({
    mutationFn: (r: Rules) => {
      const { timezone: _tz, ...body } = r; // eslint-disable-line @typescript-eslint/no-unused-vars
      return postJson<Rules>("/api/action-center/settings", body, "PUT");
    },
    onSuccess: () => { toast.success(t("settings.saved")); qc.invalidateQueries({ queryKey: ["ac-rules"] }); qc.invalidateQueries({ queryKey: ["ac-summary"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  const bad = v ? FIELDS.some((f) => !(Number.isFinite(v[f.key]) && v[f.key] >= f.min && v[f.key] <= f.max))
    || SEVERITIES.some((s) => !(Number.isInteger(v.default_due_days[s]) && v.default_due_days[s] >= 0 && v.default_due_days[s] <= 365)) : true;
  return (
    <Card data-testid="rules-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><BellRing className="h-4 w-4 text-amber-600" />{t("ac.rules.title")}</CardTitle>
        <CardDescription>{t("ac.rules.desc", { tz: q.data?.timezone ?? "" })}</CardDescription>
      </CardHeader>
      <CardContent>
        {q.isError ? <ErrorState error={q.error} /> : !v ? <LoadingState /> : (
          <form onSubmit={(e) => { e.preventDefault(); save.mutate(v); }}>
            <fieldset disabled={!canEdit} className="grid gap-3 sm:grid-cols-2">
              {FIELDS.map((f) => (
                <div key={f.key} className="flex flex-col gap-1">
                  <Label htmlFor={`rule-${f.key}`}>{t(`ac.rules.${f.key}` as TKey)}</Label>
                  <Input id={`rule-${f.key}`} type="number" min={f.min} max={f.max} step={f.step} value={v[f.key]}
                    onChange={(e) => setV({ ...v, [f.key]: e.target.value === "" ? NaN : Number(e.target.value) })} />
                </div>
              ))}
              <div className="sm:col-span-2">
                <div className="mb-1 text-xs font-medium text-slate-600">{t("ac.rules.defaultDue")}</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {SEVERITIES.map((s) => (
                    <div key={s} className="flex flex-col gap-1">
                      <Label htmlFor={`rule-due-${s}`}>{t(`severity.${s}` as TKey)}</Label>
                      <Input id={`rule-due-${s}`} type="number" min={0} max={365} step={1} value={v.default_due_days[s]}
                        onChange={(e) => setV({ ...v, default_due_days: { ...v.default_due_days, [s]: e.target.value === "" ? NaN : Number(e.target.value) } })} />
                    </div>
                  ))}
                </div>
              </div>
            </fieldset>
            {!canEdit && <p className="mt-3 text-xs text-muted-foreground">{t("auth.readOnlyHint")}</p>}
            {canEdit && (
              <Button className="mt-4" type="submit" disabled={bad || save.isPending} data-testid="rules-save">
                {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} {t("common.save")}
              </Button>
            )}
          </form>
        )}
      </CardContent>
    </Card>
  );
}

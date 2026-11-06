import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Building2, Loader2, Save, Server, Sigma } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { RulesCard } from "@/components/RulesCard";
import { AnomalyConfigForm, validateConfig } from "@/components/AnomalyConfigForm";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { postJson } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { useSettings } from "@/lib/queries";
import type { AnomalyConfig, SettingsPayload } from "@/lib/types";

const KPI_DEFS: TKey[] = [
  "formula.target_achievement", "formula.defect_rate", "formula.quality", "formula.availability", "formula.performance", "formula.oee",
  "formula.unplanned_downtime_minutes",
];

export default function SettingsPage() {
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const s = useSettings();
  const { can } = useAuth();
  const canWrite = can("settings.write");
  const [name, setName] = useState("");
  const [defLang, setDefLang] = useState<"de" | "en">("de");
  const [cfg, setCfg] = useState<AnomalyConfig | null>(null);

  useEffect(() => {
    if (s.data) {
      setName(s.data.company_name);
      setDefLang(s.data.default_language);
      setCfg(s.data.anomaly_config);
    }
  }, [s.data]);

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => postJson<SettingsPayload>("/api/settings", body, "PUT"),
    onSuccess: () => { toast.success(t("settings.saved")); qc.invalidateQueries(); },
    onError: (e) => toast.error((e as Error).message),
  });

  if (s.isLoading) return <LoadingState />;
  if (s.isError) return <ErrorState error={s.error} onRetry={() => s.refetch()} />;
  const sys = s.data!.system;
  const cfgError = cfg ? validateConfig(cfg) : null;

  return (
    <div>
      <PageHeader title={t("settings.title")} subtitle={t("settings.subtitle")} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Building2 className="h-4 w-4 text-blue-600" />{t("settings.company")}</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="cname">{t("settings.companyName")}</Label>
              <Input id="cname" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="flex w-56 flex-col gap-1">
              <Label>{t("settings.defaultLanguage")}</Label>
              <Select value={defLang} onValueChange={(v) => setDefLang(v as "de" | "en")}>
                <SelectTrigger aria-label={t("settings.defaultLanguage")}><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="de">Deutsch</SelectItem><SelectItem value="en">English</SelectItem></SelectContent>
              </Select>
            </div>
            {!canWrite && <p className="text-xs text-muted-foreground">{t("auth.readOnlyHint")}</p>}
            <Button onClick={() => save.mutate({ company_name: name.trim(), default_language: defLang })} disabled={!canWrite || !name.trim() || save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} {t("common.save")}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.anomaly")}</CardTitle><CardDescription>{t("settings.anomalyDesc")}</CardDescription></CardHeader>
          <CardContent>
            {cfg && (
              <>
                <AnomalyConfigForm value={cfg} onChange={setCfg} idPrefix="set" />
                {cfgError && <p className="mt-3 text-xs font-medium text-red-600">{cfgError}</p>}
                <Button className="mt-4" onClick={() => save.mutate({ anomaly_config: cfg })} disabled={!canWrite || !!cfgError || save.isPending}><Save /> {t("common.save")}</Button>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Sigma className="h-4 w-4 text-blue-600" />{t("settings.kpiDefs")}</CardTitle><CardDescription>{t("settings.kpiDefsDesc")}</CardDescription></CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              {KPI_DEFS.map((k) => (
                <div key={k} className="grid grid-cols-[150px_1fr] gap-2 border-b pb-2 last:border-0">
                  <dt className="font-medium text-slate-700">{t(k.replace("formula.", "kpi.") as TKey)}</dt>
                  <dd className="text-slate-600">{t(k)}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 rounded-md bg-slate-50 p-2.5 text-xs text-slate-600">{t("settings.timeModel")}</p>
          </CardContent>
        </Card>

        {can("action.read") && <RulesCard />}

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Server className="h-4 w-4 text-blue-600" />{t("settings.system")}</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[180px_1fr] gap-y-2 text-sm">
              <dt className="text-muted-foreground">{t("settings.environment")}</dt><dd>{sys.environment}</dd>
              <dt className="text-muted-foreground">{t("settings.database")}</dt><dd>{sys.database}</dd>
              <dt className="text-muted-foreground">{t("settings.uploadLimit")}</dt><dd>{sys.max_upload_mb} MB</dd>
              <dt className="text-muted-foreground">{t("settings.rowLimit")}</dt><dd>{sys.max_import_rows.toLocaleString()}</dd>
              <dt className="text-muted-foreground">{t("settings.fileTypes")}</dt><dd>{sys.allowed_file_types.join(", ")}</dd>
              <dt className="text-muted-foreground">{t("settings.externalAi")}</dt><dd className="text-emerald-700">{t("settings.externalAiValue")}</dd>
              <dt className="text-muted-foreground">Version</dt><dd>{sys.version} · {sys.build} · {sys.timezone}</dd>
              <dt className="text-muted-foreground">API</dt><dd><a className="text-primary underline-offset-2 hover:underline" href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></dd>
            </dl>
          </CardContent>
        </Card>

      </div>
    </div>
  );
}

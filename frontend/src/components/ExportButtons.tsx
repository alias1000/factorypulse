import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { downloadFile } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useToast } from "./Toast";

export function ExportButtons({ path }: { path: (format: "csv" | "xlsx") => string }) {
  const { t } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (fmt: "csv" | "xlsx") => {
    setBusy(fmt);
    try {
      await downloadFile(path(fmt), `export.${fmt}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex items-center gap-1.5" role="group" aria-label={t("common.export")}>
      {(["csv", "xlsx"] as const).map((f) => (
        <Button key={f} variant="outline" size="sm" onClick={() => run(f)} disabled={!!busy} data-testid={`export-${f}`}>
          {busy === f ? <Loader2 className="animate-spin" /> : <Download />}
          {f === "csv" ? t("common.exportCsv") : t("common.exportXlsx")}
        </Button>
      ))}
    </div>
  );
}

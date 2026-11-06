import { useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { useI18n } from "@/lib/i18n";

const textareaCls = "flex w-full rounded-md border border-input bg-white px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** Confirmation dialog that asks for a mandatory free-text reason (accessible replacement for window.prompt). */
export function PromptDialog({ trigger, title, description, label, minLength = 1, confirmLabel, destructive, onConfirm }: {
  trigger: (open: () => void) => ReactNode; title: string; description: string; label: string; minLength?: number;
  confirmLabel: string; destructive?: boolean; onConfirm: (text: string) => Promise<unknown>;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const close = (o: boolean) => { setOpen(o); if (!o) { setText(""); setError(null); } };
  const run = async () => {
    setBusy(true); setError(null);
    try { await onConfirm(text.trim()); close(false); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <>
      {trigger(() => setOpen(true))}
      <Dialog open={open} onOpenChange={close}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription>{description}</DialogDescription></DialogHeader>
          <div className="flex flex-col gap-1">
            <Label htmlFor="prompt-text">{label}</Label>
            <textarea id="prompt-text" className={textareaCls} rows={3} maxLength={5000} value={text} onChange={(e) => setText(e.target.value)} data-testid="prompt-text" />
          </div>
          {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => close(false)}>{t("common.cancel")}</Button>
            <Button variant={destructive ? "destructive" : "default"} disabled={text.trim().length < minLength || busy} onClick={run} data-testid="prompt-ok">
              {busy && <Loader2 className="animate-spin" />} {confirmLabel}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

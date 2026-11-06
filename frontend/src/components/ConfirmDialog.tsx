import { useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";

interface Props {
  trigger: (open: () => void) => ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  requireText?: string;
  /** Ask for the user's current password (server-side re-authentication). */
  requirePassword?: boolean;
  destructive?: boolean;
  onConfirm: (password: string) => Promise<unknown> | void;
}

/** Accessible confirmation dialog (replaces window.confirm). */
export function ConfirmDialog({ trigger, title, description, confirmLabel, requireText, requirePassword, destructive, onConfirm }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ok = (!requireText || text === requireText) && (!requirePassword || password.length > 0);
  const close = (o: boolean) => {
    setOpen(o);
    if (!o) { setText(""); setPassword(""); setError(null); }
  };
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(password);
      close(false);
    } catch (e) {
      setError((e as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {trigger(() => setOpen(true))}
      <Dialog open={open} onOpenChange={close}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {requireText && (
            <div className="space-y-1">
              <label htmlFor="confirm-word" className="text-xs font-medium text-slate-600">{t("admin.confirmWord", { word: requireText })}</label>
              <Input id="confirm-word" value={text} onChange={(e) => setText(e.target.value)} placeholder={requireText} autoComplete="off" data-testid="confirm-input" />
            </div>
          )}
          {requirePassword && (
            <div className="space-y-1">
              <label htmlFor="confirm-pw" className="text-xs font-medium text-slate-600">{t("admin.confirmPassword")}</label>
              <Input id="confirm-pw" type="password" autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} data-testid="confirm-password" />
            </div>
          )}
          {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => close(false)}>{t("common.cancel")}</Button>
            <Button variant={destructive ? "destructive" : "default"} disabled={!ok || busy} onClick={run} data-testid="confirm-ok">
              {busy && <Loader2 className="animate-spin" />} {confirmLabel}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

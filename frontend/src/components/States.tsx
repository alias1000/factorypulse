import { AlertCircle, Inbox, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function LoadingState({ className, label }: { className?: string; label?: string }) {
  const { t } = useI18n();
  return (
    <div className={cn("flex h-full min-h-[160px] items-center justify-center gap-2 text-sm text-muted-foreground", className)} role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label ?? t("common.loading")}
    </div>
  );
}

export function ChartSkeleton({ height = 260 }: { height?: number }) {
  return <Skeleton style={{ height }} className="w-full" />;
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const { t } = useI18n();
  return (
    <div className={cn("flex min-h-[140px] flex-col items-center justify-center gap-2 rounded-md border border-red-200 bg-red-50/60 p-4 text-center", className)} role="alert">
      <AlertCircle className="h-5 w-5 text-red-600" aria-hidden />
      <div className="text-sm font-medium text-red-800">{t("common.error")}</div>
      <div className="max-w-md text-xs text-red-700">{(error as Error)?.message}</div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw /> {t("common.retry")}
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, hint, className, icon: Icon = Inbox }: { title?: string; hint?: string; className?: string; icon?: typeof Inbox }) {
  const { t } = useI18n();
  return (
    <div className={cn("flex min-h-[160px] flex-col items-center justify-center gap-1.5 text-center", className)}>
      <Icon className="h-8 w-8 text-slate-300" aria-hidden />
      <div className="text-sm font-medium text-slate-600">{title ?? t("common.noData")}</div>
      <div className="max-w-sm text-xs text-muted-foreground">{hint ?? t("common.noDataHint")}</div>
    </div>
  );
}

import { useState } from "react";
import { AlarmClock, CalendarDays, CheckCheck, ClipboardCheck, Hourglass, Inbox } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ActionCard } from "@/components/acActions";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useMyActions, type ActionItem } from "@/lib/actionCenter";
import { useI18n, type TKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function Group({ title, items, icon: Icon, tone, testId }: { title: string; items: ActionItem[]; icon: typeof Inbox; tone: string; testId: string }) {
  if (items.length === 0) return null;
  return (
    <Card data-testid={testId}>
      <CardHeader><CardTitle className="flex items-center gap-2"><Icon className={cn("h-4 w-4", tone)} />{title} <span className="text-sm font-normal text-muted-foreground">({items.length})</span></CardTitle></CardHeader>
      <CardContent className="space-y-2">{items.map((a) => <ActionCard key={a.id} a={a} showInvestigation />)}</CardContent>
    </Card>
  );
}

export default function MyActionsPage() {
  const { t } = useI18n();
  const [finished, setFinished] = useState(false);
  const q = useMyActions(finished);
  const d = q.data;
  const total = d ? d.overdue.length + d.due_soon.length + d.later.length + d.awaiting_verification.length + (finished ? d.finished.length : 0) : 0;
  const groups: [TKey, keyof typeof GROUP_META][] = [
    ["ac.mine.overdue", "overdue"], ["ac.mine.dueSoon", "due_soon"], ["ac.mine.later", "later"], ["ac.mine.awaiting", "awaiting_verification"], ["ac.mine.finished", "finished"],
  ];
  return (
    <div>
      <PageHeader title={t("ac.mine.title")} subtitle={d ? t("ac.mine.subtitle", { tz: d.timezone }) : undefined} actions={
        <div className="flex items-center gap-2">
          <Switch id="show-finished" checked={finished} onCheckedChange={setFinished} />
          <Label htmlFor="show-finished">{t("ac.mine.showFinished")}</Label>
        </div>
      } />
      {q.isLoading ? <LoadingState /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : total === 0 ? (
        <Card><CardContent className="py-6"><EmptyState title={t("ac.mine.empty")} hint="" icon={ClipboardCheck} /></CardContent></Card>
      ) : (
        <div className="space-y-4">
          {groups.map(([label, key]) => (key !== "finished" || finished) && (
            <Group key={key} title={t(label)} items={d![key]} icon={GROUP_META[key].icon} tone={GROUP_META[key].tone} testId={`mine-${key}`} />
          ))}
        </div>
      )}
    </div>
  );
}

const GROUP_META = {
  overdue: { icon: AlarmClock, tone: "text-red-600" },
  due_soon: { icon: CalendarDays, tone: "text-amber-600" },
  later: { icon: Inbox, tone: "text-slate-500" },
  awaiting_verification: { icon: Hourglass, tone: "text-violet-600" },
  finished: { icon: CheckCheck, tone: "text-emerald-600" },
} as const;

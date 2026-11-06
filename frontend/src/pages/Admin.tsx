import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive, CheckCircle2, Database, Download, Loader2, Lock, RefreshCw, RotateCcw, Trash2, Unlock, UserPlus, XCircle,
  AlertTriangle,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, downloadFile, postJson } from "@/lib/api";
import { useAuth, type AuthUser, type Role } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import { useI18n, type TKey } from "@/lib/i18n";
import { useSettings } from "@/lib/queries";
import { fmtBytes, fmtDateTime } from "@/lib/format";
import type { Meta } from "@/lib/types";
import { cn } from "@/lib/utils";

const ROLES: Role[] = ["viewer", "analyst", "admin"];

interface BackupManifest {
  name: string; created_at: string; created_by: string | null; kind: string; note: string | null;
  alembic_revision: string; app_version: string; total_size: number;
  database: { size: number; sha256: string }; files: { count: number };
}
interface SystemCheck { name: string; status: "ok" | "warn" | "fail"; detail: string }
interface SystemStatus { status: string; version: string; build: string; environment: string; checks: SystemCheck[] }
interface ScannerHealth {
  scanner: string; status: "unknown" | "healthy" | "unavailable"; checked_at: string | null; last_ok_at: string | null;
  last_failure_at: string | null; unavailable_since: string | null; consecutive_failures: number; last_error: string | null; detail: string | null;
}
interface EvidenceStatus {
  scanner: "none" | "clamd" | "command"; scanner_label: string; health: ScannerHealth | null; health_stale: boolean;
  quarantine: { pending: number; retrying: number; retries_exhausted: number; not_scanned: number; infected: number; max_attempts: number };
  storage: { ops_pending: number; ops_failed: number; misplaced: number; missing: number; orphaned: number };
}
interface AuditRow { id: number; event: string; entity_type: string | null; entity_id: number | null; details: Record<string, unknown> | null; user_email: string | null; client_ip: string | null; created_at: string | null }

function RoleBadge({ role }: { role: Role }) {
  const { t } = useI18n();
  return <Badge variant={role === "admin" ? "danger" : role === "analyst" ? "default" : "secondary"}>{t(`auth.role.${role}` as TKey)}</Badge>;
}

// ----------------------------------------------------------------------------- users

function CreateUserDialog() {
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", display_name: "", role: "viewer" as Role, temporary_password: "" });
  const create = useMutation({
    mutationFn: () => postJson<AuthUser>("/api/users", form),
    onSuccess: () => {
      toast.success(t("admin.users.created"));
      qc.invalidateQueries({ queryKey: ["users"] });
      setOpen(false);
      setForm({ email: "", display_name: "", role: "viewer", temporary_password: "" });
    },
  });
  return (
    <>
      <Button onClick={() => setOpen(true)} data-testid="add-user"><UserPlus /> {t("admin.users.add")}</Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); create.reset(); }}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{t("admin.users.add")}</DialogTitle><DialogDescription>{t("admin.users.tempHint")}</DialogDescription></DialogHeader>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="space-y-1"><Label htmlFor="u-email">{t("auth.email")}</Label>
              <Input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="u-name">{t("admin.users.name")}</Label>
              <Input id="u-name" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></div>
            <div className="space-y-1"><Label>{t("admin.users.role")}</Label>
              <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v as Role })}>
                <SelectTrigger aria-label={t("admin.users.role")}><SelectValue /></SelectTrigger>
                <SelectContent>{ROLES.map((r) => <SelectItem key={r} value={r}>{t(`auth.role.${r}` as TKey)}</SelectItem>)}</SelectContent>
              </Select></div>
            <div className="space-y-1"><Label htmlFor="u-pw">{t("admin.users.tempPassword")}</Label>
              <Input id="u-pw" type="password" autoComplete="new-password" required value={form.temporary_password}
                onChange={(e) => setForm({ ...form, temporary_password: e.target.value })} />
              <p className="text-[11px] text-muted-foreground">{t("auth.passwordHint")}</p></div>
            {create.isError && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{(create.error as Error).message}</div>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
              <Button type="submit" disabled={create.isPending}>{create.isPending && <Loader2 className="animate-spin" />}{t("admin.users.create")}</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ResetPasswordDialog({ user }: { user: AuthUser }) {
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const reset = useMutation({
    mutationFn: () => postJson(`/api/users/${user.id}/reset-password`, { temporary_password: pw }),
    onSuccess: () => { toast.success(t("admin.users.resetDone")); qc.invalidateQueries({ queryKey: ["users"] }); setOpen(false); setPw(""); },
  });
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>{t("admin.users.resetPassword")}</Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); reset.reset(); }}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{t("admin.users.resetPassword")}</DialogTitle><DialogDescription>{user.email} · {t("admin.users.tempHint")}</DialogDescription></DialogHeader>
          <Input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} aria-label={t("admin.users.tempPassword")} />
          {reset.isError && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{(reset.error as Error).message}</div>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
            <Button onClick={() => reset.mutate()} disabled={pw.length < 12 || reset.isPending}>{t("admin.users.resetPassword")}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UsersTab() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const { me } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<AuthUser[]>("/api/users") });
  const [showInactive, setShowInactive] = useState(false);
  const inactiveCount = (users.data ?? []).filter((u) => !u.is_active).length;
  const visible = (users.data ?? []).filter((u) => showInactive || u.is_active);
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api(`/api/users/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    onSuccess: () => { toast.success(t("admin.users.updated")); qc.invalidateQueries({ queryKey: ["users"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <div className="grid gap-4 2xl:grid-cols-3">
      <Card className="2xl:col-span-2">
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
          <div><CardTitle>{t("admin.tab.users")}</CardTitle></div>
          <div className="flex items-center gap-4">
            {inactiveCount > 0 && (
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input type="checkbox" className="h-4 w-4 accent-blue-600" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
                {t("admin.users.showInactive", { n: inactiveCount })}
              </label>
            )}
            <CreateUserDialog />
          </div>
        </CardHeader>
        <CardContent>
          {users.isLoading ? <LoadingState /> : users.isError ? <ErrorState error={users.error} onRetry={() => users.refetch()} /> : (
            <div className="overflow-auto rounded-md border">
              <Table data-testid="users-table">
                <TableHeader><TableRow>
                  <TableHead>{t("admin.users.name")}</TableHead><TableHead>{t("admin.users.role")}</TableHead>
                  <TableHead>{t("admin.users.status")}</TableHead><TableHead>{t("admin.users.lastLogin")}</TableHead><TableHead />
                </TableRow></TableHeader>
                <TableBody>
                  {visible.map((u) => {
                    const self = u.id === me?.user.id;
                    return (
                      <TableRow key={u.id} className={cn(!u.is_active && "opacity-60")}>
                        <TableCell>
                          <div className="font-medium">{u.display_name} {self && <span className="text-xs text-muted-foreground">({t("admin.users.you")})</span>}</div>
                          <div className="text-xs text-muted-foreground">{u.email}</div>
                        </TableCell>
                        <TableCell>
                          {self ? <RoleBadge role={u.role} /> : (
                            <Select value={u.role} onValueChange={(v) => patch.mutate({ id: u.id, body: { role: v } })}>
                              <SelectTrigger className="h-8 w-36" aria-label={`${t("admin.users.role")} ${u.email}`}><SelectValue /></SelectTrigger>
                              <SelectContent>{ROLES.map((r) => <SelectItem key={r} value={r}>{t(`auth.role.${r}` as TKey)}</SelectItem>)}</SelectContent>
                            </Select>
                          )}
                        </TableCell>
                        <TableCell className="space-x-1">
                          <Badge variant={u.is_active ? "success" : "secondary"}>{u.is_active ? t("admin.users.active") : t("admin.users.inactive")}</Badge>
                          {u.locked && <Badge variant="danger"><Lock className="h-3 w-3" />{t("admin.users.locked")}</Badge>}
                          {u.must_change_password && <Badge variant="warning">{t("admin.users.pwPending")}</Badge>}
                        </TableCell>
                        <TableCell className="tabular text-xs">{fmtDateTime(u.last_login_at, lang)}</TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          {!self && (
                            <>
                              {u.locked && <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { unlock: true } })}><Unlock /> {t("admin.users.unlock")}</Button>}
                              <ResetPasswordDialog user={u} />
                              <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                                {u.is_active ? t("admin.users.deactivate") : t("admin.users.activate")}
                              </Button>
                            </>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>{t("admin.roles.title")}</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm">
          {ROLES.map((r) => (
            <div key={r} className="rounded-md border p-3">
              <RoleBadge role={r} />
              <p className="mt-1.5 text-slate-600">{t(`admin.roles.${r}` as TKey)}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------------------- backups

function BackupsTab() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const { logout } = useAuth();
  const [note, setNote] = useState("");
  const list = useQuery({ queryKey: ["backups"], queryFn: () => api<BackupManifest[]>("/api/admin/backups") });
  const create = useMutation({
    mutationFn: () => postJson<BackupManifest>("/api/admin/backups", { note: note || null }),
    onSuccess: () => { toast.success(t("admin.backups.created")); setNote(""); qc.invalidateQueries({ queryKey: ["backups"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Archive className="h-4 w-4 text-blue-600" />{t("admin.tab.backups")}</CardTitle>
        <CardDescription>{t("admin.backups.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-72 space-y-1"><Label htmlFor="b-note">{t("admin.backups.note")}</Label>
            <Input id="b-note" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} /></div>
          <Button onClick={() => create.mutate()} disabled={create.isPending} data-testid="create-backup">
            {create.isPending ? <><Loader2 className="animate-spin" /> {t("admin.backups.creating")}</> : <><Database /> {t("admin.backups.create")}</>}
          </Button>
        </div>
        {list.isLoading ? <LoadingState /> : list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> :
         !list.data!.length ? <EmptyState title={t("admin.backups.empty")} hint="" icon={Archive} /> : (
          <div className="overflow-auto rounded-md border">
            <Table data-testid="backup-table">
              <TableHeader><TableRow>
                <TableHead>{t("admin.backups.created_at")}</TableHead><TableHead>{t("admin.backups.kind")}</TableHead>
                <TableHead>{t("admin.backups.by")}</TableHead><TableHead className="text-right">{t("admin.backups.size")}</TableHead>
                <TableHead>Schema</TableHead><TableHead />
              </TableRow></TableHeader>
              <TableBody>
                {list.data!.map((b) => (
                  <TableRow key={b.name}>
                    <TableCell><div className="tabular font-medium">{fmtDateTime(b.created_at, lang)}</div>
                      <div className="font-mono text-[10px] text-muted-foreground">{b.name}</div>
                      {b.note && <div className="text-xs text-slate-600">{b.note}</div>}</TableCell>
                    <TableCell><Badge variant={b.kind === "pre_restore" ? "warning" : "secondary"}>{t(`admin.backups.kind.${b.kind}` as TKey)}</Badge></TableCell>
                    <TableCell className="text-xs">{b.created_by ?? "—"}</TableCell>
                    <TableCell className="tabular whitespace-nowrap text-right text-xs">{fmtBytes(b.total_size)}<div className="text-muted-foreground">{b.files.count} files</div></TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{b.alembic_revision} · v{b.app_version}</TableCell>
                    <TableCell className="whitespace-nowrap text-right">
                      <Button size="icon" variant="ghost" title={t("common.download")} aria-label={t("common.download")}
                        onClick={() => downloadFile(`/api/admin/backups/${b.name}/download`, `${b.name}.tar`).catch((e) => toast.error(e.message))}><Download /></Button>
                      <ConfirmDialog title={t("admin.backups.restoreTitle")} confirmLabel={t("admin.backups.restore")} destructive requirePassword requireText="RESTORE"
                        description={t("admin.backups.restoreDesc", { date: fmtDateTime(b.created_at, lang) })}
                        onConfirm={async (password) => {
                          await postJson(`/api/admin/backups/${b.name}/restore`, { password, confirm: "RESTORE" });
                          toast.success(t("admin.backups.restored"));
                          await logout().catch(() => undefined);
                        }}
                        trigger={(open) => <Button size="sm" variant="outline" onClick={open} data-testid="restore-backup"><RotateCcw /> {t("admin.backups.restore")}</Button>} />
                      <ConfirmDialog title={t("common.delete")} confirmLabel={t("common.delete")} destructive requirePassword requireText="DELETE"
                        description={t("admin.backups.deleteDesc")}
                        onConfirm={async (password) => { await postJson(`/api/admin/backups/${b.name}/delete`, { password, confirm: "DELETE" }); qc.invalidateQueries({ queryKey: ["backups"] }); }}
                        trigger={(open) => <Button size="icon" variant="ghost" onClick={open} aria-label={t("common.delete")}><Trash2 className="text-red-600" /></Button>} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------------- system

function SystemTab() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["system"], queryFn: () => api<SystemStatus>("/api/admin/system") });
  const icon = (s: SystemCheck["status"]) => s === "ok" ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> :
    s === "warn" ? <AlertTriangle className="h-4 w-4 text-amber-500" /> : <XCircle className="h-4 w-4 text-red-600" />;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">{t("admin.tab.system")}
            {q.data && <Badge variant={q.data.status === "ready" ? "success" : "danger"}>{q.data.status === "ready" ? t("admin.system.ready") : t("admin.system.notReady")}</Badge>}</CardTitle>
          {q.data && <CardDescription className="mt-1">{t("admin.system.version", { version: q.data.version, build: q.data.build, env: q.data.environment })}</CardDescription>}
        </div>
        <Button variant="outline" size="sm" onClick={() => q.refetch()} disabled={q.isFetching}><RefreshCw className={cn(q.isFetching && "animate-spin")} /> {t("admin.system.refresh")}</Button>
      </CardHeader>
      <CardContent>
        {q.isLoading ? <LoadingState /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
          <Table data-testid="system-checks">
            <TableHeader><TableRow><TableHead className="w-8" /><TableHead>{t("admin.system.check")}</TableHead><TableHead>{t("admin.system.detail")}</TableHead></TableRow></TableHeader>
            <TableBody>
              {q.data!.checks.map((c) => (
                <TableRow key={c.name} className={cn(c.status === "fail" && "bg-red-50/60", c.status === "warn" && "bg-amber-50/50")}>
                  <TableCell>{icon(c.status)}</TableCell>
                  <TableCell className="font-mono text-xs">{c.name}</TableCell>
                  <TableCell className="text-sm">{c.detail}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function EvidenceScannerCard() {
  const { t, lang } = useI18n();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["evidence-status"], queryFn: () => api<EvidenceStatus>("/api/admin/evidence/status") });
  const done = (key: TKey) => (data: EvidenceStatus) => {
    qc.setQueryData(["evidence-status"], data);
    qc.invalidateQueries({ queryKey: ["system"] });
    toast.success(t(key));
  };
  const check = useMutation({ mutationFn: () => postJson<EvidenceStatus>("/api/admin/evidence/scanner/check", {}),
    onSuccess: done("admin.evidence.checked"), onError: (e) => toast.error((e as Error).message) });
  const reconcile = useMutation({ mutationFn: () => postJson<EvidenceStatus>("/api/admin/evidence/reconcile", {}),
    onSuccess: done("admin.evidence.reconciled"), onError: (e) => toast.error((e as Error).message) });
  const d = q.data;
  const h = d?.health;
  const state = h?.status ?? "unknown";
  const counts = (title: TKey, rows: [string, number, boolean][]) => (
    <div>
      <div className="mb-1 text-xs font-medium text-muted-foreground">{t(title)}</div>
      <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 text-sm">
        {rows.map(([k, v, bad]) => (
          <div key={k} className="contents" data-testid={`evidence-${k.split(".").pop()}`}>
            <dt>{t(k as TKey)}</dt><dd className={cn("tabular text-right", bad && v > 0 && "font-semibold text-amber-700")}>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
  return (
    <Card data-testid="evidence-scanner-card">
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">{t("admin.evidence.title")}
            {d && d.scanner !== "none" && (
              <Badge data-testid="scanner-health" variant={state === "healthy" && !d.health_stale ? "success" : state === "unavailable" ? "danger" : "warning"}>
                {t(`admin.evidence.health.${state}` as TKey)}
              </Badge>)}
          </CardTitle>
          <CardDescription className="mt-1">{d?.scanner === "none" ? t("admin.evidence.noScanner") : t("admin.evidence.desc")}</CardDescription>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {d && d.scanner !== "none" && (
            <Button size="sm" variant="outline" data-testid="scanner-check" onClick={() => check.mutate()} disabled={check.isPending}>
              {check.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} {t("admin.evidence.check")}
            </Button>)}
          <Button size="sm" variant="outline" data-testid="evidence-reconcile" onClick={() => reconcile.mutate()} disabled={reconcile.isPending}>
            {reconcile.isPending ? <Loader2 className="animate-spin" /> : <Database />} {t("admin.evidence.reconcile")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {q.isLoading ? <LoadingState /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : d && (
          <>
            {d.scanner !== "none" && (
              <div className="space-y-1 text-sm">
                <div className="font-mono text-xs">{d.scanner_label}</div>
                {h?.checked_at && <div className="text-muted-foreground">{t("admin.evidence.checkedAt", { time: fmtDateTime(h.checked_at, lang) })}{h.status === "healthy" && h.detail ? ` · ${h.detail}` : ""}</div>}
                {state === "unavailable" && h && (
                  <div className="rounded-md border border-red-200 bg-red-50/60 p-2 text-red-800" data-testid="scanner-outage">
                    <div>{t("admin.evidence.since", { time: fmtDateTime(h.unavailable_since, lang), n: h.consecutive_failures })}</div>
                    {h.last_error && <div className="mt-0.5 break-all font-mono text-xs">{h.last_error}</div>}
                  </div>)}
                {state === "healthy" && d.health_stale && <div className="text-amber-700">{t("admin.evidence.stale")}</div>}
              </div>)}
            <div className="grid gap-4 sm:grid-cols-2">
              {counts("admin.evidence.quarantine", [
                ["admin.evidence.q.pending", d.quarantine.pending, false], ["admin.evidence.q.retrying", d.quarantine.retrying, false],
                ["admin.evidence.q.retries_exhausted", d.quarantine.retries_exhausted, true],
                ["admin.evidence.q.not_scanned", d.quarantine.not_scanned, false], ["admin.evidence.q.infected", d.quarantine.infected, true]])}
              {counts("admin.evidence.storage", [
                ["admin.evidence.s.ops_pending", d.storage.ops_pending, false], ["admin.evidence.s.ops_failed", d.storage.ops_failed, true],
                ["admin.evidence.s.misplaced", d.storage.misplaced, true], ["admin.evidence.s.missing", d.storage.missing, true],
                ["admin.evidence.s.orphaned", d.storage.orphaned, false]])}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------------- data tools

function DataTab() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const toast = useToast();
  const { setFilters } = useFilters();
  const settings = useSettings();
  const enabled = settings.data?.system.demo_tools_enabled;
  const after = async () => {
    await qc.invalidateQueries();
    const meta = await qc.fetchQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta") });
    setFilters({ startDate: meta.data_range.start ?? "", endDate: meta.data_range.end ?? "", lineId: null, machineId: null, shiftId: null, productId: null });
    toast.success(t("settings.done"));
  };
  if (settings.isLoading) return <LoadingState />;
  return (
    <Card>
      <CardHeader><CardTitle>{t("admin.tab.data")}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        {!enabled && <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">{t("admin.data.disabled")}</div>}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3">
          <div className="max-w-xl"><div className="text-sm font-semibold">{t("settings.reloadDemo")}</div><div className="text-xs text-muted-foreground">{t("settings.reloadDemoDesc")}</div></div>
          <ConfirmDialog title={t("settings.reloadDemo")} description={t("settings.reloadConfirm")} confirmLabel={t("settings.reloadDemo")} requireText="RELOAD" requirePassword
            onConfirm={async (password) => { await postJson("/api/admin/demo/load", { password, confirm: "RELOAD" }); await after(); }}
            trigger={(open) => <Button variant="outline" onClick={open} disabled={!enabled} data-testid="reload-demo"><RefreshCw /> {t("settings.reloadDemo")}</Button>} />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50/40 p-3">
          <div className="max-w-xl"><div className="text-sm font-semibold text-red-800">{t("settings.deleteAll")}</div><div className="text-xs text-red-700/80">{t("settings.deleteAllDesc")}</div></div>
          <ConfirmDialog title={t("settings.deleteAll")} description={t("settings.deleteConfirm")} confirmLabel={t("common.delete")} requireText="DELETE" requirePassword destructive
            onConfirm={async (password) => { await postJson("/api/admin/data/delete", { password, confirm: "DELETE" }); await after(); }}
            trigger={(open) => <Button variant="destructive" onClick={open} disabled={!enabled}><Trash2 /> {t("settings.deleteAll")}</Button>} />
        </div>
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------------- audit

function AuditTab() {
  const { t, lang } = useI18n();
  const [filter, setFilter] = useState("");
  const q = useQuery({
    queryKey: ["audit", filter],
    queryFn: () => api<AuditRow[]>(`/api/audit-log?limit=300${filter ? `&event=${encodeURIComponent(filter)}` : ""}`),
  });
  return (
    <Card>
      <CardHeader><CardTitle>{t("admin.tab.audit")}</CardTitle><CardDescription>{t("settings.auditDesc").replace("50", "300")}</CardDescription></CardHeader>
      <CardContent>
        <Input className="mb-3 max-w-sm" placeholder={t("admin.audit.filter")} value={filter} onChange={(e) => setFilter(e.target.value.trim())} aria-label={t("admin.audit.filter")} />
        {q.isLoading ? <LoadingState /> : q.isError ? <ErrorState error={q.error} /> : (
          <div className="max-h-[560px] overflow-auto rounded-md border">
            <Table data-testid="audit-table">
              <TableHeader className="sticky top-0"><TableRow>
                <TableHead>{t("admin.audit.time")}</TableHead><TableHead>{t("admin.audit.event")}</TableHead>
                <TableHead>{t("admin.audit.user")}</TableHead><TableHead>{t("admin.audit.details")}</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {q.data!.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="tabular whitespace-nowrap text-xs">{fmtDateTime(e.created_at, lang)}</TableCell>
                    <TableCell className="font-mono text-xs">{e.event}{e.entity_id != null && <span className="text-muted-foreground"> #{e.entity_id}</span>}</TableCell>
                    <TableCell className="text-xs">{e.user_email ?? "system"}<div className="text-muted-foreground">{e.client_ip}</div></TableCell>
                    <TableCell className="max-w-md truncate font-mono text-[11px] text-slate-600" title={e.details ? JSON.stringify(e.details) : ""}>{e.details ? JSON.stringify(e.details) : ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function AdminPage() {
  const { t } = useI18n();
  return (
    <div>
      <PageHeader title={t("admin.title")} subtitle={t("admin.subtitle")} />
      <Tabs defaultValue="users">
        <TabsList className="flex-wrap">
          <TabsTrigger value="users">{t("admin.tab.users")}</TabsTrigger>
          <TabsTrigger value="backups" data-testid="tab-backups">{t("admin.tab.backups")}</TabsTrigger>
          <TabsTrigger value="system" data-testid="tab-system">{t("admin.tab.system")}</TabsTrigger>
          <TabsTrigger value="data">{t("admin.tab.data")}</TabsTrigger>
          <TabsTrigger value="audit" data-testid="tab-audit">{t("admin.tab.audit")}</TabsTrigger>
        </TabsList>
        <TabsContent value="users"><UsersTab /></TabsContent>
        <TabsContent value="backups"><BackupsTab /></TabsContent>
        <TabsContent value="system" className="space-y-4"><SystemTab /><EvidenceScannerCard /></TabsContent>
        <TabsContent value="data"><DataTab /></TabsContent>
        <TabsContent value="audit"><AuditTab /></TabsContent>
      </Tabs>
    </div>
  );
}

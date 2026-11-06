import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";
import {
  Activity, AlertTriangle, BarChart3, ClipboardList, ListChecks, BrainCircuit, FileText, Gauge, KeyRound, LogOut, Settings, ShieldCheck, Timer,
  Upload, UserCog,
} from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useToast } from "@/components/Toast";
import { useAuth, type Permission } from "@/lib/auth";
import { ChangePasswordForm } from "@/pages/Login";
import { useI18n, type Lang, type TKey } from "@/lib/i18n";
import { useMeta } from "@/lib/queries";
import { cn } from "@/lib/utils";

const NAV: { group: TKey; items: { to: string; key: TKey; icon: typeof Gauge; perm?: Permission }[] }[] = [
  {
    group: "nav.analytics",
    items: [
      { to: "/", key: "nav.overview", icon: Gauge },
      { to: "/production", key: "nav.production", icon: BarChart3 },
      { to: "/quality", key: "nav.quality", icon: ShieldCheck },
      { to: "/downtime", key: "nav.downtime", icon: Timer },
      { to: "/insights", key: "nav.insights", icon: BrainCircuit },
    ],
  },
  {
    group: "nav.operations",
    items: [
      { to: "/actions", key: "nav.actionCenter", icon: ClipboardList, perm: "action.read" },
      { to: "/actions/mine", key: "nav.myActions", icon: ListChecks, perm: "action.read" },
    ],
  },
  {
    group: "nav.data",
    items: [
      { to: "/reports", key: "nav.reports", icon: FileText },
      { to: "/import", key: "nav.import", icon: Upload, perm: "import.read" },
      { to: "/settings", key: "nav.settings", icon: Settings },
      { to: "/admin", key: "nav.admin", icon: UserCog, perm: "user.manage" },
    ],
  },
];

function UserMenu() {
  const { t } = useI18n();
  const { me, logout } = useAuth();
  const toast = useToast();
  const [pwOpen, setPwOpen] = useState(false);
  if (!me) return null;
  const initials = me.user.display_name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  return (
    <div className="mb-3">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 text-xs font-semibold text-white" aria-hidden>{initials}</div>
        <div className="hidden min-w-0 lg:block">
          <div className="truncate text-xs font-medium text-white" data-testid="user-name">{me.user.display_name}</div>
          <div className="truncate text-[10px] text-slate-400">{t(`auth.role.${me.user.role}` as TKey)} · {me.user.email}</div>
        </div>
      </div>
      <div className="mt-2 flex flex-col gap-1 lg:flex-row">
        <button onClick={() => setPwOpen(true)} title={t("auth.changePassword")}
          className="flex flex-1 items-center justify-center gap-1.5 rounded bg-white/5 px-2 py-1 text-[11px] text-slate-300 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
          <KeyRound className="h-3.5 w-3.5" /> <span className="hidden lg:inline">{t("auth.changePassword")}</span>
        </button>
        <button onClick={() => logout().catch((e) => toast.error(e.message))} title={t("auth.logout")} data-testid="logout"
          className="flex flex-1 items-center justify-center gap-1.5 rounded bg-white/5 px-2 py-1 text-[11px] text-slate-300 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
          <LogOut className="h-3.5 w-3.5" /> <span className="hidden lg:inline">{t("auth.logout")}</span>
        </button>
      </div>
      <Dialog open={pwOpen} onOpenChange={setPwOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{t("auth.changePassword")}</DialogTitle><DialogDescription>{me.user.email}</DialogDescription></DialogHeader>
          <ChangePasswordForm onDone={() => { setPwOpen(false); toast.success(t("auth.changed")); }} />
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function Layout() {
  const { t, lang, setLang } = useI18n();
  const { can } = useAuth();
  const { pathname } = useLocation();
  const meta = useMeta();
  const company = meta.data?.company;
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 z-40 flex h-screen w-16 shrink-0 flex-col bg-navy-900 text-slate-300 lg:w-64" aria-label="Main navigation">
        <div className="flex h-16 items-center gap-3 border-b border-white/10 px-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 text-white shadow">
            <Activity className="h-5 w-5" aria-hidden />
          </div>
          <div className="hidden min-w-0 lg:block">
            <div className="text-[15px] font-semibold leading-tight text-white">FactoryPulse AI</div>
            <div className="text-[11px] text-slate-400">{t("app.tagline")}</div>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-4">
          {NAV.map((g) => (
            <div key={g.group} className="mb-5">
              <div className="mb-1 hidden px-3 text-[10px] font-semibold uppercase tracking-wider text-slate-500 lg:block">{t(g.group)}</div>
              <ul className="space-y-0.5">
                {g.items.filter((i) => !i.perm || can(i.perm)).map(({ to, key, icon: Icon }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      end={to === "/"}
                      title={t(key)}
                      className={({ isActive: a }) => {
                        const isActive = a && !(to === "/actions" && pathname.startsWith("/actions/mine"));
                        return cn(
                          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400",
                          isActive ? "bg-white/10 text-white shadow-inner" : "text-slate-300 hover:bg-white/5 hover:text-white",
                        );
                      }}
                    >
                      {({ isActive: a }) => { const isActive = a && !(to === "/actions" && pathname.startsWith("/actions/mine")); return (
                        <>
                          <Icon className={cn("h-[18px] w-[18px] shrink-0", isActive ? "text-teal-300" : "text-slate-400")} aria-hidden />
                          <span className="hidden lg:inline">{t(key)}</span>
                        </>
                      ); }}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="border-t border-white/10 p-3">
          <UserMenu />
          <div className="mb-3 hidden lg:block">
            <div className="truncate text-xs font-medium text-white" title={company?.name}>{company?.name ?? "…"}</div>
            {company?.is_synthetic && (
              <div className="mt-1 inline-flex items-center gap-1 rounded bg-amber-400/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                <AlertTriangle className="h-3 w-3" aria-hidden /> {t("app.synthetic")}
              </div>
            )}
          </div>
          <div className="flex flex-col gap-1 lg:flex-row" role="group" aria-label={t("app.language")}>
            {(["de", "en"] as Lang[]).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                aria-pressed={lang === l}
                className={cn(
                  "flex-1 rounded px-2 py-1 text-xs font-semibold uppercase transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400",
                  lang === l ? "bg-teal-500 text-white" : "bg-white/5 text-slate-400 hover:text-white",
                )}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        {company?.is_synthetic && (
          <div className="border-b border-amber-200 bg-amber-50 px-6 py-1.5 text-center text-xs font-medium text-amber-800" role="note">
            {t("app.syntheticBanner")}
          </div>
        )}
        {meta.isError && (
          <div className="border-b border-red-200 bg-red-50 px-6 py-2 text-center text-sm text-red-700" role="alert">
            {t("app.backendDown")} ({(meta.error as Error).message})
          </div>
        )}
        <div className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 md:px-6 lg:px-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

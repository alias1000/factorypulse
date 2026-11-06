import type { ReactNode } from "react";
import { ShieldOff } from "lucide-react";
import { useAuth, type Permission } from "@/lib/auth";
import { useI18n, type TKey } from "@/lib/i18n";
import { ForcedPasswordChange, LoginPage } from "@/pages/Login";
import { LoadingState } from "./States";

export function AuthGate({ children }: { children: ReactNode }) {
  const { status, me } = useAuth();
  if (status === "loading") return <LoadingState className="min-h-screen" />;
  if (status === "anonymous" || !me) return <LoginPage />;
  if (me.user.must_change_password) return <ForcedPasswordChange />;
  return <>{children}</>;
}

/** Route guard: renders a clear "no access" page instead of a broken view. The API enforces the same rule. */
export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const { can, me } = useAuth();
  const { t } = useI18n();
  if (can(permission)) return <>{children}</>;
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-2 text-center" data-testid="no-access">
      <ShieldOff className="h-10 w-10 text-slate-300" />
      <h1 className="text-xl font-semibold">{t("auth.noAccess")}</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        {t("auth.noAccessDesc", { role: t(`auth.role.${me?.user.role ?? "viewer"}` as TKey) })}
      </p>
    </div>
  );
}

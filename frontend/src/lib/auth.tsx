import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, UNAUTHORIZED_EVENT, api, postJson, setCsrfToken } from "./api";

export type Role = "viewer" | "analyst" | "admin";
export type Permission =
  | "data.read" | "anomaly.run" | "report.read" | "settings.read" | "report.generate" | "report.delete"
  | "import.read" | "import.write" | "import.revert" | "settings.write" | "audit.read" | "user.manage"
  | "backup.manage" | "system.read" | "data.destroy" | "action.read" | "action.write" | "action.manage";

export interface AuthUser {
  id: number; email: string; display_name: string; role: Role; is_active: boolean;
  must_change_password: boolean; locked: boolean; last_login_at: string | null; created_at: string | null;
}
export interface MeResponse {
  user: AuthUser; permissions: Permission[]; csrf_token: string; session_expires_at: string; idle_timeout_minutes: number;
}

type Status = "loading" | "anonymous" | "authenticated";

interface AuthCtx {
  status: Status;
  me: MeResponse | null;
  sessionExpired: boolean;
  can: (p: Permission) => boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<MeResponse | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);

  const apply = useCallback((m: MeResponse | null) => {
    setMe(m);
    setCsrfToken(m?.csrf_token ?? null);
    setStatus(m ? "authenticated" : "anonymous");
  }, []);

  useEffect(() => {
    api<MeResponse>("/api/auth/me").then(apply).catch(() => apply(null));
  }, [apply]);

  useEffect(() => {
    const onUnauthorized = () => {
      setStatus((s) => {
        if (s === "authenticated") setSessionExpired(true);
        return "anonymous";
      });
      setMe(null);
      setCsrfToken(null);
      qc.clear();
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  const value = useMemo<AuthCtx>(() => ({
    status,
    me,
    sessionExpired,
    can: (p) => !!me && !me.user.must_change_password && me.permissions.includes(p),
    login: async (email, password) => {
      const m = await postJson<MeResponse>("/api/auth/login", { email, password });
      qc.clear();
      setSessionExpired(false);
      apply(m);
    },
    logout: async () => {
      try {
        await postJson("/api/auth/logout", {});
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 401)) throw e;
      }
      qc.clear();
      apply(null);
    },
    changePassword: async (current, next) => {
      const m = await postJson<MeResponse>("/api/auth/change-password", { current_password: current, new_password: next });
      apply(m);
      qc.invalidateQueries();
    },
  }), [status, me, sessionExpired, apply, qc]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}

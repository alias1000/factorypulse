import { useState, type FormEvent } from "react";
import { Activity, AlertCircle, Factory, Gauge, KeyRound, Loader2, LogIn, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { useI18n, type Lang } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function LangSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <div className="flex gap-1" role="group" aria-label="Language">
      {(["de", "en"] as Lang[]).map((l) => (
        <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
          className={cn("rounded px-2 py-1 text-xs font-semibold uppercase", lang === l ? "bg-slate-900 text-white" : "text-slate-500 hover:text-slate-900")}>
          {l}
        </button>
      ))}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_1.1fr]">
      <div className="relative hidden overflow-hidden bg-navy-900 p-10 text-white lg:flex lg:flex-col">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 shadow">
            <Activity className="h-5 w-5" aria-hidden />
          </div>
          <div className="text-lg font-semibold">FactoryPulse AI</div>
        </div>
        <div className="mt-auto max-w-md">
          <h2 className="text-3xl font-semibold leading-tight">{t("auth.subtitle")}</h2>
          <ul className="mt-6 space-y-3 text-sm text-slate-300">
            <li className="flex items-center gap-3"><Gauge className="h-4 w-4 text-teal-300" /> OEE · {t("kpi.availability")} · {t("kpi.performance")} · {t("kpi.quality")}</li>
            <li className="flex items-center gap-3"><Factory className="h-4 w-4 text-teal-300" /> {t("nav.quality")} · {t("nav.downtime")} · {t("nav.insights")}</li>
            <li className="flex items-center gap-3"><ShieldCheck className="h-4 w-4 text-teal-300" /> {t("auth.secureNote")}</li>
          </ul>
        </div>
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-teal-500/10 blur-3xl" aria-hidden />
      </div>
      <div className="flex flex-col bg-white">
        <div className="flex justify-end p-4"><LangSwitch /></div>
        <div className="flex flex-1 items-center justify-center px-6 pb-16">
          <div className="w-full max-w-sm">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { t } = useI18n();
  const { login, sessionExpired } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell>
      <div className="mb-8 lg:hidden flex items-center gap-2 font-semibold"><Activity className="h-5 w-5 text-teal-600" /> FactoryPulse AI</div>
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{t("auth.title")}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{t("auth.secureNote")}</p>
      {sessionExpired && (
        <div className="mt-5 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="status">{t("auth.sessionExpired")}</div>
      )}
      <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="email">{t("auth.email")}</Label>
          <Input id="email" type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">{t("auth.password")}</Label>
          <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </div>
        )}
        <Button type="submit" className="w-full" size="lg" disabled={busy || !email || !password}>
          {busy ? <><Loader2 className="animate-spin" /> {t("auth.signingIn")}</> : <><LogIn /> {t("auth.signIn")}</>}
        </Button>
      </form>
      <p className="mt-6 text-xs text-muted-foreground">{t("auth.forgot")}</p>
    </Shell>
  );
}

export function ChangePasswordForm({ onDone, forced }: { onDone?: () => void; forced?: boolean }) {
  const { t } = useI18n();
  const { changePassword } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = repeat.length > 0 && next !== repeat;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== repeat) return setError(t("auth.mismatch"));
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      onDone?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="cur">{t("auth.currentPassword")}</Label>
        <Input id="cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus={forced} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new">{t("auth.newPassword")}</Label>
        <Input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <p className="text-[11px] text-muted-foreground">{t("auth.passwordHint")}</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="rep">{t("auth.repeatPassword")}</Label>
        <Input id="rep" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} aria-invalid={mismatch} />
        {mismatch && <p className="text-xs text-red-600">{t("auth.mismatch")}</p>}
      </div>
      {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</div>}
      <Button type="submit" className="w-full" disabled={busy || !current || next.length < 12 || mismatch}>
        {busy ? <Loader2 className="animate-spin" /> : <KeyRound />} {t("auth.changePassword")}
      </Button>
    </form>
  );
}

export function ForcedPasswordChange() {
  const { t } = useI18n();
  const { me, logout } = useAuth();
  return (
    <Shell>
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{t("auth.mustChangeTitle")}</h1>
      <p className="mb-6 mt-1 text-sm text-muted-foreground">{t("auth.mustChangeDesc")}</p>
      <div className="mb-4 text-sm text-slate-700">{me?.user.email}</div>
      <ChangePasswordForm forced />
      <Button variant="link" className="mt-4 px-0" onClick={() => logout()}>{t("auth.logout")}</Button>
    </Shell>
  );
}

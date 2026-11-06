import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface ToastItem { id: number; kind: "success" | "error"; text: string }
interface Ctx { success: (t: string) => void; error: (t: string) => void }
const ToastCtx = createContext<Ctx | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const remove = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const push = useCallback((kind: ToastItem["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, kind, text }]);
    setTimeout(() => remove(id), kind === "error" ? 8000 : 4500);
  }, [remove]);
  const value = useMemo(() => ({ success: (t: string) => push("success", t), error: (t: string) => push("error", t) }), [push]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite">
        {items.map((it) => (
          <div key={it.id} role={it.kind === "error" ? "alert" : "status"}
            className={cn("flex items-start gap-2 rounded-lg border bg-white p-3 text-sm shadow-lg",
              it.kind === "error" ? "border-red-200" : "border-emerald-200")}>
            {it.kind === "error" ? <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />}
            <div className="flex-1 break-words">{it.text}</div>
            <button onClick={() => remove(it.id)} aria-label="Close" className="text-slate-400 hover:text-slate-700"><X className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  const c = useContext(ToastCtx);
  if (!c) throw new Error("useToast outside provider");
  return c;
}

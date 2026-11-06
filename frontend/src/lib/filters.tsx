import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useMeta } from "./queries";

export interface Filters {
  startDate: string;
  endDate: string;
  lineId: number | null;
  machineId: number | null;
  shiftId: number | null;
  productId: number | null;
}

/** Default view: the last 30 days of available data (so period comparisons are meaningful). */
export function defaultRange(start: string | null | undefined, end: string | null | undefined): { startDate: string; endDate: string } {
  if (!end) return { startDate: "", endDate: "" };
  const d = new Date(`${end}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 29);
  const s = d.toISOString().slice(0, 10);
  return { startDate: start && start > s ? start : s, endDate: end };
}

const EMPTY: Filters = { startDate: "", endDate: "", lineId: null, machineId: null, shiftId: null, productId: null };

interface Ctx {
  filters: Filters;
  setFilters: (f: Partial<Filters>) => void;
  reset: () => void;
  ready: boolean;
}

const FilterContext = createContext<Ctx | null>(null);

export function FilterProvider({ children }: { children: ReactNode }) {
  const meta = useMeta();
  const [filters, set] = useState<Filters>(EMPTY);
  const [initialised, setInitialised] = useState(false);
  const range = meta.data?.data_range;

  // Default to the full available data range once meta data is known.
  useEffect(() => {
    if (!initialised && range) {
      set((f) => ({ ...f, ...defaultRange(range.start, range.end) }));
      setInitialised(true);
    }
  }, [range, initialised]);

  const value = useMemo<Ctx>(
    () => ({
      filters,
      ready: initialised || meta.isError,
      setFilters: (patch) =>
        set((f) => {
          const next = { ...f, ...patch };
          // keep machine consistent with the selected line
          if (patch.lineId !== undefined && next.machineId && meta.data) {
            const m = meta.data.machines.find((x) => x.id === next.machineId);
            if (m && next.lineId && m.line_id !== next.lineId) next.machineId = null;
          }
          return next;
        }),
      reset: () => set({ ...EMPTY, ...defaultRange(range?.start, range?.end) }),
    }),
    [filters, initialised, meta.isError, meta.data, range],
  );
  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>;
}

export function useFilters() {
  const ctx = useContext(FilterContext);
  if (!ctx) throw new Error("useFilters must be used inside FilterProvider");
  return ctx;
}

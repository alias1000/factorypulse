import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, filterParams } from "./api";
import type { Filters } from "./filters";
import type {
  AggRow, Comparison, DowntimeEventRow, DowntimeSummary, ImportJob, Meta, Overview, Paged,
  QualitySummary, ReportJob, SettingsPayload,
} from "./types";

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta"), staleTime: 60_000 });

const fk = (f: Filters) => [f.startDate, f.endDate, f.lineId, f.machineId, f.shiftId, f.productId];

export const useOverview = (f: Filters, enabled = true) =>
  useQuery({
    queryKey: ["overview", ...fk(f)], queryFn: () => api<Overview>(`/api/overview${filterParams(f)}`),
    enabled, placeholderData: keepPreviousData,
  });

export const useBreakdown = (f: Filters, dimension: string, enabled = true) =>
  useQuery({
    queryKey: ["breakdown", dimension, ...fk(f)],
    queryFn: () => api<{ dimension: string; rows: AggRow[] }>(`/api/production/breakdown${filterParams(f, { dimension })}`),
    enabled, placeholderData: keepPreviousData,
  });

export const useComparison = (f: Filters, enabled = true) =>
  useQuery({
    queryKey: ["comparison", ...fk(f)], queryFn: () => api<Comparison>(`/api/production/comparison${filterParams(f)}`),
    enabled, placeholderData: keepPreviousData,
  });

export const useQuality = (f: Filters, enabled = true) =>
  useQuery({
    queryKey: ["quality", ...fk(f)], queryFn: () => api<QualitySummary>(`/api/quality${filterParams(f)}`),
    enabled, placeholderData: keepPreviousData,
  });

export const useDowntime = (f: Filters, enabled = true) =>
  useQuery({
    queryKey: ["downtime", ...fk(f)], queryFn: () => api<DowntimeSummary>(`/api/downtime${filterParams(f)}`),
    enabled, placeholderData: keepPreviousData,
  });

export const useDowntimeEvents = (
  f: Filters, opts: { search: string; sort: string; order: string; page: number; pageSize: number }, enabled = true,
) =>
  useQuery({
    queryKey: ["downtime-events", ...fk(f), opts],
    queryFn: () => api<Paged<DowntimeEventRow>>(`/api/downtime/events${filterParams(f, {
      search: opts.search || undefined, sort: opts.sort, order: opts.order, page: opts.page, page_size: opts.pageSize,
    })}`),
    enabled, placeholderData: keepPreviousData,
  });

export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsPayload>("/api/settings") });
export const useReports = () => useQuery({ queryKey: ["reports"], queryFn: () => api<ReportJob[]>("/api/reports") });
export const useImports = () => useQuery({ queryKey: ["imports"], queryFn: () => api<ImportJob[]>("/api/imports") });

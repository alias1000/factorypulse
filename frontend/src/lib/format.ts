import type { Lang } from "./i18n";

const locale = (lang: Lang) => (lang === "de" ? "de-DE" : "en-GB");

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function fmtNumber(v: number | null | undefined, lang: Lang, digits = 0): string {
  if (!isNum(v)) return "N/A";
  return new Intl.NumberFormat(locale(lang), { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
}

/** Ratio (0.853) → "85.3 %" */
export function fmtPct(v: number | null | undefined, lang: Lang, digits = 1): string {
  if (!isNum(v)) return "N/A";
  return `${fmtNumber(v * 100, lang, digits)} %`;
}

export function fmtCompact(v: number | null | undefined, lang: Lang): string {
  if (!isNum(v)) return "N/A";
  return new Intl.NumberFormat(locale(lang), { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

export function fmtDate(iso: string | null | undefined, lang: Lang, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "–";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale(lang), opts ?? { day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
}

export const fmtShortDate = (iso: string, lang: Lang) => fmtDate(iso, lang, { day: "2-digit", month: "2-digit" });

export function fmtDateTime(iso: string | null | undefined, lang: Lang): string {
  return fmtDate(iso, lang, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtBytes(n: number | null | undefined): string {
  if (!isNum(n)) return "–";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Percentage-point delta of two ratios: 0.012 → "+1.2 pp" */
export function fmtPp(v: number | null | undefined, lang: Lang): string {
  if (!isNum(v)) return "N/A";
  return `${v > 0 ? "+" : ""}${fmtNumber(v * 100, lang, 1)} pp`;
}

export function fmtRel(v: number | null | undefined, lang: Lang): string {
  if (!isNum(v)) return "N/A";
  return `${v > 0 ? "+" : ""}${fmtNumber(v * 100, lang, 1)} %`;
}

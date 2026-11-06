import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nProvider, dictionaries, type TKey } from "@/lib/i18n";
import { CATEGORIES, EMPTY_AC_FILTERS, STATUSES, acParams } from "@/lib/actionCenter";
import { StatusBadge } from "@/components/ac";
import { alertText } from "@/pages/actions/Overview";
import type { Alert } from "@/lib/actionCenter";

const t = (lang: "en" | "de") => (k: TKey, v?: Record<string, string | number>) =>
  dictionaries[lang][k].replace(/\{(\w+)\}/g, (_, n) => String(v?.[n] ?? `{${n}}`));

describe("Action Center", () => {
  it("serialises only active filters", () => {
    expect(acParams(EMPTY_AC_FILTERS)).toBe("");
    expect(acParams({ ...EMPTY_AC_FILTERS, severity: "critical", lineId: 3 }, { page: 2, overdue: false })).toBe("?line_id=3&severity=critical&page=2");
  });
  it("has EN and DE labels for every status and category", () => {
    for (const lang of ["en", "de"] as const) {
      for (const s of STATUSES) expect(dictionaries[lang][`ac.status.${s}` as TKey]).toBeTruthy();
      for (const c of CATEGORIES) expect(dictionaries[lang][`ac.category.${c}` as TKey]).toBeTruthy();
    }
  });
  it("renders rule alerts with their measured values", () => {
    const a: Alert = { id: 1, rule: "critical_unassigned", severity: "critical", investigation_id: 1, action_id: null, machine_id: null,
      details: { number: "INV-2026-0001", age_hours: 6.2, threshold_hours: 4 }, first_detected_at: "", last_seen_at: "", acknowledged_at: null };
    expect(alertText(a, t("en"), "en")).toBe("INV-2026-0001: critical investigation without owner for 6 h (rule: > 4 h)");
    expect(alertText(a, t("de"), "de")).toContain("INV-2026-0001");
    const r: Alert = { ...a, rule: "repeated_anomalies", details: { machine_code: "M-10", anomaly_days: 5, window_days: 30, threshold: 3 } };
    expect(alertText(r, t("en"), "en")).toBe("M-10: anomalies on 5 days within 30 days of data (rule: ≥ 3)");
  });
  it("shows localized status badges", () => {
    render(<I18nProvider initial="de"><StatusBadge s="pending_verification" /></I18nProvider>);
    expect(screen.getByText(dictionaries.de["ac.status.pending_verification"])).toBeInTheDocument();
  });
});

describe("v0.3.1 evidence quarantine and alert freshness", () => {
  const base = {
    id: 1, filename: "a.pdf", content_type: "application/pdf", size: 10, sha256: "x".repeat(64), description: null,
    uploaded_by: "A", uploaded_by_id: 1, created_at: "2026-10-18T10:00:00Z", scanner: "clamd@clamav:3310", scanned_at: null,
    scan_detail: null, released_at: null, release_note: null, quarantined: true, downloadable: false,
    requires_acknowledgement: false, can_release: false, retries_exhausted: false, scan_attempts: 0,
  } as const;
  it("labels every scan state in both languages", async () => {
    const { ScanBadge } = await import("@/pages/actions/detailTabs");
    for (const lang of ["en", "de"] as const) {
      for (const st of ["pending", "clean", "infected", "failed", "not_scanned"] as const) {
        const { unmount } = render(<I18nProvider initial={lang}><ScanBadge a={{ ...base, scan_status: st }} /></I18nProvider>);
        expect(screen.getByText(dictionaries[lang][`ac.evidence.scan.${st}` as TKey])).toBeInTheDocument();
        unmount();
      }
    }
  });
  it("shows an admin release as released (but never for infected files)", async () => {
    const { ScanBadge } = await import("@/pages/actions/detailTabs");
    const { unmount } = render(<I18nProvider initial="en"><ScanBadge a={{ ...base, scan_status: "not_scanned", released_at: "2026-10-18T11:00:00Z" }} /></I18nProvider>);
    expect(screen.getByText("Released by admin")).toBeInTheDocument();
    unmount();
    render(<I18nProvider initial="en"><ScanBadge a={{ ...base, scan_status: "infected", released_at: "2026-10-18T11:00:00Z" }} /></I18nProvider>);
    expect(screen.getByText("Malware detected")).toBeInTheDocument();
  });
  it("v0.3.3: has texts for scanner health, quarantine and storage states", () => {
    for (const lang of ["en", "de"] as const) {
      for (const k of ["healthy", "unavailable", "unknown"]) expect(dictionaries[lang][`admin.evidence.health.${k}` as TKey]).toBeTruthy();
      for (const k of ["pending", "retrying", "retries_exhausted", "not_scanned", "infected"]) expect(dictionaries[lang][`admin.evidence.q.${k}` as TKey]).toBeTruthy();
      for (const k of ["ops_pending", "ops_failed", "misplaced", "missing", "orphaned"]) expect(dictionaries[lang][`admin.evidence.s.${k}` as TKey]).toBeTruthy();
      expect(dictionaries[lang]["ac.evidence.note.exhausted"]).toContain("{n}");
      expect(dictionaries[lang]["admin.evidence.since"]).toContain("{n}");
    }
  });
  it("has texts for every alert-evaluation state", () => {
    for (const lang of ["en", "de"] as const) {
      for (const k of ["ac.alerts.never", "ac.alerts.stale", "ac.alerts.failed", "ac.alerts.evaluateNow"]) {
        expect(dictionaries[lang][k as TKey]).toBeTruthy();
      }
      expect(dictionaries[lang]["ac.alertsDesc"]).toContain("{minutes}");
    }
  });
});

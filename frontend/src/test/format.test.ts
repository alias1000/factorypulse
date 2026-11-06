import { describe, expect, it } from "vitest";
import { fmtNumber, fmtPct, fmtPp, fmtRel } from "@/lib/format";
import { filterParams, filterBody } from "@/lib/api";
import { defaultRange } from "@/lib/filters";
import { dictionaries } from "@/lib/i18n";

describe("formatting", () => {
  it("formats ratios as percentages per locale", () => {
    expect(fmtPct(0.8531, "en")).toBe("85.3 %");
    expect(fmtPct(0.8531, "de")).toBe("85,3 %");
  });
  it("shows N/A for missing values instead of zero", () => {
    expect(fmtPct(null, "en")).toBe("N/A");
    expect(fmtNumber(undefined, "de")).toBe("N/A");
    expect(fmtPct(Number.NaN, "en")).toBe("N/A");
  });
  it("uses German thousands separators", () => {
    expect(fmtNumber(1234567, "de")).toBe("1.234.567");
    expect(fmtNumber(1234567, "en")).toBe("1,234,567");
  });
  it("formats deltas", () => {
    expect(fmtPp(0.012, "en")).toBe("+1.2 pp");
    expect(fmtRel(-0.05, "de")).toBe("-5,0 %");
  });
});

describe("filters", () => {
  const f = { startDate: "2026-09-01", endDate: "2026-09-30", lineId: 2, machineId: null, shiftId: 3, productId: null };
  it("serialises query parameters", () => {
    expect(filterParams(f, { dimension: "machine" })).toBe("?start_date=2026-09-01&end_date=2026-09-30&line_id=2&shift_id=3&dimension=machine");
    expect(filterBody(f)).toMatchObject({ line_ids: [2], machine_ids: [], shift_ids: [3] });
  });
  it("defaults to the last 30 days of data", () => {
    expect(defaultRange("2026-07-03", "2026-09-30")).toEqual({ startDate: "2026-09-01", endDate: "2026-09-30" });
    expect(defaultRange("2026-09-20", "2026-09-30")).toEqual({ startDate: "2026-09-20", endDate: "2026-09-30" });
    expect(defaultRange(null, null)).toEqual({ startDate: "", endDate: "" });
  });
});

describe("i18n", () => {
  it("German dictionary covers every English key with non-empty text", () => {
    const en = Object.keys(dictionaries.en);
    for (const k of en) expect((dictionaries.de as Record<string, string>)[k], k).toBeTruthy();
  });
});

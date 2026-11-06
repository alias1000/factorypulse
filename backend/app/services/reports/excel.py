"""Excel report rendering with openpyxl (native Excel charts, formula-injection safe)."""
from __future__ import annotations

import io

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..sanitize import sanitize_cell
from .builder import RATIO_KEYS, ReportData, fmt_date, period_text
from .i18n import t, tr_value

NAVY = "0F2744"
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
HEADER_FONT = Font(bold=True, color="FFFFFF")
PCT = "0.0%"
INT = "#,##0"


def _put(ws, row, col, value, fmt=None, bold=False):
    if isinstance(value, float) and value != value:
        value = None
    c = ws.cell(row=row, column=col, value=sanitize_cell(value))
    if fmt:
        c.number_format = fmt
    if bold:
        c.font = Font(bold=True)
    return c


def _header(ws, row, headers):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=i, value=sanitize_cell(h))
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")


def _autowidth(ws, min_w=10, max_w=48):
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[letter].width = max(min_w, min(max_w, width + 2))


def _df_sheet(wb, title, df: pd.DataFrame, columns: list[tuple[str, str, str | None]]):
    ws = wb.create_sheet(title[:31])
    _header(ws, 1, [c[1] for c in columns])
    for r_i, rec in enumerate(df.to_dict(orient="records"), start=2):
        for c_i, (key, _label, fmt) in enumerate(columns, start=1):
            v = rec.get(key)
            if hasattr(v, "date") and not isinstance(v, str):
                v = v.date()
            _put(ws, r_i, c_i, v, fmt)
    ws.freeze_panes = "A2"
    _autowidth(ws)
    return ws


PERF_COLS = [("actual_qty", "actual", INT), ("planned_qty", "planned", INT),
             ("target_achievement", "target_achievement", PCT), ("good_qty", "good_units", INT),
             ("defective_qty", "defective_units", INT), ("defect_rate", "defect_rate", PCT),
             ("unplanned_downtime_minutes", "unplanned_downtime_minutes", INT),
             ("planned_production_minutes", "planned_production_minutes", INT),
             ("operating_minutes", "operating_minutes", INT), ("availability", "availability", PCT),
             ("performance", "performance", PCT), ("quality", "quality", PCT), ("oee", "oee", PCT)]


def render_xlsx(r: ReportData) -> bytes:
    lang = r.lang
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary" if lang == "en" else "Übersicht"
    ws["A1"] = "FactoryPulse AI"
    ws["A1"].font = Font(bold=True, size=10, color="0D9488")
    _put(ws, 2, 1, r.title).font = Font(bold=True, size=16, color=NAVY)
    _put(ws, 3, 1, r.company_name).font = Font(size=12)
    row = 5
    for label, value in [(t(lang, "period"), period_text(r)),
                         (t(lang, "generated"), r.generated_at.strftime("%Y-%m-%d %H:%M %Z"))] + \
            [(f"{t(lang, 'filters')} – {k}", v) for k, v in r.filters_text]:
        _put(ws, row, 1, label, bold=True)
        _put(ws, row, 2, value)
        row += 1
    if r.is_synthetic:
        c = _put(ws, row, 1, t(lang, "synthetic"))
        c.font = Font(bold=True, color="92400E")
        c.fill = PatternFill("solid", fgColor="FEF3C7")
        row += 1
    row += 1
    _put(ws, row, 1, t(lang, "kpi_summary")).font = Font(bold=True, size=12, color=NAVY)
    row += 1
    _header(ws, row, [t(lang, "metric"), t(lang, "current"), t(lang, "previous"), t(lang, "change"), t(lang, "coverage")])
    row += 1
    cov = {k["key"]: k["coverage"] for k in r.kpis}
    for c in r.comparison:
        fmt = PCT if c["is_ratio"] else INT
        _put(ws, row, 1, c["label"])
        _put(ws, row, 2, c["current_raw"], fmt)
        _put(ws, row, 3, c["previous_raw"], fmt)
        _put(ws, row, 4, c["delta_raw"], "+0.0%;-0.0%;0.0%")
        _put(ws, row, 5, cov.get(c["key"]), "0%")
        row += 1
    note_rows = [t(lang, "na_note"), t(lang, "formulas"),
                 ("Change: percentage points for ratios, relative % for counts." if lang == "en" else
                  "Veränderung: Prozentpunkte bei Quoten, relative % bei Mengen."),
                 f"{t(lang, 'previous')}: {fmt_date(r.previous[0], lang)} – {fmt_date(r.previous[1], lang)}"]
    row += 1
    for n in note_rows:
        _put(ws, row, 1, n).font = Font(italic=True, color="64748B")
        row += 1
    ws.column_dimensions["A"].width = 38
    for col in "BCDE":
        ws.column_dimensions[col].width = 18

    if not r.has_data:
        _put(ws, row + 1, 1, t(lang, "no_data")).font = Font(bold=True)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    perf = [(k, t(lang, lbl), f) for k, lbl, f in PERF_COLS]
    # Daily sheet + charts
    daily = r.daily.copy()
    dws = _df_sheet(wb, "Daily" if lang == "en" else "Täglich", daily, [("date", t(lang, "date"), "yyyy-mm-dd")] + perf)
    n = len(daily)
    for row_i in range(2, n + 2):
        dws.cell(row_i, 1).number_format = "yyyy-mm-dd" if lang == "en" else "dd.mm.yyyy"
    if n >= 2:
        ch = BarChart()
        ch.title = t(lang, "daily_trend")
        ch.y_axis.title = t(lang, "actual")
        ch.add_data(Reference(dws, min_col=2, max_col=3, min_row=1, max_row=n + 1), titles_from_data=True)
        ch.set_categories(Reference(dws, min_col=1, min_row=2, max_row=n + 1))
        ch.height, ch.width = 8, 22
        dws.add_chart(ch, f"{get_column_letter(len(perf) + 3)}2")
        lc = LineChart()
        lc.title = "OEE / " + t(lang, "defect_rate")
        lc.add_data(Reference(dws, min_col=14, min_row=1, max_row=n + 1), titles_from_data=True)
        lc.add_data(Reference(dws, min_col=7, min_row=1, max_row=n + 1), titles_from_data=True)
        lc.set_categories(Reference(dws, min_col=1, min_row=2, max_row=n + 1))
        lc.y_axis.number_format = "0%"
        lc.height, lc.width = 8, 22
        dws.add_chart(lc, f"{get_column_letter(len(perf) + 3)}20")

    _df_sheet(wb, t(lang, "lines"), r.by_line, [("line_code", t(lang, "line"), None), ("line_name", "", None)] + perf)
    mws = _df_sheet(wb, t(lang, "machines"), r.by_machine,
                    [("machine_code", t(lang, "machine"), None), ("machine_name", "", None), ("line_code", t(lang, "line"), None)] + perf)
    nm = len(r.by_machine)
    if nm:
        bc = BarChart()
        bc.type = "bar"
        bc.title = "OEE"
        bc.add_data(Reference(mws, min_col=3 + len(perf), min_row=1, max_row=nm + 1), titles_from_data=True)
        bc.set_categories(Reference(mws, min_col=1, min_row=2, max_row=nm + 1))
        bc.x_axis.number_format = "0%"
        bc.height, bc.width = 9, 16
        mws.add_chart(bc, f"{get_column_letter(len(perf) + 5)}2")
    _df_sheet(wb, t(lang, "shifts"), r.by_shift, [("shift_code", t(lang, "shift"), None), ("shift_name", "", None)] + perf)

    for title, items, vlabel in ((f"Pareto {t(lang, 'category')}", r.defect_pareto, t(lang, "count")),
                                 (f"Pareto {t(lang, 'reason')}", r.downtime_pareto, t(lang, "minutes"))):
        if r.report_type == "quality" and items is r.downtime_pareto:
            continue
        if r.report_type == "downtime" and items is r.defect_pareto:
            continue
        pws = wb.create_sheet(title[:31])
        _header(pws, 1, [t(lang, "category"), vlabel, t(lang, "share"), t(lang, "cumulative")])
        for i, it in enumerate(items, start=2):
            _put(pws, i, 1, tr_value(it["category"], lang))
            _put(pws, i, 2, it["value"], INT)
            _put(pws, i, 3, it["share"], PCT)
            _put(pws, i, 4, it["cumulative_share"], PCT)
        _autowidth(pws)
        if len(items) >= 1:
            bar = BarChart()
            bar.title = title
            bar.add_data(Reference(pws, min_col=2, min_row=1, max_row=len(items) + 1), titles_from_data=True)
            bar.set_categories(Reference(pws, min_col=1, min_row=2, max_row=len(items) + 1))
            line = LineChart()
            line.add_data(Reference(pws, min_col=4, min_row=1, max_row=len(items) + 1), titles_from_data=True)
            line.y_axis.axId = 200
            line.y_axis.number_format = "0%"
            line.y_axis.crosses = "max"
            bar += line
            bar.height, bar.width = 9, 18
            pws.add_chart(bar, "F2")

    if r.report_type == "downtime" and not r.downtime_machines.empty:
        _df_sheet(wb, t(lang, "downtime_by_machine")[:31], r.downtime_machines,
                  [("machine_code", t(lang, "machine"), None), ("line_code", t(lang, "line"), None),
                   ("unplanned_downtime_minutes", t(lang, "minutes"), INT), ("downtime_ratio", t(lang, "downtime_ratio"), PCT),
                   ("availability", t(lang, "availability"), PCT), ("is_high_downtime", t(lang, "high_downtime"), None)])

    aws = wb.create_sheet("Anomalies" if lang == "en" else "Auffälligkeiten")
    _header(aws, 1, [t(lang, "date"), t(lang, "entity"), t(lang, "metric"), t(lang, "observed"), t(lang, "baseline"),
                     "z", t(lang, "severity"), t(lang, "explanation")])
    if r.anomaly_status != "ok":
        _put(aws, 2, 1, r.extra.get("anomaly_message"))
    elif not r.anomalies:
        _put(aws, 2, 1, t(lang, "no_anomalies"))
    for i, a in enumerate(r.anomalies, start=2):
        for j, (v, f) in enumerate([(a["date"], None), (a["entity_code"], None), (t(lang, a["metric"]), None),
                                    (a["observed"], PCT), (a["baseline"], PCT), (round(a["z_score"], 2), "0.0"),
                                    (t(lang, a["severity"]), None),
                                    (a.get("explanation_de", a["explanation"]) if lang == "de" else a["explanation"], None)], start=1):
            _put(aws, i, j, v, f)
    _put(aws, len(r.anomalies) + 4, 1, t(lang, "anomaly_note")).font = Font(italic=True, color="64748B")
    _autowidth(aws, max_w=60)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = ["render_xlsx", "RATIO_KEYS"]

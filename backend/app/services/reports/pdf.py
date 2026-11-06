"""PDF rendering with WeasyPrint (HTML/CSS → PDF). Charts are inline SVG."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import charts
from .builder import ReportData, fmt_date, period_text
from .i18n import fmt_num, fmt_pct, t, tr_value

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))


def _d(x, lang):
    return fmt_date(x.date() if hasattr(x, "date") else x, lang)


def _short(x) -> str:
    return x.strftime("%d.%m.")


def _table(headers, rows, numeric_from=1):
    return {"headers": headers, "rows": rows, "numeric_from": numeric_from}


def _perf_table(df: pd.DataFrame, label_cols: list[str], header0: str, lang: str, limit: int | None = None):
    if df.empty:
        return None
    rows = []
    for r in df.head(limit or len(df)).to_dict(orient="records"):
        rows.append([" · ".join(str(r[c]) for c in label_cols if r.get(c) is not None)] + [
            fmt_num(r["actual_qty"], lang), fmt_pct(r["target_achievement"], lang), fmt_pct(r["defect_rate"], lang),
            fmt_num(r["unplanned_downtime_minutes"], lang), fmt_pct(r["availability"], lang),
            fmt_pct(r["performance"], lang), fmt_pct(r["oee"], lang)])
    return _table([header0, t(lang, "actual"), t(lang, "target_achievement"), t(lang, "defect_rate"),
                   t(lang, "unplanned_downtime_minutes"), t(lang, "availability"), t(lang, "performance"),
                   t(lang, "oee")], rows)


def _pareto_section(items, title, value_label, lang, decimals=0):
    if not items:
        return None
    items = [{**i, "category": tr_value(i["category"], lang)} for i in items]
    top = items[:8]
    svg = charts.pareto([i["category"] for i in top], [i["value"] for i in top],
                        [i["cumulative_share"] for i in top], value_label, lang)
    tbl = _table([t(lang, "category"), value_label, t(lang, "share"), t(lang, "cumulative")],
                 [[i["category"], fmt_num(i["value"], lang, decimals), fmt_pct(i["share"], lang),
                   fmt_pct(i["cumulative_share"], lang)] for i in items])
    return {"title": title, "chart": Markup(svg), "table": tbl}


def sections(r: ReportData) -> list[dict]:
    lang = r.lang
    out: list[dict] = []
    if not r.has_data:
        return out
    d = r.daily
    dates = [_short(x) for x in d["date"]]
    if r.report_type in ("weekly", "monthly"):
        out.append({"title": t(lang, "daily_trend"), "chart": Markup(charts.line_actual_vs_planned(
            dates, d["actual_qty"].fillna(0).tolist(), d["planned_qty"].tolist(),
            [t(lang, "actual"), t(lang, "planned")], lang))})
        if d["oee"].notna().any():
            out.append({"title": t(lang, "oee_trend"), "chart": Markup(charts.line_ratio(dates, d["oee"].tolist(), "OEE", charts.TEAL))})
    if r.report_type in ("daily", "weekly", "monthly"):
        out.append({"title": t(lang, "by_line"), "table": _perf_table(r.by_line, ["line_code", "line_name"], t(lang, "line"), lang)})
        out.append({"title": t(lang, "by_shift"), "table": _perf_table(r.by_shift, ["shift_code", "shift_name"], t(lang, "shift"), lang)})
        out.append({"title": t(lang, "by_machine"), "table": _perf_table(
            r.by_machine.sort_values("oee", na_position="last"), ["machine_code", "line_code"], t(lang, "machine"), lang)})
        p = _pareto_section(r.downtime_pareto, t(lang, "downtime_pareto"), t(lang, "minutes"), lang)
        if p:
            out.append(p)
        p = _pareto_section(r.defect_pareto, t(lang, "defect_pareto"), t(lang, "count"), lang)
        if p:
            out.append(p)
    if r.report_type == "quality":
        if len(d) > 1:
            out.append({"title": t(lang, "defect_trend"), "chart": Markup(charts.line_ratio(dates, d["defect_rate"].tolist(), "", charts.RED))})
        p = _pareto_section(r.defect_pareto, t(lang, "defect_pareto"), t(lang, "count"), lang)
        if p:
            out.append(p)
        for title, df, cols, h in ((t(lang, "by_machine"), r.by_machine, ["machine_code", "line_code"], t(lang, "machine")),
                                   (t(lang, "by_product"), r.by_product, ["product_code", "product_name"], t(lang, "product"))):
            df = df.sort_values("defect_rate", ascending=False, na_position="last")
            out.append({"title": title, "table": _table(
                [h, t(lang, "actual"), t(lang, "good_units"), t(lang, "defective_units"), t(lang, "defect_rate"), t(lang, "quality")],
                [[" · ".join(str(x[c]) for c in cols), fmt_num(x["actual_qty"], lang), fmt_num(x["good_qty"], lang),
                  fmt_num(x["defective_qty"], lang), fmt_pct(x["defect_rate"], lang), fmt_pct(x["quality"], lang)]
                 for x in df.to_dict(orient="records")])})
    if r.report_type == "downtime":
        if len(d) > 1:
            out.append({"title": t(lang, "downtime_trend"), "chart": Markup(charts.bar_minutes(
                dates, d["unplanned_downtime_minutes"].fillna(0).tolist(), lang))})
        p = _pareto_section(r.downtime_pareto, t(lang, "downtime_pareto"), t(lang, "minutes"), lang)
        if p:
            out.append(p)
        m = r.downtime_machines
        if not m.empty:
            out.append({"title": t(lang, "downtime_by_machine"), "chart": Markup(charts.hbar(
                m["machine_code"].tolist(), m["unplanned_downtime_minutes"].fillna(0).tolist(), lang,
                highlight=m["is_high_downtime"].tolist(), color=charts.AMBER)),
                "table": _table([t(lang, "machine"), t(lang, "line"), t(lang, "minutes"), t(lang, "downtime_ratio"),
                                 t(lang, "availability"), ""],
                                [[x["machine_code"], x["line_code"], fmt_num(x["unplanned_downtime_minutes"], lang),
                                  fmt_pct(x["downtime_ratio"], lang), fmt_pct(x["availability"], lang),
                                  t(lang, "high_downtime") if x["is_high_downtime"] else ""]
                                 for x in m.to_dict(orient="records")])})
        out.append({"title": t(lang, "by_shift"), "table": _perf_table(r.by_shift, ["shift_code", "shift_name"], t(lang, "shift"), lang)})
    return [s for s in out if s.get("table") or s.get("chart")]


def render_pdf(r: ReportData) -> bytes:
    from weasyprint import HTML

    lang = r.lang
    anomalies = [{
        "date": fmt_date(pd.Timestamp(a["date"]).date(), lang),
        "entity": a["entity_code"], "metric": t(lang, a["metric"]),
        "observed": fmt_pct(a["observed"], lang), "baseline": fmt_pct(a["baseline"], lang),
        "severity": a["severity"], "severity_label": t(lang, a["severity"]),
        "explanation": a.get("explanation_de", a["explanation"]) if lang == "de" else a["explanation"],
    } for a in r.anomalies]
    html = _env.get_template("report.html").render(
        r=r, t=lambda k: t(lang, k), lang=lang, period=period_text(r),
        previous=f"{fmt_date(r.previous[0], lang)} – {fmt_date(r.previous[1], lang)}"
        if r.previous[0] != r.previous[1] else fmt_date(r.previous[0], lang),
        generated=r.generated_at.strftime("%d.%m.%Y %H:%M %Z" if lang == "de" else "%Y-%m-%d %H:%M %Z"),
        sections=sections(r), anomalies=anomalies,
    )
    return HTML(string=html, base_url=str(TEMPLATES)).write_pdf()

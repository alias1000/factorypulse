"""Small label dictionary + locale-aware number formatting for reports."""
from __future__ import annotations

LABELS = {
    "en": {
        "title.daily": "Daily Production Report", "title.weekly": "Weekly Production Report",
        "title.monthly": "Monthly Management Report", "title.quality": "Quality Report", "title.downtime": "Downtime Report",
        "period": "Reporting period", "filters": "Selected filters", "generated": "Generated",
        "all": "All", "lines": "Lines", "machines": "Machines", "shifts": "Shifts", "products": "Products",
        "kpi_summary": "KPI summary", "comparison": "Performance comparison", "previous": "Previous period",
        "current": "Current period", "change": "Change", "anomalies": "Key anomalies (statistical signals)",
        "no_anomalies": "No statistically significant anomalies detected in this period.",
        "anomaly_note": "Anomalies are statistical deviations from each machine's/line's own recent baseline. "
                        "They are not confirmed operational causes.",
        "by_line": "Performance by production line", "by_machine": "Performance by machine",
        "by_shift": "Performance by shift", "by_product": "Quality by product",
        "daily_trend": "Daily production: actual vs. planned", "oee_trend": "Daily OEE",
        "defect_trend": "Daily defect rate", "defect_pareto": "Defect Pareto analysis",
        "downtime_pareto": "Downtime Pareto analysis (unplanned)", "downtime_trend": "Daily unplanned downtime",
        "downtime_by_machine": "Unplanned downtime by machine", "high_downtime": "unusually high",
        "synthetic": "SYNTHETIC DEMO DATA – fictional company, not real customer results.",
        "na_note": "N/A = not computable from the available data (no values are fabricated).",
        "formulas": "OEE = Availability × Performance × Quality; Availability = operating / planned production time; "
                    "Performance = ideal cycle time × units / operating time; Quality = good / total units.",
        "metric": "Metric", "value": "Value", "coverage": "Data coverage",
        "date": "Date", "entity": "Machine/Line", "observed": "Observed", "baseline": "Baseline", "severity": "Severity",
        "category": "Category", "count": "Count", "share": "Share", "cumulative": "Cumulative", "minutes": "Minutes",
        "reason": "Reason", "line": "Line", "machine": "Machine", "shift": "Shift", "product": "Product",
        "actual": "Actual", "planned": "Planned", "no_data": "No production data for the selected period and filters.",
        "page": "Page", "of": "of", "explanation": "Explanation",
        # KPI names
        "total_production": "Total production", "planned_production": "Planned production",
        "target_achievement": "Target achievement", "good_units": "Good units", "defective_units": "Defective units",
        "defect_rate": "Defect rate", "unplanned_downtime_minutes": "Unplanned downtime (min)",
        "planned_downtime_minutes": "Planned downtime (min)", "availability": "Availability",
        "performance": "Performance", "quality": "Quality", "oee": "OEE", "operating_minutes": "Operating time (min)",
        "planned_production_minutes": "Planned production time (min)",
        "units_per_operating_hour": "Units per operating hour", "downtime_ratio": "Downtime share",
        "critical": "critical", "high": "high", "medium": "medium", "low": "low",
    },
    "de": {
        "title.daily": "Tagesproduktionsbericht", "title.weekly": "Wochenproduktionsbericht",
        "title.monthly": "Monatlicher Management-Bericht", "title.quality": "Qualitätsbericht", "title.downtime": "Stillstandsbericht",
        "period": "Berichtszeitraum", "filters": "Gewählte Filter", "generated": "Erstellt",
        "all": "Alle", "lines": "Linien", "machines": "Maschinen", "shifts": "Schichten", "products": "Produkte",
        "kpi_summary": "Kennzahlenübersicht", "comparison": "Leistungsvergleich", "previous": "Vorperiode",
        "current": "Aktuelle Periode", "change": "Veränderung", "anomalies": "Wichtige Auffälligkeiten (statistische Signale)",
        "no_anomalies": "Im Zeitraum wurden keine statistisch signifikanten Auffälligkeiten erkannt.",
        "anomaly_note": "Auffälligkeiten sind statistische Abweichungen von der jüngsten Basislinie der jeweiligen "
                        "Maschine/Linie. Sie sind keine bestätigten betrieblichen Ursachen.",
        "by_line": "Leistung nach Produktionslinie", "by_machine": "Leistung nach Maschine",
        "by_shift": "Leistung nach Schicht", "by_product": "Qualität nach Produkt",
        "daily_trend": "Tagesproduktion: Ist vs. Soll", "oee_trend": "OEE pro Tag",
        "defect_trend": "Ausschussquote pro Tag", "defect_pareto": "Pareto-Analyse der Fehlerarten",
        "downtime_pareto": "Pareto-Analyse der ungeplanten Stillstände", "downtime_trend": "Ungeplante Stillstandszeit pro Tag",
        "downtime_by_machine": "Ungeplante Stillstandszeit nach Maschine", "high_downtime": "auffällig hoch",
        "synthetic": "SYNTHETISCHE DEMODATEN – fiktives Unternehmen, keine echten Kundenergebnisse.",
        "na_note": "N/A = aus den vorhandenen Daten nicht berechenbar (es werden keine Werte erfunden).",
        "formulas": "OEE = Verfügbarkeit × Leistung × Qualität; Verfügbarkeit = Laufzeit / geplante Produktionszeit; "
                    "Leistung = ideale Taktzeit × Stückzahl / Laufzeit; Qualität = Gutteile / Gesamtmenge.",
        "metric": "Kennzahl", "value": "Wert", "coverage": "Datenabdeckung",
        "date": "Datum", "entity": "Maschine/Linie", "observed": "Beobachtet", "baseline": "Basislinie",
        "severity": "Schweregrad", "category": "Kategorie", "count": "Anzahl", "share": "Anteil",
        "cumulative": "Kumuliert", "minutes": "Minuten", "reason": "Grund", "line": "Linie", "machine": "Maschine",
        "shift": "Schicht", "product": "Produkt", "actual": "Ist", "planned": "Soll",
        "no_data": "Keine Produktionsdaten für den gewählten Zeitraum und die Filter.",
        "page": "Seite", "of": "von", "explanation": "Erläuterung",
        "total_production": "Gesamtproduktion", "planned_production": "Geplante Produktion",
        "target_achievement": "Zielerreichung", "good_units": "Gutteile", "defective_units": "Ausschuss",
        "defect_rate": "Ausschussquote", "unplanned_downtime_minutes": "Ungeplanter Stillstand (min)",
        "planned_downtime_minutes": "Geplanter Stillstand (min)", "availability": "Verfügbarkeit",
        "performance": "Leistung", "quality": "Qualität", "oee": "OEE", "operating_minutes": "Laufzeit (min)",
        "planned_production_minutes": "Geplante Produktionszeit (min)",
        "units_per_operating_hour": "Stück pro Laufstunde", "downtime_ratio": "Stillstandsanteil",
        "critical": "kritisch", "high": "hoch", "medium": "mittel", "low": "niedrig",
    },
}


def t(lang: str, key: str) -> str:
    return LABELS.get(lang, LABELS["en"]).get(key, LABELS["en"].get(key, key))


def fmt_num(v, lang: str = "en", decimals: int = 0) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "N/A"
    s = f"{v:,.{decimals}f}"
    if lang == "de":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def fmt_pct(v, lang: str = "en", decimals: int = 1) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "N/A"
    return fmt_num(v * 100, lang, decimals) + " %"


def fmt_pp(v, lang: str = "en") -> str:
    """Percentage-point delta."""
    if v is None:
        return "N/A"
    s = fmt_num(v * 100, lang, 1)
    return ("+" if v > 0 else "") + s + " pp"


# Translations for the canonical downtime reasons / defect categories used by the
# demo data and the importer defaults. Unknown (customer-specific) values are shown as-is.
DATA_VALUES_DE = {
    "Mechanical failure": "Mechanische Störung", "Electrical failure": "Elektrische Störung",
    "Tool change": "Werkzeugwechsel", "Setup / changeover": "Rüsten / Umrüsten",
    "Material shortage": "Materialmangel", "Quality inspection": "Qualitätsprüfung",
    "Staff shortage": "Personalmangel", "Planned break": "Geplante Pause",
    "Planned maintenance": "Geplante Wartung", "Planned downtime": "Geplanter Stillstand",
    "Unspecified": "Nicht angegeben", "Dimensional deviation": "Maßabweichung",
    "Surface defect": "Oberflächenfehler", "Burr": "Grat", "Material defect": "Materialfehler",
    "Other": "Sonstiges", "Assembly error": "Montagefehler", "Functional test failed": "Funktionsprüfung n.i.O.",
    "Missing component": "Fehlendes Bauteil", "Unclassified": "Nicht klassifiziert",
}


def tr_value(v: str, lang: str) -> str:
    return DATA_VALUES_DE.get(v, v) if lang == "de" else v

"""Server-side chart rendering (matplotlib → inline SVG) for PDF reports."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, PercentFormatter  # noqa: E402

NAVY = "#0f2744"
BLUE = "#2563eb"
TEAL = "#0d9488"
SLATE = "#94a3b8"
AMBER = "#d97706"
RED = "#dc2626"

plt.rcParams.update({
    "font.family": ["Inter", "DejaVu Sans", "sans-serif"],
    "font.size": 8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#cbd5e1",
    "axes.labelcolor": "#334155",
    "xtick.color": "#475569",
    "ytick.color": "#475569",
    "axes.grid": True,
    "grid.color": "#e2e8f0",
    "grid.linewidth": 0.6,
    "svg.fonttype": "none",
})


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue()
    return svg[svg.find("<svg"):]


def _thousands(lang: str):
    sep = "." if lang == "de" else ","
    return FuncFormatter(lambda x, _p: f"{x:,.0f}".replace(",", sep))


def line_actual_vs_planned(dates, actual, planned, labels, lang) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    ax.bar(dates, actual, color=BLUE, alpha=0.85, label=labels[0], width=0.8)
    ax.plot(dates, planned, color=TEAL, linewidth=1.6, marker="o", markersize=2, label=labels[1])
    ax.yaxis.set_major_formatter(_thousands(lang))
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    fig.autofmt_xdate(rotation=0, ha="center")
    _thin_xticks(ax, dates)
    return _svg(fig)


def line_ratio(dates, values, label, color=BLUE, threshold=None) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.plot(dates, values, color=color, linewidth=1.6, marker="o", markersize=2, label=label)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    _thin_xticks(ax, dates)
    return _svg(fig)


def bar_minutes(dates, values, lang) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.bar(dates, values, color=AMBER, width=0.8)
    ax.yaxis.set_major_formatter(_thousands(lang))
    _thin_xticks(ax, dates)
    return _svg(fig)


def hbar(categories, values, lang, highlight=None, pct=False, color=BLUE) -> str:
    fig, ax = plt.subplots(figsize=(7.2, max(1.4, 0.28 * len(categories) + 0.4)))
    colors = [RED if highlight and highlight[i] else color for i in range(len(categories))]
    ax.barh(categories[::-1], values[::-1], color=colors[::-1])
    ax.grid(axis="y", visible=False)
    if pct:
        ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    else:
        ax.xaxis.set_major_formatter(_thousands(lang))
    return _svg(fig)


def pareto(categories, values, cumulative, value_label, lang) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    x = range(len(categories))
    ax.bar(x, values, color=BLUE)
    ax.set_xticks(list(x))
    ax.set_xticklabels(categories, rotation=20, ha="right")
    ax.set_ylabel(value_label)
    ax.yaxis.set_major_formatter(_thousands(lang))
    ax2 = ax.twinx()
    ax2.plot(list(x), cumulative, color=TEAL, marker="o", linewidth=1.6, markersize=3)
    ax2.axhline(0.8, color=SLATE, linestyle="--", linewidth=0.8)
    ax2.set_ylim(0, 1.05)
    ax2.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    return _svg(fig)


def _thin_xticks(ax, dates):
    n = len(dates)
    if n > 14:
        step = max(1, n // 10)
        ax.set_xticks(list(dates)[::step])
    ax.tick_params(axis="x", labelsize=7)

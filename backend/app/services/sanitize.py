"""Protection against spreadsheet formula injection (CSV/Excel exports).

Any text cell starting with ``= + - @`` or a tab/CR is prefixed with a single
quote so spreadsheet software treats it as text (OWASP recommendation).
Numbers are left untouched (negative numbers are numeric, not text).
"""
from __future__ import annotations

from typing import Any

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: Any) -> Any:
    if isinstance(value, str) and value and value[0] in DANGEROUS_PREFIXES:
        return "'" + value
    return value


def sanitize_row(row: list[Any]) -> list[Any]:
    return [sanitize_cell(v) for v in row]

"""Database-independent, Unicode-correct text search for German content.

PostgreSQL's ``lower()`` depends on the database locale (with ``C`` it leaves Ä/Ö/Ü untouched) and
SQLite's ``lower()`` only folds ASCII. Instead of relying on either, each searchable row stores a
pre-normalised *search document* and queries are normalised the same way in Python:

* Unicode NFKC normalisation (macOS/iOS keyboards may send "O" + combining diaeresis instead of "Ö")
* full case folding (``casefold``: "Straße" → "strasse", "ÖLFILM" → "ölfilm")
* an additional transliterated variant (ä→ae, ö→oe, ü→ue), so "Oelfilm" also finds "Ölfilm"

The query is matched with ``LIKE`` (wildcards escaped); on PostgreSQL a ``pg_trgm`` GIN index makes
``LIKE '%term%'`` index-assisted.
"""
from __future__ import annotations

import unicodedata

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
ESCAPE = "\\"


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def document(*parts: str | None) -> str:
    base = normalize(" │ ".join(p for p in parts if p))
    alt = base.translate(_TRANSLIT)
    return base if alt == base else f"{base}\n{alt}"


def like_pattern(term: str) -> str:
    """LIKE pattern for a user search term (normalised; %, _ and \\ escaped)."""
    n = normalize(term)
    n = n.replace(ESCAPE, ESCAPE * 2).replace("%", ESCAPE + "%").replace("_", ESCAPE + "_")
    return f"%{n}%"


def investigation_document(number: str | None, title: str | None, description: str | None) -> str:
    return document(number, title, description)

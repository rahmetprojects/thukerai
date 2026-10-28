"""
onboard/infer.py - look at a table and propose what each column means
=====================================================================

WHAT IT DOES
    field_map(rows) -> {"name": col, "name_ar": col, "date": col, "status": col, "category": col,
                        "website": col, "key": col}   (only the roles it is confident about)
    score(rows, fm) -> 0..1, how usable the table is as a dataset of named records

HOW (deterministic: headers first, then the values themselves)
    name      a header like name / company / entity / fund / firm / title (English or Arabic), otherwise the
              text column with the most distinct values
    name_ar   a header mentioning Arabic, or a column that is mostly Arabic script
    date      a header like date / issued / registration, or a column where 70%+ of values parse as dates
    website   a header like website / url / domain, or a column where 70%+ of values contain a domain
    key       an id / code / number column whose values are all distinct
    The person reviewing the proposal sees this mapping and can change any of it before approving.
"""
from __future__ import annotations

import re

from store.names import domains_in, parse_date

HEADER_HINTS = {
    "name": r"\b(name|company|entity|entities|firm|fund|title|organi[sz]ation|institution|issuer|broker|person)\b|اسم|الاسم|الشركة",
    "date": r"\b(date|issued|published|registration|registered|effective|valid|since)\b|تاريخ",
    "status": r"\b(status|state)\b|الحالة",
    "category": r"\b(type|category|class|domicile|sector|activity|kind|classification)\b|نوع|فئة|النشاط",
    "website": r"\b(website|web site|url|domain|site|link)\b|الموقع",
    "key": r"\b(id|code|number|no|ref|reference|licen[sc]e|reg)\b|رقم",
}
ARABIC = re.compile(r"[؀-ۿ]")


def _col_values(rows: list[dict], col: str) -> list[str]:
    return [str(r.get(col, "")).strip() for r in rows if str(r.get(col, "")).strip()]


def field_map(rows: list[dict]) -> dict:
    if not rows:
        return {}
    cols = list(rows[0].keys())
    sample = rows[:200]
    fm: dict[str, str] = {}

    def frac(col, test):
        vals = _col_values(sample, col)
        return (sum(1 for v in vals if test(v)) / len(vals)) if vals else 0.0

    for role in ("key", "date", "status", "category", "website", "name"):
        for c in cols:
            if c in fm.values():
                continue
            if re.search(HEADER_HINTS[role], c, re.I):
                if role == "name" and (ARABIC.search(c) and not re.search(r"[A-Za-z]", c)) and \
                        frac(c, lambda v: bool(ARABIC.search(v))) > 0.6:
                    fm.setdefault("name_ar", c)
                    continue
                if role == "key" and len(set(_col_values(sample, c))) < len(_col_values(sample, c)):
                    continue                                 # an "id" column with repeats is not a key
                if role == "date" and frac(c, lambda v: bool(parse_date(v))) < 0.5:
                    continue
                fm[role] = c
                break
    for c in cols:                                           # value-based fallbacks
        if c in fm.values():
            continue
        if "date" not in fm and frac(c, lambda v: bool(parse_date(v))) >= 0.7:
            fm["date"] = c
        elif "website" not in fm and frac(c, lambda v: bool(domains_in(v))) >= 0.7:
            fm["website"] = c
        elif "name_ar" not in fm and frac(c, lambda v: bool(ARABIC.search(v))) >= 0.7:
            fm["name_ar"] = c
    if "name" not in fm:                                     # the most varied text column
        best, best_n = None, 0
        for c in cols:
            if c in fm.values():
                continue
            vals = _col_values(sample, c)
            texty = [v for v in vals if re.search(r"[A-Za-z؀-ۿ]{3}", v) and not parse_date(v)]
            if len(set(texty)) > best_n:
                best, best_n = c, len(set(texty))
        if best:
            fm["name"] = best
    return fm


def score(rows: list[dict], fm: dict) -> float:
    """How usable this table is: has rows, a name column that is mostly filled and varied."""
    if not rows or "name" not in fm:
        return 0.0
    vals = _col_values(rows, fm["name"])
    filled = len(vals) / len(rows)
    distinct = len(set(vals)) / max(1, len(vals))
    size = min(1.0, len(rows) / 20)
    return round(0.45 * filled + 0.35 * distinct + 0.2 * size, 2)

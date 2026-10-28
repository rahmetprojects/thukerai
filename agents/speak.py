"""
agents/speak.py - say numbers and dates the way a person would read them aloud
==============================================================================

WHAT IT DOES
    Tiny formatting helpers used by every workflow so answers sound natural when spoken:
        aed(250000)          -> "AED 250,000"
        months_text(50)      -> "about 50 months, which is about 4 years and 2 months"
        pct(2.33)            -> "2.3%"
        date_text("2026-10-22") -> "22 October 2026"
    Numbers are written as digits on purpose: the screen shows them clearly, the browser voice reads
    them correctly, and the safety validator can check every digit against the calculations.
"""
from __future__ import annotations

from datetime import date


def aed(x) -> str:
    if x is None:
        return "an unknown amount"
    x = float(x)
    return f"AED {x:,.0f}" if abs(x) >= 100 or x == int(x) else f"AED {x:,.2f}"


def num(x, nd: int = 0) -> str:
    return f"{float(x):,.{nd}f}"


def pct(x, nd: int = 1) -> str:
    x = float(x)
    s = f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{x:.0f}"
    return f"{s}%"


def months_text(m) -> str:
    if m is None:
        return "more than 100 years"
    m = float(m)
    y, rem = divmod(round(m), 12)
    whole = f"{m:.1f}".rstrip("0").rstrip(".")
    if y == 0:
        return f"about {whole} months"
    tail = f"{y} year{'s' if y != 1 else ''}" + (f" and {rem} month{'s' if rem != 1 else ''}" if rem else "")
    return f"about {whole} months, which is about {tail}"


def years_text(y) -> str:
    if y is None:
        return "more than 100 years"
    return f"about {float(y):.1f}".rstrip("0").rstrip(".") + " years"


def date_text(iso: str | None) -> str:
    if not iso:
        return "an unknown date"
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%B %Y')}"
    except ValueError:
        return str(iso)


def join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""

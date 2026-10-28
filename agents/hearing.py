"""
agents/hearing.py - repair the most common speech-recognition slips in money conversations
==========================================================================================

WHY
    Browser speech recognition (Chrome = Google's general US-English model) does not know UAE money words.
    Real example from testing: "I have 120,000 dhs and I want to retire" was heard as
    "I have 100,000 diplomats and I want to swim all the time".

WHAT IT DOES (and what it deliberately does NOT do)
    Fixes ONLY currency words that come right after a number, where the meaning is unambiguous:
        "100,000 diplomats" / "durhams" / "der hams" / "dhs" / "d h s"  ->  "100,000 dirhams"
    Every fix is returned so the screen can show "Heard as ..." - nothing is changed silently.
    It does NOT guess other words ("swim all the time" is not turned into "retire") and it NEVER changes a
    number: a misheard amount is caught by the read-back ("I noted savings of AED 100,000 - correct me")
    and the "Fix what I heard" button.
"""
from __future__ import annotations

import re

_NUM = r"(\d[\d,]*(?:\.\d+)?\s*(?:k|thousand|million|lakh)?|(?:a |one |two |three |five |ten |hundred )?(?:thousand|million))"
_CURRENCY_SLIPS = (r"diplomats?|durhams?|durum|der\s?hams?|dear\s?hams?|their\s?hams?|dirhems?|dirams?|dhirams?|"
                   r"dirhums?|deer\s?hams?|d\.?\s?h\.?\s?s\.?|dhs|dee\s?h\s?s|dirham's|dir\s?hams?")
CURRENCY = re.compile(rf"\b{_NUM}\s+(?:{_CURRENCY_SLIPS})\b", re.I)


def repair(text: str) -> tuple[str, list[dict]]:
    """(repaired_text, [{"heard": "100,000 diplomats", "as": "100,000 dirhams"}, ...])"""
    fixes: list[dict] = []

    def sub(m: re.Match) -> str:
        num = m.group(1).strip()
        new = f"{num} dirhams"
        if m.group(0).strip().lower() != new.lower():
            fixes.append({"heard": m.group(0).strip(), "as": new})
        return new
    return CURRENCY.sub(sub, text), fixes

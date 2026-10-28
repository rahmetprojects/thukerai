"""
guardian/lang.py - small language helpers so English and Arabic share one pipeline
==================================================================================

WHAT IT DOES
    detect_language(text)   "ar" or "en" from the letters used
    normalize_digits(text)  Arabic-Indic digits (٣٤٥) -> 0-9, so amounts and card numbers are recognised
    normalize_arabic(text)  strips diacritics/tatweel and unifies alef/ya forms, so one regex matches many spellings

USED BY
    guardian/redact.py, guardian/signals.py, guardian/guard.py, agents/slots.py
    (Ported unchanged from the previous version; all Arabic patterns elsewhere assume normalize_arabic's output.)
"""
from __future__ import annotations

import re

_ARABIC_LETTER = re.compile(r"[؀-ۿ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")

# Arabic-Indic (٠-٩) and Persian/Urdu (۰-۹) digits -> ASCII 0-9
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Diacritics (tashkeel) U+064B-U+065F, superscript alef U+0670, tatweel U+0640
_DIACRITICS = re.compile(r"[ً-ٰٟـ]")
_ALEF_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي"})


def detect_language(text: str) -> str:
    """Return "ar" if Arabic letters are at least a third of all letters, else "en"."""
    ar = len(_ARABIC_LETTER.findall(text))
    lat = len(_LATIN_LETTER.findall(text))
    if ar + lat == 0:
        return "en"                                  # digits/punctuation only
    return "ar" if ar / (ar + lat) >= 0.34 else "en"


def normalize_digits(text: str) -> str:
    return text.translate(_DIGITS)


def normalize_arabic(text: str) -> str:
    """Normalised form used for ALL Arabic pattern matching."""
    text = _DIACRITICS.sub("", text)
    return text.translate(_ALEF_FORMS)

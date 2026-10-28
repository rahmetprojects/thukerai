"""
guardian/redact.py - remove secrets BEFORE anything is stored, logged or sent to an AI
======================================================================================

WHAT IT DOES
    A confused or pressured person may read a card number or a one-time code aloud. This removes, from
    the TEXT: Emirates ID, IBAN, UAE phone numbers, 12-19 digit card/account numbers, PIN/OTP/CVV/password
    next to a keyword, and long strings of spoken digits ("four five six one two nine"), English and Arabic.
    redact(text) -> (clean_text, kinds_found), e.g. ("my pin is [REDACTED:secret_code]", ["secret_code"])

    If a SECRET was spoken (not just a phone number), guardian/guard.py switches to the fixed
    "please stop, never share that" script.

LIMITS
    Pattern based: names, home addresses and e-mails are NOT detected. Over-redacting is the safe error.
    The browser's speech service may already have heard the audio; we can only protect the text.
    (Ported unchanged from the previous version.)
"""
from __future__ import annotations

import re

from .lang import normalize_digits

_EN_DIGIT_WORDS = "zero|oh|one|two|three|four|five|six|seven|eight|nine"
# Arabic digit words (normalised spelling: bare alef). Gulf/Levantine variants included.
_AR_DIGIT_WORDS = "صفر|واحد|اثنين|اثنان|اتنين|ثلاثه|ثلاثة|تلاته|اربعه|اربعة|خمسه|خمسة|ستة|سته|سبعه|سبعة|ثمانيه|ثمانية|تمانيه|تسعه|تسعة"
# words meaning PIN/OTP/password (used to find a number that follows them)
_EN_SECRET_WORDS = r"\b(?:otp|pin|cvv|cvc|password|passcode|verification code|security code|code)\b"
_AR_SECRET_WORDS = r"(?:رمز|كود|الرقم السري|كلمه المرور|كلمة المرور|كلمه السر|كلمة السر|الباسورد|الرقم الشخصي)"

# Kinds that mean the person SAID A SECRET (card, ID, PIN...). These trigger the "please stop" safety
# script. A phone number is personal data we also remove, but it is not a secret, so it does not.
SENSITIVE_KINDS = {"emirates_id", "iban", "card_or_account_number", "secret_code", "spoken_digits"}

# ORDER MATTERS: most specific first, so an Emirates ID is not reported as a "card number".
PATTERNS: list[tuple[str, re.Pattern]] = [
    # Emirates ID: 784-YYYY-NNNNNNN-C (dashes/spaces optional)
    ("emirates_id", re.compile(r"\b784[- ]?\d{4}[- ]?\d{7}[- ]?\d\b")),
    # UAE IBAN: AE + 2 check digits + 19 digits
    ("iban", re.compile(r"\bAE\d{2}(?:[ -]?\d){19}\b", re.I)),
    # UAE phone numbers: 05x xxx xxxx, +971 5x xxx xxxx, 00971..., landlines. Placed BEFORE the card
    # pattern because +971501234567 has 12 digits and would otherwise be reported as a card.
    ("phone_number", re.compile(r"(?<!\d)(?:(?:\+|00)?971[\s-]?|0)(?:5\d|[2-47-9])(?:[\s-]?\d){7}(?!\d)")),
    # 12-19 digits, optionally grouped by spaces/dashes: card or account number
    ("card_or_account_number", re.compile(r"\b\d(?:[ -]?\d){11,18}\b")),
    # a 3-8 digit number shortly after a secret word. group(1) is the digits only.
    ("secret_code", re.compile(
        rf"(?:{_EN_SECRET_WORDS}|{_AR_SECRET_WORDS})[^0-9\n]{{0,25}}?\b(\d{{3,8}})\b", re.I)),
    # six or more digit words in a row (English)
    ("spoken_digits", re.compile(rf"\b(?:(?:{_EN_DIGIT_WORDS})[ ,-]+){{5,}}(?:{_EN_DIGIT_WORDS})\b", re.I)),
    # six or more digit words in a row (Arabic, separated by spaces or "و")
    ("spoken_digits", re.compile(rf"(?:(?:{_AR_DIGIT_WORDS})[ ،,-]+(?:و)?){{5,}}(?:{_AR_DIGIT_WORDS})")),
]


def redact(text: str) -> tuple[str, list[str]]:
    """Return (clean_text, kinds_found). `kinds_found` is unique and in detection order."""
    text = normalize_digits(text)                    # so Arabic-Indic digits are caught too
    kinds: list[str] = []
    for kind, pat in PATTERNS:
        def sub(m: re.Match, kind: str = kind) -> str:
            kinds.append(kind)
            if kind == "secret_code":                # keep the keyword, hide only the digits
                return m.group(0).replace(m.group(1), f"[REDACTED:{kind}]")
            return f"[REDACTED:{kind}]"
        text = pat.sub(sub, text)
    return text, list(dict.fromkeys(kinds))          # de-duplicate, keep order

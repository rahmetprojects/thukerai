"""
guardian/boundaries.py - three judgement calls that need no AI
=============================================================

WHAT IT DOES
    asks_for_recommendation(text)  "which fund should I buy?" -> we explain options and risks, never pick one
    referral_topic(text)           tax / legal / medical -> general information plus "ask a professional"
    low_clarity(text)              too vague to act on -> ask a gentle question instead of guessing

WHY
    This is an educational protection tool, not a licensed adviser: recommending a product could be
    regulated financial advice in the UAE. (Ported from the previous version.)
"""
from __future__ import annotations

import re

# --- 1. "What should I buy?" ---------------------------------------------------------------
RECOMMENDATION = re.compile(
    r"\b(should i|shall i|can i|is it (good|wise|smart|safe) to|what should i|which)\b.{0,40}"
    r"\b(buy|sell|invest|put|move|choose|pick|trade|hold|switch)\b"
    r"|\bwhat (to|should i) invest in\b"
    r"|\b(best|safest|top) (investment|fund|stock|bond|bank|crypto|property|gold|savings)\b"
    r"|\bis .{1,40} a good investment\b", re.I)
RECOMMENDATION_AR = re.compile(
    r"(هل|شو|ايش|ماذا|وش).{0,30}(اشتري|ابيع|استثمر|احط|انقل|اختار|اضع)"
    r"|(افضل|احسن|اضمن) (استثمار|صندوق|سهم|اسهم|بنك|ذهب|ادخار|عقار)"
    r"|(ينفع|يصلح|هل يجوز).{0,15}(استثمر|اشتري)")

# --- 2. Hand-offs (note: the verb "will" is deliberately NOT here, see tests) --------------
REFERRALS = {
    "tax": re.compile(r"\b(tax|vat|zakat|corporate tax)\b|ضريب[ةه]|زكا[ةه]", re.I),
    "legal": re.compile(
        r"\b(inheritance|inherit|probate|court|lawyer|divorce|power of attorney|guardian|will and testament)\b"
        r"|\b(write|make|draft|update|change|have|need) (a|my) will\b|\bmy will\b"
        r"|وصي[ةه]|ميراث|ارث|محام|محكم[ةه]|توكيل|طلاق", re.I),
    "medical": re.compile(r"\b(doctor|medicine|diagnos|dementia|illness|hospital bill|health insurance claim)\b"
                          r"|دكتور|طبيب|مرض|مستشفى|دواء", re.I),
}
REFER_TO = {
    "tax": "a qualified tax adviser or the Federal Tax Authority",
    "legal": "a qualified lawyer",
    "medical": "your doctor or your insurer",
}
REFER_TO_AR = {
    "tax": "مستشار ضريبي مؤهل أو الهيئة الاتحادية للضرائب",
    "legal": "محامٍ مؤهل",
    "medical": "طبيبك أو شركة التأمين",
}

# --- 3. Clarity ----------------------------------------------------------------------------
# If any money-related word is present we assume the request is understandable.
TOPIC_WORDS = re.compile(
    r"\b(money|bank|invest\w*|pension|saving\w*|loan|debt|rent\w*|property|house|villa|inflation|price\w*|"
    r"scam|fraud|call|message|pay\w*|account|card|insurance|gold|stock\w*|fund\w*|retire\w*|income|"
    r"mortgage|interest|dirham|aed|cost|bill\w*|transfer|crypto|bitcoin|tax|sell|buy|afford)\b"
    r"|فلوس|مال|بنك|استثمار|معاش|راتب|ادخار|مدخرات|قرض|دين|ايجار|عقار|بيت|فيلا|تضخم|اسعار|احتيال|نصب"
    r"|مكالم[ةه]|رسال[ةه]|بطاق[ةه]|حساب|تحويل|تامين|ذهب|اسهم|تقاعد|درهم", re.I)
# Hesitation sounds and empty words that do not count as content.
FILLERS = re.compile(r"\b(um+|uh+|er+|hmm+|ah+|hello|hi|hey|okay|ok|so|well|the|a|thing|paper|that|this)\b|^(اه+|ام+|هم+|شو|يعني|مرحبا|السلام|عليكم)$", re.I)


def asks_for_recommendation(text: str) -> bool:
    return bool(RECOMMENDATION.search(text) or RECOMMENDATION_AR.search(text))


def referral_topic(text: str) -> str | None:
    for topic, pat in REFERRALS.items():
        if pat.search(text):
            return topic
    return None


def low_clarity(text: str) -> bool:
    """True if we cannot tell what the person wants (too short, or only filler words)."""
    words = re.findall(r"[A-Za-z؀-ۿ']+", text)
    if TOPIC_WORDS.search(text):          # a clear money topic is enough, even in two words ("inflation since 2021")
        return False
    if len(words) < 3:
        return True
    content = [w for w in words if not FILLERS.fullmatch(w)]
    return len(content) < 3

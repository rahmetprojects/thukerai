"""
agents/slots.py - pull amounts, ages, rates and names out of everyday speech
===========================================================================

WHAT IT DOES
    Turns "I've got about two hundred and fifty thousand dirhams saved and spend 5k a month, I'm 58" into
        {"savings": 250000, "monthly_spending": 5000, "current_age": 58}
    Every value keeps the exact words it came from, so the assistant can read it back
    ("I noted savings of AED 250,000 - tell me if I misheard") and the person can correct it.

HOW
    1. numbers_in(text)   finds every number: digits (250,000 / 250k / 1.5m / ٢٥٠٠٠٠) and English words
                          ("fifteen thousand", "two hundred and fifty thousand", "a quarter of a million")
    2. each number is assigned to a slot by the words AROUND it (e.g. "spend ... a month" -> monthly_spending,
       "%" + "a month" -> promised_rate), using the SLOT_CUES table below; a number with no clear cue is left
       unassigned rather than guessed
    3. answer_for(slot, text) reads the reply to a question we just asked ("about five thousand") for that slot
    4. is_correction(text) spots "no, I said 150,000" so the previous value is replaced, not added

SAFETY RULE
    "Fifteen thousand" must never become 50,000: word numbers are parsed by an explicit grammar, not by an AI.
    Unknown is not zero: "none" / "no pension" is stored as 0 ONLY when the slot allows it (zero_words).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from guardian.lang import normalize_arabic, normalize_digits
from store.names import domains_in

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "a": 1, "an": 1}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "lakh": 100_000, "billion": 1_000_000_000}
WORD = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|" \
       r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|" \
       r"lakh|billion|a|an|and|half|quarter|of)"
WORD_RUN = re.compile(rf"\b(?:{WORD})(?:[\s-]+(?:{WORD}))*\b", re.I)
DIGIT_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(k|m|mn|million|thousand|lakh|bn|billion)?\b(?!\.\d)", re.I)
AR_SCALE = re.compile(r"(\d+(?:\.\d+)?)\s*(الف|آلاف|الاف|مليون|ملايين)")


@dataclass
class Num:
    value: float
    start: int
    end: int
    text: str
    is_percent: bool = False


def words_to_number(phrase: str) -> float | None:
    """English number words -> number. 'two hundred and fifty thousand' -> 250000. None if not a number."""
    toks = [t for t in re.split(r"[\s-]+", phrase.lower().strip()) if t and t != "and"]
    if not toks or all(t in ("a", "an", "of") for t in toks):
        return None
    if toks[-1] in ("a", "an", "of"):
        return None
    # fractions of a scale: "half a million", "a quarter of a million"
    joined = " ".join(toks)
    m = re.fullmatch(r"(?:a |an )?(half|quarter)(?: of)?(?: a| an)? (thousand|million|lakh)", joined)
    if m:
        return (0.5 if m.group(1) == "half" else 0.25) * SCALES[m.group(2)]
    total, current, seen = 0.0, 0.0, False
    for t in toks:
        if t in ("of",):
            continue
        if t in UNITS:
            current += UNITS[t]
            seen = seen or t not in ("a", "an")
        elif t in TENS:
            current += TENS[t]
            seen = True
        elif t == "hundred":
            current = (current or 1) * 100
            seen = True
        elif t in SCALES:
            total += (current or 1) * SCALES[t]
            current = 0
            seen = True
        elif t in ("half", "quarter"):
            return None
        else:
            return None
    return total + current if seen else None


GLUED_UNIT = re.compile(r"(\d)(aed|dhs|dirhams?|dh|درهم)", re.I)


def tidy(text: str) -> str:
    """Speech/typing quirks: '10,000aed' -> '10,000 aed', Arabic digits -> 0-9."""
    return GLUED_UNIT.sub(r"\1 \2", normalize_digits(text))


def numbers_in(text: str) -> list[Num]:
    t = tidy(text)
    out: list[Num] = []
    taken: list[tuple[int, int]] = []
    for m in DIGIT_NUM.finditer(t):
        whole = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
        scale = (m.group(3) or "").lower()
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "lakh": 1e5, "bn": 1e9, "billion": 1e9}.get(scale, 1)
        end = m.end()
        after = t[end:end + 12]
        pct = bool(re.match(r"\s*(%|٪|percent|per cent|pc\b)", after, re.I))
        out.append(Num(whole * mult, m.start(), end, m.group(0), pct))
        taken.append((m.start(), end))
    for m in AR_SCALE.finditer(t):
        mult = 1e6 if "مليون" in m.group(2) or "ملايين" in m.group(2) else 1e3
        for n in out:
            if n.start == m.start():
                n.value, n.end, n.text = float(m.group(1)) * mult, m.end(), m.group(0)
    for m in WORD_RUN.finditer(t):
        if any(a <= m.start() < b for a, b in taken):
            continue
        v = words_to_number(m.group(0))
        if v is None:
            continue
        # "a month" / "an hour" are not numbers on their own
        if re.fullmatch(r"(?:a|an)", m.group(0).strip(), re.I):
            continue
        after = t[m.end():m.end() + 12]
        out.append(Num(v, m.start(), m.end(), m.group(0), bool(re.match(r"\s*(%|percent|per cent)", after, re.I))))
    return sorted(out, key=lambda n: n.start)


# ---------------------------------------------------------------------------- slot cues
# slot -> (kind, cue regexes searched in the ~45 characters around the number, percent?)
# kind: money | pct | age | years | int ; the FIRST slot whose cue matches wins, so order matters.
SLOT_CUES: list[tuple[str, str, list[str], bool | None]] = [
    ("promised_rate_pct", "pct", [r"(a|per|every|each) (month|week|day)", r"monthly|weekly|daily"], True),
    ("loss_pct", "pct", [r"\b(fell|fallen|dropped|down|lost|loss|fall|crash)"], True),
    ("annual_fee_pct", "pct", [r"\bfees?\b|charges?|management"], True),
    ("deposit_rate_pct", "pct", [r"interest|deposit|savings account|bank (is )?offer|fixed|rate"], True),
    ("fund_return_pct", "pct", [r"return(ed|s)?|gain(ed)?|made|grew|fund|earn"], True),
    ("debt_rate_pct", "pct", [r"loan|debt|card|borrow|owe"], True),
    ("current_age", "age", [r"\bi'?m\b|\bi am\b|\baged?\b|years old|\bage\b|عمري|عندي .{0,3}سن"], False),
    ("retire_age", "age", [r"retire|stop working|تقاعد"], False),
    ("monthly_income", "money", [r"pension|income|receive|salary|get paid|paid|rent (in|income)|allowance|معاش|راتب|دخل"], False),
    ("monthly_support", "money", [r"(send|give|support|help).{0,25}(son|daughter|children|kids|family)|to my (son|daughter|children)"], False),
    ("monthly_medical", "money", [r"medical|health|doctor|medicine|treatment|insurance premium a month"], False),
    ("monthly_spending", "money", [r"spend|spent|spending|expenses?|expences|costs?|live on|bills|budget|need .{0,10}(a|per|each) month|مصروف|اصرف"], False),
    ("debt", "money", [r"\bowe\b|debt|outstanding|credit card|دين|قرض علي"], False),
    ("loan_principal", "money", [r"loan|borrow|financ|قرض"], False),
    ("holding_value", "money", [r"shares?|stocks?|in (one|a single) company|holding|سهم"], False),
    ("sale_price", "money", [r"sell (my|the|our) (home|house|villa|flat|apartment|property)|(home|house|villa|flat|apartment|property) (is )?(worth|valued)"], False),
    ("savings", "money", [r"\bsav\w*|put aside|in the bank|nest egg|مدخرات|وفرت"], False),
    ("savings~weak", "money", [r"have got|i have|i've got|got .{0,15}(aed|dirham)|total|everything|عندي"], False),
    ("amount", "money", [r"aed|dirham|dhs|\$|درهم"], False),
]
MONEY_SLOTS = {s for s, k, _, _ in SLOT_CUES if k == "money"} | {"total_investable", "old_premium", "new_premium",
                                                                  "invested", "actual_balance", "mortgage_balance",
                                                                  "annual_rent", "remit_amount", "quoted_payment"}


def _context(t: str, n: Num, width: int = 45) -> str:
    return t[max(0, n.start - width): n.end + width].lower()


CPI_WORDS = {"FNB": r"\b(food|groceries|grocery|eating)\b", "HOU": r"\b(rent|housing|house prices|utilities|electricity|water bill)\b",
             "TRN": r"\b(transport|petrol|fuel|car|taxi)\b", "MED": r"\b(medical|health|doctor|medicine|hospital)\b",
             "EDU": r"\b(education|school|university|tuition)\b", "RES": r"\b(restaurants?|hotels?)\b"}


def _fits(slot: str, kind: str, n: Num) -> bool:
    if (kind == "pct") != n.is_percent:
        return False
    if kind == "age":
        return 18 <= n.value <= 110 and n.value == int(n.value)
    if kind == "money":
        return n.value >= 50 or bool(re.search(r"\b(k|thousand|million)\b", n.text, re.I))
    return True


def _cue_distance(t: str, n: Num, cues: list[str], width: int = 60) -> int | None:
    """Characters between the number and the NEAREST cue word around it (None = no cue nearby).
    A cue in another sentence/clause counts as farther away, so 'X saved, spend Y' assigns correctly."""
    lo, hi = max(0, n.start - width), min(len(t), n.end + width)
    best = None
    for c in cues:
        for m in re.finditer(c, t[lo:hi].lower()):
            a, b = lo + m.start(), lo + m.end()
            gap = n.start - b if b <= n.start else a - n.end if a >= n.end else 0
            between = (t[b:n.start] if b <= n.start else t[n.end:a]) if gap > 0 else ""   # never the number's own commas
            gap += 25 * len(re.findall(r"[,;.!?،]|\band\b|\bbut\b", between))
            best = gap if best is None else min(best, gap)
    return best


def extract(text: str) -> dict[str, dict]:
    """Assign numbers to slots by the NEAREST cue words. Returns {slot: {"value", "words"}}. Never guesses."""
    t = normalize_arabic(tidy(text))
    found: dict[str, dict] = {}
    options = []                                   # (distance, slot order, number index, slot)
    nums = numbers_in(t)
    target_spans = [m.span() for m in re.finditer(r"\b(?:to|until|till|past|reach\w*|beyond)\s+(?:age\s+)?\d{2,3}"
                                                   r"(?:\s*(?:or|and|,)\s*\d{2,3})*", t, re.I)]
    targets = [int(x) for a, b in target_spans for x in re.findall(r"\d{2,3}", t[a:b]) if 60 <= int(x) <= 110]
    if targets:
        found["target_ages"] = {"value": targets, "words": ", ".join(map(str, targets))}
    for i, n in enumerate(nums):
        if 1900 <= n.value <= 2100 and not n.is_percent and not re.search(r"aed|dirham|dhs", _context(t, n, 12)):
            found.setdefault("years_list", {"value": [], "words": []})
            found["years_list"]["value"].append(int(n.value))
            found["years_list"]["words"].append(n.text)
            continue
        for order, (slot, kind, cues, _) in enumerate(SLOT_CUES):
            if not _fits(slot, kind, n):
                continue
            if kind == "age" and any(a <= n.start < b for a, b in target_spans):
                continue                                  # "live to 90 or 95" are target ages, not their age
            if slot == "promised_rate_pct":           # "10% a month": the period must FOLLOW the number
                d = 0 if any(re.match(r"\s*(%|percent|per cent)?\s*" + c, t[n.end:n.end + 25].lower()) for c in cues) else None
            elif kind == "age" and re.search(r"(i'?m|i am|aged?)\s*$", t[max(0, n.start - 12):n.start].lower()) \
                    or kind == "age" and re.match(r"\s*(years? old|yrs)", t[n.end:n.end + 12].lower()):
                d = 0 if slot == "current_age" else None
            else:
                d = _cue_distance(t, n, cues)
            if d is not None:
                if slot == "amount":
                    d += 40                           # "AED" is next to every amount: only a last resort
                elif slot.endswith("~weak"):
                    d += 20                           # "I have 80k in shares": "shares" beats "I have"
                options.append((d, order, i, slot))
    used_nums: set[int] = set()
    for d, order, i, slot in sorted(options):
        if slot in found or i in used_nums:
            continue
        slot = slot.split("~")[0]
        if slot in found:
            continue
        found[slot] = {"value": round(nums[i].value, 2), "words": nums[i].text.strip()}
        used_nums.add(i)
    # periods: "for 5 years", "over 3 years", "a 5-year loan"
    m = re.search(r"(?:for|over|in|of|during|past|last)\s+(\d{1,2}|\w+)\s*-?\s*years?\b|(\d{1,2})\s*-?\s*years? (?:loan|term|ago)", t, re.I)
    if m:
        raw = m.group(1) or m.group(2)
        v = float(raw) if raw.isdigit() else words_to_number(raw)
        if v and 0 < v <= 60:
            found.setdefault("years", {"value": v, "words": m.group(0)})
    if re.search(r"\b(a|per|every|each) (week)\b|weekly", t) and "promised_rate_pct" in found:
        found["promised_period"] = {"value": "week", "words": "week"}
    elif re.search(r"\b(a|per|every|each) day\b|daily", t) and "promised_rate_pct" in found:
        found["promised_period"] = {"value": "day", "words": "day"}
    elif "promised_rate_pct" in found:
        found["promised_period"] = {"value": "month", "words": "month"}
    for code, pat in CPI_WORDS.items():
        if re.search(pat, t, re.I) and re.search(r"price|cost|inflation|expensive|what about|how about|^\s*and\b|اسعار|تكلف", t, re.I):
            found["cpi_category"] = {"value": code, "words": code}
            break
    if re.search(r"\bflat\b", t, re.I) and re.search(r"\b(loan|rate|interest|financ|borrow)|%", t, re.I):
        found["_flat_rate"] = {"value": True, "words": "flat"}
    if re.search(r"\b(son|daughter|brother|sister|nephew|niece|cousin|relative|friend|family)\b.{0,60}\b(loan|borrow|"
                 r"guarantee|business)", t, re.I):
        found["_for_family"] = {"value": True, "words": "family"}
    ch = re.search(r"\b(whatsapp|telegram|instagram|facebook|tiktok|snapchat|linkedin|signal)\b", t, re.I)
    if ch:
        found["_channel"] = {"value": ch.group(1).lower().capitalize().replace("Whatsapp", "WhatsApp").replace("Tiktok", "TikTok")
                                      .replace("Linkedin", "LinkedIn"), "words": ch.group(0)}
    if re.search(r"\b(today|right now|immediately|urgent\w*|before (it|the offer) (closes|ends)|last chance|"
                 r"only (a few|\d+) (hours|spots|places))\b", t, re.I):
        found["_rush"] = {"value": True, "words": "rush"}
    doms = domains_in(text)
    if doms:
        found["domain"] = {"value": doms[0], "words": doms[0]}
    name = entity_name(text)
    if name:
        found["entity_name"] = {"value": name, "words": name}
    return found


# ---------------------------------------------------------------------------- answers to a question
ZERO_WORDS = re.compile(r"^\s*(no|none|nothing|zero|nil|not really|no pension|no income|i don'?t|لا|ولا شي|لا يوجد|صفر)\b", re.I)
YES = re.compile(r"^\s*(yes|yeah|yep|correct|right|that'?s right|exactly|sure|ok|okay|نعم|ايوه|صح|تمام)\b", re.I)
NO = re.compile(r"^\s*(no|nope|not|wrong|نو|لا)\b", re.I)


# Words that NAME a fact explicitly. "I have 4,000" is weak (could be anything); "4,000 in savings" is strong.
# When we just asked for X and the reply has one number with no strong cue for something else, it answers X.
STRONG_CUES = {
    "savings": r"\bsav\w*|nest egg|in the bank|put aside|مدخرات",
    "monthly_spending": r"\bspen[dt]\w*|expen\w*|costs?\b|bills|live on|مصروف|اصرف",
    "monthly_income": r"pension|salary|income|allowance|معاش|راتب|دخل",
    "debt": r"\bowe\b|debt|credit card|دين",
    "current_age": r"years? old|\bi'?m \d|\baged?\b|عمري",
}


def strong_slots(text: str) -> set[str]:
    t = tidy(text).lower()
    return {s for s, pat in STRONG_CUES.items() if re.search(pat, t)}


def answer_for(slot: str, kind: str, text: str, allow_zero: bool = False):
    """The value for `slot` in a short reply to our question, or None if the reply does not give one."""
    t = tidy(text)
    if kind == "yesno":
        return True if YES.search(t) else False if NO.search(t) else None
    if kind == "text":
        if slot == "entity_name":
            return entity_name(text) or (text.strip().strip(".?!\"'") if 2 <= len(text.strip()) <= 80 else None)
        if slot == "domain":
            d = domains_in(text)
            return d[0] if d else None
        return text.strip() or None
    if kind == "choice":
        return text.strip().lower() or None
    nums = [n for n in numbers_in(t) if (n.is_percent or kind != "pct")]
    if not nums:
        if allow_zero and ZERO_WORDS.search(t):
            return 0.0
        return None
    if kind == "pct":
        return nums[0].value
    if kind in ("age", "int", "years"):
        return nums[0].value
    return max(n.value for n in nums) if kind == "money" else nums[0].value


CORRECTION = re.compile(r"^\s*(no[,.!]?\s+(i said|it'?s|it is|that'?s|make it|i meant|i have|i've|i spend|i spent|my)|actually|sorry,? (i meant|it'?s)|"
                        r"not .{1,20}[,،]? (but|it'?s)|i meant|correction|لا[،,]? (قلت|اقصد)|اقصد)", re.I)


def is_correction(text: str) -> bool:
    return bool(CORRECTION.search(normalize_digits(text)))


# ---------------------------------------------------------------------------- entity names
_NAME_END = r"(?=\s*[,.?!]|\s+(?:and|but|which|who|that|is|are|was|licen[sc]ed|legit\w*|registered|regulated|real|genuine|safe|a scam)\b|\s*$)"
_NOT_A_NAME = r"(?!(?:it|this|that|they|them|he|she|the company|the firm|this company|that company|this one|that one)\b)"
NAME_PATTERNS = [
    # "its name is hoxton fx", "it's called hoxton fx", "the company is Hoxton FX" (voice gives lower case)
    r"(?i:\b(?:its|it'?s|the|their|his|her|company'?s?|firm'?s?) name is|\bit'?s called|\bit is called|\bthey'?re called|"
    r"\bthey are called|\bthe (?:company|firm|platform|broker) is)\s+" + _NOT_A_NAME + r"(.{2,60}?)" + _NAME_END,
    r"[\"“]([^\"”]{2,80})[\"”]",
    r"(?<!\w)['‘]([^'’]{2,80})['’](?!\w)",
    r"\b(?:company|firm|platform|broker|app|fund|website|site|account|influencer|page) (?:called|named|is called)\s+([A-Z0-9][\w&.\- ]{1,60}?)(?=[,.?!]|\s+(?:is|and|which|that|who|offering|offers|says|promis|licen[sc]ed|legit|legitimate|registered|regulated|real|genuine|authori[sz]ed|a scam)|$)",
    r"\b(?:called|named)\s+([A-Z0-9][\w&.\- ]{1,60}?)(?=[,.?!]|\s+(?:is|and|which|that|who|offering|offers|says|promis|licen[sc]ed|legit|legitimate|registered|regulated|real|genuine|authori[sz]ed|a scam)|$)",
    r"(?i)\b(?:is|are|check|about)\s+" + _NOT_A_NAME + r"([a-z0-9][\w&.\-]*(?:\s+[\w&][\w&.\-]*){0,6}?)\s+"
    r"(?:licensed|licenced|legit|legitimate|real|registered|regulated|a scam|safe|authori[sz]ed|on the)",
    r"(?i:\b(?:check|verify|look up|lookup|search for)\s+(?:the company |the firm |company |firm |this company |a company called )?)"
    r"([A-Za-z0-9][\w&.\-]*(?:\s+[\w&.\-]+){0,6}?)\s*(?=[,.?!]|\s+(?:for me|please|is|and|on|licen)|$)",
    r"\b(?i:someone|somebody|a man|a woman|a guy|a lady|an agent|a person|a broker|an adviser|an advisor|they|he|she)"
    r"\s+(?i:from|at|with|of)\s+([A-Z0-9][\w&.\-]*(?:\s+[A-Z0-9&][\w&.\-]*){0,5})",
    r"(?:شركة|شركه|منصة|منصه)\s+([؀-ۿA-Za-z0-9 ]{2,40}?)(?=[،,.?؟]|\s+(?:مرخص|مرخصه|حقيقي|نصب)|$)",
]
STOP_NAMES = re.compile(r"^(it|this|that|them|they|he|she|my|the bank|a company|the company|someone|somebody|i|me|you|"
                        r"another company|another one|another|a company for me|company|a name|the name|them for me|"
                        r"safe|guaranteed|risk[- ]free|secure|legit|no risk|sure|halal|this company|that company|this one|that one|"
                        r"this firm|the firm|this|that|the offer|this offer|the website|this website)$", re.I)


def entity_name(text: str) -> str | None:
    for p in NAME_PATTERNS:
        m = re.search(p, text)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .,'\"")
            name = re.sub(r"\s+(please|thanks|thank you|for me|now|today)$", "", name, flags=re.I).strip()
            if 2 <= len(name) <= 80 and not STOP_NAMES.match(name) and not re.fullmatch(r"[\d.,%\s]+", name):
                return name
    return None

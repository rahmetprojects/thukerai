"""
store/names.py - turn messy entity names and web addresses into comparable keys
==============================================================================

WHAT IT DOES
    Names in official lists and in what a person says rarely match character for character:
        "ABSOLUTE RETURN INVESTMENT ADVISERS (ARIA) LIMITED (DUBAI BRANCH)"  vs  "Absolute Return Investment Advisers"
        "1.Faraz Financial Services PTY- Limited 2. PAY2ASSIST ... 3. Website (https://www.market10.net/en)"
    This module provides the ONE normalisation used both when records are stored (ingest) and when a
    person's words are searched (store/search.py), so the two sides always agree.

FUNCTIONS
    normalize_name(s)         lower-case, Unicode-normalised, punctuation removed, Arabic letters unified
    core_name(s)              normalize_name minus legal suffixes (LLC, Ltd, FZCO, PJSC, ذ.م.م ...)
    domains_in(text)          ["market10.net", ...] from any URLs / www. addresses in the text
    split_listed_names(title) the separate entities in a warning title "1. X 2. Y 3. Website (...)"
    parse_date(s)             "22/10/2026", "2026-10-22 00:10:00", "31 Dec 2026" -> "2026-10-22"

RULE
    Normalisation makes matching FAIR, not LOOSE. It never merges two different names; deciding whether a
    near-match is the same entity is store/search.py's job, and it reports uncertainty explicitly.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_AR_DIACRITICS = re.compile(r"[ً-ٰٟـ]")
_AR_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Legal-form words removed by core_name(). Multi-word forms first. Kept deliberately short: words like
# "capital", "financial", "investment" are NOT removed, because they distinguish real firms.
LEGAL_FORMS = [
    "limited liability company", "sole proprietorship llc", "one person company", "foreign branch", "dubai branch",
    "abu dhabi branch", "free zone company", "public joint stock company", "private joint stock company",
    "l l c", "s p c", "p j s c", "p s c", "f z e", "f z c o", "f z c", "f z llc", "llc", "llc fz", "l.l.c",
    "ltd", "limited", "fzco", "fze", "fzc", "fz", "pjsc", "psc", "pty", "plc", "inc", "corp", "corporation",
    "co", "company", "est", "establishment", "branch", "sole proprietorship", "spc", "dmcc", "difc", "adgm",
    # Arabic (normalised spelling)
    "ش ذ م م", "ذ م م", "ش م ع", "ش م خ", "شركه الشخص الواحد", "شركه", "مؤسسه", "فرع",
]
_LEGAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(f) for f in sorted(LEGAL_FORMS, key=len, reverse=True)) + r")\b")

URL_RE = re.compile(r"(?:https?://|www\.)[^\s()<>\"'،,]+", re.I)
BARE_DOMAIN_RE = re.compile(r"\b[a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|ae|io|co|info|biz|pro|trade|finance|"
                            r"capital|markets|uk|us|ltd|online|site|xyz|app)(?:\.[a-z]{2})?\b", re.I)
_LIST_SPLIT = re.compile(r"(?:^|\s)(\d{1,2})\s*[.)\-]\s*")
_PREFIXES = re.compile(r"^(?:company|mr|mrs|ms|miss|the company|website|the website|platform|app|application|"
                       r"شركة|شركه|السيد|السيدة|السيده|الموقع الإلكتروني|الموقع الالكتروني|الموقع|منصة|منصه|تطبيق)\s*[/:.\-]?\s*", re.I)


def normalize_arabic(s: str) -> str:
    return _AR_DIACRITICS.sub("", s).translate(_AR_FORMS)


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).translate(_DIGITS)
    s = normalize_arabic(s).casefold()
    s = s.replace("&", " and ").replace("’", "'").replace("'", "")
    s = re.sub(r"[^\w\s]", " ", s)              # punctuation -> space (keeps Arabic and Latin letters/digits)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def core_name(s: str | None) -> str:
    """normalize_name without legal-form words and bracketed asides. '' if nothing distinctive is left."""
    n = normalize_name(re.sub(r"\((?:dubai|abu dhabi|sharjah)?\s*branch\)", " ", s or "", flags=re.I))
    for _ in range(3):                          # forms can repeat: "x llc fz llc"
        n2 = re.sub(r"\s+", " ", _LEGAL_RE.sub(" ", n)).strip()
        if n2 == n:
            break
        n = n2
    return n


def domains_in(text: str | None) -> list[str]:
    """Web domains mentioned in the text, lower-case, without 'www.' and paths. Order kept, no duplicates."""
    if not text:
        return []
    out: list[str] = []
    found = [m.group(0) for m in URL_RE.finditer(text)]
    rest = URL_RE.sub(" ", text)
    found += [m.group(0) for m in BARE_DOMAIN_RE.finditer(rest)]
    for u in found:
        host = re.sub(r"^(?:https?://)?(?:www\.)?", "", u.strip().lower()).split("/")[0].split("?")[0].strip(".")
        if "." in host and host not in out and not host.endswith((".gov.ae",)):
            out.append(host)
    return out


def split_listed_names(title: str | None) -> list[str]:
    """'1.X 2. Y 3. Website (https://...)' -> ['X', 'Y'] (websites are returned by domains_in instead)."""
    if not title:
        return []
    t = re.sub(r"\s+", " ", title).strip()
    parts = _LIST_SPLIT.split(t)
    # re.split with a capture group gives [before, num, text, num, text, ...]
    chunks = [parts[0]] + [parts[i] for i in range(2, len(parts), 2)] if len(parts) > 1 else [t]
    names = []
    for c in chunks:
        c = URL_RE.sub(" ", c)
        c = c.strip(" -–,;:/")
        c = re.sub(r"^\((.*)\)$", r"\1", c.strip())                 # "(Neolin Capital Market Ltd)"
        c = _PREFIXES.sub("", c.strip())                             # "شركة/ X", "Mr. X", "Website X"
        c = re.sub(r"\(\s*\)", " ", c)
        c = re.sub(r"^\((.*)\)$", r"\1", c.strip()).strip(" -–,;:/")
        c = re.sub(r"\s+", " ", c)
        if c.count("(") > c.count(")"):
            c += ")"
        if len(normalize_name(c)) >= 3 and not re.fullmatch(r"(?i)website|الموقع الالكتروني", c):
            names.append(c)
    return names


def parse_date(s: str | None) -> str | None:
    """Best-effort ISO date (YYYY-MM-DD). d/m/Y is assumed for slashes (the CMA's format)."""
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d", "%d/%m/%Y",
                "%d %b %Y", "%d %B %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:26], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

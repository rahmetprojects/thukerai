"""
trust/signals.py - find what to look up in whatever the person pasted
=====================================================================

WHAT IT DOES
    extract(text) turns free text into SIGNALS, the things worth checking against official lists:
        name     a company or person ("Hoxton FX", "Faraz Financial Services")
        domain   a website ("market10.net"), from a link or a bare domain
        handle   a social media account ("@promoter12", "instagram.com/promoter12")
        phone    a phone number ("+971 50 123 4567"), kept as digits
    Input can be one name, a link, a forwarded WhatsApp message, or text an AI read from a screenshot.

RULES
    * Deterministic: regular expressions and small heuristics, no AI. (The optional AI step lives in
      trust/report.py and only TRANSCRIBES an image; this module still decides what to look up.)
    * Links to social or messaging platforms (instagram.com, wa.me, t.me ...) are never checked as a
      company website: "instagram.com" is on thousands of pages. They become handles instead.
    * Short input with no sentence structure is treated as one name, because that is what people paste.
    * At most MAX_PER_KIND signals of each kind, so a long message cannot trigger hundreds of lookups.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from agents.slots import entity_name
from store.names import domains_in

MAX_PER_KIND = 5

# platform -> how its profile URLs look; the handle is the first path segment (without "@")
SOCIAL_HOSTS = {"instagram.com", "tiktok.com", "x.com", "twitter.com", "facebook.com", "fb.com", "youtube.com",
                "t.me", "telegram.me", "snapchat.com", "linkedin.com", "threads.net"}
MESSAGING_HOSTS = {"wa.me", "whatsapp.com", "api.whatsapp.com", "chat.whatsapp.com"}
NOT_A_HANDLE = {"p", "reel", "reels", "watch", "share", "stories", "channel", "c", "user", "in", "company", "add",
                "joinchat", "explore", "hashtag", "home", "profile.php", "pages", "groups", "status", "shorts"}

HANDLE_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_])?)\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+|00)?\d[\d\s\-()]{7,16}\d(?!\d)")
SOCIAL_URL_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?([a-z0-9.-]+\.[a-z]{2,})/(@?[A-Za-z0-9_.\-]+)", re.I)

# words that start a capitalised run but are never part of a firm's name
_LEAD = {"i", "hi", "hello", "dear", "the", "a", "an", "this", "that", "they", "we", "you", "he", "she", "it", "my",
         "our", "your", "please", "call", "contact", "join", "invest", "get", "send", "click", "visit", "check",
         "is", "are", "do", "does", "did", "can", "will", "would", "should", "what", "who", "how", "why", "when",
         "today", "now", "guaranteed", "limited", "offer", "yes", "no", "ok", "thanks", "dm", "whatsapp", "telegram",
         "instagram", "aed", "usd", "dhs", "dirhams", "uae", "dubai", "abu", "sharjah", "monday", "tuesday",
         "wednesday", "thursday", "friday", "saturday", "sunday"}
_CAP_RUN = re.compile(r"\b(?:[A-Z][A-Za-z0-9&'.\-]*|[0-9]+[A-Za-z][A-Za-z0-9]*)(?:\s+(?:[A-Z][A-Za-z0-9&'.\-]*|&|of|and|[0-9]+[A-Za-z]*)){0,5}")
_FIRM_WORD = re.compile(r"\b(ltd|limited|llc|l\.l\.c|fzco|fze|inc|plc|capital|markets?|invest\w*|trad\w*|fx|forex|"
                        r"financ\w*|securities|wealth|asset\w*|fund\w*|bank|broker\w*|group|holding\w*|partners|"
                        r"exchange|crypto|coin|global|consult\w*|advis\w*)\b", re.I)


@dataclass(frozen=True)
class Signal:
    kind: str           # name | domain | handle | phone
    value: str          # what we look up (normalised: domain lower-case, phone digits only, handle without @)
    shown: str          # what the person wrote, for the report

    def as_dict(self) -> dict:
        return asdict(self)


def phone_digits(s: str) -> str:
    """'+971 50-123 4567' -> '971501234567'; '050 123 4567' -> '971501234567' (UAE national form)."""
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) == 10:          # 05x xxx xxxx -> 9715x xxx xxxx
        d = "971" + d[1:]
    return d


def same_phone(a: str, b: str) -> bool:
    """Two numbers are the same if their last 9 digits match (country-code and leading-zero tolerant)."""
    da, db = phone_digits(a), phone_digits(b)
    return len(da) >= 8 and len(db) >= 8 and da[-9:] == db[-9:]


def _host(url: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.|m\.)?", "", url.lower()).split("/")[0]


def _social(text: str) -> tuple[list[Signal], set[str]]:
    """Handles from profile links; returns the handles and the platform hosts seen (to drop from domains)."""
    out, hosts = [], set()
    for m in SOCIAL_URL_RE.finditer(text):
        host = m.group(1).lower().removeprefix("www.").removeprefix("m.")
        if host in SOCIAL_HOSTS:
            hosts.add(host)
            h = m.group(2).lstrip("@")
            if h.lower() not in NOT_A_HANDLE and len(h) >= 2:
                out.append(Signal("handle", h.lower(), "@" + h))
        elif host in MESSAGING_HOSTS:
            hosts.add(host)
            digits = re.sub(r"\D", "", m.group(2))
            if len(digits) >= 8:
                out.append(Signal("phone", phone_digits(digits), m.group(0)))
    return out, hosts


def _names(text: str) -> list[str]:
    t = text.strip()
    words = t.split()
    bare = re.sub(r"https?://\S+|www\.\S+|@\w+", " ", t)
    bare_words = bare.split()
    # 1. short input that is not a sentence = a name ("Hoxton FX", "4T Global Markets")
    if 1 <= len(bare_words) <= 6 and not re.search(r"[?!]|\b(is|are|was|they|me|my|i|you|please)\b", bare, re.I) \
            and not PHONE_RE.fullmatch(bare.strip()) and len(bare.strip()) >= 2:
        return [" ".join(bare_words).strip(" .,'\"")]
    found: list[str] = []
    # 2. the conversation patterns ("is X licensed", "a company called X", "someone from X")
    n = entity_name(t)
    if n:
        found.append(re.split(r"[.!?,;:](?:\s|$)", n)[0].strip())   # "Pro Capital. Join our..." -> "Pro Capital"
    # 3. capitalised runs that look like a firm ("BitWealth Pro Capital", "HOXTON FX")
    runs = [m.group(0) for part in re.split(r"[.!?;:,](?:\s|$)", t) for m in _CAP_RUN.finditer(part)]
    for run in runs:
        run = run.strip(" .,'\"")
        toks = run.split()
        while toks and toks[0].lower().strip(".,") in _LEAD:
            toks = toks[1:]
        run = " ".join(toks)
        if not run or len(run) < 3 or run.isdigit():
            continue
        if len(toks) >= 2 or _FIRM_WORD.search(run) or run.isupper() and len(run) >= 4:
            if not any(run.lower() in f.lower() or f.lower() in run.lower() for f in found):
                found.append(run)
    return found


def extract(text: str) -> list[Signal]:
    """Every signal in the text, in order of kind (domain, handle, phone, name), de-duplicated."""
    text = (text or "").strip()[:4000]
    if not text:
        return []
    social, platform_hosts = _social(text)
    sig: list[Signal] = []
    for d in domains_in(text):
        if d not in platform_hosts and d not in SOCIAL_HOSTS and d not in MESSAGING_HOSTS:
            sig.append(Signal("domain", d, d))
    sig += [s for s in social if s.kind == "handle"]
    for m in HANDLE_RE.finditer(text):
        if "." in m.group(1) and domains_in(m.group(1)):
            continue                                   # an e-mail-like "@site.com", not a handle
        sig.append(Signal("handle", m.group(1).lower(), "@" + m.group(1)))
    sig += [s for s in social if s.kind == "phone"]
    for m in PHONE_RE.finditer(re.sub(r"https?://\S+", " ", text)):
        d = phone_digits(m.group(0))
        if 9 <= len(d) <= 15 and not re.fullmatch(r"(19|20)\d{2}\D?\d+", m.group(0).strip()):
            sig.append(Signal("phone", d, m.group(0).strip()))
    for n in _names(text):
        if domains_in(n) and len(n.split()) == 1:
            continue                                   # "market10.net" alone is a domain, already added
        sig.append(Signal("name", n, n))
    out, seen, per = [], set(), {}
    for s in sig:
        key = (s.kind, s.value.lower())
        if key in seen or per.get(s.kind, 0) >= MAX_PER_KIND:
            continue
        seen.add(key)
        per[s.kind] = per.get(s.kind, 0) + 1
        out.append(s)
    return out

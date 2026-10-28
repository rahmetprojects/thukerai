"""
explore/planner.py - a question in plain words -> a validated query PLAN
=======================================================================

THE PLAN (what every question becomes, and what the API / MCP accept directly)
    op        list | count | latest | lookup | find_datasets | changes
    datasets  dataset ids from the registry
    text      words to full-text search inside those datasets ("" = none)
    since / until   ISO dates applied to each record's `date`
    filters   {field: value}, only fields the catalogue allows for those datasets (explore/catalog.py)
    limit     how many records to return (max 200)
    reading   the plan in plain words, shown to the person as editable chips ("Warnings", "since 1 Oct 2026")

TWO PLANNERS
    rules (default, ~1 ms): dataset keywords from the registry, date phrases ("this month", "in 2025", "last 30
          days"), counting words, licensed-activity names, domiciles, and entity names for look-ups.
    AI (optional): used only when the rules place NO dataset and an AI key is set. Its JSON is accepted only if
          every dataset id, op and filter field is valid; otherwise the rules' answer stands.
    Either way the plan is DATA, not an answer: the engine runs it, and the person sees and can edit it.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from ingest.registry import Registry
from store.names import domains_in
from agents.slots import CPI_WORDS, entity_name
from trust.signals import HANDLE_RE

OPS = ("list", "count", "latest", "lookup", "find_datasets", "changes")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
STOP = set("""a an the of in on at to for from by with and or is are was were be been do does did which what who whom
whose how many much list show me give find all any every there their them they it its this that these those
please can could would should i we you my our your latest recent new newest last past since until before after
during between year years month months week weeks day days today yesterday currently now ever data dataset datasets
records record information info about against named called number count total firms firm companies company entities
entity people person ones one list lists official cma uae issued published registered added made got been have has
had get under than more less over fund funds warning warnings warned report reports finfluencer finfluencers
influencer influencers auditor auditors authorised authorized authorisation authorization licence license licensed
licenced changed change changes whose where when why being still""".split())

COUNT_RE = re.compile(r"\bhow many\b|\bnumber of\b|\bcount\b|\btotal\b|كم عدد", re.I)
CHANGES_RE = re.compile(r"\bwhat('?s| has| is)? (changed|new)\b|\bchanges?\b|\bnewly\b|ما الجديد", re.I)
LATEST_RE = re.compile(r"\b(latest|most recent|newest|recent|new)\b|أحدث|آخر", re.I)
FIND_RE = re.compile(r"\b(data ?sets?|what data|which (data|list)|where (can|do) i find|is there (any )?(data|a list|"
                     r"information)|what (lists|information) (do|does))\b", re.I)
NOT_LICENSED_RE = re.compile(r"\bunlicen[sc]ed\b|\bnot (licen[sc]ed|authori[sz]ed|regulated)\b|غير مرخص", re.I)


@dataclass
class Plan:
    op: str = "list"
    datasets: list[str] = field(default_factory=list)
    text: str = ""
    since: str | None = None
    until: str | None = None
    filters: dict = field(default_factory=dict)
    limit: int = 20
    reading: list[str] = field(default_factory=list)
    planner: str = "rules"
    confident: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------- dates
def date_range(q: str, today: date) -> tuple[str | None, str | None, str | None, str]:
    """(since, until, words shown, the matched text) for the first date phrase in q."""
    t = q.lower()
    m = re.search(r"\b(?:in the )?(?:last|past) (\d{1,3}) (day|week|month|year)s?\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        return (today - timedelta(days=days)).isoformat(), None, f"last {n} {unit}{'s' if n > 1 else ''}", m.group(0)
    if re.search(r"\btoday\b", t):
        return today.isoformat(), None, "today", "today"
    if re.search(r"\bthis week\b|\b(last|past) week\b", t):
        return (today - timedelta(days=7)).isoformat(), None, "last 7 days", "week"
    if re.search(r"\bthis month\b", t):
        s = today.replace(day=1)
        return s.isoformat(), None, f"since {s.strftime('%-d %b %Y')}", "this month"
    if re.search(r"\blast month\b", t):
        end = today.replace(day=1) - timedelta(days=1)
        s = end.replace(day=1)
        return s.isoformat(), end.isoformat(), s.strftime("%B %Y"), "last month"
    if re.search(r"\bthis year\b", t):
        return f"{today.year}-01-01", None, f"since 1 Jan {today.year}", "this year"
    if re.search(r"\blast year\b", t):
        y = today.year - 1
        return f"{y}-01-01", f"{y}-12-31", str(y), "last year"
    m = re.search(r"\b(?:in|during|for)?\s*(" + "|".join(MONTHS) + r")\s+(20\d{2})\b", t)
    if m:
        mi, y = MONTHS.index(m.group(1)) + 1, int(m.group(2))
        s = date(y, mi, 1)
        e = (date(y + (mi == 12), mi % 12 + 1, 1) - timedelta(days=1))
        return s.isoformat(), e.isoformat(), s.strftime("%B %Y"), m.group(0)
    m = re.search(r"\bsince (20\d{2})\b", t)
    if m:
        return f"{m.group(1)}-01-01", None, f"since {m.group(1)}", m.group(0)
    m = re.search(r"\b(?:in|during|for|of) (20\d{2})\b", t)
    if m:
        y = m.group(1)
        return f"{y}-01-01", f"{y}-12-31", y, m.group(0)
    return None, None, None, ""


# ---------------------------------------------------------------------------- the rule planner
class RulePlanner:
    def __init__(self, registry: Registry, activities: list[str] | None = None):
        self.reg = registry
        self.activities = activities or []
        self.kw = {d.id: [k.lower() for k in d.keywords] for d in registry.datasets}

    def dataset_scores(self, q: str) -> dict[str, float]:
        t = " " + q.lower() + " "
        scores: dict[str, float] = {}
        for ds, words in self.kw.items():
            s = 0.0
            for w in words:
                if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", t):
                    s += 1 + 0.5 * (len(w.split()) - 1)
            if s:
                scores[ds] = s
        if NOT_LICENSED_RE.search(q):                        # "not licensed" is about warnings, not licences
            scores.pop("cma_licensed_companies", None)
            scores["cma_warnings"] = scores.get("cma_warnings", 0) + 2
        return scores

    def plan(self, q: str, today: date | None = None) -> Plan:
        today = today or date.today()
        p = Plan()
        t = q.strip()
        since, until, when, matched_date = date_range(t, today)
        p.since, p.until = since, until

        # 1. a named thing to check -> lookup (the Trust Report)
        name = entity_name(t)
        doms = domains_in(t)
        handles = [m.group(1) for m in HANDLE_RE.finditer(t)]
        scores = self.dataset_scores(t)
        if (name or doms or handles) and not COUNT_RE.search(t):
            p.op, p.text = "lookup", name or (doms[0] if doms else "@" + handles[0])
            p.datasets = []
            p.reading = [f"check “{p.text}” on every official list"]
            return p

        # 2. a licensed activity named -> licensed companies filtered by that activity
        act = self._activity(t)
        if act:
            p.datasets, p.filters["activity"] = ["cma_licensed_companies"], act
            p.reading.append("licensed companies")
            p.reading.append(f"allowed to do: {act}")
        # 3. otherwise datasets by keyword
        if not p.datasets and scores:
            best = max(scores.values())
            p.datasets = [d for d, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s >= best * 0.5][:4]
            p.reading += [self.reg.get(d).official_name for d in p.datasets]

        # 4. op
        if FIND_RE.search(t) and not COUNT_RE.search(t):
            p.op = "find_datasets"
        elif COUNT_RE.search(t):
            p.op = "count"
        elif CHANGES_RE.search(t):
            p.op = "changes"
        elif LATEST_RE.search(t) and not since:
            p.op = "latest"
        else:
            p.op = "list"
        if p.op == "count":
            p.reading.insert(0, "count")
        if when:
            p.reading.append(when)

        # 5. dataset-specific filters
        tl = t.lower()
        if "cma_passported_funds" in p.datasets:
            for dom in ("ADGM", "DIFC"):
                if dom.lower() in tl:
                    p.datasets, p.filters["category"] = ["cma_passported_funds"], dom
                    p.reading.append(f"domiciled in {dom}")
        if "cma_local_funds" in p.datasets:
            for c in ("public", "private"):
                if re.search(rf"\b{c}\b", tl):
                    p.filters["category"] = c.capitalize()
                    p.reading.append(f"{c} funds")
        if "cma_finfluencers" in p.datasets:
            if re.search(r"\bexpired\b|no longer valid", tl):
                p.filters["valid"] = "no"
                p.reading.append("authorisation expired")
            elif re.search(r"\bvalid\b|\bcurrent\b|\bactive\b", tl):
                p.filters["valid"] = "yes"
                p.reading.append("authorisation still valid")
        if "cma_auditors" in p.datasets:
            for c in ("local", "foreign"):
                if re.search(rf"\b{c}\b", tl):
                    p.filters["nationality"] = c.capitalize()
                    p.reading.append(f"{c} auditors")
        if "fcsc_cpi_annual" in p.datasets:
            for code, pat in CPI_WORDS.items():
                if re.search(pat, tl):
                    p.filters["category"] = code
                    p.reading.append(f"category {code}")
                    tl = re.sub(pat, " ", tl)
                    break
            y = re.search(r"\b(20\d{2}|19\d{2})\b", tl)
            if y:
                p.filters["year"], p.since, p.until = y.group(1), None, None
                p.reading = [r for r in p.reading if r != when] + [f"year {y.group(1)}"]
                tl = tl.replace(y.group(1), " ")
        m = re.search(r"\blicensed (?:in|since) (20\d{2})\b", tl)
        if m and "cma_licensed_companies" in p.datasets:
            p.filters["licensed_since_year"] = m.group(1)
            p.since = p.until = None
            p.reading = [r for r in p.reading if r != when] + [f"licensed in {m.group(1)}"]

        # 6. leftover words -> full-text search inside the chosen datasets
        rest = tl.replace(matched_date.lower(), " ") if matched_date else tl
        for ds in p.datasets:
            for w in sorted(self.kw.get(ds, []), key=len, reverse=True):
                rest = re.sub(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", " ", rest)
        if act:
            rest = re.sub(re.escape(act.lower()), " ", rest)
        for v in p.filters.values():
            rest = re.sub(r"\b" + re.escape(str(v).lower()) + r"\b", " ", rest)
        words = [w for w in re.findall(r"[\w؀-ۿ]+", rest) if len(w) > 2 and w not in STOP
                 and not w.isdigit() and not re.fullmatch(r"(expired|valid|current|active|public|private|local|"
                                                         r"foreign|adgm|difc|allowed|permitted|warned|licensed)", w)]
        if words and p.op != "find_datasets":
            p.text = " ".join(words[:6])
            p.reading.append(f"mentioning “{p.text}”")
        if not p.datasets and p.op == "changes":
            p.reading.append("all datasets")
        elif not p.datasets and p.op != "find_datasets":
            p.confident = False                           # nothing placed: search everything, maybe ask the AI
            p.op = "list" if p.op == "latest" else p.op
            if not p.text:
                p.text = " ".join(w for w in re.findall(r"[\w؀-ۿ]+", tl) if len(w) > 2 and w not in STOP)[:80]
            p.reading.append("all datasets")
        return p

    def _activity(self, q: str) -> str | None:
        """A licensed activity named in the question ('portfolio management', 'introduction')."""
        ql = q.lower()
        stem = lambda w: re.sub(r"(ing|ment|s)$", "", w)
        qwords = {stem(w) for w in re.findall(r"[a-z]+", ql)}
        best = None
        for a in self.activities:
            aw = [stem(w) for w in re.findall(r"[a-z]+", a.lower()) if w not in STOP and len(w) > 2]
            if not aw or re.fullmatch(r"activity \d+", a.lower()):
                continue
            if a.lower() in ql or all(w in qwords for w in aw):
                if not best or len(a) > len(best):
                    best = a
        return best


# ---------------------------------------------------------------------------- validation of any plan
def validate_plan(raw: dict, registry: Registry, allowed_filters) -> Plan | None:
    """Accept a plan (from the AI, the API or MCP) only if every part is known. Returns None if not usable."""
    try:
        op = raw.get("op", "list")
        if op not in OPS:
            return None
        ids = set(registry.ids())
        datasets = [d for d in (raw.get("datasets") or []) if d in ids]
        if raw.get("datasets") and len(datasets) != len(raw["datasets"]):
            return None
        filters = {}
        for k, v in (raw.get("filters") or {}).items():
            if not datasets or not all(k in allowed_filters(d) for d in datasets):
                return None
            filters[k] = str(v)[:80]
        def iso(x):
            if not x:
                return None
            return date.fromisoformat(str(x)[:10]).isoformat()
        p = Plan(op=op, datasets=datasets, text=str(raw.get("text") or "")[:120], since=iso(raw.get("since")),
                 until=iso(raw.get("until")), filters=filters, limit=max(1, min(int(raw.get("limit") or 20), 200)))
        p.reading = [registry.get(d).official_name for d in datasets] + \
                    ([f"since {p.since}"] if p.since else []) + ([f"until {p.until}"] if p.until else []) + \
                    [f"{k}: {v}" for k, v in filters.items()] + ([f"mentioning “{p.text}”"] if p.text else [])
        return p
    except (TypeError, ValueError, KeyError):
        return None

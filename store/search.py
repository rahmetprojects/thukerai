"""
store/search.py - find a company, person or website in the official lists, and say HOW sure we are
==================================================================================================

WHAT IT DOES
    Given a name (and/or a web address) a person mentioned, looks it up in the CURRENT snapshot of the
    chosen datasets and returns candidate records, each labelled with an explicit match type:

        exact        the same name, letter for letter (ignoring case)                        strong
        domain       the same web domain (e.g. market10.net)                                  strong
        normalized   the same after removing punctuation / legal forms (LLC, Ltd, ذ.م.م ...)  strong
        probable     very similar, sharing a distinctive word  (score >= 0.90)               needs checking
        possible     somewhat similar (score >= 0.80)                                        weak; never an accusation
        none         nothing close

WHY SO CAREFUL
    A false "this company is on a warning list" can defame an innocent firm; a false "not found" can
    reassure someone about a scam. So: generic words ("financial", "capital", "investment", "services"...)
    can never create a match on their own, near-matches are labelled as such, and absence is reported
    as "no record found in this list" - never as "safe".

PERFORMANCE
    The index of the current snapshots is loaded once and cached; the cache key is the set of snapshot
    ids, so publishing a new snapshot automatically invalidates it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .db import Database
from .names import core_name, domains_in, normalize_name

GENERIC = {"financial", "finance", "services", "service", "capital", "investment", "investments", "invest",
           "markets", "market", "group", "trading", "trade", "global", "international", "holding", "holdings",
           "consultancy", "consultants", "consulting", "management", "uae", "dubai", "abu", "dhabi", "securities",
           "brokerage", "broker", "brokers", "asset", "assets", "fund", "funds", "the", "and", "of", "for", "in",
           "partners", "advisors", "advisers", "advisory", "wealth", "fx", "forex", "crypto", "digital", "bank",
           "commercial", "enterprises", "general", "middle", "east", "mena", "gulf", "emirates", "national",
           "للاستثمار", "الاستثمار", "المالية", "للخدمات", "الخدمات", "الاوراق", "للاوراق", "المالي", "شركه"}
STRONG = ("exact", "domain", "normalized")
ORDER = {"exact": 0, "domain": 1, "normalized": 2, "probable": 3, "possible": 4}


@dataclass
class Candidate:
    record_key: str
    matched_value: str
    match_type: str
    score: float
    record: dict = field(default_factory=dict)


def _distinctive(core: str) -> set[str]:
    return {t for t in core.split() if t not in GENERIC and (len(t) >= 3 or any(ch.isdigit() for ch in t))}


def _numbers(core: str) -> set[str]:
    return {t for t in core.split() if any(ch.isdigit() for ch in t)}


def _shares_distinctive(q: set[str], r: set[str]) -> bool:
    """True if a distinctive word is shared; long words may differ by a typo ('Exampel' ~ 'Example')."""
    if q & r:
        return True
    return any(len(a) >= 5 and len(b) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.8 for a in q for b in r)


def _score(q_core: str, r_core: str) -> float:
    if not q_core or not r_core:
        return 0.0
    ratio = SequenceMatcher(None, q_core, r_core).ratio()
    qt, rt = set(q_core.split()), set(r_core.split())
    jacc = len(qt & rt) / len(qt | rt) if qt | rt else 0
    contain = 0.9 if len(qt) >= 2 and qt <= rt else 0.0      # every word they said is in the official name
    single = 0.82 if len(qt) == 1 and len(q_core) >= 5 and q_core in rt else 0.0   # one rare word: only "possible"
    return max(ratio, jacc, contain, single)


class EntitySearch:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._key: tuple = ()
        self._rows: dict[str, list[dict]] = {}

    def _index(self, dataset_ids: list[str]) -> dict[str, list[dict]]:
        snaps = {d: (self.db.current_snapshot(d) or {}).get("id") for d in dataset_ids}
        key = tuple(sorted((d, s or "") for d, s in snaps.items()))
        with self._lock:
            if key != self._key:
                rows = self.db.index_rows([s for s in snaps.values() if s])
                by: dict[str, list[dict]] = {d: [] for d in dataset_ids}
                for r in rows:
                    by.setdefault(r["dataset_id"], []).append(r)
                self._rows, self._key = by, key
            return self._rows

    def search(self, dataset_id: str, name: str = "", domain: str = "", limit: int = 5) -> list[Candidate]:
        """Candidates in ONE dataset's current snapshot, best first. Empty list = nothing close."""
        rows = self._index([dataset_id]).get(dataset_id, [])
        q_raw = (name or "").strip()
        q_norm, q_core = normalize_name(q_raw), core_name(q_raw)
        q_dist = _distinctive(q_core)
        q_domains = set(domains_in(domain or "") + domains_in(q_raw))
        best: dict[str, Candidate] = {}

        def keep(c: Candidate) -> None:
            cur = best.get(c.record_key)
            if cur is None or (ORDER[c.match_type], -c.score) < (ORDER[cur.match_type], -cur.score):
                best[c.record_key] = c

        for r in rows:
            if r["kind"] == "domain":
                if r["value"] in q_domains or any(r["value"].endswith("." + d) or d.endswith("." + r["value"]) for d in q_domains):
                    keep(Candidate(r["record_key"], r["value"], "domain", 1.0))
                continue
            if not q_norm:
                continue
            if r["value"].casefold().strip() == q_raw.casefold():
                keep(Candidate(r["record_key"], r["value"], "exact", 1.0))
            elif r["norm"] == q_norm or (q_core and len(q_core) >= 4 and r["core"] == q_core):
                keep(Candidate(r["record_key"], r["value"], "normalized", 0.98))
            elif q_dist and _shares_distinctive(q_dist, _distinctive(r["core"])) \
                    and _numbers(q_core) == _numbers(r["core"]):            # "Fund 3" is never "Fund 30"
                s = _score(q_core, r["core"])
                if s >= 0.90:
                    keep(Candidate(r["record_key"], r["value"], "probable", round(s, 3)))
                elif s >= 0.80:
                    keep(Candidate(r["record_key"], r["value"], "possible", round(s, 3)))
        out = sorted(best.values(), key=lambda c: (ORDER[c.match_type], -c.score))[:limit]
        if out:
            snap = (self.db.current_snapshot(dataset_id) or {}).get("id")
            recs = {r["record_key"]: r for r in self.db.records_by_keys(snap, [c.record_key for c in out])}
            for c in out:
                c.record = recs.get(c.record_key, {})
        return out

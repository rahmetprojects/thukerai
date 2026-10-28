"""
trust/report.py - the Trust Report: every signal checked against every official list, as cited FACTS
====================================================================================================

WHAT IT DOES
    TrustService.check(text) ->
        {
          "query":     what was pasted (trimmed),
          "signals":   what we looked up (trust/signals.py),
          "headline":  {"tone", "text"}  the one line a person reads first,
          "facts":     [ {tone, text, detail, signal, match, matched, dataset_id, dataset, record_key, date,
                          source_page, fetched_at, extra} ... ]   strongest first,
          "not_proven": what a "not found" (or a match) does NOT establish - always present,
          "lists_checked", "unavailable": coverage of this answer,
          "took_ms":   how long the check took
        }

TONES (drive colour on every screen; they are not verdicts)
    alert     an official warning, an impersonated website, an expired authorisation, a published penalty
    caution   a SIMILAR name on a warning or licence list: needs a human look, not an accusation
    record    an official record exists (licensed, authorised, registered): a fact, not a guarantee
    none      nothing found on the lists checked

RULES
    * Facts, never verdicts: the words "safe", "scam", "legit" are never produced here.
    * Every fact names its dataset, record, publication date, official page and the time our copy was fetched.
    * Matching is code (store/search.py); no AI decides whether something matches.
    * A website the person was given that differs from the licensed firm's official website is an ALERT:
      impersonating real licensed firms is a common trick.
"""
from __future__ import annotations

import time
from datetime import date

from tools.retrieval import ALWAYS, ENTITY_DATASETS, Retrieval
from store.search import STRONG

from .signals import Signal, extract, same_phone

TONE_ORDER = {"alert": 0, "caution": 1, "record": 2, "none": 3}
REGISTRATION_LABEL = {
    "cma_local_funds": "a local investment fund", "cma_passported_funds": "a passported fund (allowed to be marketed "
    "in the UAE mainland)", "cma_eosg_funds": "an end-of-service gratuity fund", "cma_auditors": "an auditor of "
    "public companies and funds", "cma_assembly_organizers": "an organiser of general assemblies",
    "cma_appraisers": "an appraiser of in-kind contributions"}


def _d(iso: str | None) -> str:
    """'2026-10-22' -> '22 Oct 2026' (the format people read)."""
    if not iso or iso == "None":
        return ""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%-d %b %Y")
    except ValueError:
        return str(iso)


def _label(domain: str) -> str:
    """'4tglobal-invest.co.ae' -> '4tglobal-invest' (the part people recognise, without the endings)."""
    parts = domain.lower().split(".")
    while len(parts) > 1 and (len(parts[-1]) <= 3 or parts[-1] in ("info", "online", "site", "store", "capital")):
        parts = parts[:-1]
    return ".".join(parts)


class TrustService:
    def __init__(self, retrieval: Retrieval, llm=None):
        self.r, self.db, self.llm = retrieval, retrieval.db, llm
        self._phones: tuple[str, list] | None = None        # (snapshot id, [(phone, record)]) cache

    # ------------------------------------------------------------------ public
    def check(self, text: str = "", image: bytes | None = None, mime: str = "image/png") -> dict:
        t0 = time.perf_counter()
        notes: list[str] = []
        if image:
            read = self.llm.read_image(image, mime) if self.llm and getattr(self.llm, "can_read_images", False) else None
            if read:
                text = (text + "\n" + read).strip()
                notes.append("The text was read from your screenshot by the AI helper; what to look up was then "
                             "chosen by code from that text.")
            else:
                notes.append("Screenshots need the AI helper (GEMINI_API_KEY or ANTHROPIC_API_KEY); please paste "
                             "the text instead.")
        signals = extract(text)
        facts: list[dict] = []
        unavailable: set[str] = set()
        for s in signals:
            got = self._check_signal(s, unavailable)
            facts += got or [self._none(s)]
        facts = self._merge(facts)
        facts.sort(key=lambda f: TONE_ORDER[f["tone"]])
        lists = [d for d in ENTITY_DATASETS + self._onboarded() if d not in unavailable]
        return {"query": text[:4000], "signals": [s.as_dict() for s in signals],
                "headline": self._headline(facts, signals, len(lists), unavailable),
                "facts": facts, "not_proven": ALWAYS, "lists_checked": len(lists),
                "unavailable": sorted(unavailable), "notes": notes,
                "took_ms": round((time.perf_counter() - t0) * 1000, 1)}

    # ------------------------------------------------------------------ one signal
    def _check_signal(self, s: Signal, unavailable: set) -> list[dict]:
        if s.kind == "phone":
            return self._phone(s)
        res = self.r.check_entity(name=s.value if s.kind in ("name", "handle") else "",
                                  domain=s.value if s.kind == "domain" else "")
        unavailable.update(res["unavailable"])
        out: list[dict] = []
        for ds, r in res["results"].items():
            for c in r["candidates"][:2]:
                f = self._fact(s, ds, r, c, res["flags"])
                if f:
                    out.append(f)
        for ds in self._onboarded():                        # sources added by the onboarding agent
            r = self.r.search(ds, s.value if s.kind in ("name", "handle") else "", s.value if s.kind == "domain" else "")
            if not r["searched"]:
                unavailable.add(ds)
                continue
            for c in r["candidates"][:1]:
                if c["match_type"] in STRONG:
                    rec = {k: c.get(k) for k in ("record_key", "name", "date", "status", "category", "details", "domains")}
                    out.append(self._make(s, "record", f"{'The website ' + s.value if s.kind == 'domain' else '“' + s.shown + '”'}"
                                          f" appears in “{r['name']}” published by {r['publisher']}"
                                          + ("" if c["name"].casefold() == s.shown.casefold() else f": “{c['name']}”")
                                          + (f", status {c['status']}." if c.get("status") else "."), ds, rec,
                                          c["match_type"], detail="Added through the onboarding agent; the meaning of "
                                          "each column was approved by a person, not the publisher."))
        if s.kind == "domain" and not any(f["tone"] in ("alert", "record") for f in out):
            out += self._lookalike(s)
        if s.kind == "handle":                              # a handle quoted inside a warning's text
            for h in self.db.fts_search(f'"{s.value}"', ["cma_warnings"], limit=3):
                if not any(f["record_key"] == h["record_key"] and f["dataset_id"] == "cma_warnings" for f in out):
                    rec = self.db.records_by_keys(h["snapshot_id"], [h["record_key"]])
                    if rec:
                        out.append(self._make(s, "alert", f"The account @{s.value} is mentioned in a CMA warning "
                                              f"published {_d(rec[0]['date'])}.", "cma_warnings", rec[0], "mention",
                                              detail=rec[0]["details"].get("statement", "")))
        return self._dedupe(out)

    def _fact(self, s: Signal, ds: str, r: dict, c: dict, flags: list[str]) -> dict | None:
        strong = c["match_type"] in STRONG
        rec = {"record_key": c["record_key"], "name": c["name"], "date": c["date"], "status": c["status"],
               "category": c["category"], "details": c["details"], "domains": c.get("domains", [])}
        what = f"The website {s.value}" if s.kind == "domain" else f"“{s.shown}”"
        if ds == "cma_warnings":
            if strong:
                return self._make(s, "alert", f"{what} is named in a CMA warning published {_d(c['date'])}.", ds, rec,
                                  c["match_type"], detail=c["details"].get("statement", ""))
            return self._make(s, "caution", f"A similar name is in a CMA warning published {_d(c['date'])}. Check the "
                              "exact name and website before doing anything.", ds, rec, c["match_type"],
                              detail=c["details"].get("statement", ""))
        if ds == "cma_licensed_companies":
            if not strong:
                return self._make(s, "caution", f"A similar name, “{c['name']}”, is on the CMA licensed list. A "
                                  "similar name is not the same firm.", ds, rec, c["match_type"])
            d = c["details"]
            acts = c.get("permitted_activities") or []
            extra = {"licence_code": d.get("licence_code"), "website": d.get("website"), "activities": acts,
                     "status": c["status"], "licensed_since": d.get("licensed_since_year")}
            f = self._make(s, "record", f"{what} matches a CMA-licensed company: “{c['name']}”, licence "
                           f"{d.get('licence_code')}, status {c['status'] or 'not given'}.", ds, rec, c["match_type"],
                           detail=("Licensed for: " + "; ".join(acts) + ".") if acts else "", extra=extra)
            mism = next((x for x in flags if x.startswith("website_mismatch")), None)
            if mism and s.kind == "domain":
                return self._make(s, "alert", f"{s.value} is NOT the website of the licensed firm it resembles; the "
                                  f"official website is {', '.join(c.get('domains') or [d.get('website') or '?'])}. "
                                  "Copying real firms is a common trick.", ds, rec, c["match_type"], extra=extra)
            return f
        if ds == "cma_finfluencers":
            if not strong:
                return None                                 # a similar influencer name says little; skip
            d = c["details"]
            valid = d.get("valid_until")
            expired = bool(valid and str(valid) < date.today().isoformat())
            return self._make(s, "alert" if expired else "record",
                              f"{what} matches CMA-authorised finfluencer “{c['name']}”, registration "
                              f"{d.get('registration_no')}, " + (f"but the authorisation EXPIRED on {_d(valid)}."
                                                                if expired else f"valid until {_d(valid) or 'not given'}."),
                              ds, rec, c["match_type"],
                              detail="Authorisation lets them promote; it is not a recommendation of what they promote.",
                              extra={"valid_until": valid, "social_media": d.get("social_media", [])})
        if ds == "cma_violations":
            if not strong:
                return None
            return self._make(s, "caution", f"The CMA published a penalty against a matching name on {_d(c['date'])}: "
                              f"{c['details'].get('decision') or c['category']}.", ds, rec, c["match_type"],
                              detail=c["details"].get("violation", ""))
        if ds in REGISTRATION_LABEL and strong:
            return self._make(s, "record", f"{what} is registered with the CMA as {REGISTRATION_LABEL[ds]}: "
                              f"“{c['name']}”.", ds, rec, c["match_type"],
                              detail="A registration is not a licence to manage your money.")
        return None

    def _onboarded(self) -> list[str]:
        return [d.id for d in self.r.reg.datasets if d.kind == "remote_table"]

    def _lookalike(self, s: Signal) -> list[dict]:
        """'4tglobal-invest.net' contains the official '4tglobal' of a licensed firm but is not its website."""
        label = _label(s.value)
        out = []
        for official, rec in self._licensed_domains():
            core = _label(official)
            if len(core) >= 5 and core in label and s.value != official:
                out.append(self._make(s, "alert", f"{s.value} looks like the website of CMA-licensed “{rec['name']}”, "
                                      f"but that firm's official website is {official}. Copying real firms is a "
                                      "common trick.", "cma_licensed_companies", rec, "lookalike",
                                      extra={"official_website": official,
                                             "licence_code": rec["details"].get("licence_code")}))
        return out[:2]

    def _licensed_domains(self) -> list:
        snap = (self.db.current_snapshot("cma_licensed_companies") or {}).get("id")
        if not getattr(self, "_lic", None) or self._lic[0] != snap:
            recs = self.db.records("cma_licensed_companies", snap) if snap else []
            self._lic = (snap, [(d, r) for r in recs for d in r.get("domains") or []])
        return self._lic[1]

    def _phone(self, s: Signal) -> list[dict]:
        out = []
        for phone, rec in self._phone_index():
            if same_phone(phone, s.value):
                out.append(self._make(s, "record", f"The number {s.shown} is listed for CMA-authorised finfluencer "
                                      f"“{rec['name']}”, registration {rec['details'].get('registration_no')}.",
                                      "cma_finfluencers", rec, "exact",
                                      detail="A listed number does not prove who is calling you: call back on the "
                                             "number in the official record."))
        return out

    def _phone_index(self) -> list:
        snap = (self.db.current_snapshot("cma_finfluencers") or {}).get("id")
        if not self._phones or self._phones[0] != snap:
            recs = self.db.records("cma_finfluencers", snap) if snap else []
            self._phones = (snap, [(r["details"].get("phone") or "", r) for r in recs if r["details"].get("phone")])
        return self._phones[1]

    # ------------------------------------------------------------------ helpers
    def _make(self, s: Signal, tone: str, text: str, ds: str, rec: dict, match: str, detail: str = "",
              extra: dict | None = None) -> dict:
        st = self.r.dataset_state(ds)
        return {"tone": tone, "text": text, "detail": detail or "", "signal": s.as_dict(), "match": match,
                "matched": rec.get("name"), "dataset_id": ds, "dataset": st["name"], "record_key": rec["record_key"],
                "date": rec.get("date"), "source_page": (rec.get("details") or {}).get("source_page") or st["source_page"],
                "fetched_at": st["fetched_at"], "stale": st["status"] == "stale", "extra": extra or {}}

    def _none(self, s: Signal) -> dict:
        what = f"the website {s.value}" if s.kind == "domain" else (f"@{s.value}" if s.kind == "handle" else
                                                                     s.shown if s.kind == "phone" else f"“{s.shown}”")
        return {"tone": "none", "text": f"No record of {what} on the official lists checked.", "detail": "",
                "signal": s.as_dict(), "match": "none", "matched": None, "dataset_id": None, "dataset": None,
                "record_key": None, "date": None, "source_page": None, "fetched_at": None, "stale": False, "extra": {}}

    @staticmethod
    def _merge(facts: list[dict]) -> list[dict]:
        """Two signals hitting the SAME record (a website and a name in one warning) become one fact."""
        out, by = [], {}
        for f in facts:
            k = (f["dataset_id"], f["record_key"], f["tone"]) if f["dataset_id"] else None
            if k and k in by:
                first = by[k]
                first.setdefault("also", []).append(f["signal"]["shown"])
                continue
            if k:
                by[k] = f
            out.append(f)
        for f in out:
            if f.get("also"):
                f["detail"] = (f"Also matched by: {', '.join(dict.fromkeys(f['also']))}. " + f["detail"]).strip()
        return out

    @staticmethod
    def _dedupe(facts: list[dict]) -> list[dict]:
        seen, out = set(), []
        for f in sorted(facts, key=lambda f: TONE_ORDER[f["tone"]]):
            k = (f["dataset_id"], f["record_key"])
            if k not in seen:
                seen.add(k)
                out.append(f)
        return out

    @staticmethod
    def _headline(facts: list[dict], signals: list, n_lists: int, unavailable: set) -> dict:
        if not signals:
            return {"tone": "none", "text": "Paste a company name, website, social account or phone number to check."}
        top = facts[0] if facts else None
        if top and top["tone"] in ("alert", "caution", "record"):
            return {"tone": top["tone"], "text": top["text"]}
        if n_lists == 0:
            return {"tone": "none", "text": "The official lists are not available right now, so nothing could be checked."}
        return {"tone": "none", "text": f"Not found on the {n_lists} official lists checked. That does not prove "
                                        "it is licensed or safe."}

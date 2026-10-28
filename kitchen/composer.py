"""
kitchen/composer.py - the COMPOSER: builds the served card from evidence only, with numbered citations
=====================================================================================================

WHAT A CARD IS (the JSON the screen renders; every type shares these keys)
    type        fact | table | number | datasets | calculation | explanation | ask | proposal | cannot_answer |
                welcome | safety
    tone        alert | caution | record | none | neutral        (colour on screen; never a verdict)
    headline    one sentence, cited
    sentences   [{text, cites: [n]}]   every factual sentence carries citation numbers
    meaning     {text, cites}          "what this means", cited to the glossary (the explainer)
    offers      [{term, label}]        "Explain: passported fund" chips
    citations   [{n, kind, label, dataset_id, dataset, record_key, date, source_page, fetched_at, detail}]
                kind: record | dataset | search | calculation | glossary
    speech      what is said aloud in voice mode (at most three short sentences)
    + type-specific: facts, table, number, calc, explain, ask, proposal, api, followups, not_proven

Cards are built by templates from the specialists' evidence, so nothing in them can come from outside the data.
"""
from __future__ import annotations

import re
from datetime import date

from .glossary import TERMS


def _d(iso) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%-d %b %Y")
    except (ValueError, TypeError):
        return ""


class Book:
    """Numbered citations, de-duplicated."""
    def __init__(self):
        self.items: list[dict] = []
        self._keys: dict = {}

    def add(self, kind: str, label: str, **f) -> int:
        key = (kind, f.get("dataset_id"), f.get("record_key"), label if kind in ("glossary", "calculation") else None)
        if key in self._keys:
            return self._keys[key]
        n = len(self.items) + 1
        self.items.append({"n": n, "kind": kind, "label": label, **{k: f.get(k) for k in (
            "dataset_id", "dataset", "record_key", "date", "source_page", "fetched_at", "detail")}})
        self._keys[key] = n
        return n


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", text or "") if s.strip()]


def _speech(headline: str, meaning: str | None, offers: list[dict]) -> str:
    parts = [headline]
    if meaning:
        parts.append(_sentences(meaning)[0])
    if offers:
        parts.append(f"Would you like me to explain what “{offers[0]['label']}” means?")
    return " ".join(parts)


class Composer:
    def __init__(self, explainer, retrieval):
        self.x, self.r = explainer, retrieval

    def _gloss(self, b: Book, key: str) -> int:
        return b.add("glossary", f"Explanation: {TERMS[key]['term']}", detail="From the assistant's glossary, not an official record")

    def _dataset(self, b: Book, ds: str) -> int:
        st = self.r.dataset_state(ds)
        return b.add("dataset", st["name"], dataset_id=ds, dataset=st["name"], source_page=st["source_page"],
                     fetched_at=st["fetched_at"], detail=f"{st['rows']} records in our copy")

    # ------------------------------------------------------------------ fact card (the Trust Report)
    def fact(self, rep: dict, explained: set, promise_calc=None) -> dict:
        b = Book()
        facts = []
        for f in rep["facts"]:
            if f["dataset_id"]:
                n = b.add("record", f["matched"] or f["record_key"], dataset_id=f["dataset_id"], dataset=f["dataset"],
                          record_key=f["record_key"], date=f["date"], source_page=f["source_page"],
                          fetched_at=f["fetched_at"], detail=f["detail"])
            else:
                n = b.add("search", f"Searched {rep['lists_checked']} official lists", detail=", ".join(rep["unavailable"]) and
                          f"Unavailable now: {', '.join(rep['unavailable'])}")
            facts.append({**f, "cites": [n]})
        sentences = []
        if promise_calc is not None:
            o = promise_calc.outputs
            n = b.add("calculation", "Calculator: promised return", detail=promise_calc.formula)
            rate, period = promise_calc.inputs["rate_pct"], promise_calc.inputs["period"]
            sentences.append({"text": f"{rate:g}% a {period}, if compounded, is about {o['compounded_per_year_pct']:g}% a year: "
                                      f"10,000 would become {o['example_after_one_year']:,.0f} in one year. No real "
                                      "investment can promise that.", "cites": [n]})
        head_cites = facts[0]["cites"] if facts else []
        meaning, key = self.x.meaning_for_fact(rep, promise_calc)
        mn = self._gloss(b, key)
        text = " ".join([rep["headline"]["text"], meaning] + [f["text"] for f in facts] + [s["text"] for s in sentences])
        offers = self.x.offers(text, [f["dataset_id"] for f in facts if f["dataset_id"]], explained)
        tone = rep["headline"]["tone"]
        return {"type": "fact", "tone": tone, "headline": rep["headline"]["text"], "headline_cites": head_cites,
                "facts": facts, "sentences": sentences, "meaning": {"text": meaning, "cites": [mn]}, "offers": offers,
                "not_proven": rep["not_proven"], "signals": rep["signals"], "citations": b.items,
                "api": {"url": f"/api/v1/check?q={rep['query'][:200]}"},
                "followups": (["What should I do next?"] if tone in ("alert", "caution") else []) + ["Check another name"],
                "speech": _speech(rep["headline"]["text"], meaning, offers)}

    # ------------------------------------------------------------------ data cards (the explorer)
    def data(self, res: dict, explained: set) -> dict:
        b = Book()
        plan = res["plan"]
        ds_ids = [d["id"] for d in res["datasets"]]
        ds_cites = [self._dataset(b, d) for d in ds_ids]
        mean = self.x.meaning_for_datasets(ds_ids)
        meaning = None
        if mean:
            meaning = {"text": mean[0], "cites": [self._gloss(b, mean[1])]}
        base = {"headline": res["answer"], "headline_cites": ds_cites, "reading": res["reading"], "api": res["api"],
                "planner": plan["planner"], "citations": b.items}
        if plan["op"] == "find_datasets":
            items = [{**d, "cites": [self._dataset(b, d["id"])]} for d in res["datasets"]]
            card = {**base, "type": "datasets", "tone": "neutral", "datasets": items}
        else:
            rows = []
            for x in res["records"]:
                n = b.add("record", x["name"] or x["record_key"], dataset_id=x["dataset_id"], dataset=x["dataset"],
                          record_key=x["record_key"], date=x["date"], source_page=x["source_page"], fetched_at=x["fetched_at"])
                rows.append({**x, "cites": [n]})
            card = {**base, "type": "number" if plan["op"] == "count" else "table", "tone": "neutral",
                    "number": res["count"] if plan["op"] == "count" else None, "rows": rows,
                    "changes": res.get("changes")}
        card["citations"] = b.items
        card["meaning"] = meaning
        text = res["answer"] + " " + " ".join(r.get("name") or "" for r in res["records"][:10]) + " " + (meaning or {}).get("text", "")
        card["offers"] = self.x.offers(text, ds_ids, explained | ({mean[1]} if mean else set()))
        card["sentences"] = []
        card["followups"] = ["Download as CSV", "Show the query as code"]
        card["speech"] = _speech(res["answer"], meaning["text"] if meaning else None, card["offers"])
        return card

    # ------------------------------------------------------------------ calculation (the voice guide's coordinator)
    def calculation(self, tr, explained: set) -> dict:
        b = Book()
        ev = tr.evidence or {}
        if tr.mode in ("ask", "clarify") or not ev.get("calculations"):
            return {"type": "ask", "tone": "neutral", "headline": tr.reply, "headline_cites": [],
                    "ask": {"question": tr.reply, "quick_replies": tr.quick_replies, "slot": tr.question_slot},
                    "noted": tr.facts, "sentences": [], "citations": [], "offers": [], "speech": tr.reply,
                    "meaning": None}
        calc_n = {c["tool"]: b.add("calculation", f"Calculator: {c['tool'].replace('_', ' ')}", detail=c.get("formula", ""))
                  for c in ev["calculations"]}
        data_n = [self._dataset(b, d["dataset_id"]) for d in ev.get("data", [])]
        main = [next(iter(calc_n.values()))]
        infl = [n for t, n in calc_n.items() if "inflation" in t] + data_n

        def cites_for(sentence: str) -> list[int]:
            """Each sentence cites what it rests on: inflation figures -> the CPI calculation and the FCSC dataset."""
            if re.search(r"inflation|FCSC|prices|costs keep rising", sentence, re.I) and infl:
                return list(dict.fromkeys(infl + (main if re.search(r"months|years", sentence) else [])))
            return main
        sents = [x for x in _sentences(tr.reply) if not re.match(r"(I noted|If I misheard)\b", x)] or _sentences(tr.reply)
        head, rest = (sents[0] if sents else tr.reply), sents[1:]
        offers = self.x.offers(tr.reply, [d["dataset_id"] for d in ev.get("data", [])], explained)
        return {"type": "calculation", "tone": "neutral", "headline": head, "headline_cites": cites_for(head),
                "sentences": [{"text": s, "cites": cites_for(s)} for s in rest], "calcs": ev["calculations"],
                "assumptions": ev.get("assumptions", []), "limitations": ev.get("limitations", []), "noted": tr.facts,
                "meaning": None, "offers": offers, "citations": b.items, "followups": tr.quick_replies[:3],
                "speech": " ".join(sents[:3])}

    # ------------------------------------------------------------------ explanation
    def explanation(self, term: str | None, lang: str) -> dict:
        b = Book()
        if not term:
            known = ", ".join(TERMS[k]["term"] for k in list(TERMS)[:8])
            return {"type": "cannot_answer", "tone": "none", "headline": "I don't have an explanation for that term yet.",
                    "headline_cites": [], "sentences": [{"text": f"I can explain, for example: {known}.", "cites": []}],
                    "meaning": None, "offers": [], "citations": [], "speech": "I don't have an explanation for that term yet."}
        e = self.x.explanation(term)
        g = self._gloss(b, term)
        plain = e["plain_ar"] if lang == "ar" and e["plain_ar"] else e["plain"]
        data = [{**d, "cites": [self._dataset(b, d["dataset_id"])]} for d in e["datasets"]]
        sentences = [{"text": s, "cites": [g]} for s in _sentences(plain)]
        if e["example"]:
            sentences.append({"text": f"For example: {e['example']}", "cites": [g]})
        return {"type": "explanation", "tone": "neutral", "headline": e["term"][0].upper() + e["term"][1:],
                "headline_cites": [g], "sentences": sentences, "explain": {"term": term, "datasets": data},
                "meaning": None, "offers": [{"term": k, "label": TERMS[k]["term"]} for k in e["related"]],
                "citations": b.items, "followups": [f"Show me the {d['name']}" for d in data[:2]],
                "speech": " ".join(_sentences(plain)[:2])}

    # ------------------------------------------------------------------ the rest
    @staticmethod
    def safety(script: str, level: str) -> dict:
        return {"type": "safety", "tone": "alert", "headline": "Stop. Protect your money first." if level == "STOP"
                else "Act quickly: there is still a chance.", "headline_cites": [],
                "sentences": [{"text": s, "cites": []} for s in _sentences(script)], "meaning": None, "offers": [],
                "citations": [], "followups": ["Check the company's name", "Help me talk to my bank"], "speech": script}

    @staticmethod
    def welcome() -> dict:
        return {"type": "welcome", "tone": "neutral", "headline": "Ask me about any company, website, account, fund or "
                "money decision. I answer from official UAE data and show where every fact comes from.",
                "headline_cites": [], "sentences": [], "meaning": None, "offers": [], "citations": [],
                "followups": ["Is market10.net licensed?", "Which firms were warned against this month?",
                              "What is a passported fund?", "I have 300,000 saved. How long will it last?"],
                "speech": "Ask me about any company, website, fund or money decision."}

    @staticmethod
    def cannot(text: str, searched: int) -> dict:
        return {"type": "cannot_answer", "tone": "none", "headline": "I could not find an answer to that in the official data.",
                "headline_cites": [], "sentences": [{"text": f"I searched {searched} datasets for “{text[:80]}”.", "cites": []},
                                                    {"text": "Try a company or website name, a type of fund, or a question "
                                                     "such as \"warnings this month\".", "cites": []}],
                "meaning": None, "offers": [], "citations": [], "followups": ["What data do you have?"],
                "speech": "I could not find an answer to that in the official data."}

    @staticmethod
    def proposal(d: dict, token: str) -> dict:
        props = d["proposals"][:2]
        if not d["allowed"]:
            head = (f"I can only add data from official UAE government websites, and {d['url']} is not on that list. "
                    "Nothing was fetched.")
        else:
            head = (f"I found {len(d['proposals'])} table(s) behind that page. Check how I propose to read it, then approve."
                    if props else "I could not find a table I can read behind that page.")
        return {"type": "proposal", "tone": "neutral", "headline": head, "headline_cites": [], "sentences": [],
                "proposal": {"discovery": token, "steps": d["steps"], "proposals": props, "allowed": d["allowed"]},
                "meaning": None, "offers": [], "citations": [], "speech": head}

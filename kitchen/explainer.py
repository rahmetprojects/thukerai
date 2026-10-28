"""
kitchen/explainer.py - the EXPLAINER: what an answer means, and offers to explain the jargon in it
=================================================================================================

WHAT IT DOES
    meaning_for_fact(report)       one plain line under a fact card: what this result means for the person
    meaning_for_datasets(ids)      one plain line under a data card: what these records are
    offers(text, datasets, done)   up to 3 glossary terms that appear in the answer (or belong to the datasets used),
                                   not explained yet in this conversation -> chips "Explain: passported fund"
    explanation(term)              the explanation card body: plain meaning, example, and the datasets where the term
                                   appears with their row counts, official pages and collection times

RULES
    Explanations come only from kitchen/glossary.py and are cited as such (kind "glossary"), separately from the
    official records, so a reader can always tell "what the data says" from "what the word means".
"""
from __future__ import annotations

from .glossary import TERMS, find_terms

MAX_OFFERS = 3


class Explainer:
    def __init__(self, catalog):
        self.catalog = catalog

    # ------------------------------------------------------------------ "what this means"
    @staticmethod
    def meaning_for_fact(report: dict, promise=None) -> tuple[str, str]:
        """(sentence, glossary key) for the strongest fact in a Trust Report (and a promised return, if any)."""
        top = report["facts"][0] if report["facts"] else None
        tone = report["headline"]["tone"]
        if promise is not None and promise.outputs.get("level") in ("extreme", "very high") and tone != "alert":
            return ("This means the promise itself is the warning sign: no real investment can guarantee a return like "
                    "this, whatever the lists say. Do not send money until you have checked the firm with its regulator.",
                    "guaranteed_return")
        ds = top["dataset_id"] if top else None
        if tone == "alert" and ds == "cma_warnings":
            return ("This means the regulator has told investors not to deal with this entity. Do not send money, and "
                    "check the warning on the official page.", "cma_warning")
        if tone == "alert" and top and top["match"] == "lookalike":
            return ("This means the website is borrowing a licensed firm's name. Use only the website on the official "
                    "record, and do not send money through this one.", "lookalike")
        if tone == "alert" and ds == "cma_finfluencers":
            return ("This means the person was authorised to promote financial products, but that authorisation has "
                    "ended.", "finfluencer")
        if tone == "caution":
            return ("This means a similar name is on an official list. A similar name is not the same firm: check the "
                    "exact name and website on the official page before doing anything.", "cma_warning" if ds == "cma_warnings" else "licence")
        if tone == "record" and ds == "cma_licensed_companies":
            return ("This means the firm holds a CMA licence for the activities listed. It does not guarantee your money, "
                    "and it does not prove that the person contacting you works there.", "licence")
        if tone == "record" and ds == "cma_finfluencers":
            return ("This means the person is authorised to promote financial products on social media. It is not a "
                    "recommendation of what they promote.", "finfluencer")
        if tone == "record":
            return ("This means the name is registered with the CMA for the role shown. A registration is not a licence "
                    "to manage your money.", "licence")
        return ("Not being on these lists does not prove a firm is licensed or unlicensed: it may be regulated by the "
                "DFSA, the FSRA, the Central Bank or VARA, or not at all. Ask which regulator licensed them and check "
                "that regulator's own register.", "other_regulators")

    @staticmethod
    def meaning_for_datasets(ids: list[str]) -> tuple[str, str] | None:
        if len(ids) > 2:                                    # a catalogue answer: no single meaning
            return None
        for ds in ids:
            for k, t in TERMS.items():
                if ds in t["datasets"] and t["datasets"][0] == ds:
                    return (t["plain"], k)
        return None

    # ------------------------------------------------------------------ offers
    @staticmethod
    def offers(text: str, datasets: list[str], explained: set) -> list[dict]:
        keys = find_terms(text)
        for ds in (datasets if len(datasets) <= 2 else []):
            keys += [k for k, t in TERMS.items() if t["datasets"] and t["datasets"][0] == ds][:1]   # its main term only
        out = []
        for k in dict.fromkeys(keys):
            if k not in explained and len(out) < MAX_OFFERS:
                out.append({"term": k, "label": TERMS[k]["term"]})
        return out

    # ------------------------------------------------------------------ the explanation itself
    def explanation(self, term: str) -> dict:
        t = TERMS[term]
        data = []
        for ds in t["datasets"]:
            try:
                c = self.catalog.card(ds)
            except KeyError:
                continue
            data.append({"dataset_id": ds, "name": c["name"], "rows": c["state"]["rows"], "source_page": c["source_page"],
                         "fetched_at": c["state"]["fetched_at"], "description": c["description"]})
        return {"term": t["term"], "plain": t["plain"], "plain_ar": t.get("plain_ar", ""), "example": t.get("example", ""),
                "datasets": data, "related": [k for k in find_terms(t["plain"]) if k != term][:2]}

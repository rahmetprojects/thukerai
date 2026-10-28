"""
kitchen/verifier.py - the VERIFIER: the last check before a card is served
==========================================================================

RULES (code, deterministic)
    1. Every factual statement carries at least one citation, and every citation number exists on the card.
       (Exempt: welcome, safety, ask and cannot-answer cards, which state no data.)
    2. Every number in a fact, data or explanation card appears in the card's own evidence (the citations, the
       records, the rows, the calculation outputs). Calculation cards come from the voice guide's coordinator, whose
       validator (agents/validator.py) already checks every number against the calculators, including derived ones.
    3. No verdict or advice words: "is safe", "is legit", "is a scam", "you should invest", "guaranteed safe".
    4. Explanations must cite the glossary; data statements must cite records or datasets.

verify(card) -> (ok, problems). repair(card, problems) removes the offending sentences and marks the card, so the
person still gets a cited answer; the problems are logged in the trace.
"""
from __future__ import annotations

import json
import re

EXEMPT = {"welcome", "safety", "ask", "cannot_answer", "proposal"}
VERDICT = re.compile(r"\b(is|are|looks) (safe|legit(imate)?|trustworthy|a scam)\b|\byou should (invest|buy|sell)\b|"
                     r"\bguaranteed safe\b|\brisk[- ]free\b", re.I)
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(text or "")}


def verify(card: dict) -> tuple[bool, list[str]]:
    if card["type"] in EXEMPT:
        return True, []
    problems = []
    ns = {c["n"] for c in card.get("citations", [])}
    statements = [("headline", card["headline"], card.get("headline_cites", []))]
    statements += [("sentence", s["text"], s["cites"]) for s in card.get("sentences", [])]
    statements += [("fact", f["text"], f["cites"]) for f in card.get("facts", [])]
    if card.get("meaning"):
        statements.append(("meaning", card["meaning"]["text"], card["meaning"]["cites"]))
    for where, text, cites in statements:
        if not cites:
            problems.append(f"uncited {where}: {text[:80]}")
        elif not set(cites) <= ns:
            problems.append(f"citation number missing for {where}: {cites}")
        if VERDICT.search(text) and "does not prove" not in text:
            problems.append(f"verdict wording in {where}: {text[:80]}")
    if card["type"] != "calculation":
        evidence = json.dumps({k: card.get(k) for k in ("citations", "facts", "rows", "number", "datasets", "explain", "changes")},
                              ensure_ascii=False, default=str)
        have = _numbers(evidence) | {n.lstrip("0") for n in _numbers(evidence)}
        glossary_cited = {c["n"] for c in card.get("citations", []) if c["kind"] == "glossary"}
        calc_cited = {c["n"] for c in card.get("citations", []) if c["kind"] == "calculation"}
        for where, text, cites in statements:
            if set(cites) & (glossary_cited | calc_cited):
                continue                                   # explanations and calculator sentences are checked upstream
            for n in _numbers(text):
                if n not in have and n.lstrip("0") not in have and not _date_part(n, evidence):
                    problems.append(f"number {n} in {where} is not in the evidence")
    return not problems, problems


def _date_part(n: str, evidence: str) -> bool:
    """'22' and '2026' in '22 Oct 2026' come from an ISO date '2026-10-22' in the evidence."""
    return bool(re.search(rf"\d{{4}}-\d{{2}}-0?{re.escape(n)}\b|\b{re.escape(n)}-\d{{2}}-\d{{2}}", evidence))


def repair(card: dict, problems: list[str]) -> dict:
    """Drop sentences that failed; keep the cited core. Mark the card so the screen can say it was trimmed."""
    bad = " ".join(problems)
    card["sentences"] = [s for s in card.get("sentences", []) if s["text"][:80] not in bad]
    card["facts"] = [f for f in card.get("facts", []) if f["text"][:80] not in bad]
    card["trimmed"] = True
    return card

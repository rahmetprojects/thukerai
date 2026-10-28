"""
kitchen/front.py - the FRONT AGENT: takes the order and keeps the conversation
=============================================================================

WHAT IT DOES
    FrontAgent.order(session, text) -> Order(kind, text, ...) where kind is one of:
        safety      the Guardian stopped the turn (a scam in progress, money already sent): fixed script, no kitchen
        explain     "what is a passported fund?", or "yes" to an offered explanation
        onboard     "add this dataset: https://bayanat.ae/..." (an official URL + an add verb)
        money       a money question for the calculator (retirement, loans, inflation...), or the continuation of one
        check       a name, website, @handle, phone number or pasted message to check against every list
        data        a question about the data itself (lists, counts, filters, dates, what changed, which datasets)
        followup    "are they licensed?" / "and what about their website?" about the entity just discussed
        welcome     hello / what can you do
        ack         "no thanks" to an offered explanation: acknowledge, nothing else
        unknown     none of the above: the kitchen searches everything, or says what it can do
    Rules decide first (fast, testable). If the rules say "unknown" and an AI helper is configured, the AI may pick
    a kind from the same list; its answer is accepted only if it is one of these kinds.

MEMORY (per conversation, in RAM only)
    last_entity       the last name / website checked (for follow-ups)
    offered           explanation offers made in the last answer (so "yes" knows what to explain)
    explained         terms already explained (not offered again)
    money_active      the calculator is in the middle of asking for facts
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from agents.intents import classify
from agents.slots import NO, YES, entity_name
from guardian.guard import guard
from onboard.fetcher import official
from store.names import domains_in
from trust.signals import extract

from .glossary import by_name

EXPLAIN_RE = re.compile(r"^\s*(what('?s| is| are| does| do)|explain|define|meaning of|tell me (more )?about|ما معنى|ماذا يعني|اشرح)\b", re.I)
ADD_RE = re.compile(r"\b(add|include|onboard|ingest|connect|import|bring in|plug in)\b|أضف", re.I)
HELLO_RE = re.compile(r"^\s*(hi|hello|hey|salam|salaam|marhaba|good (morning|evening)|help|what can you do|who are you|"
                      r"مرحبا|السلام عليكم)\b[\s!.?]*$", re.I)
FOLLOW_RE = re.compile(r"\b(they|them|it|this (company|firm|one)|that (company|firm|one)|their)\b.*\b(licen[cs]ed|warn|legit|"
                       r"real|regulated|authori[sz]ed|fined|penal|website|registered)|\b(licen[cs]ed|warned|regulated)\b.*\b(they|it)\b",
                       re.I)
COMPANY_RE = re.compile(r"\b(L\.?L\.?C|LTD|LIMITED|P\.?J\.?S\.?C|INC|FZ-?LLC|FZE|FZCO|PLC|DMCC)\b\.?", re.I)
PROMISE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:a|per|every|each)?\s*(day|week|month|year)", re.I)
MONEY_PILLARS = ("knowledge", "decisions")
KINDS = ("safety", "explain", "onboard", "money", "check", "data", "followup", "welcome", "ack", "unknown")


@dataclass
class Order:
    kind: str
    text: str
    lang: str = "en"
    mode: str = "text"
    term: str | None = None               # explain: glossary key
    url: str | None = None                # onboard
    plan: object | None = None            # data: explorer Plan
    promise: tuple | None = None          # check: (rate, period) of a promised return in the message
    script: str | None = None             # safety: the Guardian's fixed script
    level: str = "NORMAL"
    decided_by: str = "rules"

    def summary(self) -> dict:
        return {"kind": self.kind, "text": self.text[:300], "lang": self.lang, "mode": self.mode, "term": self.term,
                "url": self.url, "promise": self.promise, "decided_by": self.decided_by,
                "plan": self.plan.as_dict() if self.plan is not None else None}


@dataclass
class KSession:
    id: str
    lang: str = "en"
    last_entity: str | None = None
    offered: list = field(default_factory=list)
    explained: set = field(default_factory=set)
    money_active: bool = False
    touched: float = field(default_factory=time.time)


class FrontAgent:
    def __init__(self, service):
        self.s = service

    def order(self, ses: KSession, text: str, lang: str | None, mode: str) -> Order:
        g = guard(text, lang)
        o = Order(kind="unknown", text=g.clean_text or text, lang=g.lang, mode=mode, level=g.level)
        t = o.text.strip()
        # 1. the Guardian always goes first
        if g.level in ("STOP", "SENT"):
            o.kind, o.script = "safety", g.script
            return o
        # 2. "yes" to an explanation we offered
        if ses.offered and (YES.match(t) or re.match(r"^\s*(please|go on|explain|sure|نعم|اشرح)\b", t, re.I)):
            named = by_name(t)
            o.kind, o.term = "explain", named if named in ses.offered else ses.offered[0]
            return o
        if ses.offered and NO.match(t) and len(t.split()) <= 3:
            o.kind = "ack"
            return o
        # 3. "what is ...?" about a glossary term
        term = by_name(t)
        if term and EXPLAIN_RE.match(t) and not re.search(r"\b(how many|which|list|show)\b", t, re.I):
            o.kind, o.term = "explain", term
            return o
        if EXPLAIN_RE.match(t) and re.search(r"\b(mean|meaning)\b", t, re.I):
            o.kind = "explain"                                  # a term we have no entry for -> say so
            return o
        # 4. add an official source
        m = re.search(r"https?://\S+", t)
        hosts = domains_in(t)
        if (m or hosts) and ADD_RE.search(t):
            o.kind, o.url = "onboard", (m.group(0).rstrip(".,)") if m else "https://" + hosts[0])
            return o                                            # the onboarding agent refuses non-official domains
        # 5. greetings
        if HELLO_RE.match(t):
            o.kind = "welcome"
            return o
        plan = self.s.explorer.planner().plan(t)
        signals = extract(t)
        strong_signal = any(s.kind in ("domain", "handle", "phone") for s in signals)
        # 6. a calculator conversation in progress keeps going, unless this is plainly a new data question or check
        coord = self.s.coordinator.sessions.get(ses.id)
        if ses.money_active and coord and coord.pending:
            if not (strong_signal or plan.datasets or plan.op in ("count", "find_datasets", "changes", "lookup")):
                o.kind = "money"
                return o
            ses.money_active = False
        c = classify(t)
        money_intent = bool(c.intent and c.score >= 2 and c.intent.pillar in MONEY_PILLARS and c.intent.id != "explain_term")
        # 6b. a bare "yes" / "no" with nothing offered or pending is not a name to check
        if (YES.match(t) or NO.match(t)) and len(t.split()) <= 3:
            o.kind = "ack"
            return o
        # 7. a follow-up about the entity just discussed ("are they licensed?"), if nothing new is named
        named = any(s.kind == "name" for s in signals)
        if ses.last_entity and FOLLOW_RE.search(t) and not strong_signal and not named:
            o.kind, o.text = "followup", ses.last_entity
            return o
        # 8. something to check
        promise = PROMISE_RE.search(t)
        if plan.op == "lookup" or strong_signal or (COMPANY_RE.search(t) and not money_intent) or (entity_name(t) and not money_intent) or \
                (named and not money_intent and not plan.datasets) or \
                (self._name_like(t) and not plan.datasets and not money_intent):
            o.kind = "check"
            o.promise = (float(promise.group(1)), promise.group(2).lower()) if promise else None
            return o
        # 9. a money question
        if money_intent:
            o.kind = "money"
            return o
        # 10. a data question the rules could place
        if plan.datasets or plan.op in ("find_datasets", "changes"):
            o.kind, o.plan = "data", plan
            return o
        # 11. the AI helper may classify what the rules could not
        ai = self._ai_kind(t)
        if ai:
            o.kind, o.decided_by = ai, "ai"
            if ai == "data":
                o.plan = plan
            if ai == "followup" and not ses.last_entity:
                o.kind = "unknown"
            elif ai == "followup":
                o.text = ses.last_entity
            return o
        o.plan = plan
        return o

    @staticmethod
    def _name_like(t: str) -> bool:
        words = t.split()
        return 1 <= len(words) <= 6 and not re.search(r"[?]|\b(i|my|me|we|you|how|what|why|when|which|should|can|is|are|"
                                                       r"the|a|an)\b", t, re.I) and not t.isdigit()

    def _ai_kind(self, t: str) -> str | None:
        llm = self.s.llm
        if not getattr(llm, "available", False):
            return None
        out = llm._complete("Classify a message to an assistant about official UAE financial data. Reply JSON only: "
                            '{"kind": one of ["check","data","money","explain","followup","welcome","unknown"]}. '
                            "check = verify a company, website, account or phone; data = questions about official lists "
                            "or statistics; money = personal calculations (savings, loans, inflation); explain = meaning "
                            "of a financial term.", t, json_mode=True)
        try:
            kind = json.loads(re.search(r"\{.*\}", out or "", re.S).group(0)).get("kind")
        except (AttributeError, ValueError):
            return None
        return kind if kind in KINDS and kind not in ("safety", "onboard", "unknown") else None

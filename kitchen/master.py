"""
kitchen/master.py - the MASTER AGENT: turns an order into a recipe and runs the specialists
==========================================================================================

WHAT IT DOES
    recipe(order)           -> [Step]   at most 8 steps, each naming the specialist that does it. Deterministic:
                                         the same order always gives the same recipe (so it can be tested and shown).
    cook(order, ses, emit)  -> card     runs the steps (independent ones in parallel), reports each step through
                                         emit("step", ...), and hands the evidence to the composer.

THE SPECIALISTS (all existing, tested modules; the master only routes)
    reader       trust/signals.py        names, websites, handles and phone numbers in the message
    lookup       trust/report.py         every official list, lookalike websites, phone index -> Trust Report
    calculator   tools/calculators.py    promised_return for "15% a month" in a message
    query        explore/engine.py       a validated query plan run over the records (counts, lists, changes)
    catalogue    explore/catalog.py      which datasets answer a question
    guide        agents/coordinator.py   multi-turn money questions (asks for facts, then calculates)
    onboarding   onboard/discover.py     reads an official page and proposes how to load its table
    explainer    kitchen/explainer.py    what the result means + which terms to offer to explain
    The verifier (kitchen/verifier.py) runs after the composer, in kitchen.py.

WHY A FIXED RECIPE AND NOT A FREE AI LOOP
    The tools are few and the orders are well typed, so a rule recipe is faster (no model round trip), cannot
    wander, and is easy to audit. The AI helps only where rules fail: classifying an odd order (front agent) and
    proposing a query plan (explorer), and both answers are validated before use.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from explore.planner import Plan
from tools.calculators import CalcError, promised_return
from trust.signals import extract


@dataclass
class Step:
    id: str
    agent: str
    label: str

    def as_dict(self) -> dict:
        return {"id": self.id, "agent": self.agent, "label": self.label}


def recipe(o) -> list[Step]:
    k = o.kind
    if k in ("check", "followup"):
        steps = [Step("read", "reader", "Find names, websites, accounts and phone numbers"),
                 Step("lookup", "lookup", "Search every official list")]
        if o.promise:
            steps.append(Step("calc", "calculator", f"Work out what {o.promise[0]:g}% a {o.promise[1]} really means"))
        return steps + [Step("explain", "explainer", "Say what it means; spot terms to explain")]
    if k == "data":
        return [Step("plan", "query", "Turn the question into a query"),
                Step("run", "query" if o.plan.op != "find_datasets" else "catalogue", "Run it over the official records"),
                Step("explain", "explainer", "Say what it means; spot terms to explain")]
    if k == "explain":
        return [Step("term", "explainer", "Find the explanation"),
                Step("link", "catalogue", "Link the datasets where this term appears")]
    if k == "money":
        return [Step("guide", "guide", "Work through the money question")]
    if k == "onboard":
        return [Step("check", "onboarding", "Confirm the website is official"),
                Step("discover", "onboarding", "Look for a table behind the page")]
    if k == "unknown":
        return [Step("search", "catalogue", "Search every dataset for these words")]
    return []


class Master:
    def __init__(self, service, composer, explainer, discovery_factory=None):
        self.s, self.c, self.x = service, composer, explainer
        self.discovery_factory = discovery_factory
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kitchen")

    def cook(self, o, ses, emit) -> dict:
        fn = getattr(self, f"_{o.kind}", None)
        return fn(o, ses, emit) if fn else self.c.cannot(o.text, len(self.s.retrieval.reg.datasets))

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _step(emit, sid, fn, detail=lambda r: ""):
        t0 = time.perf_counter()
        emit("step", {"id": sid, "status": "running"})
        try:
            r = fn()
        except Exception as e:
            emit("step", {"id": sid, "status": "failed", "detail": type(e).__name__, "ms": _ms(t0)})
            raise
        emit("step", {"id": sid, "status": "done", "detail": detail(r), "ms": _ms(t0)})
        return r

    # ------------------------------------------------------------------ check / followup
    def _check(self, o, ses, emit) -> dict:
        signals = self._step(emit, "read", lambda: extract(o.text),
                             lambda s: ", ".join(x.shown for x in s[:4]) or "nothing specific: searching the words")
        calc_f = None
        if o.promise:                                       # independent of the look-up: run in parallel
            calc_f = self.pool.submit(self._step, emit, "calc", lambda: promised_return(*o.promise),
                                      lambda c: f"{c.outputs['compounded_per_year_pct']:g}% a year")
        rep = self._step(emit, "lookup", lambda: self.s.trust.check(o.text),
                         lambda r: f"{r['lists_checked']} lists, {len([f for f in r['facts'] if f['dataset_id']])} matching record(s)")
        calc = None
        if calc_f:
            try:
                calc = calc_f.result(timeout=5)
            except (CalcError, Exception):
                calc = None
        card = self._step(emit, "explain", lambda: self.c.fact(rep, ses.explained, calc),
                          lambda c: f"{len(c['offers'])} term(s) to offer")
        if signals:
            ses.last_entity = signals[0].value if signals[0].kind != "name" else signals[0].shown
        elif rep["query"]:
            ses.last_entity = rep["query"][:80]
        return card

    _followup = _check

    # ------------------------------------------------------------------ data
    def _data(self, o, ses, emit) -> dict:
        plan: Plan = o.plan
        self._step(emit, "plan", lambda: plan, lambda p: "; ".join(p.reading[:3]) or p.op)
        run = (lambda: self.s.explorer.ask(o.text)) if not plan.confident else (lambda: self.s.explorer.run(plan, question=o.text))
        res = self._step(emit, "run", run,
                         lambda r: r["answer"][:90])
        if plan.op == "lookup" and res.get("report"):
            return self._step(emit, "explain", lambda: self.c.fact(res["report"], ses.explained),
                              lambda c: f"{len(c['offers'])} term(s) to offer")
        return self._step(emit, "explain", lambda: self.c.data(res, ses.explained),
                          lambda c: f"{len(c['offers'])} term(s) to offer")

    # ------------------------------------------------------------------ explain
    def _explain(self, o, ses, emit) -> dict:
        self._step(emit, "term", lambda: o.term, lambda t: t or "no entry")
        card = self._step(emit, "link", lambda: self.c.explanation(o.term, o.lang),
                          lambda c: f"{len((c.get('explain') or {}).get('datasets', []))} dataset(s)")
        if o.term:
            ses.explained.add(o.term)
        return card

    # ------------------------------------------------------------------ money (the voice guide's coordinator)
    def _money(self, o, ses, emit) -> dict:
        tr = self._step(emit, "guide", lambda: self.s.coordinator.turn(ses.id, o.text, o.lang),
                        lambda t: "asking for a fact" if t.mode in ("ask", "clarify") else "calculated")
        if tr.mode == "safety":
            return self.c.safety(tr.reply, tr.level)
        card = self.c.calculation(tr, ses.explained)
        ses.money_active = tr.mode == "ask"
        return card

    # ------------------------------------------------------------------ onboard
    def _onboard(self, o, ses, emit) -> dict:
        from onboard.discover import Discovery, default_capture
        self._step(emit, "check", lambda: o.url, lambda u: "official domain")
        disc = (self.discovery_factory or (lambda: Discovery(None, default_capture())))()
        d = self._step(emit, "discover", lambda: disc.discover(o.url),
                       lambda d: f"{len(d['proposals'])} table(s) found")
        return {"_discovery": d}                            # kitchen.py stores the proposals and builds the card

    # ------------------------------------------------------------------ unknown: search everything
    def _unknown(self, o, ses, emit) -> dict:
        """Nothing matched the rules: let the explorer plan it (AI plan if configured, validated), else search words."""
        def go():
            if getattr(self.s.llm, "available", False):
                res = self.s.explorer.ask(o.text)
                if res["records"] or res["plan"]["op"] == "find_datasets":
                    return res
            words = " ".join(w for w in re.findall(r"[\w؀-ۿ]+", o.text.lower()) if w not in STOP and len(w) > 2)
            plan = Plan(op="find_datasets", text=words[:120], reading=[f"search every dataset for: {words or '-'}"])
            res = self.s.explorer.run(plan, question=o.text)
            res["datasets"] = [d for d in res["datasets"] if d.get("matched")]
            res["answer"] = f"Your words appear in {len(res['datasets'])} dataset{'s' if len(res['datasets']) != 1 else ''}."
            return res if words else {**res, "datasets": []}
        res = self._step(emit, "search", go, lambda r: f"{len(r['datasets'])} dataset(s) matched")
        if not res["datasets"] and not res["records"]:
            return self.c.cannot(o.text, len(self.s.retrieval.reg.datasets))
        return self.c.data(res, ses.explained)


STOP = set("""the and for are was were who what when where which why how did does do you your our has have had can could
would should will this that these those there their they them with from about into over after lately recently any all
some most more much many just been being is it its of to in on at by an or as me my we us he she his her""".split())


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)

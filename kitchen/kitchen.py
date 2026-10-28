"""
kitchen/kitchen.py - one turn of the assistant, as a stream of events
=====================================================================

    Kitchen(service, discovery_factory=None, transport=None).turn(session_id, text, lang, mode) -> iterator of events

THE EVENTS (each a dict {"event": name, "data": {...}}; the API sends them as Server-Sent Events)
    order     what the front agent understood (kind, decided_by, and the query plan if any)
    recipe    the master agent's steps: [{id, agent, label}]
    step      {id, status: running | done | failed, detail, ms}   the ticker on screen
    card      the composed, verified card (kitchen/composer.py has the shape)
    verify    {ok, problems}   what the verifier found (problems are repaired before the card is sent)
    done      {took_ms, session}

    The order of work is always:
        Guardian (inside the front agent) -> front agent -> master (recipe, specialists) -> composer -> verifier.
    A failure anywhere becomes a "cannot answer" card with the reason, never a stack trace and never a guess.

SESSIONS
    KSession per conversation, in memory, expired after config.SESSION_IDLE_MINUTES. It holds what the front agent
    needs (last entity, explanations offered and given, money conversation in progress), never the raw text.

ONBOARDING THROUGH THE CHAT
    An "add this source" order returns a proposal card with a token. Nothing is saved until the person presses
    Approve, which calls approve(token, proposal_id) -> the normal approval path (onboard/approve.py), then the
    service reloads its registry so the new dataset is searchable at once.
"""
from __future__ import annotations

import logging
import queue
import secrets
import threading
import time

import config

from agents.slots import YES

from .composer import Composer
from .explainer import Explainer
from .front import FrontAgent, KSession
from .master import Master, recipe
from .verifier import repair, verify

log = logging.getLogger("kitchen")


class Kitchen:
    def __init__(self, service, discovery_factory=None, transport=None):
        self.s = service
        self.transport = transport
        self.x = Explainer(service.explorer.catalog)
        self.c = Composer(self.x, service.retrieval)
        self.front = FrontAgent(service)
        self.master = Master(service, self.c, self.x, discovery_factory)
        self.sessions: dict[str, KSession] = {}
        self.proposals: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ sessions
    def session(self, sid: str) -> KSession:
        with self._lock:
            now = time.time()
            for k in [k for k, s in self.sessions.items() if now - s.touched > config.SESSION_IDLE_MINUTES * 60]:
                del self.sessions[k]
            for k in [k for k, (t, _) in self.proposals.items() if now - t > 1800]:
                del self.proposals[k]
            s = self.sessions.setdefault(sid, KSession(sid))
            s.touched = now
            return s

    def reset(self, sid: str) -> None:
        with self._lock:
            self.sessions.pop(sid, None)
        self.s.coordinator.reset(sid)

    # ------------------------------------------------------------------ one turn
    def turn(self, sid: str, text: str, lang: str | None = None, mode: str = "text"):
        t0 = time.perf_counter()
        ses = self.session(sid)
        q: queue.Queue = queue.Queue()
        box: dict = {}
        o = None

        def emit(name, data):                               # called from the cooking thread (and the pool)
            q.put({"event": name, "data": data})

        def cook():
            try:
                box["card"] = self._cook(o, ses, emit)
            except Exception:
                log.exception("kitchen turn failed (session %s)", sid[:6])
            finally:
                q.put(None)

        try:
            o = self.front.order(ses, text, lang, mode)
            ses.lang = o.lang
            yield {"event": "order", "data": o.summary()}
            steps = recipe(o)
            if steps:
                yield {"event": "recipe", "data": {"steps": [s.as_dict() for s in steps]}}
            threading.Thread(target=cook, daemon=True).start()
            while (e := q.get(timeout=60)) is not None:     # step events reach the screen as they happen
                yield e
        except Exception:
            log.exception("kitchen order failed (session %s)", sid[:6])
        card = box.get("card")
        if card is None:
            card = self.c.cannot(text, len(self.s.retrieval.reg.datasets))
            card["headline"] = "Something went wrong on my side, so I won't guess. Please ask again."

        ok, problems = verify(card)
        if not ok:
            log.warning("verifier repaired a card: %s", problems)
            card = repair(card, problems)
        yield {"event": "verify", "data": {"ok": ok, "problems": problems}}
        card["id"] = secrets.token_hex(4)
        ses.offered = [x["term"] for x in card.get("offers", [])]
        yield {"event": "card", "data": card}
        took = round((time.perf_counter() - t0) * 1000, 1)
        log.info("kitchen turn session=%s kind=%s by=%s card=%s tone=%s cites=%d offers=%d ok=%s ms=%.1f", sid[:6],
                 o.kind if o else "error", o.decided_by if o else "-", card["type"], card.get("tone"),
                 len(card.get("citations", [])), len(card.get("offers", [])), ok, took)
        yield {"event": "done", "data": {"took_ms": took, "session": sid}}

    def _cook(self, o, ses, emit) -> dict:
        if o.kind == "safety":
            return self.c.safety(o.script, o.level)
        if o.kind == "welcome":
            return self.c.welcome()
        if o.kind == "ack":
            head = ("No problem. What else would you like to check?" if ses.offered or not YES.match(o.text) else
                    "What would you like me to check? Say a company, website, fund, or a money question.")
            ses.offered = []
            return {**self.c.welcome(), "headline": head, "followups": [], "speech": head}
        if o.kind != "money":
            ses.money_active = False
        card = self.master.cook(o, ses, emit)
        if "_discovery" in card:
            d = card["_discovery"]
            token = secrets.token_urlsafe(12)
            with self._lock:
                self.proposals[token] = (time.time(), {p["id"]: p for p in d["proposals"]})
            card = self.c.proposal(d, token)
        return card

    def run(self, sid: str, text: str, lang: str | None = None, mode: str = "text") -> dict:
        """The whole turn at once (tests, MCP, non-streaming clients): {events, card}."""
        evs = list(self.turn(sid, text, lang, mode))
        return {"events": evs, "card": next(e["data"] for e in evs if e["event"] == "card")}

    # ------------------------------------------------------------------ images (a screenshot of an offer)
    def image(self, sid: str, image: bytes, mime: str) -> dict:
        ses = self.session(sid)
        rep = self.s.trust.check("", image=image, mime=mime)
        card = self.c.fact(rep, ses.explained)
        ok, problems = verify(card)
        if not ok:
            card = repair(card, problems)
        card["id"] = secrets.token_hex(4)
        ses.offered = [x["term"] for x in card.get("offers", [])]
        return card

    # ------------------------------------------------------------------ onboarding approval
    def approve(self, token: str, proposal_id: str, overrides: dict | None = None) -> dict:
        from onboard.approve import approve as do_approve
        with self._lock:
            found = self.proposals.get(token, (0, {}))[1].get(proposal_id)
        if not found:
            return {"ok": False, "error": "proposal not found or expired: ask again"}
        res = do_approve(found, overrides, db=self.s.db, transport=self.transport)
        self.s.reload()
        return res

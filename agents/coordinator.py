"""
agents/coordinator.py - one conversation turn, from "the person said X" to a checked, spoken reply
=================================================================================================

THE PIPELINE (fixed order; no AI decides the next step)

    1 GUARDIAN      guardian/guard.py      redact secrets, scam signs  ->  STOP / SENT: fixed script, END
    2 UNDERSTAND    agents/slots.py        numbers & names from everyday speech, corrections ("no, I said ...")
                    agents/intents.py      which of the 3 pillars and which intent (rules; optional AI fallback)
    3 PLAN          (this file)            which required facts are still missing?
                                           -> ask ONE short question (most important first) and STOP the turn
    4 ACT           agents/workflows.py    deterministic calculations + official-data checks (tools/)
    5 EXPLAIN       agents/workflows.py    plain-language template;  agents/llm.py may rephrase / translate
    6 VALIDATE      agents/validator.py    numbers vs evidence, no advice/guarantee/false reassurance, caveats
                                           -> an AI draft that fails is REPLACED by the template
    7 RECORD        trace + logs           labels only (intent, tools, rule ids), never what the person said
    (0 HEARING      agents/hearing.py      before everything: "100,000 diplomats" -> "100,000 dirhams", shown on screen)

    Bounded: at most config.MAX_WORKFLOW_STEPS tool calls per turn, one question per turn, each fact asked
    at most twice. Facts the person gave are kept for this conversation only (RAM, dropped after
    config.SESSION_IDLE_MINUTES) and read back so a misheard number can be corrected.

OUTPUT
    TurnResult - the reply plus everything the screen shows: pillar, intent, the question being asked,
    quick replies, and the EVIDENCE panel (calculations, official records with snapshot dates, data status,
    assumptions, limitations).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field

import config
from guardian.guard import guard

from . import slots as S
from store.names import domains_in

from .hearing import repair
from .intents import BY_ID, INTENTS, PILLARS, SLOTS, classify
from .llm import LLM
from .validator import evidence_of, validate
from .workflows import RUNNERS, Answer, Kit

log = logging.getLogger("agents.coordinator")

# When an intent needs fact X and the person already gave fact Y that means the same thing here.
ALIASES = {
    "purchasing_power": {"amount": ["savings"]}, "fund_vs_bank": {"amount": ["savings"]},
    "deposit_offer": {"amount": ["savings"]}, "rate_change": {"amount": ["savings"]},
    "asset_hype": {"total_investable": ["savings"]}, "concentration": {"total_investable": ["savings"]},
    "remittance": {"remit_amount": ["amount"]},
    "return_gap": {"invested": ["amount"], "stated_rate_pct": ["fund_return_pct", "deposit_rate_pct"]},
    "loan_offer": {"loan_principal": ["amount", "debt"]}, "debt_vs_savings": {"debt": ["amount"]},
    "market_drop": {"amount": ["holding_value"]},
}
# No topic words, but the facts say it all: "I'm 62, 350,000 saved, I spend 9,000" is a retirement question.
FACT_TOPICS = [(("loan_principal",), "loan_offer"), (("holding_value",), "concentration"),
               (("promised_rate_pct",), "suspicious_offer"), (("savings", "monthly_spending"), "retirement_readiness"),
               (("debt", "savings"), "debt_vs_savings")]
NAME_INTENTS = ("check_entity", "finfluencer_check", "suspicious_offer")
ANOTHER = re.compile(r"\b(another|different|other|next|new)\b|\bone more\b|اخرى|آخر|ثاني", re.I)
ABOUT_IT = re.compile(r"\b(licen[sc]ed|legit\w*|safe|real|genuine|scam|trust|regulated|registered|authori[sz]ed)\b", re.I)
VERDICT_SHORT = {
    "warning_found": "No. “{name}” matches a Capital Market Authority warning about entities that are not licensed. "
                     "Please do not send them money.",
    "possible_warning": "I can't confirm it. A similar name is on a Capital Market Authority warning, so please check the exact "
                        "name and website before doing anything, and do not send money until you are sure.",
    "licensed_record_found": "“{name}” is on the Capital Market Authority's licensed list. That does not guarantee your money, "
                             "and it does not prove the person contacting you works there: use the website on the official record.",
    "authorised_finfluencer_found": "“{name}” is on the CMA's authorised finfluencer list. That allows them to promote; it is "
                                    "not a recommendation of what they promote.",
    "registered_record_found": "“{name}” is on a CMA registration list, which is not a licence to manage your money.",
    "no_record_found": "I could not find “{name}” on the Capital Market Authority lists, so I cannot confirm it is licensed by "
                       "the CMA. Ask them which regulator licensed them and their licence number, then check it on that "
                       "regulator's own website.",
    "could_not_check": "I could not check the official lists for “{name}” because they are not available right now.",
}
QUICK_NEXT = {"scams": ["Check another company", "They contacted me on WhatsApp", "They promise a high return"],
              "knowledge": ["Will my savings last?", "Is my money keeping up with prices?"],
              "decisions": ["Should I use savings to clear my debt?", "My bank is offering me a loan"]}


def NAME_LIKE(text: str) -> bool:
    """A short reply that looks like a name ("HOXTON FX", "Seid Fayaz", "www.x.com"), not a sentence or a question."""
    t = text.strip()
    if re.fullmatch(r"(?i)[\d,.\s%]+(aed|dhs|dirhams?|k|a month|per month|a year)?\.?", t) or S.strong_slots(t):
        return False                                     # "8,000", "2,000 pension": an amount, not a company
    return (1 <= len(t.split()) <= 8 and len(t) <= 80 and "?" not in t
            and not (domains_in(t) and len(t.split()) > 1)      # "their website is x.com": a website, not a name
            and not re.search(r"\b(what|how|why|when|should|could|would|can|is it|they|them|their|his|her|me|my|i|you|"
                              r"yes|no|ok|okay|thanks|thank|please|help|don'?t|know|website|site|called)\b", t, re.I))


THANKS = re.compile(r"^\W*(ok(ay)?|great|good|perfect|alright|fine)?\W*(thanks?( you)?( so much| very much)?|thank u|cheers|"
                    r"shukran|شكرا\w*|that'?s (all|helpful|great))\W*$", re.I)
WHAT_NOW = re.compile(r"\b(what (do|should|can) i do( now| next)?|what now|what next|what are the steps|tell me again|"
                      r"repeat (that|it)|ماذا افعل|شو اسوي)\b", re.I)
JUDGE = re.compile(r"\b(is (that|this|it) (ok|okay|good|fine|bad|enough|affordable|a good (idea|deal))|"
                   r"should i (do it|take it|accept|go ahead|sign)|what do you (think|suggest|recommend)|do you recommend|"
                   r"what would you do|هل هذا جيد|هل انصح)\b", re.I)
# How to judge an answer, without telling the person what to do (no product, no amount, no new numbers).
HOW_TO_JUDGE = {
    "decisions": "I can't make the decision for you, but here is how to judge it. Look at what is left each month after "
                 "essentials and the payment: it should leave you room for surprises and still keep an emergency fund. "
                 "Ask for the full cost in writing, compare at least one other offer, check the fees for paying early, and "
                 "talk it over with someone you trust before you sign anything.",
    "knowledge": "I can't decide for you, but here is how to judge it. These figures assume nothing changes, so treat them "
                 "as a rough guide. Try a lower and a higher figure to see how much the answer moves, keep an emergency fund "
                 "you never invest, and for a big change speak to a licensed adviser who is paid a fee, not a commission.",
    "scams": "Here is how to judge it: if anyone pressures you to act quickly, keep it secret, or move money to them, stop. "
             "Check the firm on the regulator's own website, call it on a number you find yourself, and never send money "
             "or share codes until you are sure.",
}
FRUSTRATED = re.compile(r"\b(i (just )?(said|told you)|already (said|told)|you (didn'?t|did not) (listen|hear)|i said it|"
                        r"قلت لك|قلتها)\b", re.I)
RANGES = {"monthly_spending": ["3,000", "6,000", "10,000", "15,000"], "savings": ["50,000", "200,000", "500,000", "1,000,000"],
          "monthly_income": ["none", "3,000", "8,000", "15,000"], "amount": ["10,000", "50,000", "100,000", "500,000"],
          "debt": ["10,000", "50,000", "100,000", "300,000"], "debt_rate_pct": ["5%", "10%", "18%", "30%"],
          "deposit_rate_pct": ["2%", "3.5%", "4%", "5%"], "total_investable": ["100,000", "300,000", "600,000", "1,000,000"],
          "monthly_support": ["1,000", "3,000", "5,000", "10,000"], "monthly_medical": ["none", "500", "1,500", "3,000"]}
RANGE_REPLIES = {k: [("About " + x if x[0].isdigit() and not x.endswith("%") else x.capitalize() if x == "none" else x)
                     + (" a month" if k.startswith("monthly") and x != "none" else "") for x in v] for k, v in RANGES.items()}
DONT_KNOW = re.compile(r"\b(don'?t know|not sure|no idea|skip|can'?t remember|لا اعرف|مش عارف)\b", re.I)
CLARIFY_EN = ("I'd be glad to help. I can help you in three ways: understand your retirement money and avoid costly mistakes, "
              "think through a big money decision, or check an offer or company for scams. Which would you like?")
CLARIFY_AR = ("يسعدني مساعدتك بثلاث طرق: فهم أموال تقاعدك وتجنب الأخطاء المكلفة، أو التفكير في قرار مالي كبير، "
              "أو التحقق من عرض أو شركة لتجنب الاحتيال. ماذا تفضّل؟")
AR_NOTE = "سأجيبك بالإنجليزية الآن لأن الشرح العربي التفصيلي يحتاج إلى مساعد الذكاء الاصطناعي، وهو غير مفعّل."


@dataclass
class Session:
    id: str
    lang: str = "en"
    intent: str | None = None
    facts: dict = field(default_factory=dict)          # slot -> value (this conversation only)
    words: dict = field(default_factory=dict)          # slot -> the words it came from
    pending: str | None = None
    asked: dict = field(default_factory=dict)          # slot -> times asked
    last_set: list = field(default_factory=list)       # slots set in the previous turn (for corrections)
    completed: bool = False
    soft_pending: str | None = None                    # after a name check: a bare name next means "check this one"
    texts: list = field(default_factory=list)          # redacted user texts (validator: numbers they said)
    hinted: bool = False                               # "if I misheard, correct me" said once already
    main_intent: str | None = None                     # last real topic answered ("what does flat mean?" is a detour)
    last_safety: str | None = None                     # the last safety script, so "what do I do now?" repeats it
    touched: float = field(default_factory=time.time)


@dataclass
class TurnResult:
    reply: str
    mode: str                                           # answer | ask | safety | clarify
    lang: str = "en"
    pillar: str | None = None
    intent: str | None = None
    intent_title: str | None = None
    question_slot: str | None = None
    quick_replies: list = field(default_factory=list)
    noted: list = field(default_factory=list)           # [{"slot","label","value","display"}] read back on screen
    facts: list = field(default_factory=list)           # every fact in use, labelled (same shape as noted)
    evidence: dict = field(default_factory=dict)
    level: str = "NORMAL"
    signals: list = field(default_factory=list)
    redacted: list = field(default_factory=list)
    ai_used: bool = False
    heard: list = field(default_factory=list)           # speech slips repaired: [{"heard": ..., "as": ...}]
    trace: list = field(default_factory=list)

    def as_dict(self, debug: bool = False) -> dict:
        d = asdict(self)
        if not debug:
            d.pop("trace")
        return d


class Coordinator:
    def __init__(self, kit: Kit, llm: LLM | None = None):
        self.kit, self.llm = kit, llm or LLM()
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ sessions
    def session(self, sid: str) -> Session:
        with self._lock:
            now = time.time()
            for k in [k for k, s in self.sessions.items() if now - s.touched > config.SESSION_IDLE_MINUTES * 60]:
                del self.sessions[k]
            s = self.sessions.setdefault(sid, Session(sid))
            s.touched = now
            return s

    def reset(self, sid: str) -> None:
        with self._lock:
            self.sessions.pop(sid, None)

    # ------------------------------------------------------------------ the turn
    def turn(self, sid: str, raw_text: str, lang: str | None = None) -> TurnResult:
        """Repair known speech slips (shown to the person), then run the pipeline."""
        text, fixes = repair(raw_text)
        try:
            res = self._turn(sid, text, lang)
        except Exception:                                # a bug must never look like "could not reach the guide"
            log.exception("turn failed (session %s)", sid[:6])
            st = self.session(sid)
            res = TurnResult("Sorry, something went wrong on my side. I have kept what you told me. Could you say that "
                             "once more, in a few words?", "ask" if st.pending else "clarify", st.lang,
                             BY_ID[st.intent].pillar if st.intent else None, st.intent, question_slot=st.pending,
                             facts=self._public_facts(st))
            res.trace = [{"step": "error"}]
        res.heard = fixes
        if fixes:
            res.trace.insert(0, {"step": "hearing", "fixes": len(fixes)})
        return res

    def _turn(self, sid: str, raw_text: str, lang: str | None = None) -> TurnResult:
        t0 = time.time()
        trace: list[dict] = []
        st = self.session(sid)
        g = guard(raw_text, lang)
        st.lang = g.lang
        trace.append({"step": "guardian", "level": g.level, "signals": g.signal_ids, "redacted": g.redacted, "lang": g.lang})
        if g.level in ("STOP", "SENT"):
            st.pending, st.last_safety = None, g.script
            res = TurnResult(g.script, "safety", g.lang, "scams", None, None, level=g.level, signals=g.signal_ids,
                             redacted=g.redacted,
                             quick_replies=(["Help me talk to my bank", "Check a company name"] if g.lang != "ar"
                                            else ["ساعدني في التحدث مع البنك", "تحقق من اسم شركة"]))
            res.trace = trace
            self._log(sid, res, t0)
            return res

        text = g.clean_text
        small = self._small_talk(st, text, g)
        if small:
            trace.append({"step": small[0]})
            return self._finish(sid, t0, trace, g, small[1])
        st.texts.append(text)
        set_now: list[str] = []
        ex = S.extract(text)
        strong = S.strong_slots(text)
        ai_facts = self._ai_facts(st, text, ex, trace)
        for k, v in ai_facts.items():                    # AI only fills gaps; every value was checked against the words
            ex.setdefault(k, {"value": v, "words": "ai"})
            strong.add(k)

        # --- 1. facts the person NAMED ("1,000,000 savings", "I spent 10,000") always win: they may be corrections
        named = {k: e for k, e in ex.items() if k in strong and k in SLOTS}
        for k, e in named.items():
            if st.facts.get(k) != e["value"]:
                trace.append({"step": "updated" if k in st.facts else "set", "slot": k})
            st.facts[k], st.words[k] = e["value"], e["words"]
            set_now.append(k)
            if st.pending == k:
                st.pending = None
            ex.pop(k)

        # --- 2. a bare correction: "no, I said 150,000" -> the figure we noted last
        if not named and S.is_correction(text) and st.last_set:
            target = self._correction_target(st, text)
            v = S.answer_for(target, SLOTS[target].kind, text, SLOTS[target].allow_zero) if target else None
            if v is not None:
                log.info("session %s: corrected %s (%s -> %s)", sid[:6], target, st.facts.get(target), v)
                st.facts[target], st.words[target] = v, "corrected"
                set_now.append(target)
                trace.append({"step": "correction", "slot": target})
                ex = {}

        was_completed, prev_intent = st.completed, st.intent
        c0 = classify(text, ex)
        other_intent = bool(c0.intent and c0.score >= 3 and c0.intent.id != st.intent)

        # --- 2b. after a name check, a short bare name ("HOXTON FX") means: check this one too
        soft, st.soft_pending = st.soft_pending, None
        if soft and not set_now and not other_intent and "entity_name" not in ex and NAME_LIKE(text):
            st.facts["entity_name"], st.words["entity_name"] = text.strip(" .?!\"'"), text
            st.facts.pop("domain", None)
            set_now.append("entity_name")
            trace.append({"step": "new_name"})
        # --- 2c. "check another company" -> forget the previous name and ask for the new one
        if c0.intent and c0.intent.id in NAME_INTENTS and ANOTHER.search(text) and "entity_name" not in ex and not set_now:
            st.facts.pop("entity_name", None)
            st.facts.pop("domain", None)
            trace.append({"step": "another_name"})

        # --- 3. an answer to the question we just asked gets priority over weak guesses ("I have 4,000")
        if st.pending and SLOTS[st.pending].kind == "text" and other_intent:
            st.pending = None                            # they changed the subject instead of answering
        if st.pending and not set_now:
            slot = SLOTS[st.pending]
            v = ex.pop(st.pending)["value"] if st.pending in ex else None
            if v is None and st.pending == "charges":
                v = [n.value for n in S.numbers_in(text)] or None
            if v is None:
                v = S.answer_for(st.pending, slot.kind, text, slot.allow_zero)
            repeated = next((k for k in st.last_set if k != st.pending and isinstance(v, (int, float)) and v
                             and st.facts.get(k) == v), None)
            if repeated:                                  # the same figure again = a repeat of the last answer, not a new one
                trace.append({"step": "repeat", "slot": repeated})
                f = self._noted(st, [repeated])[0]
                it = BY_ID[st.intent]
                msg = (f"I already have {f['display']} as your {f['label']}. "
                       + (SLOTS[st.pending].ask_ar if g.lang == "ar" else SLOTS[st.pending].ask_en))
                return self._finish(sid, t0, trace, g, TurnResult(
                    msg, "ask", g.lang, it.pillar, it.id, it.title, question_slot=st.pending,
                    quick_replies=self._ask_replies(st.pending), facts=self._public_facts(st)))
            if v is not None:
                st.facts[st.pending] = v
                set_now.append(st.pending)
                ex = {k: e for k, e in ex.items() if not (isinstance(e["value"], (int, float)) and e["value"] == v)}
                trace.append({"step": "answer", "slot": st.pending})
                st.pending = None
            elif DONT_KNOW.search(text):
                trace.append({"step": "dont_know", "slot": st.pending})
                return self._finish(sid, t0, trace, g, self._estimate_help(st, g))
            elif FRUSTRATED.search(text):
                trace.append({"step": "frustrated", "slot": st.pending})
                return self._finish(sid, t0, trace, g, self._summary_and_ask(st, g))

        # --- the same name again ("is hoxton fx licensed?" right after checking it) is a question about it, not a new check
        for k in ("entity_name", "domain"):
            if st.completed and k in ex and str(ex[k]["value"]).casefold() == str(st.facts.get(k, "")).casefold():
                ex.pop(k)
                trace.append({"step": "same_name"})

        # --- which intent?
        c = classify(text, ex)
        trace.append({"step": "classify", "intent": c.intent.id if c.intent else None, "score": round(c.score, 1),
                      "runner_up": c.runner_up.id if c.runner_up else None})
        new_intent = c.intent.id if c.intent else None
        answered_q = any(s.get("step") == "answer" for s in trace)
        if not new_intent and not set_now and not answered_q and (not st.intent or st.completed) and self.llm.available:
            new_intent = self.llm.understand(text, [(i.id, i.title) for i in INTENTS])
            trace.append({"step": "ai_understand", "intent": new_intent})
        if not new_intent and not st.intent:
            known = set(st.facts) | set(ex)
            new_intent = next((i for need, i in FACT_TOPICS if set(need) <= known), None)
            if new_intent:
                trace.append({"step": "topic_from_facts", "intent": new_intent})
        answered = any(s.get("step") == "answer" for s in trace)
        if new_intent and new_intent != st.intent:
            # A new topic wins - unless this is a reply to our question that merely contains a topic word
            # ("2,000 pension" while we asked about income must not start a pension conversation).
            if not (answered and c.score < 3):
                st.intent, st.pending, st.asked, st.completed = new_intent, None, {}, False
                trace.append({"step": "switch", "intent": new_intent})
        elif st.intent and st.completed:
            it = BY_ID[st.intent]
            if (set(ex) | set(set_now)) & set(it.required + it.optional):
                st.completed = False                      # "what if I spend 4,000?" -> rerun with the new figure
                trace.append({"step": "follow_up", "intent": st.intent})

        for k, e in ex.items():
            if k in ("entity_name", "domain") and k in st.facts and st.completed is False and st.intent and "entity_name" not in BY_ID[st.intent].required + BY_ID[st.intent].optional:
                continue
            st.facts[k], st.words[k] = e["value"], e["words"]
            if k in SLOTS:
                set_now.append(k)
        if st.intent == "explain_term":
            st.facts["_text"] = text

        if not st.intent:
            noted = self._noted(st, set_now)                  # read back numbers even before we know the topic
            rb = self._readback(noted, g.lang, st)
            res = TurnResult(((rb + " ") if rb else "") + (CLARIFY_AR if g.lang == "ar" else CLARIFY_EN), "clarify", g.lang,
                             noted=noted, facts=self._public_facts(st), level=g.level,
                             signals=g.signal_ids, redacted=g.redacted,
                             quick_replies=["Will my savings last?", "Help me decide about a loan", "Check a company for me"])
            res.trace = trace
            st.last_set = set_now
            self._log(sid, res, t0)
            return res

        it = BY_ID[st.intent]
        facts = self._resolve(st, it)
        noted = self._noted(st, set_now)
        missing = [k for k in it.required if facts.get(k) is None]

        # --- ask for the next missing fact (a workflow NEVER runs with a required fact missing)
        if missing:
            slot_id = missing[0]
            st.completed = False
            st.asked[slot_id] = st.asked.get(slot_id, 0) + 1
            st.pending = slot_id
            q = SLOTS[slot_id].ask_ar if g.lang == "ar" else SLOTS[slot_id].ask_en
            prefix = self._pause_prefix(g) + self._readback(noted, g.lang, st)
            replies = self._ask_replies(slot_id)
            asks_back = "?" in text or re.match(r"(?i)\s*(should|can|could|is|are|do|does|what|how|why)\b", text)
            if st.asked[slot_id] >= 2 and not set_now and asks_back and g.lang != "ar":
                q = "Good question. I'll come to it as soon as I know one more thing. " + q   # they asked, not misheard
            elif st.asked[slot_id] >= 2 and not set_now:    # not understood: make answering easier, never give up
                q = (f"Sorry, I didn't catch that. A rough figure is fine, for example {(RANGES.get(slot_id) or ['5,000', '5,000'])[min(1, len(RANGES.get(slot_id) or ['5,000']) - 1)]}. "
                     if g.lang != "ar" else "عذراً، لم ألتقط ذلك. رقم تقريبي يكفي. ") + q
                replies = RANGE_REPLIES.get(slot_id, replies)
            res = TurnResult((prefix + " " + q).strip(), "ask", g.lang, it.pillar, it.id, it.title, question_slot=slot_id,
                             quick_replies=replies)
            res.noted, res.facts = noted, self._public_facts(st)
            res.level, res.signals, res.redacted = g.level, g.signal_ids, g.redacted
            trace.append({"step": "ask", "slot": slot_id, "times": st.asked.get(slot_id)})
            res.trace = trace
            st.last_set = set_now or st.last_set
            self._log(sid, res, t0)
            return res

        # --- nothing new since the last answer: do not repeat it
        if was_completed and prev_intent == st.intent and st.completed:
            trace.append({"step": "nothing_new"})
            msg = ("I don't have anything new to add to my last answer. " if g.lang != "ar" else "ليس لدي جديد أضيفه. ") + \
                  ("You can give me a different figure, ask about something else, or tap one of the suggestions."
                   if g.lang != "ar" else "يمكنك إعطائي رقماً مختلفاً أو السؤال عن شيء آخر.")
            last = st.facts.get("_last_check")
            if it.id in NAME_INTENTS and last and ABOUT_IT.search(text):
                msg = VERDICT_SHORT.get(last["verdict"], VERDICT_SHORT["no_record_found"]).format(name=last["name"])
                st.soft_pending = "entity_name"
            elif it.id in NAME_INTENTS:
                st.soft_pending = "entity_name"
                msg = ("If you want me to check another company, person or website, just tell me its name."
                       if g.lang != "ar" else "إذا أردت التحقق من اسم آخر، قل لي الاسم فقط.")
            rb = self._readback(noted, g.lang, st)                # e.g. a figure for another topic: show we kept it
            return self._finish(sid, t0, trace, g, TurnResult(
                ((rb + " ") if rb else "") + msg, "ask", g.lang, it.pillar, it.id, it.title, noted=noted,
                quick_replies=QUICK_NEXT.get(it.pillar, []),
                facts=self._public_facts(st)))

        # --- act, explain, validate
        answer: Answer = RUNNERS[it.id](facts, self.kit)
        answer.tools = answer.tools[: config.MAX_WORKFLOW_STEPS]
        trace.append({"step": "workflow", "intent": it.id, "tools": answer.tools})
        template = " ".join(answer.say)
        prefix = self._pause_prefix(g) + self._readback(noted, "en", st)
        referral = f" This also touches on a {g.referral} matter; for that, please speak to {g.referral_to}." if g.referral else ""
        draft = (prefix + " " + template + referral).strip()
        verdict_kind = next((c.get("verdict") for c in answer.checks if "verdict" in c), None)
        ev = evidence_of(answer)
        reply, ai_used = draft, False
        if self.llm.available and (g.lang == "ar" or len(template) > 0):
            alt = self.llm.rephrase(draft, g.lang)
            if alt:
                v = validate(alt, ev, st.texts, entity_verdict=verdict_kind, data_states=answer.data)
                trace.append({"step": "validate_ai", "action": v.action, "problems": v.problems})
                if v.action != "replace":
                    reply, ai_used = v.text, True
        if not ai_used:
            v = validate(draft, ev, st.texts, entity_verdict=verdict_kind, data_states=answer.data)
            trace.append({"step": "validate_template", "action": v.action, "problems": v.problems})
            if v.action == "replace":                    # should never happen: templates are tested; log loudly
                log.error("TEMPLATE FAILED VALIDATION intent=%s problems=%s", it.id, v.problems)
            reply = v.text
            if g.lang == "ar":
                reply = AR_NOTE + " " + reply
        st.completed, st.pending = True, None
        if it.id != "explain_term":
            st.main_intent = it.id
        if it.id in NAME_INTENTS:
            st.soft_pending = "entity_name"
            chk = next((c for c in answer.checks if "verdict" in c), None)
            if chk:
                st.facts["_last_check"] = {"name": chk["query"]["name"] or chk["query"]["domain"], "verdict": chk["verdict"]}
        st.last_set = set_now or st.last_set
        res = TurnResult(reply, "answer", g.lang, it.pillar, it.id, it.title, None, answer.quick_replies, noted,
                         self._public_facts(st), self._evidence(answer), g.level, g.signal_ids, g.redacted, ai_used)
        res.trace = trace
        self._log(sid, res, t0)
        return res

    # ------------------------------------------------------------------ helpers
    def _finish(self, sid: str, t0: float, trace: list, g, res: TurnResult) -> TurnResult:
        res.level, res.signals, res.redacted = g.level, g.signal_ids, g.redacted
        res.trace = trace
        self._log(sid, res, t0)
        return res

    def _small_talk(self, st: Session, text: str, g) -> tuple[str, TurnResult] | None:
        """Thanks, 'what do I do now?' after a safety script, and 'is that OK?' after an answer."""
        it = BY_ID.get(st.intent) if st.intent else None
        pillar = it.pillar if it else None
        title = it.title if it else None
        if THANKS.match(text):
            msg = ("You're welcome. I'm here whenever you want to check a figure, a decision or an offer."
                   if g.lang != "ar" else "على الرحب والسعة. أنا هنا متى احتجت.")
            if st.pending:
                msg += " " + (SLOTS[st.pending].ask_ar if g.lang == "ar" else SLOTS[st.pending].ask_en)
            return "thanks", TurnResult(msg, "ask" if st.pending else "clarify", g.lang, pillar, st.intent, title,
                                        question_slot=st.pending, facts=self._public_facts(st),
                                        quick_replies=QUICK_NEXT.get(pillar or "", [])[:2] or
                                        ["Will my savings last?", "Help me decide about a loan", "Check a company for me"])
        if st.last_safety and WHAT_NOW.search(text) and not S.numbers_in(text):
            return "safety_repeat", TurnResult(
                ("Here are the steps again. " if g.lang != "ar" else "إليك الخطوات مرة أخرى. ") + st.last_safety,
                "safety", g.lang, "scams", quick_replies=["Help me talk to my bank", "Check a company name"])
        if it and it.id == "explain_term" and st.main_intent:
            it = BY_ID[st.main_intent]                     # "should I do it?" is about the loan, not the word we explained
        if it and st.completed and not st.pending and JUDGE.search(text) and not S.numbers_in(text) \
                and not (it.id in NAME_INTENTS and ABOUT_IT.search(text)):
            return "how_to_judge", TurnResult(HOW_TO_JUDGE[it.pillar], "ask", g.lang, it.pillar, it.id, it.title,
                                              facts=self._public_facts(st), quick_replies=QUICK_NEXT.get(it.pillar, []))
        return None

    def _estimate_help(self, st: Session, g) -> TurnResult:
        """'I don't know' -> make a rough answer easy instead of ending the conversation."""
        slot = st.pending
        it = BY_ID[st.intent] if st.intent else None
        examples = RANGES.get(slot)
        if g.lang == "ar":
            msg = "لا بأس، رقم تقريبي يكفي ويمكنك تغييره في أي وقت. " + SLOTS[slot].ask_ar
        elif examples:
            msg = (f"That's fine, a rough guess is enough, and you can change it any time. Is it closer to "
                   f"{', '.join(examples[:-1])} or {examples[-1]}?")
        else:
            msg = "That's fine, a rough guess is enough, and you can change it any time. " + SLOTS[slot].ask_en
        return TurnResult(msg, "ask", g.lang, it.pillar if it else None, st.intent, it.title if it else None,
                          question_slot=slot, quick_replies=RANGE_REPLIES.get(slot, ["Skip this"]),
                          facts=self._public_facts(st))

    def _summary_and_ask(self, st: Session, g) -> TurnResult:
        """'I just said it' -> show what was understood, and exactly what is still needed."""
        it = BY_ID[st.intent] if st.intent else None
        it_slots = set(it.required + it.optional) if it else set()
        have = [f for f in self._public_facts(st) if not it_slots or f["slot"] in it_slots]
        got = ", ".join(f"{f['label']} {f['display']}" for f in have) or "nothing yet"
        msg = (f"Sorry about that. Here is what I have so far: {got}. I still need your {SLOTS[st.pending].label}. "
               f"You can just say the number, for example {RANGES.get(st.pending, ['5,000', '5,000'])[1]}.")
        return TurnResult(msg, "ask", g.lang, it.pillar if it else None, st.intent, it.title if it else None,
                          question_slot=st.pending, quick_replies=RANGE_REPLIES.get(st.pending, []), facts=have)

    def _ai_facts(self, st: Session, text: str, ex: dict, trace: list) -> dict:
        """Optional: let the AI read garbled speech for the facts we need. Every number must appear in the words."""
        if not self.llm.available or not hasattr(self.llm, "extract_facts"):
            return {}
        wanted = [st.pending] if st.pending else []
        if st.intent:
            wanted += [k for k in BY_ID[st.intent].required + BY_ID[st.intent].optional if k not in st.facts]
        wanted = [k for k in dict.fromkeys(wanted) if k in SLOTS and SLOTS[k].kind in ("money", "pct", "age", "years")
                  and k not in ex]
        if not wanted or not S.numbers_in(text):
            return {}
        proposed = self.llm.extract_facts(text, {k: SLOTS[k].label for k in wanted}) or {}
        said = [n.value for n in S.numbers_in(text)]
        ok = {k: float(v) for k, v in proposed.items()
              if k in wanted and isinstance(v, (int, float)) and any(abs(float(v) - n) < 0.01 for n in said)}
        if proposed:
            trace.append({"step": "ai_facts", "proposed": len(proposed), "accepted": len(ok)})
        return ok

    def _resolve(self, st: Session, it) -> dict:
        facts = dict(st.facts)
        for slot, alts in ALIASES.get(it.id, {}).items():
            if facts.get(slot) is None:
                for a in alts:
                    if facts.get(a) is not None:
                        facts[slot] = facts[a]
                        break
        return facts

    def _correction_target(self, st: Session, text: str) -> str | None:
        nums = S.numbers_in(text)
        if not nums:
            return st.last_set[-1] if st.last_set else None
        new = nums[0].value
        best, score = None, None
        for k in st.last_set:
            old = st.facts.get(k)
            if not isinstance(old, (int, float)) or k not in SLOTS or SLOTS[k].kind != ("pct" if nums[0].is_percent else SLOTS[k].kind):
                continue
            o, n = str(int(old)), str(int(new))
            sc = 0 if (o in n or n in o) else abs((new - old) / old) if old else 9
            if score is None or sc < score:
                best, score = k, sc
        return best

    def _noted(self, st: Session, set_now: list[str]) -> list[dict]:
        out = []
        for k in dict.fromkeys(set_now):
            if k not in SLOTS or k in ("entity_name", "domain"):
                continue
            v, slot = st.facts.get(k), SLOTS[k]
            if isinstance(v, list):
                disp = ", ".join(f"AED {x:,.0f}" for x in v)
            elif slot.kind == "money" and v == 0:
                disp = "none"
            elif slot.kind == "money":
                disp = f"AED {v:,.0f}"
            elif slot.kind == "pct":
                disp = f"{v:g}%"
            elif slot.kind == "age":
                disp = f"{v:g}"
            elif slot.kind == "years":
                disp = f"{v:g} years"
            else:
                disp = str(v)
            out.append({"slot": k, "label": slot.label, "value": v, "display": disp})
        return out

    def _readback(self, noted: list[dict], lang: str, st: "Session | None" = None) -> str:
        if not noted or lang == "ar":
            return ""
        parts = [f"age {n['display']}" if n["label"] == "age" else f"no {n['label']}" if n["display"] == "none"
                 else f"{n['label']} of {n['display']}" for n in noted]
        txt = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        hint = "" if st is not None and st.hinted else " If I misheard, just correct me."
        if st is not None:
            st.hinted = True
        return f"I noted {txt}.{hint}"

    @staticmethod
    def _pause_prefix(g) -> str:
        if g.level != "PAUSE":
            return ""
        why = g.signals[0].why_for(g.lang) if g.signals else ""
        if g.lang == "ar":
            return ("دعنا نتمهل قليلاً. " + why).strip() + " "
        return ("Let's slow down for a moment. " + why).strip() + " "

    @staticmethod
    def _ask_replies(slot_id: str) -> list[str]:
        slot = SLOTS[slot_id]
        if slot.kind == "yesno":
            return ["Yes", "No"]
        if slot.allow_zero:
            return ["None", "I don't know"]
        return ["I don't know"]

    def _public_facts(self, st: Session) -> list[dict]:
        """Every fact in use this conversation, labelled for the 'Facts I am using' chips."""
        return self._noted(st, [k for k in st.facts if k in SLOTS])

    @staticmethod
    def _evidence(a: Answer) -> dict:
        checks = []
        for c in a.checks:
            if "verdict" in c:
                checks.append({"type": "entity_check", "query": c["query"], "verdict": c["verdict"], "flags": c["flags"],
                               "lists": [{"dataset_id": d, "name": r["name"], "searched": r["searched"], "match": r["match"],
                                          "snapshot_id": r["snapshot_id"], "fetched_at": r["fetched_at"],
                                          "candidates": r["candidates"][:3]} for d, r in c["results"].items()]})
            else:
                checks.append({"type": "search", "dataset_id": c["dataset_id"], "name": c["name"], "searched": c["searched"],
                               "match": c["match"], "snapshot_id": c["snapshot_id"], "fetched_at": c["fetched_at"],
                               "candidates": c["candidates"][:3]})
        return {"calculations": a.calcs, "checks": checks, "data": a.data, "points": a.points, "next_steps": a.next_steps,
                "assumptions": a.assumptions, "limitations": a.limitations, "tools": a.tools}

    @staticmethod
    def _log(sid: str, r: TurnResult, t0: float) -> None:
        log.info("turn session=%s mode=%s level=%s pillar=%s intent=%s ask=%s ai=%s tools=%s ms=%d",
                 sid[:6], r.mode, r.level, r.pillar, r.intent, r.question_slot, r.ai_used,
                 ",".join(r.evidence.get("tools", [])) if r.evidence else "", (time.time() - t0) * 1000)


def pillars_catalogue() -> list[dict]:
    """For the home screen: the three pillars and their example questions."""
    examples = {"knowledge": ["Will my savings last if I retire at 60?", "Is my money keeping up with prices?",
                              "I'm worried about living to 95"],
                "decisions": ["My bank is offering me a loan", "Should I use savings to clear my debt?",
                              "Should I sell my home and invest?"],
                "scams": ["Someone offers me 10% a month", "Is this company licensed?", "Is this influencer authorised?"]}
    return [{"id": p, "title": t[0], "title_ar": t[1], "examples": examples[p],
             "intents": [{"id": i.id, "title": i.title} for i in INTENTS if i.pillar == p]} for p, t in PILLARS.items()]

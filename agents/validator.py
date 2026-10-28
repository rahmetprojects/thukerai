"""
agents/validator.py - the evidence & safety check every reply must pass before anyone hears it
=============================================================================================

WHAT IT DOES
    Plain code (no AI: a model grading itself is not a safety layer). Given the reply text and the evidence
    the workflow produced, it returns pass / repair / replace:

      R1 recommendation      "I recommend", "you should buy/sell/invest"...                       -> replace
      R2 guarantee           "guaranteed", "risk-free" - unless used as a WARNING                  -> replace
      R3 invented numbers    every amount / percent in the reply must come from a calculation,
                             an official record, the person's own words, or the fixed template
                             constants listed below                                                -> replace
      R4 false reassurance   "is safe", "legitimate", "you can trust", "your money is protected"
                             - unless negated ("is not proof it is safe")                          -> replace
      R5 missing caveat      an entity check that found nothing / found a licence must say it is
                             NOT proof of safety                                                   -> repair (append)
      R6 data honesty        if a dataset used was unavailable or stale, the reply must say so     -> repair (append)

    "replace" means: the caller speaks the deterministic template instead (used when an AI rephrasing breaks
    a rule). Templates themselves are tested to pass (tests/test_validator.py), so the template is always safe.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from guardian.lang import normalize_arabic, normalize_digits

RECOMMEND = re.compile(
    r"\bi (?:strongly |would |do )?(?:recommend|suggest) (?:you )?(?:buy|sell|invest|put|move|choose|take|accept)"
    r"|\byou should (?:buy|sell|invest|put|move|transfer|choose|accept|take|cancel)\b"
    r"|\bgo ahead and (?:buy|invest|sell|accept)\b|\b(?:the )?best (?:investment|fund|stock|bank|option) (?:is|for you)\b"
    r"|\b(?:buy|invest in) (?:this|that|it) now\b", re.I)
RECOMMEND_AR = re.compile(r"انصحك ب(?:شراء|بيع|الاستثمار)|عليك (?:ان )?(?:تشتري|تستثمر|تبيع)|افضل استثمار لك")
GUARANTEE = re.compile(r"\b(?:guarantee[sd]?|risk[- ]free|cannot lose|can't lose|sure profit|no risk)\b", re.I)
REASSURE = re.compile(r"\b(?:is|are) (?:safe|legit(?:imate)?|trustworthy|genuine|not a scam)\b|\byou can trust\b"
                      r"|\b(?:your|the) (?:money|deposit|savings) (?:is|are|will be) (?:safe|protected|secure)\b|\bno need to worry\b", re.I)
WARN_BEFORE = re.compile(r"(?:\b(?:no|none|neither|not|never|nothing|without|cannot|isn'?t|doesn'?t|does not|don'?t|proof|mean|means|prove|"
                         r"beware|wary|fake|promis\w*|claims?|offers?|scam\w*|warning|whether|if|assum\w*|says?|said|than)\b|n't)"
                         r"\W+(?:\w+\W+){0,5}$", re.I)
WARN_AFTER = re.compile(r"^\W*(?:[\w-]+\W+){0,9}?(?:warning|scam\w*|fraud\w*|red flag|sign|fake|lie|too good|is not|isn't)", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(\s*%)?")
CAVEAT = re.compile(r"not proof|does not (?:guarantee|prove|mean)|doesn't (?:guarantee|prove|mean)|cannot prove|can't prove|"
                    r"is not the same as a licence|not a recommendation|can't say it is the same", re.I)
DATA_NOTE = re.compile(r"not available|could not (?:check|load)|not loaded|illustration|out of date|days old|copied from", re.I)

# Numbers the fixed templates themselves use (scenario sizes, rule-of-thumb bands, the police number ...).
TEMPLATE_CONSTANTS = {0.5, 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 25, 30, 50, 90, 95, 100, 999, 50000, 2.0, 0.25}


@dataclass
class Verdict:
    action: str                       # pass | repair | replace
    problems: list[str] = field(default_factory=list)
    text: str = ""


def _numbers(obj) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [abs(float(obj))]
    if isinstance(obj, str):
        return [float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")) for m in NUMBER.finditer(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers(v)]
    if isinstance(obj, (list, tuple)):
        return [n for v in obj for n in _numbers(v)]
    return []


def _unwarned(text: str, pat: re.Pattern) -> bool:
    for m in pat.finditer(text):
        if not (WARN_BEFORE.search(text[:m.start()]) or WARN_AFTER.search(text[m.end():])):
            return True
    return False


def allowed_numbers(evidence: dict, user_texts: list[str]) -> list[float]:
    allowed = list(TEMPLATE_CONSTANTS)
    allowed += _numbers(evidence)
    for t in user_texts:
        allowed += _numbers(normalize_digits(t))
    # derived values the templates legitimately compute: x12 per year, halves, simple rounding of years
    extra = []
    for n in allowed:
        extra += [n * 12, n / 12, n / 2, round(n), round(n, 1), round(n) % 12, round(n) // 12]
    return allowed + extra


def validate(text: str, evidence: dict, user_texts: list[str], *, entity_verdict: str | None = None,
             data_states: list[dict] | None = None) -> Verdict:
    t = normalize_digits(text)
    ar = normalize_arabic(t)
    problems: list[str] = []
    if RECOMMEND.search(t) or RECOMMEND_AR.search(ar):
        problems.append("R1_recommendation")
    if _unwarned(t, GUARANTEE):
        problems.append("R2_guarantee")
    if _unwarned(t, REASSURE):
        problems.append("R4_false_reassurance")
    allowed = allowed_numbers(evidence, user_texts)
    for m in NUMBER.finditer(t):
        raw = m.group(1).replace(",", "")
        n = float(raw + ("." + m.group(2) if m.group(2) else ""))
        if 1900 <= n <= 2100 and not m.group(3):
            continue                                            # years are checked by the data, not here
        if n < 10 and not m.group(3):
            continue                                            # small counts ("three things", "6 months") are template wording
        tol = max(0.6, n * 0.006)
        if not any(abs(n - a) <= tol for a in allowed):
            problems.append(f"R3_unsupported_number:{m.group(0).strip()}")
            break
    if problems:
        return Verdict("replace", problems, text)

    repaired, notes = text, []
    if entity_verdict in ("no_record_found", "licensed_record_found", "registered_record_found",
                          "authorised_finfluencer_found") and not CAVEAT.search(t):
        repaired += " Remember: appearing, or not appearing, on a CMA list is not proof that an offer is safe."
        notes.append("R5_caveat_appended")
    bad_data = [d for d in (data_states or []) if d.get("status") in ("unavailable", "stale")]
    if bad_data and not DATA_NOTE.search(t):
        names = ", ".join(d.get("name", d.get("dataset_id", "")) for d in bad_data)
        repaired += f" Note: some official data I rely on is out of date or not available right now ({names})."
        notes.append("R6_data_note_appended")
    return Verdict("repair" if notes else "pass", notes, repaired)


def evidence_of(answer) -> dict:
    """The parts of a workflow Answer whose numbers a reply may quote."""
    return json.loads(json.dumps({"calcs": answer.calcs, "checks": answer.checks, "points": answer.points},
                                 default=str))

"""
tests/test_understanding.py - speech to facts, intents, guardian levels, entity matching, validator rules
=======================================================================================================
"""
import pytest

from agents.intents import classify
from agents.slots import answer_for, entity_name, extract, is_correction, words_to_number
from agents.validator import validate
from guardian.guard import guard
from store.names import core_name, domains_in, split_listed_names


# ------------------------------------------------------------------ numbers in speech
@pytest.mark.parametrize("words,value", [("fifteen thousand", 15000), ("two hundred and fifty thousand", 250000),
                                         ("a quarter of a million", 250000), ("half a million", 500000),
                                         ("one million two hundred thousand", 1200000)])
def test_word_numbers(words, value):
    assert words_to_number(words) == value


def test_fifteen_thousand_never_becomes_fifty():
    assert answer_for("savings", "money", "about fifteen thousand") == 15000


def test_numbers_assigned_by_nearest_cue():
    f = {k: v["value"] for k, v in extract("I have AED 180,000 saved, spend AED 5,500 a month, offered 10% a month").items()}
    assert f["savings"] == 180000 and f["monthly_spending"] == 5500 and f["promised_rate_pct"] == 10


def test_target_age_is_not_current_age():
    f = extract("I'm worried about running out of money if I live to 90 or 95")
    assert "current_age" not in f and f["target_ages"]["value"] == [90, 95]


def test_zero_only_when_allowed():
    assert answer_for("monthly_income", "money", "none", allow_zero=True) == 0
    assert answer_for("savings", "money", "none", allow_zero=False) is None


def test_corrections_and_names():
    assert is_correction("no, I said 150,000") and not is_correction("no pension")
    assert entity_name("Is a company called Zentrova Wealth Partners licensed?") == "Zentrova Wealth Partners"
    assert entity_name("Someone offers a 'safe' investment") is None


# ------------------------------------------------------------------ intents (three pillars)
@pytest.mark.parametrize("text,intent", [
    ("Someone on WhatsApp promises 5% a week", "suspicious_offer"),
    ("Is Faraz Financial Services licensed?", "check_entity"),
    ("Should I use my savings to pay off my credit card?", "debt_vs_savings"),
    ("Will my pension and savings last if I retire next year?", "retirement_readiness"),
])
def test_intents(text, intent):
    assert classify(text, extract(text)).intent.id == intent


def test_unclear_request_is_not_guessed():
    assert classify("hello there").intent is None


# ------------------------------------------------------------------ guardian
@pytest.mark.parametrize("text,level", [
    ("They asked me to read them the code from my SMS", "STOP"),
    ("I installed AnyDesk because the bank told me to", "STOP"),
    ("my card number is 4111 1111 1111 1111", "STOP"),
    ("I already transferred 20,000 to them", "SENT"),
    ("I want to sell my villa", "PAUSE"),
    ("How much have prices gone up since 2020?", "NORMAL"),
])
def test_guardian_levels(text, level):
    assert guard(text).level == level


def test_secrets_never_reach_the_text():
    d = guard("my pin is 4512 and my card is 4111 1111 1111 1111")
    assert "4512" not in d.clean_text and "4111" not in d.clean_text


# ------------------------------------------------------------------ names and matching
def test_warning_title_split():
    t = "1.Faraz Financial Services PTY- Limited 2. PAY2ASSIST DYNAMICS CONSULTANCY - FZCO 3. Website (https://www.market10.net/en)"
    assert split_listed_names(t) == ["Faraz Financial Services PTY- Limited", "PAY2ASSIST DYNAMICS CONSULTANCY - FZCO"]
    assert domains_in(t) == ["market10.net"]
    assert core_name("Example Investment L.L.C") == core_name("example investment llc")


def test_matching_rules(fixture_service):
    s = fixture_service.retrieval.search_engine
    assert s.search("cma_warnings", "Faraz Financial Services")[0].match_type == "normalized"
    assert s.search("cma_warnings", "", domain="https://market10.net/login")[0].match_type == "domain"
    assert s.search("cma_warnings", "Exampel Capital 3 Ltd")[0].match_type == "probable"     # typo tolerated, labelled
    assert all(c.matched_value != "Example Capital 30 Ltd" for c in s.search("cma_warnings", "Example Capital 3"))
    assert s.search("cma_warnings", "Capital Investment Services") == []                   # generic words alone


def test_check_entity_never_says_safe(fixture_service):
    r = fixture_service.retrieval.check_entity("Zentrova Wealth Partners")
    assert r["verdict"] == "no_record_found"
    assert any("does not mean" in x for x in r["limitations"])
    lic = fixture_service.retrieval.check_entity("4T Global Markets Financial Services LLC", "https://fake-4tglobal.com")
    assert lic["verdict"] == "licensed_record_found"
    assert any(f.startswith("website_mismatch") for f in lic["flags"])


# ------------------------------------------------------------------ validator
def test_validator_rules():
    ev = {"calcs": [{"outputs": {"months": 50}}]}
    assert validate("Your savings last about 50 months.", ev, []).action == "pass"
    assert validate("Your savings last about 70 months.", ev, []).action == "replace"           # invented number
    assert validate("I recommend you buy this fund.", ev, []).action == "replace"
    assert validate("This investment is guaranteed.", ev, []).action == "replace"
    assert validate("No investment is guaranteed.", ev, []).action == "pass"
    assert validate("Good news, the company is safe.", ev, []).action == "replace"
    assert validate("That is not proof that it is safe.", ev, []).action == "pass"
    r = validate("I found no record.", ev, [], entity_verdict="no_record_found")
    assert r.action == "repair" and "not proof" in r.text


# ------------------------------------------------------------------ speech slips (real example from testing)
def test_currency_slips_are_repaired_and_reported():
    from agents.hearing import repair
    text, fixes = repair("So I have 100,000 diplomats and I want to swim all the time. My son is 18.")
    assert "100,000 dirhams" in text and fixes == [{"heard": "100,000 diplomats", "as": "100,000 dirhams"}]
    assert "swim all the time" in text                       # never guesses words it cannot be sure about
    assert repair("my diplomats friend")[1] == []            # only right after a number
    assert repair("I have 120,000 dhs")[0] == "I have 120,000 dirhams"

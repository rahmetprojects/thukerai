"""
tests/test_conversation_api.py - the coordinator end to end, the web API, and the 30-scenario evals
=================================================================================================
"""
from fastapi.testclient import TestClient

from api.app import create_app
from ingest.scheduler import Scheduler

SID = "test-session-0000000001"


def test_asks_one_question_at_a_time_then_answers(fixture_service):
    c = fixture_service.coordinator
    r1 = c.turn(SID, "I have AED 250,000 saved. Can I retire?")
    assert r1.mode == "ask" and r1.question_slot == "monthly_spending" and r1.pillar == "knowledge"
    r2 = c.turn(SID, "5,000 a month")
    assert r2.question_slot == "monthly_income"
    r3 = c.turn(SID, "none")
    assert r3.mode == "answer" and "50 months" in r3.reply
    assert r3.evidence["calculations"][0]["tool"] == "savings_runway"
    assert any(d["dataset_id"] == "fcsc_cpi_annual" for d in r3.evidence["data"])       # current data changed the answer
    r4 = c.turn(SID, "what if I spend 4,000 a month?")                                   # follow-up reuses the facts
    assert r4.mode == "answer" and "62.5 months" in r4.reply


def test_current_data_changes_the_answer(fixture_service):
    """Counterfactual: the inflation-adjusted runway must move when the official CPI moves."""
    from tools import calculators as C
    a = C.savings_runway(250000, 5000, 0, 1.0).outputs["months_with_assumptions"]
    b = C.savings_runway(250000, 5000, 0, 6.0).outputs["months_with_assumptions"]
    assert b < a


def test_safety_script_skips_everything(fixture_service):
    r = fixture_service.coordinator.turn("test-session-0000000002", "the police called and want my OTP")
    assert r.mode == "safety" and r.level == "STOP" and "999" in r.reply and not r.evidence


def test_api_endpoints(fixture_service):
    client = TestClient(create_app(fixture_service, Scheduler(fixture_service.db)))
    assert client.get("/api/health").json()["ok"]
    r = client.post("/api/chat", json={"session": SID + "x", "text": "Is Faraz Financial Services licensed?"}).json()
    assert r["intent"] == "check_entity" and r["evidence"]["checks"][0]["verdict"] == "warning_found"
    assert "trace" not in r
    st = client.get("/api/data/status").json()
    assert len(st["datasets"]) == 13 and st["not_fetched"]
    browse = client.get("/api/data/records/cma_warnings?q=faraz").json()
    assert browse["total"] == 1 and browse["match"] == "possible"         # one word: only ever a "possible" match
    assert client.post("/api/chat", json={"session": "short", "text": "hi"}).status_code == 422
    assert client.get("/api/nope").status_code == 404


def test_thirty_scenarios_pass():
    from evals.run import main
    assert main([]) == 0


def test_intents_without_benchmark_scenarios(fixture_service):
    c = fixture_service.coordinator
    r = c.turn("test-session-0000000003", "What is a flat rate?")
    assert r.intent == "explain_term" and "flat rate charges interest" in r.reply
    r = c.turn("test-session-0000000004", "Is the influencer called Promoter 12 authorised to promote stocks?")
    assert r.intent == "finfluencer_check" and "authorised finfluencers" in r.reply
    r = c.turn("test-session-0000000005", "How do I spot investment scams?")
    assert r.intent == "scam_tips" and "warning signs" in r.reply


def test_misheard_speech_is_read_back_and_correctable(fixture_service):
    c = fixture_service.coordinator
    r = c.turn("test-session-0000000006", "So I have 100,000 diplomats and I want to swim all the time. My son is 18.")
    assert r.heard and "AED 100,000" in r.reply              # the misheard amount is read back at once
    r = c.turn("test-session-0000000006", "no, I said 120,000")
    r = c.turn("test-session-0000000006", "I want to retire")
    assert r.intent == "retirement_readiness" and any(f["display"] == "AED 120,000" for f in r.facts)


def test_transcribe_endpoint(fixture_service):
    client = TestClient(create_app(fixture_service, Scheduler(fixture_service.db)))
    assert client.get("/api/health").json()["stt"] == "browser"
    assert client.post("/api/transcribe", content=b"\x1aE\xdf\xa3" * 1000,
                       headers={"content-type": "audio/webm"}).status_code == 501      # no key: browser recognition only

    class FakeLLM:
        provider, model, available, can_transcribe = "gemini", "fake", True, True
        def transcribe(self, audio, mime, lang): return "I have 120,000 dirhams and I want to retire"
        def rephrase(self, *a): return None
        def understand(self, *a): return None
    old = fixture_service.llm
    fixture_service.llm = FakeLLM()
    try:
        r = client.post("/api/transcribe?lang=en", content=b"x" * 5000, headers={"content-type": "audio/webm"})
        assert r.json()["text"].startswith("I have 120,000 dirhams")
        assert client.get("/api/health").json()["stt"] == "gemini"
    finally:
        fixture_service.llm = old


def test_never_crashes_or_dead_ends_after_i_dont_know(fixture_service):
    """Regression for a real session: 'I don't know' used to end the workflow; the next answer then crashed."""
    c, sid = fixture_service.coordinator, "test-session-0000000007"
    c.turn(sid, "I have 300,000 saved and want to retire")
    r = c.turn(sid, "I don't know")
    assert r.mode == "ask" and r.question_slot == "monthly_spending" and r.quick_replies   # ranges offered
    r = c.turn(sid, "maybe 4,000 dirhams")
    assert r.question_slot == "monthly_income"
    r = c.turn(sid, "i just said it")
    assert "Here is what I have so far" in r.reply and "AED 4,000" in r.reply


def test_bug_in_a_turn_is_answered_gracefully(fixture_service, monkeypatch):
    c = fixture_service.coordinator
    monkeypatch.setattr(c, "_turn", lambda *a, **k: 1 / 0)
    r = c.turn("test-session-0000000008", "hello")
    assert "something went wrong on my side" in r.reply


def test_ai_facts_cannot_invent_numbers(fixture_service):
    class FakeLLM:
        provider, model, available, can_transcribe = "gemini", "fake", True, False
        def extract_facts(self, text, wanted): return {"monthly_spending": 7000, "monthly_income": 2500}   # 2500 never said
        def rephrase(self, *a): return None
        def understand(self, *a): return None
    c = fixture_service.coordinator
    old, c.llm = c.llm, FakeLLM()
    try:
        sid = "test-session-0000000009"
        c.turn(sid, "I have 400,000 saved and want to retire")
        r = c.turn(sid, "it's around seven thousand, give or take, 7000")
        facts = {f["slot"]: f["value"] for f in r.facts}
        assert facts.get("monthly_spending") == 7000 and "monthly_income" not in facts
    finally:
        c.llm = old


def test_follow_ups_after_a_company_check(fixture_service):
    """Regression for a real session: every follow-up used to repeat the first company's answer."""
    c, sid = fixture_service.coordinator, "test-session-0000000010"
    c.turn(sid, "Check a company for me")
    assert "Seid Fayaz" in c.turn(sid, "Seid Fayaz").reply
    r = c.turn(sid, "HOXTON FX")                                   # a bare name = check this one
    assert "HOXTON FX" in r.reply and "Seid Fayaz" not in r.reply
    r = c.turn(sid, "They contacted me on WhatsApp")
    assert r.intent == "unsolicited_contact" and "out of the blue" in r.reply
    assert c.turn(sid, "check another company").question_slot == "entity_name"
    c.turn(sid, "Faraz Financial Services")
    r = c.turn(sid, "is it licensed?")
    assert r.reply.startswith("No.") and "warning" in r.reply


def test_small_talk_and_follow_ups(fixture_service):
    """Regressions from a batch of realistic conversations: thanks, 'is that OK?', 'what now?', shares, websites."""
    c = fixture_service.coordinator
    sid = "test-session-0000000011"
    for t in ("the bank offered me a loan", "200,000", "6%", "7 years", "15000 salary", "9000"):
        r = c.turn(sid, t)
    assert r.mode == "answer"
    r = c.turn(sid, "is that ok?")
    assert "how to judge" in r.reply and "AED" not in r.reply           # guidance, not a recommendation
    assert c.turn(sid, "thanks").reply.startswith("You're welcome")

    sid = "test-session-0000000012"
    assert c.turn(sid, "someone called from the police and wants my emirates id").mode == "safety"
    r = c.turn(sid, "what do I do now")
    assert r.mode == "safety" and r.reply.startswith("Here are the steps again")

    sid = "test-session-0000000013"
    r = c.turn(sid, "I have 80k in emaar shares")
    assert r.intent == "concentration" and r.question_slot == "total_investable"
    assert "Good question" in c.turn(sid, "should I buy more").reply
    assert c.turn(sid, "my total savings are 250k").mode == "answer"

    sid = "test-session-0000000014"
    c.turn(sid, "check 4T global markets")
    r = c.turn(sid, "their website is 4tglobal-invest.net")
    assert "their website" not in r.reply                                # a website is not a company name


def test_spoken_names_in_lower_case(fixture_service):
    """Regression for a real session: 'its name is hoxton fx' was checked as the name 'its name is hoxton fx'."""
    c, sid = fixture_service.coordinator, "test-session-0000000015"
    r = c.turn(sid, "Is this company genuine?")
    assert r.question_slot == "entity_name"
    r = c.turn(sid, "its name is hoxton fx")
    assert "“hoxton fx”" in r.reply and "its name is" not in r.reply
    r = c.turn(sid, "is hoxton fx licensed?")                           # same name again: the short verdict
    assert r.reply.startswith("I could not find “hoxton fx”") and "its name is" not in r.reply
    r = c.turn(sid, "is faraz financial services licensed?")             # a different name, lower case: check it
    assert r.reply.startswith("No.") or "warning" in r.reply

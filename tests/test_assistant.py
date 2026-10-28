"""
tests/test_assistant.py - the single assistant: front agent, master, specialists, explainer, composer, verifier
==============================================================================================================

What is proven here (offline, no AI key, on the fake CMA data):
    * the front agent sends every kind of order to the right place (check, data, explain, money, onboard, safety,
      follow-up, welcome, "no thanks"), and the Guardian always goes first
    * every served card is fully cited: each statement has citation numbers that exist on the card, records carry
      their dataset, official page and collection time, and explanations cite the glossary separately
    * explanations: answers say what they mean, offer the jargon they used, "yes" explains the offered term with
      the datasets where it appears (row counts, pages), and an explained term is not offered again
    * a promised return in a message is worked out by the calculator, in parallel with the look-up
    * money questions run the voice guide's multi-turn calculator, and a new data question breaks out of it
    * the verifier catches uncited statements, numbers that are not in the evidence, and verdict wording
    * onboarding through the chat: a proposal card, nothing saved until approval, then the new source is searchable;
      non-official websites are refused without a single request
    * the streaming endpoint sends order -> recipe -> steps -> verify -> card -> done
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from ingest.registry import load_registry
from kitchen.kitchen import Kitchen
from kitchen.verifier import verify


@pytest.fixture()
def k(fixture_service):
    kit = Kitchen(fixture_service)
    yield kit
    for sid in list(kit.sessions):
        kit.reset(sid)


def _ask(k, text, sid="s-test-0000000001"):
    r = k.run(sid, text)
    order = r["events"][0]["data"]
    return order, r["card"], r["events"]


def _all_cited(card):
    ok, problems = verify(card)
    assert ok, problems
    ns = {c["n"] for c in card["citations"]}
    assert ns == set(range(1, len(ns) + 1))


# ---------------------------------------------------------------------------- routing
@pytest.mark.parametrize("text,kind", [
    ("hello", "welcome"),
    ("yes", "ack"),                                                        # nothing offered: not a name to check
    ("Is market10.net licensed?", "check"),
    ("@quickprofits_uae", "check"),
    ("BitWealth Pro promised me 15% a month, is it real?", "check"),
    ("What is a passported fund?", "explain"),
    ("what does EBITDA mean", "explain"),
    ("Which firms were warned against this month?", "data"),
    ("how many passported funds are there", "data"),
    ("what data do you have", "data"),
    ("I have 300,000 saved. How long will it last?", "money"),
    ("add this dataset https://opendata.example.gov.ae/dataset/x", "onboard"),
    ("I already transferred 20,000 to them", "safety"),
    ("They asked me to read them the code from my SMS", "safety"),
])
def test_front_agent_routes(fixture_service, text, kind):
    k = Kitchen(fixture_service)
    order, card, _ = _ask(k, text, sid="route-" + "0" * 12)
    assert order["kind"] == kind, (text, order)
    k.reset("route-" + "0" * 12)


# ---------------------------------------------------------------------------- cards, citations, explanations
def test_check_card_is_cited_and_explained(k):
    _, c, events = _ask(k, "Is market10.net licensed?")
    assert c["type"] == "fact" and c["tone"] == "alert" and "CMA warning" in c["headline"]
    rec = next(x for x in c["citations"] if x["kind"] == "record")
    assert rec["dataset_id"] == "cma_warnings" and rec["source_page"].startswith("https://") and rec["fetched_at"]
    assert c["meaning"]["text"].startswith("This means")
    assert any(x["kind"] == "glossary" for x in c["citations"])              # meaning cites the glossary, not a record
    assert [o["term"] for o in c["offers"]][:1] == ["cma_warning"]
    assert "explain" in c["speech"] and len(c["speech"]) < 400
    steps = [e["data"] for e in events if e["event"] == "step"]
    assert {s["id"] for s in steps if s["status"] == "done"} == {"read", "lookup", "explain"}
    _all_cited(c)


def test_yes_explains_the_offered_term_with_its_datasets(k):
    _ask(k, "Is market10.net licensed?")
    order, c, _ = _ask(k, "yes please")
    assert order["kind"] == "explain" and order["term"] == "cma_warning"
    assert c["type"] == "explanation" and c["sentences"][0]["text"].startswith("A CMA warning")
    ds = c["explain"]["datasets"]
    assert ds[0]["dataset_id"] == "cma_warnings" and ds[0]["rows"] == 233 and ds[0]["source_page"]
    _all_cited(c)
    _, again, _ = _ask(k, "Is market10.net licensed?")                    # explained once: not offered again
    assert "cma_warning" not in [o["term"] for o in again["offers"]]


def test_no_thanks_clears_the_offer(k):
    _ask(k, "Is market10.net licensed?")
    order, c, _ = _ask(k, "no")
    assert order["kind"] == "ack" and not k.session("s-test-0000000001").offered


def test_unknown_term_is_not_improvised(k):
    _, c, _ = _ask(k, "what does EBITDA mean")
    assert c["type"] == "cannot_answer" and "don't have an explanation" in c["headline"]


def test_promised_return_is_calculated_in_parallel(k):
    _, c, events = _ask(k, "BitWealth Pro promised me 15% a month, is it real?")
    calc = next(s for s in c["sentences"] if "a year" in s["text"])
    assert "435%" in calc["text"] and "53,503" in calc["text"]
    assert c["citations"][calc["cites"][0] - 1]["kind"] == "calculation"
    assert any(e["event"] == "step" and e["data"]["id"] == "calc" and e["data"]["status"] == "done" for e in events)
    assert "compounding" in [o["term"] for o in c["offers"]]
    assert c["meaning"]["text"].startswith("This means the promise itself is the warning sign")
    _all_cited(c)


def test_data_answers_cite_each_record(k):
    _, c, _ = _ask(k, "how many passported funds are there")
    assert c["type"] == "number" and c["number"] == 144
    assert c["meaning"] and "passported" in c["meaning"]["text"]
    assert all(r["cites"] and c["citations"][r["cites"][0] - 1]["record_key"] == r["record_key"] for r in c["rows"])
    assert c["api"]["url"].startswith("/api/v1/query?op=count")
    _all_cited(c)
    _, w, _ = _ask(k, "Which firms were warned against this month?")
    assert w["type"] == "table" and all(r["dataset_id"] == "cma_warnings" for r in w["rows"])


def test_follow_up_uses_the_last_entity(k):
    _ask(k, "Is market10.net licensed?")
    order, c, _ = _ask(k, "are they warned?")
    assert order["kind"] == "followup" and order["text"] == "market10.net" and c["tone"] == "alert"


def test_money_conversation_then_break_out(k):
    o1, c1, _ = _ask(k, "I have 300,000 saved. How long will it last?")
    assert o1["kind"] == "money" and c1["type"] == "ask"
    o2, c2, _ = _ask(k, "about 8,000 a month")
    assert o2["kind"] == "money" and c2["type"] == "ask"
    o3, c3, _ = _ask(k, "how many passported funds are there")           # a new question is not an answer
    assert o3["kind"] == "data"
    o4, c4, _ = _ask(k, "I have 300,000 saved. How long will it last?")
    o5, c5, _ = _ask(k, "none")
    assert c5["type"] == "calculation" and "37" in c5["headline"] + " ".join(s["text"] for s in c5["sentences"])
    assert any(x["kind"] == "calculation" for x in c5["citations"])
    infl = next(s for s in c5["sentences"] if "FCSC" in s["text"])        # the inflation figure cites the FCSC data
    assert any(c5["citations"][n - 1]["kind"] == "dataset" for n in infl["cites"])


def test_safety_goes_first(k):
    order, c, events = _ask(k, "I already transferred 20,000 to them, is market10.net licensed?")
    assert order["kind"] == "safety" and c["type"] == "safety" and c["tone"] == "alert"
    assert not any(e["event"] == "recipe" for e in events)                # no kitchen work before safety


# ---------------------------------------------------------------------------- the verifier
def test_verifier_catches_planted_problems():
    base = {"type": "fact", "headline": "Named in a warning published 22 Oct 2026.", "headline_cites": [1],
            "citations": [{"n": 1, "kind": "record", "date": "2026-10-22", "record_key": "k1"}],
            "sentences": [], "facts": []}
    assert verify(base)[0]
    planted = dict(base, sentences=[{"text": "They have 5000 clients.", "cites": [1]}])
    ok, problems = verify(planted)
    assert not ok and "5000" in problems[0]
    assert not verify(dict(base, sentences=[{"text": "This firm is safe.", "cites": [1]}]))[0]
    assert not verify(dict(base, sentences=[{"text": "A fact.", "cites": []}]))[0]
    assert not verify(dict(base, headline_cites=[7]))[0]


# ---------------------------------------------------------------------------- onboarding through the chat
def test_onboarding_through_the_chat(tmp_path, monkeypatch):
    import config
    from onboard.discover import Discovery
    from service import build_service
    from store.db import Database
    from tests.fake_gov import FakeGov
    gov = FakeGov()
    monkeypatch.setattr(config, "LOCAL_REGISTRY_YAML", tmp_path / "registry.local.yaml")
    load_registry.cache_clear()
    try:
        svc = build_service(Database(tmp_path / "o.sqlite3"))
        k = Kitchen(svc, discovery_factory=lambda: Discovery(gov.transport()), transport=gov.transport())
        sid = "onboard-" + "0" * 10
        n = len(gov.requests)
        _, bad, _ = _ask(k, "please add https://scam-site.com/list", sid)
        assert "only add data from official" in bad["headline"] and len(gov.requests) == n
        _, c, _ = _ask(k, "add this dataset https://opendata.example.gov.ae/dataset/licensed-brokers", sid)
        assert c["type"] == "proposal" and c["proposal"]["proposals"]
        assert not (tmp_path / "registry.local.yaml").exists()                # nothing saved before approval
        p = c["proposal"]["proposals"][0]
        res = k.approve(c["proposal"]["discovery"], p["id"], {"official_name": "Licensed Brokers"})
        assert res["ok"] and res["rows"] == 4
        _, f, _ = _ask(k, "Falcon Crest Brokerage LLC", sid)
        assert f["tone"] == "record" and "Licensed Brokers" in f["headline"]
        assert k.approve("nope-nope-nope", "x")["ok"] is False
    finally:
        load_registry.cache_clear()


# ---------------------------------------------------------------------------- the streaming endpoint
def test_streaming_endpoint(fixture_service):
    c = TestClient(create_app(fixture_service))
    sid = "stream-" + "0" * 12
    r = c.post("/api/assistant", json={"session": sid, "text": "Is market10.net licensed?", "mode": "voice"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in r.text.strip().split("\n\n"):
        name = block.split("\n")[0].removeprefix("event: ")
        events.append((name, json.loads(block.split("\n", 1)[1].removeprefix("data: "))))
    names = [n for n, _ in events]
    assert names[0] == "order" and names[1] == "recipe" and names[-2:] == ["card", "done"] and "verify" in names
    assert events[0][1]["mode"] == "voice"
    card = dict(events)["card"]
    assert card["tone"] == "alert" and card["id"]
    assert c.post("/api/assistant", json={"session": "bad", "text": "x"}).status_code == 422
    assert c.post(f"/api/assistant/image?session={sid}", content=b"x",
                  headers={"content-type": "text/plain"}).status_code == 415
    assert c.post("/api/assistant/approve", json={"session": sid, "discovery": "nonexistent", "proposal": "x"}).status_code == 404
    assert c.post("/api/assistant/reset", json={"session": sid}).json() == {"ok": True}

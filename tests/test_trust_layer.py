"""
tests/test_trust_layer.py - the trust layer: Trust Report, explorer, onboarding agent, API v1, MCP
=================================================================================================

What is proven here (all offline, on the fake CMA site built from real captured responses + fake official sites):
    * signals: names, websites, handles and phone numbers are found in pasted text; platform links are handles
    * Trust Report: warnings, lookalike websites, licences, finfluencers; facts carry sources; never a verdict;
      one record hit by two signals is ONE fact; a check takes well under 200 ms
    * explorer: questions become the right plans (datasets, dates, filters, ops); the API URL it returns
      reproduces the same answer; an AI plan is used only if every field is valid
    * onboarding: CKAN, file links, HTML tables and JSON APIs are detected; non-official domains are refused
      without a request; an approved source is fetched by the normal pipeline and is at once searchable in the
      Trust Report; it lives in the local registry, never the curated one
    * API v1 + MCP: endpoints, CSV, DCAT, CORS, server-side proposals only, MCP over HTTP and over stdio
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from explore.planner import RulePlanner, validate_plan
from ingest.registry import load_registry
from trust.signals import extract

TODAY = date(2026, 10, 28)
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------- signals
def test_signals_from_a_forwarded_message():
    sig = {(s.kind, s.value) for s in extract(
        "Hi dear, I am Sara from BitWealth Pro Capital. Join https://wa.me/971501234567 or call +971 50 123 4567. "
        "Visit https://bitwealth-pro.com and follow https://www.instagram.com/bitwealth.official/")}
    assert ("domain", "bitwealth-pro.com") in sig and ("handle", "bitwealth.official") in sig
    assert ("phone", "971501234567") in sig and ("name", "BitWealth Pro Capital") in sig
    assert not any(k == "domain" and v in ("instagram.com", "wa.me") for k, v in sig)   # platforms are not companies


def test_short_input_is_one_name():
    assert [(s.kind, s.value) for s in extract("Hoxton FX")] == [("name", "Hoxton FX")]
    assert [(s.kind, s.value) for s in extract("050 123 4567")] == [("phone", "971501234567")]


# ---------------------------------------------------------------------------- Trust Report
def test_trust_report_tones_and_sources(fixture_service):
    t = fixture_service.trust
    r = t.check("market10.net")
    assert r["headline"]["tone"] == "alert" and "CMA warning" in r["headline"]["text"]
    f = r["facts"][0]
    assert f["dataset_id"] == "cma_warnings" and f["source_page"].startswith("https://www.uaecma.gov.ae")
    assert f["fetched_at"] and f["record_key"] and f["date"] == "2026-10-22"

    look = t.check("4tglobal-invest.net")                       # a lookalike of a licensed firm's site
    assert look["headline"]["tone"] == "alert" and "4tglobal.ae" in look["headline"]["text"]
    real = t.check("4tglobal.ae")
    assert real["headline"]["tone"] == "record" and "CP-0001315" in real["headline"]["text"]

    fin = t.check("https://www.instagram.com/promoter12/")
    assert fin["headline"]["tone"] == "record" and "finfluencer" in fin["headline"]["text"]

    none = t.check("Hoxton FX")
    assert none["headline"]["tone"] == "none" and "does not prove" in none["headline"]["text"]
    assert none["not_proven"] and none["lists_checked"] >= 10


def test_trust_report_never_gives_a_verdict_and_merges_one_record(fixture_service):
    r = fixture_service.trust.check("Sara from Faraz Financial Services says visit market10.net, guaranteed 15%")
    texts = " ".join(f["text"] for f in r["facts"]).lower()
    for word in (" is safe", "legit", "is a scam", "trustworthy", "guaranteed safe"):
        assert word not in texts
    warn = [f for f in r["facts"] if f["dataset_id"] == "cma_warnings"]
    assert len(warn) == 1 and "Also matched by" in warn[0]["detail"]     # name + website = the same warning
    assert r["took_ms"] < 200


# ---------------------------------------------------------------------------- explorer
@pytest.fixture()
def planner(fixture_service):
    acts = [r["name"] for r in fixture_service.db.records("cma_licensed_activities")]
    return RulePlanner(load_registry(), acts)


@pytest.mark.parametrize("q,op,datasets,since,until,filters", [
    ("Which firms were warned against this month?", "list", ["cma_warnings"], "2026-10-01", None, {}),
    ("How many warnings were published in 2026", "count", ["cma_warnings"], "2026-01-01", "2026-12-31", {}),
    ("licensed companies allowed to do introduction", "list", ["cma_licensed_companies"], None, None, {"activity": "Introduction"}),
    ("passported funds from ADGM", "list", ["cma_passported_funds"], None, None, {"category": "ADGM"}),
    ("finfluencers whose authorisation expired", "list", ["cma_finfluencers"], None, None, {"valid": "no"}),
    ("housing inflation 2025", "list", ["fcsc_cpi_annual"], None, None, {"category": "HOU", "year": "2025"}),
    ("warnings in the last 30 days", "list", ["cma_warnings"], "2026-09-28", None, {}),
    ("what data do you have on funds", "find_datasets", ["cma_local_funds", "cma_passported_funds", "cma_eosg_funds"], None, None, {}),
])
def test_questions_become_plans(planner, q, op, datasets, since, until, filters):
    p = planner.plan(q, TODAY)
    assert (p.op, p.since, p.until, p.filters) == (op, since, until, filters)
    assert sorted(p.datasets) == sorted(datasets)


def test_lookup_and_reproducible_api_url(fixture_service):
    c = TestClient(create_app(fixture_service))
    r = fixture_service.explorer.ask("Is Faraz Financial Services licensed?", today=TODAY)
    assert r["plan"]["op"] == "lookup" and r["report"]["headline"]["tone"] == "alert"
    a = fixture_service.explorer.ask("Which firms were warned against this month?", today=TODAY)
    again = c.get(a["api"]["url"]).json()                       # the question as code = the same answer
    assert [x["record_key"] for x in again["records"]] == [x["record_key"] for x in a["records"]]
    assert a["api"]["mcp"]["tool"] == "query_records" and a["took_ms"] < 200
    assert all(x["source_page"] and x["fetched_at"] for x in a["records"])


def test_ai_plans_are_validated(fixture_service):
    reg = load_registry()
    flt = fixture_service.explorer.catalog.filters
    assert validate_plan({"op": "list", "datasets": ["cma_warnings"], "since": "2026-10-01"}, reg, flt)
    assert validate_plan({"op": "drop_table", "datasets": ["cma_warnings"]}, reg, flt) is None
    assert validate_plan({"op": "list", "datasets": ["secret_table"]}, reg, flt) is None
    assert validate_plan({"op": "list", "datasets": ["cma_warnings"], "filters": {"password": "x"}}, reg, flt) is None

    class FakeLLM:
        available = True
        def plan_query(self, q, cat, today):
            return {"op": "count", "datasets": ["cma_violations"], "filters": {}}
    ex = fixture_service.explorer
    old, ex.llm = ex.llm, FakeLLM()
    try:
        r = ex.ask("who did the regulator go after lately", today=TODAY)   # rules place nothing -> the AI plan is used
        assert r["plan"]["planner"] == "ai" and r["plan"]["op"] == "count"
    finally:
        ex.llm = old


# ---------------------------------------------------------------------------- onboarding
@pytest.fixture()
def gov():
    from tests.fake_gov import FakeGov
    return FakeGov()


def test_discovery_ladder(gov):
    from onboard.discover import Discovery
    ckan = Discovery(gov.transport()).discover("https://opendata.example.gov.ae/dataset/licensed-brokers")
    assert ckan["proposals"][0]["rung"] == 1 and ckan["proposals"][0]["field_map"]["name"] == "Company Name"
    assert {p["request"]["format"] for p in ckan["proposals"]} == {"csv", "xlsx"}       # XLSX with a title row too
    page = Discovery(gov.transport()).discover("https://www.example.gov.ae/registers/valuers")
    assert {p["rung"] for p in page["proposals"]} == {4, 5} and page["documents"]       # file link, table, PDF listed
    api = Discovery(gov.transport()).discover("https://api.example.gov.ae/v1/firms")
    p = api["proposals"][0]
    assert p["request"]["json_path"] == "data.items" and p["field_map"]["name"] == "entity.name"
    assert p["request"]["page_param"] == "page"
    n = len(gov.requests)
    bad = Discovery(gov.transport()).discover("https://scam-site.com/list")
    assert not bad["allowed"] and not bad["proposals"] and len(gov.requests) == n          # refused, nothing sent


def test_approve_makes_a_new_source_searchable(gov, tmp_path, monkeypatch):
    import config
    from onboard.approve import approve, remove
    from onboard.discover import Discovery
    from service import build_service
    from store.db import Database
    monkeypatch.setattr(config, "LOCAL_REGISTRY_YAML", tmp_path / "registry.local.yaml")
    load_registry.cache_clear()
    db = Database(tmp_path / "o.sqlite3")
    curated = config.REGISTRY_YAML.read_text(encoding="utf-8")
    try:
        prop = Discovery(gov.transport()).discover("https://opendata.example.gov.ae/dataset/licensed-brokers")["proposals"][0]
        res = approve(prop, {"official_name": "Licensed Brokers"}, db=db, transport=gov.transport())
        assert res["ok"] and res["rows"] == 4
        svc = build_service(db)
        r = svc.trust.check("Falcon Crest Brokerage LLC")
        assert r["headline"]["tone"] == "record" and "Licensed Brokers" in r["headline"]["text"]
        assert svc.explorer.ask("brokers that are suspended", today=TODAY)["count"] == 1
        assert config.REGISTRY_YAML.read_text(encoding="utf-8") == curated                  # curated file untouched
        api = Discovery(gov.transport()).discover("https://api.example.gov.ae/v1/firms")["proposals"][0]
        assert approve(api, db=db, transport=gov.transport())["rows"] == 3                  # both pages fetched
        assert remove(api["entry"]["id"]) and not remove("nope")
        bad = dict(prop, entry={**prop["entry"], "request": {**prop["entry"]["request"], "url": "https://evil.com/x.csv"}})
        assert approve(bad, db=db)["ok"] is False                                           # non-official URL refused
    finally:
        load_registry.cache_clear()


# ---------------------------------------------------------------------------- API v1 + MCP
def test_api_v1(fixture_service):
    c = TestClient(create_app(fixture_service))
    assert c.get("/api/v1/check", params={"q": "market10.net"}).json()["headline"]["tone"] == "alert"
    assert c.post("/api/v1/check", json={"text": "4tglobal.ae"}).json()["headline"]["tone"] == "record"
    r = c.get("/api/v1/check?q=x")
    assert r.headers["access-control-allow-origin"] == "*"
    assert c.post("/api/v1/check/image", content=b"x", headers={"content-type": "text/plain"}).status_code == 415
    img = c.post("/api/v1/check/image", content=b"\x89PNG....", headers={"content-type": "image/png"}).json()
    assert any("AI helper" in n for n in img["notes"])                       # no key: says so, does not guess
    assert len(c.get("/api/v1/datasets").json()["datasets"]) == 13
    assert c.get("/api/v1/datasets/cma_warnings").json()["profile"]["records"] == 233
    csv_text = c.get("/api/v1/datasets/cma_warnings/records?since=2026-10-01&format=csv").text
    assert csv_text.count("\n") == 4 and "source_page" in csv_text
    assert c.get("/api/v1/catalog.jsonld").json()["@type"] == "dcat:Catalog"
    assert c.get("/api/v1/query?op=list&datasets=nope").status_code == 400
    assert c.get("/api/v1/datasets/cma_warnings/records?f.password=1").status_code == 400
    assert c.get("/api/v1/changes").status_code == 200
    assert c.post("/api/v1/onboard/approve", json={"discovery": "x", "proposal": "y"}).status_code == 404


def test_mcp_over_http(fixture_service):
    c = TestClient(create_app(fixture_service))
    rpc = lambda m, p=None, i=1: c.post("/mcp", json={"jsonrpc": "2.0", "id": i, "method": m, "params": p or {}}).json()
    assert rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]["protocolVersion"] == "2025-06-18"
    names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    assert names == ["check_entity", "ask_data", "query_records", "list_datasets", "get_dataset"]
    out = rpc("tools/call", {"name": "check_entity", "arguments": {"text": "market10.net"}})["result"]
    assert not out["isError"] and "CMA warning" in out["content"][0]["text"] and out["structuredContent"]["facts"]
    bad = rpc("tools/call", {"name": "query_records", "arguments": {"op": "list", "datasets": ["nope"]}})["result"]
    assert bad["isError"]
    assert rpc("nope")["error"]["code"] == -32601
    assert c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202


def test_mcp_over_stdio():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py")], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    lines = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]          # stdout = protocol only, no logs
    assert [x["id"] for x in lines] == [1, 2] and lines[0]["result"]["protocolVersion"] == "2024-11-05"
    assert len(lines[1]["result"]["tools"]) == 5

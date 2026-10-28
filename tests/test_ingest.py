"""
tests/test_ingest.py - the data pipeline: real envelopes, pagination, failure handling, last-known-good
=====================================================================================================
Covers the ingestion tests required by the spec: valid responses, HTML error page with HTTP 200, a
different JSON structure (schema drift), a page failing mid-run, repeated pages, an identical refresh
(no duplicates), a failed refresh keeping the previous snapshot, expired tokens, logs and raw files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest.adapters import cma_content, cma_integration, html_table
from ingest.http_client import find_token
from ingest.pipeline import run_ingest
from ingest.responses import FetchError, RawResponse, expect_json, sniff
from tests.fake_cma import FakeCMA

FIX = Path(__file__).parent / "fixtures" / "cma"


def resp(name: str, status=200) -> RawResponse:
    return RawResponse("https://x", "POST", status, "application/json", (FIX / name).read_bytes(), 0.1)


def run(db, fake, only=None):
    return run_ingest(only, db=db, transport=fake.transport(), sleep=lambda _s: None, use_lock=False)


def by_id(summary):
    return {d["dataset_id"]: d for d in summary["datasets"]}


# ------------------------------------------------------------------ response classification (real fixtures)
def test_real_error_envelopes_are_errors_not_empty_lists():
    with pytest.raises(FetchError) as e:
        expect_json(resp("err_no_token.json", 403))
    assert e.value.kind == "token_invalid"
    with pytest.raises(FetchError) as e:                       # HTTP 200 but code 500 inside
        expect_json(resp("err_no_lang.json", 200))
    assert e.value.kind == "api_error"


def test_html_with_http_200_is_rejected():
    r = RawResponse("u", "POST", 200, "application/json", b"<!doctype html><html><body>Oops</body></html>", 0.1)
    assert sniff(r.body) == "html"
    with pytest.raises(FetchError) as e:
        expect_json(r)
    assert e.value.kind == "html_not_json"


def test_token_found_in_real_page():
    tok, where = find_token((FIX / "page_warnings.html").read_text(encoding="utf-8"))
    assert tok.startswith("anonymous:") and where == "hidden input"


# ------------------------------------------------------------------ adapters (pure extract)
def test_content_list_extract_real_fixture():
    info = cma_content.extract(json.loads((FIX / "warnings_p1.json").read_text(encoding="utf-8")))
    assert info.total_records == 6 and info.total_pages == 1 and len(info.items) == 6
    empty = cma_content.extract(json.loads((FIX / "warnings_beyond.json").read_text()))
    assert empty.items == [] and empty.total_records == 0


def test_content_list_rejects_other_structures():
    with pytest.raises(FetchError) as e:
        cma_content.extract({"code": 200, "result": {"items": [{"a": 1}]}})
    assert e.value.kind == "schema_drift"


def test_integration_extract_real_fixtures():
    lic = cma_integration.extract(json.loads((FIX / "licensed_p1.json").read_text()))
    assert lic.items[0]["code"] == "CP-0001315"
    beyond = cma_integration.extract(json.loads((FIX / "licensed_beyond.json").read_text()))
    assert beyond.items == [] and beyond.total_pages == 1           # the bogus totals quirk


def test_html_table_extract():
    info = html_table.extract((FIX / "appraisers.html").read_text(), {"name": "Company Name", "date": "Date of Registration"})
    assert info.items == [{"name": "Truth Economic and Management Consultancy L.L.C", "date": "25/03/2025"}]
    with pytest.raises(FetchError):
        html_table.extract("<table><tr><td>Other</td></tr></table>", {"name": "Company Name"})


# ------------------------------------------------------------------ full pipeline
def test_full_run_then_identical_run(fresh_db):
    fake = FakeCMA()
    s1 = run(fresh_db, fake)
    assert s1["counts"]["SUCCESS"] == 13, s1["counts"]
    w = by_id(s1)["cma_warnings"]
    assert w["pages"] == 5 and w["rows"] == 233                         # 466 items, paired by groupId
    assert fresh_db.current_snapshot("cma_licensed_companies")["row_count"] == 333
    lic = fresh_db.records("cma_licensed_companies")
    assert lic[0]["name_ar"].startswith("شركة")                         # Arabic pass attached by licence code
    s2 = run(fresh_db, fake)
    assert s2["counts"]["UNCHANGED"] == 13                              # idempotent: nothing duplicated
    assert len(fresh_db.records("cma_warnings")) == 233


def test_failed_refresh_keeps_last_good_copy(fresh_db):
    run(fresh_db, FakeCMA(), ["cma_warnings"])
    good = fresh_db.current_snapshot("cma_warnings")["id"]
    s = run(fresh_db, FakeCMA(html_error_for=1071), ["cma_warnings"])
    d = by_id(s)["cma_warnings"]
    assert d["status"] == "FAILED" and "html_not_json" in d["reason"]
    assert fresh_db.current_snapshot("cma_warnings")["id"] == good
    assert any("still serving the previous snapshot" in n for n in d["notes"])
    assert list(Path(d["raw_dir"]).glob("FAILED_*.body"))               # the bad answer is kept as evidence


def test_schema_drift(fresh_db):
    s = run(fresh_db, FakeCMA(drift_for=1039), ["cma_violations"])
    assert by_id(s)["cma_violations"]["status"] == "SCHEMA_DRIFT"
    assert fresh_db.current_snapshot("cma_violations") is None


def test_gap_mid_run_is_not_published(fresh_db):
    s = run(fresh_db, FakeCMA(empty_page=3), ["cma_warnings"])
    d = by_id(s)["cma_warnings"]
    assert d["status"] == "FAILED" and "page 3 is empty" in d["reason"]


def test_server_ignoring_page_index_is_detected(fresh_db):
    s = run(fresh_db, FakeCMA(ignore_page_index=True), ["cma_warnings"])
    assert "repeated page" in by_id(s)["cma_warnings"]["reason"]


def test_expired_token_reopens_session_once(fresh_db):
    fake = FakeCMA(expire_token_after=2)
    s = run(fresh_db, fake, ["cma_warnings"])
    assert by_id(s)["cma_warnings"]["status"] == "SUCCESS"
    assert fake.log.count("GET /en/open-data/warnings") == 2


def test_sudden_drop_is_refused(fresh_db):
    run(fresh_db, FakeCMA(), ["cma_finfluencers"])
    fake = FakeCMA()
    fake.finfluencers = fake.finfluencers[:60]                          # 195 -> 60 rows (69% drop)
    s = run(fresh_db, fake, ["cma_finfluencers"])
    d = by_id(s)["cma_finfluencers"]
    assert d["status"] == "FAILED" and "fell from 195 to 60" in d["reason"]
    assert fresh_db.current_snapshot("cma_finfluencers")["row_count"] == 195


def test_logs_status_file_and_raw_files(fresh_db):
    import config
    s = run(fresh_db, FakeCMA(), ["cma_violations"])
    events = [json.loads(l) for l in Path(s["event_log"]).read_text(encoding="utf-8").splitlines()]
    stages = {e["stage"] for e in events if e["dataset"] == "cma_violations"}
    assert {"start", "http", "token", "paging", "page", "reconcile", "normalise", "outcome"} <= stages
    assert not any("anonymous:" in json.dumps(e) for e in events)       # never log the token
    st = json.loads(config.STATUS_FILE.read_text())
    assert st["run_id"] == s["run_id"]
    raw = Path(by_id(s)["cma_violations"]["raw_dir"])
    meta = json.loads((raw / "page_001.meta.json").read_text())
    assert meta["status"] == 200 and len(meta["sha256"]) == 64 and "RequestVerificationToken" not in json.dumps(meta)


def test_rebuild_from_raw_without_network(fresh_db):
    s = run(fresh_db, FakeCMA(), ["cma_warnings", "cma_licensed_companies"])
    s2 = run_ingest(["cma_warnings", "cma_licensed_companies"], db=fresh_db, from_raw=s["run_id"], use_lock=False)
    assert {d["status"] for d in s2["datasets"]} <= {"UNCHANGED", "SUCCESS"}
    assert fresh_db.current_snapshot("cma_warnings")["row_count"] == 233

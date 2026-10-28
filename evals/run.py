"""
evals/run.py - run the 30 benchmark scenarios (and extra protection cases) end to end, offline
=============================================================================================

WHAT IT DOES
    1. builds a throw-away database by running the REAL ingest pipeline against tests/fake_cma.py
       (which serves real CMA records captured from the live site + generated filler);
    2. plays every conversation in evals/scenarios.yaml through the REAL coordinator (no AI key: the
       deterministic path that must be correct on its own);
    3. checks, for each scenario: the pillar and intent, which question was asked at each step, which
       tools ran, numbers and phrases that must / must not appear;
    4. prints PASS/FAIL per scenario with the reasons, and writes evals/last_report.json.

    python -m evals.run              all scenarios
    python -m evals.run S01 S30      only these
    python -m evals.run -v           also print every reply
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _setup_env() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="sg_eval_"))
    os.environ["SG_DATA_DIR"] = str(tmp / "data")
    os.environ["SG_LOG_DIR"] = str(tmp / "logs")
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(k, None)
    (tmp / "data" / "reference").mkdir(parents=True)
    src = ROOT / "data" / "reference" / "fcsc_cpi_annual.csv"
    (tmp / "data" / "reference" / src.name).write_bytes(src.read_bytes())
    return tmp


def build_fixture_service():
    """A Service whose database was filled by the real pipeline from the fake CMA site."""
    sys.path.insert(0, str(ROOT))
    from ingest.pipeline import run_ingest
    from service import build_service
    from store.db import Database
    from tests.fake_cma import FakeCMA
    db = Database()
    run_ingest(db=db, transport=FakeCMA().transport(), sleep=lambda _s: None, use_lock=False)
    return build_service(db)


def check(sc: dict, results: list, svc) -> list[str]:
    errs = []
    last = results[-1]
    for i, (turn, r) in enumerate(zip(sc["turns"], results)):
        if "asks" in turn and r.question_slot != turn["asks"]:
            errs.append(f"turn {i + 1}: expected to ask for {turn['asks']}, got mode={r.mode} ask={r.question_slot}")
    if "level" in sc and last.level != sc["level"]:
        errs.append(f"level {last.level} != {sc['level']}")
    if "pillar" in sc and last.pillar != sc["pillar"]:
        errs.append(f"pillar {last.pillar} != {sc['pillar']}")
    if "intent" in sc and last.intent != sc["intent"]:
        errs.append(f"intent {last.intent} != {sc['intent']}")
    if "intent" in sc and last.mode != "answer":
        errs.append(f"final mode is {last.mode} (asked {last.question_slot}), expected an answer")
    tools = (last.evidence or {}).get("tools", [])
    for t in sc.get("tools", []):
        if t not in tools:
            errs.append(f"tool {t} did not run (ran: {tools})")
    for n in sc.get("numbers", []):
        if n not in last.reply:
            errs.append(f"number {n} missing from reply")
    for p in sc.get("must_say", []):
        if not re.search(p, last.reply, re.I):
            errs.append(f"must say /{p}/")
    for p in sc.get("must_not_say", []):
        if re.search(p, last.reply, re.I):
            errs.append(f"must NOT say /{p}/")
    return errs


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    verbose = "-v" in argv
    only = [a for a in argv if not a.startswith("-")]
    _setup_env()
    from logsetup import setup_logging
    setup_logging("WARNING")
    svc = build_fixture_service()
    scenarios = yaml.safe_load((ROOT / "evals" / "scenarios.yaml").read_text(encoding="utf-8"))
    report, failed = [], 0
    for sc in scenarios:
        if only and sc["id"] not in only:
            continue
        sid = f"eval-{sc['id']}-session-0000"
        results = [svc.coordinator.turn(sid, t["text"]) for t in sc["turns"]]
        errs = check(sc, results, svc)
        failed += bool(errs)
        print(f"{'PASS' if not errs else 'FAIL'}  {sc['id']}  {sc['title']}")
        for e in errs:
            print(f"        - {e}")
        if verbose or errs:
            for t, r in zip(sc["turns"], results):
                print(f"        > {t['text']}\n        < [{r.mode}/{r.intent}] {r.reply}")
        report.append({"id": sc["id"], "pass": not errs, "errors": errs, "replies": [r.reply for r in results],
                       "tools": (results[-1].evidence or {}).get("tools", [])})
    total = len(report)
    print(f"\n{total - failed}/{total} scenarios passed")
    (ROOT / "evals" / "last_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

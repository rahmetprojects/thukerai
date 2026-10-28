"""
ingest/report.py - "what data do I have right now, and what happened last time?"
==============================================================================

    python -m ingest.report                 status of every dataset (no network)
    python -m ingest.report cma_warnings    the last 10 runs of one dataset, with reasons and notes

WHAT IT SHOWS (from the database, so it is always the truth the assistant uses)
    dataset | rows served | snapshot age | last attempt status | last error
    plus the datasets on the CMA portal that are deliberately NOT fetched, and why.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from store.db import Database

from .registry import load_registry


def _age(iso: str | None) -> str:
    if not iso:
        return "-"
    h = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} d"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    db = Database()
    db.register(load_registry().datasets)
    if argv:
        for r in db.run_history(argv[0]):
            print(f"{r['at']}  {r['status']:<13} rows={r['rows']:<6} pages={r['pages']:<4} reqs={r['requests']:<4} "
                  f"{r['seconds']:.1f}s  {r['reason']}")
            if r["notes"] and r["notes"] != "[]":
                print(f"{'':>27}notes: {r['notes']}")
        return 0
    print(f"{'dataset':<28}{'rows':>7}  {'snapshot age':<13}{'last status':<14}{'pillar':<11}last error / note")
    print("-" * 110)
    for d in db.status():
        snap = d["snapshot"]
        print(f"{d['id']:<28}{(snap or {}).get('row_count', 0):>7}  {_age((snap or {}).get('created_at')):<13}"
              f"{d['last_status'] or 'never run':<14}{d['pillar']:<11}{(d['last_error'] or '')[:60]}")
    print("\nNot fetched on purpose:")
    for nf in load_registry().not_fetched:
        print(f"  - {nf['name']}: {nf['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
ingest/run.py - command line for the data pipeline
==================================================

    python -m ingest.run                         fetch every dataset in ingest/registry.yaml
    python -m ingest.run --only cma_warnings cma_licensed_companies
    python -m ingest.run --from-raw 20261028T020511Z    rebuild from saved raw files (no network)
    python -m ingest.run --list                  list the datasets in the registry and exit

WHAT YOU SEE
    Every request and page as it happens, then a summary table:
        dataset   status   rows   pages   requests   KB   seconds   snapshot served
    The same summary is saved to logs/ingest/latest.summary.txt, and every event to
    logs/ingest/<run_id>.jsonl. Exit code 0 = no dataset FAILED, 1 = at least one failed.

    See ingest/report.py for re-printing the status later without fetching anything.
"""
from __future__ import annotations

import argparse
import sys

from logsetup import setup_logging


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ingest.run", description="Fetch official datasets into the local database.")
    ap.add_argument("--only", nargs="+", metavar="DATASET_ID", help="only these dataset ids")
    ap.add_argument("--from-raw", metavar="RUN_ID", help="re-parse the raw files saved by an earlier run (no network)")
    ap.add_argument("--list", action="store_true", help="list the registry and exit")
    args = ap.parse_args(argv)
    setup_logging()

    from .pipeline import Busy, run_ingest
    from .registry import load_registry

    if args.list:
        reg = load_registry()
        for d in reg.datasets:
            print(f"{d.id:<28} {d.pillar:<10} {d.kind:<18} {d.official_name}")
        for nf in reg.not_fetched:
            print(f"{'(not fetched)':<28} {'':<10} {'':<18} {nf['name']}: {nf['reason']}")
        return 0
    try:
        summary = run_ingest(args.only, from_raw=args.from_raw)
    except Busy as e:
        print(f"Not started: {e}")
        return 2
    bad = summary["counts"].get("FAILED", 0) + summary["counts"].get("SCHEMA_DRIFT", 0)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

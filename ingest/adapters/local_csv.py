"""
ingest/adapters/local_csv.py - a CSV file shipped with the app, treated like any other source
============================================================================================

USED FOR (registry kind: local_csv)
    fcsc_cpi_annual: the FCSC annual consumer price index (data/reference/fcsc_cpi_annual.csv).

WHY GO THROUGH THE PIPELINE AT ALL?
    So that inflation figures get exactly the same treatment as live data: validated columns, a snapshot
    id, a content hash, a row in the status page, and a citation in every answer that uses them.
    To update: download a newer CSV from the FCSC portal, save it over the file, run  python -m ingest.run
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import config

from ..responses import FetchError, RawResponse
from .base import Context, FetchResult


def fetch(spec, ctx: Context) -> FetchResult:
    path = Path(config.REFERENCE_DIR) / spec.request["file"]
    if not path.exists():
        raise FetchError("config", f"reference file not found: {path}")
    body = path.read_bytes()
    ctx.raw.save(RawResponse(f"file:{path.name}", "READ", 200, "text/csv", body, 0.0), "page")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    missing = [c for c in spec.required_fields if rows and c not in rows[0]]
    if missing:
        raise FetchError("schema_drift", f"CSV columns changed: missing {missing}; found {list(rows[0])[:12]}")
    ctx.runlog.event(spec.id, "page", "info", f"read {len(rows)} rows from {path.name}", bytes=len(body))
    return FetchResult(items=rows, pages=1, source_url=spec.source_page, total_reported=len(rows),
                       as_of=max((r.get("TIME_PERIOD", "") for r in rows), default=None))

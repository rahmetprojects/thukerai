"""
ingest/pipeline.py - run the data pipeline: fetch -> save raw -> validate -> normalise -> publish
===============================================================================================

WHAT IT DOES
    For every dataset in ingest/registry.yaml (or the ones you name), in order:

      1 FETCH       the dataset's adapter talks to the source (one shared browserless CMA session)
      2 RAW         every response is saved byte-for-byte under data/raw/<run_id>/<dataset>/
      3 VALIDATE    required fields, empty, duplicates, minimum rows, sudden drops   (ingest/validate.py)
      4 NORMALISE   raw items -> canonical records                                   (ingest/schemas.py)
      5 PUBLISH     new snapshot made current in ONE transaction - or UNCHANGED if identical content
      6 RECORD      outcome written to the run log, the database and the status file

    A dataset that fails at ANY step keeps serving its last good snapshot; the failure, its reason and a
    sample of the bad answer are logged. One dataset failing never stops the others.

HOW TO RUN IT
    python -m ingest.run                       # everything
    python -m ingest.run --only cma_warnings   # one dataset
    python -m ingest.run --from-raw <run_id>   # re-parse saved raw files, no network
    The server also runs it in the background at start-up when data is missing or old.

ONE RUN AT A TIME
    A lock file (data/status/ingest.lock) stops two runs overlapping (e.g. the server and the CLI).
"""
from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path

import config
from store.db import Database, content_hash

from .adapters import ADAPTERS
from .adapters.base import Context, FetchResult
from .http_client import CmaSession, HttpClient
from .raw_store import RawStore, load_raw_pages
from .registry import load_registry
from .responses import FetchError, RawResponse, expect_json
from .runlog import DatasetOutcome, RunLog
from .schemas import NORMALIZERS
from .validate import Rejection, check_counts, check_required, dedupe

log = logging.getLogger("ingest.pipeline")
NETWORK_KINDS = {"cma_content_list", "cma_integration", "cma_activity_map", "html_table", "remote_table"}
LOCK = config.DATA_DIR / "status" / "ingest.lock"
LOCK_STALE_S = 30 * 60


class Busy(Exception):
    """Another ingest run holds the lock."""


def _acquire_lock() -> None:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < LOCK_STALE_S:
        raise Busy(f"another ingest run is in progress (lock {LOCK}, {int(time.time() - LOCK.stat().st_mtime)} s old)")
    LOCK.write_text(str(os.getpid()), encoding="utf-8")


def _release_lock() -> None:
    try:
        LOCK.unlink()
    except OSError:
        pass


def run_ingest(only: list[str] | None = None, *, db: Database | None = None, transport=None, sleep=time.sleep,
               from_raw: str | None = None, run_id: str | None = None, use_lock: bool = True) -> dict:
    """Run the pipeline. Returns the run summary (also written to logs/ingest and data/status)."""
    reg = load_registry()
    db = db or Database()
    db.register(reg.datasets)
    specs = [s for s in reg.datasets if not only or s.id in only]
    unknown = sorted(set(only or []) - {s.id for s in reg.datasets})
    if use_lock:
        _acquire_lock()
    runlog = RunLog(run_id)
    try:
        if unknown:
            runlog.event("run", "config", "error", f"unknown dataset id(s) ignored: {unknown}", known=reg.ids())
        http = HttpClient(runlog, transport=transport, sleep=sleep)
        session = CmaSession(http, reg.site)
        try:
            for spec in specs:
                _one(spec, reg, db, runlog, http, session, sleep, from_raw)
        finally:
            http.close()
        summary = runlog.finish()
        db.save_run(summary)
        return summary
    finally:
        if use_lock:
            _release_lock()


def _one(spec, reg, db: Database, runlog: RunLog, http: HttpClient, session: CmaSession, sleep, from_raw) -> None:
    o = DatasetOutcome(spec.id, source_url=spec.source_page)
    t0, sent0, bytes0 = time.time(), http.sent, http.bytes
    raw = RawStore(runlog.run_id, spec.id)
    o.raw_dir = str(raw.dir)
    prev = db.current_snapshot(spec.id)
    runlog.event(spec.id, "start", "info", f"{spec.official_name} ({spec.kind})", source=spec.source_page,
                 current_snapshot=prev["id"] if prev else None, current_rows=prev["row_count"] if prev else 0)
    try:
        if not spec.enabled:
            raise Rejection("SKIPPED", "disabled in registry.yaml")
        if from_raw:
            res = _from_raw(spec, from_raw)
        else:
            if spec.kind in NETWORK_KINDS and not config.LIVE_DATA:
                raise Rejection("SKIPPED", "live data is switched off (SG_LIVE_DATA=0)")
            ctx = Context(runlog=runlog, raw=raw, session=session, site=reg.site, sleep=sleep)
            res = ADAPTERS[spec.kind].fetch(spec, ctx)
        o.pages, o.total_reported = res.pages, res.total_reported
        items, notes = check_required(res.items, spec.required_fields)
        records, stats = NORMALIZERS[spec.schema](items, spec, res.extra)
        runlog.event(spec.id, "normalise", "info", f"{len(items)} raw items -> {len(records)} records", **stats)
        records, dnotes = dedupe(records)
        notes += res.notes + dnotes
        o.rows, o.notes = len(records), notes
        notes += check_counts(records, spec, prev["row_count"] if prev else None)
        if prev and prev["content_hash"] == content_hash(records):
            db.mark_checked(spec.id)
            o.status, o.snapshot_id = "UNCHANGED", prev["id"]
            o.reason = f"content identical to the current snapshot ({len(records)} records)"
        else:
            o.snapshot_id = db.publish(spec.id, runlog.run_id, records, source_url=res.source_url or spec.source_page,
                                       as_of=res.as_of, total_reported=res.total_reported, pages=res.pages,
                                       raw_dir=str(raw.dir), notes=notes)
            o.status, o.published = "SUCCESS", True
            o.reason = f"published {len(records)} records" + (f" (was {prev['row_count']})" if prev else " (first copy)")
    except Rejection as r:
        o.status, o.reason = r.status, r.reason
    except FetchError as e:
        o.status = "SCHEMA_DRIFT" if e.kind == "schema_drift" else "FAILED"
        o.reason = f"{e.kind}: {e.message}"
        if e.sample:
            runlog.event(spec.id, "sample", "error", "start of the answer that failed (also saved in raw dir)",
                         sample=e.sample[:400])
    except Exception as e:                                   # a bug must not take down the other datasets
        o.status, o.reason = "FAILED", f"unexpected {type(e).__name__}: {e}"
        runlog.event(spec.id, "bug", "error", "unexpected error", traceback=traceback.format_exc()[-1500:])
    finally:
        o.seconds = time.time() - t0
        o.requests, o.bytes = http.sent - sent0, http.bytes - bytes0
        if not o.published:
            o.snapshot_id = o.snapshot_id or (prev["id"] if prev else None)
            if o.status not in ("UNCHANGED", "SKIPPED") and prev:
                o.notes.append(f"still serving the previous snapshot {prev['id']} ({prev['row_count']} records)")
            elif o.status not in ("UNCHANGED", "SKIPPED"):
                o.notes.append("no previous snapshot exists: this dataset is UNAVAILABLE to the assistant")
        if raw.count == 0:
            try:
                Path(o.raw_dir).rmdir()
                o.raw_dir = ""
            except OSError:
                pass
        db.record_outcome(runlog.run_id, o)
        runlog.outcome(o)


# ---------------------------------------------------------------------- rebuild from saved raw files
def _from_raw(spec, run_id: str) -> FetchResult:
    """Re-parse a previous run's raw files (no network). Supported for every kind except the activity map."""
    from .adapters import cma_content, cma_integration, html_table
    d = config.RAW_DIR / run_id / spec.id
    pages = load_raw_pages(d)
    if not pages:
        raise Rejection("SKIPPED", f"no raw pages saved for this dataset in run {run_id} ({d})")
    if spec.kind == "cma_activity_map":
        raise Rejection("SKIPPED", "the activity map cannot be rebuilt from raw yet; run it live")
    items, seen, arabic = [], set(), {}
    for meta, body in pages:
        resp = RawResponse(meta["url"], meta["method"], meta["status"], meta["content_type"], body, 0.0)
        if spec.kind == "html_table":
            items += html_table.extract(resp.text, spec.request["columns"]).items
        elif spec.kind == "remote_table":
            from .adapters import remote_table
            items += remote_table.parse(body, spec.request["format"], spec.request)
        elif spec.kind == "local_csv":
            import csv, io
            items += list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        else:
            mod = cma_content if spec.kind == "cma_content_list" else cma_integration
            info = mod.extract(expect_json(resp))
            key = "id" if spec.kind == "cma_content_list" else spec.record_key
            for it in info.items:
                if meta.get("lang") == "ar":
                    arabic[str(it.get(key))] = it
                elif str(it.get(key)) not in seen:
                    seen.add(str(it.get(key)))
                    items.append(it)
    return FetchResult(items=items, pages=len(pages), source_url=spec.source_page,
                       extra={"arabic_by_key": arabic} if arabic else {},
                       notes=[f"rebuilt from raw files of run {run_id} (no network)"])

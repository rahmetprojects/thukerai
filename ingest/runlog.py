"""
ingest/runlog.py - the data pipeline's flight recorder
=====================================================

WHAT IT DOES
    Records EVERYTHING that happens during one ingest run, so you can always answer:
        * which datasets were fetched, from which address, with which request?
        * which ones worked, which came back EMPTY, which FAILED and exactly why?
        * how many pages / rows / bytes / HTTP calls, and how long did each take?
        * was a new snapshot published, or did we keep the last good one?

WHAT IT WRITES (one set per run, run_id = UTC timestamp like 20261028T020511Z)
    logs/ingest/<run_id>.jsonl         every event, one JSON object per line (machine readable)
    logs/ingest/<run_id>.summary.txt   a plain-text table you can read in 10 seconds
    logs/ingest/latest.summary.txt     copy of the newest summary (always the same name)
    data/status/latest_run.json        the same summary as JSON (served at /api/data/status)
    ...and each event is also echoed to logs/app.log and the console through `logging`.

DATASET OUTCOMES (exactly one per dataset per run)
    SUCCESS       fetched, validated, NEW snapshot published
    UNCHANGED     fetched and valid, but identical to the current snapshot (nothing to publish)
    EMPTY         the source answered correctly but with ZERO records  -> nothing published, old kept
    FAILED        network / HTTP / token / parse error                  -> nothing published, old kept
    SCHEMA_DRIFT  the answer's structure is not what the adapter expects -> sample saved, old kept
    SKIPPED       disabled in the registry, or live data switched off

SECURITY
    Cookies, anti-forgery tokens and API keys are NEVER written. Use logsetup.redact_headers().
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger("ingest")

STATUSES = ("SUCCESS", "UNCHANGED", "EMPTY", "FAILED", "SCHEMA_DRIFT", "SKIPPED")
_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class DatasetOutcome:
    """The one-line verdict for a dataset in a run (also stored in the database)."""
    dataset_id: str
    status: str = "SKIPPED"
    rows: int = 0                      # valid, de-duplicated records in what we fetched
    pages: int = 0                     # API pages / files read
    requests: int = 0                  # HTTP requests sent (including retries and token attempts)
    bytes: int = 0                     # raw bytes received
    seconds: float = 0.0
    total_reported: int | None = None  # what the source SAID the total was, if it told us
    snapshot_id: str | None = None     # the snapshot now being served (new one, or the old one kept)
    published: bool = False            # True only for SUCCESS
    reason: str = ""                   # plain-English explanation (always set for non-SUCCESS)
    source_url: str = ""
    raw_dir: str = ""                  # where this run's raw responses for the dataset were saved
    notes: list[str] = field(default_factory=list)   # warnings that did not stop the run

    def as_row(self) -> dict:
        return asdict(self)


class RunLog:
    """Create one per ingest run. Pass it to every stage; call finish() at the end."""

    def __init__(self, run_id: str | None = None, log_dir: Path | None = None, status_file: Path | None = None):
        self.run_id = run_id or new_run_id()
        self.dir = Path(log_dir or (config.LOG_DIR / "ingest"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status_file = Path(status_file or config.STATUS_FILE)
        self.jsonl = self.dir / f"{self.run_id}.jsonl"
        self.started = time.time()
        self.outcomes: dict[str, DatasetOutcome] = {}
        self.event("run", "start", "info", f"ingest run {self.run_id} started", live=config.LIVE_DATA)

    # ------------------------------------------------------------------ events
    def event(self, dataset: str, stage: str, level: str, message: str, **fields) -> None:
        """One thing happened. `stage` is fetch / token / page / parse / validate / publish / ..."""
        rec = {"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "run": self.run_id,
               "dataset": dataset, "stage": stage, "level": level, "msg": message, **fields}
        try:
            with self.jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass                                            # logging must never break ingestion
        extra = " ".join(f"{k}={v}" for k, v in fields.items() if v not in (None, "", [], {}))
        log.log(_LEVELS.get(level, logging.INFO), "[%s] %-9s %s%s", dataset, stage, message,
                f"  ({extra})" if extra else "")

    def outcome(self, o: DatasetOutcome) -> DatasetOutcome:
        assert o.status in STATUSES, o.status
        self.outcomes[o.dataset_id] = o
        lvl = "info" if o.status in ("SUCCESS", "UNCHANGED", "SKIPPED") else "error" if o.status != "EMPTY" else "warn"
        self.event(o.dataset_id, "outcome", lvl, f"{o.status}: {o.reason or 'ok'}",
                   rows=o.rows, pages=o.pages, requests=o.requests, bytes=o.bytes,
                   seconds=round(o.seconds, 2), snapshot=o.snapshot_id, published=o.published)
        return o

    # ------------------------------------------------------------------ the end
    def summary(self) -> dict:
        counts = {s: sum(1 for o in self.outcomes.values() if o.status == s) for s in STATUSES}
        return {"run_id": self.run_id,
                "started_at": datetime.fromtimestamp(self.started, timezone.utc).isoformat(timespec="seconds"),
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seconds": round(time.time() - self.started, 1),
                "counts": counts,
                "event_log": str(self.jsonl),
                "datasets": [o.as_row() for o in self.outcomes.values()]}

    def summary_text(self, s: dict | None = None) -> str:
        s = s or self.summary()
        lines = [f"INGEST RUN {s['run_id']}   started {s['started_at']}   took {s['seconds']} s",
                 "counts: " + ", ".join(f"{k}={v}" for k, v in s["counts"].items() if v),
                 f"full event log: {s['event_log']}", ""]
        hdr = f"{'dataset':<26}{'status':<14}{'rows':>7}{'pages':>7}{'reqs':>6}{'KB':>8}{'secs':>7}  snapshot served"
        lines += [hdr, "-" * len(hdr)]
        for d in s["datasets"]:
            lines.append(f"{d['dataset_id']:<26}{d['status']:<14}{d['rows']:>7}{d['pages']:>7}{d['requests']:>6}"
                         f"{d['bytes'] // 1024:>8}{d['seconds']:>7.1f}  {d['snapshot_id'] or '-'}")
        lines.append("")
        for d in s["datasets"]:
            if d["status"] != "SUCCESS" or d["notes"]:
                lines.append(f"* {d['dataset_id']} [{d['status']}]: {d['reason'] or 'ok'}")
                lines += [f"      note: {n}" for n in d["notes"]]
                if d["raw_dir"]:
                    lines.append(f"      raw responses: {d['raw_dir']}")
        return "\n".join(lines) + "\n"

    def finish(self) -> dict:
        s = self.summary()
        text = self.summary_text(s)
        self.event("run", "finish", "info", f"ingest run finished in {s['seconds']} s", **s["counts"])
        for path in (self.dir / f"{self.run_id}.summary.txt", self.dir / "latest.summary.txt"):
            path.write_text(text, encoding="utf-8")
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.status_file)
        log.info("ingest summary:\n%s", text)
        return s

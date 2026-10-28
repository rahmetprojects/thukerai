"""
ingest/scheduler.py - keep the data fresh while the server runs (background, never blocks a conversation)
======================================================================================================

WHAT IT DOES
    On server start (if SG_REFRESH_ON_START=1 and SG_LIVE_DATA=1) and then every hour, finds the datasets
    whose current snapshot is missing or older than their `refresh_hours` in ingest/registry.yaml, and runs
    the pipeline for ONLY those, in a background thread. Conversations keep using the last good snapshot
    meanwhile. A manual refresh can also be requested from the status page (POST /api/data/refresh).

    Only one ingest run happens at a time (the pipeline's lock file); a refresh request while one is running
    is reported as "already running", not queued twice.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import config

from .pipeline import Busy, run_ingest
from .registry import load_registry

log = logging.getLogger("ingest.scheduler")


class Scheduler:
    def __init__(self, db):
        self.db = db
        self.running = False
        self.last_summary: dict | None = None
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def due(self) -> list[str]:
        out = []
        for spec in load_registry().datasets:
            row = self.db.dataset_row(spec.id) or {}
            last = row.get("last_success_at")
            if not last:
                out.append(spec.id)
                continue
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
            if age_h >= spec.refresh_hours:
                out.append(spec.id)
        return out

    def refresh(self, only: list[str] | None = None) -> str:
        """Start a background run. Returns 'started', 'already running' or 'nothing due'."""
        with self._lock:
            if self.running:
                return "already running"
            ids = only or self.due()
            if not ids:
                return "nothing due"
            self.running = True
        threading.Thread(target=self._run, args=(ids,), name="ingest", daemon=True).start()
        return "started"

    def _run(self, ids: list[str]) -> None:
        log.info("background refresh of %d dataset(s): %s", len(ids), ", ".join(ids))
        try:
            self.last_summary = run_ingest(ids, db=self.db)
            self.last_error = None
        except Busy as e:
            self.last_error = str(e)
            log.warning("refresh skipped: %s", e)
        except Exception as e:                          # never kill the server
            self.last_error = f"{type(e).__name__}: {e}"
            log.exception("background refresh crashed")
        finally:
            self.running = False

    def start(self) -> None:
        if not (config.LIVE_DATA and config.REFRESH_ON_START):
            log.info("background refresh disabled (SG_LIVE_DATA=%s, SG_REFRESH_ON_START=%s)",
                     int(config.LIVE_DATA), int(config.REFRESH_ON_START))
            return

        def loop():
            while not self._stop.is_set():
                state = self.refresh()
                log.info("scheduled refresh check: %s", state)
                self._stop.wait(3600)
        threading.Thread(target=loop, name="ingest-scheduler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

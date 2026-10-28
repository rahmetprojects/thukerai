"""
store/db.py - the SQLite database: versioned snapshots of every dataset, plus the ingest history
================================================================================================

WHAT IT DOES
    Stores the CANONICAL (cleaned, validated) records of every dataset, one immutable SNAPSHOT per
    successful refresh, and switches the "current" snapshot ATOMICALLY: either the whole new snapshot
    becomes visible at once, or nothing changes. A failed or partial refresh therefore can never
    replace good data. Every search result carries the snapshot id it came from.

TABLES
    datasets      one row per dataset in the registry: which snapshot is current, last attempt / success,
                  last status (SUCCESS / EMPTY / FAILED ...), last error, row count, publisher date
    snapshots     one row per published snapshot: run id, row count, content hash, source URL, notes
    records       the canonical records, keyed by (snapshot_id, record_key)
    entity_index  one row per searchable name or web domain of a record (used by store/search.py)
    record_fts    full-text index (SQLite FTS5) over the CURRENT snapshot of every dataset: names in English and
                  Arabic, websites, categories, statements. Rebuilt for a dataset in the same transaction that
                  publishes it, so search never sees half a snapshot (used by agents/explorer.py)
    dataset_runs  the outcome of every dataset in every ingest run (the history behind the status page)
    runs          one row per ingest run with its full summary

WHY SQLITE
    One file, no server, fast enough for tens of thousands of records, and transactions give us the
    atomic publish for free. (The registry/adapters do not depend on it; Postgres could replace it.)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

from .names import core_name, normalize_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets(
  id TEXT PRIMARY KEY, official_name TEXT, publisher TEXT, source_page TEXT, pillar TEXT,
  current_snapshot TEXT, last_attempt_at TEXT, last_success_at TEXT, last_status TEXT,
  last_error TEXT, row_count INTEGER DEFAULT 0, as_of TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
  id TEXT PRIMARY KEY, dataset_id TEXT, run_id TEXT, created_at TEXT, row_count INTEGER,
  content_hash TEXT, parser_version TEXT, source_url TEXT, as_of TEXT, total_reported INTEGER,
  pages INTEGER, raw_dir TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS records(
  snapshot_id TEXT, dataset_id TEXT, record_key TEXT, name TEXT, name_ar TEXT, date TEXT,
  status TEXT, category TEXT, names TEXT, domains TEXT, details TEXT,
  PRIMARY KEY(snapshot_id, record_key));
CREATE TABLE IF NOT EXISTS entity_index(
  snapshot_id TEXT, dataset_id TEXT, record_key TEXT, kind TEXT, value TEXT, norm TEXT, core TEXT);
CREATE INDEX IF NOT EXISTS ix_entity_core ON entity_index(core);
CREATE INDEX IF NOT EXISTS ix_entity_norm ON entity_index(norm);
CREATE INDEX IF NOT EXISTS ix_entity_snap ON entity_index(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_records_ds ON records(dataset_id, snapshot_id);
CREATE TABLE IF NOT EXISTS dataset_runs(
  run_id TEXT, dataset_id TEXT, status TEXT, rows INTEGER, pages INTEGER, requests INTEGER,
  bytes INTEGER, seconds REAL, total_reported INTEGER, reason TEXT, snapshot_id TEXT,
  published INTEGER, at TEXT, notes TEXT, raw_dir TEXT);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, summary TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(
  dataset_id UNINDEXED, record_key UNINDEXED, snapshot_id UNINDEXED, title, body,
  tokenize = 'unicode61 remove_diacritics 2');
"""
PARSER_VERSION = "3.0.0"        # bump when a normaliser changes meaning, so snapshots record which parser made them


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(records: list[dict]) -> str:
    """Stable hash of a snapshot's content (order-independent), used to detect 'nothing changed'."""
    canon = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) for r in records)
    return hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._backfill_fts()

    # ------------------------------------------------------------------ registry sync
    def register(self, specs) -> None:
        with self._lock:
            for s in specs:
                self.conn.execute(
                    "INSERT INTO datasets(id, official_name, publisher, source_page, pillar) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET official_name=excluded.official_name, publisher=excluded.publisher, "
                    "source_page=excluded.source_page, pillar=excluded.pillar",
                    (s.id, s.official_name, s.publisher, s.source_page, s.pillar))

    # ------------------------------------------------------------------ reads
    def dataset_row(self, dataset_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return dict(r) if r else None

    def current_snapshot(self, dataset_id: str) -> dict | None:
        r = self.conn.execute("SELECT s.* FROM snapshots s JOIN datasets d ON d.current_snapshot=s.id WHERE d.id=?",
                              (dataset_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["notes"] = json.loads(d["notes"] or "[]")
        return d

    def records(self, dataset_id: str, snapshot_id: str | None = None) -> list[dict]:
        snap = snapshot_id or (self.current_snapshot(dataset_id) or {}).get("id")
        if not snap:
            return []
        rows = self.conn.execute("SELECT * FROM records WHERE snapshot_id=? ORDER BY rowid", (snap,)).fetchall()
        return [self._rec(r) for r in rows]

    def records_by_keys(self, snapshot_id: str, keys: list[str]) -> list[dict]:
        if not keys:
            return []
        q = f"SELECT * FROM records WHERE snapshot_id=? AND record_key IN ({','.join('?' * len(keys))})"
        return [self._rec(r) for r in self.conn.execute(q, (snapshot_id, *keys)).fetchall()]

    def index_rows(self, snapshot_ids: list[str]) -> list[dict]:
        if not snapshot_ids:
            return []
        q = f"SELECT * FROM entity_index WHERE snapshot_id IN ({','.join('?' * len(snapshot_ids))})"
        return [dict(r) for r in self.conn.execute(q, snapshot_ids).fetchall()]

    @staticmethod
    def _rec(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("names", "domains", "details"):
            d[k] = json.loads(d[k] or ("{}" if k == "details" else "[]"))
        return d

    def status(self) -> list[dict]:
        """Everything the status page needs, one dict per dataset."""
        out = []
        for r in self.conn.execute("SELECT * FROM datasets ORDER BY pillar, id").fetchall():
            d = dict(r)
            snap = self.current_snapshot(d["id"])
            d["snapshot"] = {k: snap[k] for k in ("id", "created_at", "row_count", "as_of", "source_url",
                                                  "total_reported", "pages", "notes")} if snap else None
            last = self.conn.execute("SELECT * FROM dataset_runs WHERE dataset_id=? ORDER BY at DESC LIMIT 1",
                                     (d["id"],)).fetchone()
            d["last_run"] = dict(last) if last else None
            if d["last_run"]:
                d["last_run"]["notes"] = json.loads(d["last_run"]["notes"] or "[]")
            out.append(d)
        return out

    def run_history(self, dataset_id: str, limit: int = 10) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM dataset_runs WHERE dataset_id=? ORDER BY at DESC LIMIT ?",
                                 (dataset_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ writes
    def publish(self, dataset_id: str, run_id: str, records: list[dict], *, source_url: str, as_of: str | None,
                total_reported: int | None, pages: int, raw_dir: str, notes: list[str]) -> str:
        """Insert a new snapshot and make it current, in ONE transaction. Returns the snapshot id."""
        snap_id = f"{dataset_id}@{run_id}"
        h = content_hash(records)
        with self._lock:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("DELETE FROM records WHERE snapshot_id=?", (snap_id,))        # idempotent re-run
                c.execute("DELETE FROM entity_index WHERE snapshot_id=?", (snap_id,))
                c.execute("DELETE FROM snapshots WHERE id=?", (snap_id,))
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (snap_id, dataset_id, run_id, now_iso(), len(records), h, PARSER_VERSION, source_url,
                           as_of, total_reported, pages, raw_dir, json.dumps(notes, ensure_ascii=False)))
                for r in records:
                    c.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (snap_id, dataset_id, str(r["record_key"]), r.get("name", ""), r.get("name_ar", ""),
                               r.get("date"), r.get("status", ""), r.get("category", ""),
                               json.dumps(r.get("names", []), ensure_ascii=False),
                               json.dumps(r.get("domains", []), ensure_ascii=False),
                               json.dumps(r.get("details", {}), ensure_ascii=False, default=str)))
                    for kind, value in [("name", n) for n in r.get("names", [])] + \
                                       [("domain", d) for d in r.get("domains", [])]:
                        c.execute("INSERT INTO entity_index VALUES(?,?,?,?,?,?,?)",
                                  (snap_id, dataset_id, str(r["record_key"]), kind, value,
                                   normalize_name(value) if kind == "name" else value.lower(),
                                   core_name(value) if kind == "name" else value.lower()))
                self._index_fts(dataset_id, snap_id, records)
                c.execute("UPDATE datasets SET current_snapshot=?, last_success_at=?, row_count=?, as_of=? WHERE id=?",
                          (snap_id, now_iso(), len(records), as_of, dataset_id))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            self._prune(dataset_id)
        return snap_id

    # ------------------------------------------------------------------ full-text search (FTS5)
    @staticmethod
    def _fts_text(r: dict) -> tuple[str, str]:
        """(title, body) for one record: what a person might type to find it."""
        def flat(v):
            if isinstance(v, dict):
                return " ".join(flat(x) for k, x in v.items() if k != "source_page")
            if isinstance(v, (list, tuple)):
                return " ".join(flat(x) for x in v)
            return "" if v is None else str(v)
        title = " ".join(dict.fromkeys([r.get("name") or "", r.get("name_ar") or "", *(r.get("names") or [])]))
        body = " ".join([r.get("category") or "", r.get("status") or "", str(r.get("date") or ""),
                         " ".join(r.get("domains") or []), flat(r.get("details") or {})])
        return title, body

    def _index_fts(self, dataset_id: str, snap_id: str, records: list[dict]) -> None:
        """Replace a dataset's rows in the index (call inside the publish transaction)."""
        self.conn.execute("DELETE FROM record_fts WHERE dataset_id=?", (dataset_id,))
        self.conn.executemany("INSERT INTO record_fts VALUES(?,?,?,?,?)",
                              [(dataset_id, str(r["record_key"]), snap_id, *self._fts_text(r)) for r in records])

    def _backfill_fts(self) -> None:
        """Databases made before the index existed: index their current snapshots once."""
        with self._lock:
            if self.conn.execute("SELECT 1 FROM record_fts LIMIT 1").fetchone():
                return
            rows = self.conn.execute("SELECT id, current_snapshot FROM datasets WHERE current_snapshot IS NOT NULL").fetchall()
            if not rows:
                return
            self.conn.execute("BEGIN IMMEDIATE")
            for ds, snap in rows:
                self._index_fts(ds, snap, self.records(ds, snap))
            self.conn.execute("COMMIT")

    def fts_search(self, match: str, dataset_ids: list[str] | None = None, limit: int = 50) -> list[dict]:
        """Ranked full-text hits: [{dataset_id, record_key, snapshot_id, rank, title_snippet}] (best first)."""
        sql = ("SELECT dataset_id, record_key, snapshot_id, bm25(record_fts, 0, 0, 0, 4.0, 1.0) AS rank, "
               "snippet(record_fts, 3, '[', ']', ' ... ', 12) AS snip FROM record_fts WHERE record_fts MATCH ?")
        args: list = [match]
        if dataset_ids:
            sql += f" AND dataset_id IN ({','.join('?' * len(dataset_ids))})"
            args += dataset_ids
        sql += " ORDER BY rank LIMIT ?"
        try:
            return [dict(r) for r in self.conn.execute(sql, (*args, limit)).fetchall()]
        except sqlite3.OperationalError:            # a query the FTS syntax rejects: no hits rather than a crash
            return []

    def previous_snapshot(self, dataset_id: str) -> str | None:
        """The snapshot published before the current one (for 'what changed')."""
        cur = (self.dataset_row(dataset_id) or {}).get("current_snapshot")
        r = self.conn.execute("SELECT id FROM snapshots WHERE dataset_id=? AND id<>? ORDER BY created_at DESC, id DESC "
                              "LIMIT 1", (dataset_id, cur)).fetchone()
        return r[0] if r else None

    def record_keys(self, snapshot_id: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT record_key FROM records WHERE snapshot_id=?", (snapshot_id,))}

    def mark_checked(self, dataset_id: str) -> None:
        """A refresh found identical content: the current snapshot is still right, and now verified again."""
        with self._lock:
            self.conn.execute("UPDATE datasets SET last_success_at=? WHERE id=?", (now_iso(), dataset_id))

    def record_outcome(self, run_id: str, o) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO dataset_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (run_id, o.dataset_id, o.status, o.rows, o.pages, o.requests, o.bytes, round(o.seconds, 2),
                               o.total_reported, o.reason, o.snapshot_id, int(o.published), now_iso(),
                               json.dumps(o.notes, ensure_ascii=False), o.raw_dir))
            self.conn.execute("UPDATE datasets SET last_attempt_at=?, last_status=?, last_error=? WHERE id=?",
                              (now_iso(), o.status, None if o.status in ("SUCCESS", "UNCHANGED") else o.reason,
                               o.dataset_id))

    def save_run(self, summary: dict) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)",
                              (summary["run_id"], summary["started_at"], summary["finished_at"],
                               json.dumps(summary, ensure_ascii=False)))

    def _prune(self, dataset_id: str) -> None:
        old = self.conn.execute("SELECT id FROM snapshots WHERE dataset_id=? ORDER BY created_at DESC, id DESC",
                                (dataset_id,)).fetchall()[config.SNAPSHOTS_TO_KEEP:]
        current = (self.dataset_row(dataset_id) or {}).get("current_snapshot")
        for (sid,) in old:
            if sid == current:
                continue
            self.conn.execute("DELETE FROM records WHERE snapshot_id=?", (sid,))
            self.conn.execute("DELETE FROM entity_index WHERE snapshot_id=?", (sid,))
            self.conn.execute("DELETE FROM snapshots WHERE id=?", (sid,))

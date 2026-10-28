"""
ingest/raw_store.py - keep every server answer exactly as received, before we parse it
=====================================================================================

WHAT IT DOES
    Saves the ORIGINAL bytes of every response (page 1, page 2, ...) plus a small metadata file, so:
        * you can open the file and see exactly what the CMA sent (debugging, audits, disputes);
        * a parser bug can be fixed and the data rebuilt WITHOUT refetching:  python -m ingest.run --from-raw <run_id>
        * failed / unexpected answers are kept as evidence (schema-drift samples).

LAYOUT
    data/raw/<run_id>/<dataset_id>/
        page_001.body           the exact bytes received
        page_001.meta.json      url, method, request body, HTTP status, content type, sha256, bytes, seconds
        page_002.body ...
        FAILED_<n>.body         the body of an answer that made the dataset fail (if any)

SECURITY
    Request headers are NOT saved (they contain the session token). Request bodies are saved: they hold
    only public parameters (typeId, page number, language).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import config

from .responses import RawResponse


class RawStore:
    def __init__(self, run_id: str, dataset_id: str, root: Path | None = None):
        self.dir = Path(root or config.RAW_DIR) / run_id / dataset_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def save(self, resp: RawResponse, label: str = "page", extra: dict | None = None) -> dict:
        """Write body + meta. Returns the meta dict (also useful for the run log)."""
        self.count += 1
        stem = f"{label}_{self.count:03d}"
        sha = hashlib.sha256(resp.body).hexdigest()
        meta = {"file": f"{stem}.body", "url": resp.url, "method": resp.method, "status": resp.status,
                "content_type": resp.content_type, "bytes": len(resp.body), "sha256": sha,
                "seconds": round(resp.seconds, 3), "attempt": resp.attempt,
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "request_body": resp.request_body, **(extra or {})}
        (self.dir / f"{stem}.body").write_bytes(resp.body)
        (self.dir / f"{stem}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return meta


def load_raw_pages(run_dir: Path) -> list[tuple[dict, bytes]]:
    """All saved pages of one dataset in one run, in order: [(meta, body), ...] (used by --from-raw)."""
    out = []
    for meta_file in sorted(run_dir.glob("page_*.meta.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        out.append((meta, (run_dir / meta["file"]).read_bytes()))
    return out

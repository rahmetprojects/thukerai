"""
ingest/validate.py - the quality gate between "fetched" and "published"
======================================================================

WHAT IT DOES
    Decides whether a freshly fetched dataset is trustworthy enough to REPLACE the current copy.
    Each check either passes, adds a NOTE (logged, shown on the status page, publish still allowed),
    or raises a Rejection with the outcome status the run log should record.

THE CHECKS, IN ORDER
    1. required fields   raw items missing a required field are dropped; > 10 % missing => SCHEMA_DRIFT
    2. empty             zero valid records => EMPTY (never published: an empty list would look like
                         "no warnings exist", which is false and dangerous)
    3. duplicates        two records with the same key are merged (first kept) and counted
    4. minimum rows      fewer than the registry's min_rows => FAILED
    5. sudden drop       row count fell by more than max_drop_pct vs the current snapshot => FAILED
                         (protects against a half-broken source silently deleting most of a list)
"""
from __future__ import annotations


class Rejection(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def check_required(items: list[dict], required: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    """Drop items missing required fields. Raises SCHEMA_DRIFT if too many are missing."""
    notes: list[str] = []
    good = [it for it in items if all(it.get(f) not in (None, "") for f in required)]
    bad = len(items) - len(good)
    if items and bad > max(1, len(items) // 10):
        missing = sorted({f for it in items for f in required if it.get(f) in (None, "")})
        raise Rejection("SCHEMA_DRIFT", f"{bad} of {len(items)} items lack required fields {missing}; "
                        "the source's structure probably changed")
    if bad:
        notes.append(f"{bad} item(s) dropped for missing required fields {list(required)}")
    return good, notes


def dedupe(records: list[dict]) -> tuple[list[dict], list[str]]:
    seen, out = set(), []
    for r in records:
        k = str(r["record_key"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    dup = len(records) - len(out)
    return out, ([f"{dup} duplicate record(s) merged (same record key)"] if dup else [])


def check_counts(records: list[dict], spec, previous_rows: int | None) -> list[str]:
    n = len(records)
    if n == 0:
        raise Rejection("EMPTY", "the source answered correctly but returned zero records")
    if n < spec.min_rows:
        raise Rejection("FAILED", f"only {n} valid records; the registry expects at least {spec.min_rows}")
    notes = []
    if previous_rows:
        drop = 100 * (previous_rows - n) / previous_rows
        if drop > spec.max_drop_pct:
            raise Rejection("FAILED", f"row count fell from {previous_rows} to {n} ({drop:.0f}% drop, limit "
                            f"{spec.max_drop_pct:.0f}%); looks broken, keeping the previous copy")
        if drop > 0:
            notes.append(f"{previous_rows - n} fewer records than the previous snapshot ({previous_rows} -> {n})")
        elif n > previous_rows:
            notes.append(f"{n - previous_rows} more records than the previous snapshot ({previous_rows} -> {n})")
    return notes

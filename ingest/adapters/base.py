"""
ingest/adapters/base.py - what every dataset adapter receives and returns
========================================================================

WHAT IT DOES
    Defines the small, shared vocabulary of the adapters:
        Context      everything an adapter may use during a fetch (session, run log, raw store, sleep)
        PageInfo     what ONE page/response contained after its envelope was checked
        FetchResult  everything an adapter fetched for a dataset (all pages), before normalising

    An adapter has two jobs, kept separate on purpose:
        fetch(spec, ctx)          talk to the network, save every raw response, walk the pages
        extract(spec, obj)        PURE: read one parsed response envelope -> PageInfo, or raise schema_drift
    Because extract() is pure, the same code re-parses saved raw files (--from-raw) and runs in tests.

    Adapters NEVER guess where the records are (no "longest list in the JSON" heuristics): each one knows
    the exact envelope path for its endpoint, verified on the live site, and rejects anything else.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..raw_store import RawStore
from ..runlog import RunLog


@dataclass
class Context:
    runlog: RunLog
    raw: RawStore
    session: object | None = None            # ingest.http_client.CmaSession (None for local files)
    site: dict = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep


@dataclass
class PageInfo:
    items: list[dict]
    total_records: int | None = None          # what the source SAYS the total number of rows is
    total_pages: int | None = None
    page: int | None = None
    as_of: str | None = None                   # the publisher's own "last modified" for this answer


@dataclass
class FetchResult:
    items: list[dict]                          # all raw items, all pages, in order (duplicates removed)
    pages: int
    source_url: str
    total_reported: int | None = None
    as_of: str | None = None
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # adapter-specific (e.g. Arabic names by key)

"""
ingest/adapters/html_table.py - read a dataset published as a plain HTML <table>
================================================================================

USED FOR (registry kind: html_table)
    Appraisers of in-kind contributions (the CMA publishes it as a static table, verified 2026-10-28).

HOW
    GET the page (through the same CMA session, so cookies are consistent), find the <table> whose header
    row contains EVERY column name listed in the registry (`request.columns`), and read its rows.
    If no table has those headers, that is schema drift: the page changed and a human must look.
"""
from __future__ import annotations

from html.parser import HTMLParser

from ..responses import FetchError, sniff
from .base import Context, FetchResult, PageInfo


class _Tables(HTMLParser):
    """Collects every table as a list of rows (each a list of cell texts)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.tables[-1].append(self._row)
            self._row = None
        elif tag == "table":
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def extract(html: str, columns: dict[str, str]) -> PageInfo:
    p = _Tables()
    p.feed(html)
    wanted = {v.lower(): k for k, v in columns.items()}
    for t in p.tables:
        if not t:
            continue
        header = [h.lower() for h in t[0]]
        if all(w in header for w in wanted):
            idx = {wanted[h]: i for i, h in enumerate(header) if h in wanted}
            rows = [{k: (r[i] if i < len(r) else "") for k, i in idx.items()} for r in t[1:]]
            return PageInfo(items=rows, total_records=len(rows))
    raise FetchError("schema_drift", f"no table with columns {list(columns.values())} "
                     f"(found {len(p.tables)} table(s) with headers {[t[0] for t in p.tables if t][:3]})")


def fetch(spec, ctx: Context) -> FetchResult:
    resp = ctx.session.get_page(spec.id, spec.source_page, purpose="table page")
    meta = ctx.raw.save(resp, "page")
    if resp.status != 200 or sniff(resp.body) != "html":
        ctx.raw.save(resp, "FAILED")
        raise FetchError("http_status", f"HTTP {resp.status}, body looks like {sniff(resp.body)}", status=resp.status)
    info = extract(resp.text, spec.request["columns"])
    ctx.runlog.event(spec.id, "page", "info", f"table found with {len(info.items)} rows", bytes=meta["bytes"])
    return FetchResult(items=info.items, pages=1, source_url=spec.source_page, total_reported=len(info.items))

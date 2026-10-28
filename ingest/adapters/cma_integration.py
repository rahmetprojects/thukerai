"""
ingest/adapters/cma_integration.py - adapter for the CMA "GeIntegrationResult" API
==================================================================================

USED FOR (registry kind: cma_integration)
    licensed companies (integrationId 2045), authorised finfluencers (2056)
    (and, through cma_activities.py, the activity list 2047)

THE ENDPOINT (verified on the live site, 2026-10-28)
    POST https://www.uaecma.gov.ae/api/PublicApi/GeIntegrationResult      <- "Ge", the site's own spelling
    body    {"integrationId": 2045, "urlParameters": {"pageIndex": 1, "pageSize": 100, "type": "",
             "keyword": "", "languageId": 1, "languageCode": "en-GB"}}
    answer  {"code": 200, "data": {"total": <PAGES>, "records": <ROWS>, "page": p, "items": [...]},
             "headers": {...}, "dateModified": "..."}

QUIRKS FOUND ON THE LIVE SITE (and how we handle them)
    * `total` is the number of PAGES, `records` the number of ROWS - but ONLY on page 1. Later pages
      (and any request whose pageSize exceeds the row count) say total=1, records=1. So we read the
      totals from page 1 only, and only trust them when page 1 says there is more than one page.
    * A page past the end returns items: [] with the same bogus total=1, records=1.
    * Finfluencers: the site itself asks for pageSize 2147483647 (everything at once); we do the same.

ARABIC
    With `also_arabic: true` in the registry, the list is fetched a second time with languageId 2 and the
    Arabic names are attached to the English records by their key (e.g. licence code CP-0001315).
"""
from __future__ import annotations

from ..responses import FetchError, expect_json
from .base import Context, FetchResult, PageInfo


def extract(obj: dict) -> PageInfo:
    data = obj.get("data")
    if not isinstance(data, dict):
        raise FetchError("schema_drift", f"'data' is {type(data).__name__}, expected an object; keys={list(obj)[:8]}")
    items = data.get("items")
    if not isinstance(items, list):
        raise FetchError("schema_drift", f"'data.items' missing or not a list; data keys={list(data)[:8]}")
    if any(not isinstance(it, dict) for it in items):
        raise FetchError("schema_drift", "'data.items' contains non-objects")
    return PageInfo(items=items, total_records=data.get("records") if isinstance(data.get("records"), int) else None,
                    total_pages=data.get("total") if isinstance(data.get("total"), int) else None,
                    page=data.get("page"), as_of=obj.get("dateModified"))


def call(ctx: Context, dataset_id: str, integration_id: int, params: dict, lang: str, purpose: str, label: str = "page"):
    lang_id, code = ("2", "ar-AE") if lang == "ar" else ("1", "en-GB")
    url_params = {**params, "languageId": int(lang_id), "languageCode": code}
    resp = ctx.session.post_api(dataset_id, ctx.site["integration_endpoint"],
                                {"integrationId": int(integration_id), "urlParameters": url_params},
                                lang=lang, purpose=purpose)
    meta = ctx.raw.save(resp, label, {"purpose": purpose, "lang": lang})
    try:
        return extract(expect_json(resp)), meta
    except FetchError:
        ctx.raw.save(resp, "FAILED", {"purpose": purpose})
        raise


def fetch_all(spec_id: str, ctx: Context, integration_id: int, *, page_size: int, paged: bool,
              key: str, params: dict | None = None, lang: str = "en") -> FetchResult:
    """Every item of one integration list, walking pages if `paged`. Shared with cma_activities.py."""
    from ingest import pacing
    log, params = ctx.runlog, dict(params or {})
    if not paged:
        info, meta = call(ctx, spec_id, integration_id, {**params, "pageIndex": 1, "pageSize": page_size}, lang,
                          f"all rows ({lang})")
        log.event(spec_id, "page", "info", f"single request ({lang}): {len(info.items)} items", bytes=meta["bytes"],
                  sha256=meta["sha256"][:12], reported_total=info.total_records)
        notes = []
        if info.total_records not in (None, len(info.items)) and info.total_records > 1:
            notes.append(f"source reported {info.total_records} rows but returned {len(info.items)}")
        return FetchResult(items=_dedupe(info.items, key, log, spec_id), pages=1, source_url="",
                           total_reported=None, as_of=info.as_of, notes=notes)

    items: list[dict] = []
    seen: set = set()
    prev: tuple = ()
    total_pages = total_rows = None
    as_of = None
    page = 1
    while True:
        pacing.check_page_cap(page, spec_id)
        info, meta = call(ctx, spec_id, integration_id, {**params, "pageIndex": page, "pageSize": page_size}, lang,
                          f"page {page} ({lang})")
        if page == 1 and (info.total_pages or 0) > 1:
            total_pages, total_rows = info.total_pages, info.total_records
            log.event(spec_id, "paging", "info", f"source reports {total_rows} rows in {total_pages} pages ({lang})")
        elif page == 1:
            log.event(spec_id, "paging", "info", f"page 1 fits everything; source totals not usable "
                      f"(total={info.total_pages}, records={info.total_records}) - a known quirk")
        keys = tuple(str(it.get(key)) for it in info.items)
        new = [it for it in info.items if str(it.get(key)) not in seen]
        as_of = max(filter(None, [as_of, info.as_of]), default=None)
        log.event(spec_id, "page", "info", f"page {page} ({lang}): {len(info.items)} items ({len(new)} new)",
                  duplicates=len(info.items) - len(new), bytes=meta["bytes"], sha256=meta["sha256"][:12])
        if not info.items:
            if total_pages and page <= total_pages:
                raise FetchError("pagination", f"page {page} empty but page 1 said {total_pages} pages")
            break
        if keys == prev:
            raise FetchError("pagination", f"page {page} repeated page {page - 1}: the server ignored pageIndex")
        prev = keys
        for it in new:
            seen.add(str(it.get(key)))
            items.append(it)
        if (total_pages and page >= total_pages) or len(info.items) < page_size:
            break
        page += 1
        ctx.sleep(pacing.page_delay())
    notes: list[str] = []
    if total_rows is not None and len(items) < total_rows:
        raise FetchError("incomplete", f"got {len(items)} unique rows but page 1 reported {total_rows}")
    if total_rows is not None and len(items) > total_rows:
        notes.append(f"got {len(items)} rows, more than the {total_rows} reported (rows added during the crawl?)")
    log.event(spec_id, "reconcile", "info", f"collected {len(items)} unique rows over {page} page(s) ({lang})",
              reported=total_rows)
    return FetchResult(items=items, pages=page, source_url="", total_reported=total_rows, as_of=as_of, notes=notes)


def _dedupe(items: list[dict], key: str, log, spec_id: str) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = str(it.get(key))
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    if len(out) < len(items):
        log.event(spec_id, "dedupe", "warn", f"{len(items) - len(out)} duplicate rows dropped (same {key})")
    return out


def fetch(spec, ctx: Context) -> FetchResult:
    req = spec.request
    res = fetch_all(spec.id, ctx, req["integration_id"], page_size=int(req.get("page_size", 100)),
                    paged=bool(req.get("paged", True)), key=spec.record_key, params=req.get("params"))
    res.source_url = spec.source_page
    if req.get("also_arabic"):
        try:
            ar = fetch_all(spec.id, ctx, req["integration_id"], page_size=int(req.get("page_size", 100)),
                           paged=bool(req.get("paged", True)), key=spec.record_key, params=req.get("params"), lang="ar")
            res.extra["arabic_by_key"] = {str(it.get(spec.record_key)): it for it in ar.items}
            res.pages += ar.pages
            matched = sum(1 for it in res.items if str(it.get(spec.record_key)) in res.extra["arabic_by_key"])
            ctx.runlog.event(spec.id, "arabic", "info", f"Arabic names attached to {matched} of {len(res.items)} rows")
        except FetchError as e:                       # Arabic is an enrichment: its failure must not lose the English list
            res.notes.append(f"Arabic names not fetched ({e.kind}: {e.message})")
            ctx.runlog.event(spec.id, "arabic", "warn", f"Arabic pass failed, English kept: {e.message}")
    return res

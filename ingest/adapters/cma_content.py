"""
ingest/adapters/cma_content.py - adapter for the CMA "GetContentList" API
=========================================================================

USED FOR (registry kind: cma_content_list)
    warnings (typeId 1071), violations (1039), local funds (1036), passported funds (1037),
    end-of-service funds (1076), financial auditors (1045), assembly organisers (1070), publications (16)

THE ENDPOINT (verified on the live site, 2026-10-28)
    POST https://www.uaecma.gov.ae/api/PublicApi/GetContentList
    body    {"htmlTemplatePath": "ajax-templates/pages/<template>", "loadResultAsHTML": false,
             "typeId": <id>, "languageId": 1, "languageCode": "en-GB", "pageIndex": <1..>,
             "pageSize": <n>, "isArchived": false, "FilterGroups": [<active filter>], "Sort": [...]}
    answer  {"code": 200, "data": {"page": p, "records": <total rows>, "totalPages": <n>,
             "contents": [ {id, groupId, title, languageId, isoItemDate1, lastUpdateDate, attr:{...}}, ... ]},
             "dateModified": "..."}
    paging  pageIndex starts at 1 (0 is treated as 1). A page past the end returns contents: [] and
            records: 0. pageSize up to 500 works; we use 100 to stay polite and to exercise paging.

STOPPING RULES (no guessing)
    * stop after page `totalPages` reported on page 1
    * an EMPTY page before that point is an error (a gap), not the end
    * the same set of ids twice in a row is an error (the server ignored pageIndex)
    * at the end, the number of unique items must equal `records` from page 1; fewer = incomplete -> error
"""
from __future__ import annotations

from ..responses import FetchError, expect_json
from .base import Context, FetchResult, PageInfo

ITEM_REQUIRED = ("id", "groupId", "title")


def extract(obj: dict) -> PageInfo:
    """One parsed GetContentList answer -> PageInfo. Raises schema_drift on any unknown structure."""
    data = obj.get("data")
    if not isinstance(data, dict):
        raise FetchError("schema_drift", f"'data' is {type(data).__name__}, expected an object; keys={list(obj)[:8]}")
    contents = data.get("contents")
    if not isinstance(contents, list):
        raise FetchError("schema_drift", f"'data.contents' missing or not a list; data keys={list(data)[:8]}")
    for k in ("records", "totalPages"):
        if not isinstance(data.get(k), int):
            raise FetchError("schema_drift", f"'data.{k}' missing or not a number (got {data.get(k)!r})")
    bad = [i for i, it in enumerate(contents) if not isinstance(it, dict) or any(f not in it for f in ITEM_REQUIRED)]
    if bad and len(bad) > max(1, len(contents) // 10):
        raise FetchError("schema_drift", f"{len(bad)} of {len(contents)} items lack {ITEM_REQUIRED}; first bad index {bad[0]}")
    items = [it for i, it in enumerate(contents) if i not in set(bad)]
    return PageInfo(items=items, total_records=data["records"], total_pages=data["totalPages"],
                    page=data.get("page"), as_of=obj.get("dateModified"))


def build_payload(req: dict, site: dict, page: int) -> dict:
    payload = {"htmlTemplatePath": f"ajax-templates/pages/{req['template']}", "loadResultAsHTML": False,
               "typeId": int(req["type_id"]), "languageId": 1, "languageCode": "en-GB",
               "pageIndex": page, "pageSize": int(req.get("page_size", 100)), "isArchived": False,
               "FilterGroups": site.get("active_filter", [])}
    if req.get("sort"):
        payload["Sort"] = req["sort"]
    return payload


def fetch(spec, ctx: Context) -> FetchResult:
    from ingest import pacing   # local import: keeps this module importable without the pipeline
    req, log = spec.request, ctx.runlog
    endpoint = ctx.site["content_list_endpoint"]
    items: list[dict] = []
    seen: set = set()
    notes: list[str] = []
    prev_ids: tuple = ()
    total_records = total_pages = None
    as_of = None
    page = 1
    while True:
        pacing.check_page_cap(page, spec.id)
        resp = ctx.session.post_api(spec.id, endpoint, build_payload(req, ctx.site, page), purpose=f"page {page}")
        meta = ctx.raw.save(resp, "page", {"page": page})
        try:
            info = extract(expect_json(resp))
        except FetchError:
            ctx.raw.save(resp, "FAILED", {"page": page})
            raise
        if page == 1:
            total_records, total_pages = info.total_records, info.total_pages
            log.event(spec.id, "paging", "info", f"source reports {total_records} rows in {total_pages} pages",
                      page_size=req.get("page_size"))
        as_of = max(filter(None, [as_of, info.as_of]), default=None)
        ids = tuple(it["id"] for it in info.items)
        new = [it for it in info.items if it["id"] not in seen]
        langs = sorted({it.get("languageId") for it in info.items})
        log.event(spec.id, "page", "info", f"page {page}: {len(info.items)} items ({len(new)} new)",
                  duplicates=len(info.items) - len(new), languages=langs, sha256=meta["sha256"][:12],
                  bytes=meta["bytes"])
        if not info.items:
            if page == 1:
                break                                                     # genuinely empty dataset
            if total_pages and page <= total_pages:
                raise FetchError("pagination", f"page {page} is empty but page 1 said there are {total_pages} pages")
            break
        if ids == prev_ids:
            raise FetchError("pagination", f"page {page} repeated page {page - 1}: the server ignored pageIndex")
        prev_ids = ids
        for it in new:
            seen.add(it["id"])
            items.append(it)
        if total_pages is not None and page >= total_pages:
            break
        page += 1
        ctx.sleep(pacing.page_delay())

    # reconcile with what the source said
    if total_records is not None:
        if len(items) < total_records:
            raise FetchError("incomplete", f"got {len(items)} unique items but the source reported {total_records}; "
                             "not publishing a partial list")
        if len(items) > total_records:
            notes.append(f"got {len(items)} unique items, more than the {total_records} the source reported "
                         "(records were probably added during the crawl)")
    log.event(spec.id, "reconcile", "info", f"collected {len(items)} unique items over {page} page(s)",
              reported=total_records)
    return FetchResult(items=items, pages=page, source_url=spec.source_page, total_reported=total_records,
                       as_of=as_of, notes=notes)

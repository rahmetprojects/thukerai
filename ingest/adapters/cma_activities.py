"""
ingest/adapters/cma_activities.py - which licensed company may do WHICH activity
================================================================================

WHY THIS EXISTS
    A licence is for specific activities. "This firm is licensed" is not enough to protect someone:
    a firm licensed only to introduce clients is not allowed to manage their money. The CMA page lets
    visitors filter licensed companies by activity, and we reproduce that filter for every activity.

HOW (registry kind: cma_activity_map, verified on the live site 2026-10-28)
    1. GeIntegrationResult 2047 {languageCode}            -> [{companyId: <activity id>, title}, ...]
       (despite the name, "companyId" here is the ACTIVITY id; one id can appear with two titles,
        e.g. the same approval on two exchanges)
    2. for each distinct activity id:
       GeIntegrationResult 2045 {type: <activity id>, pageIndex, pageSize, keyword: ""}  -> the firms
    Result: one record per activity id, with the licence codes of the firms allowed to do it.

COST
    About 55 small requests with a polite pause between them (~40 s). Refreshed every 72 hours.
    One failing activity fails the dataset (we never publish a map with silent holes).
"""
from __future__ import annotations

from .base import Context, FetchResult
from .cma_integration import call, fetch_all


def fetch(spec, ctx: Context) -> FetchResult:
    from ingest import pacing
    req, log = spec.request, ctx.runlog
    info, meta = call(ctx, spec.id, req["activity_list_integration_id"], {}, "en", "activity list")
    titles: dict[str, list[str]] = {}
    for it in info.items:
        aid, title = str(it.get("companyId", "")).strip(), str(it.get("title", "")).strip()
        if aid and title:
            titles.setdefault(aid, [])
            if title not in titles[aid]:
                titles[aid].append(title)
    log.event(spec.id, "activities", "info", f"{len(info.items)} activity entries -> {len(titles)} distinct activity ids",
              bytes=meta["bytes"])
    items, pages = [], 1
    for n, (aid, names) in enumerate(titles.items(), start=1):
        ctx.sleep(pacing.page_delay())
        res = fetch_all(spec.id, ctx, req["company_integration_id"], page_size=int(req.get("page_size", 100)),
                        paged=True, key="code", params={"type": aid, "keyword": ""})
        pages += res.pages
        codes = [str(c.get("code")) for c in res.items if c.get("code")]
        log.event(spec.id, "activity", "info" if codes else "warn",
                  f"[{n}/{len(titles)}] activity {aid}: {len(codes)} firms", activity=names[0][:70])
        items.append({"activity_id": aid, "activity": names[0], "titles": names, "company_codes": codes})
    empty = [i["activity_id"] for i in items if not i["company_codes"]]
    notes = [f"{len(empty)} activities currently have no licensed firm: {', '.join(empty[:10])}"] if empty else []
    return FetchResult(items=items, pages=pages, source_url=spec.source_page, total_reported=len(titles),
                       as_of=info.as_of, notes=notes)

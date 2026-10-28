"""
api/v1.py - the ONE DOOR: the public, read-only API every touchpoint uses (and the onboarding endpoints)
======================================================================================================

CHECK (the Trust Report; used by the Check page, the bank demo, the browser extension, MCP)
    GET  /api/v1/check?q=<anything>          names, websites, @handles, phone numbers, or a pasted message
    POST /api/v1/check        {"text"}       the same, for long text
    POST /api/v1/check/image  raw image      a screenshot (needs an AI key to read the text in it)

FIND DATA (the explorer)
    GET  /api/v1/ask?q=<question>            question -> plan -> cited records + the reusable API URL
    GET  /api/v1/query?op=&datasets=&text=&since=&until=&f.<field>=&limit=&format=json|csv
                                             run an explicit plan (what /ask returns as `api.url`)
    GET  /api/v1/datasets                    the catalogue (cards)
    GET  /api/v1/datasets/{id}               one card: description EN/AR, fields, profile, filters, health
    GET  /api/v1/datasets/{id}/records       records with the same filters as /query (format=csv to download)
    GET  /api/v1/catalog.jsonld              the catalogue as DCAT JSON-LD (open-data portal standard)
    GET  /api/v1/changes                     records added since the previous copy of each dataset

ONBOARDING (a person adds a new official source)
    POST   /api/v1/onboard/discover {"url"}                     run the detection ladder, return proposals
    POST   /api/v1/onboard/approve  {"discovery", "proposal", "overrides"?}   save + fetch once
    DELETE /api/v1/onboard/{id}                                 remove an onboarded source

RULES
    Read endpoints are public data: CORS is open for GET, no cookies are used, nothing personal is stored.
    Approvals use proposals the SERVER produced (kept 30 minutes), never a config sent by the browser.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import threading
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

import config
from explore.planner import Plan, validate_plan

MAX_IMAGE_BYTES = 5_000_000


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DiscoverIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)


class ApproveIn(BaseModel):
    discovery: str
    proposal: str
    overrides: dict | None = None


def _plan_from_query(request: Request, service, dataset: str | None = None) -> Plan | None:
    q = request.query_params
    raw = {"op": q.get("op", "list"), "text": q.get("text") or q.get("q") or "", "since": q.get("since"),
           "until": q.get("until"), "limit": q.get("limit") or 50,
           "datasets": [dataset] if dataset else [d for d in (q.get("datasets") or "").split(",") if d],
           "filters": {k[2:]: v for k, v in q.items() if k.startswith("f.")}}
    return validate_plan(raw, service.retrieval.reg, service.explorer.catalog.filters)


def _csv(records: list[dict]) -> Response:
    buf = io.StringIO()
    cols = ["dataset_id", "record_key", "name", "name_ar", "date", "status", "category", "domains", "source_page",
            "fetched_at", "details"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in records:
        w.writerow({**r, "domains": " ".join(r.get("domains") or []),
                    "details": json.dumps(r.get("details") or {}, ensure_ascii=False)})
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=records.csv"})


def create_router(service, discovery_factory=None, transport=None) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    proposals: dict[str, tuple[float, dict]] = {}
    lock = threading.Lock()

    # ------------------------------------------------------------------ check
    @r.get("/check")
    def check_get(q: str = Query(..., min_length=1, max_length=4000)):
        return service.trust.check(q)

    @r.post("/check")
    def check_post(body: TextIn):
        return service.trust.check(body.text)

    @r.post("/check/image")
    async def check_image(request: Request):
        img = await request.body()
        if not img or len(img) > MAX_IMAGE_BYTES:
            return JSONResponse({"error": "no image, or larger than 5 MB"}, status_code=413)
        mime = request.headers.get("content-type", "image/png").split(";")[0]
        if not mime.startswith("image/"):
            return JSONResponse({"error": "send an image (image/png or image/jpeg)"}, status_code=415)
        import anyio
        return await anyio.to_thread.run_sync(lambda: service.trust.check("", image=img, mime=mime))

    # ------------------------------------------------------------------ find data
    @r.get("/ask")
    def ask(q: str = Query(..., min_length=2, max_length=500)):
        return service.explorer.ask(q)

    @r.get("/query")
    def query(request: Request, format: str = "json"):
        plan = _plan_from_query(request, service)
        if not plan:
            return JSONResponse({"error": "invalid query: unknown op, dataset id or filter (see /api/v1/datasets)"},
                                status_code=400)
        out = service.explorer.run(plan)
        return _csv(out["records"]) if format == "csv" else out

    @r.get("/datasets")
    def datasets():
        return {"datasets": service.explorer.catalog.cards()}

    @r.get("/datasets/{dataset_id}")
    def dataset(dataset_id: str):
        if dataset_id not in service.retrieval.reg.ids():
            return JSONResponse({"error": "unknown dataset"}, status_code=404)
        return service.explorer.catalog.card(dataset_id)

    @r.get("/datasets/{dataset_id}/records")
    def dataset_records(dataset_id: str, request: Request, format: str = "json"):
        if dataset_id not in service.retrieval.reg.ids():
            return JSONResponse({"error": "unknown dataset"}, status_code=404)
        plan = _plan_from_query(request, service, dataset_id)
        if not plan:
            return JSONResponse({"error": "invalid filter for this dataset"}, status_code=400)
        out = service.explorer.run(plan)
        return _csv(out["records"]) if format == "csv" else out

    @r.get("/catalog.jsonld")
    def catalog(request: Request):
        base = str(request.base_url).rstrip("/")
        return JSONResponse(service.explorer.catalog.dcat(base), media_type="application/ld+json")

    @r.get("/changes")
    def changes():
        return service.explorer.run(Plan(op="changes", reading=["what changed"]))

    # ------------------------------------------------------------------ onboarding
    @r.post("/onboard/discover")
    def discover(body: DiscoverIn):
        from onboard.discover import Discovery, default_capture
        d = (discovery_factory or (lambda: Discovery(transport, default_capture())))()
        out = d.discover(body.url.strip())
        token = secrets.token_urlsafe(12)
        with lock:
            now = time.time()
            for k in [k for k, (t, _) in proposals.items() if now - t > 1800]:
                del proposals[k]
            proposals[token] = (now, {p["id"]: p for p in out["proposals"]})
        out["discovery"] = token
        return out

    @r.post("/onboard/approve")
    def approve(body: ApproveIn):
        from onboard.approve import approve as do_approve
        with lock:
            found = proposals.get(body.discovery, (0, {}))[1].get(body.proposal)
        if not found:
            return JSONResponse({"ok": False, "error": "proposal not found or expired: run discovery again"},
                                status_code=404)
        res = do_approve(found, body.overrides, db=service.db, transport=transport)
        service.reload()
        return res

    @r.delete("/onboard/{dataset_id}")
    def remove(dataset_id: str):
        from onboard.approve import remove as do_remove
        ok = do_remove(dataset_id)
        service.reload()
        return {"ok": ok}

    return r

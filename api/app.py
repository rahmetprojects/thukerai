"""
api/app.py - the web server (FastAPI): the screen talks to this, this talks to the coordinator
=============================================================================================

ENDPOINTS (JSON; interactive docs at /docs)
  the assistant (one screen: order -> recipe -> specialists -> verified, cited, explained card)
    POST /api/assistant           {session, text, lang?, mode?}  -> Server-Sent Events: order, recipe, step...,
                                  verify, card, done  (kitchen/kitchen.py)
    POST /api/assistant/image     raw image bytes (?session=) -> one card (a screenshot of an offer)
    POST /api/assistant/approve   {session, discovery, proposal, overrides?}  add an official source proposed in chat
    POST /api/assistant/reset     {session}
  the voice guide's original conversation endpoint (kept; the assistant uses the same coordinator for money questions)
    POST /api/chat                {session, text, lang?}  -> reply, mode, pillar, intent, question, quick replies,
                                                            noted facts, EVIDENCE (calculations, official records,
                                                            data snapshots, assumptions, limitations)
    POST /api/reset               {session}                forget this conversation's facts
    POST /api/transcribe          raw audio (audio/webm)   -> {"text"} better speech-to-text via Gemini, if configured
    GET  /api/pillars             the three protection pillars and their example questions (home screen)
    GET  /api/health              AI mode, data summary
  data transparency (what was fetched, what worked, what failed)
    GET  /api/data/status         every dataset: rows served, snapshot, last attempt status + error, notes,
                                  the last run's summary, and the portal datasets NOT fetched (and why)
    GET  /api/data/history/{id}   the last runs of one dataset
    GET  /api/data/events         the newest run's event log (?dataset=cma_warnings&limit=200): every request,
                                  page, row count, token step and validation decision
    GET  /api/data/records/{id}   browse the records currently served (?q=name&limit=50)
    POST /api/data/refresh        {only?: [ids]} start a background refresh now
  the public API, v1 (api/v1.py): Trust Report, explorer, catalogue, DCAT, changes, onboarding
    GET  /api/v1/...              see api/v1.py and docs/API.md
  AI assistants
    POST /mcp                     Model Context Protocol over HTTP (JSON-RPC; see mcp_server.py)
  the screen
    GET  /                        the React app (frontend/dist)

SECURITY
    Listens on 127.0.0.1 by default (this computer only). Request size and message length are capped,
    session ids must look like random tokens, security headers are set. No login and no HTTPS: do not
    expose it to a network as is (docs/LIMITATIONS.md).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from agents.coordinator import pillars_catalogue
from ingest.registry import load_registry

log = logging.getLogger("api")
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
SESSION = r"^[A-Za-z0-9_-]{16,64}$"

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Senior Guide</title><body style="font:20px system-ui;max-width:640px;margin:10vh auto">
<h2>The screen has not been built yet</h2><p>The server is running. Run <code>cd frontend && npm install && npm run build</code>,
then refresh this page.</p></body>"""


class ChatIn(BaseModel):
    session: str = Field(pattern=SESSION)
    text: str = Field(min_length=1, max_length=config.MAX_TEXT_CHARS)
    lang: Optional[Literal["en", "ar"]] = None


class SessionIn(BaseModel):
    session: str = Field(pattern=SESSION)


class AssistantIn(BaseModel):
    session: str = Field(pattern=SESSION)
    text: str = Field(min_length=1, max_length=config.MAX_TEXT_CHARS)
    lang: Optional[Literal["en", "ar"]] = None
    mode: Literal["text", "voice"] = "text"


class AssistantApproveIn(BaseModel):
    session: str = Field(pattern=SESSION)
    discovery: str = Field(min_length=8, max_length=64)
    proposal: str = Field(min_length=1, max_length=120)
    overrides: Optional[dict] = None


class RefreshIn(BaseModel):
    only: Optional[list[str]] = None


def create_app(service, scheduler=None, dist: Path = DIST, transport=None, discovery_factory=None) -> FastAPI:
    app = FastAPI(title="Official data trust layer", version="4.0", docs_url="/docs", redoc_url=None)
    reg = load_registry()
    from api.v1 import MAX_IMAGE_BYTES, create_router
    from kitchen.kitchen import Kitchen
    from mcp_server import MCP
    mcp = MCP(service)
    if discovery_factory is None and transport is not None:
        from onboard.discover import Discovery
        discovery_factory = lambda: Discovery(transport)              # noqa: E731  (tests: fake sites, no browser)
    kitchen = Kitchen(service, discovery_factory=discovery_factory, transport=transport)
    app.state.kitchen = kitchen

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        limit = (config.MAX_AUDIO_BYTES if path == "/api/transcribe" else MAX_IMAGE_BYTES if path in ("/api/v1/check/image", "/api/assistant/image")
                 else 64_000 if path in ("/api/v1/check", "/api/v1/onboard/approve", "/mcp", "/api/assistant",
                                         "/api/assistant/approve") else config.MAX_BODY_BYTES)
        if int(request.headers.get("content-length") or 0) > limit:
            return JSONResponse({"error": "request too large"}, status_code=413)
        if request.method == "OPTIONS" and path.startswith("/api/v1/"):
            resp = JSONResponse({})
        else:
            resp = await call_next(request)
        if path.startswith("/api/v1/") and request.method in ("GET", "OPTIONS", "POST") and \
                not path.startswith("/api/v1/onboard"):
            resp.headers["Access-Control-Allow-Origin"] = "*"           # public official data, no cookies used
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                                                   "img-src 'self' data:; font-src 'self'; connect-src 'self'")
        if not request.url.path.startswith("/assets"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app.include_router(create_router(service, discovery_factory=discovery_factory, transport=transport))

    @app.post("/mcp")
    async def mcp_http(request: Request):
        try:
            msg = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        import anyio
        batch = msg if isinstance(msg, list) else [msg]
        replies = [x for x in [await anyio.to_thread.run_sync(mcp.handle, m) for m in batch] if x]
        if not replies:
            return Response(status_code=202)
        return JSONResponse(replies if isinstance(msg, list) else replies[0])

    # ------------------------------------------------------------------ the assistant (streamed)
    @app.post("/api/assistant")
    def assistant(body: AssistantIn):
        def stream():
            for e in kitchen.turn(body.session, body.text, body.lang, body.mode):
                yield f"event: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False, default=str)}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/assistant/image")
    async def assistant_image(request: Request, session: str = Query(pattern=SESSION)):
        import anyio
        mime = request.headers.get("content-type", "image/png").split(";")[0]
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            return JSONResponse({"error": "send a PNG, JPEG or WebP image"}, status_code=415)
        data = await request.body()
        if not data:
            return JSONResponse({"error": "empty image"}, status_code=400)
        return await anyio.to_thread.run_sync(kitchen.image, session, data, mime)

    @app.post("/api/assistant/approve")
    def assistant_approve(body: AssistantApproveIn):
        res = kitchen.approve(body.discovery, body.proposal, body.overrides)
        return JSONResponse(res, status_code=404 if "not found or expired" in str(res.get("error")) else 200)

    @app.post("/api/assistant/reset")
    def assistant_reset(body: SessionIn):
        kitchen.reset(body.session)
        return {"ok": True}

    # ------------------------------------------------------------------ conversation
    @app.post("/api/chat")
    def chat(body: ChatIn):
        r = service.coordinator.turn(body.session, body.text, body.lang)
        return r.as_dict(debug=config.DEBUG)

    @app.post("/api/reset")
    def reset(body: SessionIn):
        service.coordinator.reset(body.session)
        return {"ok": True}

    @app.post("/api/transcribe")
    async def transcribe(request: Request, lang: Literal["en", "ar"] = "en"):
        if not service.llm.can_transcribe:
            return JSONResponse({"error": "server transcription is not configured (set GEMINI_API_KEY)"}, status_code=501)
        audio = await request.body()
        if not audio or len(audio) > config.MAX_AUDIO_BYTES:
            return JSONResponse({"error": "no audio, or too long"}, status_code=413)
        import anyio
        text = await anyio.to_thread.run_sync(service.llm.transcribe, audio,
                                              request.headers.get("content-type", "audio/webm"), lang)
        return {"text": text or ""}

    @app.get("/api/pillars")
    def pillars():
        return {"pillars": pillars_catalogue()}

    @app.get("/api/health")
    def health():
        st = service.db.status()
        return {"ok": True, "ai": service.llm.provider, "model": service.llm.model,
                "stt": "gemini" if service.llm.can_transcribe else "browser",
                "data": {"datasets": len(st), "available": sum(1 for d in st if d["snapshot"]),
                         "failing": [d["id"] for d in st if d["last_status"] in ("FAILED", "SCHEMA_DRIFT", "EMPTY")]},
                "refreshing": bool(scheduler and scheduler.running)}

    # ------------------------------------------------------------------ data transparency
    @app.get("/api/data/status")
    def data_status():
        latest = None
        if config.STATUS_FILE.exists():
            try:
                latest = json.loads(config.STATUS_FILE.read_text(encoding="utf-8"))
            except ValueError:
                latest = None
        specs = {d.id: d for d in reg.datasets}
        rows = []
        for d in service.db.status():
            s = specs.get(d["id"])
            rows.append({**d, "kind": s.kind if s else None, "refresh_hours": s.refresh_hours if s else None,
                         "supports": list(s.supports) if s else [], "limitations": list(s.limitations) if s else [],
                         "state": service.retrieval.dataset_state(d["id"])["status"] if s else None})
        return {"datasets": rows, "latest_run": latest, "not_fetched": list(reg.not_fetched),
                "refreshing": bool(scheduler and scheduler.running), "live_data": config.LIVE_DATA,
                "scheduler_error": scheduler.last_error if scheduler else None}

    @app.get("/api/data/history/{dataset_id}")
    def history(dataset_id: str):
        return {"dataset_id": dataset_id, "runs": service.db.run_history(dataset_id, 20)}

    @app.get("/api/data/events")
    def events(dataset: str | None = None, limit: int = Query(300, ge=1, le=5000), run_id: str | None = None):
        log_dir = config.LOG_DIR / "ingest"
        files = sorted(log_dir.glob("*.jsonl")) if log_dir.exists() else []
        if run_id:
            files = [f for f in files if f.stem == run_id]
        if not files:
            return {"run_id": None, "events": []}
        f = files[-1]
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if dataset and e.get("dataset") not in (dataset, "run"):
                continue
            out.append(e)
        return {"run_id": f.stem, "events": out[-limit:], "runs": [p.stem for p in files[-10:]][::-1]}

    @app.get("/api/data/records/{dataset_id}")
    def records(dataset_id: str, q: str = "", limit: int = Query(50, ge=1, le=500)):
        if dataset_id not in reg.ids():
            return JSONResponse({"error": "unknown dataset"}, status_code=404)
        recs = service.db.records(dataset_id)
        if q:                       # browsing: simple contains-filter, plus the matcher's verdict for the same text
            res = service.retrieval.search(dataset_id, q, q)
            low = q.casefold()
            hits = [r for r in recs if low in " ".join([r["name"], r["name_ar"], *r["names"], *r["domains"]]).casefold()]
            return {"dataset_id": dataset_id, "query": q, "match": res["match"], "candidates": res["candidates"],
                    "total": len(hits), "records": hits[:limit]}
        return {"dataset_id": dataset_id, "total": len(recs), "records": recs[:limit]}

    @app.post("/api/data/refresh")
    def refresh(body: RefreshIn):
        if not scheduler:
            return JSONResponse({"error": "no scheduler"}, status_code=503)
        if not config.LIVE_DATA:
            return {"state": "live data is switched off (SG_LIVE_DATA=0)"}
        only = [i for i in (body.only or []) if i in reg.ids()] or reg.ids()
        return {"state": scheduler.refresh(only)}

    # ------------------------------------------------------------------ the React app
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        target = (dist / path).resolve()
        if path and target.is_file() and dist.resolve() in target.parents:
            return FileResponse(target)
        index = dist / "index.html"
        return FileResponse(index) if index.is_file() else HTMLResponse(NOT_BUILT)

    return app

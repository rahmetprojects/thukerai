"""
mcp_server.py - official UAE capital-market data for AI assistants (Model Context Protocol)
=========================================================================================

WHY
    Ask ChatGPT or Claude "is this firm licensed?" and the answer comes from whatever the web says, and web
    content can be planted (AI search poisoning). Connected to this server, the assistant answers from the
    regulator's own lists instead, with the record, the date and the official page.

WHAT IT EXPOSES (tools)
    check_entity    the Trust Report for any text: names, websites, @handles, phone numbers -> cited facts
    ask_data        a question in plain words -> the query plan, matching records with sources, and the API URL
    query_records   run an explicit plan (op, datasets, text, since, until, filters, limit)
    list_datasets   the catalogue: every dataset, what it means, how fresh our copy is
    get_dataset     one dataset's card: fields explained, profile, filters

HOW TO CONNECT
    Claude Desktop (local, stdio) - add to claude_desktop_config.json:
        {"mcpServers": {"uae-official-data": {"command": "python", "args": ["C:/path/to/senior_guide_v3/mcp_server.py"]}}}
    Over HTTP - the web server also answers MCP at POST http://127.0.0.1:8000/mcp (JSON responses). A remote
    assistant (for example ChatGPT developer mode) needs that URL exposed over HTTPS, e.g. through a tunnel.

DESIGN
    Dependency-free: the JSON-RPC protocol is ~100 lines here (initialize, ping, tools/list, tools/call), so it
    works with any client version and needs no SDK. Over stdio, stdout belongs to the protocol: every log line
    goes to stderr. The tools call the same services as the web API (service.py), so the answers are identical.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SUPPORTED = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = ("Official data from the UAE Capital Market Authority (CMA, formerly SCA) and other official sources, "
                "harvested, validated and versioned. Use check_entity whenever a user asks whether a company, website, "
                "social account or phone number is licensed, authorised, registered or warned against. Quote the facts "
                "with their dates and sources. Never say 'safe' or 'legitimate': not being on a list proves nothing.")

TOOLS = [
    {"name": "check_entity",
     "description": "Check names, websites, @handles or phone numbers (or a whole pasted message) against every official "
                    "list: CMA warnings, licensed companies (+ permitted activities, official website), authorised "
                    "finfluencers, funds, violations, and onboarded official sources. Returns cited facts, never verdicts.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description":
                     "What to check: a name, a URL, an @handle, a phone number or a pasted message"}}, "required": ["text"]}},
    {"name": "ask_data",
     "description": "Ask a question about the official datasets in plain words (e.g. 'warnings published this month', "
                    "'licensed companies allowed to do introduction', 'how many passported funds'). Returns the query plan, "
                    "matching records with sources, and the reusable API URL.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "query_records",
     "description": "Run an explicit query. op: list|count|latest|lookup|find_datasets|changes. datasets: ids from "
                    "list_datasets. filters: fields allowed by get_dataset. Dates are ISO (YYYY-MM-DD).",
     "inputSchema": {"type": "object", "properties": {
         "op": {"type": "string", "enum": ["list", "count", "latest", "lookup", "find_datasets", "changes"]},
         "datasets": {"type": "array", "items": {"type": "string"}}, "text": {"type": "string"},
         "since": {"type": "string"}, "until": {"type": "string"},
         "filters": {"type": "object", "additionalProperties": {"type": "string"}},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["op"]}},
    {"name": "list_datasets",
     "description": "The catalogue of official datasets available: id, name, description, rows, when our copy was fetched.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_dataset",
     "description": "One dataset's card: plain-language description, every field explained, data profile, allowed filters.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
]


class MCP:
    def __init__(self, service):
        self.s = service

    # ------------------------------------------------------------------ JSON-RPC
    def handle(self, msg: dict) -> dict | None:
        """One JSON-RPC message in, one response out (None for notifications)."""
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:                                         # notifications (initialized, cancelled ...)
            return None
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                result = {"protocolVersion": want if want in SUPPORTED else SUPPORTED[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "uae-official-data", "version": "1.0.0"},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call(params.get("name"), params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:                                  # never crash the connection
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}

    # ------------------------------------------------------------------ tools
    def call(self, name: str, args: dict) -> dict:
        from explore.planner import validate_plan
        if name == "check_entity":
            data = self.s.trust.check(str(args.get("text", ""))[:4000])
            summary = self._facts_text(data)
        elif name == "ask_data":
            data = self.s.explorer.ask(str(args.get("question", ""))[:500])
            summary = self._ask_text(data)
        elif name == "query_records":
            plan = validate_plan(args, self.s.retrieval.reg, self.s.explorer.catalog.filters)
            if not plan:
                return self._error("invalid query: unknown op, dataset id or filter field (see list_datasets / get_dataset)")
            data = self.s.explorer.run(plan)
            summary = self._ask_text(data)
        elif name == "list_datasets":
            data = {"datasets": [{"id": c["id"], "name": c["name"], "publisher": c["publisher"],
                                  "description": c["description"], "rows": c["state"]["rows"],
                                  "fetched_at": c["state"]["fetched_at"], "status": c["state"]["status"]}
                                 for c in self.s.explorer.catalog.cards()]}
            summary = "\n".join(f"- {d['id']}: {d['name']} ({d['rows']} rows, fetched {d['fetched_at']})" for d in data["datasets"])
        elif name == "get_dataset":
            try:
                data = self.s.explorer.catalog.card(str(args.get("id", "")))
            except KeyError:
                return self._error("unknown dataset id; call list_datasets")
            summary = f"{data['name']}: {data['description']}"
        else:
            return self._error(f"unknown tool {name}")
        return {"content": [{"type": "text", "text": summary + "\n\nFull data (JSON):\n" +
                             json.dumps(data, ensure_ascii=False, default=str)[:60000]}],
                "structuredContent": data, "isError": False}

    @staticmethod
    def _error(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}], "isError": True}

    @staticmethod
    def _facts_text(r: dict) -> str:
        lines = [r["headline"]["text"]]
        for f in r["facts"]:
            src = f" [source: {f['dataset']}, record {f['record_key']}, {f['source_page']}, our copy fetched " \
                  f"{f['fetched_at']}]" if f["dataset"] else ""
            lines.append(f"- ({f['tone']}) {f['text']}{src}")
        lines.append("Not proven: " + " ".join(r["not_proven"]))
        return "\n".join(lines)

    @staticmethod
    def _ask_text(r: dict) -> str:
        lines = [r["answer"], "Understood as: " + "; ".join(r["reading"]), f"API: {r['api']['url']}"]
        for x in r["records"][:20]:
            lines.append(f"- {x['name']} ({x['dataset']}, {x['date'] or 'no date'}) source: {x['source_page']}")
        if r.get("report"):
            lines.append(MCP._facts_text(r["report"]))
        return "\n".join(lines)


def main() -> None:
    """stdio transport: one JSON-RPC message per line on stdin, responses on stdout, logs on stderr."""
    from logsetup import setup_logging
    setup_logging(stream=sys.stderr)
    from service import build_service
    server = MCP(build_service())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (server.handle(m) for m in batch) if r]
            out = replies if isinstance(msg, list) else (replies[0] if replies else None)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

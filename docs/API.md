# API v1, MCP and the browser extension

Base address: `http://127.0.0.1:8000` (interactive docs at `/docs`). All read endpoints are public official data:
CORS is open for them, no cookies are used, nothing personal is stored.

## Check (the Trust Report)

```bash
curl -s 'http://127.0.0.1:8000/api/v1/check?q=market10.net'
curl -s -X POST http://127.0.0.1:8000/api/v1/check -H 'Content-Type: application/json' \
     -d '{"text": "Hi, I am from Faraz Financial Services, WhatsApp +971 50 123 4567"}'
curl -s -X POST http://127.0.0.1:8000/api/v1/check/image -H 'Content-Type: image/png' --data-binary @screenshot.png
```

Answer (shortened):
```json
{"headline": {"tone": "alert", "text": "The website market10.net is named in a CMA warning published 22 Oct 2026."},
 "facts": [{"tone": "alert", "text": "...", "detail": "The CMA advises investors against dealing with ...",
            "dataset_id": "cma_warnings", "dataset": "Warnings (unlicensed entities)", "record_key": "d959d295-...",
            "date": "2026-10-22", "source_page": "https://www.uaecma.gov.ae/en/open-data/warnings",
            "fetched_at": "2026-10-28T11:18:02+00:00", "match": "domain"}],
 "not_proven": ["Not appearing on a CMA list does not mean a company is safe or licensed.", "..."],
 "signals": [{"kind": "domain", "value": "market10.net"}], "lists_checked": 10, "took_ms": 4.8}
```
Tones: `alert` (warning, lookalike of a licensed firm's website, expired authorisation), `caution` (similar name,
published penalty), `record` (licensed, authorised, registered), `none` (nothing found; proves nothing).
Screenshots need an AI key (the AI only transcribes; code chooses what to look up).

## Find data

| Endpoint | What it returns |
|---|---|
| `GET /api/v1/ask?q=warnings this month` | the plan (`reading` in plain words), the answer, records with sources, the datasets, and `api` = the same question as a URL, a curl line and an MCP call |
| `GET /api/v1/query?op=list&datasets=cma_warnings&since=2026-10-01&f.category=...&limit=20` | run a plan directly; `format=csv` to download |
| `GET /api/v1/datasets` / `/datasets/{id}` | catalogue cards: description EN/AR, fields, profile, filters, health |
| `GET /api/v1/datasets/{id}/records?text=&since=&until=&f.<field>=&format=csv` | one dataset's records |
| `GET /api/v1/catalog.jsonld` | the catalogue as DCAT JSON-LD |
| `GET /api/v1/changes` | records added or removed since each dataset's previous copy |

Ops: `list`, `count`, `latest`, `lookup`, `find_datasets`, `changes`. Filters per dataset are listed in its card
(`filters`); anything else is rejected with HTTP 400, so a typo never silently returns everything.

## Onboarding

| Endpoint | |
|---|---|
| `POST /api/v1/onboard/discover {"url"}` | runs the detection ladder; returns steps, proposals (sample rows, column meanings, problems) and a `discovery` id |
| `POST /api/v1/onboard/approve {"discovery", "proposal", "overrides"}` | approves a proposal the server made (never a config sent by the browser), fetches it once, returns the outcome |
| `DELETE /api/v1/onboard/{id}` | removes an onboarded source |

## MCP (AI assistants)

Tools: `check_entity`, `ask_data`, `query_records`, `list_datasets`, `get_dataset`. Each returns a readable summary
with sources plus the full JSON (`structuredContent`).

* **Claude Desktop** (`claude_desktop_config.json`, then restart):
  `{"mcpServers": {"uae-official-data": {"command": "python", "args": ["C:/path/to/senior_guide_v3/mcp_server.py"]}}}`
  The server reads the same database as the web app (`data/senior_guide.sqlite3`), so run `python -m ingest.run` first.
* **Over HTTP:** `POST /mcp` with JSON-RPC (initialize, tools/list, tools/call). A remote assistant such as ChatGPT
  developer mode needs this URL exposed over HTTPS (a tunnel such as `cloudflared tunnel --url http://127.0.0.1:8000`).
  There is no authentication on it: expose it only for a demo.

## The browser extension (`extension/`)

Chrome -> `chrome://extensions` -> Developer mode -> Load unpacked -> `extension/`. On every https page it sends
the host name (or, on instagram / tiktok / x / youtube / t.me / facebook, the profile handle) to
`GET /api/v1/check` through its background worker, and shows ONE strip with the top fact, its list, date and a link
to the official page. Nothing found = nothing shown. Server address: the extension's Options page.

## The assistant (`api/app.py`, `kitchen/`)

| Call | What it does |
|---|---|
| `POST /api/assistant` `{session, text, lang?, mode: text|voice}` | one turn, streamed as Server-Sent Events: `order`, `recipe`, `step`..., `verify`, `card`, `done` (docs/ASSISTANT.md) |
| `POST /api/assistant/image?session=` (PNG/JPEG/WebP body) | a screenshot of an offer -> one fact card (reading the image needs the AI helper) |
| `POST /api/assistant/approve` `{session, discovery, proposal, overrides?}` | approve a source the assistant proposed in the chat; fetched by the normal pipeline, searchable at once |
| `POST /api/assistant/reset` `{session}` | forget the conversation (offers, last entity, money facts) |

The `session` is a random token (16 to 64 characters). Conversation memory is in RAM only and expires.


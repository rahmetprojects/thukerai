# BucketTreasure: official financial data that reaches the moment people decide

A trust layer over UAE official data, with **one assistant** in front of it. Ask by voice or text about any company,
website, message, fund, data question or money decision; a team of agents behind it (front agent, master agent,
specialists, explainer, verifier) answers from the Capital Market Authority's open data (CMA, formerly SCA) and
other official sources, **explains what the answer means**, offers to explain any financial term it used, and
numbers every fact with a link to the record it came from. The same data layer also serves a bank's payment
screen, a browser badge and AI assistants (MCP).

Built for TDRA Hackathon 2026, Challenge 8 (SCA): *Data provision for empowering public and investor
decision-making*. The answer to "how do we make this data discoverable and usable" is: stop waiting for people to
visit the data; deliver it, with its source, where the decision is made.

## Why it is different from asking ChatGPT

| | Ask ChatGPT / search | This |
|---|---|---|
| Where answers come from | whatever the web says; web content can be planted (AI search poisoning) | only allowlisted official sources, enforced in code (`config.OFFICIAL_DOMAINS`) |
| "Not found" means | it did not happen to find it | searched every record of every list, as of a stated date |
| Counting, listing a whole register | estimates | SQL over the full, versioned data |
| Proof | sometimes a link | every fact: list, record, publication date, official page, when our copy was fetched |
| Reuse | a chat transcript | an API call, an MCP tool, a CSV |

## Run it

```bash
pip install -r requirements.txt
python -m ingest.run          # fetch the official data (every request logged; summary at the end)
python run_server.py          # open http://127.0.0.1:8000
```

The screen is pre-built (`frontend/dist`); Node is only needed to change it (`cd frontend && npm install && npm run build`).
No AI key is needed. Optional `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`: screenshots in the Trust Report, AI planning of
messy data questions, better voice transcription, Arabic answers.

## The assistant (the main screen, `#ask`)

One input: hold the microphone or type. Each answer shows the agents' steps live, then a card: what the official
data says (cited), **what this means** (plain words), **Explain** chips for the jargon used, follow-ups, and a
numbered sources list (dataset, record, publication date, official page, when we collected it). By voice the answer
is spoken and ends with "Would you like me to explain …?" so "yes" works. How it works: `docs/ASSISTANT.md`.

## Other places the same data shows up

| Where | What it does | File |
|---|---|---|
| **Check** (`#check`) | paste a name, website, @account, phone number, message or screenshot -> official facts with sources | `frontend/src/views/Check.tsx`, `trust/` |
| **Bank demo** (`#bank`) | a payment screen that pauses a transfer to a warned payee, using one API call | `frontend/src/views/Bank.tsx` |
| **Browser badge** | a strip on top of any website or social profile with an official fact; silent otherwise | `extension/` |
| **AI assistants** | Claude / ChatGPT answer from CMA data through MCP instead of the open web | `mcp_server.py` |
| **Voice guide** (`#guide`) | the same facts by voice, English or Arabic, for someone who won't type | `agents/`, `frontend/src/views/Guide.tsx` |

`#check`, `#bank` and `#guide` are kept as direct links (not in the menu). **Under the hood** (`#data`): ask for data in plain words (the answer is a query you can reuse: API URL, curl, MCP
call, CSV), the catalogue (every dataset explained, also as DCAT JSON-LD), **add a source** with the onboarding agent,
and the health of every dataset.

## Set up the extras

* **Browser badge:** Chrome -> `chrome://extensions` -> Developer mode -> Load unpacked -> choose `extension/`.
  It talks to `http://127.0.0.1:8000` (change it in the extension's options).
* **Claude Desktop (MCP):** add to `claude_desktop_config.json`, then restart Claude Desktop:
  ```json
  {"mcpServers": {"uae-official-data": {"command": "python", "args": ["C:/path/to/senior_guide_v3/mcp_server.py"]}}}
  ```
  Over HTTP the same tools answer at `POST /mcp` (expose it over HTTPS, e.g. a tunnel, for remote assistants).
* **Onboarding headless browser (optional):** `pip install playwright && playwright install chromium` enables rung 3.

## Tests

```bash
python -m pytest              # 116 tests: assistant, pipeline failures, trust report, explorer, onboarding, API, MCP
python -m evals.run           # 42 end-to-end scenarios for the voice guide
cd frontend && npm test       # frontend unit tests
```

## Documentation

| File | What it explains |
|---|---|
| `docs/ASSISTANT.md` | the assistant: front agent, master, specialists, explainer, verifier, the stream, voice |
| `docs/ARCHITECTURE.md` | the layers, the trust rules, what each folder does |
| `docs/API.md` | API v1, MCP tools, the browser extension |
| `docs/ONBOARDING.md` | the onboarding agent: the detection ladder, approval, limits |
| `docs/DEMO.md` | the 3-minute demo, step by step, with backups |
| `docs/DATA_PIPELINE.md` | how data is fetched without a browser, validated, published and logged |
| `docs/CMA_API_NOTES.md` | the CMA portal's API as verified on the live site (and its quirks) |
| `docs/SCENARIOS.md` | the voice guide's 30 benchmark scenarios |
| `docs/LIMITATIONS.md` | what this cannot do and what has not been tested |

Every code file starts with a comment that says what it does.

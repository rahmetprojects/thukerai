# Architecture

One trusted layer of official data, one door, many touchpoints. In front: ONE assistant (`kitchen/`, docs/ASSISTANT.md)
that takes any question by voice or text and answers with cited, explained facts. The regulator would own the
middle two layers.

```
 OFFICIAL SOURCES           THE BUCKET                         ONE DOOR                 WHERE MONEY MOVES
 ----------------           ----------                         --------                 -----------------
 CMA open-data portal  -->  ingest/  fetch, validate,     -->  kitchen/  the assistant -> ONE SCREEN (#ask), voice + text
                            normalise, publish atomically      api/v1.py  Check API -->  Check page (#check)
 FCSC CPI (file)                                               DCAT, changes             Browser badge (extension/)
 Bayanat (CKAN), any   -->  onboard/ the onboarding agent      Query API, catalogue,     Bank payee check (#bank)
 official page              store/   SQLite: snapshots,        mcp_server.py  MCP   -->  AI assistants (Claude, ChatGPT)
                            records, FTS index, history        (same tools)              Voice guide (#guide, agents/)
                            trust/   the Trust Report
                            explore/ catalogue, planner, engine
```

## 1. The rules the design is built on

1. **Only official sources go in.** The onboarding agent refuses any host not on `config.OFFICIAL_DOMAINS`, before
   sending a request. This is what "it cannot be influenced" means in code: SEO, planted pages and a scammer's own
   "we are licensed" site never become data.
2. **Facts, never verdicts.** The Trust Report says "named in a CMA warning published 22 Oct 2026", never "scam" or
   "safe". Tones (alert, caution, record, none) express how strong the official match is, not a judgement.
3. **Every value is traceable:** dataset, record key, publication date, official page, when our copy was fetched,
   snapshot id. Every raw response is kept (`data/raw/`) and every run is logged (`logs/ingest/`).
4. **AI proposes, code decides.** The AI helper may transcribe a screenshot, plan a messy data question or propose
   a config for a new source. Matching, filtering, counting and publishing are code, and every AI output is
   validated against the catalogue (`explore/planner.validate_plan`) or approved by a person (`onboard/approve.py`).
5. **No partial data, ever.** Each refresh is validated (required fields, minimum rows, maximum drop, schema drift)
   and published in one transaction; a failed refresh keeps serving the last good copy and says why.
6. **Fast enough for a payment flow.** A check runs in ~5 to 50 ms on the test data (SQLite + FTS5 + in-memory
   caches per snapshot), so a bank can call it on every transfer.
7. **No personal data stored.** Checks are not logged against people; the browser badge sends only a host name or
   a handle; the bank demo sends only the payee's name.

## 2. The Trust Report (`trust/`)

```
pasted text / screenshot --(AI transcribes an image only)--> trust/signals.py --> signals
    names ("Hoxton FX"), domains ("market10.net"), handles ("@promoter12"), phone numbers ("971501234567")
signals --> trust/report.py --> for each signal, every official list:
    warnings (exact / probable)            -> alert / caution
    licensed companies (+ activities)      -> record; a LOOKALIKE of a licensed firm's website -> alert
    authorised finfluencers (handles too)  -> record; expired authorisation -> alert
    violations                             -> caution
    funds, auditors, organisers, appraisers -> record ("registered", not "licensed")
    sources added by the onboarding agent  -> record, labelled as onboarded
--> facts (two signals hitting one record = one fact), headline, what is NOT proven, coverage, timing
```

## 3. Finding data (`explore/`)

* `catalog.py`: one card per dataset from the registry's `catalog:` section (plain description in English and
  Arabic, keywords, what the date means, field definitions) plus the live state and a profile of the data itself.
  Exported as DCAT JSON-LD, the open-data portal standard.
* `planner.py`: a question becomes a **plan** `{op, datasets, text, since, until, filters, limit}`. Rules first
  (dataset keywords, date phrases, counting words, activity names, domiciles, entity names): about 1 ms. The AI
  planner runs only when the rules place no dataset, and its plan is accepted only if every field is valid.
* `engine.py`: runs the plan on the current snapshots (records cached per snapshot, text through FTS5) and returns
  the records with their sources, the plan in plain words, and the same question as code (API URL, curl, MCP call).

Why not RAG / a vector database: the data is registers of names and dates. Questions are exact look-ups,
filters, counts and "what changed", which need complete, versioned, structured data. Retrieval over text would be
right for CMA circulars and reports in PDF (listed by the onboarding agent as documents, not yet indexed).

## 4. The onboarding agent (`onboard/`, details in docs/ONBOARDING.md)

An official URL -> the detection ladder (CKAN API, data file or JSON API, JSON calls the page makes, file links,
HTML tables, PDFs) -> a trial through the same parser and normaliser the scheduled runs use -> a proposed registry
entry with column meanings -> a person approves (and may correct) -> the entry is written to the LOCAL registry
(`data/registry.local.yaml`, never the curated `ingest/registry.yaml`) and fetched by the normal pipeline -> the
source is immediately in the Trust Report, the explorer, the API and MCP. One generic adapter
(`ingest/adapters/remote_table.py`) serves every onboarded source: adapters exist per pattern, not per website.

## 5. The data pipeline (details in docs/DATA_PIPELINE.md)

```
 registry.yaml (+ registry.local.yaml) --> pipeline.py --> adapter.fetch() --> raw_store --> responses.expect_json()
     --> paging rules --> validate.py --> schemas.py --> db.publish() (one transaction, + FTS index for that dataset)
 every step --> runlog.py --> logs/ingest/<run>.jsonl + summary.txt + data/status/latest_run.json --> Health page
```
Outcomes per dataset per run: SUCCESS, UNCHANGED, EMPTY, FAILED, SCHEMA_DRIFT, SKIPPED.

## 6. The voice guide (`agents/`)

A bounded conversation over the same data, for older people who will not type. Each turn: hearing repair
(`agents/hearing.py`) -> guardian (`guardian/`: secrets redacted, live-scam signs stop the turn with a fixed safety
script) -> facts and intent (`agents/slots.py`, `agents/intents.py`) -> ask ONE missing fact, or run one workflow
(`agents/workflows.py`, calculators in `tools/calculators.py`, official data through `tools/retrieval.py`) ->
template answer (optional AI rephrase) -> validator (`agents/validator.py`: every number and claim checked) ->
answer with "How I worked this out". 42 scenarios in `evals/` (30 benchmark + 12 multi-turn and safety).

## 7. The assistant (`kitchen/`, details in docs/ASSISTANT.md)

Guardian -> front agent (what kind of order) -> master agent (a recipe of at most 8 steps; independent steps in
parallel; each streamed) -> specialists (the modules above: Trust Report, explorer, calculators, the voice guide's
coordinator, onboarding) -> explainer (what it means; offers for the jargon used; explanations linked to datasets)
-> composer (templates over evidence, numbered citations) -> verifier (cited, traceable numbers, no verdicts).
Nothing new is trusted: the assistant only routes to the tested specialists and checks what they return.

## 8. Folder by folder

| Folder / file | What it does |
|---|---|
| `run_server.py` | starts everything: wiring, background data refresh, web server (API, MCP over HTTP, screens) |
| `service.py` | wires database, retrieval, Trust Report, explorer, conversation, optional AI (`reload()` after an approval) |
| `config.py` | every setting, including the official-domain allowlist and the local registry path |
| `mcp_server.py` | Model Context Protocol server (stdio; also mounted at `POST /mcp`), dependency-free |
| **`kitchen/`** | the assistant: front agent, master agent, glossary + explainer, composer, verifier, the streamed turn |
| **`trust/`** | the Trust Report: signal extraction, cited facts, lookalike-domain detection |
| **`explore/`** | the catalogue (+ DCAT), the question planner, the query engine |
| **`onboard/`** | the onboarding agent: safe fetcher, detection ladder, column inference, approval |
| **`ingest/`** | the data pipeline: registry, browserless CMA session, adapters (incl. the generic `remote_table`), raw store, validation, normalisers, publish, run logs, scheduler |
| **`store/`** | SQLite: snapshots, records, entity index, FTS5 full-text index, run history; name normalisation and matching |
| **`tools/`** | typed official-data retrieval, official inflation, 24 calculators (voice guide) |
| **`guardian/`**, **`agents/`** | the voice guide: safety, understanding, coordinator, workflows, validator, optional AI |
| `api/` | `app.py` (server, middleware, the assistant's stream, the voice guide's endpoints, health), `v1.py` (the public API and onboarding) |
| `extension/` | Chrome extension: the browser badge |
| `frontend/` | React screens (`src/views/`: Assistant (main), Explore, Check, Bank, Guide, DataStatus; `components/AnswerCard.tsx`) and the built copy (`dist/`) |
| `evals/`, `tests/` | 42 voice-guide scenarios; 116 tests with a fake CMA site and fake official open-data sites |
| `data/`, `logs/` | reference CSV; at run time the database, raw files, status, local registry; logs |

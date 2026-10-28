# The data pipeline: what is fetched, how, and how to see exactly what happened

## 1. Run it

```bash
python -m ingest.run                          # fetch every dataset (about 2 minutes, mostly polite pauses)
python -m ingest.run --only cma_warnings      # one dataset
python -m ingest.run --list                   # what is in the registry (and what is deliberately not fetched)
python -m ingest.report                       # what the assistant is using RIGHT NOW (no network)
python -m ingest.report cma_warnings          # the last runs of one dataset, with reasons and notes
python -m ingest.run --from-raw <run_id>      # rebuild from saved raw files, no network
```

The server also refreshes datasets in the background (only those older than their `refresh_hours`), and the
**Data** page in the app shows the same information with a "Refresh now" button.

## 2. No browser needed: how the CMA API is called

Found by replaying the live page's own requests on 2026-10-28 (evidence: `docs/CMA_API_NOTES.md`).

```
1. GET  https://www.uaecma.gov.ae/en/open-data/warnings
        <- Set-Cookie: <session cookie, HttpOnly>
        <- HTML containing  <input type="hidden" name="__RequestVerificationToken" value="anonymous:...">
2. POST https://www.uaecma.gov.ae/api/PublicApi/GetContentList      (or /GeIntegrationResult)
        RequestVerificationToken: <token from step 1>   required, else 403 {"error":"token_invalid"}
        languageId: 1   languageCode: en-GB             required, else HTTP 200 with {"code":500,...} inside
        Content-Type: application/json
        Cookie: <session cookie from step 1>            required (the token is bound to the session)
```

`ingest/http_client.py` does exactly this with plain HTTP (`httpx`). If the token is rejected mid-run (an
expired session), it re-opens the session once and retries. If the page ever stops containing the token,
setting `SG_ALLOW_BROWSER=1` lets a headless browser obtain the cookie + token only; all data calls stay plain HTTP.

## 3. The datasets (ingest/registry.yaml)

| id | pillar | endpoint | notes |
|---|---|---|---|
| `cma_warnings` | scams | GetContentList typeId 1071 | English + Arabic items paired by `groupId`; titles list several entities |
| `cma_finfluencers` | scams | GeIntegrationResult 2056 | the site itself requests everything in one call |
| `cma_violations` | scams | GetContentList 1039 | individuals are anonymised as "Investor" (not searchable) |
| `cma_licensed_companies` | decisions | GeIntegrationResult 2045 | paged; Arabic names fetched in a second pass and joined by licence code |
| `cma_licensed_activities` | decisions | 2047 then 2045 per activity | which firm may do which activity (~55 calls) |
| `cma_local_funds` / `cma_passported_funds` / `cma_eosg_funds` | decisions / knowledge | GetContentList 1036 / 1037 / 1076 | |
| `cma_auditors` / `cma_assembly_organizers` | decisions | GetContentList 1045 / 1070 | registration, not licensing |
| `cma_appraisers` | decisions | static HTML table | |
| `cma_publications` | knowledge | GetContentList 16 | reports and data files with download links |
| `fcsc_cpi_annual` | knowledge | local CSV (`data/reference/`) | goes through the same pipeline for provenance |

Not fetched, on purpose (shown on the Data page): Virtual Asset Service Providers (the CMA links to VARA's own
register), Graphs and Statistics (interactive only), Investor Portal (a menu), CMA Budget (not relevant).

## 4. The stages and the rules

| Stage | File | Rule |
|---|---|---|
| fetch | `adapters/*.py`, `http_client.py` | timeouts; retries only for network/429/5xx with backoff + jitter; a request budget per run; 0.6 s between pages |
| raw | `raw_store.py` | every response saved byte-for-byte **before** parsing, with url, status, sha256, size, time |
| classify | `responses.py` | the body decides, not the status code: HTML, invalid JSON, `token_invalid`, `code:500` inside 200 are all errors |
| extract | `adapters/*.extract()` | the exact envelope path for that endpoint (`data.contents`, `data.items`); anything else = schema drift. No "longest list" guessing |
| paginate | adapters | stop at the reported last page; an empty page *before* it is a gap (error); the same page twice is an error; the unique count must reach the reported total, or the run fails as **incomplete** |
| validate | `validate.py` | required fields; zero records = EMPTY (never published); below `min_rows` = FAILED; a drop larger than `max_drop_pct` vs the current copy = FAILED |
| normalise | `schemas.py` | one canonical record per dataset item: names (all entities, English + Arabic), web domains, dates, details |
| publish | `store/db.py` | identical content hash = UNCHANGED; otherwise a new snapshot made current in ONE transaction; 3 snapshots kept |

## 5. Reading the logs: "what was fetched, which was empty, which failed"

**At a glance**: `logs/ingest/latest.summary.txt` (also printed at the end of every run). Illustrative example:

```
INGEST RUN 20261028T091500Z   started 2026-10-28T09:15:00+00:00   took 96.4 s
counts: SUCCESS=11, UNCHANGED=1, FAILED=1

dataset                   status           rows  pages  reqs      KB   secs  snapshot served
--------------------------------------------------------------------------------------------
cma_warnings              SUCCESS           228      5     6     502    4.1  cma_warnings@20261028T091500Z
cma_licensed_companies    SUCCESS           333      8     8      83    5.2  cma_licensed_companies@20261028T091500Z
cma_violations            FAILED              0      1     1       0    0.4  cma_violations@20261027T091200Z
...
* cma_violations [FAILED]: html_not_json: HTTP 200: expected JSON but got a web page
      note: still serving the previous snapshot cma_violations@20261027T091200Z (10 records)
      raw responses: data/raw/20261028T091500Z/cma_violations
```

**Every event**: `logs/ingest/<run_id>.jsonl`, one JSON object per line ("rows" in the CMA's totals are API items: each warning is one English and one Arabic item), e.g.

```json
{"t":"...","dataset":"cma_warnings","stage":"token","msg":"session opened; token found in hidden input","token_chars":123,"cookies_set":["ASP.NET_SessionId"]}
{"t":"...","dataset":"cma_warnings","stage":"http","msg":"POST /api/PublicApi/GetContentList -> HTTP 200","bytes":120311,"seconds":0.84,"purpose":"page 1"}
{"t":"...","dataset":"cma_warnings","stage":"paging","msg":"source reports 455 rows in 5 pages","page_size":100}
{"t":"...","dataset":"cma_warnings","stage":"page","msg":"page 1: 100 items (100 new)","duplicates":0,"languages":[1,2],"sha256":"0a9172bebd7f"}
{"t":"...","dataset":"cma_warnings","stage":"reconcile","msg":"collected 455 unique items over 5 page(s)","reported":455}
{"t":"...","dataset":"cma_warnings","stage":"normalise","msg":"455 raw items -> 228 records","with_both_languages":227,"arabic_only":1}
{"t":"...","dataset":"cma_warnings","stage":"outcome","msg":"SUCCESS: published 228 records (was 226)"}
```

Stages you will see: `start`, `http` (every attempt, including retries), `token`, `paging`, `page`,
`reconcile`, `normalise`, `dedupe`, `arabic`, `activities`, `sample` (start of a bad answer), `bug`, `outcome`.

**The raw bytes**: `data/raw/<run_id>/<dataset>/page_001.body` + `page_001.meta.json`; a failing answer is also
saved as `FAILED_<n>.body`.

**In the app**: the Data page (top-right button) shows each dataset's last outcome, records in use, fetch dates,
the reason and notes, and the full event log of the newest run (filterable by dataset).

**Never logged**: cookie values, the token, API keys, or anything a person said.

## 6. When something fails (runbook)

| Outcome / reason | What it means | What to do |
|---|---|---|
| `token_invalid` on every call | the site changed how the token is issued | open the page, check the hidden input still exists; try `SG_ALLOW_BROWSER=1` |
| `html_not_json` | an error, maintenance or anti-bot page came back | open the saved `FAILED_*.body`; retry later; if it says "captcha", stop automated access |
| `api_error` (code 500 inside 200) | a required header or parameter is missing/changed | compare the page's own request (browser dev tools) with `adapters/` |
| `SCHEMA_DRIFT` | valid JSON in a new shape | look at the sample in the log; update the adapter's `extract()` and add a fixture + test |
| `pagination` / `incomplete` | gap, repeat, or fewer rows than the source reported | rerun; if it persists the site's paging changed |
| `EMPTY` | the source answered with zero records | nothing is published; check the website shows data |
| `FAILED: row count fell from X to Y` | the new copy is suspiciously smaller | check the website; if the drop is real, raise `max_drop_pct` for that dataset |

## 7. Adding a dataset

1. Open the CMA page in a browser, find its own API call (dev tools → Network), note the endpoint and body.
2. Add an entry to `ingest/registry.yaml` (kind, request, schema, record key, required fields, supports, limitations).
3. If its shape is new, add a normaliser to `ingest/schemas.py` (and an adapter if the endpoint is new).
4. Save a trimmed real response in `tests/fixtures/cma/` and add a test. Run `python -m ingest.run --only <id>`.

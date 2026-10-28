# The onboarding agent: an official URL in, a maintained data feed out

## Why

Every new government dataset used to mean a hand-written scraper (the CMA portal alone needed a session cookie,
an anti-forgery token, language headers and four undocumented quirks: `docs/CMA_API_NOTES.md`). That does not
scale to Bayanat, ADX, DFM, the Central Bank and every emirate's portal. The agent writes the configuration once;
plain code runs it on every refresh, with the same quality gates as the CMA datasets.

## The ladder (`onboard/discover.py`)

The agent tries each rung in order and reports every one (found / none / skipped / error, with the reason):

| Rung | Looks for | Example |
|---|---|---|
| 1 | a CKAN open-data API: `status_show`, then `package_show` for the dataset's CSV / XLSX / JSON resources | bayanat.ae runs CKAN 2.9 |
| 2 | the URL itself is a data file or a JSON API (the list of records is located once, then FIXED in the config; paging such as `?page=N` is detected) | `https://api.example.gov.ae/v1/firms` |
| 3 | the JSON calls the page makes, recorded in a headless browser and replayed without it (optional: Playwright) | single-page portals |
| 4 | CSV / Excel / JSON links on the page | "Download the list (CSV)" |
| 5 | HTML tables on the page | a static register |
| 6 | PDF links: listed as documents for future document search, not proposed as tables | annual reports |

Each candidate gets a **trial** through `ingest/adapters/remote_table.parse` and `ingest/schemas.generic`, the same
code the scheduled runs use, so the sample a person approves is exactly what will be served.

## Column meanings (`onboard/infer.py`)

name, Arabic name, date, status, category, website, key: from headers first (English and Arabic), then from the
values (dates that parse, domains, Arabic script, distinct values). The person reviewing can change any of them.

## Approval (`onboard/approve.py`)

1. The entry is validated by the registry's own parser; the URL must be on an official domain.
2. It is appended to `data/registry.local.yaml`. The curated `ingest/registry.yaml` is never written by software.
3. The normal pipeline fetches it once (raw files, run log, validation, atomic publish).
4. It is searchable at once: Trust Report, explorer, catalogue, DCAT, API, MCP.

## Limits (honest)

* It does not get past logins, CAPTCHAs or terms of use, and never tries to.
* A JSON call that only works inside the page's session (tokens bound to cookies, as on the CMA portal) is flagged:
  it needs a custom adapter like `ingest/http_client.py`.
* Column meanings are proposed by heuristics and approved by a person, not confirmed by the publisher; the
  dataset's limitations say so.
* Only official domains are accepted (`SG_OFFICIAL_DOMAINS` to change the list).
* Tested against fake CKAN, page and API sites (`tests/fake_gov.py`); not yet run against the live Bayanat portal
  from this build machine (its network blocks it). Try it on your network with a Bayanat dataset URL.

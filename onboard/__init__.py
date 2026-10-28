"""
onboard/ - the onboarding agent: an official URL in, a maintained, validated data feed out
=========================================================================================

    fetcher.py   a small, safe HTTP getter (official domains only, size and time limits, injectable transport)
    infer.py     reads a table and proposes which column is the name, date, status, category, website and key
    discover.py  the detection LADDER (stops at the first rung that works):
                   1. CKAN open-data API (Bayanat runs CKAN)
                   2. the URL itself is a JSON API or a data file
                   3. JSON calls the page makes in a headless browser (optional: needs Playwright)
                   4. CSV / Excel / JSON file links on the page
                   5. HTML tables on the page
                   6. PDFs (reported as documents, not proposed as tables)
                 and, for each candidate, a TRIAL FETCH through the same parser the scheduled runs use
    approve.py   writes an approved proposal to the local registry (never the curated one) and runs it once

THE PRINCIPLE
    The agent writes a CONFIG once (a registry entry: where the table is, how to read it, which column is what);
    plain code runs that config on every refresh, with the same quality gates as the CMA datasets. A person
    approves every new source. If the site changes, the run fails with schema_drift, the last good copy keeps
    serving, and discovery can be re-run to propose a fix.
"""

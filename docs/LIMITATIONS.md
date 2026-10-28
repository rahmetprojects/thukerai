# Limitations: what this cannot do, and what has not been tested

## What was tested, and how

| Area | Status |
|---|---|
| CMA API protocol (token, cookie, headers, envelopes, paging, quirks) | **Verified on the live site** on 2026-10-28 from a real browser session (docs/CMA_API_NOTES.md) |
| The Python client calling the live site with plain HTTP | **Not run from here**: the build machine's network blocks uaecma.gov.ae. It follows the verified protocol exactly and is tested against a faithful imitation (`tests/fake_cma.py`). Run `python -m ingest.run` once on your network and check `logs/ingest/latest.summary.txt` |
| Pipeline failure handling, calculators, matching, guardian, validator, coordinator, API | 116 automated tests pass (`python -m pytest`) |
| The assistant (`kitchen/`) | 26 tests: routing, citations, explanations, verifier, onboarding by chat, the stream. Screen and voice loop checked in headless Chromium with a **simulated** recogniser and voice (the spoken text was captured and checked); not yet with a real microphone. The front agent's rules were tuned on our own example questions; real people will phrase things we did not anticipate (the AI helper covers some of that, untested live) |
| The glossary (`kitchen/glossary.py`) | 28 plain-language explanations written for this prototype; **not reviewed by the CMA**. Arabic explanations exist for 3 terms only; the assistant speaks English |
| Trust Report, explorer, API v1, MCP (HTTP and stdio) | Tested on the fake CMA site built from real captured responses; checks take ~5 to 50 ms there. Timings on the full live data are not measured yet |
| Onboarding agent | Tested on fake CKAN, page and API sites (`tests/fake_gov.py`); **not run against live Bayanat / ADX / DFM** from this build machine |
| Browser extension | Loaded in Chromium and checked on simulated pages (warning, lookalike, finfluencer, ordinary site); not tested on the real Instagram / TikTok pages, whose URLs and layouts change |
| The 30 benchmark scenarios + 12 conversation, protection and judging cases | 42/42 pass end to end, offline (`python -m evals.run`) |
| AI helper (Gemini / Claude) | **Not run against the live services** in this build; the app falls back to templates if a call fails |
| Voice recognition | Browser recognition mishears UAE money words (real example: "120,000 dhs ... retire" -> "100,000 diplomats ... swim all the time"). Mitigated by guess re-ranking, currency-slip repair, read-back and "Fix what I heard"; optional Gemini transcription **not yet run against the live service** |
| Screen | Checked in headless Chromium at phone and desktop sizes; **not** with a real microphone, real voices, a screen reader, or real older users |
| Arabic | Guardian scripts, questions and screen text exist but **have not been reviewed by a native speaker**; offline answers are in English with an Arabic note (full Arabic answers need the AI helper) |

## Trust-layer limits

1. **Lookalike websites** are detected when a domain contains a licensed firm's own domain name ("4tglobal-invest.net"
   vs "4tglobal.ae"). Other tricks (swapped letters, other alphabets) are not detected yet.
2. **Handles and phone numbers** are matched only where the official lists contain them (the finfluencer list does;
   warnings mostly name companies and websites). An unmatched handle proves nothing.
3. **Screenshots** need an AI key; the AI only transcribes, but a mis-read character changes what is looked up.
4. **MCP over HTTP has no authentication**: expose `/mcp` beyond this computer only for a demo.
5. **The bank screen is a demo**; no bank has integrated the API.
6. **ADX market data** (its own MCP server, paid plans) is not connected; the architecture treats it as one more
   source to add, it is not built.
7. **Documents (PDF)** are listed by the onboarding agent but not yet searchable.

## Protection limits

1. **"Not on a CMA list" never means safe.** The CMA covers onshore capital markets only. Banks and insurers
   (Central Bank), DIFC firms (DFSA), ADGM firms (FSRA) and virtual-asset firms (VARA) are not in these lists.
2. **A name match is not identity.** Scammers copy real firms' names and sites; the assistant says so and compares
   websites where it can, but it cannot confirm who is actually calling.
3. **Pattern-based safety.** Scam signs and secrets are detected by fixed patterns; unusual wording, dialect or a
   speech-recognition error can slip past. A wrong alarm is the accepted, safer error.
4. **No human escalation.** It cannot alert a family member, bank or police; it tells the person what to do.

## Data limits

1. **No live market data**: no stock, gold or property prices, no live exchange rates or bank/central-bank rates.
   The answers that would need them say so and calculate with the person's own figures instead.
2. **Inflation is annual and national** (FCSC CPI shipped as `data/reference/fcsc_cpi_annual.csv`, 2010-2025).
   Replace the file with a newer FCSC download to update it. Category codes (ALL, HOU, MED ...) are inferred.
3. **No official data on pensions, government benefits, deposit protection limits or insurance rules**: the
   assistant explains the questions to ask and points to official sources; it never invents an amount or eligibility.
4. **Data is as fresh as the last successful run** (warnings every 12 h, most lists daily). Every check shows the date
   our copy was fetched; stale or unavailable data is announced in the answer.

## Privacy and security limits

1. Browser speech recognition sends audio to Google (Chrome) or Microsoft (Edge); only the TEXT is redacted here.
2. Names, addresses and e-mails are not detected by redaction; they would reach an AI provider if one is configured.
3. No login, no HTTPS, no rate limiting: the server is meant for **this computer only** (`127.0.0.1`).
4. Facts are kept in memory for one conversation only and are not encrypted; nothing personal is written to disk or logs.

## Not advice

This is an educational protection tool, not a licensed financial adviser. It explains, calculates on the person's
own numbers and checks official lists; it never recommends a product, a trade or an amount to invest.
Whether any of its output counts as regulated financial advice in the UAE should be confirmed with a lawyer before
real-world use.

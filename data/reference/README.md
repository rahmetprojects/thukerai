# data/reference - files shipped with the app

| file | what it is | how to update |
|---|---|---|
| `fcsc_cpi_annual.csv` | UAE consumer price index, annual, 2010-2025, from the Federal Competitiveness and Statistics Centre (FCSC). Two index bases (2014=100 and 2021=100) chained by `tools/inflation.py` | download a newer annual CPI CSV from the FCSC open-data portal, save it over this file, run `python -m ingest.run --only fcsc_cpi_annual` |

At run time the app also creates, next to this folder: `senior_guide.sqlite3` (the database), `raw/` (every raw
response, per run) and `status/latest_run.json` (the last ingest run's summary).

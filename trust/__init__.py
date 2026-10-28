"""
trust/ - the Trust Report: paste anything, get official facts with sources
=========================================================================

    signals.py   finds what to look up in free text: company names, websites, social handles, phone numbers
    report.py    looks every signal up in every official list and returns FACTS (never verdicts), each with
                 its dataset, record, date, official page and the time our copy was fetched

The same report is served to every touchpoint: the Trust Report page, the bank payee check, the browser
badge, the MCP server for AI assistants and the senior voice guide. See docs/ARCHITECTURE.md.
"""

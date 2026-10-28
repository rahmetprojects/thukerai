"""
explore/ - find official data by asking for it: catalogue, question -> query, query -> cited records
====================================================================================================

    catalog.py   one card per dataset (plain description EN/AR, fields explained, live state, profile) and the
                 standard DCAT JSON-LD export of the whole catalogue
    planner.py   a question -> a validated query PLAN (rules first, in milliseconds; the AI planner only for
                 questions the rules cannot place, and only after every field is checked against the catalogue)
    engine.py    runs a plan against the published snapshots and returns records with their sources, the
                 plain-words reading of the question, and the exact API call that reproduces the answer

Not a chatbot: a question returns a QUERY you can see, edit and reuse (filters, an API URL, an MCP call).
"""

# The 3-minute demo

One assistant, one data layer, a few doors. Before going on stage: `python -m ingest.run` on the venue's network,
`python run_server.py`, the extension loaded, Claude Desktop connected to `mcp_server.py`, and a backup recording
of every stop.

Pick the demo firm from the REAL warnings list the morning of the demo (Check page -> type a recent name, or
`GET /api/v1/query?op=latest&datasets=cma_warnings&limit=5`). Never present a real firm that has not been warned as
suspicious. For the "not on any list" stop, use a clearly made-up name and check beforehand that it returns nothing.

| Time | Stop | What you do | What they see |
|---|---|---|---|
| 0:00 | Hook | Headline: Dubai retiree loses Dh400,000 to an investment group found on social media (Khaleej Times, 22 Oct 2026). "The CMA has hundreds of active warnings. How many people see that page before they send money?" | the problem in one sentence |
| 0:25 | Ask by voice | Assistant: hold the mic, "Is market10.net licensed?" | the agents' steps tick by (reader, lookup, explainer, ~20 ms); a red card: named in a CMA warning, date [1]; **what this means**; it says aloud "Would you like me to explain what 'CMA warning' means?" |
| 0:50 | "Yes" | hold the mic, "yes" | the explanation, cited to the glossary, and *where this appears in the official data*: the warnings list, 233 records, its official page |
| 1:10 | The message | paste the WhatsApp text: "BitWealth Pro Capital promised me 15% a month" | not on any list (and why that proves nothing), 15% a month = 435% a year, "the promise itself is the warning sign", Explain: compounding |
| 1:35 | The data | "How many passported funds are there?" then click a [n] | 144, each row numbered to its record; Download CSV; As code (the API URL, curl and MCP call) |
| 1:55 | Another door | Bank demo (`#bank`) or the browser badge on the firm's site, same API | the same fact where the money moves |
| 2:15 | Add a source | "Add this dataset https://<official open-data page>" in the chat, then Approve | the onboarding agent's ladder, a proposal, approved, then found by the next question |
| 2:40 | Ask | "This is what CMA would own: one trusted layer every bank, app and AI can use, with one assistant anyone can talk to. We ask for a pilot with CMA's data team and one bank's payment flow." | end on the person, not the API |

## Backup

* Every stop recorded as a video.
* No network at the venue: the app serves the last good copy; the Health page says when it was fetched.
* Extension not loading: show the same strip from the Check page (same card, same API).
* MCP not connecting: `POST /mcp` with curl shows the same answer.

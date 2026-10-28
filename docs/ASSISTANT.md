# The assistant: one front, a kitchen of agents behind it

The person sees **one assistant** (`#ask`, `frontend/src/views/Assistant.tsx`). They type or hold the microphone
and ask anything: a company, a website, a pasted message, a data question, a financial term, a money decision.
Behind it, a small team of agents works like a bar: the front agent takes the order, the master agent writes the
recipe, the specialists fetch the ingredients from the official data, the explainer says what it means, and the
verifier checks every number and source before anything is served. Code: `kitchen/`.

```
 person (voice or text)
   │
   ▼
 Guardian ──── live scam / money already sent ──► SAFETY card (fixed script, nothing else runs)
   │
 FRONT AGENT  kitchen/front.py      what kind of order is this? rules first; AI only if rules can't say (validated)
   │          check · followup · data · explain · money · onboard · welcome · ack · unknown
   ▼
 MASTER       kitchen/master.py     the recipe: at most 8 steps, each naming its specialist; independent steps
   │                                run in parallel; each step is streamed to the screen as it finishes
   ├─ reader       trust/signals.py        names, websites, @handles, phone numbers in the message
   ├─ lookup       trust/report.py         every official list, lookalike websites -> Trust Report
   ├─ calculator   tools/calculators.py    "15% a month" -> 435% a year (in parallel with the look-up)
   ├─ query        explore/engine.py       a validated query plan over the records (count, list, latest, changes)
   ├─ catalogue    explore/catalog.py      which datasets answer this
   ├─ guide        agents/coordinator.py   multi-turn money questions (asks one fact at a time, then calculates)
   └─ onboarding   onboard/discover.py     "add this official page" -> a proposal; nothing saved until approved
   ▼
 EXPLAINER    kitchen/explainer.py  WHAT THIS MEANS (one plain line) + up to 3 "Explain" offers for the jargon the
   │                                answer used + explanation cards linked to the datasets where the term appears
 COMPOSER     kitchen/composer.py   the card, built by templates from evidence only; numbered citations
   ▼
 VERIFIER     kitchen/verifier.py   every statement cited · every number in the evidence · no verdict words
   │                                (a failing sentence is removed, never served; the card says it was trimmed)
   ▼
 card + speech  ─────────────────►  screen (sources panel, explain chips, follow-ups) and voice (≤ 3 sentences)
```

## The explanation layer (why "don't just serve the data")

Every answer has three parts the person can tell apart:

| Part | Example | Cited to |
|---|---|---|
| What the data says | "The website market10.net is named in a CMA warning published 22 Oct 2026." [1] | the **record** (dataset, record key, publication date, official page, when we collected it) |
| What this means | "This means the regulator has told investors not to deal with this entity. Do not send money…" [2] | the **glossary** (marked "Explanation · not an official record") |
| Explain offers | `? CMA warning` `? guaranteed return` | — (asking is a new turn) |

* **Offers, not lectures.** The explainer finds the financial terms the answer used (`kitchen/glossary.py`,
  28 terms: CMA warning, passported fund, NAV, sukuk, flat rate, compounding, …) and offers at most three. By voice,
  the spoken answer ends with "Would you like me to explain what 'CMA warning' means?", so **"yes"** is the natural
  next turn; "no" is acknowledged and dropped. A term explained once is not offered again in that conversation.
* **Linked to the data.** An explanation card ends with *where this appears in the official data*: the dataset,
  how many records it has, when we collected it, and its official page, with a follow-up "Show me the …".
* **Never improvised.** A term not in the glossary gets "I don't have an explanation for that term yet", not a guess.
  The glossary is reviewed text, kept separate from records so a reader always knows which is which.
* **Promises are worked out.** "15% a month" in a message is turned into its yearly figure by the calculator, and
  the meaning line says the promise itself is the warning sign, even when the firm is on no list.

## The stream (`POST /api/assistant`, Server-Sent Events)

```
event: order    {"kind": "check", "decided_by": "rules", "plan": null, ...}
event: recipe   {"steps": [{"id": "read", "agent": "reader", "label": "..."}, ...]}
event: step     {"id": "lookup", "status": "running"}
event: step     {"id": "lookup", "status": "done", "detail": "10 lists, 1 matching record(s)", "ms": 18.2}
event: verify   {"ok": true, "problems": []}
event: card     {"type": "fact", "tone": "alert", "headline": "...", "citations": [...], "speech": "...", ...}
event: done     {"took_ms": 21.4}
```

The ticker under each question shows these steps live; when done it folds into one line
("3 steps · reader, lookup, explainer · 21 ms · show work").

## Voice

* **In:** hold the microphone (or Space on it). With a Gemini key the recording is transcribed on the server
  (`/api/transcribe`, better with UAE accents and money words); otherwise the browser's recogniser, which keeps the
  most money-like of five guesses (`frontend/src/lib/speech.ts`).
* **Out:** a question asked by voice is answered by voice with the card's `speech` (headline, the meaning, one
  explain offer). If nothing is ready after 1.5 s, a short spoken cue says the data is being checked.
* **Barge-in:** starting to talk stops the voice at once.

## Why rules first, and where the AI is allowed

The order kinds are few and well typed, so rules are faster (no model round trip), testable, and cannot wander.
The AI helper, when configured, may (1) classify an order the rules could not place, accepted only if it names one
of the known kinds, and (2) propose a query plan, accepted only if every dataset, field and filter exists
(`explore/planner.validate_plan`). It never writes a card: cards are templates over evidence, and the verifier
checks them.

## Tests

`tests/test_assistant.py` (26 tests): routing of every order kind; every card fully cited; the "yes" explanation
flow with linked datasets; "no thanks"; unknown terms not improvised; the promised-return calculation in parallel;
record-level citations on data answers; follow-ups; the money conversation and breaking out of it; safety first;
the verifier catching a planted number, verdict wording and missing citations; onboarding through the chat
(refusal of non-official sites without a request, proposal, approval, then searchable); the streaming endpoint.

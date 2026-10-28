"""
kitchen/ - the agentic assistant: one agent faces the person, a kitchen of agents works behind it
================================================================================================

The bartender model (docs/SPEC in the team's Software Specification):

    front.py      FRONT AGENT   takes the order: what kind of question, which entities, which language and mode;
                                asks at most one question; keeps the conversation's memory
    master.py     MASTER AGENT  turns the order into a RECIPE (steps), runs each step on a specialist, in parallel
                                where possible, and streams progress
                  specialists   look-up (trust/), query + catalogue (explore/), calculator (tools/ and the voice
                                guide's coordinator for multi-turn money questions), onboarding (onboard/)
    explainer.py  EXPLAINER     "what this means" for every card; spots financial jargon in the answer and offers
                                to explain it; explanations link to the datasets where the term appears
    composer.py   COMPOSER      builds the served card from evidence only, with numbered citations
    verifier.py   VERIFIER      every factual sentence cited, every number traceable, no verdict words
    kitchen.py    the turn: Guardian -> front agent -> master agent -> composer + explainer -> verifier -> stream

Every step is ordinary code calling typed tools; a language model, when configured, only helps the front agent
classify messy orders and the explorer plan messy data questions, and is validated by code.
"""

"""
service.py - wire the whole app together (used by the server, the evals and the tests)
=====================================================================================

WHAT IT DOES
    build_service() creates, in order:
        Database      store/db.py         the SQLite file with every dataset snapshot
        Retrieval     tools/retrieval.py  typed official-data lookups (warnings, licences, ...)
        Inflation     tools/inflation.py  official CPI figures from the database
        Kit           agents/workflows.py the tool belt handed to each workflow
        LLM           agents/llm.py       optional AI helper (offline if no key)
        Coordinator   agents/coordinator.py  runs a conversation turn (the senior voice guide)
        TrustService  trust/report.py     the Trust Report: paste anything -> official facts with sources
        Explorer      explore/engine.py   question -> query plan -> cited records + the reusable API call
    and returns them in one small object, so every entry point is wired identically.
"""
from __future__ import annotations

from dataclasses import dataclass

from agents.coordinator import Coordinator
from agents.llm import LLM
from agents.workflows import Kit
from explore.engine import Explorer
from ingest.registry import load_registry
from store.db import Database
from tools.inflation import Inflation
from tools.retrieval import Retrieval
from trust.report import TrustService


@dataclass
class Service:
    """Everything an entry point needs. reload() picks up sources approved through the onboarding agent."""
    db: Database
    retrieval: Retrieval
    inflation: Inflation
    coordinator: Coordinator
    llm: LLM
    trust: TrustService
    explorer: Explorer

    def reload(self) -> None:
        load_registry.cache_clear()
        reg = load_registry()
        self.db.register(reg.datasets)
        self.retrieval.reg = reg


def build_service(db: Database | None = None, llm: LLM | None = None) -> Service:
    db = db or Database()
    reg = load_registry()
    db.register(reg.datasets)
    retrieval = Retrieval(db, reg)
    inflation = Inflation(db)
    llm = llm or LLM()
    coord = Coordinator(Kit(retrieval, inflation), llm)
    trust = TrustService(retrieval, llm)
    return Service(db, retrieval, inflation, coord, llm, trust, Explorer(retrieval, trust, llm))

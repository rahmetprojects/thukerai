"""
tools/retrieval.py - typed lookups in the official datasets (what the agents are allowed to ask)
==============================================================================================

WHAT IT DOES
    The ONLY door from the agents to the official data. Agents never read the database or the web
    directly; they call these functions and get structured results that ALWAYS include:
        status        searched | unavailable        (a failed lookup is never disguised as "no match")
        snapshot      which snapshot answered, when it was fetched, and whether it is stale
        match         exact | domain | normalized | probable | possible | none
        candidates    the matching official records (name, date, statement, licence code ...)
        limitations   what the dataset can NOT establish (from ingest/registry.yaml)

THE MAIN FUNCTION: check_entity(name, domain)
    Looks a company / person / website up in every relevant CMA list at once and returns a VERDICT
    from a fixed vocabulary - never "safe":
        warning_found                 a strong match on the CMA warnings list
        possible_warning              a similar name on the warnings list (needs checking, not an accusation)
        licensed_record_found         a strong match on the licensed-companies list (+ its permitted activities,
                                      + a warning if the website they gave differs from the licensed website)
        authorised_finfluencer_found  a strong match on the finfluencer list
        registered_record_found       found on a registration list (funds, auditors ...) - not a licence
        no_record_found               searched, nothing found: explicitly NOT proof of legitimacy
        could_not_check               the key lists are unavailable right now
"""
from __future__ import annotations

from datetime import datetime, timezone

import config
from ingest.registry import Registry, load_registry
from store.db import Database
from store.names import domains_in
from store.search import STRONG, EntitySearch

ENTITY_DATASETS = ["cma_warnings", "cma_licensed_companies", "cma_finfluencers", "cma_violations",
                   "cma_local_funds", "cma_passported_funds", "cma_eosg_funds", "cma_auditors",
                   "cma_assembly_organizers", "cma_appraisers"]
REGISTRATION_ONLY = {"cma_local_funds", "cma_passported_funds", "cma_eosg_funds", "cma_auditors",
                     "cma_assembly_organizers", "cma_appraisers"}
ALWAYS = ["Not appearing on a CMA list does not mean a company is safe or licensed.",
          "CMA lists do not cover banks and insurers (Central Bank), DIFC firms (DFSA), ADGM firms (FSRA) or "
          "virtual-asset firms (VARA).",
          "A matching name does not prove the person contacting you really works for that firm: verify through the "
          "contact details on the official record, not the ones they gave you."]


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    return round((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600, 1)


class Retrieval:
    def __init__(self, db: Database, registry: Registry | None = None):
        self.db = db
        self.reg = registry or load_registry()
        self.search_engine = EntitySearch(db)

    # ------------------------------------------------------------------ dataset state
    def dataset_state(self, dataset_id: str) -> dict:
        spec = self.reg.get(dataset_id)
        row = self.db.dataset_row(dataset_id) or {}
        snap = self.db.current_snapshot(dataset_id)
        age = _age_hours(snap["created_at"]) if snap else None
        checked_age = _age_hours(row.get("last_success_at"))
        return {"dataset_id": dataset_id, "name": spec.official_name, "publisher": spec.publisher,
                "source_page": spec.source_page,
                "status": "unavailable" if not snap else ("stale" if (checked_age or 0) > config.STALE_AFTER_HOURS else "ok"),
                "snapshot_id": snap["id"] if snap else None, "fetched_at": snap["created_at"] if snap else None,
                "last_verified_at": row.get("last_success_at"), "age_hours": age,
                "rows": snap["row_count"] if snap else 0, "publisher_last_modified": snap["as_of"] if snap else None,
                "last_attempt_status": row.get("last_status"), "last_error": row.get("last_error"),
                "limitations": list(spec.limitations)}

    # ------------------------------------------------------------------ one dataset
    def search(self, dataset_id: str, name: str = "", domain: str = "") -> dict:
        state = self.dataset_state(dataset_id)
        out = {**state, "query": {"name": name, "domain": domain}, "match": "none", "candidates": []}
        if state["status"] == "unavailable":
            out["searched"] = False
            return out
        out["searched"] = True
        cands = self.search_engine.search(dataset_id, name, domain)
        out["candidates"] = [{"match_type": c.match_type, "score": c.score, "matched_value": c.matched_value,
                              "record_key": c.record_key, "name": c.record.get("name"), "name_ar": c.record.get("name_ar"),
                              "date": c.record.get("date"), "status": c.record.get("status"),
                              "category": c.record.get("category"), "domains": c.record.get("domains", []),
                              "details": c.record.get("details", {})} for c in cands]
        if cands:
            out["match"] = cands[0].match_type
        return out

    def activities_for(self, licence_code: str) -> list[str]:
        acts = []
        for r in self.db.records("cma_licensed_activities"):
            if licence_code in (r["details"].get("company_codes") or []):
                acts.append(r["name"])
        return acts

    # ------------------------------------------------------------------ the combined check
    def check_entity(self, name: str = "", domain: str = "") -> dict:
        domain = " ".join(domains_in(domain or "")) or domain
        results = {d: self.search(d, name, domain) for d in ENTITY_DATASETS}
        unavailable = [d for d, r in results.items() if not r["searched"]]
        strong = {d: r for d, r in results.items() if r["match"] in STRONG}
        weak = {d: r for d, r in results.items() if r["match"] in ("probable", "possible")}
        flags: list[str] = []

        if "cma_warnings" in strong:
            verdict = "warning_found"
        elif "cma_warnings" in weak:
            verdict = "possible_warning"
        elif "cma_licensed_companies" in strong:
            verdict = "licensed_record_found"
        elif "cma_finfluencers" in strong:
            verdict = "authorised_finfluencer_found"
        elif any(d in REGISTRATION_ONLY for d in strong):
            verdict = "registered_record_found"
        elif "cma_warnings" in unavailable and "cma_licensed_companies" in unavailable:
            verdict = "could_not_check"
        else:
            verdict = "no_record_found"

        lic = results["cma_licensed_companies"]
        if lic["candidates"] and lic["match"] in STRONG:
            top = lic["candidates"][0]
            top["permitted_activities"] = self.activities_for(top["record_key"])
            given = set(domains_in(domain or ""))
            if given and top["domains"] and not (given & set(top["domains"])):
                flags.append(f"website_mismatch: you gave {', '.join(sorted(given))} but the licensed firm's website is "
                             f"{', '.join(top['domains'])}")
            if top.get("status") and top["status"].lower() != "active":
                flags.append(f"licence_status: {top['status']}")
        if "cma_violations" in strong:
            flags.append("published_violation: the CMA has published a penalty that matches this name")
        if "cma_warnings" in strong and "cma_licensed_companies" in strong:
            flags.append("conflict: the name matches BOTH a warning and a licensed firm; impersonation of a real firm "
                         "is common, so verify through the official licensed contact details")
        if unavailable:
            flags.append("some_lists_unavailable: " + ", ".join(unavailable))
        return {"query": {"name": name, "domain": domain}, "verdict": verdict, "flags": flags,
                "results": results, "unavailable": unavailable, "limitations": ALWAYS}

    def catalog(self) -> list[dict]:
        """What the agents are told exists (descriptions + live state)."""
        return [{**d.describe(), **{k: v for k, v in self.dataset_state(d.id).items()
                                     if k in ("status", "rows", "fetched_at", "snapshot_id")}} for d in self.reg.datasets]

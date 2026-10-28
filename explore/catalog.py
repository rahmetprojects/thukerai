"""
explore/catalog.py - the dataset catalogue: what each dataset means, what is in it now, how to get it
====================================================================================================

WHAT IT DOES
    Catalog.cards()      one card per dataset, built from TWO sources so it can never drift from reality:
                           * the registry (ingest/registry.yaml, `catalog:` section): plain-language description
                             in English and Arabic, keywords, what the date means, field definitions;
                           * the database: rows served, when our copy was fetched, health of the last refresh,
                             and a PROFILE of the data itself (how many records fill each field, date range,
                             the most common categories).
    Catalog.dcat()       the whole catalogue as DCAT JSON-LD (W3C Data Catalog Vocabulary), the format national
                         open-data portals (CKAN-based ones such as Bayanat) exchange.
    Catalog.filters(id)  which filters the query engine accepts for a dataset (used to validate AI plans).

    Profiles are cached per snapshot id, so they are recomputed only when new data is published.
"""
from __future__ import annotations

from collections import Counter

from tools.retrieval import Retrieval

# Filters a dataset supports beyond the generic ones (since/until on `date`, text search, status, category).
EXTRA_FILTERS = {
    "cma_licensed_companies": {"activity": "a licensed activity, e.g. 'Introduction'",
                               "licensed_since_year": "year the company was licensed, e.g. 2025"},
    "cma_finfluencers": {"valid": "'yes' for authorisations still valid today, 'no' for expired ones"},
    "cma_auditors": {"nationality": "Local or Foreign"},
    "fcsc_cpi_annual": {"year": "a year, e.g. 2025", "measure": "which index or rate"},
}
GENERIC_FILTERS = {"status": "the record's status", "category": "the record's category"}


class Catalog:
    def __init__(self, retrieval: Retrieval):
        self.r, self.db = retrieval, retrieval.db
        self._profiles: dict[str, tuple[str, dict]] = {}

    # ------------------------------------------------------------------ cards
    def cards(self) -> list[dict]:
        return [self.card(s.id) for s in self.r.reg.datasets]

    def card(self, dataset_id: str) -> dict:
        spec = self.r.reg.get(dataset_id)
        st = self.r.dataset_state(dataset_id)
        return {
            "id": spec.id, "name": spec.official_name, "publisher": spec.publisher,
            "description": spec.description, "description_ar": spec.description_ar,
            "keywords": list(spec.keywords), "date_means": spec.date_means,
            "fields": [{"name": k, "definition": v} for k, v in spec.fields],
            "source_page": spec.source_page, "refresh_hours": spec.refresh_hours,
            "questions": list(spec.supports), "limitations": list(spec.limitations),
            "state": {k: st[k] for k in ("status", "rows", "fetched_at", "last_verified_at", "last_attempt_status",
                                         "last_error", "snapshot_id")},
            "profile": self.profile(dataset_id, st["snapshot_id"]),
            "filters": self.filters(dataset_id),
            "api": {"records": f"/api/v1/datasets/{spec.id}/records", "csv": f"/api/v1/datasets/{spec.id}/records?format=csv",
                    "card": f"/api/v1/datasets/{spec.id}"},
        }

    def filters(self, dataset_id: str) -> dict:
        return {**GENERIC_FILTERS, **EXTRA_FILTERS.get(dataset_id, {})}

    def profile(self, dataset_id: str, snapshot_id: str | None) -> dict:
        if not snapshot_id:
            return {}
        cached = self._profiles.get(dataset_id)
        if cached and cached[0] == snapshot_id:
            return cached[1]
        recs = self.db.records(dataset_id, snapshot_id)
        dates = sorted(str(r["date"])[:10] for r in recs if r.get("date") and str(r["date"]) != "None")
        filled = Counter()
        for r in recs:
            for k in ("name", "name_ar", "date", "status", "category"):
                if r.get(k) and str(r[k]) != "None":
                    filled[k] += 1
            if r.get("domains"):
                filled["domains"] += 1
            for k, v in (r.get("details") or {}).items():
                if v not in (None, "", [], {}) and k != "source_page":
                    filled[f"details.{k}"] += 1
        prof = {"records": len(recs), "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None,
                "fill": {k: round(v / len(recs), 2) for k, v in filled.most_common()} if recs else {},
                "top_categories": Counter(r["category"] for r in recs if r.get("category")).most_common(5)}
        self._profiles[dataset_id] = (snapshot_id, prof)
        return prof

    # ------------------------------------------------------------------ DCAT
    def dcat(self, base_url: str = "") -> dict:
        """The catalogue as DCAT JSON-LD. Field names follow DCAT 2 / DCAT-AP."""
        ds = []
        for c in self.cards():
            ds.append({
                "@type": "dcat:Dataset", "@id": f"{base_url}/api/v1/datasets/{c['id']}",
                "dct:identifier": c["id"], "dct:title": [{"@value": c["name"], "@language": "en"}],
                "dct:description": [{"@value": c["description"], "@language": "en"},
                                    {"@value": c["description_ar"], "@language": "ar"}],
                "dct:publisher": {"@type": "foaf:Organization", "foaf:name": c["publisher"]},
                "dcat:keyword": c["keywords"], "dcat:landingPage": c["source_page"],
                "dct:modified": c["state"]["fetched_at"],
                "dct:accrualPeriodicity": f"every {c['refresh_hours']:g} hours (our refresh)",
                "dcat:distribution": [
                    {"@type": "dcat:Distribution", "dcat:accessURL": f"{base_url}{c['api']['records']}",
                     "dct:format": "JSON", "dcat:mediaType": "application/json"},
                    {"@type": "dcat:Distribution", "dcat:downloadURL": f"{base_url}{c['api']['csv']}",
                     "dct:format": "CSV", "dcat:mediaType": "text/csv"}],
                "sg:fields": c["fields"], "sg:dateMeans": c["date_means"], "sg:rows": c["state"]["rows"],
            })
        return {"@context": {"dcat": "http://www.w3.org/ns/dcat#", "dct": "http://purl.org/dc/terms/",
                             "foaf": "http://xmlns.com/foaf/0.1/", "sg": f"{base_url}/api/v1/terms#"},
                "@type": "dcat:Catalog", "dct:title": "Official UAE capital-market data (harvested, validated, versioned)",
                "dcat:dataset": ds}

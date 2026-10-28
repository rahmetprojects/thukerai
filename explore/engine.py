"""
explore/engine.py - run a query plan and return cited records, plus the API call that reproduces it
==================================================================================================

WHAT IT DOES
    Explorer.ask(question)  -> plan it (explore/planner.py), run it, return:
        {question, plan, reading, answer, count, datasets[], records[], report?, api{url, curl, mcp}, took_ms}
    Explorer.run(plan)      -> the same, for a plan given directly (the API's /api/v1/query and the MCP tool).

    Ops:
        list / latest   matching records, newest first
        count           how many records match (and the first few, so the number can be checked)
        lookup          the Trust Report for a name / website / handle (trust/report.py)
        find_datasets   which datasets answer the question, ranked, with their cards
        changes         records added to / removed from each dataset since the previous snapshot

SPEED
    Records of the current snapshots are cached in memory per snapshot id; text search uses the FTS5 index
    (store/db.py). On the test data a question is answered in single-digit milliseconds without AI.

EVERY RECORD CARRIES ITS SOURCE
    dataset id and name, record key, the official page, when our copy was fetched, and the snapshot id.
"""
from __future__ import annotations

import time
from datetime import date
from urllib.parse import urlencode

from trust.report import TrustService

from .catalog import Catalog
from .planner import Plan, RulePlanner, validate_plan

SHOW_DETAILS = {"cma_warnings": ["statement"], "cma_licensed_companies": ["licence_code", "website", "licensed_since_year"],
                "cma_finfluencers": ["registration_no", "valid_until"], "cma_violations": ["violation", "decision"],
                "cma_passported_funds": ["manager_or_title", "domicile"], "cma_local_funds": ["manager_or_title"],
                "cma_eosg_funds": ["manager_or_title"], "cma_auditors": ["nationality"],
                "cma_publications": ["documents"], "fcsc_cpi_annual": ["measure", "year", "value"]}


class Explorer:
    def __init__(self, retrieval, trust: TrustService, llm=None):
        self.r, self.db, self.trust, self.llm = retrieval, retrieval.db, trust, llm
        self.catalog = Catalog(retrieval)
        self._recs: dict[str, tuple[str, list]] = {}

    # ------------------------------------------------------------------ entry points
    def planner(self) -> RulePlanner:
        acts = [r["name"] for r in self._records("cma_licensed_activities")]
        return RulePlanner(self.r.reg, acts)

    def ask(self, question: str, today: date | None = None) -> dict:
        t0 = time.perf_counter()
        today = today or date.today()
        plan = self.planner().plan(question, today)
        if not plan.confident and self.llm is not None and getattr(self.llm, "available", False):
            raw = self.llm.plan_query(question, self._catalog_for_ai(), today.isoformat())
            ai = validate_plan(raw or {}, self.r.reg, self.catalog.filters)
            if ai and (ai.datasets or ai.op in ("lookup", "find_datasets")):
                ai.planner = "ai"
                plan = ai
        out = self.run(plan, question=question, today=today)
        out["took_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    def run(self, plan: Plan, question: str = "", today: date | None = None) -> dict:
        t0 = time.perf_counter()
        today = today or date.today()
        out = {"question": question, "plan": plan.as_dict(), "reading": plan.reading, "answer": "", "count": None,
               "datasets": [], "records": [], "report": None, "api": self.api_for(plan)}
        if plan.op == "lookup":
            rep = self.trust.check(plan.text)
            out["report"], out["answer"] = rep, rep["headline"]["text"]
            out["datasets"] = self._ds_list({f["dataset_id"] for f in rep["facts"] if f["dataset_id"]}, why="has a matching record")
        elif plan.op == "find_datasets":
            out.update(self._find(plan))
        elif plan.op == "changes":
            out.update(self._changes(plan))
        else:
            out.update(self._records_for(plan, today))
        out["took_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    # ------------------------------------------------------------------ ops
    def _records_for(self, plan: Plan, today: date) -> dict:
        datasets = plan.datasets or [s.id for s in self.r.reg.datasets if s.id != "fcsc_cpi_annual"]
        hits = None
        if plan.text:
            hits = {(h["dataset_id"], h["record_key"]) for h in self.db.fts_search(self._fts(plan.text), datasets, 2000)}
        rows, per = [], {}
        for ds in datasets:
            for rec in self._records(ds):
                if hits is not None and (ds, rec["record_key"]) not in hits:
                    continue
                if not self._passes(ds, rec, plan, today):
                    continue
                rows.append((ds, rec))
                per[ds] = per.get(ds, 0) + 1
        rows.sort(key=lambda x: str(x[1].get("date") or ""), reverse=True)
        n = len(rows)
        shown = rows[: (5 if plan.op == "count" else plan.limit)]
        names = ", ".join(self.r.reg.get(d).official_name for d in per) or "the datasets searched"
        if plan.op == "count":
            answer = f"{n} record{'s' if n != 1 else ''} match in {names}."
        elif n:
            answer = f"{n} record{'s' if n != 1 else ''} found in {names}" + (f"; showing the newest {len(shown)}." if n > len(shown) else ".")
        else:
            answer = "No records match. Try fewer words or a wider date range."
        return {"answer": answer, "count": n, "records": [self._row(d, r) for d, r in shown],
                "datasets": self._ds_list(per.keys() or datasets, why="matched your question", counts=per)}

    def _find(self, plan: Plan) -> dict:
        ids = plan.datasets or [s.id for s in self.r.reg.datasets]
        hits = {}
        if plan.text:
            for h in self.db.fts_search(self._fts(plan.text), None, 500):
                hits[h["dataset_id"]] = hits.get(h["dataset_id"], 0) + 1
            ids = list(dict.fromkeys([*ids, *hits]))
        ds = self._ds_list(ids, why="answers this kind of question", counts=hits or None, cards=True)
        return {"answer": f"{len(ds)} dataset{'s' if len(ds) != 1 else ''} can answer this.", "datasets": ds}

    def _changes(self, plan: Plan) -> dict:
        ids = plan.datasets or [s.id for s in self.r.reg.datasets if s.id != "fcsc_cpi_annual"]
        rows, summary = [], []
        for ds in ids:
            cur = (self.db.current_snapshot(ds) or {}).get("id")
            prev = self.db.previous_snapshot(ds)
            if not cur:
                continue
            if not prev:
                summary.append({"dataset_id": ds, "compared": False,
                                "note": "only one copy so far; changes appear after the next refresh that finds new data"})
                continue
            now, before = self.db.record_keys(cur), self.db.record_keys(prev)
            added, removed = now - before, before - now
            summary.append({"dataset_id": ds, "compared": True, "previous_snapshot": prev,
                            "added": len(added), "removed": len(removed)})
            rows += [(ds, r) for r in self.db.records_by_keys(cur, sorted(added))]
        rows.sort(key=lambda x: str(x[1].get("date") or ""), reverse=True)
        compared = [s for s in summary if s["compared"]]
        added = sum(s["added"] for s in compared)
        answer = (f"{added} new record{'s' if added != 1 else ''} since the previous copy of "
                  f"{len(compared)} dataset{'s' if len(compared) != 1 else ''}." if compared
                  else "No earlier copy to compare with yet: changes show after the next refresh that finds new data.")
        return {"answer": answer, "count": added, "records": [self._row(d, r) for d, r in rows[: plan.limit]],
                "changes": summary, "datasets": self._ds_list([s["dataset_id"] for s in summary], why="compared")}

    # ------------------------------------------------------------------ filters
    def _passes(self, ds: str, rec: dict, plan: Plan, today: date) -> bool:
        d = str(rec.get("date") or "")[:10]
        if plan.since and (not d or d == "None" or d < plan.since):
            return False
        if plan.until and (not d or d == "None" or d > plan.until):
            return False
        det = rec.get("details") or {}
        for k, v in plan.filters.items():
            v = str(v).lower()
            if k == "activity":
                codes = self._activity_codes(v)
                if rec["record_key"] not in codes:
                    return False
            elif k == "valid":
                vu = str(det.get("valid_until") or "")
                valid = bool(vu) and vu >= today.isoformat()
                if (v in ("yes", "true", "1")) != valid:
                    return False
            elif k in ("status", "category"):
                if str(rec.get(k) or "").lower() != v:
                    return False
            elif str(det.get(k, "")).lower() != v:
                return False
        return True

    def _activity_codes(self, activity: str) -> set:
        for r in self._records("cma_licensed_activities"):
            if r["name"].lower() == activity.lower():
                return set(r["details"].get("company_codes") or [])
        return set()

    # ------------------------------------------------------------------ shaping
    def _row(self, ds: str, r: dict) -> dict:
        st = self.r.dataset_state(ds)
        det = r.get("details") or {}
        return {"dataset_id": ds, "dataset": st["name"], "record_key": r["record_key"], "name": r.get("name"),
                "name_ar": r.get("name_ar"), "date": r.get("date") if str(r.get("date")) != "None" else None,
                "status": r.get("status"), "category": r.get("category"), "domains": r.get("domains") or [],
                "details": {k: det[k] for k in SHOW_DETAILS.get(ds, []) if det.get(k) not in (None, "", [])},
                "source_page": det.get("source_page") or st["source_page"], "fetched_at": st["fetched_at"],
                "snapshot_id": st["snapshot_id"]}

    def _ds_list(self, ids, why: str, counts: dict | None = None, cards: bool = False) -> list[dict]:
        out = []
        for d in ids:
            c = self.catalog.card(d)
            item = {"id": d, "name": c["name"], "why": why, "matched": (counts or {}).get(d),
                    "rows": c["state"]["rows"], "fetched_at": c["state"]["fetched_at"], "status": c["state"]["status"],
                    "source_page": c["source_page"], "api": c["api"]}
            if cards:
                item.update({"description": c["description"], "fields": c["fields"], "date_means": c["date_means"]})
            out.append(item)
        return out

    def _records(self, ds: str) -> list[dict]:
        snap = (self.db.current_snapshot(ds) or {}).get("id")
        if not snap:
            return []
        if ds not in self._recs or self._recs[ds][0] != snap:
            self._recs[ds] = (snap, self.db.records(ds, snap))
        return self._recs[ds][1]

    @staticmethod
    def _fts(text: str) -> str:
        """Words -> an FTS5 query: every word must appear, each as a prefix ("adgm" matches "ADGM-domiciled")."""
        import re
        words = [w for w in re.findall(r"[\w؀-ۿ]+", text) if w]
        return " ".join(f'"{w}"*' for w in words[:8])

    def api_for(self, plan: Plan) -> dict:
        """The reusable form of the question: an API URL, a curl line and the equivalent MCP tool call."""
        params = {"op": plan.op}
        if plan.datasets:
            params["datasets"] = ",".join(plan.datasets)
        for k in ("text", "since", "until"):
            if getattr(plan, k):
                params[k] = getattr(plan, k)
        for k, v in plan.filters.items():
            params[f"f.{k}"] = v
        params["limit"] = plan.limit
        url = "/api/v1/query?" + urlencode(params)
        mcp_args = {k: v for k, v in plan.as_dict().items() if k in ("op", "datasets", "text", "since", "until",
                                                                     "filters", "limit") and v not in (None, "", [], {})}
        return {"url": url, "curl": f"curl -s 'http://127.0.0.1:8000{url}'",
                "mcp": {"tool": "query_records", "arguments": mcp_args}}

    def _catalog_for_ai(self) -> str:
        lines = []
        for c in self.catalog.cards():
            lines.append(f"- {c['id']}: {c['name']}. {c['description']} Date = {c['date_means']}. "
                         f"Filters: {', '.join(c['filters'])}.")
        return "\n".join(lines)

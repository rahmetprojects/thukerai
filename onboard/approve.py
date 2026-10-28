"""
onboard/approve.py - a person approves a proposal: save the config, fetch it once, make it searchable
====================================================================================================

WHAT IT DOES
    approve(proposal, overrides, db, transport) ->
        1. validates the entry (the registry's own parser; the field_map columns must exist; official domain)
        2. appends it to the LOCAL registry (config.LOCAL_REGISTRY_YAML) - the curated ingest/registry.yaml is
           never modified by software
        3. clears the registry cache and runs the normal pipeline for that one dataset (same validation, raw
           files, run log, atomic publish as every CMA dataset)
        4. returns the run outcome: SUCCESS with N records, or why it failed (nothing half-published)
    remove(dataset_id) takes an onboarded source out of the local registry (its old snapshots stay in the
    database until pruned, but it is no longer refreshed, searched or listed).

    `overrides` lets the reviewer correct the proposal before approving: a better name, publisher,
    description, or a different column for any field_map role.
"""
from __future__ import annotations

import copy
import threading

import yaml

import config
from ingest.registry import RegistryError, load_registry, parse_registry

from .fetcher import official

_lock = threading.Lock()


def _read_local() -> dict:
    p = config.LOCAL_REGISTRY_YAML
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def _write_local(data: dict) -> None:
    p = config.LOCAL_REGISTRY_YAML
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Sources added through the onboarding agent and approved by a person (onboard/approve.py).\n"
              "# Same format as ingest/registry.yaml. Edit or delete entries here; the curated registry is separate.\n")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    tmp.replace(p)


def approve(proposal: dict, overrides: dict | None = None, *, db=None, transport=None) -> dict:
    o = overrides or {}
    entry = copy.deepcopy(proposal["entry"])
    cat = copy.deepcopy(proposal.get("catalog") or {})
    for k in ("official_name", "publisher"):
        if o.get(k):
            entry[k] = str(o[k])[:160]
    if o.get("description"):
        cat["description"] = str(o["description"])[:600]
    fm = {**entry["request"].get("field_map", {}), **{k: v for k, v in (o.get("field_map") or {}).items() if v}}
    cols = set(proposal.get("columns") or [])
    bad = [v for v in fm.values() if cols and v not in cols]
    if bad:
        return {"ok": False, "error": f"unknown column(s) in field_map: {bad}"}
    if "name" not in fm:
        return {"ok": False, "error": "choose which column holds the name before approving"}
    entry["request"]["field_map"] = fm
    entry["required_fields"] = [fm["name"]]
    entry["record_key"] = fm.get("key", "auto")
    if not official(entry["request"]["url"]) or not official(entry["source_page"]):
        return {"ok": False, "error": "only official domains can be onboarded"}

    with _lock:
        local = _read_local()
        datasets = [d for d in local.get("datasets") or [] if d.get("id") != entry["id"]]
        catalog = {k: v for k, v in (local.get("catalog") or {}).items() if k != entry["id"]}
        datasets.append(entry)
        catalog[entry["id"]] = cat
        base = yaml.safe_load(config.REGISTRY_YAML.read_text(encoding="utf-8"))
        try:                                                    # the registry's own validation, before saving
            parse_registry({**base, "datasets": list(base["datasets"]) + datasets,
                            "catalog": {**(base.get("catalog") or {}), **catalog}})
        except RegistryError as e:
            return {"ok": False, "error": f"registry check failed: {e}"}
        _write_local({"datasets": datasets, "catalog": catalog})
        load_registry.cache_clear()

    from ingest.pipeline import run_ingest
    summary = run_ingest([entry["id"]], db=db, transport=transport, use_lock=False)
    outcome = next((d for d in summary.get("datasets", []) if d.get("dataset_id") == entry["id"]), {})
    return {"ok": outcome.get("status") in ("SUCCESS", "UNCHANGED"), "dataset_id": entry["id"],
            "status": outcome.get("status"), "rows": outcome.get("rows"), "reason": outcome.get("reason"),
            "run_id": summary.get("run_id")}


def remove(dataset_id: str) -> bool:
    with _lock:
        local = _read_local()
        before = len(local.get("datasets") or [])
        local["datasets"] = [d for d in local.get("datasets") or [] if d.get("id") != dataset_id]
        (local.get("catalog") or {}).pop(dataset_id, None)
        if len(local["datasets"]) == before:
            return False
        _write_local(local)
        load_registry.cache_clear()
        return True

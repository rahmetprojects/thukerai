"""
ingest/registry.py - load and check ingest/registry.yaml
========================================================

WHAT IT DOES
    Reads the dataset registry (the YAML file next to this one) into typed `DatasetSpec` objects and
    refuses to start if an entry is incomplete. Every other part of the pipeline asks THIS module
    "which datasets exist and how do I fetch them?"; nothing hard-codes a URL or an id.

USED BY
    ingest/pipeline.py (what to fetch), store/db.py (registers datasets in the database),
    tools/retrieval.py (descriptions + limitations returned with search results),
    api/app.py (/api/data/status lists every dataset, including the "not fetched" ones).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

import config

KINDS = {"cma_content_list", "cma_integration", "cma_activity_map", "html_table", "local_csv", "remote_table"}
PILLARS = {"knowledge", "decisions", "scams"}
REQUIRED_KEYS = ("id", "official_name", "publisher", "source_page", "kind", "schema", "pillar",
                 "request", "record_key", "required_fields", "supports", "limitations")


class RegistryError(Exception):
    """registry.yaml is malformed. The message says which entry and which key."""


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    official_name: str
    publisher: str
    source_page: str
    kind: str
    schema: str
    pillar: str
    request: dict
    record_key: str
    required_fields: tuple[str, ...]
    supports: tuple[str, ...]
    limitations: tuple[str, ...]
    min_rows: int = 1
    max_drop_pct: float = 50
    refresh_hours: float = 24
    enabled: bool = True
    # catalogue metadata (the `catalog:` section of registry.yaml): what the dataset MEANS
    description: str = ""
    description_ar: str = ""
    keywords: tuple[str, ...] = ()
    date_means: str = ""
    fields: tuple[tuple[str, str], ...] = ()

    def describe(self) -> dict:
        """What the agents and the status page are told about this dataset."""
        return {"id": self.id, "name": self.official_name, "publisher": self.publisher,
                "source_page": self.source_page, "pillar": self.pillar,
                "supports": list(self.supports), "limitations": list(self.limitations)}


@dataclass(frozen=True)
class Registry:
    site: dict
    datasets: tuple[DatasetSpec, ...]
    not_fetched: tuple[dict, ...] = field(default_factory=tuple)

    def get(self, dataset_id: str) -> DatasetSpec:
        for d in self.datasets:
            if d.id == dataset_id:
                return d
        raise KeyError(f"unknown dataset {dataset_id!r}; known: {[d.id for d in self.datasets]}")

    def ids(self) -> list[str]:
        return [d.id for d in self.datasets]


def parse_registry(raw: dict) -> Registry:
    if not isinstance(raw, dict) or "datasets" not in raw or "site" not in raw:
        raise RegistryError("registry must have top-level 'site' and 'datasets'")
    specs, seen = [], set()
    cat = raw.get("catalog") or {}
    for i, d in enumerate(raw["datasets"]):
        where = f"datasets[{i}] ({d.get('id', '?')})"
        missing = [k for k in REQUIRED_KEYS if k not in d]
        if missing:
            raise RegistryError(f"{where}: missing {missing}")
        if d["kind"] not in KINDS:
            raise RegistryError(f"{where}: kind {d['kind']!r} not one of {sorted(KINDS)}")
        if d["pillar"] not in PILLARS:
            raise RegistryError(f"{where}: pillar {d['pillar']!r} not one of {sorted(PILLARS)}")
        if d["id"] in seen:
            raise RegistryError(f"{where}: duplicate id")
        seen.add(d["id"])
        specs.append(DatasetSpec(
            id=d["id"], official_name=d["official_name"], publisher=d["publisher"], source_page=d["source_page"],
            kind=d["kind"], schema=d["schema"], pillar=d["pillar"], request=dict(d["request"]),
            record_key=d["record_key"], required_fields=tuple(d["required_fields"]),
            supports=tuple(d["supports"]), limitations=tuple(d["limitations"]),
            min_rows=int(d.get("min_rows", 1)), max_drop_pct=float(d.get("max_drop_pct", 50)),
            refresh_hours=float(d.get("refresh_hours", 24)), enabled=bool(d.get("enabled", True)),
            description=str(cat.get(d["id"], {}).get("description", "")),
            description_ar=str(cat.get(d["id"], {}).get("description_ar", "")),
            keywords=tuple(str(k) for k in cat.get(d["id"], {}).get("keywords", ())),
            date_means=str(cat.get(d["id"], {}).get("date_means", "")),
            fields=tuple((str(k), str(v)) for k, v in (cat.get(d["id"], {}).get("fields") or {}).items())))
    unknown = sorted(set(cat) - seen)
    if unknown:
        raise RegistryError(f"catalog: entries for unknown datasets {unknown}")
    return Registry(site=dict(raw["site"]), datasets=tuple(specs), not_fetched=tuple(raw.get("not_fetched") or ()))


@lru_cache(maxsize=4)
def load_registry(path: str | None = None) -> Registry:
    """The curated registry, plus the sources a person approved through the onboarding agent.

    Approved sources live in a SEPARATE file (config.LOCAL_REGISTRY_YAML, default ingest/registry.local.yaml) so
    the curated, reviewed registry is never rewritten by software. Call load_registry.cache_clear() after an
    approval to pick the new source up without restarting."""
    p = Path(path or config.REGISTRY_YAML)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise RegistryError(f"cannot read {p}: {e}") from e
    local = Path(config.LOCAL_REGISTRY_YAML)
    if path is None and local.exists():
        try:
            extra = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise RegistryError(f"cannot read {local}: {e}") from e
        raw["datasets"] = list(raw["datasets"]) + list(extra.get("datasets") or [])
        raw["catalog"] = {**(raw.get("catalog") or {}), **(extra.get("catalog") or {})}
    return parse_registry(raw)

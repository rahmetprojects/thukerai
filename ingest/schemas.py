"""
ingest/schemas.py - turn each dataset's raw items into CANONICAL records
=======================================================================

WHAT IT DOES
    Every dataset arrives in its own shape. This module maps each shape (the registry's `schema`) to one
    canonical record that the rest of the app understands:

        record_key   stable id from the source (groupId, licence code, registration number ...)
        name         the main display name (English when available)
        name_ar      Arabic name when the source gives one
        names        EVERY name a person might use for it (each listed entity, English + Arabic)  -> searchable
        domains      web domains mentioned in the record (e.g. market10.net)                        -> searchable
        date         the publication / registration date (ISO), when there is one
        status       e.g. "Active" for licences
        category     e.g. fund domicile, violation decision
        details      everything else worth showing, with ORIGINAL field names kept

    Each normaliser also returns `stats` (counts the run log prints, e.g. how many warnings had
    only an Arabic version), so you can see exactly what happened to the raw items.

FIELD NAMES USED BELOW
    were read from the live API on 2026-10-28; see tests/fixtures/cma/ for real examples.
"""
from __future__ import annotations

import hashlib
import json

import re

from store.names import core_name, domains_in, normalize_name, parse_date, split_listed_names

BASE = "https://www.uaecma.gov.ae"


def _uniq(names: list[str]) -> list[str]:
    """Distinct names, compared by their core form (so 'X LLC' and 'X L.L.C.' count once)."""
    out, seen = [], set()
    for n in names:
        n = re.sub(r"\s+", " ", (n or "")).strip()
        key = core_name(n) or normalize_name(n)
        if n and key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


# ------------------------------------------------------------------ CMA content lists
def warnings(items: list[dict], spec, extra: dict) -> tuple[list[dict], dict]:
    """One warning = one groupId. The API sends an English (languageId 1) and an Arabic (2) item per group."""
    groups: dict[str, dict] = {}
    for it in items:
        g = groups.setdefault(str(it["groupId"]), {})
        g["ar" if it.get("languageId") == 2 else "en"] = it
    stats = {"items": len(items), "warnings": len(groups),
             "with_both_languages": sum(1 for g in groups.values() if "en" in g and "ar" in g),
             "english_only": sum(1 for g in groups.values() if "en" in g and "ar" not in g),
             "arabic_only": sum(1 for g in groups.values() if "ar" in g and "en" not in g)}
    out = []
    for gid, g in groups.items():
        en, ar = g.get("en"), g.get("ar")
        main = en or ar
        en_title, ar_title = _clean((en or {}).get("title")), _clean((ar or {}).get("title"))
        attr_en, attr_ar = (en or {}).get("attr") or {}, (ar or {}).get("attr") or {}
        names = _uniq(split_listed_names(en_title) + split_listed_names(ar_title))
        text = " ".join(filter(None, [en_title, ar_title, _clean(attr_en.get("address")), _clean(attr_ar.get("address"))]))
        out.append({
            "record_key": gid,
            "name": en_title or ar_title,
            "name_ar": ar_title,
            "names": names,
            "domains": domains_in(text),
            "date": parse_date(main.get("isoItemDate1") or main.get("shortItemDate1")),
            "status": "warning",
            "category": "unlicensed activity",
            "details": {"statement": _clean(attr_en.get("unlicensedPractice")),
                        "statement_ar": _clean(attr_ar.get("unlicensedPractice")),
                        "address": _clean(attr_en.get("address") or attr_ar.get("address")),
                        "listed_entities": len(names),
                        "cma_item_ids": [x["id"] for x in (en, ar) if x],
                        "last_update": main.get("lastUpdateDate"),
                        "source_page": spec.source_page}})
    return out, stats


def violations(items, spec, extra):
    out = []
    for it in items:
        a = it.get("attr") or {}
        name = _clean(it.get("title"))
        out.append({"record_key": str(it["groupId"]), "name": name, "name_ar": "",
                    # "Investor" is how the CMA anonymises individuals: never index it as a searchable name
                    "names": [] if name.lower() in ("investor", "an investor", "individual") else [name],
                    "domains": [], "date": parse_date(it.get("isoItemDate1") or it.get("shortItemDate1")),
                    "status": "penalised", "category": _clean(a.get("decision")),
                    "details": {"violation": _clean(a.get("descriptionViolation")), "decision": _clean(a.get("decision")),
                                "number": a.get("no"), "source_page": spec.source_page}})
    return out, {"items": len(items), "anonymised_individuals": sum(1 for r in out if not r["names"])}


def funds(items, spec, extra):
    out = []
    for it in items:
        a = it.get("attr") or {}
        title, fund_name = _clean(it.get("title")), _clean(a.get("fundName"))
        cats = [c.get("title") for c in (it.get("categories") or []) if isinstance(c, dict)]
        out.append({"record_key": str(it["groupId"]), "name": fund_name or title, "name_ar": "",
                    "names": _uniq([title, fund_name]), "domains": [],
                    "date": parse_date(it.get("isoItemDate1") or it.get("shortItemDate1")),
                    "status": "registered", "category": _clean(a.get("funddomicile")) or ", ".join(filter(None, cats)),
                    "details": {"manager_or_title": title if fund_name else "", "fund_name": fund_name or title,
                                "domicile": _clean(a.get("funddomicile")), "reference_no": a.get("refNo") or a.get("no"),
                                "categories": cats, "source_page": spec.source_page}})
    return out, {"items": len(items)}


def registered_entities(items, spec, extra):
    out = []
    for it in items:
        a = it.get("attr") or {}
        name = _clean(it.get("title"))
        out.append({"record_key": str(it["groupId"]), "name": name, "name_ar": "", "names": [name], "domains": [],
                    "date": parse_date(a.get("dateRegistration") or it.get("isoItemDate1")),
                    "status": "registered", "category": spec.official_name,
                    "details": {k: v for k, v in a.items() if v not in (None, "")} | {"source_page": spec.source_page}})
    return out, {"items": len(items)}


def publications(items, spec, extra):
    out = []
    for it in items:
        docs = [{"title": _clean(d.get("title")), "type": d.get("extension"), "size": d.get("formatedsize"),
                 "url": f"{BASE}/{d['dlink'].lstrip('/')}" if d.get("dlink") else ""}
                for d in ((it.get("attr") or {}).get("documents") or []) if isinstance(d, dict)]
        out.append({"record_key": str(it["groupId"]), "name": _clean(it.get("title")).replace("​", ""), "name_ar": "",
                    "names": [], "domains": [], "date": parse_date(it.get("isoItemDate1")), "status": "published",
                    "category": "publication", "details": {"documents": docs, "source_page": spec.source_page}})
    return out, {"items": len(items), "documents": sum(len(r["details"]["documents"]) for r in out)}


# ------------------------------------------------------------------ CMA integration lists
def licensed_companies(items, spec, extra):
    ar_by_key = extra.get("arabic_by_key") or {}
    out = []
    for it in items:
        code = _clean(it.get("code"))
        ar = ar_by_key.get(code) or {}
        name, name_ar = _clean(it.get("name")), _clean(ar.get("name"))
        website = _clean(it.get("website"))
        out.append({"record_key": code, "name": name, "name_ar": name_ar, "names": _uniq([name, name_ar]),
                    "domains": domains_in(website), "date": None, "status": _clean(it.get("status")),
                    "category": "licensed", "details": {"licence_code": code, "website": website,
                                                        "licensed_since_year": _clean(it.get("year")),
                                                        "status_ar": _clean(ar.get("status")),
                                                        "source_page": spec.source_page}})
    return out, {"items": len(items), "with_arabic_name": sum(1 for r in out if r["name_ar"]),
                 "statuses": sorted({r["status"] for r in out})}


def finfluencers(items, spec, extra):
    out = []
    for it in items:
        social = [s for s in re.split(r"\s+", _clean(it.get("socialMedia"))) if s.startswith(("http", "www"))]
        handles = [re.sub(r"^https?://(www\.)?[^/]+/@?", "", s).strip("/") for s in social]
        name, name_ar = _clean(it.get("name")), _clean(it.get("nameAR"))
        out.append({"record_key": _clean(it.get("regNo")), "name": name, "name_ar": name_ar,
                    "names": _uniq([name, name_ar] + [h for h in handles if len(h) >= 4]), "domains": [],
                    "date": parse_date(it.get("issueDate")), "status": "authorised",
                    "category": "finfluencer",
                    "details": {"registration_no": _clean(it.get("regNo")), "valid_until": parse_date(it.get("validityDate")),
                                "valid_until_text": _clean(it.get("validityDate")), "email": _clean(it.get("email")),
                                "social_media": social, "phone": _clean(it.get("phone")),
                                "source_page": spec.source_page}})
    return out, {"items": len(items), "with_social_links": sum(1 for r in out if r["details"]["social_media"])}


def activity_map(items, spec, extra):
    out = [{"record_key": it["activity_id"], "name": it["activity"], "name_ar": "", "names": [], "domains": [],
            "date": None, "status": "", "category": "licensed activity",
            "details": {"activity_id": it["activity_id"], "titles": it["titles"], "company_codes": it["company_codes"],
                        "firms": len(it["company_codes"]), "source_page": spec.source_page}} for it in items]
    return out, {"activities": len(out), "firm_activity_links": sum(len(i["company_codes"]) for i in items)}


def html_registered(items, spec, extra):
    out = [{"record_key": normalize_name(it.get("name")), "name": _clean(it.get("name")), "name_ar": "",
            "names": [_clean(it.get("name"))], "domains": [], "date": parse_date(it.get("date")),
            "status": "registered", "category": spec.official_name, "details": {"source_page": spec.source_page}}
           for it in items if _clean(it.get("name"))]
    return out, {"items": len(items)}


def cpi(items, spec, extra):
    out = [{"record_key": f"{r['MEASURE']}|{r['CPI_DIV']}|{r['TIME_PERIOD']}", "name": "", "name_ar": "", "names": [],
            "domains": [], "date": None, "status": "", "category": r["CPI_DIV"],
            "details": {"measure": r["MEASURE"], "division": r["CPI_DIV"], "year": int(r["TIME_PERIOD"]),
                        "value": float(r["OBS_VALUE"])}}
           for r in items if str(r.get("OBS_VALUE", "")).strip() not in ("", "NaN")]
    return out, {"items": len(items), "divisions": len({r["category"] for r in out}),
                 "years": f"{min(r['details']['year'] for r in out)}-{max(r['details']['year'] for r in out)}" if out else ""}


def generic(items, spec, extra):
    """Any onboarded table: the registry's request.field_map says which column is what (onboard/infer.py)."""
    fm = spec.request.get("field_map") or {}
    out, no_name = [], 0
    for it in items:
        name = _clean(it.get(fm.get("name", ""), ""))
        if not name:
            no_name += 1
            continue
        name_ar = _clean(it.get(fm.get("name_ar", ""), ""))
        site = _clean(it.get(fm.get("website", ""), ""))
        key = _clean(it.get(fm.get("key", ""), "")) or hashlib.sha1(
            json.dumps(it, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        details = {k: v for k, v in it.items() if v not in ("", None)}
        details["source_page"] = spec.source_page
        out.append({"record_key": key, "name": name, "name_ar": name_ar, "names": _uniq([name, name_ar]),
                    "domains": domains_in(site) if site else [], "date": parse_date(it.get(fm.get("date", ""))),
                    "status": _clean(it.get(fm.get("status", ""), "")), "category": _clean(it.get(fm.get("category", ""), "")),
                    "details": details})
    return out, {"items": len(items), "records": len(out), "rows_without_a_name": no_name}


NORMALIZERS = {"warnings": warnings, "violations": violations, "funds": funds,
               "registered_entities": registered_entities, "publications": publications,
               "licensed_companies": licensed_companies, "finfluencers": finfluencers,
               "activity_map": activity_map, "html_registered": html_registered, "cpi": cpi, "generic": generic}

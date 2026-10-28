"""
tools/inflation.py - official UAE inflation figures, from the CPI snapshot in the database
=========================================================================================

WHAT IT DOES
    Answers "how much did prices rise between year A and year B?" (overall or by category) using the FCSC
    annual consumer price index that the ingest pipeline stored as dataset `fcsc_cpi_annual`. The result
    carries the snapshot id and the source, like every other piece of evidence.

THE ONE TRICKY PART: TWO INDEX BASES
    The FCSC file mixes a 2014 = 100 index (2010-2020) and a 2021 = 100 index (2021 onwards). Numbers on
    different bases must never be divided by each other. We chain them:
        inside each base:   ratio of index values (exact)
        across the break:   the OFFICIAL published % change for 2021 (CPI_ANNCHG...) - the only legitimate link
    and refuse to answer (InflationError) on gaps, a missing link, or an implausible jump.

CATEGORY CODES
    ALL overall, HOU housing & utilities, FNB food, TRN transport, MED medical, EDU education, ... (labels below).
    The meaning of the codes is inferred from their abbreviations; confirm against FCSC's code list.
"""
from __future__ import annotations

from store.db import Database

DATASET = "fcsc_cpi_annual"
LABELS = {"ALL": "overall consumer prices", "HOU": "housing and utilities", "FNB": "food and beverages",
          "TOB": "tobacco", "TEX": "clothing and footwear", "FUR": "furnishings and household equipment",
          "MED": "health and medical care", "TRN": "transport", "COM": "communications",
          "REC": "recreation and culture", "EDU": "education", "RES": "restaurants and hotels",
          "INS": "insurance and financial services", "MIS": "miscellaneous goods and services"}


class InflationError(Exception):
    """The CPI data cannot answer this safely. The message says why, in plain words."""


def _series(rows: list[dict], div: str) -> dict[int, float]:
    def pick(prefix: str) -> dict[int, float]:
        return {r["year"]: r["value"] for r in rows if r["division"] == div and r["measure"] == prefix}
    old, new = pick("CPI_INDEX14"), pick("CPI_INDEX21")
    # The link year's change: the old-series measure CPI_ANNCHG first, the new-series CPI_ANNCHG21 as fallback.
    chg = {r["year"]: r["value"] for r in rows if r["division"] == div and r["measure"] == "CPI_ANNCHG21"}
    chg.update({r["year"]: r["value"] for r in rows if r["division"] == div and r["measure"] == "CPI_ANNCHG"})
    if not old and not new:
        raise InflationError(f"no CPI rows for category {div}")
    if not old:
        base = min(new)
        return {y: 100 * v / new[base] for y, v in sorted(new.items())}
    oy, ny = sorted(old), sorted(new)
    for name, ys in (("2014-base", oy), ("2021-base", ny)):
        if ys and ys != list(range(ys[0], ys[-1] + 1)):
            raise InflationError(f"{div}: a year is missing in the {name} index")
    chained = {y: 100 * old[y] / old[oy[0]] for y in oy}
    if ny:
        link = ny[0]
        if link != oy[-1] + 1 or link not in chg:
            raise InflationError(f"{div}: cannot link the two index bases (missing official change for {link})")
        chained[link] = chained[oy[-1]] * (1 + chg[link] / 100)
        for y in ny[1:]:
            chained[y] = chained[link] * new[y] / new[link]
    ys = sorted(chained)
    for a, b in zip(ys, ys[1:]):
        if chained[a] <= 0 or abs(chained[b] / chained[a] - 1) > 0.5:
            raise InflationError(f"{div}: implausible change between {a} and {b}; refusing to use it")
    return chained


class Inflation:
    def __init__(self, db: Database):
        self.db = db

    def available(self) -> dict:
        snap = self.db.current_snapshot(DATASET)
        if not snap:
            return {"status": "unavailable"}
        rows = [r["details"] for r in self.db.records(DATASET)]
        years = sorted({r["year"] for r in rows})
        return {"status": "ok", "years": [years[0], years[-1]] if years else [], "snapshot_id": snap["id"],
                "categories": sorted({r["division"] for r in rows})}

    def change(self, start_year: int, end_year: int, category: str = "ALL") -> dict:
        snap = self.db.current_snapshot(DATASET)
        if not snap:
            raise InflationError("the official inflation data has not been loaded yet")
        rows = [r["details"] for r in self.db.records(DATASET)]
        div = (category or "ALL").upper()
        s = _series(rows, div)
        lo, hi = min(s), max(s)
        if start_year not in s or end_year not in s:
            raise InflationError(f"the official figures cover {lo} to {hi}; {start_year}-{end_year} is outside that")
        if end_year <= start_year:
            raise InflationError("the end year must be after the start year")
        cum = s[end_year] / s[start_year] - 1
        ann = (1 + cum) ** (1 / (end_year - start_year)) - 1
        return {"category": LABELS.get(div, div), "category_code": div, "start_year": start_year, "end_year": end_year,
                "total_price_rise_pct": round(cum * 100, 2), "average_per_year_pct": round(ann * 100, 2),
                "latest_year_available": hi, "earliest_year_available": lo,
                "source": "UAE Federal Competitiveness and Statistics Centre (FCSC), annual consumer price index",
                "dataset_id": DATASET, "snapshot_id": snap["id"], "snapshot_created_at": snap["created_at"],
                "limitations": ["Annual national averages; your own costs (especially rent) may differ.",
                                "Past inflation does not predict future inflation."]}

    def latest(self, category: str = "ALL", years: int = 1) -> dict:
        a = self.available()
        if a["status"] != "ok":
            raise InflationError("the official inflation data has not been loaded yet")
        hi = a["years"][1]
        return self.change(hi - years, hi, category)

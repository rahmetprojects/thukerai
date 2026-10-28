"""
onboard/discover.py - the detection ladder: find the table behind an official URL and propose a config
======================================================================================================

WHAT IT DOES
    discover(url) -> {
        "url", "allowed", "steps": [{"rung", "name", "result": "found|none|skipped|error", "detail"}],
        "proposals": [ {id, name, publisher, rung, source_page, request{url, format, json_path|table_index, field_map},
                        columns, rows, sample[5], normalised_sample[3], field_map, score, problems[], entry{...}} ],
        "documents": [pdf links], "took_ms" }

    The rungs, in order (the first that yields a usable table wins, the rest are still reported):
        1  ckan        the site runs CKAN: package_show gives the dataset's resources (CSV / XLSX / JSON files)
        2  direct      the URL itself returns JSON, CSV or Excel
        3  json_calls  a headless browser records the JSON the page loads (needs Playwright; optional)
        4  file_links  CSV / XLSX / JSON links on the page
        5  html_table  <table>s on the page
        6  documents   PDF links (listed for document search; not tables)

    Every candidate gets a TRIAL: the same parser the scheduled runs use (ingest/adapters/remote_table.parse)
    and the same normaliser (ingest/schemas.generic), so the sample the person approves is what will be served.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

from ingest.adapters.remote_table import flatten, parse
from ingest.responses import FetchError
from ingest.schemas import generic

from .fetcher import Fetcher, official
from .infer import field_map, score

FILE_EXT = {".csv": "csv", ".xlsx": "xlsx", ".json": "json"}
MAX_FILES = 6


class _Page(HTMLParser):
    """Links (href + text), the <title> and the meta description of a page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.title, self.description = "", ""
        self._a: str | None = None
        self._text: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._a, self._text = a["href"], []
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.description = a.get("content") or ""

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            self.links.append((self._a, " ".join("".join(self._text).split())))
            self._a = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._a is not None:
            self._text.append(data)
        if self._in_title:
            self.title += data


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48] or "dataset"


def _fmt_of(url: str, content_type: str = "") -> str | None:
    path = urlparse(url).path.lower()
    for ext, f in FILE_EXT.items():
        if path.endswith(ext):
            return f
    ct = content_type.lower()
    if "json" in ct:
        return "json"
    if "csv" in ct:
        return "csv"
    if "spreadsheetml" in ct or "excel" in ct:
        return "xlsx"
    return None


def json_path_to_records(obj, path: str = "", depth: int = 0) -> list[tuple[str, int]]:
    """Every dotted path that leads to a list of objects, with its length (biggest first). Discovery only:
    the chosen path is then FIXED in the config, so scheduled runs never guess."""
    out = []
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:20]):
        out.append((path, len(obj)))
    if isinstance(obj, dict) and depth < 4:
        for k, v in obj.items():
            out += json_path_to_records(v, f"{path}.{k}" if path else k, depth + 1)
    return sorted(out, key=lambda x: -x[1])


class Discovery:
    def __init__(self, transport=None, browser_capture=None, today: date | None = None):
        self.f = Fetcher(transport)
        self.capture = browser_capture          # callable(url) -> [(method, url, body, headers, response_bytes)]
        self.today = today or date.today()
        self._found_so_far = False

    # ------------------------------------------------------------------ main
    def discover(self, url: str) -> dict:
        t0 = time.perf_counter()
        out = {"url": url, "allowed": official(url), "steps": [], "proposals": [], "documents": []}
        if not out["allowed"]:
            out["steps"].append({"rung": 0, "name": "official source", "result": "error",
                                 "detail": f"{urlparse(url).hostname} is not an official domain; only official "
                                           "sources can be onboarded"})
            return self._done(out, t0)
        page = self.f.get(url)
        if page.error:
            out["steps"].append({"rung": 0, "name": "open the page", "result": "error", "detail": page.error})
            return self._done(out, t0)
        meta = _Page()
        if "html" in page.content_type or page.text.lstrip()[:1] == "<":
            try:
                meta.feed(page.text)
            except Exception:
                pass
        title = " ".join(meta.title.split()) or urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).hostname

        for rung, name, fn in ((1, "CKAN open-data API", self._ckan), (2, "the URL is a data file or API", self._direct),
                               (3, "JSON the page loads (headless browser)", self._json_calls),
                               (4, "file links on the page", self._file_links), (5, "HTML tables", self._tables)):
            try:
                found, detail = fn(url, page, meta, title)
            except Exception as e:                               # one rung failing must not stop the others
                found, detail = [], f"{type(e).__name__}: {e}"
                out["steps"].append({"rung": rung, "name": name, "result": "error", "detail": detail})
                continue
            out["steps"].append({"rung": rung, "name": name, "result": "found" if found else
                                 ("skipped" if detail.startswith("skipped") else "none"), "detail": detail})
            out["proposals"] += found
            self._found_so_far = bool(out["proposals"])
        pdfs = [(urljoin(url, h), t) for h, t in meta.links if urlparse(h).path.lower().endswith(".pdf")]
        out["documents"] = [{"url": u, "title": t} for u, t in pdfs[:20]]
        out["steps"].append({"rung": 6, "name": "documents (PDF)", "result": "found" if pdfs else "none",
                             "detail": f"{len(pdfs)} PDF link(s): candidates for document search, not tables"})
        out["proposals"].sort(key=lambda p: (-p["score"], p["rung"]))
        return self._done(out, t0)

    def _done(self, out: dict, t0: float) -> dict:
        out["took_ms"] = round((time.perf_counter() - t0) * 1000)
        out["requests"] = self.f.requests
        self.f.close()
        return out

    # ------------------------------------------------------------------ rungs
    def _ckan(self, url, page, meta, title):
        u = urlparse(url)
        m = re.search(r"^(.*?)/dataset/([^/?#]+)", u.path)
        base = f"{u.scheme}://{u.netloc}{m.group(1) if m else ''}"
        st = self.f.get(f"{base}/api/3/action/status_show")
        try:
            ok = st.ok and json.loads(st.text).get("success")
        except ValueError:
            ok = False
        if not ok:
            return [], "not a CKAN site"
        if not m:
            return [], "CKAN site, but this is not a dataset page: open one dataset (…/dataset/<name>) and try again"
        pk = self.f.get(f"{base}/api/3/action/package_show?id={m.group(2)}")
        pkg = json.loads(pk.text).get("result") or {}
        org = (pkg.get("organization") or {}).get("title") or u.hostname
        props = []
        for res in (pkg.get("resources") or [])[:MAX_FILES]:
            fmt = (res.get("format") or "").lower().replace("xls", "xlsx").replace("xlsxx", "xlsx") or _fmt_of(res.get("url", ""))
            if fmt not in ("csv", "xlsx", "json") or not res.get("url"):
                continue
            p = self._trial(1, res["url"], {"format": fmt}, name=f"{pkg.get('title') or title}: {res.get('name') or fmt.upper()}",
                            publisher=org, source_page=url, description=pkg.get("notes") or "",
                            keywords=[t.get("name") for t in pkg.get("tags") or [] if t.get("name")])
            if p:
                props.append(p)
        return props, f"CKAN {json.loads(st.text).get('result', {}).get('ckan_version', '')}: dataset '{m.group(2)}' " \
                      f"has {len(pkg.get('resources') or [])} resource(s), {len(props)} readable as a table"

    def _direct(self, url, page, meta, title):
        fmt = _fmt_of(url, page.content_type)
        if not fmt:
            return [], "the URL returns a web page, not data"
        req = {"format": fmt}
        if fmt == "json":
            paths = json_path_to_records(json.loads(page.text))
            if not paths:
                return [], "JSON, but no list of records inside"
            req["json_path"] = paths[0][0]
            parent = json.loads(page.text)
            for part in req["json_path"].split(".")[:-1]:
                parent = parent.get(part, {}) if isinstance(parent, dict) else {}
            pk = next((k for k in ("page", "pageIndex", "pageNumber", "page_number") if isinstance(parent, dict) and k in parent), None)
            if pk:
                req["page_param"], req["max_pages"] = pk, 50
        p = self._trial(2, url, req, name=title, publisher=urlparse(url).hostname, source_page=url, body=page.body)
        if p and req.get("page_param"):
            p["problems"].append(f"paging guessed from the answer: ?{req['page_param']}=1,2,3... until an empty page")
        return ([p] if p else []), f"{fmt.upper()} answer" + (f", records at '{req.get('json_path')}'" if fmt == "json" else "")

    def _json_calls(self, url, page, meta, title):
        if self._found_so_far:
            return [], "skipped: an earlier rung already found the data"
        if not self.capture:
            return [], "skipped: headless browser not available (pip install playwright && playwright install chromium)"
        try:
            calls = self.capture(url)
        except Exception as e:
            return [], f"skipped: the headless browser could not open the page ({type(e).__name__})"
        props = []
        for method, call_url, body, headers, resp in calls[:MAX_FILES]:
            try:
                paths = json_path_to_records(json.loads(resp))
            except ValueError:
                continue
            if not paths or paths[0][1] < 3 or not official(call_url):
                continue
            req = {"format": "json", "json_path": paths[0][0], "method": method}
            if body:
                try:
                    req["body"] = json.loads(body)
                except ValueError:
                    continue
            replay = self.f.get(call_url, method, req.get("body"))           # does it work WITHOUT the browser?
            p = self._trial(3, call_url, req, name=title, publisher=urlparse(url).hostname, source_page=url,
                            body=replay.body if replay.ok else None)
            if p:
                if not replay.ok:
                    p["problems"].append("this call only works inside the page's session (tokens or cookies): it "
                                         "needs a custom adapter like the CMA one (ingest/http_client.py)")
                props.append(p)
        return props, f"{len(calls)} JSON call(s) seen, {len(props)} with a list of records"

    def _file_links(self, url, page, meta, title):
        links = []
        for h, text in meta.links:
            full = urljoin(url, h)
            fmt = _fmt_of(full)
            if fmt and official(full) and full not in [l[0] for l in links]:
                links.append((full, fmt, text))
        props = []
        for full, fmt, text in links[:MAX_FILES]:
            p = self._trial(4, full, {"format": fmt}, name=f"{title}: {text or full.rsplit('/', 1)[-1]}",
                            publisher=urlparse(url).hostname, source_page=url, description=meta.description)
            if p:
                props.append(p)
        return props, f"{len(links)} data file link(s), {len(props)} readable"

    def _tables(self, url, page, meta, title):
        if "html" not in page.content_type and page.text.lstrip()[:1] != "<":
            return [], "not an HTML page"
        from ingest.adapters.html_table import _Tables
        t = _Tables()
        t.feed(page.text)
        props = []
        for i, tab in enumerate(t.tables):
            if len(tab) >= 3:
                p = self._trial(5, url, {"format": "html", "table_index": i}, name=f"{title}: table {i + 1}",
                                publisher=urlparse(url).hostname, source_page=url, body=page.body,
                                description=meta.description)
                if p:
                    props.append(p)
        return props, f"{len(t.tables)} table(s) on the page, {len(props)} with 2+ data rows"

    # ------------------------------------------------------------------ the trial
    def _trial(self, rung, url, req, *, name, publisher, source_page, body=None, description="", keywords=None):
        problems = []
        if body is None:
            got = self.f.get(url, req.get("method", "GET"), req.get("body"))
            if got.error or got.status != 200:
                return None
            body = got.body
        try:
            rows = parse(body, req["format"], req)
        except FetchError:
            return None
        if len(rows) < 2:
            return None
        fm = field_map(rows)
        sc = score(rows, fm)
        if "name" not in fm:
            problems.append("no column looks like a name, so records cannot be looked up by name")
        req = {"url": url, **req, "field_map": fm}
        spec = SimpleNamespace(id="trial", source_page=source_page, request=req)
        normalised, stats = generic(rows, spec, {})
        if stats["rows_without_a_name"]:
            problems.append(f"{stats['rows_without_a_name']} row(s) have no name and would be skipped")
        did = "onb_" + _slug(name)
        entry = {"id": did, "official_name": name[:120], "publisher": publisher or "", "source_page": source_page,
                 "kind": "remote_table", "schema": "generic", "pillar": "knowledge", "request": req,
                 "record_key": fm.get("key", "auto"), "required_fields": [fm["name"]] if "name" in fm else [],
                 "min_rows": max(1, len(rows) // 2), "max_drop_pct": 50, "refresh_hours": 24,
                 "supports": [f"Is a name or website in {name[:80]}?"],
                 "limitations": [f"Onboarded by the onboarding agent on {self.today.isoformat()}; column meanings were "
                                 "proposed automatically and approved by a person, not reviewed by the publisher."]}
        catalog = {"description": (description or f"Records published by {publisher} at {source_page}.")[:600],
                   "description_ar": "", "keywords": [k for k in (keywords or []) if k][:20] or
                   [w for w in re.findall(r"[a-z]{4,}", name.lower())][:8],
                   "date_means": f"the value of the column '{fm['date']}'" if "date" in fm else "not given",
                   "fields": {f"details.{c}": f"column '{c}' as published" for c in list(rows[0].keys())[:20]}}
        return {"id": did, "name": name[:120], "publisher": publisher, "rung": rung, "source_page": source_page,
                "request": req, "columns": list(rows[0].keys()), "rows": len(rows), "sample": rows[:5],
                "normalised_sample": [{k: r[k] for k in ("name", "name_ar", "date", "status", "category", "domains")}
                                      for r in normalised[:3]],
                "field_map": fm, "score": sc, "problems": problems, "entry": entry, "catalog": catalog}


def capture_with_playwright(url: str, wait_ms: int = 4000) -> list[tuple]:
    """Rung 3 helper: load the page in headless Chromium and record every JSON response it receives.
    Returns [(method, url, post_body, request_headers, response_bytes)]. Needs `pip install playwright` and
    `playwright install chromium`; raises ImportError otherwise (discovery then reports the rung as skipped)."""
    from playwright.sync_api import sync_playwright
    calls = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()

        def on_response(r):
            try:
                if "json" in (r.headers.get("content-type") or "") and r.request.resource_type in ("xhr", "fetch"):
                    calls.append((r.request.method, r.url, r.request.post_data or "", dict(r.request.headers), r.body()))
            except Exception:
                pass
        pg.on("response", on_response)
        pg.goto(url, wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(wait_ms)
        b.close()
    return calls


def default_capture():
    """The browser capture if Playwright is installed, else None (the rung is skipped and the reason shown)."""
    try:
        import playwright.sync_api  # noqa: F401
        return capture_with_playwright
    except ImportError:
        return None

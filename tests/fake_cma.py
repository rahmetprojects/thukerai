"""
tests/fake_cma.py - an in-memory imitation of the CMA website, for tests (no network)
====================================================================================

WHAT IT DOES
    Behaves like www.uaecma.gov.ae as observed on 2026-10-28, through an httpx.MockTransport:
      * GET  /en/open-data/warnings       sets an HttpOnly-style session cookie + a session-bound token
      * POST /api/PublicApi/GetContentList     needs cookie + RequestVerificationToken (else 403 token_invalid)
                                               and the languageId header (else HTTP 200 {"code":500,...})
      * POST /api/PublicApi/GeIntegrationResult  2045 (licensed, paged, the page-1-only totals quirk),
                                               2047 (activities), 2056 (finfluencers)
      * GET  the appraisers page               a static <table>
    Records are generated from the real fixture shapes (tests/fixtures/cma), so adapters see real fields.

FAULTS YOU CAN SWITCH ON (to prove the pipeline refuses bad data)
    empty_page=N           page N of content lists comes back empty (a gap)
    ignore_page_index      the server always returns page 1
    html_error_for=typeId  that list answers with an HTML error page (HTTP 200)
    drift_for=typeId       that list answers with a different JSON structure
    expire_token_after=N   the token stops working after N API calls (forces a session re-open)
    warnings_groups=N      how many warning groups exist (default 230 -> 460 items, 5 pages of 100)
"""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlparse

import httpx

BASE = "https://www.uaecma.gov.ae"


def _warning(i: int, lang: int) -> dict:
    gid = f"g-{i:05d}"
    if i % 3 == 0:
        title_en = f"1.Example Capital {i} Ltd 2. Example Markets {i} FZCO 3. Website (https://www.examplefx{i}.com/en)"
    else:
        title_en = f"Example Investment {i} L.L.C"
    title = title_en if lang == 1 else "شركة/ " + title_en
    return {"id": i * 10 + lang, "groupId": gid, "title": title, "languageId": lang, "contentTypeId": 1071,
            "isoItemDate1": f"2026-0{1 + i % 9}-1{i % 9} 00:00:00", "shortItemDate1": f"1{i % 9}/0{1 + i % 9}/2026",
            "lastUpdateDate": "2026-10-22T12:25:56.077", "isPublished": True, "isDeleted": False,
            "attr": {"address": "Dubai" if lang == 1 else "دبي",
                     "unlicensedPractice": "The CMA advises investors against dealing with this entity." if lang == 1
                     else "تهيب الهيئة بالمستثمرين عدم التعامل مع الجهة."}}


class FakeCMA:
    def __init__(self, *, warnings_groups=230, licensed=333, empty_page=None, ignore_page_index=False,
                 html_error_for=None, drift_for=None, expire_token_after=None):
        self.sessions: dict[str, str] = {}
        self.calls = 0
        self.api_calls = 0
        self.log: list[str] = []
        self.opts = dict(empty_page=empty_page, ignore_page_index=ignore_page_index, html_error_for=html_error_for,
                         drift_for=drift_for, expire_token_after=expire_token_after)
        real = json.load(open(__file__.replace("fake_cma.py", "fixtures/cma/warnings_p1.json"), encoding="utf-8"))
        self.content = {
            # the REAL warnings from the fixture first, then generated ones to reach a realistic size
            1071: real["data"]["contents"] + [_warning(i, lang) for i in range(1, warnings_groups + 1) for lang in (2, 1)],
            1039: [{"id": 500 + i, "groupId": f"v-{i}", "title": "Investor" if i % 2 else f"Broker {i} L.L.C",
                    "languageId": 1, "isoItemDate1": "2026-01-22 00:01:00",
                    "attr": {"no": str(i), "descriptionViolation": "Violated Article 16.", "decision": "Financial penalty of AED 100,000"}}
                   for i in range(1, 11)],
            1036: [{"id": 600 + i, "groupId": f"f-{i}", "title": f"Local Fund {i}", "languageId": 1,
                    "categories": [{"title": "Public"}], "attr": {"no": str(i)}} for i in range(1, 84)],
            1037: [{"id": 700 + i, "groupId": f"p-{i}", "title": f"Passported Fund {i}", "languageId": 1,
                    "isoItemDate1": "2026-10-21 00:10:00", "attr": {"refNo": str(i), "funddomicile": "ADGM"}} for i in range(1, 145)],
            1076: [{"id": 800 + i, "groupId": f"e-{i}", "title": "Emirates NBD Bank", "languageId": 1,
                    "attr": {"fundName": f"EOSB Fund {i}"}} for i in range(1, 14)],
            1045: [{"id": 900 + i, "groupId": f"a-{i}", "title": f"Audit Firm {i}", "languageId": 1,
                    "attr": {"no": str(i), "nationality": "Local"}} for i in range(1, 15)],
            1070: [{"id": 950 + i, "groupId": f"o-{i}", "title": f"Organizer {i}", "languageId": 1,
                    "attr": {"no": str(i), "dateRegistration": "30/12/2024"}} for i in range(1, 8)],
            16: [{"id": 990 + i, "groupId": f"s-{i}", "title": f"Annual Report {2000 + i}", "languageId": 1,
                  "isoItemDate1": "2026-05-04 00:05:00",
                  "attr": {"documents": [{"title": "report.pdf", "dlink": "assets/download/x/report.pdf.aspx", "extension": "pdf"}]}}
                 for i in range(1, 53)],
        }
        self.licensed = [{"code": f"CP-{i:07d}", "name": f"Licensed Firm {i} L.L.C", "status": "Active",
                          "website": f"www.firm{i}.ae", "year": "2024"} for i in range(1, licensed + 1)]
        self.licensed[0] = {"code": "CP-0001315", "name": "4T GLOBAL MARKETS FINANCIAL SERVICES L.L.C", "status": "Active",
                            "website": "https://4tglobal.ae/", "year": "2025"}
        self.activities = [{"companyId": str(a), "title": f"Activity {a}"} for a in range(1, 26)] + \
                          [{"companyId": "3", "title": "Activity 3 - second exchange"}]
        self.activities[0] = {"companyId": "1", "title": "Introduction"}
        self.activities[1] = {"companyId": "2", "title": "Trading broker in the international markets"}
        self.finfluencers = [{"name": f"Promoter {i}", "nameAR": f"مروج {i}", "regNo": str(i), "issueDate": "2024-07-10T12:00:00",
                              "validityDate": "31 Dec 2026", "phone": "", "email": f"p{i}@mail.com",
                              "socialMedia": f"https://www.instagram.com/promoter{i}", "year": "2024"} for i in range(1, 196)]

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        path = urlparse(str(request.url)).path
        self.log.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/en/open-data/warnings":
            sid = secrets.token_hex(8)
            token = f"anonymous:{sid}:{secrets.token_hex(8)}:1793152980:HMAC{secrets.token_hex(6)}="
            self.sessions[sid] = token
            html = (open(__file__.replace("fake_cma.py", "fixtures/cma/page_warnings.html"), encoding="utf-8").read()
                    .replace("anonymous:SESSIONID:abc123:1793152980:FAKEHMAC=", token))
            return httpx.Response(200, html=html, headers={"set-cookie": f"ASP.NET_SessionId={sid}; path=/; HttpOnly"})
        if request.method == "GET" and "appraisers" in path:
            html = open(__file__.replace("fake_cma.py", "fixtures/cma/appraisers.html"), encoding="utf-8").read()
            return httpx.Response(200, html=html)
        if request.method == "POST" and path in ("/api/PublicApi/GetContentList", "/api/PublicApi/GeIntegrationResult"):
            self.api_calls += 1
            sid = request.headers.get("cookie", "").partition("ASP.NET_SessionId=")[2].split(";")[0]
            exp = self.opts["expire_token_after"]
            if not sid or self.sessions.get(sid) != request.headers.get("RequestVerificationToken") or \
                    (exp and self.api_calls == exp + 1):
                if exp and self.api_calls == exp + 1:
                    self.sessions.pop(sid, None)
                return httpx.Response(403, json={"error": "token_invalid", "message": "Invalid anti-forgery token",
                                                 "code": 403, "timestamp": "2026-10-28T02:03:55Z"})
            if not request.headers.get("languageId"):
                return httpx.Response(200, json={"code": 500, "error": {"ClassName": "System.Exception",
                                                                          "Message": "An error occurred processing your request."}})
            body = json.loads(request.content)
            return self._content(body) if path.endswith("GetContentList") else self._integration(body)
        return httpx.Response(404, html="<html><body>Page not found</body></html>")

    # ------------------------------------------------------------------ endpoints
    def _content(self, b: dict) -> httpx.Response:
        tid = int(b["typeId"])
        if self.opts["html_error_for"] == tid:
            return httpx.Response(200, html="<!doctype html><html><body>Service temporarily unavailable</body></html>")
        if self.opts["drift_for"] == tid:
            return httpx.Response(200, json={"code": 200, "result": {"items": self.content[tid][:5]}})
        rows = self.content.get(tid, [])
        size = int(b.get("pageSize", 6))
        page = max(1, int(b.get("pageIndex", 1)))
        if self.opts["ignore_page_index"]:
            page = 1
        pages = -(-len(rows) // size) if rows else 0
        chunk = rows[(page - 1) * size: page * size]
        if self.opts["empty_page"] == page:
            chunk = []
        if page > pages:
            return httpx.Response(200, json={"code": 200, "data": {"page": page, "records": 0, "totalPages": 0, "contents": []}})
        return httpx.Response(200, json={"code": 200, "data": {"page": page, "records": len(rows), "totalPages": pages,
                                                               "contents": chunk}, "dateModified": "2026-10-22T12:25:56.077"})

    def _integration(self, b: dict) -> httpx.Response:
        iid, p = int(b["integrationId"]), b.get("urlParameters") or {}
        if iid == 2047:
            return httpx.Response(200, json={"code": 200, "data": {"total": 1, "records": 1, "page": 0, "items": self.activities}})
        if iid == 2056:
            return httpx.Response(200, json={"code": 200, "data": {"total": 1, "records": 1, "page": 1, "items": self.finfluencers}})
        if iid == 2045:
            rows = self.licensed
            if p.get("type"):
                a = int(p["type"])
                rows = [r for i, r in enumerate(rows) if i % 5 == a % 5 or (i == 0 and a in (1, 2))][: 7 * a]
            if p.get("languageId") == 2:
                rows = [{**r, "name": "شركة " + r["name"], "status": "نشط"} for r in rows]
            size, page = int(p.get("pageSize", 12)), max(1, int(p.get("pageIndex", 1)))
            pages = -(-len(rows) // size) if rows else 1
            chunk = rows[(page - 1) * size: page * size]
            total, records = (pages, len(rows)) if page == 1 and pages > 1 else (1, 1)   # the real quirk
            return httpx.Response(200, json={"code": 200, "data": {"total": total, "records": records, "page": page, "items": chunk},
                                             "headers": {}, "dateModified": "2026-10-28T02:07:44Z"})
        return httpx.Response(200, json={"code": 500, "error": {"Message": "unknown integration"}})

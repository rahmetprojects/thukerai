"""
tests/fake_gov.py - a fake set of OFFICIAL open-data sites for the onboarding agent (no network)
==============================================================================================

WHAT IT IMITATES (all hosts end in .gov.ae, so the official-domain rule lets them in)
    opendata.example.gov.ae   a CKAN 2.9 portal (like Bayanat): status_show, package_show, a CSV and an XLSX
                              resource for the dataset "licensed-brokers"
    www.example.gov.ae        an HTML page "Registered Valuers" with a <table>, a link to a CSV file and a PDF
    api.example.gov.ae        a JSON API: {"data": {"items": [...]}} with ?page=N paging (2 pages, then empty)
    scam-site.com             a non-official site: the agent must refuse it without sending a request

The brokers file contains "Falcon Crest Brokerage", which tests then look up through the Trust Report to prove
a newly onboarded source is searchable immediately.
"""
from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlparse

import httpx

BROKERS_CSV = ("Licence No,Company Name,Arabic Name,Status,Website,Date Licensed\n"
               "B-001,Falcon Crest Brokerage LLC,فالكون كريست للوساطة,Active,https://falconcrest.ae,12/03/2021\n"
               "B-002,Gulf Pearl Securities,لؤلؤة الخليج للأوراق المالية,Active,https://gulfpearl.ae,05/07/2019\n"
               "B-003,Desert Rose Capital,وردة الصحراء كابيتال,Suspended,https://desertrose.ae,22/11/2020\n"
               "B-004,Oasis Markets FZ,واحة الأسواق,Active,https://oasismarkets.ae,01/02/2024\n")

VALUERS_HTML = """<!doctype html><html><head><title>Registered Valuers</title>
<meta name="description" content="Valuers registered with the Example Authority."></head><body>
<h1>Registered Valuers</h1>
<table><tr><th>No.</th><th>Valuer Name</th><th>Registration Date</th></tr>
<tr><td>1</td><td>Summit Valuation Partners</td><td>14/01/2023</td></tr>
<tr><td>2</td><td>Harbor Appraisal Group</td><td>02/06/2022</td></tr>
<tr><td>3</td><td>Crescent Asset Valuers</td><td>19/09/2024</td></tr></table>
<p><a href="/files/valuers.csv">Download the list (CSV)</a> <a href="/files/annual-report.pdf">Annual report</a></p>
</body></html>"""

VALUERS_CSV = "No.,Valuer Name,Registration Date\n1,Summit Valuation Partners,14/01/2023\n2,Harbor Appraisal Group,02/06/2022\n3,Crescent Asset Valuers,19/09/2024\n"

API_PAGES = {1: [{"id": 11, "entity": {"name": "Atlas Fund Managers"}, "type": "Fund manager", "issued": "2025-04-01"},
                 {"id": 12, "entity": {"name": "Meridian Investments"}, "type": "Fund manager", "issued": "2025-05-12"}],
             2: [{"id": 13, "entity": {"name": "Northwind Advisory"}, "type": "Adviser", "issued": "2026-01-20"}]}


def _xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Brokers register (xlsx)", None])           # a title row above the header, as real files have
    for line in BROKERS_CSV.strip().split("\n"):
        ws.append(line.split(","))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class FakeGov:
    def __init__(self):
        self.requests: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url))
        self.requests.append(f"{req.method} {u.netloc}{u.path}")
        q = parse_qs(u.query)
        host, path = u.netloc, u.path
        j = lambda obj: httpx.Response(200, json=obj)
        if host == "opendata.example.gov.ae":
            if path == "/api/3/action/status_show":
                return j({"success": True, "result": {"ckan_version": "2.9.5"}})
            if path == "/api/3/action/package_show" and q.get("id") == ["licensed-brokers"]:
                return j({"success": True, "result": {
                    "title": "Licensed Brokers", "notes": "Brokerage firms licensed by the Example Authority.",
                    "organization": {"title": "Example Authority"}, "tags": [{"name": "brokers"}, {"name": "licensed"}],
                    "resources": [{"name": "Brokers CSV", "format": "CSV", "url": "https://opendata.example.gov.ae/files/brokers.csv"},
                                  {"name": "Brokers Excel", "format": "XLSX", "url": "https://opendata.example.gov.ae/files/brokers.xlsx"},
                                  {"name": "Methodology", "format": "PDF", "url": "https://opendata.example.gov.ae/files/method.pdf"}]}})
            if path == "/dataset/licensed-brokers":
                return httpx.Response(200, text="<html><title>Licensed Brokers - Open Data</title><body>CKAN</body></html>",
                                      headers={"content-type": "text/html"})
            if path == "/files/brokers.csv":
                return httpx.Response(200, content=BROKERS_CSV.encode("utf-8"), headers={"content-type": "text/csv"})
            if path == "/files/brokers.xlsx":
                return httpx.Response(200, content=_xlsx(), headers={
                    "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        if host == "www.example.gov.ae":
            if path == "/registers/valuers":
                return httpx.Response(200, text=VALUERS_HTML, headers={"content-type": "text/html; charset=utf-8"})
            if path == "/files/valuers.csv":
                return httpx.Response(200, content=VALUERS_CSV.encode(), headers={"content-type": "text/csv"})
            if path == "/api/3/action/status_show":
                return httpx.Response(404, text="not found")
        if host == "api.example.gov.ae" and path == "/v1/firms":
            page = int((q.get("page") or ["1"])[0])
            return j({"data": {"items": API_PAGES.get(page, []), "page": page}})
        if host.endswith(".gov.ae") and path == "/api/3/action/status_show":
            return httpx.Response(404, text="not found")
        return httpx.Response(404, text="not found")

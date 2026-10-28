"""
ingest/responses.py - decide what a server answer REALLY is (never trust the status code alone)
==============================================================================================

WHAT IT DOES
    Turns raw HTTP answers into either a parsed JSON object or a precise, typed error. The CMA site
    is a good example of why this matters (all verified on the live site):
        * a missing token        -> HTTP 403 {"error":"token_invalid", ...}
        * a missing language hdr -> HTTP 200 {"code":500,"error":{...}}   <- an error dressed as success
        * a page past the end    -> HTTP 200 {"code":200,"data":{"contents":[]}}  <- valid, just empty
    A naive scraper would save the second one as "0 rows" and report success. We refuse to.

ERROR KINDS (FetchError.kind) - these words appear in the logs and on the status page
    network        could not connect / timed out
    http_status    non-2xx status without a recognisable error body
    token_invalid  the site rejected our anti-forgery token or session cookie
    html_not_json  we asked for JSON and got a web page (error page, login page, bot challenge)
    invalid_json   the body is not valid JSON
    api_error      valid JSON that says it failed (e.g. code 500 inside an HTTP 200)
    empty_body     zero bytes
    schema_drift   valid JSON, but not the structure the adapter knows (see adapters/*)
    budget         the request budget for the run was used up
    config         the registry entry is wrong / incomplete
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

BOT_HINTS = re.compile(r"captcha|cf-chl|just a moment|access denied|request rejected|incapsula", re.I)


class FetchError(Exception):
    """Anything that stops a dataset from being fetched. `kind` is one of the words in the briefing."""

    def __init__(self, kind: str, message: str, *, status: int | None = None, sample: str = ""):
        super().__init__(f"{kind}: {message}")
        self.kind, self.message, self.status, self.sample = kind, message, status, sample[:600]


@dataclass
class RawResponse:
    """What the HTTP layer hands to the adapters (and what raw_store saves to disk)."""
    url: str
    method: str
    status: int
    content_type: str
    body: bytes
    seconds: float
    request_body: str = ""          # the JSON we sent (never contains secrets: the token is a header)
    attempt: int = 1

    @property
    def text(self) -> str:
        return self.body.decode("utf-8-sig", errors="replace")


def sniff(body: bytes) -> str:
    """What the bytes actually are, regardless of the Content-Type header: json | html | csv | empty | binary."""
    head = body[:512].lstrip()
    if not head:
        return "empty"
    if head[:1] in (b"{", b"["):
        return "json"
    low = head[:200].lower()
    if low.startswith((b"<!doctype", b"<html", b"<?xml")) or b"<head" in low or b"<body" in low or b"<table" in low:
        return "html"
    if head[:4] == b"PK\x03\x04":
        return "xlsx_or_zip"
    try:
        head.decode("utf-8")
        return "csv" if b"," in head.split(b"\n")[0] else "text"
    except UnicodeDecodeError:
        return "binary"


def expect_json(resp: RawResponse) -> dict:
    """Parse a CMA API answer or raise a precise FetchError. Never returns an error envelope."""
    kind = sniff(resp.body)
    if kind == "empty":
        raise FetchError("empty_body", f"HTTP {resp.status} with an empty body", status=resp.status)
    if kind == "html":
        hint = "bot/anti-robot challenge page" if BOT_HINTS.search(resp.text[:4000]) else "a web page"
        raise FetchError("html_not_json", f"HTTP {resp.status}: expected JSON but got {hint}",
                         status=resp.status, sample=resp.text[:600])
    if kind != "json":
        raise FetchError("invalid_json", f"HTTP {resp.status}: body looks like {kind}, not JSON",
                         status=resp.status, sample=resp.text[:300])
    try:
        obj = json.loads(resp.text)
    except ValueError as e:
        raise FetchError("invalid_json", f"HTTP {resp.status}: {e}", status=resp.status, sample=resp.text[:300]) from e
    if not isinstance(obj, dict):
        raise FetchError("schema_drift", f"expected a JSON object, got {type(obj).__name__}", status=resp.status)

    # Error envelopes, as observed on the CMA site.
    err = obj.get("error")
    if err == "token_invalid" or (resp.status == 403 and "token" in str(obj.get("message", "")).lower()):
        raise FetchError("token_invalid", f"HTTP {resp.status}: {obj.get('message', 'anti-forgery token rejected')}",
                         status=resp.status, sample=resp.text[:300])
    code = obj.get("code")
    if err is not None or (isinstance(code, int) and code != 200):
        msg = err.get("Message") if isinstance(err, dict) else (obj.get("message") or err)
        raise FetchError("api_error", f"HTTP {resp.status} but the API says code={code}: {msg}",
                         status=resp.status, sample=resp.text[:300])
    if not 200 <= resp.status < 300:
        raise FetchError("http_status", f"HTTP {resp.status}", status=resp.status, sample=resp.text[:300])
    return obj

"""
ingest/http_client.py - talk to the CMA website WITHOUT a browser
=================================================================

WHAT IT DOES
    Replaces the Playwright scraper. It reproduces, with plain HTTP, exactly what the CMA page's own
    JavaScript does (verified on the live site on 2026-10-28):

      1. GET https://www.uaecma.gov.ae/en/open-data/warnings
            -> the server sets a session cookie (HttpOnly) and the HTML contains
               <input type="hidden" name="__RequestVerificationToken" value="anonymous:...">
      2. POST /api/PublicApi/GetContentList   (or /GeIntegrationResult) with headers
               RequestVerificationToken: <value from step 1>      (required, else 403 token_invalid)
               languageId: 1      languageCode: en-GB             (required, else "code 500" inside HTTP 200)
               Content-Type: application/json
            and the session cookie from step 1 (sent automatically by the cookie jar; required too).

    The token is bound to the session cookie, so both must come from the same GET. If the server
    rejects the token mid-run (session expired), the session is re-opened ONCE and the call retried.

    OPTIONAL FALLBACK: if the page ever stops containing the token (e.g. the site starts rendering it
    with JavaScript), and SG_ALLOW_BROWSER=1, a headless browser (Playwright) is used ONLY to obtain
    the cookie + token; all data calls still go through plain HTTP. Off by default.

RELIABILITY
    * timeouts on every request (config.HTTP_TIMEOUT)
    * retries ONLY for network errors, 429 and 5xx: exponential backoff + random jitter, bounded
    * a request budget per run (config.MAX_REQUESTS_PER_RUN) so a pagination bug cannot loop forever
    * a polite pause between pages (config.PAGE_DELAY)

LOGGING (every attempt goes to the run log, see ingest/runlog.py)
    url, method, status, bytes, content type, seconds, attempt number, and for the session: which
    cookies were set (NAMES only) and where the token was found (length only). Never the values.
"""
from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

import config

from .responses import FetchError, RawResponse
from .runlog import RunLog

TOKEN_PATTERNS = [
    ("hidden input", re.compile(r'<input[^>]*name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']', re.I)),
    ("hidden input", re.compile(r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']__RequestVerificationToken["\']', re.I)),
    ("meta tag", re.compile(r'<meta[^>]*name=["\'](?:RequestVerificationToken|csrf-token)["\'][^>]*content=["\']([^"\']+)["\']', re.I)),
]
LANG = {"en": ("1", "en-GB"), "ar": ("2", "ar-AE")}
RETRY_STATUS = {429, 500, 502, 503, 504}


def find_token(html: str) -> tuple[str | None, str]:
    """(token, where_found). Exported for tests."""
    for where, pat in TOKEN_PATTERNS:
        m = pat.search(html)
        if m and len(m.group(1)) >= 16:
            return m.group(1), where
    return None, "not found"


class HttpClient:
    """Polite HTTP with retries, a budget and full attempt logging. One per ingest run."""

    def __init__(self, runlog: RunLog, transport: httpx.BaseTransport | None = None,
                 budget: int | None = None, sleep=time.sleep):
        self.log = runlog
        self.budget = budget or config.MAX_REQUESTS_PER_RUN
        self.sent = 0
        self.bytes = 0
        self.sleep = sleep                      # injectable so tests run instantly
        self.client = httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True, transport=transport,
                                   headers={"User-Agent": config.USER_AGENT,
                                            "Accept-Language": "en-GB,en;q=0.9,ar;q=0.8"})

    def close(self) -> None:
        self.client.close()

    def request(self, dataset: str, method: str, url: str, *, headers: dict | None = None,
                json_body: dict | None = None, purpose: str = "") -> RawResponse:
        body_text = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")) if json_body is not None else ""
        last: Exception | None = None
        for attempt in range(1, config.HTTP_MAX_RETRIES + 2):
            if self.sent >= self.budget:
                raise FetchError("budget", f"request budget of {self.budget} for this run is used up")
            self.sent += 1
            t0 = time.time()
            try:
                r = self.client.request(method, url, headers=headers,
                                        content=body_text.encode("utf-8") if body_text else None)
            except httpx.ProxyError as e:                # a proxy/firewall refusing the site: retrying will not help
                self.log.event(dataset, "http", "error", f"{method} {_short(url)} -> blocked by a proxy or firewall",
                               attempt=attempt, error=str(e)[:200], purpose=purpose)
                raise FetchError("network", f"this network's proxy/firewall refused the connection to "
                                 f"{urlparse(url).netloc} ({e}). Run the pipeline from a network that can open the "
                                 "CMA website in a browser.") from e
            except httpx.TransportError as e:            # DNS, connect, read, timeout, protocol errors
                secs = time.time() - t0
                last = FetchError("network", f"{type(e).__name__}: {e}")
                self.log.event(dataset, "http", "warn", f"{method} {_short(url)} -> network error",
                               attempt=attempt, seconds=round(secs, 2), error=str(e)[:200], purpose=purpose)
                self._backoff(attempt)
                continue
            secs = time.time() - t0
            self.bytes += len(r.content)
            resp = RawResponse(str(r.url), method, r.status_code, r.headers.get("content-type", ""), r.content,
                               secs, body_text, attempt)
            self.log.event(dataset, "http", "info" if r.status_code < 400 else "warn",
                           f"{method} {_short(url)} -> HTTP {r.status_code}", attempt=attempt, bytes=len(r.content),
                           content_type=resp.content_type.split(";")[0], seconds=round(secs, 2), purpose=purpose)
            if r.status_code in RETRY_STATUS and attempt <= config.HTTP_MAX_RETRIES:
                last = FetchError("http_status", f"HTTP {r.status_code}", status=r.status_code)
                self._backoff(attempt, r.headers.get("retry-after"))
                continue
            return resp
        raise last or FetchError("network", "failed after retries")

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after and retry_after.isdigit():
            wait = min(float(retry_after), 60.0)
        else:
            wait = config.HTTP_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.75)
        self.sleep(wait)


class CmaSession:
    """A browserless session on the CMA website: cookie jar + anti-forgery token."""

    def __init__(self, http: HttpClient, site: dict):
        self.http = http
        self.site = site
        self.base = site["base_url"].rstrip("/")
        self.token: str | None = None
        self.opened_at = 0.0
        self.reopens = 0

    # ------------------------------------------------------------------ session
    def open(self, dataset: str = "session") -> None:
        page = self.site["session_page"]
        resp = self.http.request(dataset, "GET", page, headers={"Accept": "text/html,application/xhtml+xml"},
                                 purpose="open session")
        if resp.status != 200:
            raise FetchError("http_status", f"session page answered HTTP {resp.status}", status=resp.status,
                             sample=resp.text[:300])
        cookie_names = sorted({c.name for c in self.http.client.cookies.jar})
        token, where = find_token(resp.text)
        if not token and config.ALLOW_BROWSER:
            self.http.log.event(dataset, "token", "warn", "token not in HTML; trying the browser fallback")
            token, where, cookie_names = self._browser_fallback(page)
        if not token:
            raise FetchError("token_invalid", "the page no longer contains __RequestVerificationToken; "
                             "the site changed. Set SG_ALLOW_BROWSER=1 to try the browser fallback, and see "
                             "docs/CMA_API_NOTES.md", sample=resp.text[:400])
        self.token = token
        self.opened_at = time.time()
        self.http.log.event(dataset, "token", "info", f"session opened; token found in {where}",
                            token_chars=len(token), cookies_set=cookie_names, page_bytes=len(resp.body))

    def _browser_fallback(self, page: str) -> tuple[str | None, str, list[str]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise FetchError("config", "SG_ALLOW_BROWSER=1 but Playwright is not installed "
                             "(pip install playwright ; playwright install chromium)")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(user_agent=config.USER_AGENT)
                pg = ctx.new_page()
                pg.goto(page, wait_until="domcontentloaded", timeout=60000)
                token = pg.eval_on_selector('input[name="__RequestVerificationToken"]', "e => e.value")
                names = []
                for c in ctx.cookies():
                    self.http.client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
                    names.append(c["name"])
                return token, "browser DOM (fallback)", sorted(names)
            finally:
                browser.close()

    # ------------------------------------------------------------------ API calls
    def post_api(self, dataset: str, endpoint: str, payload: dict, lang: str = "en",
                 purpose: str = "") -> RawResponse:
        """POST to a CMA API endpoint with the session headers. Re-opens the session once on token_invalid."""
        if not self.token:
            self.open(dataset)
        lang_id, lang_code = LANG[lang]
        url = urljoin(self.base + "/", endpoint.lstrip("/"))
        for round_ in (1, 2):
            headers = {"Content-Type": "application/json; charset=utf-8",
                       "Accept": "application/json, text/javascript, */*; q=0.01",
                       "X-Requested-With": "XMLHttpRequest",
                       "RequestVerificationToken": self.token or "",
                       "languageId": lang_id, "languageCode": lang_code,
                       "Origin": self.base, "Referer": self.site["session_page"]}
            resp = self.http.request(dataset, "POST", url, headers=headers, json_body=payload, purpose=purpose)
            if resp.status == 403 and b"token" in resp.body[:300].lower() and round_ == 1:
                self.reopens += 1
                self.http.log.event(dataset, "token", "warn", "token rejected (403); re-opening the session once")
                self.open(dataset)
                continue
            return resp
        return resp

    def get_page(self, dataset: str, url: str, purpose: str = "") -> RawResponse:
        return self.http.request(dataset, "GET", url, headers={"Accept": "text/html"}, purpose=purpose)


def _short(url: str) -> str:
    u = urlparse(url)
    return u.path + (("?" + u.query) if u.query else "")

"""
onboard/fetcher.py - a safe getter for discovery (official domains only)
=======================================================================

WHAT IT DOES
    Fetcher.get(url) -> Fetched(url, status, content_type, body, error)
    * refuses any host that is not on config.OFFICIAL_DOMAINS (suffix match): the promise "only official sources
      go in" is enforced here, so a scam site can never be onboarded, by a person or by an AI;
    * http/https only, follows redirects but re-checks the final host, 20 s timeout, at most
      config.ONBOARD_MAX_BYTES per response;
    * accepts an httpx transport so tests run against fake sites with no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

import config

UA = "SeniorGuide-Onboarding/1.0 (+official open-data harvesting; contact: the operator of this server)"


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes
    error: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8-sig", errors="replace")

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error


def official(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and any(host == d.lstrip(".") or host.endswith(d if d.startswith(".") else "." + d)
                              for d in config.OFFICIAL_DOMAINS)


class Fetcher:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(transport=transport, timeout=20, follow_redirects=True,
                                   headers={"User-Agent": UA})
        self.requests = 0

    def get(self, url: str, method: str = "GET", json_body=None, headers: dict | None = None) -> Fetched:
        if urlparse(url).scheme not in ("http", "https"):
            return Fetched(url, 0, "", b"", "only http(s) links can be onboarded")
        if not official(url):
            return Fetched(url, 0, "", b"", f"{urlparse(url).hostname} is not on the list of official domains "
                                             "(config.OFFICIAL_DOMAINS)")
        self.requests += 1
        try:
            with self.client.stream(method, url, json=json_body, headers=headers) as r:
                if not official(str(r.url)):
                    return Fetched(str(r.url), r.status_code, "", b"", "redirected to a non-official domain")
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > config.ONBOARD_MAX_BYTES:
                        return Fetched(str(r.url), r.status_code, r.headers.get("content-type", ""), b"",
                                       f"larger than {config.ONBOARD_MAX_BYTES // 1_000_000} MB")
                return Fetched(str(r.url), r.status_code, r.headers.get("content-type", ""), body)
        except httpx.HTTPError as e:
            return Fetched(url, 0, "", b"", f"{type(e).__name__}: {e}")

    def close(self) -> None:
        self.client.close()

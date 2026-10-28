"""
logsetup.py - one logging setup for the whole app
=================================================

WHAT IT DOES
    Configures Python's `logging` once, so every module can simply do
        log = logging.getLogger(__name__)
    and get the same format, written to BOTH the console and logs/app.log.

WHERE LOGS GO
    logs/app.log          everything the server and the agents do (rotated at 5 MB, 5 files kept)
    logs/ingest/...       the DATA pipeline keeps its own, much more detailed logs
                          (one JSON-lines file + one plain-text summary per run). See ingest/runlog.py.

PRIVACY RULE
    Never log what a person said, their amounts, cookies, tokens or API keys. Log labels, ids,
    counts and durations. `redact_headers()` below is the helper for HTTP headers.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

import config

_DONE = False
SECRET_HEADERS = {"cookie", "set-cookie", "authorization", "requestverificationtoken",
                  "x-xsrf-token", "x-csrf-token", "x-api-key", "ocp-apim-subscription-key"}


def setup_logging(level: str | None = None, stream=None) -> None:
    """Idempotent: safe to call from every entry point (server, CLI, tests).
    stream: where console logs go (default stdout). The MCP server passes sys.stderr, because over stdio the
    protocol owns stdout and a single log line there would corrupt it."""
    global _DONE
    if _DONE:
        return
    _DONE = True
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-22s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level or config.LOG_LEVEL)

    try:                                   # Windows consoles (cp1252) cannot print Arabic: replace, never crash
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    console = logging.StreamHandler(stream or sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file = logging.handlers.RotatingFileHandler(config.LOG_DIR / "app.log", maxBytes=5_000_000,
                                                backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    root.addHandler(file)

    for noisy in ("httpx", "httpcore", "uvicorn.access", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def redact_headers(headers: dict) -> dict:
    """Copy of `headers` safe to log: secret values replaced by '<hidden, N chars>'."""
    out = {}
    for k, v in (headers or {}).items():
        out[k] = f"<hidden, {len(str(v))} chars>" if k.lower() in SECRET_HEADERS else v
    return out

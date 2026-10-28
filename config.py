"""
config.py - every setting in one place
=======================================

WHAT IT DOES
    Holds all paths, limits and switches used by the app. Nothing else in the project should
    hard-code a path, a timeout or a model name: import it from here instead.

HOW TO CHANGE A SETTING
    Set an environment variable before starting the app (no code change needed), e.g.
        SG_LIVE_DATA=0 python run_server.py        # never touch the network (demo / tests)

ENVIRONMENT VARIABLES (all optional)
    SG_DATA_DIR            where the database, raw snapshots and logs live   (default ./data)
    SG_LOG_DIR             where the log files are written                    (default ./logs)
    SG_LIVE_DATA           1 = fetch official data on start-up, 0 = never     (default 1)
    SG_REFRESH_ON_START    1 = run the ingest pipeline in the background when the server starts
    SG_HTTP_TIMEOUT        seconds per HTTP request to a government site      (default 30)
    SG_ALLOW_BROWSER       1 = allow the Playwright fallback when plain HTTP cannot get a token
    SG_LOG_LEVEL           DEBUG / INFO / WARNING                              (default INFO)
    GEMINI_API_KEY         optional AI phrasing + intent help via Google Gemini
    ANTHROPIC_API_KEY      optional AI phrasing + intent help via Claude
    SG_HOST / SG_PORT      where the web server listens (default 127.0.0.1:8000, this computer only)
    SG_DEBUG               1 = /api/chat also returns the full agent trace
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip() not in ("0", "false", "no", "")


# --- folders ------------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("SG_DATA_DIR", ROOT / "data"))
LOG_DIR = Path(os.environ.get("SG_LOG_DIR", ROOT / "logs"))
DB_PATH = DATA_DIR / "senior_guide.sqlite3"          # canonical records + snapshot history
RAW_DIR = DATA_DIR / "raw"                           # immutable raw responses, one folder per run
STATUS_FILE = DATA_DIR / "status" / "latest_run.json"  # machine-readable summary of the last ingest run
REFERENCE_DIR = DATA_DIR / "reference"               # files shipped with the app (FCSC CPI csv)
REGISTRY_YAML = ROOT / "ingest" / "registry.yaml"    # THE list of datasets and how to fetch them
# Sources approved through the onboarding agent (onboard/) are written here, never into the curated file above.
LOCAL_REGISTRY_YAML = Path(os.environ.get("SG_LOCAL_REGISTRY", str(DATA_DIR / "registry.local.yaml")))
# The onboarding agent only accepts sources on these official domains (suffix match). "Only official sources go
# in" is enforced here, in code: a scammer's website can never become a data source. Comma-separated to override.
OFFICIAL_DOMAINS = tuple(d.strip().lower() for d in os.environ.get(
    "SG_OFFICIAL_DOMAINS", ".gov.ae,bayanat.ae,uaecma.gov.ae,adx.ae,dfm.ae,centralbank.ae,dfsa.ae,adgm.com,vara.ae,"
    "fcsc.gov.ae,u.ae,data.gov.ae").split(",") if d.strip())
ONBOARD_MAX_BYTES = 8_000_000                          # a discovered file larger than this is not downloaded
FIXTURES_DIR = ROOT / "tests" / "fixtures"           # sample responses used by tests / demo mode

# --- data ingestion -----------------------------------------------------------------------
LIVE_DATA = _flag("SG_LIVE_DATA", "1")
REFRESH_ON_START = _flag("SG_REFRESH_ON_START", "1")
HTTP_TIMEOUT = float(os.environ.get("SG_HTTP_TIMEOUT", "30"))
HTTP_MAX_RETRIES = 3                 # per request, only for network errors / 429 / 5xx
HTTP_BACKOFF_BASE = 1.5              # seconds; doubled each retry, plus random jitter
PAGE_DELAY = 0.6                     # seconds between pages: be polite to the government server
MAX_PAGES = 200                      # per dataset; hitting it is an ERROR, never a silent stop
MAX_REQUESTS_PER_RUN = 600           # request budget for one whole ingest run
ALLOW_BROWSER = _flag("SG_ALLOW_BROWSER", "0")
USER_AGENT = os.environ.get(
    "SG_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 SeniorGuide/3.0")
SNAPSHOTS_TO_KEEP = 3                # older snapshots of a dataset are pruned after a publish
STALE_AFTER_HOURS = 72               # answers say "this copy is X days old" after this

# --- logging ------------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("SG_LOG_LEVEL", "INFO").upper()

# --- AI (optional; the app works fully without it) -----------------------------------------
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
LLM_TIMEOUT = 25
MAX_REPLY_TOKENS = 700

# --- server -------------------------------------------------------------------------------
HOST = os.environ.get("SG_HOST", "127.0.0.1")
PORT = int(os.environ.get("SG_PORT", "8000"))
DEBUG = _flag("SG_DEBUG", "0")
MAX_TEXT_CHARS = 1500
MAX_BODY_BYTES = 12_000
MAX_AUDIO_BYTES = 3_000_000          # ~2 minutes of compressed speech for /api/transcribe

# --- conversation -------------------------------------------------------------------------
MAX_WORKFLOW_STEPS = 8               # tool calls per turn; a workflow that needs more is a bug
MAX_HISTORY_TURNS = 12
SESSION_IDLE_MINUTES = 60            # a conversation's working memory is dropped after this

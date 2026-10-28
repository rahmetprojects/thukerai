"""
tests/conftest.py - shared test setup: a private data folder, no network, no AI keys
===================================================================================

WHAT IT DOES
    * points SG_DATA_DIR / SG_LOG_DIR at a temporary folder BEFORE config.py is imported, so tests
      never touch your real database or logs;
    * removes AI keys, so every test exercises the deterministic (offline) path;
    * provides `fixture_service`: a Service whose database was filled by the REAL ingest pipeline from
      tests/fake_cma.py (an imitation of the CMA site built from responses captured on the live site).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="sg_test_"))
os.environ["SG_DATA_DIR"] = str(_TMP / "data")
os.environ["SG_LOG_DIR"] = str(_TMP / "logs")
os.environ["SG_REFRESH_ON_START"] = "0"
for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)
(_TMP / "data" / "reference").mkdir(parents=True, exist_ok=True)
shutil.copy(ROOT / "data" / "reference" / "fcsc_cpi_annual.csv", _TMP / "data" / "reference" / "fcsc_cpi_annual.csv")
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def fixture_service():
    from ingest.pipeline import run_ingest
    from service import build_service
    from store.db import Database
    from tests.fake_cma import FakeCMA
    db = Database(_TMP / "data" / "fixture.sqlite3")
    run_ingest(db=db, transport=FakeCMA().transport(), sleep=lambda _s: None, use_lock=False)
    return build_service(db)


@pytest.fixture()
def fresh_db(tmp_path):
    from store.db import Database
    return Database(tmp_path / "t.sqlite3")

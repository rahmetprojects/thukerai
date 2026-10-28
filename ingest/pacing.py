"""
ingest/pacing.py - the politeness and safety limits shared by all adapters
==========================================================================

WHAT IT DOES
    Two tiny rules in one place, so every adapter obeys the same limits:
        page_delay()            seconds to wait between pages (config.PAGE_DELAY): be kind to a government server
        check_page_cap(page)    stop with an ERROR (never silently) if a dataset needs more than
                                config.MAX_PAGES pages: that means pagination is broken, not that we are done.
"""
from __future__ import annotations

import config

from .responses import FetchError


def page_delay() -> float:
    return config.PAGE_DELAY


def check_page_cap(page: int, dataset_id: str) -> None:
    if page > config.MAX_PAGES:
        raise FetchError("pagination", f"{dataset_id}: more than {config.MAX_PAGES} pages; refusing to continue "
                         "(pagination is probably broken). Raise SG MAX_PAGES only if the dataset really grew.")

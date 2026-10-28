"""
run_server.py - start the app: the web server, the screen, and background data refresh
====================================================================================

    python run_server.py          then open the address it prints, in Chrome or Edge

WHAT HAPPENS
    1. logging is set up (console + logs/app.log)
    2. the service is wired (database, retrieval, calculators, coordinator, optional AI) - service.py
    3. the background scheduler checks which datasets are missing or old and refreshes ONLY those,
       without blocking conversations (watch the console or open the "Data" page in the app)
    4. the server listens on http://127.0.0.1:8000 (or the next free port)

FIRST RUN TIP
    Run the data pipeline once in the foreground to see everything it does:
        python -m ingest.run
"""
from __future__ import annotations

import socket

import uvicorn

import config
from api.app import create_app
from ingest.scheduler import Scheduler
from logsetup import setup_logging
from service import build_service


def free_port(host: str, start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port between {start} and {start + 19}; set SG_PORT.")


def main() -> None:
    setup_logging()
    svc = build_service()
    sched = Scheduler(svc.db)
    sched.start()
    port = free_port(config.HOST, config.PORT)
    available = sum(1 for d in svc.db.status() if d["snapshot"])
    print(f"\n  BucketTreasure is running:  http://{config.HOST}:{port}")
    print(f"  AI helper: {svc.llm.provider}{' (' + svc.llm.model + ')' if svc.llm.model else ' - answers use tested templates'}")
    print(f"  Official datasets available now: {available}; refresh due: {', '.join(sched.due()) or 'none'}")
    print("  Data status page: Data button in the app, or  python -m ingest.report\n")
    uvicorn.run(create_app(svc, sched), host=config.HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()

"""Console mode: collector + watchdog agent without a window.

Handy for leaving the agent running in a terminal (or a scheduled task) and
reading the alerts later from the app or straight from SQLite.
"""

from __future__ import annotations

import os
import queue
import signal
import time
from datetime import datetime

from .agent import WatchdogAgent
from .collector import Collector
from .database import Database

COLORS = {"info": "\033[94m", "warning": "\033[93m", "critical": "\033[91m"}
RESET = "\033[0m"


def run_headless(args) -> int:
    if os.name == "nt":
        os.system("")  # turns on ANSI colours in the classic Windows console
    db = Database(args.db)
    db.resolve_open_alerts(time.time())
    rules = db.load_rules(enabled_only=True)
    db.close()

    collector = Collector(args.db, interval=args.interval, retention_hours=args.retention, top_n=args.top)
    agent = WatchdogAgent(args.db)
    collector.listeners.append(agent.submit)
    agent.start()
    collector.start()

    print(f"Watchdog agent running locally: {len(rules)} enabled rules, sampling every {args.interval:g}s.")
    print(f"Database: {args.db}")
    print("Press Ctrl+C to stop.\n")

    stopping = False

    def _stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    deadline = time.monotonic() + args.run_for if getattr(args, "run_for", 0) else None
    try:
        while not stopping:
            if deadline is not None and time.monotonic() >= deadline:
                break
            try:
                alert = agent.outbox.get(timeout=0.5)
            except queue.Empty:
                if collector.error or agent.error:
                    print(f"stopped: {collector.error or agent.error}")
                    break
                continue
            stamp = datetime.fromtimestamp(alert.ts).strftime("%Y-%m-%d %H:%M:%S")
            color = COLORS.get(alert.severity, "")
            print(f"{stamp}  {color}{alert.severity.upper():8}{RESET}  {alert.title}: {alert.message}", flush=True)
    finally:
        collector.stop()
        agent.stop()
        collector.join(timeout=3)
        agent.join(timeout=3)
        print(f"\nStopped after {collector.samples_written} samples.")
    return 1 if (collector.error or agent.error) else 0

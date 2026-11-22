"""Console mode: collector + watchdog agent without a window.

Handy for leaving the agent running in a terminal (or a scheduled task) and
reading the alerts later from the app or straight from SQLite.
"""

from __future__ import annotations

import os
import queue
import signal
import sys
import time
from datetime import datetime

from .agent import WatchdogAgent
from .collector import Collector
from .database import Database
from .webhooks import WebhookDispatcher, endpoints_from_args

COLORS = {"info": "\033[94m", "warning": "\033[93m", "critical": "\033[91m"}
RESET = "\033[0m"


def run_headless(args) -> int:
    if os.name == "nt":
        os.system("")  # turns on ANSI colours in the classic Windows console
    db = Database(args.db)
    db.resolve_open_alerts(time.time())
    rules = db.load_rules(enabled_only=True)
    db_hooks = db.load_webhooks()
    db.close()

    collector = Collector(args.db, interval=args.interval, retention_hours=args.retention, top_n=args.top)
    agent = WatchdogAgent(args.db)
    collector.listeners.append(agent.submit)
    webhooks = WebhookDispatcher(args.db, endpoints_from_args(
        getattr(args, "webhook", []), getattr(args, "webhook_min_severity", "warning")))
    agent.listeners.append(webhooks.submit)
    webhooks.start()
    agent.start()
    collector.start()

    print(f"Watchdog agent running locally: {len(rules)} enabled rules, sampling every {args.interval:g}s.")
    print(f"Database: {args.db}")
    hooks = len(webhooks.static_endpoints) + len(db_hooks)
    if hooks:
        print(f"Forwarding alerts to {hooks} webhook endpoint(s).")
    print(f"Running for {args.run_for:g}s.\n" if args.run_for else "Press Ctrl+C to stop.\n")

    stopping = False

    def _stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, _stop) for sig in (signal.SIGINT, signal.SIGTERM)}  # SIGTERM = docker stop
    use_color = sys.stdout.isatty()
    deadline = time.monotonic() + args.run_for if args.run_for else None
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
            color, reset = (COLORS.get(alert.severity, ""), RESET) if use_color else ("", "")
            print(f"{stamp}  {color}{alert.severity.upper():8}{reset}  {alert.title}: {alert.message}", flush=True)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        collector.stop()
        agent.stop()
        collector.join(timeout=3)
        agent.join(timeout=3)
        webhooks.stop()  # delivers whatever is still queued (e.g. resolve events) first
        webhooks.join(timeout=15)
        try:
            closing = Database(args.db)
            closing.resolve_open_alerts(time.time())
            closing.close()
        except Exception:
            pass
        print(f"\nStopped after {collector.samples_written} samples.")
    return 1 if (collector.error or agent.error) else 0

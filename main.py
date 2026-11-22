"""Entry point for System Process Monitor.

Usage:
    python main.py [--interval SECONDS] [--db PATH] [--retention HOURS] [--top N]
                   [--no-agent] [--no-sound] [--headless [--run-for SECONDS]]
                   [--webhook URL ...] [--webhook-min-severity LEVEL]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="spm", description="System Process Monitor")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="sampling interval in seconds (default: 2)")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "monitor.db",
                        help="SQLite database file (default: ./data/monitor.db)")
    parser.add_argument("--retention", type=float, default=24.0,
                        help="hours of history to keep (default: 24)")
    parser.add_argument("--top", type=int, default=40,
                        help="processes stored per snapshot, by CPU and by memory (default: 40)")
    parser.add_argument("--no-agent", action="store_true",
                        help="disable the local watchdog agent (no rules, no alerts)")
    parser.add_argument("--no-sound", action="store_true",
                        help="do not play a sound when an alert pops up")
    parser.add_argument("--headless", action="store_true",
                        help="run collector + watchdog agent in the console, without the window")
    parser.add_argument("--run-for", type=float, default=0, metavar="SECONDS",
                        help="headless only: stop automatically after this many seconds")
    parser.add_argument("--webhook", action="append", default=[], metavar="URL",
                        help="POST alerts as JSON incidents to this URL (repeatable; "
                             "also read from the SPM_WEBHOOKS environment variable, comma separated)")
    parser.add_argument("--webhook-min-severity", choices=("info", "warning", "critical"),
                        default=os.environ.get("SPM_WEBHOOK_MIN_SEVERITY", "warning"),
                        help="lowest severity forwarded to --webhook URLs (default: warning)")
    args = parser.parse_args(argv)
    args.interval = max(0.5, args.interval)
    env_hooks = [u for u in os.environ.get("SPM_WEBHOOKS", "").split(",") if u.strip()]
    args.webhook = list(args.webhook) + env_hooks
    return args


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    if args.headless:
        from spm.headless import run_headless
        return run_headless(args)
    try:
        from spm.app import App
    except ImportError as exc:
        print(f"Missing dependency: {exc}\nInstall with: pip install -r requirements.txt", file=sys.stderr)
        return 1
    App(args).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

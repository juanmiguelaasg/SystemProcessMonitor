"""Health probe used by the Docker image.

Exits 0 when the collector wrote a snapshot recently, 1 otherwise:

    python -m spm.healthcheck --db /data/monitor.db --max-age 30
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path


def check(db_path: str | Path, max_age: float = 30.0) -> tuple[bool, str]:
    path = Path(db_path).resolve()
    if not path.exists():
        return False, f"{path} does not exist yet"
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            row = conn.execute("SELECT MAX(ts) FROM system_snapshots").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"database error: {exc}"
    if not row or row[0] is None:
        return False, "no snapshots recorded yet"
    age = time.time() - row[0]
    return age <= max_age, f"last snapshot {age:.0f}s ago"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="System Process Monitor health probe")
    parser.add_argument("--db", default="/data/monitor.db")
    parser.add_argument("--max-age", type=float, default=30.0,
                        help="seconds since the last snapshot before reporting unhealthy")
    args = parser.parse_args(argv)
    ok, message = check(args.db, args.max_age)
    print(("healthy: " if ok else "unhealthy: ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

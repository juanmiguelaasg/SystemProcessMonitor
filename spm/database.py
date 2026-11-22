"""SQLite persistence layer: schema bootstrap, inserts, reports and ad-hoc queries."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@dataclass
class Report:
    name: str
    title: str
    description: str
    sql: str


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


def load_reports(path: Path = SQL_DIR / "reports.sql") -> list[Report]:
    """Parse reports.sql into Report objects using its "-- key: value" headers."""
    reports: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^--\s*(name|title|description):\s*(.*)$", line)
        if header:
            key, value = header.groups()
            if key == "name":
                current = {"name": value.strip(), "title": value.strip(), "description": "", "sql": []}
                reports.append(current)
            elif current is not None:
                current[key] = value.strip()
        elif current is not None:
            current["sql"].append(line)
    return [
        Report(r["name"], r["title"], r["description"], "\n".join(r["sql"]).strip())
        for r in reports
    ]


class Database:
    """Thin wrapper around a sqlite3 connection.

    A writable instance bootstraps the schema. A read-only instance (used by the
    SQL console) opens the file with mode=ro so user queries can never modify data.
    """

    def __init__(self, path: str | Path, readonly: bool = False) -> None:
        self.path = Path(path).resolve()
        self.readonly = readonly
        if readonly:
            self.conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), timeout=15)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
            self.conn.commit()

    # -- writes ----------------------------------------------------------------
    def start_session(self, hostname: str, platform: str, cpu_count: int, total_memory: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO sessions (started_at, hostname, platform, cpu_count, total_memory)
                   VALUES (?, ?, ?, ?, ?)""",
                (time.time(), hostname, platform, cpu_count, total_memory),
            )
        return int(cur.lastrowid)

    def insert_snapshot(self, session_id: int, s) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO system_snapshots
                   (session_id, ts, cpu_percent, mem_percent, mem_used, swap_percent, disk_percent,
                    disk_read_rate, disk_write_rate, net_sent_rate, net_recv_rate,
                    process_count, thread_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, s.ts, s.cpu, s.mem_percent, s.mem_used, s.swap_percent, s.disk_percent,
                 s.disk_read_rate, s.disk_write_rate, s.net_sent_rate, s.net_recv_rate,
                 s.process_count, s.thread_count),
            )
        return int(cur.lastrowid)

    def insert_process_samples(self, snapshot_id: int, ts: float, processes: Iterable) -> None:
        rows = [
            (snapshot_id, ts, p.pid, p.name, p.username, p.cpu, p.rss, p.mem_percent, p.threads, p.status)
            for p in processes
        ]
        with self.conn:
            self.conn.executemany(
                """INSERT INTO process_samples
                   (snapshot_id, ts, pid, name, username, cpu_percent, mem_rss, mem_percent, threads, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

    def log_event(self, pid: int, name: str, action: str, result: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO process_events (ts, pid, name, action, result) VALUES (?, ?, ?, ?, ?)",
                (time.time(), pid, name, action, result),
            )

    def prune(self, retention_hours: float) -> int:
        """Delete history older than the retention window. Returns removed snapshots."""
        cutoff = time.time() - retention_hours * 3600
        with self.conn:
            self.conn.execute("DELETE FROM process_samples WHERE ts < ?", (cutoff,))
            cur = self.conn.execute("DELETE FROM system_snapshots WHERE ts < ?", (cutoff,))
            self.conn.execute(
                """DELETE FROM sessions
                   WHERE started_at < ?
                     AND id NOT IN (SELECT DISTINCT session_id FROM system_snapshots)""",
                (cutoff,),
            )
        return cur.rowcount

    # -- reads -----------------------------------------------------------------
    def stats(self) -> dict[str, int]:
        row = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM system_snapshots),
                      (SELECT COUNT(*) FROM process_samples),
                      (SELECT COUNT(*) FROM sessions)"""
        ).fetchone()
        return {"snapshots": row[0], "samples": row[1], "sessions": row[2]}

    def recent_history(self, seconds: float, limit: int) -> list[tuple]:
        """Most recent snapshots (oldest first) used to pre-fill the dashboard charts."""
        rows = self.conn.execute(
            """SELECT cpu_percent,
                      mem_percent,
                      disk_read_rate + disk_write_rate,
                      net_recv_rate + net_sent_rate
               FROM system_snapshots
               WHERE ts >= ?
               ORDER BY ts DESC
               LIMIT ?""",
            (time.time() - seconds, limit),
        ).fetchall()
        return rows[::-1]

    def file_size(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def schema(self) -> list[tuple[str, str, list[tuple[str, str]]]]:
        """Return [(name, type, [(column, decl_type), ...]), ...] for tables and views."""
        objects = self.conn.execute(
            """SELECT name, type FROM sqlite_master
               WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        result = []
        for name, kind in objects:
            cols = [(c[1], c[2] or "") for c in self.conn.execute(f'PRAGMA table_info("{name}")')]
            result.append((name, kind, cols))
        return result

    def query(self, sql: str, params: dict | Sequence | None = None,
              max_rows: int = 2000, timeout_s: float = 8.0) -> QueryResult:
        """Run a single statement, returning at most max_rows rows.

        A progress handler aborts statements that run longer than timeout_s.
        """
        started = time.perf_counter()
        self.conn.set_progress_handler(
            lambda: 1 if time.perf_counter() - started > timeout_s else 0, 20_000
        )
        try:
            cur = self.conn.execute(sql, params or {})
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1) if columns else []
        finally:
            self.conn.set_progress_handler(None, 0)
        truncated = len(rows) > max_rows
        return QueryResult(columns, rows[:max_rows], truncated, (time.perf_counter() - started) * 1000)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

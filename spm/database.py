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

    # -- watchdog rules and alerts ---------------------------------------------
    RULE_COLUMNS = "id, name, metric, threshold, duration_s, cooldown_s, severity, target, enabled"

    def load_rules(self, enabled_only: bool = False) -> list[tuple]:
        sql = f"SELECT {self.RULE_COLUMNS} FROM alert_rules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self.conn.execute(sql + " ORDER BY id").fetchall()

    def save_rule(self, rule_id: int | None, name: str, metric: str, threshold: float,
                  duration_s: float, cooldown_s: float, severity: str,
                  target: str | None, enabled: bool) -> int:
        values = (name, metric, threshold, duration_s, cooldown_s, severity, target or None, int(enabled))
        with self.conn:
            if rule_id is None:
                cur = self.conn.execute(
                    """INSERT INTO alert_rules
                       (name, metric, threshold, duration_s, cooldown_s, severity, target, enabled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", values)
                return int(cur.lastrowid)
            self.conn.execute(
                """UPDATE alert_rules
                   SET name = ?, metric = ?, threshold = ?, duration_s = ?, cooldown_s = ?,
                       severity = ?, target = ?, enabled = ?
                   WHERE id = ?""", (*values, rule_id))
        return rule_id

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE alert_rules SET enabled = ? WHERE id = ?", (int(enabled), rule_id))

    def delete_rule(self, rule_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))

    def insert_alert(self, ts: float, rule_id: int | None, rule_name: str, metric: str,
                     severity: str, title: str, message: str, value: float | None,
                     pid: int | None, process: str | None) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO alerts
                   (ts, rule_id, rule_name, metric, severity, title, message, value, pid, process)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, rule_id, rule_name, metric, severity, title, message, value, pid, process))
        return int(cur.lastrowid)

    def resolve_alert(self, alert_id: int, ts: float) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE alerts SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL", (ts, alert_id))

    def resolve_open_alerts(self, ts: float) -> None:
        """Close alerts left open by a previous run (the agent lost its state when it stopped)."""
        with self.conn:
            self.conn.execute("UPDATE alerts SET resolved_at = ? WHERE resolved_at IS NULL", (ts,))

    def acknowledge(self, alert_id: int | None = None) -> None:
        with self.conn:
            if alert_id is None:
                self.conn.execute("UPDATE alerts SET acknowledged = 1 WHERE acknowledged = 0")
            else:
                self.conn.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))

    def recent_alerts(self, limit: int = 300) -> list[tuple]:
        return self.conn.execute(
            """SELECT id, ts, severity, rule_name, message, resolved_at, acknowledged
               FROM alerts ORDER BY ts DESC, id DESC LIMIT ?""", (limit,)).fetchall()

    def alert_counts(self) -> dict[str, int]:
        row = self.conn.execute(
            """SELECT COALESCE(SUM(resolved_at IS NULL), 0),
                      COALESCE(SUM(acknowledged = 0), 0),
                      COALESCE(SUM(ts >= ?), 0),
                      COALESCE(SUM(severity = 'critical' AND acknowledged = 0), 0)
               FROM alerts""", (time.time() - 86400,)).fetchone()
        return {"open": row[0], "unacknowledged": row[1], "last_24h": row[2], "critical": row[3]}

    def get_alert(self, alert_id: int) -> dict | None:
        cur = self.conn.execute(
            """SELECT id, ts, rule_id, rule_name, metric, severity, title, message, value,
                      pid, process, resolved_at, acknowledged
               FROM alerts WHERE id = ?""", (alert_id,))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None

    # -- baselines -------------------------------------------------------------
    def load_baselines(self) -> dict[str, tuple[float, float, int, float]]:
        return {row[0]: tuple(row[1:]) for row in self.conn.execute(
            "SELECT series, mean, var, samples, updated_at FROM baselines")}

    def save_baselines(self, rows: Iterable[tuple]) -> None:
        """rows: (series, mean, var, std, samples, updated_at)."""
        with self.conn:
            self.conn.executemany(
                """INSERT INTO baselines (series, mean, var, std, samples, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(series) DO UPDATE SET
                       mean = excluded.mean, var = excluded.var, std = excluded.std,
                       samples = excluded.samples, updated_at = excluded.updated_at""",
                list(rows))

    # -- webhooks --------------------------------------------------------------
    def load_webhooks(self, enabled_only: bool = True) -> list[tuple[str, str, str]]:
        """[(name, url, min_severity), ...]"""
        sql = "SELECT name, url, min_severity FROM webhook_endpoints"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self.conn.execute(sql + " ORDER BY id").fetchall()

    def add_webhook(self, name: str, url: str, min_severity: str = "warning") -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO webhook_endpoints (name, url, min_severity) VALUES (?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET name = excluded.name,
                       min_severity = excluded.min_severity, enabled = 1""",
                (name, url, min_severity))

    def log_delivery(self, alert_id: int | None, endpoint: str, event: str, ok: bool,
                     status_code: int | None, attempts: int, error: str | None) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO webhook_deliveries
                   (ts, alert_id, endpoint, event, ok, status_code, attempts, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), alert_id, endpoint, event, int(ok), status_code, attempts, error))

    # -- maintenance -----------------------------------------------------------
    def prune(self, retention_hours: float) -> int:
        """Delete history older than the retention window. Returns removed snapshots."""
        cutoff = time.time() - retention_hours * 3600
        with self.conn:
            # alerts are tiny, keep them for 30 days or the retention window, whichever is longer
            self.conn.execute("DELETE FROM alerts WHERE ts < ?",
                              (min(cutoff, time.time() - 30 * 86400),))
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

import sqlite3
import time

import pytest

from conftest import make_proc, record
from spm.database import Database


def test_schema_created(db):
    names = {name for name, _kind, _cols in db.schema()}
    assert {"sessions", "system_snapshots", "process_samples", "process_events"} <= names
    assert {"v_process_stats", "v_system_minutely"} <= names


def test_schema_is_idempotent(db):
    # opening the same file again must not blow up on CREATE statements
    again = Database(db.path)
    again.close()


def test_wal_mode(db):
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_insert_snapshot_and_samples(db):
    sess = db.start_session("host", "Windows 11", 8, 16 * 1024 ** 3)
    record(db, sess, time.time(), procs=[make_proc(1), make_proc(2, "b.exe")])

    assert db.stats() == {"snapshots": 1, "samples": 2, "sessions": 1}
    names = [r[0] for r in db.conn.execute("SELECT name FROM process_samples ORDER BY pid")]
    assert names == ["app.exe", "b.exe"]


def test_prune_removes_old_rows_only(db):
    sess = db.start_session("host", "x", 4, 1)
    now = time.time()
    record(db, sess, now - 48 * 3600, procs=[make_proc()])
    record(db, sess, now - 10, procs=[make_proc()])

    removed = db.prune(retention_hours=24)

    assert removed == 1
    assert db.stats()["snapshots"] == 1
    assert db.stats()["samples"] == 1


def test_prune_drops_empty_old_sessions(db):
    db.conn.execute(
        "INSERT INTO sessions (started_at, hostname, platform, cpu_count, total_memory) VALUES (?, 'h', 'p', 1, 1)",
        (time.time() - 100 * 3600,))
    db.conn.commit()
    db.prune(24)
    assert db.stats()["sessions"] == 0


def test_log_event(db):
    db.log_event(1234, "notepad.exe", "terminate", "terminated")
    row = db.conn.execute("SELECT pid, name, action, result FROM process_events").fetchone()
    assert row == (1234, "notepad.exe", "terminate", "terminated")


def test_recent_history_is_oldest_first(db):
    sess = db.start_session("h", "p", 1, 1)
    now = time.time()
    for i, cpu in enumerate([10, 20, 30]):
        record(db, sess, now - 30 + i * 10, cpu=cpu)

    rows = db.recent_history(seconds=3600, limit=10)
    assert [r[0] for r in rows] == [10, 20, 30]
    # disk/net columns are summed read+write and recv+sent
    assert rows[0][2] == 3000.0
    assert rows[0][3] == 1000.0


def test_recent_history_respects_limit_and_window(db):
    sess = db.start_session("h", "p", 1, 1)
    now = time.time()
    record(db, sess, now - 7200, cpu=99)  # outside the window
    for i in range(5):
        record(db, sess, now - i, cpu=i)
    rows = db.recent_history(seconds=600, limit=3)
    assert len(rows) == 3
    assert 99 not in [r[0] for r in rows]


def test_readonly_connection_rejects_writes(db, ro_db):
    with pytest.raises(sqlite3.OperationalError):
        ro_db.query("DELETE FROM sessions")


def test_query_truncates(db, ro_db):
    sess = db.start_session("h", "p", 1, 1)
    now = time.time()
    for i in range(30):
        record(db, sess, now - i)
    res = ro_db.query("SELECT * FROM system_snapshots", max_rows=10)
    assert len(res.rows) == 10
    assert res.truncated
    assert "cpu_percent" in res.columns


def test_query_with_named_params(db, ro_db):
    sess = db.start_session("h", "p", 1, 1)
    now = time.time()
    record(db, sess, now - 5000)
    record(db, sess, now - 10)
    res = ro_db.query("SELECT COUNT(*) FROM system_snapshots WHERE ts >= :since", {"since": now - 60})
    assert res.rows[0][0] == 1


def test_query_timeout(ro_db):
    # recursive CTE that would run basically forever
    slow = """
        WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n)
        SELECT COUNT(*) FROM n
    """
    with pytest.raises(sqlite3.OperationalError, match="interrupt"):
        ro_db.query(slow, timeout_s=0.3)
    # connection should still be usable afterwards
    assert ro_db.query("SELECT 1").rows == [(1,)]


def test_query_rejects_multiple_statements(ro_db):
    with pytest.raises((sqlite3.Error, sqlite3.Warning)):
        ro_db.query("SELECT 1; SELECT 2")


def test_file_size_counts_wal(db):
    assert db.file_size() > 0

import time

import pytest

from conftest import make_proc, record
from spm.database import load_reports
from spm.views.analytics import inline_since

REPORTS = load_reports()


def test_reports_parsed():
    names = [r.name for r in REPORTS]
    assert names == ["top_cpu", "top_memory", "load_per_minute", "cpu_spikes",
                     "memory_growth", "sessions", "events"]
    for r in REPORTS:
        assert r.title and r.description
        assert r.sql.lstrip().upper().startswith(("SELECT", "WITH"))
        assert "-- name:" not in r.sql


@pytest.fixture
def filled(db):
    """two snapshots, chrome grows from 100MB to 300MB, second snapshot is a cpu spike"""
    sess = db.start_session("h", "p", 4, 1)
    now = time.time()
    mb = 1024 * 1024
    record(db, sess, now - 120, cpu=20, procs=[
        make_proc(1, "chrome.exe", cpu=5, rss=60 * mb),
        make_proc(2, "chrome.exe", cpu=1, rss=40 * mb),
        make_proc(3, "code.exe", cpu=2, rss=200 * mb),
    ])
    record(db, sess, now - 60, cpu=75, procs=[
        make_proc(1, "chrome.exe", cpu=50, rss=200 * mb),
        make_proc(2, "chrome.exe", cpu=2, rss=100 * mb),
        make_proc(3, "code.exe", cpu=4, rss=200 * mb),
    ])
    db.log_event(3, "code.exe", "terminate", "access denied")
    return db


def run(ro_db, name, since=0):
    report = next(r for r in REPORTS if r.name == name)
    res = ro_db.query(report.sql, {"since": since})
    return [dict(zip(res.columns, row)) for row in res.rows]


@pytest.mark.parametrize("report", REPORTS, ids=lambda r: r.name)
def test_every_report_runs_on_empty_db(ro_db, report):
    res = ro_db.query(report.sql, {"since": 0})
    assert res.columns


def test_top_cpu(filled, ro_db):
    rows = run(ro_db, "top_cpu")
    assert rows[0]["process"] == "chrome.exe"
    assert rows[0]["instances"] == 2
    assert rows[0]["peak_cpu"] == 50


def test_top_memory_sums_instances(filled, ro_db):
    rows = run(ro_db, "top_memory")
    chrome = next(r for r in rows if r["process"] == "chrome.exe")
    assert chrome["peak_mem_mb"] == 300.0
    assert chrome["max_instances"] == 2


def test_cpu_spikes_picks_culprit(filled, ro_db):
    rows = run(ro_db, "cpu_spikes")
    assert len(rows) == 1  # the 20% snapshot is below the threshold
    assert rows[0]["top_process"] == "chrome.exe"
    assert rows[0]["pid"] == 1


def test_memory_growth(filled, ro_db):
    rows = run(ro_db, "memory_growth")
    first = rows[0]
    assert first["process"] == "chrome.exe"
    assert first["start_mb"] == 100.0
    assert first["end_mb"] == 300.0
    assert first["growth_mb"] == 200.0
    code = next(r for r in rows if r["process"] == "code.exe")
    assert code["growth_mb"] == 0


def test_sessions_report(filled, ro_db):
    rows = run(ro_db, "sessions")
    assert rows[0]["snapshots"] == 2
    assert rows[0]["peak_cpu"] == 75


def test_events_report(filled, ro_db):
    rows = run(ro_db, "events")
    assert rows[0]["process"] == "code.exe"
    assert rows[0]["result"] == "access denied"


def test_since_filters(filled, ro_db):
    assert run(ro_db, "top_cpu", since=time.time() + 10) == []


def test_inline_since_all_time():
    assert inline_since("WHERE ts >= :since", None) == "WHERE ts >= 0"


def test_inline_since_window(filled, ro_db):
    report = next(r for r in REPORTS if r.name == "top_cpu")
    sql = inline_since(report.sql, 3600)
    assert ":since" not in sql
    # the inlined version should give the same answer as the bound one
    assert ro_db.query(sql).rows == ro_db.query(report.sql, {"since": time.time() - 3600}).rows

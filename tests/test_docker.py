"""Checks that keep the Docker image honest without needing Docker installed."""

import fnmatch
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from conftest import record
from spm import healthcheck
from spm.headless import run_headless

ROOT = Path(__file__).resolve().parent.parent


def test_headless_path_does_not_need_tk():
    # the image is python:slim with psutil only - no tkinter, no customtkinter
    code = (
        "import sys, main, spm.headless, spm.healthcheck;"
        "bad = [m for m in ('tkinter', 'customtkinter') if m in sys.modules];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


def test_agent_requirements_are_a_subset():
    agent = (ROOT / "requirements-agent.txt").read_text().split()
    full = (ROOT / "requirements.txt").read_text().split()
    assert agent and set(agent) <= set(full)


def test_dockerignore_keeps_what_the_image_copies():
    ignored = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines() if l.strip()]
    for needed in ("main.py", "spm", "sql", "requirements-agent.txt"):
        assert not any(fnmatch.fnmatch(needed, pat) for pat in ignored), needed


def test_dockerfile_points_at_real_things():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"spm.healthcheck"' in dockerfile
    assert '"--headless"' in dockerfile
    assert (ROOT / "spm" / "healthcheck.py").exists()


def test_healthcheck_missing_db(tmp_path):
    ok, msg = healthcheck.check(tmp_path / "nope.db")
    assert not ok and "does not exist" in msg


def test_healthcheck_empty_db(db):
    ok, msg = healthcheck.check(db.path)
    assert not ok and "no snapshots" in msg


def test_healthcheck_fresh_and_stale(db):
    sess = db.start_session("h", "p", 1, 1)
    record(db, sess, time.time() - 120)
    assert healthcheck.check(db.path, max_age=30)[0] is False
    record(db, sess, time.time() - 2)
    ok, msg = healthcheck.check(db.path, max_age=30)
    assert ok, msg
    assert healthcheck.main(["--db", str(db.path)]) == 0


def test_headless_runs_and_stops(tmp_path, capsys):
    from spm.database import Database

    path = tmp_path / "headless.db"
    db = Database(path)
    with db.conn:
        db.conn.execute("DELETE FROM alert_rules")
    db.save_rule(None, "anything running", "process_count", 1, 0, 3600, "info", None, True)
    db.close()

    args = SimpleNamespace(db=path, interval=0.5, retention=1, top=10, run_for=6)
    assert run_headless(args) == 0

    out = capsys.readouterr().out
    assert "anything running" in out
    assert "\033[" not in out  # no colour codes when stdout is not a terminal
    assert healthcheck.check(path)[0]
    check = Database(path, readonly=True)
    # open alerts are closed on shutdown
    assert check.query("SELECT COUNT(*) FROM alerts WHERE resolved_at IS NULL").rows[0][0] == 0
    check.close()

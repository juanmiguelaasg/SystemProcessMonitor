import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spm.collector import ProcessInfo, Sample  # noqa: E402
from spm.database import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def ro_db(db):
    r = Database(db.path, readonly=True)
    yield r
    r.close()


def make_proc(pid=100, name="app.exe", cpu=1.0, rss=50 * 1024 * 1024, user="me"):
    return ProcessInfo(pid=pid, name=name, username=user, cpu=cpu, rss=rss,
                       mem_percent=1.0, threads=4, status="running", create_time=time.time() - 60)


def make_sample(ts=None, cpu=10.0, mem=50.0, processes=None):
    processes = processes or []
    return Sample(
        ts=ts if ts is not None else time.time(),
        cpu=cpu, per_cpu=[cpu, cpu], cpu_freq=3000.0,
        mem_percent=mem, mem_used=8 * 1024 ** 3, mem_total=16 * 1024 ** 3,
        swap_percent=5.0, disk_percent=60.0, disk_used=1, disk_total=2,
        disk_read_rate=1000.0, disk_write_rate=2000.0,
        net_sent_rate=300.0, net_recv_rate=700.0,
        process_count=len(processes), thread_count=sum(p.threads for p in processes),
        processes=processes,
    )


def record(db, session_id, ts, cpu=10.0, procs=None):
    """insert one snapshot + its process rows, returns snapshot id"""
    s = make_sample(ts=ts, cpu=cpu, processes=procs or [])
    sid = db.insert_snapshot(session_id, s)
    db.insert_process_samples(sid, ts, s.processes)
    return sid

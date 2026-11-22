import os
import time

import psutil
import pytest

from conftest import make_proc
from spm import winproc
from spm.collector import Collector


@pytest.fixture
def collector(tmp_path):
    return Collector(tmp_path / "c.db", interval=0.5, top_n=2)


def test_interval_has_a_floor(tmp_path):
    assert Collector(tmp_path / "x.db", interval=0.01).interval == 0.5


def test_heaviest_is_union_of_cpu_and_memory(collector):
    procs = [
        make_proc(1, cpu=90, rss=1),
        make_proc(2, cpu=80, rss=2),
        make_proc(3, cpu=0, rss=900),
        make_proc(4, cpu=0, rss=800),
        make_proc(5, cpu=0, rss=3),
    ]
    picked = {p.pid for p in collector._heaviest(procs)}
    assert picked == {1, 2, 3, 4}


def test_heaviest_no_duplicates(collector):
    procs = [make_proc(1, cpu=90, rss=900), make_proc(2, cpu=1, rss=1)]
    pids = [p.pid for p in collector._heaviest(procs)]
    assert sorted(pids) == [1, 2]


def test_processes_contains_ourselves(collector):
    collector._processes()
    procs = collector._processes()
    me = next((p for p in procs if p.pid == os.getpid()), None)
    assert me is not None
    assert me.rss > 0
    assert me.threads >= 1
    assert all(p.pid != 0 for p in procs)
    assert all(0 <= p.cpu <= 100 for p in procs)


def test_psutil_fallback_matches(collector):
    fast = {p.pid for p in collector._processes()}
    slow = {p.pid for p in collector._processes_psutil()}
    # processes come and go between the two calls, so just require a big overlap
    assert len(fast & slow) / max(len(slow), 1) > 0.9


def test_username_cache_gets_pruned(collector):
    collector._usernames[(999999999, 1.0)] = "ghost"
    collector._processes()
    collector._processes()
    assert (999999999, 1.0) not in collector._usernames


def test_cpu_percent_sees_busy_loop(collector):
    collector._processes()
    end = time.perf_counter() + 0.6
    while time.perf_counter() < end:
        pass
    me = next(p for p in collector._processes() if p.pid == os.getpid())
    # one core pegged -> roughly 100 / cpu_count, allow lots of slack for CI
    assert me.cpu > 100 / collector.cpu_count * 0.3


@pytest.mark.skipif(not winproc.AVAILABLE, reason="windows only")
def test_winproc_snapshot():
    snap = winproc.snapshot()
    by_pid = {p.pid: p for p in snap}
    assert os.getpid() in by_pid
    me = by_pid[os.getpid()]
    assert me.name.lower().startswith("python")
    assert me.ppid == os.getppid()
    assert abs(me.create_time - psutil.Process().create_time()) < 2
    assert me.working_set > 0


def test_collector_thread_writes_to_db(tmp_path):
    from spm.database import Database

    path = tmp_path / "live.db"
    Database(path).close()
    c = Collector(path, interval=0.5)
    c.start()
    deadline = time.time() + 15
    while c.samples_written < 2 and time.time() < deadline and c.error is None:
        time.sleep(0.1)
    c.stop()
    c.join(5)

    assert c.error is None
    assert c.samples_written >= 2
    s = c.latest
    assert 0 <= s.cpu <= 100
    assert s.process_count == len(s.processes) > 0

    ro = Database(path, readonly=True)
    stats = ro.stats()
    ro.close()
    assert stats["snapshots"] >= 2
    assert stats["samples"] > 0

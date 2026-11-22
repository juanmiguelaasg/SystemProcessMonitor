import pytest

from conftest import make_proc, make_sample
from spm.agent import METRICS, Rule, WatchdogAgent

MB = 1024 * 1024
T0 = 1_800_000_000.0


@pytest.fixture
def agent(db):
    # start from an empty rule set, the defaults would only get in the way
    with db.conn:
        db.conn.execute("DELETE FROM alert_rules")
    return WatchdogAgent(db=db)


def add_rule(agent, name="rule", metric="system_cpu", threshold=90, duration=0, cooldown=0,
             severity="warning", target=None, enabled=True):
    rid = agent.db.save_rule(None, name, metric, threshold, duration, cooldown, severity, target, enabled)
    agent.reload_rules()
    return rid


def tick(agent, ts, cpu=10.0, mem=50.0, procs=None):
    return agent.evaluate(make_sample(ts=T0 + ts, cpu=cpu, mem=mem, processes=procs or []))


def open_alerts(db):
    return db.conn.execute("SELECT rule_name, pid FROM alerts WHERE resolved_at IS NULL").fetchall()


def test_default_rules_seeded_once(db):
    n = db.conn.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0]
    assert n >= 8
    db.delete_rule(db.load_rules()[0][0])
    from spm.database import Database
    Database(db.path).close()  # re-running the schema must not bring the deleted rule back
    assert db.conn.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0] == n - 1


def test_default_rules_use_known_metrics(db):
    for row in db.load_rules():
        assert Rule.from_row(row).metric in METRICS


def test_fires_immediately_without_duration(agent):
    add_rule(agent, threshold=90)
    assert tick(agent, 0, cpu=50) == []
    fired = tick(agent, 2, cpu=95)
    assert len(fired) == 1
    assert "95.0%" in fired[0].message
    assert agent.outbox.get_nowait().id == fired[0].id


def test_waits_for_duration(agent):
    add_rule(agent, threshold=90, duration=10)
    assert tick(agent, 0, cpu=95) == []
    assert tick(agent, 6, cpu=95) == []
    assert len(tick(agent, 10, cpu=95)) == 1


def test_dip_resets_duration(agent):
    add_rule(agent, threshold=90, duration=10)
    tick(agent, 0, cpu=95)
    tick(agent, 6, cpu=40)  # condition broke
    assert tick(agent, 12, cpu=95) == []
    assert len(tick(agent, 22, cpu=95)) == 1


def test_alert_stays_open_then_resolves(agent):
    add_rule(agent, name="cpu", threshold=90)
    tick(agent, 0, cpu=95)
    assert tick(agent, 2, cpu=97) == []  # no duplicate while still open
    assert open_alerts(agent.db) == [("cpu", None)]
    assert agent.open_alerts == 1
    tick(agent, 4, cpu=20)
    assert open_alerts(agent.db) == []
    assert agent.open_alerts == 0


def test_cooldown(agent):
    add_rule(agent, threshold=90, cooldown=60)
    assert tick(agent, 0, cpu=95)
    tick(agent, 2, cpu=10)
    assert tick(agent, 10, cpu=95) == []  # inside cooldown
    tick(agent, 20, cpu=10)
    assert tick(agent, 61, cpu=95)


def test_process_rule_respects_target(agent):
    add_rule(agent, metric="process_cpu", threshold=50, target="chrome*.exe")
    procs = [make_proc(1, "chrome.exe", cpu=80), make_proc(2, "game.exe", cpu=99),
             make_proc(3, "Chrome_helper.exe", cpu=70)]
    fired = tick(agent, 0, procs=procs)
    assert sorted(a.pid for a in fired) == [1, 3]


def test_process_memory_rule(agent):
    add_rule(agent, metric="process_memory_mb", threshold=1000)
    fired = tick(agent, 0, procs=[make_proc(1, "big.exe", rss=2000 * MB), make_proc(2, "small.exe", rss=10 * MB)])
    assert [a.process for a in fired] == ["big.exe"]
    assert "2000.0 MB" in fired[0].message


def test_vanished_process_resolves_its_alert(agent):
    add_rule(agent, metric="process_cpu", threshold=50)
    tick(agent, 0, procs=[make_proc(7, "busy.exe", cpu=90)])
    assert open_alerts(agent.db) == [("rule", 7)]
    tick(agent, 2, procs=[])
    assert open_alerts(agent.db) == []


def test_memory_growth(agent):
    add_rule(agent, metric="memory_growth_mb", threshold=100, duration=60)
    fired = []
    for i, mb in enumerate([200, 220, 260, 280, 300, 320, 340]):  # +140 MB over 60s
        fired += tick(agent, i * 10, procs=[make_proc(5, "leaky.exe", rss=mb * MB),
                                            make_proc(6, "stable.exe", rss=500 * MB)])
    assert [a.process for a in fired] == ["leaky.exe"]
    assert "grew by 140 MB" in fired[0].message


def test_memory_growth_needs_a_full_window(agent):
    add_rule(agent, metric="memory_growth_mb", threshold=100, duration=600)
    assert tick(agent, 0, procs=[make_proc(5, "x.exe", rss=100 * MB)]) == []
    # huge jump, but we have only watched it for 10 seconds
    assert tick(agent, 10, procs=[make_proc(5, "x.exe", rss=900 * MB)]) == []


def test_process_started(agent):
    add_rule(agent, metric="process_started", target="notepad.exe", severity="info")
    assert tick(agent, 0, procs=[make_proc(1, "notepad.exe")]) == []  # already running at startup
    fired = tick(agent, 2, procs=[make_proc(1, "notepad.exe"), make_proc(2, "notepad.exe")])
    assert [a.pid for a in fired] == [2]
    assert open_alerts(agent.db) == []  # start events are closed right away


def test_process_stopped(agent):
    add_rule(agent, name="svc", metric="process_stopped", target="service.exe", duration=4)
    tick(agent, 0, procs=[make_proc(1, "service.exe")])
    assert tick(agent, 2, procs=[]) == []
    fired = tick(agent, 6, procs=[])
    assert len(fired) == 1 and "service.exe" in fired[0].message
    tick(agent, 8, procs=[make_proc(9, "service.exe")])
    assert open_alerts(agent.db) == []


def test_process_stopped_ignores_never_seen(agent):
    add_rule(agent, metric="process_stopped", target="missing.exe")
    assert tick(agent, 0) == []
    assert tick(agent, 100) == []


def test_disabled_rules_are_skipped(agent):
    add_rule(agent, threshold=10, enabled=False)
    assert tick(agent, 0, cpu=99) == []


def test_reload_picks_up_changes(agent):
    rid = add_rule(agent, threshold=90)
    tick(agent, 0, cpu=95)
    agent.db.set_rule_enabled(rid, False)
    agent.reload_rules()
    tick(agent, 2, cpu=95)
    assert agent.rules == []
    assert open_alerts(agent.db) == []  # disabling a rule closes its open alert


def test_acknowledge_and_counts(agent):
    add_rule(agent, threshold=90, severity="critical")
    tick(agent, 0, cpu=95)
    c = agent.db.alert_counts()
    assert c["open"] == 1 and c["unacknowledged"] == 1 and c["critical"] == 1
    agent.db.acknowledge()
    assert agent.db.alert_counts()["unacknowledged"] == 0


def test_agent_thread_end_to_end(db):
    from spm.database import Database

    path = db.path
    with db.conn:
        db.conn.execute("DELETE FROM alert_rules")
    db.save_rule(None, "always", "process_count", 1, 0, 0, "info", None, True)

    agent = WatchdogAgent(path)
    agent.start()
    agent.submit(make_sample(ts=T0, processes=[make_proc(1), make_proc(2)]))
    alert = agent.outbox.get(timeout=5)
    agent.stop()
    agent.join(5)
    assert agent.error is None
    assert alert.title == "always"
    ro = Database(path, readonly=True)
    assert ro.query("SELECT COUNT(*) FROM alerts").rows[0][0] == 1
    ro.close()


def test_rule_describe():
    r = Rule(1, "x", "process_cpu", 50, 60, 0, "warning", "chrome.exe")
    assert r.describe() == "Process CPU [chrome.exe] >= 50% for 60s"


# -- anomaly detection --------------------------------------------------------

def warm_up(agent, n=80, cpu=15.0, jitter=3.0, start=0):
    for i in range(n):
        tick(agent, start + i * 2, cpu=cpu + (jitter if i % 2 else -jitter))
    return start + n * 2


def test_anomaly_fires_on_spike_after_warmup(agent):
    add_rule(agent, name="odd cpu", metric="anomaly_system_cpu", threshold=4)
    t = warm_up(agent)
    assert tick(agent, t, cpu=17) == []          # normal wobble
    fired = tick(agent, t + 2, cpu=70)
    assert len(fired) == 1
    assert "sigma above its baseline" in fired[0].message
    assert fired[0].value > 4


def test_anomaly_quiet_during_warmup(agent):
    add_rule(agent, metric="anomaly_system_cpu", threshold=3)
    tick(agent, 0, cpu=5)
    assert tick(agent, 2, cpu=95) == []


def test_anomaly_ignores_normal_noise(agent):
    import random

    rng = random.Random(1)
    add_rule(agent, metric="anomaly_system_cpu", threshold=5)
    fired = []
    for i in range(600):
        fired += tick(agent, i * 2, cpu=max(0, 20 + rng.gauss(0, 4)))
    assert fired == []


def test_anomaly_respects_hold_and_resolves(agent):
    add_rule(agent, name="odd", metric="anomaly_system_cpu", threshold=4, duration=6)
    t = warm_up(agent)
    assert tick(agent, t, cpu=80) == []
    assert tick(agent, t + 2, cpu=80) == []
    assert len(tick(agent, t + 6, cpu=80)) == 1
    tick(agent, t + 8, cpu=15)
    assert open_alerts(agent.db) == []


def test_baseline_survives_restart(db):
    with db.conn:
        db.conn.execute("DELETE FROM alert_rules")
    first = WatchdogAgent(db=db)
    for i in range(80):
        first.evaluate(make_sample(ts=T0 + i * 2, cpu=30.0))
    first.save_baselines()

    second = WatchdogAgent(db=db)
    second.evaluate(make_sample(ts=T0 + 200, cpu=30.0))
    cpu = second.baselines["system_cpu"]
    assert cpu.ready
    assert cpu.mean == pytest.approx(30.0, abs=0.5)


def test_stale_baseline_is_relearned(db):
    with db.conn:
        db.conn.execute("DELETE FROM alert_rules")
    db.save_baselines([("system_cpu", 50.0, 4.0, 2.0, 500, T0 - 7 * 3600)])
    agent = WatchdogAgent(db=db)
    agent.evaluate(make_sample(ts=T0, cpu=10.0))
    assert agent.baselines["system_cpu"].samples == 1


def test_listeners_get_fired_and_resolved(agent):
    events = []
    agent.listeners.append(lambda event, alert_id: events.append(event))
    add_rule(agent, threshold=90)
    tick(agent, 0, cpu=95)
    tick(agent, 2, cpu=10)
    assert events == ["fired", "resolved"]


def test_anomaly_rules_seeded(db):
    metrics = {r[2] for r in db.load_rules()}
    assert {"anomaly_system_cpu", "anomaly_system_memory", "anomaly_process_count"} <= metrics


def test_anomaly_describe():
    r = Rule(1, "x", "anomaly_network", 5, 10, 0, "info", None)
    assert r.describe() == "Network anomaly: 5 sigma above baseline for 10s"

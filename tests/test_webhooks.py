import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spm.webhooks import (Endpoint, WebhookDispatcher, build_payload, endpoints_from_args,
                          post_json, safe_name)


class Recorder(BaseHTTPRequestHandler):
    """/ok -> 200, /bad -> 400, /down -> 500, /flaky -> 500 twice then 200"""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        srv = self.server
        srv.calls.append((self.path, body, self.headers.get("Content-Type")))
        if self.path == "/flaky":
            srv.flaky += 1
            code = 200 if srv.flaky > 2 else 500
        else:
            code = {"/ok": 200, "/bad": 400, "/down": 500}.get(self.path, 404)
        self.send_response(code)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    srv.calls, srv.flaky = [], 0
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


def make_alert(db, severity="warning", resolved=False, instant=False, pid=4120):
    now = time.time()
    aid = db.insert_alert(now, 7, "Runaway process", "process_cpu", severity, "Runaway process",
                          "game.exe (PID 4120): process cpu at 63.0%", 63.0, pid, "game.exe")
    if resolved or instant:
        db.resolve_alert(aid, now if instant else now + 30)
    return aid


def dispatcher(db, *endpoints):
    return WebhookDispatcher(db=db, endpoints=list(endpoints), retries=3, backoff=0.01, host="box")


def test_payload_shape(db):
    alert = db.get_alert(make_alert(db))
    p = build_payload("fired", alert, "box")
    assert p["event"] == "alert.fired"
    inc = p["incident"]
    assert inc["status"] == "open"
    assert inc["id"] == f"box-{alert['id']}"
    assert inc["dedup_key"] == "box:7:4120"
    assert inc["severity"] == "warning" and inc["process"] == "game.exe"
    assert inc["opened_at"] and inc["resolved_at"] is None
    assert p["text"] == p["content"]
    assert p["text"].startswith("[WARNING] Runaway process on box")


def test_system_alerts_share_a_dedup_key(db):
    a = db.get_alert(make_alert(db, pid=None))
    assert build_payload("fired", a, "box")["incident"]["dedup_key"] == "box:7:system"


def test_fired_and_resolved_are_delivered(db, server):
    d = dispatcher(db, Endpoint(server.base + "/ok", min_severity="info"))
    aid = make_alert(db)
    d.process("fired", aid)
    db.resolve_alert(aid, time.time() + 5)
    d.process("resolved", aid)

    events = [body["event"] for _path, body, _ct in server.calls]
    assert events == ["alert.fired", "alert.resolved"]
    assert server.calls[0][2] == "application/json"
    assert server.calls[1][1]["incident"]["status"] == "resolved"
    assert d.sent == 2 and d.failed == 0


def test_same_event_is_not_sent_twice(db, server):
    d = dispatcher(db, Endpoint(server.base + "/ok", min_severity="info"))
    aid = make_alert(db)
    d.process("fired", aid)
    d.process("fired", aid)
    assert len(server.calls) == 1


def test_min_severity_filter(db, server):
    d = dispatcher(db, Endpoint(server.base + "/ok", min_severity="critical"))
    assert d.process("fired", make_alert(db, severity="warning")) == 0
    assert d.process("fired", make_alert(db, severity="critical")) == 1
    assert len(server.calls) == 1


def test_test_event_ignores_min_severity(db, server):
    d = dispatcher(db, Endpoint(server.base + "/ok", min_severity="critical"))
    d.process("test", make_alert(db, severity="info"))
    assert server.calls[0][1]["event"] == "alert.test"


def test_instant_resolve_is_skipped(db, server):
    d = dispatcher(db, Endpoint(server.base + "/ok", min_severity="info"))
    aid = make_alert(db, instant=True)  # e.g. "process started" events
    d.process("fired", aid)
    d.process("resolved", aid)
    assert [b["event"] for _p, b, _c in server.calls] == ["alert.fired"]


def test_retries_until_success(server):
    ok, status, error, attempts = post_json(server.base + "/flaky", {"x": 1}, retries=3, backoff=0.01)
    assert ok and status == 200 and attempts == 3 and error is None


def test_client_error_is_not_retried(server):
    ok, status, error, attempts = post_json(server.base + "/bad", {}, retries=3, backoff=0.01)
    assert not ok and status == 400 and attempts == 1


def test_server_error_gives_up(server):
    ok, status, _error, attempts = post_json(server.base + "/down", {}, retries=3, backoff=0.01)
    assert not ok and status == 500 and attempts == 3


def test_unreachable_endpoint_is_logged(db):
    d = dispatcher(db, Endpoint("http://127.0.0.1:9/hook", min_severity="info"))
    d.timeout = 0.5
    d.process("fired", make_alert(db))
    row = db.conn.execute("SELECT endpoint, ok, attempts, error FROM webhook_deliveries").fetchone()
    assert row[0] == "127.0.0.1:9"
    assert row[1] == 0 and row[2] == 3 and row[3]
    assert d.failed == 1


def test_endpoints_from_database(db, server):
    db.add_webhook("ticketing", server.base + "/ok", "info")
    d = dispatcher(db)
    d.process("fired", make_alert(db))
    assert len(server.calls) == 1
    assert db.conn.execute("SELECT endpoint, ok FROM webhook_deliveries").fetchone() == ("ticketing", 1)


def test_secret_tokens_never_logged():
    url = "https://hooks.slack.com/services/T000/B000/very-secret-token"
    assert safe_name(url) == "hooks.slack.com"
    (ep,) = endpoints_from_args([url, "  "], "critical")
    assert ep.label == "hooks.slack.com" and ep.min_severity == "critical"


def test_dispatcher_thread_drains_queue_on_stop(db, server):
    db.add_webhook("hook", server.base + "/ok", "info")
    d = WebhookDispatcher(db.path, backoff=0.01, host="box")
    d.start()
    for _ in range(3):
        d.submit("fired", make_alert(db))
    d.stop()
    d.join(10)
    assert d.error is None
    assert len(server.calls) == 3


def test_agent_feeds_dispatcher(db, server):
    from conftest import make_sample
    from spm.agent import WatchdogAgent

    with db.conn:
        db.conn.execute("DELETE FROM alert_rules")
    db.save_rule(None, "cpu", "system_cpu", 90, 0, 0, "critical", None, True)
    db.add_webhook("hook", server.base + "/ok", "warning")
    agent = WatchdogAgent(db=db)
    d = dispatcher(db)
    agent.listeners.append(d.process)  # synchronous for the test

    agent.evaluate(make_sample(ts=1000, cpu=95))
    agent.evaluate(make_sample(ts=1002, cpu=20))
    events = [b["event"] for _p, b, _c in server.calls]
    assert events == ["alert.fired", "alert.resolved"]

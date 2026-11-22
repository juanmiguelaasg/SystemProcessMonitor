"""Forward alerts as incidents to webhook / REST endpoints.

The watchdog agent already deduplicates: an alert is raised once per incident
(it stays open while the condition persists, and a cooldown applies after it).
The dispatcher sends one `alert.fired` message when an incident opens and one
`alert.resolved` message when it closes, and never sends the same event for the
same alert to the same endpoint twice.

Payload (JSON, POST):

    {
      "source": "system-process-monitor",
      "version": "1.2.0",
      "event": "alert.fired" | "alert.resolved" | "alert.test",
      "host": "<hostname>",
      "incident": {
        "id": "<host>-<alert id>",
        "dedup_key": "<host>:<rule id>:<pid or 'system'>",
        "status": "open" | "resolved",
        "severity": "info" | "warning" | "critical",
        "rule": "...", "metric": "...", "title": "...", "message": "...",
        "value": 97.2, "pid": 1234, "process": "chrome.exe",
        "opened_at": "2026-11-22T22:14:33+00:00", "resolved_at": null
      },
      "text": "...",      # one-line summary for Slack / Mattermost
      "content": "..."    # the same for Discord
    }

Only the standard library is used (urllib), so the Docker image needs nothing extra.
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .database import Database

log = logging.getLogger(__name__)

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def safe_name(url: str) -> str:
    """Host part only: webhook URLs often carry a secret token in the path."""
    parts = urllib.parse.urlsplit(url)
    return parts.netloc or "webhook"


@dataclass(frozen=True)
class Endpoint:
    url: str
    name: str = ""
    min_severity: str = "warning"

    @property
    def label(self) -> str:
        return self.name or safe_name(self.url)

    def accepts(self, severity: str) -> bool:
        return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(self.min_severity, 0)


def _iso(ts: float | None) -> str | None:
    """UTC, ISO 8601 - what ticketing systems expect."""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def build_payload(event: str, alert: dict, host: str) -> dict:
    status = "resolved" if event == "resolved" else "open"
    target = alert["pid"] if alert.get("pid") is not None else "system"
    summary = f"[{alert['severity'].upper()}] {alert['title']} on {host}: {alert['message']}"
    if event == "resolved":
        summary = f"[RESOLVED] {alert['title']} on {host}: {alert['message']}"
    return {
        "source": "system-process-monitor",
        "version": __version__,
        "event": f"alert.{event}",
        "host": host,
        "incident": {
            "id": f"{host}-{alert['id']}",
            "dedup_key": f"{host}:{alert.get('rule_id') or alert['rule_name']}:{target}",
            "status": status,
            "severity": alert["severity"],
            "rule": alert["rule_name"],
            "metric": alert["metric"],
            "title": alert["title"],
            "message": alert["message"],
            "value": alert.get("value"),
            "pid": alert.get("pid"),
            "process": alert.get("process"),
            "opened_at": _iso(alert["ts"]),
            "resolved_at": _iso(alert.get("resolved_at")),
        },
        "text": summary,
        "content": summary,
    }


def post_json(url: str, payload: dict, timeout: float = 5.0, retries: int = 3,
              backoff: float = 1.0) -> tuple[bool, int | None, str | None, int]:
    """POST with retries on network errors, 429 and 5xx. Returns (ok, status, error, attempts)."""
    body = json.dumps(payload).encode("utf-8")
    status: int | None = None
    error: str | None = None
    attempt = 0
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": f"system-process-monitor/{__version__}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                return True, status, None, attempt
        except urllib.error.HTTPError as exc:
            status, error = exc.code, f"HTTP {exc.code}"
            if exc.code != 429 and exc.code < 500:
                break  # client error: retrying will not help
        except (urllib.error.URLError, OSError, ValueError) as exc:
            status, error = None, str(getattr(exc, "reason", exc))
        if attempt < retries:
            time.sleep(backoff * (2 ** (attempt - 1)))
    return False, status, error, attempt


class WebhookDispatcher(threading.Thread):
    """Delivers alert events on its own thread so slow endpoints never block the agent."""

    def __init__(self, db_path: str | Path | None = None, endpoints=(), db: Database | None = None,
                 timeout: float = 5.0, retries: int = 3, backoff: float = 1.0,
                 host: str | None = None) -> None:
        super().__init__(name="spm-webhooks", daemon=True)
        self.db_path = db_path
        self.db = db
        self.static_endpoints = list(endpoints)
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.host = host or socket.gethostname()
        self.queue: queue.Queue = queue.Queue()
        self.sent = 0
        self.failed = 0
        self.error: Exception | None = None
        self._delivered: set[tuple[int, str, str]] = set()

    # -- control ---------------------------------------------------------------
    def submit(self, event: str, alert_id: int) -> None:
        """Agent listener: event is 'fired', 'resolved' or 'test'."""
        self.queue.put((event, alert_id))

    def stop(self) -> None:
        self.queue.put(None)  # processed after everything already queued

    def run(self) -> None:
        try:
            if self.db is None:
                self.db = Database(self.db_path)
            while True:
                item = self.queue.get()
                if item is None:
                    break
                self.process(*item)
        except Exception as exc:
            log.exception("webhook dispatcher crashed")
            self.error = exc

    # -- delivery --------------------------------------------------------------
    def endpoints(self) -> list[Endpoint]:
        found = {e.url: e for e in self.static_endpoints}
        if self.db is not None:
            for name, url, min_severity in self.db.load_webhooks():
                found.setdefault(url, Endpoint(url, name, min_severity))
        return list(found.values())

    def process(self, event: str, alert_id: int) -> int:
        """Deliver one event to every matching endpoint. Returns how many were attempted."""
        alert = self.db.get_alert(alert_id)
        if alert is None:
            return 0
        # one-shot events (e.g. "process started") open and close in the same instant
        if event == "resolved" and alert.get("resolved_at") is not None \
                and alert["resolved_at"] - alert["ts"] < 0.001:
            return 0
        payload = build_payload(event, alert, self.host)
        attempted = 0
        for endpoint in self.endpoints():
            if event != "test" and not endpoint.accepts(alert["severity"]):
                continue
            key = (alert_id, event, endpoint.url)
            if key in self._delivered:
                continue
            self._delivered.add(key)
            attempted += 1
            ok, status, error, attempts = post_json(endpoint.url, payload, self.timeout,
                                                    self.retries, self.backoff)
            if ok:
                self.sent += 1
            else:
                self.failed += 1
                log.warning("webhook %s failed for alert %s: %s", endpoint.label, alert_id, error)
            self.db.log_delivery(alert_id, endpoint.label, payload["event"],
                                 ok, status, attempts, error)
        return attempted


def endpoints_from_args(urls, min_severity: str = "warning") -> list[Endpoint]:
    result = []
    for url in urls or []:
        url = url.strip()
        if url:
            result.append(Endpoint(url, safe_name(url), min_severity))
    return result

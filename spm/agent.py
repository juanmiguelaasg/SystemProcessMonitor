"""Local watchdog agent.

Runs entirely on this machine: no network, no cloud service. The agent receives
every Sample from the collector, evaluates the rules stored in the
`alert_rules` table and records what it finds in the `alerts` table. New alerts
are also pushed onto `outbox` so a UI (or the headless console mode) can show
them immediately.

Rule semantics
--------------
* The condition is `value >= threshold`.
* It must hold for `duration_s` seconds before the alert fires. For
  `memory_growth_mb` the duration is the observation window instead.
* While the condition keeps holding the alert stays open; when it clears the
  alert is marked resolved.
* After firing, the same rule/target stays quiet for `cooldown_s` seconds.
* `target` is an optional, case-insensitive glob on the process name
  (for example `chrome*.exe`). Required for the start/stop watch metrics.
"""

from __future__ import annotations

import fnmatch
import logging
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .baseline import SERIES, Ewma, new_baseline
from .database import Database

log = logging.getLogger(__name__)

BASELINE_SAVE_EVERY = 30          # evaluations between baseline checkpoints
BASELINE_STALE_S = 6 * 3600       # a stored baseline older than this is relearned

SEVERITIES = ("info", "warning", "critical")

# metric -> (label, kind, unit)
METRICS: dict[str, tuple[str, str, str]] = {
    "system_cpu": ("System CPU", "system", "%"),
    "system_memory": ("System memory", "system", "%"),
    "swap": ("Swap usage", "system", "%"),
    "disk_usage": ("System disk usage", "system", "%"),
    "process_count": ("Process count", "system", ""),
    "process_cpu": ("Process CPU", "process", "%"),
    "process_memory_mb": ("Process memory", "process", " MB"),
    "memory_growth_mb": ("Memory growth", "growth", " MB"),
    "process_started": ("Watched process started", "watch", ""),
    "process_stopped": ("Watched process stopped", "watch", ""),
    # anomaly rules: threshold is a z-score against the learned baseline
    "anomaly_system_cpu": ("CPU anomaly", "anomaly", " sigma"),
    "anomaly_system_memory": ("Memory anomaly", "anomaly", " sigma"),
    "anomaly_process_count": ("Process count anomaly", "anomaly", " sigma"),
    "anomaly_network": ("Network anomaly", "anomaly", " sigma"),
    "anomaly_disk_io": ("Disk I/O anomaly", "anomaly", " sigma"),
}

SYSTEM_VALUES: dict[str, Callable] = {
    "system_cpu": lambda s: s.cpu,
    "system_memory": lambda s: s.mem_percent,
    "swap": lambda s: s.swap_percent,
    "disk_usage": lambda s: s.disk_percent,
    "process_count": lambda s: s.process_count,
}

MB = 1024 * 1024


@dataclass
class Rule:
    id: int
    name: str
    metric: str
    threshold: float
    duration_s: float
    cooldown_s: float
    severity: str
    target: str | None
    enabled: bool = True

    @classmethod
    def from_row(cls, row) -> "Rule":
        return cls(row[0], row[1], row[2], float(row[3]), float(row[4]), float(row[5]),
                   row[6], row[7] or None, bool(row[8]))

    def matches(self, process_name: str) -> bool:
        if not self.target:
            return True
        return fnmatch.fnmatch(process_name.lower(), self.target.lower())

    def describe(self) -> str:
        label, kind, unit = METRICS.get(self.metric, (self.metric, "", ""))
        who = f" [{self.target}]" if self.target else ""
        if kind == "watch":
            extra = f" for {self.duration_s:g}s" if self.duration_s else ""
            return f"{label}{who}{extra}"
        if kind == "growth":
            return f"{label}{who} >= {self.threshold:g}{unit} in {self.duration_s:g}s"
        if kind == "anomaly":
            hold = f" for {self.duration_s:g}s" if self.duration_s else ""
            return f"{label}: {self.threshold:g} sigma above baseline{hold}"
        hold = f" for {self.duration_s:g}s" if self.duration_s else ""
        return f"{label}{who} >= {self.threshold:g}{unit}{hold}"


@dataclass
class Alert:
    id: int
    ts: float
    rule_id: int
    rule_name: str
    severity: str
    title: str
    message: str
    value: float | None = None
    pid: int | None = None
    process: str | None = None


@dataclass
class _State:
    since: float | None = None
    alert_id: int | None = None
    last_fired: float = float("-inf")


@dataclass
class _Watch:
    pids: set[int] = field(default_factory=set)
    initialised: bool = False
    ever_seen: bool = False


class WatchdogAgent(threading.Thread):
    """Evaluates alert rules against collector samples on its own thread."""

    def __init__(self, db_path: str | Path | None = None, db: Database | None = None) -> None:
        super().__init__(name="spm-agent", daemon=True)
        self.db_path = db_path
        self.db = db
        self.inbox: queue.Queue = queue.Queue(maxsize=32)
        self.outbox: queue.Queue = queue.Queue()
        self.rules: list[Rule] = []
        self.error: Exception | None = None
        self.evaluated = 0
        self.open_alerts = 0
        self._states: dict[tuple, _State] = {}
        self._watches: dict[int, _Watch] = {}
        self._mem_history: dict[int, deque] = {}
        # learned baselines, one per series in baseline.SERIES
        self.baselines: dict[str, Ewma] = {}
        self._baselines_loaded = False
        self._scores: dict[str, tuple[float, float, float, float | None]] = {}  # value, mean, std, z
        # called as listener(event, alert_id) with event "fired" or "resolved" (webhooks)
        self.listeners: list[Callable[[str, int], None]] = []
        self._reload = threading.Event()
        self._reload.set()
        self._stop_event = threading.Event()

    # -- control ---------------------------------------------------------------
    def submit(self, sample) -> None:
        """Called from the collector thread. Never blocks; drops the oldest sample if behind."""
        try:
            self.inbox.put_nowait(sample)
        except queue.Full:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                pass
            self.inbox.put_nowait(sample)

    def reload_rules(self) -> None:
        self._reload.set()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self.inbox.put_nowait(None)
        except queue.Full:
            pass

    def run(self) -> None:
        try:
            if self.db is None:
                self.db = Database(self.db_path)
            while not self._stop_event.is_set():
                try:
                    sample = self.inbox.get(timeout=1.0)
                except queue.Empty:
                    continue
                if sample is None:
                    break
                self.evaluate(sample)
        except Exception as exc:
            log.exception("watchdog agent crashed")
            self.error = exc
        finally:
            if self.db is not None and self._baselines_loaded:
                try:
                    self.save_baselines()
                except Exception:
                    log.exception("could not save baselines")

    # -- baselines -------------------------------------------------------------
    def load_baselines(self, now: float) -> None:
        self._baselines_loaded = True
        stored = self.db.load_baselines()
        for series in SERIES:
            baseline = new_baseline(series)
            row = stored.get(series)
            if row and now - row[3] <= BASELINE_STALE_S:
                baseline.mean, baseline.var, baseline.samples, baseline.last_ts = row
            self.baselines[series] = baseline

    def save_baselines(self) -> None:
        self.db.save_baselines([
            (name, b.mean, b.var, b.std, b.samples, b.last_ts)
            for name, b in self.baselines.items() if b.mean is not None
        ])

    def _learn(self, sample) -> None:
        if not self._baselines_loaded:
            self.load_baselines(sample.ts)
        for series, spec in SERIES.items():
            baseline = self.baselines[series]
            value = float(spec.value(sample))
            mean, std = baseline.mean, baseline.std
            z = baseline.observe(value, sample.ts)
            self._scores[series] = (value, mean if mean is not None else value, std, z)

    # -- evaluation ------------------------------------------------------------
    def evaluate(self, sample) -> list[Alert]:
        """Evaluate every enabled rule against one sample. Returns the alerts fired."""
        if self._reload.is_set():
            self._reload.clear()
            self.rules = [Rule.from_row(r) for r in self.db.load_rules(enabled_only=True)]
            self._forget_removed_rules(sample.ts)

        now = sample.ts
        self._learn(sample)
        fired: list[Alert] = []
        live: set[tuple] = set()
        for rule in self.rules:
            kind = METRICS.get(rule.metric, ("", "unknown", ""))[1]
            if kind == "anomaly":
                series = rule.metric[len("anomaly_"):]
                z = self._scores.get(series, (0, 0, 0, None))[3]
                self._check(rule, (rule.id, "system"), z is not None and z >= rule.threshold,
                            z, now, rule.duration_s, fired)
            elif kind == "system":
                value = float(SYSTEM_VALUES[rule.metric](sample))
                self._check(rule, (rule.id, "system"), value >= rule.threshold, value,
                            now, rule.duration_s, fired)
            elif kind == "process":
                for p in sample.processes:
                    if not rule.matches(p.name):
                        continue
                    value = p.cpu if rule.metric == "process_cpu" else p.rss / MB
                    key = (rule.id, p.pid)
                    live.add(key)
                    self._check(rule, key, value >= rule.threshold, value, now,
                                rule.duration_s, fired, process=p)
            elif kind == "growth":
                self._check_growth(rule, sample, now, live, fired)
            elif kind == "watch":
                self._check_watch(rule, sample, now, fired)

        # processes that disappeared: close their alerts and forget their state
        for key in [k for k in self._states if isinstance(k[1], int) and k not in live]:
            state = self._states.pop(key)
            if state.alert_id is not None:
                self._resolve(state.alert_id, now)
        alive = {p.pid for p in sample.processes}
        for pid in [pid for pid in self._mem_history if pid not in alive]:
            del self._mem_history[pid]

        self.evaluated += 1
        if self.evaluated % BASELINE_SAVE_EVERY == 0:
            self.save_baselines()
        self.open_alerts = sum(1 for s in self._states.values() if s.alert_id is not None)
        return fired

    def _check(self, rule: Rule, key: tuple, condition: bool, value: float | None,
               now: float, hold: float, fired: list, process=None) -> None:
        state = self._states.setdefault(key, _State())
        if not condition:
            state.since = None
            if state.alert_id is not None:
                self._resolve(state.alert_id, now)
                state.alert_id = None
            return
        if state.since is None:
            state.since = now
        if state.alert_id is not None:
            return
        if now - state.since < hold or now - state.last_fired < rule.cooldown_s:
            return
        alert = self._fire(rule, now, value, process, held=now - state.since)
        state.alert_id = alert.id
        state.last_fired = now
        fired.append(alert)

    def _check_growth(self, rule: Rule, sample, now: float, live: set, fired: list) -> None:
        window = max(rule.duration_s, 1.0)
        for p in sample.processes:
            if not rule.matches(p.name):
                continue
            history = self._mem_history.setdefault(p.pid, deque())
            if not history or history[-1][0] != now:
                history.append((now, p.rss))
            while history and now - history[0][0] > window * 1.5:
                history.popleft()
            # oldest point that is still inside the window
            start = next(((ts, rss) for ts, rss in history if now - ts <= window), None)
            covered = start is not None and now - history[0][0] >= window * 0.9
            growth = (p.rss - start[1]) / MB if start else 0.0
            key = (rule.id, p.pid)
            live.add(key)
            self._check(rule, key, covered and growth >= rule.threshold, growth, now, 0, fired, process=p)

    def _check_watch(self, rule: Rule, sample, now: float, fired: list) -> None:
        if not rule.target:
            return
        watch = self._watches.setdefault(rule.id, _Watch())
        current = {p.pid: p for p in sample.processes if rule.matches(p.name)}
        if current:
            watch.ever_seen = True
        if rule.metric == "process_started":
            if watch.initialised:
                state = self._states.setdefault((rule.id, "watch"), _State())
                for pid in sorted(set(current) - watch.pids):
                    if now - state.last_fired < rule.cooldown_s:
                        break
                    alert = self._fire(rule, now, None, current[pid])
                    state.last_fired = now
                    self.db.resolve_alert(alert.id, now)  # an event, nothing stays open
                    fired.append(alert)
        else:  # process_stopped
            self._check(rule, (rule.id, "watch"), watch.ever_seen and not current, None,
                        now, rule.duration_s, fired)
        watch.pids = set(current)
        watch.initialised = True

    def _fire(self, rule: Rule, now: float, value: float | None, process=None,
              held: float = 0.0) -> Alert:
        label, kind, unit = METRICS.get(rule.metric, (rule.metric, "", ""))
        who = f"{process.name} (PID {process.pid})" if process is not None else None
        if rule.metric == "process_started":
            message = f"{who} started"
        elif rule.metric == "process_stopped":
            message = f"No process matching {rule.target} is running"
        elif kind == "growth":
            message = f"{who} grew by {value:.0f} MB in {rule.duration_s:g}s (limit {rule.threshold:g} MB)"
        elif kind == "anomaly":
            series = rule.metric[len("anomaly_"):]
            spec = SERIES[series]
            current, mean, std, _z = self._scores[series]
            message = (f"{spec.label} at {current:.1f}{spec.unit} is {value:.1f} sigma above its "
                       f"baseline of {mean:.1f}{spec.unit} (std {std:.1f})")
            if held >= 1:
                message += f" for {held:.0f}s"
        else:
            subject = f"{who}: {label.lower()}" if who else label
            message = f"{subject} at {value:.1f}{unit} (limit {rule.threshold:g}{unit})"
            if held >= 1:
                message += f" for {held:.0f}s"
        alert_id = self.db.insert_alert(
            now, rule.id, rule.name, rule.metric, rule.severity, rule.name, message, value,
            process.pid if process is not None else None,
            process.name if process is not None else None)
        alert = Alert(alert_id, now, rule.id, rule.name, rule.severity, rule.name, message,
                      value, process.pid if process is not None else None,
                      process.name if process is not None else None)
        self.outbox.put(alert)
        log.info("ALERT [%s] %s: %s", rule.severity, rule.name, message)
        self._notify("fired", alert_id)
        return alert

    def _resolve(self, alert_id: int, now: float) -> None:
        self.db.resolve_alert(alert_id, now)
        self._notify("resolved", alert_id)

    def _notify(self, event: str, alert_id: int) -> None:
        for listener in self.listeners:
            try:
                listener(event, alert_id)
            except Exception:
                log.exception("alert listener failed")

    def _forget_removed_rules(self, now: float) -> None:
        active = {r.id for r in self.rules}
        for key in [k for k in self._states if k[0] not in active]:
            state = self._states.pop(key)
            if state.alert_id is not None:
                self._resolve(state.alert_id, now)
        for rule_id in [r for r in self._watches if r not in active]:
            del self._watches[rule_id]

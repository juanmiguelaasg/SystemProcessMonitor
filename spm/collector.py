"""Background sampler: reads system and process metrics with psutil and records them in SQLite."""

from __future__ import annotations

import logging
import os
import platform
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import winproc
from .database import Database

log = logging.getLogger(__name__)


@dataclass
class ProcessInfo:
    pid: int
    name: str
    username: str
    cpu: float          # 0..100, normalised across all logical cores
    rss: int
    mem_percent: float
    threads: int
    status: str
    create_time: float


@dataclass
class Sample:
    ts: float
    cpu: float
    per_cpu: list[float]
    cpu_freq: float
    mem_percent: float
    mem_used: int
    mem_total: int
    swap_percent: float
    disk_percent: float
    disk_used: int
    disk_total: int
    disk_read_rate: float
    disk_write_rate: float
    net_sent_rate: float
    net_recv_rate: float
    process_count: int
    thread_count: int
    processes: list[ProcessInfo] = field(default_factory=list)


def system_drive() -> str:
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


class Collector(threading.Thread):
    """Samples metrics every `interval` seconds on a daemon thread.

    The newest Sample is exposed through `latest`; the UI polls it, so the
    collector never touches Tk objects.
    """

    def __init__(self, db_path: str | Path, interval: float = 2.0,
                 retention_hours: float = 24.0, top_n: int = 40) -> None:
        super().__init__(name="spm-collector", daemon=True)
        self.db_path = Path(db_path)
        self.interval = max(0.5, float(interval))
        self.retention_hours = retention_hours
        self.top_n = top_n
        self.cpu_count = psutil.cpu_count() or 1
        self.session_id: int | None = None
        self.error: Exception | None = None
        self.samples_written = 0
        self._latest: Sample | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._usernames: dict[tuple[int, float], str] = {}
        self._disk_path = system_drive()
        self._fast_failed = False
        self._prev_cpu: dict[tuple[int, float], int] = {}
        self._prev_wall = 0.0
        self._total_mem = psutil.virtual_memory().total
        self.listeners: list = []  # callables receiving every new Sample (e.g. the watchdog agent)

    @property
    def latest(self) -> Sample | None:
        with self._lock:
            return self._latest

    def stop(self) -> None:
        self._stop_event.set()

    # -------------------------------------------------------------------------
    def run(self) -> None:
        try:
            self._loop()
        except Exception as exc:  # surfaced in the UI status area
            log.exception("collector crashed")
            self.error = exc

    def _loop(self) -> None:
        db = Database(self.db_path)
        self.session_id = db.start_session(
            socket.gethostname(),
            f"{platform.system()} {platform.release()} ({platform.machine()})",
            self.cpu_count,
            psutil.virtual_memory().total,
        )
        db.prune(self.retention_hours)

        # Prime the counters: the first cpu_percent() call always returns 0.0.
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        self._processes()
        net_prev = psutil.net_io_counters()
        disk_prev = self._disk_io()
        t_prev = time.monotonic()
        self._stop_event.wait(min(1.0, self.interval))

        tick = 0
        while not self._stop_event.is_set():
            started = time.monotonic()
            dt = max(started - t_prev, 1e-3)
            t_prev = started

            net = psutil.net_io_counters()
            disk_io = self._disk_io()
            vm = psutil.virtual_memory()
            try:
                du = psutil.disk_usage(self._disk_path)
                disk_percent, disk_used, disk_total = du.percent, du.used, du.total
            except OSError:
                disk_percent, disk_used, disk_total = 0.0, 0, 0
            try:
                freq = psutil.cpu_freq()
                cpu_freq = freq.current if freq else 0.0
            except Exception:
                cpu_freq = 0.0

            processes = self._processes()
            sample = Sample(
                ts=time.time(),
                cpu=psutil.cpu_percent(None),
                per_cpu=psutil.cpu_percent(None, percpu=True),
                cpu_freq=cpu_freq,
                mem_percent=vm.percent,
                mem_used=vm.total - vm.available,
                mem_total=vm.total,
                swap_percent=psutil.swap_memory().percent,
                disk_percent=disk_percent,
                disk_used=disk_used,
                disk_total=disk_total,
                disk_read_rate=max(0.0, (disk_io[0] - disk_prev[0]) / dt),
                disk_write_rate=max(0.0, (disk_io[1] - disk_prev[1]) / dt),
                net_sent_rate=max(0.0, (net.bytes_sent - net_prev.bytes_sent) / dt),
                net_recv_rate=max(0.0, (net.bytes_recv - net_prev.bytes_recv) / dt),
                process_count=len(processes),
                thread_count=sum(p.threads for p in processes),
                processes=processes,
            )
            net_prev, disk_prev = net, disk_io

            snapshot_id = db.insert_snapshot(self.session_id, sample)
            db.insert_process_samples(snapshot_id, sample.ts, self._heaviest(processes))
            self.samples_written += 1

            with self._lock:
                self._latest = sample
            for listener in self.listeners:
                try:
                    listener(sample)
                except Exception:
                    log.exception("sample listener failed")

            tick += 1
            if tick % 300 == 0:
                db.prune(self.retention_hours)

            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.05, self.interval - elapsed))
        db.close()

    # -------------------------------------------------------------------------
    @staticmethod
    def _disk_io() -> tuple[int, int]:
        try:
            io = psutil.disk_io_counters()
            if io:
                return io.read_bytes, io.write_bytes
        except Exception:
            pass
        return 0, 0

    def _heaviest(self, processes: list[ProcessInfo]) -> list[ProcessInfo]:
        """Union of the top-N processes by CPU and the top-N by memory."""
        by_cpu = sorted(processes, key=lambda p: p.cpu, reverse=True)[: self.top_n]
        by_mem = sorted(processes, key=lambda p: p.rss, reverse=True)[: self.top_n]
        chosen = {p.pid: p for p in by_cpu}
        chosen.update({p.pid: p for p in by_mem})
        return list(chosen.values())

    def _username(self, pid: int, key: tuple[int, float]) -> str:
        user = self._usernames.get(key)
        if user is None:
            try:
                user = psutil.Process(pid).username() or ""
            except (psutil.Error, OSError):
                user = ""
            user = user.split("\\", 1)[-1]
            self._usernames[key] = user
        return user

    def _processes(self) -> list[ProcessInfo]:
        if winproc.AVAILABLE and not self._fast_failed:
            try:
                return self._processes_windows()
            except OSError:
                log.exception("fast Windows enumeration failed, falling back to psutil")
                self._fast_failed = True
        return self._processes_psutil()

    def _processes_windows(self) -> list[ProcessInfo]:
        """One NtQuerySystemInformation call; CPU% derived from cpu-time deltas."""
        now = time.monotonic()
        elapsed = now - self._prev_wall if self._prev_wall else 0.0
        total_mem = self._total_mem
        prev = self._prev_cpu
        current: dict[tuple[int, float], int] = {}
        result: list[ProcessInfo] = []
        for raw in winproc.snapshot():
            if raw.pid == 0:
                continue
            key = (raw.pid, round(raw.create_time, 3))
            current[key] = raw.cpu_time
            cpu = 0.0
            if elapsed > 0 and key in prev:
                cpu = (raw.cpu_time - prev[key]) / (elapsed * 1e7 * self.cpu_count) * 100.0
            result.append(ProcessInfo(
                pid=raw.pid,
                name=raw.name,
                username=self._username(raw.pid, key),
                cpu=min(max(cpu, 0.0), 100.0),
                rss=raw.working_set,
                mem_percent=raw.working_set / total_mem * 100.0 if total_mem else 0.0,
                threads=raw.threads,
                status="running",
                create_time=raw.create_time,
            ))
        self._prev_cpu, self._prev_wall = current, now
        for key in [k for k in self._usernames if k not in current]:
            del self._usernames[key]
        return result

    def _processes_psutil(self) -> list[ProcessInfo]:
        result: list[ProcessInfo] = []
        seen: set[tuple[int, float]] = set()
        attrs = ["pid", "name", "memory_info", "memory_percent", "num_threads", "status", "create_time"]
        for proc in psutil.process_iter(attrs, ad_value=None):
            info = proc.info
            pid = info["pid"]
            if pid == 0:  # "System Idle Process" reports idle time as CPU usage
                continue
            try:
                cpu = proc.cpu_percent(None) / self.cpu_count
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                cpu = 0.0
            key = (pid, info["create_time"] or 0.0)
            seen.add(key)
            user = self._username(pid, key)
            mem = info["memory_info"]
            result.append(ProcessInfo(
                pid=pid,
                name=info["name"] or f"pid {pid}",
                username=user,
                cpu=min(cpu, 100.0),
                rss=mem.rss if mem else 0,
                mem_percent=info["memory_percent"] or 0.0,
                threads=info["num_threads"] or 0,
                status=info["status"] or "",
                create_time=info["create_time"] or 0.0,
            ))
        for key in [k for k in self._usernames if k not in seen]:
            del self._usernames[key]
        return result

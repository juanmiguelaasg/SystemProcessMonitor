from __future__ import annotations

import platform
import socket
import time
from collections import deque
from datetime import datetime

import customtkinter as ctk
import psutil

from .. import theme as t
from .. import widgets as w
from .base import Page

HISTORY = 120


class DashboardPage(Page):
    title = "Overview"
    subtitle = "Live system health, refreshed by the background sampler"

    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        body = self.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(2, weight=0)

        # live pill in the header
        self.clock = ctk.CTkLabel(self.actions, text="", font=w.FONTS.small, text_color=t.MUTED)
        self.clock.pack(side="right")

        # -- row 0: stat cards -------------------------------------------------
        cards = ctk.CTkFrame(body, fg_color="transparent")
        cards.grid(row=0, column=0, sticky="ew")
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")
        self.cpu_card = w.StatCard(cards, "Processor", t.CYAN)
        self.mem_card = w.StatCard(cards, "Memory", t.VIOLET)
        self.disk_card = w.StatCard(cards, "Disk I/O", t.AMBER, y_max=None, min_y_max=64 * 1024)
        self.net_card = w.StatCard(cards, "Network", t.GREEN, y_max=None, min_y_max=16 * 1024)
        for i, card in enumerate((self.cpu_card, self.mem_card, self.disk_card, self.net_card)):
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0 if i == 3 else 8))

        # -- row 1: history chart + cores --------------------------------------
        mid = ctk.CTkFrame(body, fg_color="transparent")
        mid.grid(row=1, column=0, sticky="nsew", pady=16)
        mid.grid_columnconfigure(0, weight=3, uniform="mid")
        mid.grid_columnconfigure(1, weight=2, uniform="mid")
        mid.grid_rowconfigure(0, weight=1)

        self.cpu_hist: deque = deque(maxlen=HISTORY)
        self.mem_hist: deque = deque(maxlen=HISTORY)
        hist = w.Panel(mid, "Utilisation history",
                       f"Last {HISTORY * app.args.interval / 60:.0f} minutes at {app.args.interval:g}s resolution")
        hist.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        hist.legend([("CPU", t.CYAN), ("Memory", t.VIOLET)])
        hist.body.grid_columnconfigure(0, weight=1)
        hist.body.grid_rowconfigure(0, weight=1)
        self.history_chart = w.LineChart(
            hist.body, [w.Series("Memory", t.VIOLET, self.mem_hist), w.Series("CPU", t.CYAN, self.cpu_hist)],
            max_points=HISTORY, height=200)
        self.history_chart.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        cores = w.Panel(mid, "Logical processors",
                        f"{psutil.cpu_count(logical=False) or '?'} cores / {psutil.cpu_count()} threads")
        cores.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        cores.body.grid_columnconfigure(0, weight=1)
        cores.body.grid_rowconfigure(0, weight=1)
        self.core_bars = w.CoreBars(cores.body, height=150)
        self.core_bars.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self.freq_label = ctk.CTkLabel(cores.slot, text="", font=w.FONTS.small, text_color=t.MUTED)
        self.freq_label.pack(side="right")

        # -- row 2: top processes + system info --------------------------------
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="nsew")
        bottom.grid_columnconfigure(0, weight=3, uniform="bottom")
        bottom.grid_columnconfigure(1, weight=2, uniform="bottom")
        bottom.grid_rowconfigure(0, weight=1)

        top = w.Panel(bottom, "Busiest processes", "Sorted by CPU, top 8")
        top.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        top.body.grid_columnconfigure(0, weight=1)
        top.body.grid_rowconfigure(0, weight=1)
        self.top_table = w.DataTable(top.body, height=4, border=False)
        self.top_table.grid(row=0, column=0, sticky="nsew")
        self.top_table.set_columns([
            ("name", "Process", 220), ("pid", "PID", 70, "e", False),
            ("cpu", "CPU", 80, "e", False), ("mem", "Memory", 100, "e", False),
            ("threads", "Threads", 80, "e", False),
        ])

        info = w.Panel(bottom, "System", "")
        info.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        info.body.grid_columnconfigure(1, weight=1)
        self.info_values: dict[str, ctk.CTkLabel] = {}
        vm = psutil.virtual_memory()
        boot = psutil.boot_time()
        static = [
            ("Host", socket.gethostname()),
            ("OS", f"{platform.system()} {platform.release()} {platform.version()}"),
            ("Processor", (platform.processor() or platform.machine())[:48]),
            ("Memory", t.fmt_bytes(vm.total)),
            ("Booted", datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M")),
            ("Uptime", ""),
            ("Session", f"#{app.collector.session_id or '-'}"),
        ]
        for row, (key, value) in enumerate(static):
            ctk.CTkLabel(info.body, text=key, font=w.FONTS.small, text_color=t.MUTED, height=22,
                         anchor="w").grid(row=row, column=0, sticky="w", padx=(8, 16), pady=1)
            lbl = ctk.CTkLabel(info.body, text=value, font=w.FONTS.small, text_color=t.TEXT, anchor="w", height=22)
            lbl.grid(row=row, column=1, sticky="w", pady=1)
            self.info_values[key] = lbl
        self.boot_time = boot
        self._prefill()

    def _prefill(self) -> None:
        """Seed the charts from SQLite so a restart does not start with empty graphs."""
        try:
            rows = self.app.db.recent_history(HISTORY * self.app.args.interval * 1.5, HISTORY)
        except Exception:
            return
        for cpu, mem, disk, net in rows:
            self.cpu_hist.append(cpu)
            self.mem_hist.append(mem)
            self.cpu_card.data.append(cpu)
            self.mem_card.data.append(mem)
            self.disk_card.data.append(disk)
            self.net_card.data.append(net)

    # -------------------------------------------------------------------------
    def on_sample(self, s) -> None:
        self.cpu_hist.append(s.cpu)
        self.mem_hist.append(s.mem_percent)
        self.cpu_card.data.append(s.cpu)
        self.mem_card.data.append(s.mem_percent)
        self.disk_card.data.append(s.disk_read_rate + s.disk_write_rate)
        self.net_card.data.append(s.net_recv_rate + s.net_sent_rate)
        self._last = s
        if self.visible:
            self._render(s)

    def on_show(self) -> None:
        last = getattr(self, "_last", None)
        if last is not None:
            self._render(last)

    def _render(self, s) -> None:
        self.clock.configure(text=datetime.fromtimestamp(s.ts).strftime("Updated %H:%M:%S"))
        self.cpu_card.update_card(
            f"{s.cpu:.1f}%",
            f"{s.process_count} processes  /  {s.thread_count:,} threads",
            "HIGH" if s.cpu >= 85 else ("BUSY" if s.cpu >= 60 else "OK"))
        self.mem_card.update_card(
            f"{s.mem_percent:.1f}%",
            f"{t.fmt_bytes(s.mem_used)} of {t.fmt_bytes(s.mem_total)}",
            f"SWAP {s.swap_percent:.0f}%")
        self.disk_card.update_card(
            t.fmt_rate(s.disk_read_rate + s.disk_write_rate),
            f"R {t.fmt_rate(s.disk_read_rate)}  /  W {t.fmt_rate(s.disk_write_rate)}",
            f"{s.disk_percent:.0f}% FULL")
        self.net_card.update_card(
            t.fmt_rate(s.net_recv_rate + s.net_sent_rate),
            f"In {t.fmt_rate(s.net_recv_rate)}  /  Out {t.fmt_rate(s.net_sent_rate)}",
            "LIVE")

        self.history_chart.redraw()
        self.core_bars.set_values(s.per_cpu)
        if s.cpu_freq:
            self.freq_label.configure(text=f"{s.cpu_freq / 1000:.2f} GHz")

        busiest = sorted(s.processes, key=lambda p: (p.cpu, p.rss), reverse=True)[:8]
        self.top_table.sync([
            (str(p.pid), (p.name, p.pid, f"{p.cpu:.1f}%", t.fmt_bytes(p.rss), p.threads))
            for p in busiest
        ])

        self.info_values["Uptime"].configure(text=t.fmt_duration(time.time() - self.boot_time))
        if self.app.collector.session_id:
            self.info_values["Session"].configure(text=f"#{self.app.collector.session_id}")

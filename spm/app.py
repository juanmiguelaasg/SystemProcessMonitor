"""Main window: sidebar navigation, page switching and the UI polling loop."""

from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from . import __version__
from . import theme as t
from . import widgets as w
from .agent import WatchdogAgent
from .collector import Collector
from .database import Database
from .notify import ToastManager
from .webhooks import WebhookDispatcher, endpoints_from_args
from .views.alerts import AlertsPage
from .views.analytics import AnalyticsPage
from .views.console import ConsolePage
from .views.dashboard import DashboardPage
from .views.processes import ProcessesPage

NAV = [
    ("dashboard", "Overview", DashboardPage),
    ("processes", "Processes", ProcessesPage),
    ("analytics", "Analytics", AnalyticsPage),
    ("console", "SQL Console", ConsolePage),
    ("alerts", "Alerts", AlertsPage),
]


class App(ctk.CTk):
    def __init__(self, args) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=t.BG)
        self.args = args
        self.title("System Process Monitor")
        w.SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self._fit_to_screen(1320, 820)
        self.minsize(1080, 660)
        w.FONTS = t.init_fonts(self)
        self._configure_ttk()

        # Writable connection first so the schema exists before anything else opens it.
        self.db = Database(args.db)
        self.ro_db = Database(args.db, readonly=True)
        self.collector = Collector(args.db, interval=args.interval,
                                   retention_hours=args.retention, top_n=args.top)
        self.agent: WatchdogAgent | None = None
        self.webhooks: WebhookDispatcher | None = None
        if not args.no_agent:
            self.db.resolve_open_alerts(time.time())
            self.agent = WatchdogAgent(args.db)
            self.collector.listeners.append(self.agent.submit)
            self.webhooks = WebhookDispatcher(
                args.db, endpoints_from_args(args.webhook, args.webhook_min_severity))
            self.agent.listeners.append(self.webhooks.submit)
            self.webhooks.start()
            self.agent.start()
        self.collector.start()
        self.toasts = ToastManager(self, on_open=self._open_alerts, sound=not args.no_sound)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color=t.BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {}
        for key, _label, cls in NAV:
            page = cls(self.content, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
        self.current = None
        self.show("dashboard")

        self._last_ts = 0.0
        self._pulse = False
        self._toast_job = None
        self.refresh_alert_badge()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self._poll)
        self.after(1000, self._update_status)

    # -------------------------------------------------------------------------
    def _fit_to_screen(self, width: int, height: int) -> None:
        """Use the preferred size, shrunk to fit the screen, centred. CTk scales geometry by DPI."""
        scale = w.SCALE
        screen_w = self.winfo_screenwidth() / scale
        screen_h = self.winfo_screenheight() / scale
        width = int(min(width, screen_w * 0.94))
        height = int(min(height, screen_h * 0.86))
        x = int((screen_w - width) / 2 * scale)
        y = int(max(0, (screen_h - height) / 2 - 20) * scale)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_ttk(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        row_h = w.px(30)
        style.configure("SPM.Treeview", background=t.CARD, fieldbackground=t.CARD,
                        foreground=t.TEXT, rowheight=row_h, borderwidth=0, relief="flat",
                        font=(t.FONT, 10))
        style.map("SPM.Treeview",
                  background=[("selected", t.mix(t.CYAN, t.CARD, 0.72))],
                  foreground=[("selected", "#ffffff")])
        style.configure("SPM.Treeview.Heading", background=t.SURFACE, foreground=t.MUTED,
                        relief="flat", borderwidth=0, font=(t.FONT, 9, "bold"),
                        padding=(w.px(8), w.px(7)))
        style.map("SPM.Treeview.Heading",
                  background=[("active", t.CARD_HOVER)], foreground=[("active", t.TEXT)])
        style.layout("SPM.Treeview", [("SPM.Treeview.treearea", {"sticky": "nswe"})])

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=t.SURFACE, corner_radius=0, width=w.px(230))
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(2, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 26))
        logo = tk.Canvas(brand, width=w.px(38), height=w.px(38), bg=t.SURFACE, highlightthickness=0)
        logo.pack(side="left")
        s = w.px(38)
        w.round_rect(logo, 0, 0, s, s, w.px(10), fill=t.mix(t.CYAN, t.SURFACE, 0.82), outline="")
        logo.create_line(s * .15, s * .55, s * .35, s * .55, s * .45, s * .28, s * .58, s * .75,
                         s * .68, s * .45, s * .85, s * .45, fill=t.CYAN, width=max(2, w.px(2.4)),
                         capstyle="round", joinstyle="round")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=12)
        ctk.CTkLabel(names, text="Process Monitor", font=w.FONTS.brand, text_color=t.TEXT,
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text=f"v{__version__}  /  SQLite", font=w.FONTS.small,
                     text_color=t.DIM, anchor="w").pack(anchor="w")

        nav = ctk.CTkFrame(bar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=12)
        ctk.CTkLabel(nav, text="NAVIGATION", font=w.FONTS.caption, text_color=t.DIM,
                     anchor="w").pack(fill="x", padx=10, pady=(0, 6))
        self.nav_buttons = {}
        for key, label, _cls in NAV:
            btn = ctk.CTkButton(nav, text=label, anchor="w", height=40, corner_radius=10,
                                font=w.FONTS.body, fg_color="transparent", hover_color=t.CARD_HOVER,
                                text_color=t.MUTED, command=lambda k=key: self.show(k))
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn
        self.alert_badge = ctk.CTkLabel(self.nav_buttons["alerts"], text="", font=w.FONTS.caption,
                                        fg_color=t.ROSE, text_color="#0b0e14", corner_radius=9,
                                        width=24, height=20)

        # status block
        status = ctk.CTkFrame(bar, fg_color=t.CARD, corner_radius=12, border_width=1,
                              border_color=t.BORDER)
        status.grid(row=3, column=0, sticky="ew", padx=14, pady=16)
        status.grid_columnconfigure(1, weight=1)
        self.dot = tk.Canvas(status, width=w.px(12), height=w.px(12), bg=t.CARD, highlightthickness=0)
        self.dot.grid(row=0, column=0, padx=(14, 6), pady=(12, 2))
        self.dot_id = self.dot.create_oval(w.px(1), w.px(1), w.px(11), w.px(11), fill=t.GREEN, outline="")
        self.rec_label = ctk.CTkLabel(status, text="Recording", font=w.FONTS.body_bold,
                                      text_color=t.TEXT, anchor="w")
        self.rec_label.grid(row=0, column=1, sticky="w", pady=(12, 2))
        self.status_lines = ctk.CTkLabel(status, text="", font=w.FONTS.small, text_color=t.MUTED,
                                         anchor="w", justify="left")
        self.status_lines.grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))

        self.toast_label = ctk.CTkLabel(bar, text="", font=w.FONTS.small, text_color=t.AMBER,
                                        wraplength=w.px(190), justify="left", anchor="w")
        self.toast_label.grid(row=2, column=0, sticky="sew", padx=18)

    # -------------------------------------------------------------------------
    def show(self, key: str) -> None:
        if self.current:
            self.pages[self.current].visible = False
        self.current = key
        page = self.pages[key]
        page.visible = True
        page.tkraise()
        for k, btn in self.nav_buttons.items():
            active = k == key
            btn.configure(fg_color=t.CARD_HOVER if active else "transparent",
                          text_color=t.TEXT if active else t.MUTED)
        page.on_show()

    def toast(self, message: str) -> None:
        self.toast_label.configure(text=message)
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(6000, lambda: self.toast_label.configure(text=""))

    def _poll(self) -> None:
        sample = self.collector.latest
        if sample is not None and sample.ts != self._last_ts:
            self._last_ts = sample.ts
            for page in self.pages.values():
                try:
                    page.on_sample(sample)
                except Exception as exc:  # keep the loop alive whatever happens
                    self.toast(f"UI error: {exc}")
        if self.agent is not None:
            new_alerts = []
            while True:
                try:
                    new_alerts.append(self.agent.outbox.get_nowait())
                except queue.Empty:
                    break
            for alert in new_alerts[-6:]:
                self.toasts.show(alert)
            if new_alerts:
                self.pages["alerts"].on_alert(new_alerts[-1])
                self.refresh_alert_badge()
        self.after(250, self._poll)

    def _update_status(self) -> None:
        self._pulse = not self._pulse
        if self.collector.error:
            self.dot.itemconfigure(self.dot_id, fill=t.ROSE)
            self.rec_label.configure(text="Collector stopped")
            self.status_lines.configure(text=str(self.collector.error)[:120])
        else:
            self.dot.itemconfigure(self.dot_id, fill=t.GREEN if self._pulse else t.mix(t.GREEN, t.CARD, 0.6))
            try:
                stats = self.db.stats()
                self.status_lines.configure(text=(
                    f"Every {self.args.interval:g}s  /  keep {self.args.retention:g}h\n"
                    f"{stats['snapshots']:,} snapshots\n"
                    f"{stats['samples']:,} process rows\n"
                    f"{t.fmt_bytes(self.db.file_size())} on disk\n"
                    f"{self._agent_line()}"))
            except Exception:
                pass
        if int(time.time()) % 5 == 0:
            self.refresh_alert_badge()
        self.after(1000, self._update_status)

    def _agent_line(self) -> str:
        if self.agent is None:
            return "Agent off"
        if self.agent.error:
            return "Agent error"
        return f"Agent: {len(self.agent.rules)} rules, {self.agent.open_alerts} open"


    def refresh_alert_badge(self) -> None:
        try:
            count = self.db.alert_counts()["unacknowledged"]
        except Exception:
            return
        if count:
            self.alert_badge.configure(text=str(count) if count < 100 else "99+")
            self.alert_badge.place(relx=1.0, rely=0.5, x=-10, anchor="e")
        else:
            self.alert_badge.place_forget()

    def _open_alerts(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self.show("alerts")

    def _on_close(self) -> None:
        self.collector.stop()
        if self.agent is not None:
            self.agent.stop()
            self.agent.join(timeout=3)
        if self.webhooks is not None:
            self.webhooks.stop()
            self.webhooks.join(timeout=5)
        self.collector.join(timeout=3)
        self.ro_db.close()
        self.db.close()
        self.destroy()

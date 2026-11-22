from __future__ import annotations

from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk
import psutil

from .. import theme as t
from .. import widgets as w
from .base import Page, accent_button

COLUMNS = [
    ("pid", "PID", 80, "e", False),
    ("name", "Name", 240, "w", True),
    ("user", "User", 140, "w", True),
    ("status", "Status", 90, "w", False),
    ("cpu", "CPU", 80, "e", False),
    ("rss", "Memory", 110, "e", False),
    ("mem", "Mem %", 80, "e", False),
    ("threads", "Threads", 80, "e", False),
    ("started", "Started", 150, "w", False),
]

SORT_KEYS = {
    "pid": lambda p: p.pid,
    "name": lambda p: p.name.lower(),
    "user": lambda p: p.username.lower(),
    "status": lambda p: p.status,
    "cpu": lambda p: (p.cpu, p.rss),
    "rss": lambda p: p.rss,
    "mem": lambda p: p.mem_percent,
    "threads": lambda p: p.threads,
    "started": lambda p: p.create_time,
}


class ProcessesPage(Page):
    title = "Processes"
    subtitle = "Every running process. Click a column header to sort, select a row to act on it."

    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        self.processes = []
        self.paused = False

        self.search = ctk.CTkEntry(self.actions, placeholder_text="Filter by name, user or PID",
                                   width=260, height=34, corner_radius=10, fg_color=t.CARD,
                                   border_color=t.BORDER, text_color=t.TEXT, font=w.FONTS.body)
        self.search.pack(side="left", padx=(0, 10))
        self.search.bind("<KeyRelease>", lambda _e: self.refresh())

        self.pause_switch = ctk.CTkSwitch(self.actions, text="Freeze", command=self._toggle_pause,
                                          progress_color=t.CYAN, font=w.FONTS.body,
                                          text_color=t.MUTED, width=60)
        self.pause_switch.pack(side="left", padx=(0, 14))
        self.kill_btn = accent_button(self.actions, "End task", self._end_task, color=t.ROSE)
        self.kill_btn.pack(side="left")

        body = self.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.table = w.DataTable(body, height=20, on_sort=lambda _c: self.refresh())
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.set_columns(COLUMNS)
        self.table.set_sort("cpu", True)
        self.table.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_footer())
        self.table.tree.bind("<Delete>", lambda _e: self._end_task())

        self.footer = ctk.CTkLabel(body, text="", font=w.FONTS.small, text_color=t.MUTED, anchor="w")
        self.footer.grid(row=1, column=0, sticky="ew", pady=(10, 0))

    def _toggle_pause(self) -> None:
        self.paused = bool(self.pause_switch.get())

    def on_sample(self, s) -> None:
        if self.paused:
            return
        self.processes = s.processes
        if self.visible:
            self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        query = self.search.get().strip().lower()
        procs = self.processes
        if query:
            procs = [p for p in procs
                     if query in p.name.lower() or query in p.username.lower() or query == str(p.pid)]
        key = SORT_KEYS.get(self.table.sort_col or "cpu", SORT_KEYS["cpu"])
        procs = sorted(procs, key=key, reverse=self.table.sort_desc)
        self.table.sync([
            (str(p.pid), (
                p.pid, p.name, p.username or "-", p.status,
                f"{p.cpu:.1f}", t.fmt_bytes(p.rss), f"{p.mem_percent:.1f}", p.threads,
                datetime.fromtimestamp(p.create_time).strftime("%Y-%m-%d %H:%M") if p.create_time else "-",
            ))
            for p in procs
        ])
        self._shown = len(procs)
        self._update_footer()

    def _selected_process(self):
        iid = self.table.selected()
        if not iid:
            return None
        pid = int(iid)
        return next((p for p in self.processes if p.pid == pid), None)

    def _update_footer(self) -> None:
        total_threads = sum(p.threads for p in self.processes)
        text = f"{getattr(self, '_shown', 0)} of {len(self.processes)} processes   |   {total_threads:,} threads"
        proc = self._selected_process()
        if proc:
            text += f"   |   Selected: {proc.name} (PID {proc.pid}), {t.fmt_bytes(proc.rss)}, {proc.cpu:.1f}% CPU"
        if self.paused:
            text += "   |   Frozen"
        self.footer.configure(text=text)

    def _end_task(self) -> None:
        proc = self._selected_process()
        if not proc:
            messagebox.showinfo("End task", "Select a process first.", parent=self)
            return
        if not messagebox.askyesno(
                "End task",
                f"Terminate {proc.name} (PID {proc.pid})?\n\nUnsaved data in that program will be lost.",
                icon="warning", parent=self):
            return
        try:
            target = psutil.Process(proc.pid)
            target.terminate()
            try:
                target.wait(timeout=3)
                result = "terminated"
            except psutil.TimeoutExpired:
                result = "signalled (still running)"
        except psutil.NoSuchProcess:
            result = "already exited"
        except psutil.AccessDenied:
            result = "access denied"
            messagebox.showerror("End task",
                                 f"Access denied while terminating {proc.name}.\n"
                                 "Run the monitor as administrator to manage system processes.",
                                 parent=self)
        except psutil.Error as exc:
            result = f"error: {exc}"
        self.app.db.log_event(proc.pid, proc.name, "terminate", result)
        self.app.toast(f"{proc.name} (PID {proc.pid}): {result}")

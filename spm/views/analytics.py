from __future__ import annotations

import sqlite3
import time

import customtkinter as ctk

from .. import theme as t
from .. import widgets as w
from ..database import load_reports
from .base import Page, accent_button, ghost_button

RANGES = {
    "15 min": 15 * 60,
    "1 hour": 3600,
    "6 hours": 6 * 3600,
    "24 hours": 24 * 3600,
    "All": None,
}


def inline_since(sql: str, seconds: int | None) -> str:
    """Replace the :since parameter with a literal SQLite expression for the console."""
    expr = "0" if seconds is None else f"(CAST(strftime('%s', 'now') AS REAL) - {seconds})"
    return sql.replace(":since", expr)


class AnalyticsPage(Page):
    title = "Analytics"
    subtitle = "Pre-built SQL reports over the recorded history"

    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        self.reports = load_reports()
        self.current = self.reports[0]
        self.items: dict[str, ctk.CTkFrame] = {}

        self.range = ctk.CTkSegmentedButton(
            self.actions, values=list(RANGES), command=lambda _v: self.run(),
            font=w.FONTS.small, height=32, corner_radius=10,
            fg_color=t.CARD, selected_color=t.CYAN, selected_hover_color=t.mix(t.CYAN, "#ffffff", 0.2),
            unselected_color=t.CARD, unselected_hover_color=t.CARD_HOVER, text_color=t.TEXT)
        self.range.set("1 hour")
        self.range.pack(side="left", padx=(0, 10))
        ghost_button(self.actions, "Open in console", self._open_console, width=130).pack(side="left", padx=(0, 10))
        accent_button(self.actions, "Refresh", self.run, width=96).pack(side="left")

        body = self.body
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # -- report list --------------------------------------------------------
        listing = ctk.CTkScrollableFrame(body, width=w.px(250), fg_color=t.SURFACE, corner_radius=14,
                                         border_width=1, border_color=t.BORDER,
                                         scrollbar_button_color=t.BORDER)
        listing.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        ctk.CTkLabel(listing, text="REPORTS", font=w.FONTS.caption, text_color=t.DIM,
                     anchor="w").pack(fill="x", padx=12, pady=(8, 6))
        for report in self.reports:
            self._report_item(listing, report)

        # -- detail -------------------------------------------------------------
        detail = ctk.CTkFrame(body, fg_color="transparent")
        detail.grid(row=0, column=1, sticky="nsew")
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(detail, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self.report_title = ctk.CTkLabel(head, text="", font=w.FONTS.h2, text_color=t.TEXT, anchor="w")
        self.report_title.grid(row=0, column=0, sticky="w")
        self.report_desc = ctk.CTkLabel(head, text="", font=w.FONTS.small, text_color=t.MUTED,
                                        anchor="w", justify="left")
        self.report_desc.grid(row=1, column=0, sticky="w")
        self.status = ctk.CTkLabel(head, text="", font=w.FONTS.small, text_color=t.MUTED)
        self.status.grid(row=0, column=1, rowspan=2, sticky="e")

        self.sql_box = ctk.CTkTextbox(detail, height=w.px(150), font=w.FONTS.mono_small, fg_color=t.SURFACE,
                                      border_width=1, border_color=t.BORDER, corner_radius=12,
                                      text_color=t.TEXT, wrap="none", activate_scrollbars=True)
        self.sql_box.grid(row=1, column=0, sticky="ew", pady=12)
        w.configure_sql_tags(self.sql_box)

        self.table = w.DataTable(detail, height=12, horizontal=True)
        self.table.grid(row=2, column=0, sticky="nsew")
        self._select(self.current, run=False)

    def _report_item(self, master, report) -> None:
        item = ctk.CTkFrame(master, fg_color="transparent", corner_radius=10, cursor="hand2")
        item.pack(fill="x", padx=4, pady=2)
        title = ctk.CTkLabel(item, text=report.title, font=w.FONTS.body_bold, text_color=t.TEXT,
                             anchor="w", cursor="hand2")
        title.pack(fill="x", padx=12, pady=(8, 0))
        desc = ctk.CTkLabel(item, text=report.description, font=w.FONTS.small, text_color=t.MUTED,
                            anchor="w", justify="left", wraplength=w.px(200), cursor="hand2")
        desc.pack(fill="x", padx=12, pady=(0, 8))
        for widget in (item, title, desc):
            widget.bind("<Button-1>", lambda _e, r=report: self._select(r))
        self.items[report.name] = item

    def _select(self, report, run: bool = True) -> None:
        self.current = report
        for name, item in self.items.items():
            item.configure(fg_color=t.CARD_HOVER if name == report.name else "transparent")
        self.report_title.configure(text=report.title)
        self.report_desc.configure(text=report.description)
        self.sql_box.configure(state="normal")
        self.sql_box.delete("1.0", "end")
        self.sql_box.insert("1.0", report.sql)
        w.highlight_sql(self.sql_box)
        self.sql_box.configure(state="disabled")
        if run:
            self.run()

    def _seconds(self) -> int | None:
        return RANGES.get(self.range.get(), 3600)

    def run(self) -> None:
        seconds = self._seconds()
        since = 0.0 if seconds is None else time.time() - seconds
        try:
            result = self.app.ro_db.query(self.current.sql, {"since": since})
        except sqlite3.Error as exc:
            self.status.configure(text=f"Error: {exc}", text_color=t.ROSE)
            return
        self.table.sort_col = None
        self.table.set_columns([(c, c, 120, "w") for c in result.columns])
        self.table.set_rows(result.rows)
        self.status.configure(text=f"{len(result.rows)} rows  /  {result.elapsed_ms:.1f} ms",
                              text_color=t.MUTED)

    def _open_console(self) -> None:
        self.app.pages["console"].set_sql(
            f"-- {self.current.title}\n" + inline_since(self.current.sql, self._seconds()), run=True)
        self.app.show("console")

    def on_show(self) -> None:
        self.run()

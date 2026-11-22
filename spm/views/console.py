from __future__ import annotations

import csv
import sqlite3
from tkinter import filedialog

import customtkinter as ctk

from .. import theme as t
from .. import widgets as w
from ..database import load_reports
from .analytics import inline_since
from .base import Page, accent_button, ghost_button

EXTRA_EXAMPLES = {
    "Latest snapshots": (
        "SELECT datetime(ts, 'unixepoch', 'localtime') AS time,\n"
        "       ROUND(cpu_percent, 1) AS cpu, ROUND(mem_percent, 1) AS mem,\n"
        "       process_count, thread_count\n"
        "FROM system_snapshots\n"
        "ORDER BY ts DESC\n"
        "LIMIT 50;"
    ),
    "Process stats (view)": "SELECT * FROM v_process_stats\nORDER BY avg_cpu DESC\nLIMIT 50;",
    "Per-minute rollup (view)": "SELECT * FROM v_system_minutely\nORDER BY minute DESC\nLIMIT 60;",
    "Processes per user": (
        "SELECT COALESCE(NULLIF(username, ''), '(system)') AS user,\n"
        "       COUNT(DISTINCT name) AS executables,\n"
        "       ROUND(AVG(cpu_percent), 2) AS avg_cpu\n"
        "FROM process_samples\n"
        "GROUP BY user\n"
        "ORDER BY executables DESC;"
    ),
    "Query plan": (
        "EXPLAIN QUERY PLAN\n"
        "SELECT name, AVG(cpu_percent) FROM process_samples\n"
        "WHERE ts >= CAST(strftime('%s', 'now') AS REAL) - 600\n"
        "GROUP BY name;"
    ),
}

WELCOME = """-- SQL console (read-only connection to the monitor database)
-- Press Ctrl+Enter to run. Click a table on the right to preview it.

SELECT name,
       COUNT(*)                         AS samples,
       ROUND(AVG(cpu_percent), 2)       AS avg_cpu,
       ROUND(MAX(mem_rss) / 1048576.0)  AS peak_mb
FROM process_samples
WHERE ts >= CAST(strftime('%s', 'now') AS REAL) - 300
GROUP BY name
ORDER BY avg_cpu DESC
LIMIT 20;
"""


class ConsolePage(Page):
    title = "SQL Console"
    subtitle = "Query the recorded history directly. The connection is opened read-only."

    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        self.examples = dict(EXTRA_EXAMPLES)
        for report in load_reports():
            self.examples[report.title] = inline_since(report.sql, 3600)
        self.last_result = None
        self._hl_job = None

        self.example_menu = ctk.CTkOptionMenu(
            self.actions, values=list(self.examples), command=self._load_example,
            width=200, height=34, corner_radius=10, font=w.FONTS.body, dropdown_font=w.FONTS.body,
            fg_color=t.CARD, button_color=t.CARD_HOVER, button_hover_color=t.BORDER,
            text_color=t.TEXT, dropdown_fg_color=t.CARD, dropdown_hover_color=t.CARD_HOVER,
            dropdown_text_color=t.TEXT)
        self.example_menu.set("Examples")
        self.example_menu.pack(side="left", padx=(0, 10))
        ghost_button(self.actions, "Export CSV", self._export, width=110).pack(side="left", padx=(0, 10))
        accent_button(self.actions, "Run query", self.run, width=110).pack(side="left")

        body = self.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        self.editor = ctk.CTkTextbox(body, height=w.px(190), font=w.FONTS.mono, fg_color=t.SURFACE,
                                     border_width=1, border_color=t.BORDER, corner_radius=12,
                                     text_color=t.TEXT, wrap="none", undo=True)
        self.editor.grid(row=0, column=0, sticky="ew")
        w.configure_sql_tags(self.editor)
        self.editor.bind("<Control-Return>", self._on_ctrl_enter)
        self.editor.bind("<KeyRelease>", self._schedule_highlight)
        self.set_sql(WELCOME)

        self.status = ctk.CTkLabel(body, text="Ready", font=w.FONTS.small, text_color=t.MUTED, anchor="w")
        self.status.grid(row=1, column=0, sticky="ew", pady=(8, 8))

        self.table = w.DataTable(body, height=12, horizontal=True)
        self.table.grid(row=2, column=0, sticky="nsew")

        # schema browser
        self.schema_frame = ctk.CTkScrollableFrame(body, width=w.px(230), fg_color=t.SURFACE,
                                                   corner_radius=14, border_width=1,
                                                   border_color=t.BORDER,
                                                   scrollbar_button_color=t.BORDER)
        self.schema_frame.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=(16, 0))
        self._schema_loaded = False

    # -------------------------------------------------------------------------
    def _build_schema(self) -> None:
        for child in self.schema_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.schema_frame, text="SCHEMA", font=w.FONTS.caption, text_color=t.DIM,
                     anchor="w").pack(fill="x", padx=10, pady=(8, 4))
        try:
            objects = self.app.ro_db.schema()
        except sqlite3.Error as exc:
            ctk.CTkLabel(self.schema_frame, text=str(exc), text_color=t.ROSE).pack()
            return
        for name, kind, cols in objects:
            card = ctk.CTkFrame(self.schema_frame, fg_color=t.CARD, corner_radius=10)
            card.pack(fill="x", padx=4, pady=4)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 2))
            title = ctk.CTkLabel(head, text=name, font=w.FONTS.body_bold, text_color=t.TEXT,
                                 anchor="w", cursor="hand2")
            title.pack(side="left")
            color = t.CYAN if kind == "table" else t.VIOLET
            ctk.CTkLabel(head, text=f" {kind.upper()} ", font=w.FONTS.caption, text_color=color,
                         fg_color=t.mix(color, t.CARD, 0.85), corner_radius=6, height=18).pack(side="right")
            title.bind("<Button-1>", lambda _e, n=name: self.set_sql(f"SELECT *\nFROM {n}\nLIMIT 100;", run=True))
            col_text = "\n".join(f"{c}  {d.lower()}".rstrip() for c, d in cols)
            ctk.CTkLabel(card, text=col_text, font=w.FONTS.mono_small, text_color=t.MUTED,
                         anchor="w", justify="left").pack(fill="x", padx=10, pady=(0, 8))
        self._schema_loaded = True

    def on_show(self) -> None:
        if not self._schema_loaded:
            self._build_schema()
            if self.last_result is None:
                self.run()

    # -------------------------------------------------------------------------
    def set_sql(self, sql: str, run: bool = False) -> None:
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", sql)
        w.highlight_sql(self.editor)
        if run:
            self.run()

    def _load_example(self, title: str) -> None:
        self.set_sql(f"-- {title}\n{self.examples[title]}", run=True)
        self.example_menu.set("Examples")

    def _on_ctrl_enter(self, _event):
        self.run()
        return "break"

    def _schedule_highlight(self, _event=None) -> None:
        if self._hl_job:
            self.after_cancel(self._hl_job)
        self._hl_job = self.after(150, lambda: w.highlight_sql(self.editor))

    def _statement(self) -> str:
        try:
            selected = self.editor.get("sel.first", "sel.last")
            if selected.strip():
                return selected
        except Exception:
            pass
        return self.editor.get("1.0", "end-1c")

    def run(self) -> None:
        sql = self._statement().strip()
        if not sql:
            return
        try:
            result = self.app.ro_db.query(sql)
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            if "readonly" in msg or "read-only" in msg:
                msg = "This console is read-only. " + msg
            if "interrupted" in msg:
                msg = "Query cancelled after the time limit."
            self.status.configure(text=f"Error: {msg}", text_color=t.ROSE)
            return
        except (sqlite3.Error, ValueError) as exc:
            self.status.configure(text=f"Error: {exc}", text_color=t.ROSE)
            return
        self.last_result = result
        self.table.sort_col = None
        self.table.set_columns([(c, c, 130, "w") for c in result.columns] or [("info", "Result", 300)])
        self.table.set_rows(result.rows if result.columns else [("Statement executed",)])
        note = "  (truncated)" if result.truncated else ""
        self.status.configure(
            text=f"{len(result.rows)} rows{note}  /  {len(result.columns)} columns  /  {result.elapsed_ms:.1f} ms",
            text_color=t.GREEN)

    def _export(self) -> None:
        if not self.last_result or not self.last_result.columns:
            self.status.configure(text="Run a query first.", text_color=t.AMBER)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], initialfile="query.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.last_result.columns)
            writer.writerows(self.last_result.rows)
        self.status.configure(text=f"Exported {len(self.last_result.rows)} rows to {path}", text_color=t.GREEN)

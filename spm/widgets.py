"""Custom widgets: canvas charts, stat cards, panels and a themed data table."""

from __future__ import annotations

import math
import re
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Iterable, Sequence

import customtkinter as ctk

from . import theme as t

SCALE = 1.0      # set by the App from the screen DPI
FONTS = None     # set by the App (theme.init_fonts)


def px(value: float) -> int:
    return int(round(value * SCALE))


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


# -----------------------------------------------------------------------------
@dataclass
class Series:
    label: str
    color: str
    data: deque


class LineChart(tk.Canvas):
    """Smooth, right-aligned time-series chart with optional area fill."""

    def __init__(self, master, series: Sequence[Series], max_points: int = 60,
                 y_max: float | None = 100.0, min_y_max: float = 1.0,
                 bg: str = t.CARD, height: int = 120, show_labels: bool = True,
                 show_grid: bool = True, fill: bool = True,
                 fmt: Callable[[float], str] = lambda v: f"{v:.0f}%") -> None:
        super().__init__(master, bg=bg, height=px(height), highlightthickness=0, bd=0)
        self.series = list(series)
        self.max_points = max_points
        self.y_max = y_max
        self.min_y_max = min_y_max
        self.bg = bg
        self.show_labels = show_labels
        self.show_grid = show_grid
        self.fill = fill
        self.fmt = fmt
        self.bind("<Configure>", lambda _e: self.redraw())

    def _scale_max(self) -> float:
        if self.y_max is not None:
            return self.y_max
        peak = max((max(s.data) for s in self.series if s.data), default=0.0)
        peak = max(peak * 1.2, self.min_y_max)
        magnitude = 10 ** math.floor(math.log10(peak))
        for step in (1, 2, 2.5, 5, 10):
            if peak <= step * magnitude:
                return step * magnitude
        return peak

    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        pl = px(58) if self.show_labels else px(2)
        pr, pt, pb = px(8), px(8), px(6)
        pw, ph = w - pl - pr, h - pt - pb
        y_max = self._scale_max()

        if self.show_grid:
            for i in range(5):
                y = pt + ph * i / 4
                self.create_line(pl, y, w - pr, y, fill=t.GRID,
                                 dash=(2, 4) if i < 4 else None)
                if self.show_labels:
                    self.create_text(pl - px(10), y, text=self.fmt(y_max * (4 - i) / 4),
                                     anchor="e", fill=t.DIM, font=(t.FONT, 8))

        step = pw / max(self.max_points - 1, 1)
        lines = []
        for s in self.series:
            data = list(s.data)[-self.max_points:]
            if len(data) < 2:
                continue
            n = len(data)
            pts: list[float] = []
            for i, v in enumerate(data):
                pts.append(pl + pw - (n - 1 - i) * step)
                pts.append(pt + ph - (min(max(v, 0.0), y_max) / y_max) * ph)
            lines.append((s, pts))

        if self.fill:
            for s, pts in lines:
                base = pt + ph
                # two stacked bands give a soft vertical gradient
                self.create_polygon([pts[0], base, *pts, pts[-2], base],
                                    fill=t.mix(s.color, self.bg, 0.88), outline="")
        for s, pts in lines:
            self.create_line(*pts, fill=s.color, width=max(1.6, 2.0 * SCALE),
                             smooth=True, capstyle="round", joinstyle="round")
            x, y = pts[-2], pts[-1]
            r = 3.5 * SCALE
            halo = r * 2.2
            self.create_oval(x - halo, y - halo, x + halo, y + halo,
                             fill=t.mix(s.color, self.bg, 0.75), outline="")
            self.create_oval(x - r, y - r, x + r, y + r, fill=s.color, outline="")


class CoreBars(tk.Canvas):
    """Per-core utilisation as a grid of rounded vertical bars."""

    def __init__(self, master, bg: str = t.CARD, height: int = 160) -> None:
        super().__init__(master, bg=bg, height=px(height), highlightthickness=0, bd=0)
        self.bg = bg
        self.values: list[float] = []
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_values(self, values: Iterable[float]) -> None:
        self.values = list(values)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        n = len(self.values)
        w, h = self.winfo_width(), self.winfo_height()
        if not n or w < 20 or h < 20:
            return
        rows = 1 if n <= 16 else 2
        per_row = math.ceil(n / rows)
        gap = px(6)
        label_h = px(16)
        row_h = (h - gap * (rows - 1)) / rows
        bar_w = (w - gap * (per_row - 1)) / per_row
        for idx, value in enumerate(self.values):
            row, col = divmod(idx, per_row)
            x1 = col * (bar_w + gap)
            x2 = x1 + bar_w
            y1 = row * (row_h + gap)
            y2 = y1 + row_h - label_h
            r = min(px(5), bar_w / 2)
            round_rect(self, x1, y1, x2, y2, r, fill=t.TRACK, outline="")
            fill_h = (y2 - y1) * min(max(value, 0.0), 100.0) / 100.0
            if fill_h > 1:
                round_rect(self, x1, y2 - fill_h, x2, y2, r,
                           fill=t.level_color(value), outline="")
            self.create_text((x1 + x2) / 2, y2 + label_h / 2 + px(1),
                             text=f"{value:.0f}" if bar_w > px(18) else "",
                             fill=t.MUTED, font=(t.FONT, 8))


# -----------------------------------------------------------------------------
class Panel(ctk.CTkFrame):
    """Rounded card with a header row (title, optional subtitle, right-side slot)."""

    def __init__(self, master, title: str, subtitle: str = "", **kw) -> None:
        super().__init__(master, fg_color=t.CARD, corner_radius=14,
                         border_width=1, border_color=t.BORDER, **kw)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=title, font=FONTS.h2, text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.subtitle = ctk.CTkLabel(head, text=subtitle, font=FONTS.small,
                                     text_color=t.MUTED, anchor="w")
        if subtitle:
            self.subtitle.grid(row=1, column=0, sticky="w")
        self.slot = ctk.CTkFrame(head, fg_color="transparent", width=1, height=1)
        self.slot.grid(row=0, column=1, rowspan=2, sticky="e")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    def legend(self, items: Sequence[tuple[str, str]]) -> None:
        for label, color in items:
            box = ctk.CTkFrame(self.slot, fg_color="transparent")
            box.pack(side="left", padx=(12, 0))
            ctk.CTkFrame(box, width=10, height=10, corner_radius=5,
                         fg_color=color).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(box, text=label, font=FONTS.small, text_color=t.MUTED).pack(side="left")


class StatCard(ctk.CTkFrame):
    """Headline metric with a caption, sub-line and a sparkline."""

    def __init__(self, master, title: str, accent: str, y_max: float | None = 100.0,
                 min_y_max: float = 1.0) -> None:
        super().__init__(master, fg_color=t.CARD, corner_radius=14,
                         border_width=1, border_color=t.BORDER)
        self.accent = accent
        self.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        ctk.CTkFrame(top, width=8, height=8, corner_radius=4, fg_color=accent).pack(side="left")
        ctk.CTkLabel(top, text=title.upper(), font=FONTS.caption,
                     text_color=t.MUTED).pack(side="left", padx=8)
        self.badge = ctk.CTkLabel(top, text="", font=FONTS.caption, text_color=accent,
                                  fg_color=t.mix(accent, t.CARD, 0.85), corner_radius=8,
                                  height=20)
        self.badge.pack(side="right")
        self.value = ctk.CTkLabel(self, text="--", font=FONTS.metric, text_color=t.TEXT, anchor="w")
        self.value.grid(row=1, column=0, sticky="w", padx=18, pady=(6, 0))
        self.sub = ctk.CTkLabel(self, text="", font=FONTS.small, text_color=t.MUTED, anchor="w")
        self.sub.grid(row=2, column=0, sticky="w", padx=18)
        self.data: deque = deque(maxlen=60)
        self.chart = LineChart(self, [Series("", accent, self.data)], max_points=60,
                               y_max=y_max, min_y_max=min_y_max, height=42,
                               show_labels=False, show_grid=False)
        self.chart.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 14))

    def update_card(self, value: str, sub: str, badge: str) -> None:
        self.value.configure(text=value)
        self.sub.configure(text=sub)
        self.badge.configure(text=f"  {badge}  " if badge else "")
        self.chart.redraw()


# -----------------------------------------------------------------------------
def _sort_key(value):
    if value is None or value == "":
        return (2, 0)
    if isinstance(value, (int, float)):
        return (0, value)
    try:
        return (0, float(str(value).replace(",", "")))
    except ValueError:
        return (1, str(value).lower())


class DataTable(ctk.CTkFrame):
    """ttk.Treeview restyled to match the dark theme, with click-to-sort headers.

    Two feeding modes:
      * set_rows(rows)  - replace everything, sorted client-side (console, reports)
      * sync(items)     - incremental update keyed by iid that keeps selection
                          and scroll position (live process list)
    """

    def __init__(self, master, height: int = 10, horizontal: bool = False,
                 on_sort: Callable[[str], None] | None = None,
                 fg_color: str = t.CARD, border: bool = True) -> None:
        super().__init__(master, fg_color=fg_color, corner_radius=12,
                         border_width=1 if border else 0, border_color=t.BORDER)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, style="SPM.Treeview", show="headings",
                                 selectmode="browse", height=height)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 8 if not horizontal else 0))
        vsb = ctk.CTkScrollbar(self, command=self.tree.yview, button_color=t.BORDER,
                               button_hover_color=t.DIM, width=12, height=40)
        vsb.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=8)
        self.tree.configure(yscrollcommand=vsb.set)
        if horizontal:
            hsb = ctk.CTkScrollbar(self, orientation="horizontal", command=self.tree.xview,
                                   button_color=t.BORDER, button_hover_color=t.DIM, height=12, width=40)
            hsb.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 6))
            self.tree.configure(xscrollcommand=hsb.set)
        self.tree.tag_configure("odd", background=t.ROW_ALT)
        self.tree.tag_configure("even", background=t.CARD)
        self.on_sort = on_sort
        self.sort_col: str | None = None
        self.sort_desc = False
        self._headings: dict[str, str] = {}
        self._rows: list[tuple] = []
        self._columns: list[str] = []

    # -- columns ---------------------------------------------------------------
    def set_columns(self, columns: Sequence[tuple]) -> None:
        """columns: (id, heading, width, anchor='w', stretch=True)."""
        ids = [c[0] for c in columns]
        self._columns = ids
        self.tree.configure(columns=ids, displaycolumns=ids)
        self._headings = {}
        for col in columns:
            cid, heading, width = col[0], col[1], col[2]
            anchor = col[3] if len(col) > 3 else "w"
            stretch = col[4] if len(col) > 4 else True
            self._headings[cid] = heading
            self.tree.heading(cid, text=heading, anchor=anchor,
                              command=lambda c=cid: self._header_click(c))
            self.tree.column(cid, width=px(width), minwidth=px(50), anchor=anchor, stretch=stretch)
        self._refresh_heading_arrows()

    def _header_click(self, col: str) -> None:
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, True
        self._refresh_heading_arrows()
        if self.on_sort:
            self.on_sort(col)
        else:
            self._render()

    def set_sort(self, col: str, desc: bool) -> None:
        self.sort_col, self.sort_desc = col, desc
        self._refresh_heading_arrows()

    def _refresh_heading_arrows(self) -> None:
        for cid, heading in self._headings.items():
            arrow = ""
            if cid == self.sort_col:
                arrow = "  ▾" if self.sort_desc else "  ▴"
            self.tree.heading(cid, text=heading + arrow)

    # -- full replace ----------------------------------------------------------
    def set_rows(self, rows: Sequence[tuple]) -> None:
        self._rows = list(rows)
        self._render()

    def _render(self) -> None:
        rows = self._rows
        if self.sort_col in self._columns:
            idx = self._columns.index(self.sort_col)
            rows = sorted(rows, key=lambda r: _sort_key(r[idx]), reverse=self.sort_desc)
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(rows):
            values = ["NULL" if v is None else v for v in row]
            self.tree.insert("", "end", values=values, tags=("odd" if i % 2 else "even",))

    # -- incremental -----------------------------------------------------------
    def sync(self, items: Sequence[tuple[str, tuple]]) -> None:
        tree = self.tree
        existing = set(tree.get_children())
        wanted = []
        for idx, (iid, values) in enumerate(items):
            tag = ("odd" if idx % 2 else "even",)
            if iid in existing:
                tree.item(iid, values=values, tags=tag)
            else:
                tree.insert("", "end", iid=iid, values=values, tags=tag)
            if tree.index(iid) != idx:
                tree.move(iid, "", idx)
            wanted.append(iid)
        stale = existing.difference(wanted)
        if stale:
            tree.delete(*stale)

    def selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None


# -----------------------------------------------------------------------------
SQL_KEYWORDS = (
    "SELECT FROM WHERE GROUP BY ORDER HAVING LIMIT OFFSET JOIN LEFT RIGHT INNER OUTER CROSS ON AS "
    "AND OR NOT IN IS NULL LIKE GLOB BETWEEN CASE WHEN THEN ELSE END DISTINCT WITH UNION ALL "
    "EXCEPT INTERSECT ASC DESC OVER PARTITION ROWS RANGE CAST EXISTS INSERT UPDATE DELETE "
    "CREATE VIEW TABLE INDEX VALUES SET PRAGMA EXPLAIN QUERY PLAN"
).split()
SQL_FUNCTIONS = (
    "COUNT SUM AVG MIN MAX ROUND ABS COALESCE IFNULL NULLIF LENGTH LOWER UPPER SUBSTR "
    "DATETIME DATE TIME STRFTIME JULIANDAY UNIXEPOCH ROW_NUMBER RANK DENSE_RANK LAG LEAD "
    "FIRST_VALUE LAST_VALUE NTILE TOTAL GROUP_CONCAT PRINTF INSTR REPLACE TRIM"
).split()
_TOKEN_RE = re.compile(
    r"(?P<comment>--[^\n]*)"
    r"|(?P<string>'(?:[^']|'')*'?)"
    r"|(?P<param>:[A-Za-z_]\w*)"
    r"|(?P<number>\b\d+(?:\.\d+)?\b)"
    r"|(?P<word>\b[A-Za-z_]\w*\b)"
)
_KW = set(SQL_KEYWORDS)
_FN = set(SQL_FUNCTIONS)


def configure_sql_tags(textbox: ctk.CTkTextbox) -> None:
    textbox.tag_config("kw", foreground=t.VIOLET)
    textbox.tag_config("fn", foreground=t.CYAN)
    textbox.tag_config("str", foreground=t.GREEN)
    textbox.tag_config("num", foreground=t.AMBER)
    textbox.tag_config("param", foreground=t.ROSE)
    textbox.tag_config("comment", foreground=t.DIM)


def highlight_sql(textbox: ctk.CTkTextbox) -> None:
    text = textbox.get("1.0", "end-1c")
    for tag in ("kw", "fn", "str", "num", "param", "comment"):
        textbox.tag_remove(tag, "1.0", "end")
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        tag = None
        if kind == "comment":
            tag = "comment"
        elif kind == "string":
            tag = "str"
        elif kind == "param":
            tag = "param"
        elif kind == "number":
            tag = "num"
        elif kind == "word":
            word = m.group().upper()
            if word in _KW:
                tag = "kw"
            elif word in _FN:
                tag = "fn"
        if tag:
            textbox.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")

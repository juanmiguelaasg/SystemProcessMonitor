"""Colour palette, fonts and small formatting helpers shared by the UI."""

from __future__ import annotations

import tkinter.font as tkfont
from datetime import timedelta
from types import SimpleNamespace

import customtkinter as ctk

# --- palette -----------------------------------------------------------------
BG = "#0b0e14"
SURFACE = "#11151e"
CARD = "#161b26"
CARD_HOVER = "#1e2433"
ROW_ALT = "#1a2030"
BORDER = "#242b3b"
GRID = "#222939"
TRACK = "#232a3a"

TEXT = "#e7eaf1"
MUTED = "#8b93a7"
DIM = "#5d6578"

CYAN = "#22d3ee"
VIOLET = "#a78bfa"
GREEN = "#34d399"
AMBER = "#fbbf24"
ROSE = "#fb7185"
BLUE = "#60a5fa"

FONT = "Segoe UI"
MONO = "Consolas"


def mix(color_a: str, color_b: str, t: float) -> str:
    """Blend two hex colours. t=0 returns color_a, t=1 returns color_b."""
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def level_color(pct: float) -> str:
    if pct >= 85:
        return ROSE
    if pct >= 60:
        return AMBER
    return GREEN


def init_fonts(root) -> SimpleNamespace:
    """Pick the best available families and build the CTk fonts."""
    global FONT, MONO
    families = set(tkfont.families(root))
    for candidate in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica"):
        if candidate in families:
            FONT = candidate
            break
    for candidate in ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New"):
        if candidate in families:
            MONO = candidate
            break
    display = "Segoe UI Variable Display" if "Segoe UI Variable Display" in families else FONT
    return SimpleNamespace(
        h1=ctk.CTkFont(display, 24, "bold"),
        h2=ctk.CTkFont(display, 15, "bold"),
        brand=ctk.CTkFont(display, 16, "bold"),
        metric=ctk.CTkFont(display, 30, "bold"),
        body=ctk.CTkFont(FONT, 13),
        body_bold=ctk.CTkFont(FONT, 13, "bold"),
        small=ctk.CTkFont(FONT, 12),
        caption=ctk.CTkFont(FONT, 11, "bold"),
        mono=ctk.CTkFont(MONO, 13),
        mono_small=ctk.CTkFont(MONO, 11),
    )


# --- formatting --------------------------------------------------------------
def fmt_bytes(value: float, precision: int = 1) -> str:
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.{precision}f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def fmt_rate(value: float) -> str:
    return fmt_bytes(value) + "/s"


def fmt_duration(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    days, rem = td.days, td.seconds
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s"

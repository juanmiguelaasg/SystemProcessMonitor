"""Desktop notifications for watchdog alerts: stacked toast windows plus a system sound."""

from __future__ import annotations

import os

import customtkinter as ctk

from . import theme as t
from . import widgets as w

SEVERITY_COLORS = {"info": t.BLUE, "warning": t.AMBER, "critical": t.ROSE}

try:  # Windows only
    import winsound
except ImportError:  # pragma: no cover
    winsound = None


def play_sound(severity: str) -> None:
    if winsound is None or severity == "info":
        return
    flag = winsound.MB_ICONHAND if severity == "critical" else winsound.MB_ICONEXCLAMATION
    try:
        winsound.MessageBeep(flag)
    except RuntimeError:
        pass


class Toast(ctk.CTkToplevel):
    WIDTH, HEIGHT = 360, 96

    def __init__(self, manager: "ToastManager", alert) -> None:
        super().__init__(manager.root, fg_color=t.BORDER)
        self.manager = manager
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        color = SEVERITY_COLORS.get(alert.severity, t.CYAN)

        card = ctk.CTkFrame(self, fg_color=t.CARD, corner_radius=0)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        ctk.CTkFrame(card, width=5, fg_color=color, corner_radius=0).pack(side="left", fill="y")
        text = ctk.CTkFrame(card, fg_color="transparent")
        text.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        head = ctk.CTkFrame(text, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=alert.severity.upper(), font=w.FONTS.caption, text_color=color,
                     height=16).pack(side="left")
        ctk.CTkLabel(head, text="  Watchdog agent", font=w.FONTS.caption, text_color=t.DIM,
                     height=16).pack(side="left")
        ctk.CTkLabel(text, text=alert.title, font=w.FONTS.body_bold, text_color=t.TEXT,
                     anchor="w", height=20).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(text, text=alert.message, font=w.FONTS.small, text_color=t.MUTED, anchor="w",
                     justify="left", wraplength=w.px(self.WIDTH - 50), height=18).pack(fill="x")

        for widget in (self, card, text, head, *text.winfo_children(), *head.winfo_children()):
            widget.bind("<Button-1>", lambda _e: self.manager.open_from_toast(self))
        self._job = self.after(manager.lifetime_ms, self.close)

    def place_at(self, index: int) -> None:
        scale = w.SCALE
        sw = self.winfo_screenwidth() / scale
        sh = self.winfo_screenheight() / scale
        x = sw - self.WIDTH - 16
        y = sh - 60 - (self.HEIGHT + 10) * (index + 1)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{int(x * scale)}+{int(y * scale)}")

    def close(self) -> None:
        try:
            self.after_cancel(self._job)
        except Exception:
            pass
        self.manager.remove(self)
        self.destroy()


class ToastManager:
    """Shows at most `max_visible` toasts stacked in the bottom-right corner."""

    def __init__(self, root, on_open=None, lifetime_ms: int = 9000, max_visible: int = 4,
                 sound: bool = True) -> None:
        self.root = root
        self.on_open = on_open
        self.lifetime_ms = lifetime_ms
        self.max_visible = max_visible
        self.sound = sound and os.name == "nt"
        self.muted = False
        self.toasts: list[Toast] = []

    def show(self, alert) -> None:
        if self.muted:
            return
        while len(self.toasts) >= self.max_visible:
            self.toasts[0].close()
        toast = Toast(self, alert)
        self.toasts.append(toast)
        self._restack()
        if self.sound:
            play_sound(alert.severity)

    def remove(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
            self._restack()

    def _restack(self) -> None:
        for i, toast in enumerate(self.toasts):
            toast.place_at(i)

    def open_from_toast(self, toast: Toast) -> None:
        toast.close()
        if self.on_open:
            self.on_open()

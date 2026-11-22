from __future__ import annotations

import customtkinter as ctk

from .. import theme as t
from .. import widgets as w


class Page(ctk.CTkFrame):
    """A full-height page with a title header and a body area."""

    title = ""
    subtitle = ""

    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color=t.BG, corner_radius=0)
        self.app = app
        self.visible = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 14))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=self.title, font=w.FONTS.h1, text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text=self.subtitle, font=w.FONTS.body, text_color=t.MUTED,
                     anchor="w").grid(row=1, column=0, sticky="w")
        self.actions = ctk.CTkFrame(header, fg_color="transparent", width=1, height=1)
        self.actions.grid(row=0, column=1, rowspan=2, sticky="e")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 22))

    def on_sample(self, sample) -> None:  # new data from the collector
        pass

    def on_show(self) -> None:
        pass


def accent_button(master, text: str, command, color: str = t.CYAN, width: int = 110, **kw):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34,
                         corner_radius=10, fg_color=color,
                         hover_color=t.mix(color, "#ffffff", 0.18),
                         text_color="#0b0e14", font=w.FONTS.body_bold, **kw)


def ghost_button(master, text: str, command, width: int = 110, **kw):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34,
                         corner_radius=10, fg_color=t.CARD, hover_color=t.CARD_HOVER,
                         border_width=1, border_color=t.BORDER, text_color=t.TEXT,
                         font=w.FONTS.body, **kw)

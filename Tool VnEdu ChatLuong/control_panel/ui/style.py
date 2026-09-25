"""Style ttk của dashboard."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..config import (
    MUTED_TEXT_COLOR,
    PANEL_COLOR,
    SUBTLE_TEXT_COLOR,
    SURFACE_COLOR,
    TEXT_COLOR,
    UI_FONT_FAMILY,
)


def configure_style(root: tk.Tk) -> None:
    """Apply conservative ttk styling for the control-panel dashboard."""

    root.configure(background=SURFACE_COLOR)
    root.option_add("*Font", f"{{{UI_FONT_FAMILY}}} 10")

    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure(".", font=(UI_FONT_FAMILY, 10), foreground=TEXT_COLOR)
    style.configure("TFrame", background=SURFACE_COLOR)
    style.configure("App.TFrame", background=SURFACE_COLOR)
    style.configure("Panel.TFrame", background=PANEL_COLOR)
    style.configure("TLabel", background=SURFACE_COLOR, foreground=TEXT_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("Hero.TLabel", background=SURFACE_COLOR, foreground=TEXT_COLOR, font=(UI_FONT_FAMILY, 20, "bold"))
    style.configure(
        "DashboardTitle.TLabel",
        background=SURFACE_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 19, "bold"),
    )
    style.configure("Muted.TLabel", background=SURFACE_COLOR, foreground=MUTED_TEXT_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("Status.TLabel", background=SURFACE_COLOR, foreground="#334155", font=(UI_FONT_FAMILY, 10))
    style.configure("Footer.TLabel", background=SURFACE_COLOR, foreground=SUBTLE_TEXT_COLOR, font=(UI_FONT_FAMILY, 9))
    style.configure("Panel.TLabel", background=PANEL_COLOR, foreground=TEXT_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure(
        "PanelTitle.TLabel",
        background=PANEL_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 12, "bold"),
    )
    style.configure(
        "CardTitle.TLabel",
        background=PANEL_COLOR,
        foreground="#0f172a",
        font=(UI_FONT_FAMILY, 11, "bold"),
    )
    style.configure(
        "DialogTitle.TLabel",
        background=SURFACE_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 17, "bold"),
    )
    style.configure(
        "DialogFieldTitle.TLabel",
        background=PANEL_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 10, "bold"),
    )
    style.configure(
        "PanelMuted.TLabel",
        background=PANEL_COLOR,
        foreground=MUTED_TEXT_COLOR,
        font=(UI_FONT_FAMILY, 10),
    )
    style.configure(
        "PanelStatus.TLabel",
        background=PANEL_COLOR,
        foreground="#334155",
        font=(UI_FONT_FAMILY, 10),
    )
    style.configure("TButton", font=(UI_FONT_FAMILY, 10), padding=(10, 6))
    style.configure("Primary.TButton", font=(UI_FONT_FAMILY, 10, "bold"), padding=(16, 7))
    style.configure("Secondary.TButton", font=(UI_FONT_FAMILY, 10), padding=(12, 6))
    style.configure("Tool.TButton", font=(UI_FONT_FAMILY, 10, "bold"), padding=(12, 6))
    style.configure("TCheckbutton", background=PANEL_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("App.TCheckbutton", background=SURFACE_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("TLabelframe.Label", font=(UI_FONT_FAMILY, 10, "bold"))

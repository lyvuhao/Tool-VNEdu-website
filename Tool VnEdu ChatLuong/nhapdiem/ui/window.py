"""Tiện ích cửa sổ Tkinter: vùng làm việc màn hình, style, focus."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..config import (
    APP_BG,
    APP_PANEL_BG,
    APP_PANEL_BORDER,
    APP_TABLE_HEADER,
    APP_TABLE_LINE,
    APP_TEXT,
)


def get_tk_work_area(widget: tk.Misc | None = None) -> tuple[int, int, int, int]:
    """Return the current monitor work area in Tk logical pixels, excluding the taskbar."""
    screen_w = int(widget.winfo_screenwidth()) if widget else 1280
    screen_h = int(widget.winfo_screenheight()) if widget else 720
    try:
        import ctypes

        user32 = ctypes.windll.user32
        phys_x = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
        phys_y = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
        phys_w = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
        phys_h = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
        if phys_w <= 0 or phys_h <= 0:
            raise RuntimeError("Invalid virtual screen metrics")

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        hwnd = 0
        if widget is not None:
            try:
                hwnd = int(widget.winfo_id())
            except Exception:
                hwnd = 0

        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
        else:
            rect = RECT()
            if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                raise RuntimeError("SPI_GETWORKAREA failed")

        scale_x = screen_w / max(phys_w, 1)
        scale_y = screen_h / max(phys_h, 1)
        x = int(round((int(rect.left) - phys_x) * scale_x))
        y = int(round((int(rect.top) - phys_y) * scale_y))
        w = int(round((int(rect.right) - int(rect.left)) * scale_x))
        h = int(round((int(rect.bottom) - int(rect.top)) * scale_y))
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, screen_w, screen_h


def fit_geometry_to_work_area(
    widget: tk.Misc,
    preferred_w: int,
    preferred_h: int,
    *,
    margin: int = 12,
    min_w: int = 1040,
    min_h: int = 660,
    chrome_w: int = 32,
    chrome_h: int = 52,
) -> tuple[int, int, int, int]:
    """Clamp Tk client geometry so the full native window stays inside work area."""
    work_x, work_y, work_w, work_h = get_tk_work_area(widget)
    available_w = max(1, work_w - margin * 2 - max(0, int(chrome_w)))
    available_h = max(1, work_h - margin * 2 - max(0, int(chrome_h)))
    min_w = min(max(1, int(min_w)), available_w)
    min_h = min(max(1, int(min_h)), available_h)
    width = min(max(int(preferred_w), min_w), available_w)
    height = min(max(int(preferred_h), min_h), available_h)
    x = work_x + (work_w - width) // 2
    y = work_y + margin
    return width, height, max(work_x + margin, x), max(work_y + margin, y)


def apply_app_styles(style: ttk.Style) -> None:
    """Apply one consistent desktop design system for the Tk/ttk interface."""
    try:
        style.theme_use("clam" if "clam" in style.theme_names() else style.theme_use())
    except tk.TclError:
        pass
    style.configure(".", font=("Segoe UI", 10))
    style.configure("TFrame", background=APP_BG)
    style.configure("TLabelframe", background=APP_BG, bordercolor=APP_PANEL_BORDER, relief="solid")
    style.configure(
        "TLabelframe.Label",
        background=APP_BG,
        foreground=APP_TEXT,
        font=("Segoe UI", 10, "bold"),
    )
    style.configure("TLabel", background=APP_BG, foreground=APP_TEXT)
    style.configure("TCheckbutton", background=APP_BG, foreground=APP_TEXT)
    style.configure("TRadiobutton", background=APP_BG, foreground=APP_TEXT)
    style.configure("TEntry", padding=(5, 4))
    style.configure("TCombobox", padding=(5, 4))
    style.configure("TButton", padding=(12, 6), foreground=APP_TEXT)
    style.map("TButton", background=[("active", "#edf2f7")])
    style.configure(
        "Treeview",
        background=APP_PANEL_BG,
        fieldbackground=APP_PANEL_BG,
        foreground=APP_TEXT,
        rowheight=30,
        bordercolor=APP_TABLE_LINE,
        lightcolor=APP_TABLE_LINE,
        darkcolor=APP_TABLE_LINE,
    )
    style.configure(
        "Treeview.Heading",
        background=APP_TABLE_HEADER,
        foreground=APP_TEXT,
        font=("Segoe UI", 10, "bold"),
        padding=(7, 6),
        relief="solid",
    )
    style.map(
        "Treeview",
        background=[("selected", "#bfdbfe")],
        foreground=[("selected", "#0f172a")],
    )


def _is_text_input_focus(widget: object | None) -> bool:
    """Returns whether the focused widget should keep receiving Space normally."""
    if widget is None:
        return False
    try:
        widget_class = str(widget.winfo_class() or "").strip().lower()
    except Exception:
        widget_class = widget.__class__.__name__.strip().lower()
    return widget_class == "text" or widget_class.endswith("entry") or widget_class.endswith("combobox")

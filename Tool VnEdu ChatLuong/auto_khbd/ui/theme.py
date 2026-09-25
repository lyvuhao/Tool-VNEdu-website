"""Bảng màu, kích thước lưới TKB và tiện ích vùng làm việc màn hình."""

from __future__ import annotations

import logging
import tkinter as tk


# ######################################################################
# Section: wizard (UI)
# ######################################################################







# =====================================================================
# Constants — đồng bộ palette với auto_KHBD1.py
# =====================================================================

DEFAULT_CDP_PORT = 9224


# Color palette
CLR_NAVY        = "#111827"


CLR_LINK        = "#2563eb"


CLR_BODY        = "#111827"


CLR_SUBTLE      = "#374151"


CLR_HINT        = "#6b7280"


CLR_FOOTNOTE    = "#888888"


CLR_OK          = "#166534"


CLR_WARN        = "#d97706"


CLR_ERR         = "#dc2626"


CLR_PANEL_BG    = "#f6f8fb"


CLR_BORDER      = "#d1d5db"


CLR_GRID_LINE   = "#94a3b8"


CLR_SLOT_FILLED = "#dbeafe"


CLR_SLOT_FILLED_BORDER = "#2563eb"


CLR_SLOT_FILLED_TEXT = "#1e3a8a"


CLR_SLOT_EMPTY  = "#f8fafc"


CLR_SLOT_EMPTY_HOVER = "#e0f2fe"


CLR_SLOT_FILLED_HOVER = "#bfdbfe"


CLR_SLOT_HOVER_BORDER = "#0284c7"


# Slot selection / drag visual feedback colors.
CLR_SLOT_SELECTED_BG = "#fffbeb"      # vàng nhạt cho ô đang selected (tương tự highlight Excel)


CLR_SLOT_SELECTED_BORDER = "#f59e0b"


CLR_SLOT_DRAG_TARGET_BG = "#dcfce7"   # xanh nhạt cho ô đang hover trong drag


# Threshold pixels: chỉ trigger drag khi mouse di chuyển vượt ngưỡng,
# tránh click thường bị nhận nhầm thành drag (tay run, click 2 lần liên tiếp).
SLOT_DRAG_THRESHOLD_PX = 8


CLR_GRID_HEADER_BG = "#eef2ff"


CLR_GRID_HEADER_FG = "#1f2937"


CLR_GRID_HEADER_BORDER = CLR_GRID_LINE


CLR_BANNER_INFO_BG = "#fff3d9"


CLR_BANNER_INFO_FG = "#9c6500"


# Layout
DAYS = [(2, "Thứ 2"), (3, "Thứ 3"), (4, "Thứ 4"), (5, "Thứ 5"),
        (6, "Thứ 6"), (7, "Thứ 7"), (8, "CN")]


TIETS_BY_BUOI = [
    ("Sáng", 1, [1, 2, 3, 4, 5]),
    ("Chiều", 2, [1, 2, 3, 4, 5]),
]


def get_tk_work_area(widget: tk.Misc | None = None) -> tuple[int, int, int, int]:
    """Return Windows work area in Tk logical pixels, excluding the taskbar."""
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
    except Exception as exc:
        logging.debug("Could not resolve Windows work area: %s", exc)
    return 0, 0, screen_w, screen_h


def fit_geometry_to_work_area(
    widget: tk.Misc,
    preferred_w: int,
    preferred_h: int,
    *,
    margin: int = 10,
    min_w: int = 720,
    min_h: int = 520,
    anchor: str = "top_center",
    chrome_w: int = 32,
    chrome_h: int = 52,
) -> tuple[int, int, int, int]:
    """Clamp Tk client geometry so the full native window stays inside work area."""
    work_x, work_y, work_w, work_h = get_tk_work_area(widget)
    # Tk geometry sizes the client area; Windows still adds title bar + borders.
    available_w = max(1, work_w - margin * 2 - max(0, int(chrome_w)))
    available_h = max(1, work_h - margin * 2 - max(0, int(chrome_h)))
    min_w = min(max(1, int(min_w)), available_w)
    min_h = min(max(1, int(min_h)), available_h)
    width = min(max(int(preferred_w), min_w), available_w)
    height = min(max(int(preferred_h), min_h), available_h)

    x = work_x + (work_w - width) // 2
    y = work_y + margin if anchor == "top_center" else work_y + (work_h - height) // 2

    left_limit = work_x + margin
    right_limit = work_x + max(work_w - width - margin, 0)
    top_limit = work_y + margin
    bottom_limit = work_y + max(work_h - height - margin, 0)
    if right_limit >= left_limit:
        x = min(max(x, left_limit), right_limit)
    if bottom_limit >= top_limit:
        y = min(max(y, top_limit), bottom_limit)
    return width, height, x, y

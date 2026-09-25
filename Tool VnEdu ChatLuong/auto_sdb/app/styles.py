"""Style ttk và ảnh checkbox."""

import tkinter as tk

from ..config import (
    UI_BG_APP,
    UI_BORDER,
    UI_DISABLED_BG,
    UI_DISABLED_TEXT,
    UI_PRIMARY,
    UI_PRIMARY_ACTIVE,
    UI_SUCCESS,
    UI_SUCCESS_ACTIVE,
    UI_SURFACE_ALT,
    UI_TEXT,
    UI_TEXT_MUTED,
)


class StylesMixin:
    """Style ttk và ảnh checkbox."""

    # -----------------------------------------------------------------
    # UI SETUP
    # -----------------------------------------------------------------

    def _setup_styles(self):
        """Khai báo các style ttk dùng riêng cho app."""
        try:
            # `clam` cho phép custom màu nền button ổn định hơn trên Windows.
            self.style.theme_use("clam")
        except Exception:
            pass

        base_font = ("Segoe UI", 10)
        button_font = ("Segoe UI", 10, "bold")
        hint_font = ("Segoe UI", 9)

        self.style.configure(".", font=base_font)
        self.style.configure("TFrame", background=UI_BG_APP)
        self.style.configure("TLabel", background=UI_BG_APP, foreground=UI_TEXT, font=base_font)
        self.style.configure(
            "TLabelframe",
            background=UI_BG_APP,
            bordercolor=UI_BORDER,
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=UI_BG_APP,
            foreground=UI_TEXT,
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "Subtle.TButton",
            background=UI_SURFACE_ALT,
            foreground="#374151",
            padding=(12, 6),
            borderwidth=1,
            font=base_font,
        )
        self.style.map(
            "Subtle.TButton",
            background=[("disabled", UI_DISABLED_BG), ("pressed", "#e5e7eb"), ("active", "#eef2ff")],
            foreground=[("disabled", UI_DISABLED_TEXT)],
        )
        self.style.configure(
            "Primary.TButton",
            background=UI_PRIMARY,
            foreground="#ffffff",
            padding=(14, 7),
            borderwidth=1,
            font=button_font,
        )
        self.style.map(
            "Primary.TButton",
            background=[("disabled", "#bfdbfe"), ("pressed", UI_PRIMARY_ACTIVE), ("active", "#3b82f6")],
            foreground=[("disabled", "#eff6ff")],
        )
        self.style.configure(
            "Danger.TButton",
            background="#fee2e2",
            foreground="#7f1d1d",
            padding=(12, 6),
            borderwidth=1,
            font=button_font,
        )
        self.style.map(
            "Danger.TButton",
            background=[("disabled", "#f5e8e8"), ("pressed", "#fecaca"), ("active", "#fee2e2")],
            foreground=[("disabled", "#a8a29e")],
        )
        self.style.configure(
            "Highlight.TButton",
            background="#fef3c7",
            foreground="#78350f",
            padding=(12, 6),
            borderwidth=1,
            font=base_font,
        )
        self.style.map(
            "Highlight.TButton",
            background=[
                ("disabled", "#f7f0d2"),
                ("pressed", "#fde68a"),
                ("active", "#fef3c7"),
            ],
            foreground=[
                ("disabled", UI_DISABLED_TEXT),
            ],
        )
        self.style.configure(
            "QuickGreen.TButton",
            background=UI_SUCCESS,
            foreground="#ffffff",
            padding=(14, 7),
            borderwidth=1,
            font=button_font,
        )
        self.style.map(
            "QuickGreen.TButton",
            background=[
                ("disabled", "#bbf7d0"),
                ("pressed", UI_SUCCESS_ACTIVE),
                ("active", "#22c55e"),
            ],
            foreground=[
                ("disabled", "#f0fdf4"),
            ],
        )
        self.style.configure(
            "LiveGreen.Horizontal.TProgressbar",
            troughcolor="#e5e7eb",
            background=UI_SUCCESS,
            darkcolor=UI_SUCCESS_ACTIVE,
            lightcolor="#4ade80",
            bordercolor=UI_BORDER,
            thickness=16,
        )
        self.style.configure(
            "LiveGreen.TLabel",
            background=UI_BG_APP,
            foreground="#166534",
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "Hint.TLabel",
            background=UI_BG_APP,
            foreground=UI_TEXT_MUTED,
            font=hint_font,
        )

    def _build_schedule_checkbox_image(self, size=18, checked=False, disabled=False):
        """Tạo ảnh checkbox vuông cho grid lịch dạy."""
        img = tk.PhotoImage(width=size, height=size)
        bg = "#ffffff" if not disabled else "#f0f0f0"
        border = "#787878" if not disabled else "#b8b8b8"
        mark = "#111111" if not disabled else "#8a8a8a"

        img.put(bg, to=(0, 0, size, size))
        img.put(border, to=(0, 0, size, 1))
        img.put(border, to=(0, size - 1, size, size))
        img.put(border, to=(0, 0, 1, size))
        img.put(border, to=(size - 1, 0, size, size))

        if checked:
            mark_size = max(8, size - 8)
            start = max(1, (size - mark_size) // 2)
            thickness = 2
            for idx in range(mark_size):
                x = start + idx
                y_main = start + idx
                y_cross = start + mark_size - 1 - idx
                for offset in range(thickness):
                    y1 = y_main + offset
                    y2 = y_cross - offset
                    if 0 <= x < size and 0 <= y1 < size:
                        img.put(mark, to=(x, y1, x + 1, y1 + 1))
                    if 0 <= x < size and 0 <= y2 < size:
                        img.put(mark, to=(x, y2, x + 1, y2 + 1))
        return img

    def _init_schedule_checkbox_images(self):
        """Khởi tạo bộ ảnh checkbox lớn hơn cho grid lịch dạy."""
        self._sched_checkbox_images = {
            "off": self._build_schedule_checkbox_image(checked=False, disabled=False),
            "on": self._build_schedule_checkbox_image(checked=True, disabled=False),
            "off_disabled": self._build_schedule_checkbox_image(checked=False, disabled=True),
            "on_disabled": self._build_schedule_checkbox_image(checked=True, disabled=True),
        }

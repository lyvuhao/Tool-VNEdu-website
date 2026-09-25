"""Cửa sổ phủ hiển thị tiến độ khi chạy."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..theme import CLR_BODY, CLR_BORDER, CLR_ERR, CLR_HINT, CLR_NAVY, CLR_PANEL_BG


# =====================================================================
# Dialog — SlotPickerDialog (chọn lớp + môn + phân môn cho 1 tiết)
# =====================================================================

class ProgressOverlayWindow(tk.Toplevel):
    """Floating progress overlay — góc dưới-phải màn hình, always-on-top.

    Compact ~280×120 px, hiển thị:
      - Mini title bar (drag được + nút thu nhỏ + nút đóng)
      - Progress bar tổng tiến trình (week_idx / total_weeks)
      - Status line ngắn (đang làm gì)
      - Stats line (đã xong / lỗi / extras)

    Không kill executor khi đóng — chỉ ẩn overlay.
    Auto-hide 5s sau khi nhận update_done().
    """

    WIDTH = 320
    HEIGHT = 130
    MARGIN_RIGHT = 24
    MARGIN_BOTTOM = 60   # tránh đè lên taskbar

    def __init__(self, master):
        super().__init__(master)
        # Borderless + topmost + slight transparency
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.95)
        except tk.TclError:
            pass
        self.configure(bg=CLR_PANEL_BG, highlightthickness=1,
                      highlightbackground=CLR_BORDER)

        # Anchor bottom-right
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = sw - self.WIDTH - self.MARGIN_RIGHT
        y = sh - self.HEIGHT - self.MARGIN_BOTTOM
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        self._minimized = False
        self._auto_hide_after_id: str | None = None
        self._drag_start: tuple[int, int] | None = None

        self._build_ui()

    def _build_ui(self):
        # Title bar (drag handle)
        self._title_bar = tk.Frame(
            self, bg=CLR_NAVY, height=24, cursor="fleur",
        )
        self._title_bar.pack(fill="x", side="top")
        self._title_bar.bind("<ButtonPress-1>", self._on_drag_start)
        self._title_bar.bind("<B1-Motion>", self._on_drag_motion)

        self._title_label = tk.Label(
            self._title_bar, bg=CLR_NAVY, fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            text="⚡ KHDH — Đang chuẩn bị…", anchor="w",
            padx=8,
        )
        self._title_label.pack(side="left", fill="x", expand=True)
        self._title_label.bind("<ButtonPress-1>", self._on_drag_start)
        self._title_label.bind("<B1-Motion>", self._on_drag_motion)

        # Mini button: thu/mở
        self._btn_min = tk.Label(
            self._title_bar, bg=CLR_NAVY, fg="#ffffff", text="—",
            font=("Segoe UI", 11, "bold"), padx=8, cursor="hand2",
        )
        self._btn_min.pack(side="right")
        self._btn_min.bind("<Button-1>", lambda e: self.toggle_minimize())

        # Close button (chỉ ẩn, không kill executor)
        self._btn_close = tk.Label(
            self._title_bar, bg=CLR_NAVY, fg="#ffcccc", text="×",
            font=("Segoe UI", 12, "bold"), padx=8, cursor="hand2",
        )
        self._btn_close.pack(side="right")
        self._btn_close.bind("<Button-1>", lambda e: self.hide())

        # Body
        self._body = tk.Frame(self, bg=CLR_PANEL_BG, padx=10, pady=8)
        self._body.pack(fill="both", expand=True)

        # Setup green style cho progress bar (đẹp hơn default xám của clam)
        try:
            style = ttk.Style(self)
            style.configure(
                "OverlayGreen.Horizontal.TProgressbar",
                background="#2fa549",        # xanh lá tươi
                troughcolor=CLR_PANEL_BG,
                bordercolor=CLR_BORDER,
                lightcolor="#37b551",
                darkcolor="#1f7d36",
                thickness=14,
            )
        except Exception:
            pass

        # Progress bar
        self._var_progress = tk.IntVar(value=0)
        self._progress = ttk.Progressbar(
            self._body,
            variable=self._var_progress,
            maximum=100,
            length=self.WIDTH - 30,
            mode="determinate",
            style="OverlayGreen.Horizontal.TProgressbar",
        )
        self._progress.pack(fill="x", pady=(2, 4))

        # Status line
        self._var_status = tk.StringVar(value="Đang chuẩn bị…")
        self._lbl_status = tk.Label(
            self._body, textvariable=self._var_status,
            bg=CLR_PANEL_BG, fg=CLR_BODY,
            font=("Segoe UI", 9), anchor="w", justify="left",
        )
        self._lbl_status.pack(fill="x")

        # Stats line
        self._var_stats = tk.StringVar(value="")
        self._lbl_stats = tk.Label(
            self._body, textvariable=self._var_stats,
            bg=CLR_PANEL_BG, fg=CLR_HINT,
            font=("Segoe UI", 8), anchor="w",
        )
        self._lbl_stats.pack(fill="x", pady=(2, 0))

    # -----------------------------------------------------------
    # Drag-to-move
    # -----------------------------------------------------------

    def _on_drag_start(self, event):
        self._drag_start = (event.x_root - self.winfo_x(),
                           event.y_root - self.winfo_y())

    def _on_drag_motion(self, event):
        if self._drag_start is None:
            return
        x = event.x_root - self._drag_start[0]
        y = event.y_root - self._drag_start[1]
        self.geometry(f"+{x}+{y}")

    # -----------------------------------------------------------
    # State updates (gọi từ ExecutorWorker event handler)
    # -----------------------------------------------------------

    def update_progress(self, current_idx: int, total: int,
                        week_num: int | None = None):
        """current_idx: tuần hiện tại đã xử lý xong (1-based). total: tổng tuần."""
        if total <= 0:
            return
        pct = int(min(100, max(0, (current_idx / total) * 100)))
        self._var_progress.set(pct)
        if week_num is not None:
            self._title_label.configure(
                text=f"⚡ Tuần {week_num} • {pct}% ({current_idx}/{total})"
            )

    def update_status(self, text: str):
        # Truncate dài
        if len(text) > 50:
            text = text[:47] + "…"
        self._var_status.set(text)

    def update_stats(self, done: int = 0, errors: int = 0,
                     extras: int = 0, skipped: int = 0):
        parts = [f"Đã xong: {done}"]
        if skipped:
            parts.append(f"Skip: {skipped}")
        if extras:
            parts.append(f"Bù: {extras}")
        if errors:
            parts.append(f"Lỗi: {errors}")
        self._var_stats.set("  ·  ".join(parts))

    def set_error_mode(self):
        """Chuyển progress bar sang màu đỏ khi có error."""
        try:
            style = ttk.Style(self)
            style.configure(
                "OverlayError.Horizontal.TProgressbar",
                background=CLR_ERR,
                troughcolor=CLR_PANEL_BG,
                bordercolor=CLR_BORDER,
                lightcolor="#d62a3f",
                darkcolor="#8a0014",
                thickness=14,
            )
            self._progress.configure(style="OverlayError.Horizontal.TProgressbar")
        except Exception:
            pass

    def reset_color(self):
        """Khôi phục màu xanh lá (default normal)."""
        try:
            self._progress.configure(style="OverlayGreen.Horizontal.TProgressbar")
        except Exception:
            pass

    def auto_hide_after(self, ms: int = 5000):
        if self._auto_hide_after_id:
            try:
                self.after_cancel(self._auto_hide_after_id)
            except Exception:
                pass
        self._auto_hide_after_id = self.after(ms, self.hide)

    # -----------------------------------------------------------
    # Show/hide/minimize
    # -----------------------------------------------------------

    def toggle_minimize(self):
        if self._minimized:
            self._body.pack(fill="both", expand=True)
            self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
            self._btn_min.configure(text="—")
            self._minimized = False
        else:
            self._body.pack_forget()
            self.geometry(f"{self.WIDTH}x24")
            self._btn_min.configure(text="□")
            self._minimized = True

    def hide(self):
        if self._auto_hide_after_id:
            try:
                self.after_cancel(self._auto_hide_after_id)
            except Exception:
                pass
            self._auto_hide_after_id = None
        try:
            self.withdraw()
        except Exception:
            pass

    def show(self):
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
        except Exception:
            pass

"""Hộp thoại xác nhận trước khi chạy."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..styles import apply_wizard_styles
from ..theme import CLR_PANEL_BG


class ConfirmRunDialog(tk.Toplevel):
    """Hộp xác nhận trước khi chạy plan.

    Args:
        parent
        summary_lines: list[str] — các dòng tóm tắt
        warnings: list[(level, text)] — level: "info" / "warn" / "error"

    Result truy cập qua self.choice:
        "real"     — Áp dụng thật
        "dry_run"  — Chạy thử
        None       — Hủy / đóng
    """

    def __init__(
        self,
        parent,
        summary_lines: list[str],
        warnings: list[tuple[str, str]] | None = None,
    ):
        super().__init__(parent)
        self.summary_lines = list(summary_lines)
        self.warnings = list(warnings or [])
        self.choice: str | None = None

        self.title("Xác nhận trước khi chạy")
        self.configure(background=CLR_PANEL_BG)
        self.resizable(False, False)
        apply_wizard_styles(ttk.Style(self))

        self._build_ui()
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

        # Center
        self.update_idletasks()
        if parent and hasattr(parent, "winfo_toplevel"):
            top = parent.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() // 2) - (self.winfo_width() // 2)
            y = top.winfo_rooty() + (top.winfo_height() // 2) - (self.winfo_height() // 2)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _build_ui(self):
        body = ttk.Frame(self, padding=18, style="Wiz.TFrame")
        body.pack(fill="both", expand=True)

        ttk.Label(
            body, text="Bạn sắp nhập KHDH lên VnEdu",
            style="WizTitle.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        # Summary
        for line in self.summary_lines:
            ttk.Label(
                body, text=f"   • {line}",
                style="Wiz.TLabel",
            ).pack(anchor="w", pady=1)

        # Warnings
        if self.warnings:
            ttk.Separator(body, orient="horizontal").pack(
                fill="x", pady=(12, 8)
            )
            for level, text in self.warnings:
                style = (
                    "WizErr.TLabel" if level == "error"
                    else "WizWarn.TLabel" if level == "warn"
                    else "WizAccent.TLabel"
                )
                icon = "❌" if level == "error" else "⚠" if level == "warn" else "ℹ"
                ttk.Label(
                    body, text=f"{icon}  {text}",
                    style=style, wraplength=520, justify="left",
                ).pack(anchor="w", pady=2)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(12, 8))

        # Buttons
        btn_row = ttk.Frame(body, style="Wiz.TFrame")
        btn_row.pack(fill="x")

        ttk.Button(
            btn_row, text="Hủy", command=self.destroy,
            style="WizSubtle.TButton",
        ).pack(side="right", padx=(6, 0))

        ttk.Button(
            btn_row, text="🚀 Áp dụng thật",
            command=self._on_real,
            style="WizSuccess.TButton",
        ).pack(side="right", padx=(6, 0))

        ttk.Button(
            btn_row, text="👁 Chạy thử (không lưu)",
            command=self._on_dry,
            style="WizPrimary.TButton",
        ).pack(side="right")

    def _on_dry(self):
        self.choice = "dry_run"
        self.destroy()

    def _on_real(self):
        # Có error block thì không cho run
        for level, _ in self.warnings:
            if level == "error":
                messagebox.showwarning(
                    "Có lỗi", "Hãy sửa các lỗi trước khi áp dụng thật.",
                    parent=self,
                )
                return
        # Re-confirm khi có warning
        if any(l == "warn" for l, _ in self.warnings):
            if not messagebox.askyesno(
                "Vẫn tiếp tục?",
                "Có cảnh báo. Bạn vẫn muốn áp dụng thật vào VnEdu?",
                parent=self,
            ):
                return
        self.choice = "real"
        self.destroy()

"""Thanh nút và khung log."""

import tkinter as tk
from datetime import datetime
from tkinter import ttk

from vnedu_common.logging_setup import log_ui_message

from ..config import UI_LOG_BG, UI_LOG_TEXT


class ToolbarLogMixin:
    """Thanh nút và khung log."""

    # ----- Buttons -----

    def _build_buttons(self, parent):
        """Tạo toolbar nhỏ cho các thao tác UI chung."""
        self.btn_compact = ttk.Button(
            parent, text="Thu gọn", command=self._toggle_compact,
            style="Subtle.TButton",
        )
        self.btn_compact.pack(side="right", padx=1)

    def _build_class_stats_toolbar(self, parent):
        """Nút mở thống kê PPCT/lớp nằm dưới log."""
        self.btn_open_class_stats = ttk.Button(
            parent,
            text="Quét lớp và thống kê PPCT",
            style="Highlight.TButton",
            command=self._on_open_class_stats_dialog,
        )
        self.btn_open_class_stats.pack(anchor="w")

    # ----- Log -----

    def _build_log(self, parent):
        """Tạo Text widget cho log."""
        self.txt_log = tk.Text(
            parent, height=6, font=("Consolas", 10),
            state="disabled", wrap="word",
            bg=UI_LOG_BG, fg=UI_LOG_TEXT,
            insertbackground="#ffffff"
        )
        scrollbar = ttk.Scrollbar(parent, orient="vertical",
                                  command=self.txt_log.yview)
        self.txt_log.config(yscrollcommand=scrollbar.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _log(self, message, level="info"):
        """Ghi log message.

        Args:
            message: Nội dung log
            level: "info" | "success" | "warning" | "error"
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix_map = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
        }
        prefix = prefix_map.get(level, "")
        line = f"{timestamp}  {prefix} {message}\n"
        log_ui_message("auto_sdb.ui", message, level)

        self.txt_log.config(state="normal")
        self.txt_log.insert("end", line)
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

        # Giới hạn 500 dòng
        line_count = int(self.txt_log.index("end-1c").split(".")[0])
        if line_count > 500:
            self.txt_log.config(state="normal")
            self.txt_log.delete("1.0", "100.0")
            self.txt_log.config(state="disabled")

"""Chạy sao lưu."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from ..theme import DEFAULT_CDP_PORT
from ..workers.backup import BackupWorker


class BackupActionsMixin:
    """Chạy sao lưu."""

    # -----------------------------------------------------------
    # Backup actions
    # -----------------------------------------------------------

    def _on_backup_clicked(self):
        if self._backup_worker is not None and self._backup_worker.is_alive():
            return
        if self.wizard._guard_cdp_exclusive("Sao lưu KHDH"):
            return
        try:
            tf = int(self.var_backup_from.get())
            tt = int(self.var_backup_to.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Tuần không hợp lệ",
                "Hãy nhập số tuần hợp lệ.",
                parent=self,
            )
            return
        if not (1 <= tf <= tt <= 52):
            messagebox.showerror(
                "Tuần không hợp lệ",
                f"Khoảng {tf}..{tt} không hợp lệ (phải 1 ≤ from ≤ to ≤ 52).",
                parent=self,
            )
            return
        path = self.var_backup_path.get().strip()
        if not path:
            messagebox.showerror(
                "Thiếu đường dẫn",
                "Hãy chọn đường dẫn lưu tệp sao lưu.",
                parent=self,
            )
            return

        self._backup_stop = threading.Event()
        self._backup_queue = queue.Queue()
        try:
            port = int(self.wizard.var_port.get())
        except (tk.TclError, ValueError):
            port = DEFAULT_CDP_PORT
        self._backup_worker = BackupWorker(
            port=port,
            tuan_from=tf,
            tuan_to=tt,
            save_path=path,
            note=self.var_backup_note.get(),
            event_queue=self._backup_queue,
            stop_event=self._backup_stop,
        )
        self._backup_worker.start()
        self.btn_backup_run.configure(state="disabled")
        self.btn_backup_stop.configure(state="normal")
        n_weeks = tt - tf + 1
        self._backup_progress_bar.configure(maximum=n_weeks)
        self.var_backup_progress.set(0)
        self.var_backup_status.set(f"Đang sao lưu {n_weeks} tuần…")
        self._poll_after_id = self.after(150, self._poll_backup_queue)

    def _on_backup_stop(self):
        if self._backup_worker and self._backup_worker.is_alive():
            self._backup_stop.set()
            self.btn_backup_stop.configure(state="disabled")
            self.var_backup_status.set("⏸ Đang dừng…")

    def _poll_backup_queue(self):
        try:
            while True:
                ev = self._backup_queue.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.var_backup_status.set(str(ev[1]))
                elif kind == "progress":
                    _, done, total, label = ev
                    self.var_backup_progress.set(int(done))
                    self.var_backup_status.set(
                        f"Đã quét {done}/{total} ({label})"
                    )
                elif kind == "done":
                    _, bf, saved_path = ev
                    self.var_backup_progress.set(
                        self._backup_progress_bar["maximum"]
                    )
                    self.var_backup_status.set(
                        f"✓ Hoàn tất: {len(bf.weeks)} tuần, "
                        f"{bf.total_filled_slots} ô có dữ liệu"
                        + (f". Tệp: {Path(saved_path).name}" if saved_path else "")
                    )
                    if saved_path:
                        messagebox.showinfo(
                            "Sao lưu thành công",
                            f"Đã lưu tệp:\n{saved_path}\n\n"
                            f"Tổng: {len(bf.weeks)} tuần, "
                            f"{bf.total_filled_slots} ô có dữ liệu.",
                            parent=self,
                        )
                elif kind == "error":
                    self.var_backup_status.set("❌ Lỗi sao lưu")
                    messagebox.showerror(
                        "Lỗi sao lưu",
                        str(ev[1]).splitlines()[0],
                        parent=self,
                    )
        except queue.Empty:
            pass
        if self._backup_worker and self._backup_worker.is_alive():
            self._poll_after_id = self.after(150, self._poll_backup_queue)
        else:
            self._poll_after_id = None
            self.btn_backup_run.configure(state="normal")
            self.btn_backup_stop.configure(state="disabled")

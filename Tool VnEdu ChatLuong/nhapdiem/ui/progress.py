"""Log, tiến độ, đồng hồ mic và chạy tác vụ nền."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from queue import Empty
from tkinter import messagebox
from typing import Callable

from vnedu_common.logging_setup import log_ui_message

from ..config import LOG_MAX_LINES, LOG_TRIM_LINES, ProgressCallback
from ..scorebook_core import build_progress_caption, clamp_progress_value


class ProgressMixin:
    """Log, tiến độ, đồng hồ mic và chạy tác vụ nền."""

    def _dispatch_ui_callback(self, callback: Callable[[], None]) -> bool:
        """Schedules one UI callback safely from any worker thread."""
        try:
            self.root.after(0, callback)
            return True
        except (tk.TclError, RuntimeError):
            return False

    def _append_log_line(self, line: str, *, tag: str = "") -> None:
        """Writes one log line into the Tk text widget on the UI thread."""
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, line + "\n", tag or ())
            # BUG-12 FIX: Trim log khi vượt LOG_MAX_LINES để tránh memory leak và GUI chậm
            current_lines = int(self.log_text.index("end-1c").split(".")[0])
            if current_lines > LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{LOG_TRIM_LINES}.0")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        except tk.TclError:
            return

    def _log(self, message: str, *, tag: str = "") -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        print(line)
        log_ui_message("nhapdiem.ui", message, tag or "info")
        if threading.current_thread() is threading.main_thread():
            self._append_log_line(line, tag=tag)
            return
        self._dispatch_ui_callback(lambda line=line, tag=tag: self._append_log_line(line, tag=tag))

    def _set_busy(self, busy: bool, status_text: str = "") -> None:
        self._busy = busy
        for widget, normal_state in self._busy_widgets:
            try:
                widget.configure(state=("disabled" if busy else normal_state))
            except tk.TclError:
                continue
        self._sync_context_combo_states()
        try:
            self.root.configure(cursor="watch" if busy else "")
        except tk.TclError:
            pass
        if status_text:
            self._set_progress(0.0 if busy else self._progress_value, status_text)

    def _set_progress(self, value: float, message: str = "") -> None:
        self._progress_value = clamp_progress_value(value)
        if message:
            self.status_var.set(message)
            self._progress_message = message
        self._render_progress()

    def _render_progress(self) -> None:
        try:
            width = max(int(self.progress_canvas.winfo_width()), 1)
            height = max(int(self.progress_canvas.winfo_height()), 1)
        except (AttributeError, tk.TclError):
            return
        fill = int((width - 2) * (self._progress_value / 100.0))
        self.progress_canvas.coords(self._progress_fill_id, 1, 1, 1 + max(fill, 0), max(height - 1, 1))
        # IMP-D1: Đổi màu progress bar theo trạng thái (xanh = OK, vàng = đang chạy, đỏ = lỗi)
        if self._progress_value <= 0:
            bar_color = "#dfe9df"
        elif self._progress_value >= 100:
            bar_color = "#2fa34a"  # xanh = hoàn thành
        elif "lỗi" in self._progress_message.lower() or "thất bại" in self._progress_message.lower():
            bar_color = "#e74c3c"  # đỏ = lỗi
        else:
            bar_color = "#f39c12"  # vàng = đang xử lý
        self.progress_canvas.itemconfigure(self._progress_fill_id, fill=bar_color)
        self.progress_canvas.coords(self._progress_text_id, 8, height / 2)
        self.progress_canvas.itemconfigure(
            self._progress_text_id,
            text=build_progress_caption(self._progress_value, self._progress_message, max_message_length=34),
        )

    def _reset_voice_meter(self) -> None:
        self._voice_meter_level = 0.0
        self._voice_meter_peak = 0.0
        self._render_voice_meter()

    def _sync_voice_meter(self) -> None:
        capture_level = 0.0
        if self._ptt_enabled and self._ptt_capture is not None:
            try:
                capture_level = self._ptt_capture.current_level()
            except Exception:
                capture_level = 0.0
        self._voice_meter_level = max(0.0, min(1.0, float(capture_level)))
        if self._voice_meter_level >= self._voice_meter_peak:
            self._voice_meter_peak = self._voice_meter_level
        else:
            self._voice_meter_peak = max(self._voice_meter_level, self._voice_meter_peak - 0.045)
        self._render_voice_meter()

    def _render_voice_meter(self) -> None:
        try:
            canvas = self.voice_meter_canvas
            width = max(int(canvas.winfo_width()), 1)
            height = max(int(canvas.winfo_height()), 1)
        except (AttributeError, tk.TclError):
            return

        inner_left = 2
        inner_top = 2
        inner_right = max(width - 2, inner_left)
        inner_bottom = max(height - 2, inner_top)
        inner_width = max(inner_right - inner_left, 1)
        level_width = int(inner_width * self._voice_meter_level)
        peak_x = inner_left + int(inner_width * self._voice_meter_peak)

        if not self._ptt_enabled:
            fill_color = "#d9e2da"
            peak_color = "#b8c5ba"
            meter_text = "Micro realtime: bộ đàm đang tắt"
        elif self._ptt_recording:
            fill_color = "#27ae60"
            peak_color = "#176a34"
            meter_text = f"Đang nghe realtime: {int(self._voice_meter_level * 100):02d}%"
        else:
            fill_color = "#2fa34a"
            peak_color = "#176a34"
            meter_text = f"Mức thu âm realtime: {int(self._voice_meter_level * 100):02d}%"

        canvas.coords(
            self._voice_meter_fill_id,
            inner_left,
            inner_top,
            inner_left + max(level_width, 0),
            inner_bottom,
        )
        canvas.itemconfigure(self._voice_meter_fill_id, fill=fill_color)
        canvas.coords(self._voice_meter_peak_id, peak_x, 5, peak_x, max(height - 5, 5))
        canvas.itemconfigure(self._voice_meter_peak_id, fill=peak_color, state=("normal" if self._ptt_enabled else "hidden"))
        canvas.coords(self._voice_meter_text_id, 10, height / 2)
        canvas.itemconfigure(self._voice_meter_text_id, text=meter_text, fill="#20532b")

    def _progress_reporter(self, task_token: int) -> ProgressCallback:
        def report(value: float, message: str = "") -> None:
            self._progress_queue.put((task_token, clamp_progress_value(value), str(message or "").strip()))
        return report

    def _run_background(
        self,
        busy_text: str,
        worker: Callable[[ProgressCallback], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._busy:
            self._log("Đang có tác vụ khác chạy, bỏ qua lệnh mới.")
            return
        self._foreground_task_token += 1
        task_token = self._foreground_task_token
        self._set_busy(True, busy_text)
        reporter = self._progress_reporter(task_token)
        reporter(2.0, busy_text)

        def background_worker() -> None:
            result = None
            captured_error = None
            try:
                with self._automation_lock:
                    result = worker(reporter)
            except Exception as error:  # noqa: BLE001
                captured_error = error
            self._result_queue.put((result, captured_error, on_success, on_error))

        threading.Thread(target=background_worker, daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                task_token, progress_value, progress_message = self._progress_queue.get_nowait()
                if self._busy and task_token == self._foreground_task_token:
                    self._set_progress(progress_value, progress_message)
        except Empty:
            pass
        try:
            while True:
                result, error, on_success, on_error = self._result_queue.get_nowait()
                self._set_busy(False)
                try:
                    if error is not None:
                        if on_error is not None:
                            on_error(error)
                        self._set_progress(0.0, self.status_var.get() or "Tác vụ thất bại")
                    else:
                        on_success(result)
                        self._set_progress(100.0, self.status_var.get() or "Đã hoàn tất")
                except Exception as callback_error:  # noqa: BLE001
                    messagebox.showerror("Lỗi nội bộ", str(callback_error))
                    self._log(f"Lỗi callback UI: {callback_error}")
                    self._set_progress(0.0, "Lỗi callback UI")
        except Empty:
            pass
        self._sync_voice_meter()
        try:
            self._poll_after_id = self.root.after(50, self._poll_results)
        except tk.TclError:
            self._poll_after_id = None

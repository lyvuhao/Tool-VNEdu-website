"""Log, thanh tiến độ và chạy tác vụ nền."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from queue import Empty
from tkinter import messagebox
from typing import Callable, Tuple

from vnedu_common.logging_setup import log_ui_message

from ..config import ProgressCallback
from ..progress import build_progress_caption, clamp_progress_value, password_entry_show_value


class ProgressMixin:
    """Log, thanh tiến độ và chạy tác vụ nền."""

    def _log(self, message: str) -> None:
        """Stores one timestamped diagnostic line without rendering a GUI log panel."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self._log_history.append(line)
        if len(self._log_history) > 300:
            self._log_history = self._log_history[-300:]
        print(line)
        log_ui_message("nhanxet.ui", message)

    def _apply_password_visibility(self) -> None:
        """Toggles whether the password entry reveals the current VNEDU password."""
        show_value = password_entry_show_value(bool(self.show_password_var.get()))
        try:
            self.password_entry.configure(show=show_value)
        except (AttributeError, tk.TclError):
            return

    def _on_progress_canvas_configure(self, _event: tk.Event | None = None) -> None:
        """Re-renders the progress canvas whenever its available width changes."""
        self._render_progress_bar()

    def _render_progress_bar(self) -> None:
        """Draws the custom green progress bar together with the latest caption."""
        canvas = getattr(self, "progress_canvas", None)
        if canvas is None:
            return
        try:
            canvas_width = max(int(canvas.winfo_width()), 1)
            canvas_height = max(int(canvas.winfo_height()), 1)
        except tk.TclError:
            return

        fill_width = int((canvas_width - 2) * (clamp_progress_value(self._progress_value) / 100.0))
        canvas.coords(
            self._progress_fill_id,
            1,
            1,
            1 + max(fill_width, 0),
            max(canvas_height - 1, 1),
        )
        canvas.itemconfigure(
            self._progress_fill_id,
            fill=("#2fa34a" if self._progress_value > 0 else "#dfe9df"),
        )
        canvas.coords(self._progress_text_id, 8, canvas_height / 2)
        canvas.itemconfigure(
            self._progress_text_id,
            text=build_progress_caption(self._progress_value, self._progress_message),
        )

    def _set_status_text(self, status_text: str, sync_progress_caption: bool = True) -> None:
        """Updates the status line and optionally keeps the progress caption in sync with it."""
        normalized_text = str(status_text or "").strip()
        if not normalized_text:
            return
        self.status_var.set(normalized_text)
        if sync_progress_caption:
            self._progress_message = normalized_text
            self._render_progress_bar()

    def _set_progress(self, value: float, status_text: str = "") -> None:
        """Updates the visible progress bar and optionally mirrors the text into the status line."""
        self._progress_value = clamp_progress_value(value)
        if status_text:
            self._set_status_text(status_text, sync_progress_caption=True)
        self._render_progress_bar()

    def _make_progress_reporter(self, task_token: int) -> ProgressCallback:
        """Creates one thread-safe reporter that forwards worker progress back to the Tk thread."""

        def report(value: float, message: str = "") -> None:
            self._progress_queue.put((task_token, clamp_progress_value(value), str(message or "").strip()))

        return report

    def _set_busy(self, is_busy: bool, status_text: str = "") -> None:
        """Freezes interactive controls while a background automation task is running."""
        self._busy = bool(is_busy)
        for widget, normal_state in self._busy_widgets:
            try:
                widget.configure(state=("disabled" if is_busy else normal_state))
            except tk.TclError:
                continue

        try:
            self.root.configure(cursor="watch" if is_busy else "")
        except tk.TclError:
            pass

        if status_text:
            self._set_progress(self._progress_value if not is_busy else 0.0, status_text)

    def _cancel_pending_context_apply(self) -> None:
        """Cancels one queued auto-apply request for the scorebook context selectors."""
        after_id = getattr(self, "_context_apply_after_id", None)
        if not after_id:
            return
        self._context_apply_after_id = None
        try:
            self.root.after_cancel(after_id)
        except (tk.TclError, AttributeError):
            return

    def _selected_context_matches_current_context(self) -> bool:
        """Checks whether the GUI comboboxes still point to the current live scorebook context."""
        if self.current_context is None:
            return True
        selected_grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id)
        selected_class_id = self._selected_option_id(self.class_var, self.class_label_to_id)
        selected_subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id)
        selected_term_id = self._selected_option_id(self.term_var, self.term_label_to_id)
        return (
            selected_grade_id == self.current_context.selected_grade_id
            and selected_class_id == self.current_context.selected_class_id
            and selected_subject_id == self.current_context.selected_subject_id
            and selected_term_id == self.current_context.selected_term_id
        )

    def _schedule_context_apply(self) -> None:
        """Debounces UI selection changes so one burst of combobox edits triggers one live apply."""
        self._cancel_pending_context_apply()
        try:
            self._context_apply_after_id = self.root.after(
                self._context_apply_delay_ms,
                self._run_debounced_context_apply,
            )
        except (tk.TclError, AttributeError):
            self._context_apply_after_id = None
            self.on_apply_selected_context()

    def _run_debounced_context_apply(self) -> None:
        """Runs one delayed context apply if the UI selection is still different from live context."""
        self._context_apply_after_id = None
        if self._suspend_context_events or self._busy or self.current_context is None:
            return
        if self._selected_context_matches_current_context():
            return
        self.on_apply_selected_context()

    def _run_background_task(
        self,
        busy_text: str,
        worker: Callable[[ProgressCallback], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Runs one long browser automation task off the Tk main thread."""
        if self._busy:
            self._log("Đang có tác vụ khác chạy, bỏ qua lệnh mới.")
            return

        self._cancel_pending_context_apply()
        self._foreground_task_token += 1
        self._set_busy(True, busy_text)
        task_token = self._foreground_task_token
        progress_reporter = self._make_progress_reporter(task_token)
        progress_reporter(2.0, busy_text)

        def background_worker() -> None:
            result: object | None = None
            captured_error: Exception | None = None
            try:
                with self._automation_lock:
                    result = worker(progress_reporter)
            except Exception as error:  # noqa: BLE001 - UI thread will handle the result
                captured_error = error
            self._result_queue.put((result, captured_error, on_success, on_error))

        threading.Thread(target=background_worker, daemon=True).start()

    def _run_passive_background_task(
        self,
        worker: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Runs a non-blocking background task that does not freeze the main UI."""

        def background_worker() -> None:
            result: object | None = None
            captured_error: Exception | None = None
            try:
                result = worker()
            except Exception as error:  # noqa: BLE001 - main thread callback will handle the result
                captured_error = error
            self._passive_result_queue.put((result, captured_error, on_success, on_error))

        threading.Thread(target=background_worker, daemon=True).start()

    def _access_scan_block_reason(
        self,
        scan_key: Tuple[str, str, str, str],
        expected_task_token: int,
    ) -> str:
        """Explains why one passive access scan should no longer touch the live UI/browser state."""
        if expected_task_token != self._foreground_task_token:
            return "đã có tác vụ chính mới"
        if self._busy:
            return "đang có tác vụ chính chạy"
        if self.current_context is None:
            return "không còn ngữ cảnh hiện tại"
        current_scan_key = self._access_cache_key(
            self.current_context.selected_grade_id,
            self.current_context.selected_term_id,
        )
        if current_scan_key != scan_key:
            return "ngữ cảnh hiện tại đã đổi"
        return ""

    def _poll_background_results(self) -> None:
        """Processes background worker results back on the Tk main thread."""
        try:
            while True:
                task_token, progress_value, progress_message = self._progress_queue.get_nowait()
                if task_token == self._foreground_task_token and self._busy:
                    self._set_progress(progress_value, progress_message)
        except Empty:
            pass

        try:
            while True:
                result, error, on_success, on_error = self._passive_result_queue.get_nowait()
                try:
                    if error is not None:
                        if on_error is not None:
                            on_error(error)
                    else:
                        on_success(result)
                except Exception as callback_error:  # noqa: BLE001 - passive callback guard
                    self._log(f"Lỗi callback nền: {callback_error}")
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
                except Exception as callback_error:  # noqa: BLE001 - final guard for Tk callbacks
                    messagebox.showerror("Lỗi nội bộ", str(callback_error))
                    self._log(f"Lỗi callback UI: {callback_error}")
        except Empty:
            pass

        try:
            self._poll_after_id = self.root.after(50, self._poll_background_results)
        except tk.TclError:
            self._poll_after_id = None
            return

"""Trạng thái nút và hàng đợi tác vụ UI."""

import threading

from ..config import SCHEDULE_MODE_KHDH, SCHEDULE_MODE_MANUAL, UI_TASK_POLL_MS


class ScheduleStateMixin:
    """Trạng thái nút và hàng đợi tác vụ UI."""

    def _update_sched_ppct_runtime(self, last_success=None, next_ppct=None, status="idle"):
        """Đồng bộ trạng thái PPCT runtime lên GUI.

        `var_sched_ppct_start` hiển thị tiết PPCT vừa nhập thành công gần nhất,
        còn `next_ppct` được giữ riêng cho resume để tránh trùng PPCT.
        """
        self._schedule_last_success_ppct = last_success
        self._schedule_next_ppct = next_ppct

        if last_success is not None:
            try:
                self.var_sched_ppct_start.set(int(last_success))
            except Exception:
                pass

        status_map = {
            "idle": "Sẵn sàng",
            "running": "Đang chạy",
            "paused": "Tạm dừng",
            "done": "Hoàn tất",
        }
        status_text = status_map.get(status, "Sẵn sàng")
        if self._is_sched_khdh_mode():
            last_text = "--" if last_success in (None, "") else str(last_success)
            self.var_sched_ppct_runtime.set(
                f"{status_text} | Mode KHDH | PPCT gần nhất từ popup: {last_text}"
            )
            return
        last_text = "--" if last_success is None else str(last_success)
        next_text = "--" if next_ppct is None else str(next_ppct)
        self.var_sched_ppct_runtime.set(
            f"{status_text} | PPCT vừa nhập: {last_text} | Kế tiếp nội bộ: {next_text}"
        )

    def _set_schedule_button_states(self, running=False, can_resume=False):
        """Đồng bộ trạng thái Run/Stop/Resume theo state schedule hiện tại."""
        current_mode = self._get_sched_mode()
        if current_mode == SCHEDULE_MODE_KHDH:
            subject_error = self._get_sched_mode_invalid_reason(current_mode)
            form_ready = not subject_error
            selection_ready = form_ready
        else:
            form_ready = self._has_valid_sched_form_scan()
            subject_selection = None
            subject_error = ""
            if form_ready:
                subject_selection, subject_error = self._resolve_current_sched_subject_selection()
            selection_ready = subject_selection is not None
        resume_ready = form_ready
        resume_selection_ready = selection_ready
        if can_resume and self._schedule_resume_params:
            resume_mode = str(
                self._schedule_resume_params.get("schedule_mode", SCHEDULE_MODE_MANUAL)
                or SCHEDULE_MODE_MANUAL
            ).strip().lower()
            if resume_mode == SCHEDULE_MODE_KHDH:
                resume_error = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
                resume_ready = not resume_error
                resume_selection_ready = resume_ready
            else:
                resume_ready = self._has_valid_sched_form_scan()
                resume_subject_selection = None
                if resume_ready:
                    resume_subject_selection, _resume_error = self._resolve_current_sched_subject_selection()
                    resume_selection_ready = resume_subject_selection is not None
                else:
                    resume_selection_ready = False
        self.btn_sched_run.config(
            state="disabled" if running or not self._cdp_connected or not form_ready or not selection_ready else "normal"
        )
        self.btn_sched_stop.config(state="normal" if running else "disabled")
        self.btn_sched_resume.config(
            state="normal"
            if (
                can_resume and not running and self._cdp_connected
                and resume_ready and resume_selection_ready
            )
            else "disabled"
        )
        self._refresh_sched_form_guidance(subject_error=subject_error)

    def _set_auto_login_button_state(self):
        """Đồng bộ trạng thái nút auto-login + nhập dữ liệu."""
        if not hasattr(self, "btn_auto_login_run"):
            return
        enabled = (
            (not self._auto_login_running)
            and (not self._schedule_running)
            and not (self._class_stats_thread and self._class_stats_thread.is_alive())
        )
        self.btn_auto_login_run.config(state="normal" if enabled else "disabled")

    def _root_exists(self):
        """Kiểm tra root window còn sống để tránh callback ghi vào Tcl đã đóng."""
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    def _post_ui(self, callback):
        """Đưa callback về UI thread an toàn qua queue nội bộ."""
        if callback is None or self._closing:
            return
        if threading.current_thread() is threading.main_thread():
            if self._root_exists():
                callback()
            return
        self._ui_task_queue.put(callback)

    def _pump_ui_tasks(self):
        """Thực thi các callback UI được worker gửi về; chỉ chạy trên main thread."""
        self._ui_task_pump_after_id = None
        if self._closing or not self._root_exists():
            return
        try:
            while not self._ui_task_queue.empty():
                callback = self._ui_task_queue.get_nowait()
                if self._closing or not self._root_exists():
                    continue
                try:
                    callback()
                except Exception as e:
                    print(f"[UI_TASK] Error: {e}")
        finally:
            if not self._closing and self._root_exists():
                self._ui_task_pump_after_id = self.root.after(
                    UI_TASK_POLL_MS,
                    self._pump_ui_tasks,
                )

"""Lưu/khôi phục tiến trình chạy lịch (resume)."""

import copy
import json
import os
import time

from ..config import SCHEDULE_MODE_KHDH, SCHEDULE_MODE_MANUAL, SCHEDULE_RESUME_FILE
from ..paths import TOOL_DIR


class ScheduleResumeMixin:
    """Lưu/khôi phục tiến trình chạy lịch (resume)."""

    def _write_json_atomic(self, file_path, payload):
        """Ghi JSON theo kiểu temp-file + os.replace để tránh file nửa chừng."""
        temp_path = f"{file_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(temp_path, file_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _schedule_resume_file_path(self):
        """Đường dẫn sidecar lưu checkpoint schedule để resume sau khi mở lại app."""
        return os.path.join(str(TOOL_DIR), SCHEDULE_RESUME_FILE)

    @staticmethod
    def _build_initial_schedule_resume_state(params):
        """Sinh checkpoint ban đầu cho một phiên schedule mới."""
        schedule_mode = str(params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL)
        next_ppct = None
        if schedule_mode != SCHEDULE_MODE_KHDH:
            next_ppct = int(params.get("ppct_start", 1) or 1)
        return {
            "next_lop_idx": 0,
            "next_tuan_num": int(params.get("tuan_from", 1) or 1),
            "next_slot_idx": 0,
            "next_row_key": None,
            "next_ppct": next_ppct,
            "completed": 0,
            "skipped": 0,
            "errors": 0,
            "last_success_ppct": None,
        }

    def _persist_schedule_resume_snapshot(self):
        """Persist resume params/state xuống đĩa để survive app close/crash."""
        file_path = self._schedule_resume_file_path()
        if not self._schedule_resume_params or not self._schedule_resume_state:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            self._schedule_resume_dirty = False
            return

        payload = {
            "version": 1,
            "saved_at": time.time(),
            "params": copy.deepcopy(self._schedule_resume_params),
            "resume_state": copy.deepcopy(self._schedule_resume_state),
        }
        self._write_json_atomic(file_path, payload)
        self._schedule_resume_dirty = False

    def _update_schedule_resume_snapshot(self, resume_state=None, params=None, persist=True):
        """Cập nhật checkpoint đang giữ trong RAM và ghi xuống đĩa nếu cần."""
        if params is not None:
            self._schedule_resume_params = copy.deepcopy(params) if params else None
        self._schedule_resume_state = copy.deepcopy(resume_state) if resume_state else None
        self._schedule_resume_dirty = True
        if persist:
            self._persist_schedule_resume_snapshot()

    def _load_schedule_resume_snapshot(self):
        """Nạp checkpoint schedule còn dang dở từ sidecar JSON."""
        file_path = self._schedule_resume_file_path()
        if not os.path.exists(file_path):
            self._update_schedule_resume_snapshot(None, params=None, persist=False)
            self._schedule_resume_dirty = False
            return
        try:
            with open(file_path, "r", encoding="utf-8") as resume_file:
                raw = json.load(resume_file)
            params = raw.get("params")
            resume_state = raw.get("resume_state")
            if not isinstance(params, dict) or not isinstance(resume_state, dict):
                raise ValueError("Resume snapshot không hợp lệ")
            self._update_schedule_resume_snapshot(
                resume_state=resume_state,
                params=params,
                persist=False,
            )
            self._schedule_resume_dirty = False
        except Exception as e:
            self._log(f"Lỗi load checkpoint schedule: {e}", "warning")
            self._update_schedule_resume_snapshot(None, params=None, persist=False)
            self._schedule_resume_dirty = False

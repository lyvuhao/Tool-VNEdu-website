"""Nạp Sổ điểm và áp dụng lựa chọn khối/lớp/môn."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Dict, List

from ..access import resolve_accessible_selection
from ..models import ScorebookContext


class ContextMixin:
    """Nạp Sổ điểm và áp dụng lựa chọn khối/lớp/môn."""

    def _handle_load_scorebook_shell_success(self, result: object) -> None:
        """Applies one freshly loaded live scorebook context onto the UI."""
        context, login_message = result if isinstance(result, tuple) else (None, "")
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ.")
        if login_message:
            self._log(login_message)
        self._apply_scorebook_context(context)
        if context.permission_text:
            self._log(context.permission_text)
        if context.teacher_text:
            self._log(context.teacher_text)
        self._log_detected_columns(context.column_schemas, context.detected_columns)
        self._log("Đã đọc nhanh khung Khối/Lớp/Môn/Học kỳ từ live Chrome.")

    def _handle_load_scorebook_shell_error(self, error: Exception) -> None:
        """Handles one scorebook-shell load failure on the UI thread."""
        messagebox.showerror("Lỗi đọc Sổ điểm", str(error))
        self._log(f"Lỗi đọc Sổ điểm: {error}")
        self._set_status_text("Chưa đọc được Sổ điểm")

    def on_load_scorebook_shell(self) -> None:
        """Logs in if needed, then reads the scorebook shell from the live browser."""
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi đọc Sổ điểm", str(error))
            self._log(f"Lỗi đọc Sổ điểm: {error}")
            return

        username, password = self._background_credentials()

        self._run_background_task(
            "Đang đăng nhập và đọc nhanh Sổ điểm...",
            worker=lambda progress: automation.load_scorebook_context(
                username=username,
                password=password,
                progress_callback=progress,
            ),
            on_success=self._handle_load_scorebook_shell_success,
            on_error=self._handle_load_scorebook_shell_error,
        )

    def on_context_selection_changed(self, _event: tk.Event | None = None) -> None:
        """Auto-applies context changes when the user picks a different combobox value."""
        if self._suspend_context_events or self._busy or self.current_context is None:
            return

        if self._selected_context_matches_current_context():
            self._cancel_pending_context_apply()
            return

        self._schedule_context_apply()

    def on_score_source_selection_changed(self, _event: tk.Event | None = None) -> None:
        """Keeps the chosen score source column for the next direct write run."""
        if self._suspend_context_events or self.current_context is None:
            return
        selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)
        self.preferred_score_source_key = selected_key
        self._schedule_config_autosave()
        if not selected_key:
            return
        selected_schema = self._schema_by_key(self.current_context.column_schemas, selected_key)
        if selected_schema is not None:
            self._log(f"Đã chọn cột điểm dùng để xét: {selected_schema.display_name}")

    def _collect_selected_context_apply_request(self) -> Dict[str, object]:
        """Collects the current UI selection and cache state for one live context apply run."""
        if self.current_context is None:
            raise RuntimeError("Hãy đọc Sổ điểm trước khi áp ngữ cảnh.")

        automation = self._build_automation()
        requested_grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id)
        requested_class_id = self._selected_option_id(self.class_var, self.class_label_to_id)
        requested_subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id)
        requested_term_id = self._selected_option_id(self.term_var, self.term_label_to_id)
        username, password = self._background_credentials()
        requested_scan_key = self._access_cache_key(
            requested_grade_id or self.current_context.selected_grade_id,
            requested_term_id or self.current_context.selected_term_id,
        )
        requested_scan_grade_id = requested_grade_id or self.current_context.selected_grade_id
        requested_scan_term_id = requested_term_id or self.current_context.selected_term_id
        matrix_access_scan_enabled = getattr(self, "_matrix_access_scan_enabled", True)
        has_cached_matrix = matrix_access_scan_enabled and requested_scan_key in self.access_entry_cache
        cached_entries = (
            list(self.access_entry_cache.get(requested_scan_key, []))
            if matrix_access_scan_enabled and has_cached_matrix
            else []
        )

        fallback_notes: List[str] = []
        effective_class_id = requested_class_id
        effective_subject_id = requested_subject_id
        if cached_entries:
            effective_class_id, effective_subject_id, fallback_notes = resolve_accessible_selection(
                cached_entries,
                preferred_class_id=requested_class_id,
                preferred_subject_id=requested_subject_id,
            )

        return {
            "automation": automation,
            "requested_grade_id": requested_grade_id,
            "requested_class_id": requested_class_id,
            "requested_subject_id": requested_subject_id,
            "requested_term_id": requested_term_id,
            "effective_class_id": effective_class_id,
            "effective_subject_id": effective_subject_id,
            "requested_scan_key": requested_scan_key,
            "requested_scan_grade_id": requested_scan_grade_id,
            "requested_scan_term_id": requested_scan_term_id,
            "has_cached_matrix": has_cached_matrix,
            "cached_entries": cached_entries,
            "fallback_notes": fallback_notes,
            "previous_context": self.current_context,
            "username": username,
            "password": password,
        }

    def _run_selected_context_apply(self, request: Dict[str, object]) -> object:
        """Executes one live scorebook context apply request from the UI layer."""
        automation = request["automation"]
        return automation.select_scorebook_context(
            grade_id=request["requested_grade_id"],
            class_id=request["effective_class_id"],
            subject_id=request["effective_subject_id"],
            term_id=request["requested_term_id"],
            username=request["username"],
            password=request["password"],
            progress_callback=request.get("progress_callback"),
        )

    def _handle_selected_context_apply_success(
        self,
        result: object,
        request: Dict[str, object],
    ) -> None:
        """Handles one successful live context apply back on the UI thread."""
        context, login_message, selection_message = result if isinstance(result, tuple) else (None, "", "")
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi áp ngữ cảnh.")
        if getattr(self, "_matrix_access_scan_enabled", True) and request["has_cached_matrix"] and not (
            context.accessible_entries
            or context.accessible_grade_id
            or context.accessible_term_id
        ):
            context.accessible_entries = list(request["cached_entries"])
            context.accessible_grade_id = request["requested_scan_grade_id"]
            context.accessible_term_id = request["requested_scan_term_id"]
        if login_message:
            self._log(login_message)
        if selection_message:
            self._log(selection_message)
        for note in request["fallback_notes"]:
            self._log(note)
        self._apply_scorebook_context(context)
        if context.permission_text:
            self._log(context.permission_text)
        if context.teacher_text:
            self._log(context.teacher_text)
        self._log_detected_columns(context.column_schemas, context.detected_columns)
        self._log("Đã áp ngữ cảnh Khối/Lớp/Môn/Học kỳ lên live Chrome.")

    def _handle_selected_context_apply_error(
        self,
        error: Exception,
        previous_context: ScorebookContext | None,
    ) -> None:
        """Handles one apply-context failure and restores the previous UI context when possible."""
        if previous_context is not None:
            self._apply_scorebook_context(previous_context)
        messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
        self._log(f"Lỗi áp ngữ cảnh: {error}")
        self._set_status_text("Áp ngữ cảnh thất bại")

    def on_apply_selected_context(self) -> None:
        """Applies the current GUI combo selections back to the live VNEDU scorebook."""
        self._cancel_pending_context_apply()
        if self.current_context is None:
            messagebox.showwarning("Chưa có dữ liệu", "Hãy đọc Sổ điểm trước khi áp ngữ cảnh.")
            self._log("Bỏ qua áp ngữ cảnh vì chưa có ScorebookContext.")
            return

        try:
            request = self._collect_selected_context_apply_request()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
            self._log(f"Lỗi áp ngữ cảnh: {error}")
            return

        self._run_background_task(
            "Đang áp ngữ cảnh Sổ điểm lên live Chrome...",
            worker=lambda progress: self._run_selected_context_apply(
                {
                    **request,
                    "progress_callback": progress,
                }
            ),
            on_success=lambda result: self._handle_selected_context_apply_success(result, request),
            on_error=lambda error: self._handle_selected_context_apply_error(
                error,
                request["previous_context"],
            ),
        )

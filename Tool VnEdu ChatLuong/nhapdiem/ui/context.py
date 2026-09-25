"""Combobox khối/lớp/môn/cột điểm và đồng bộ context."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict

from ..automation.client import VnEduScoreEntryAutomation
from ..scorebook_core import (
    password_entry_show_value,
    ScorebookContext,
    ScoreColumnSchema,
    VnEduScoreAutomation,
)


class ContextMixin:
    """Combobox khối/lớp/môn/cột điểm và đồng bộ context."""

    def _build_automation(self) -> VnEduScoreAutomation:
        self._ensure_access_cache_loaded()
        try:
            debug_port = int(self.port_var.get().strip())
        except ValueError as error:
            raise ValueError("CDP Port phải là số nguyên hợp lệ.") from error
        target_url = self.url_var.get().strip()
        if not target_url:
            raise ValueError("URL VNEDU không được để trống.")
        # SECURITY: Validate URL scheme to prevent non-HTTP connections
        if not target_url.lower().startswith(("http://", "https://")):
            raise ValueError("URL VNEDU phải bắt đầu bằng http:// hoặc https://.")
        return VnEduScoreEntryAutomation(debug_port=debug_port, target_url=target_url)

    def _render_options(self, combo: ttk.Combobox, variable: tk.StringVar, label_map: Dict[str, str], options: list[object], selected_id: str) -> None:
        label_map.clear()
        labels: list[str] = []
        for option in options:
            option_id = str(getattr(option, "option_id", "")).strip()
            option_text = str(getattr(option, "option_text", "")).strip() or option_id
            if not option_id:
                continue
            label = option_text if option_text not in label_map else f"{option_text} ({option_id})"
            label_map[label] = option_id
            labels.append(label)
        combo["values"] = labels
        # IMP-B4: Enable combo khi có data, disable khi rỗng
        combo["state"] = "readonly" if labels else "disabled"
        selected_label = next((label for label, option_id in label_map.items() if option_id == selected_id), "")
        variable.set(selected_label or (labels[0] if labels else "(chưa đọc)"))

    def _selected_id(self, variable: tk.StringVar, label_map: Dict[str, str]) -> str:
        return label_map.get(variable.get().strip(), "").strip()

    def _selected_context_ids(self) -> tuple[str, str, str, str]:
        return (
            self._selected_id(self.grade_var, self.grade_label_to_id),
            self._selected_id(self.class_var, self.class_label_to_id),
            self._selected_id(self.subject_var, self.subject_label_to_id),
            self._selected_id(self.term_var, self.term_label_to_id),
        )

    def _context_request_ids(self) -> tuple[str, str, str, str]:
        selected_grade_id, selected_class_id, selected_subject_id, selected_term_id = self._selected_context_ids()
        if self.current_context is None:
            return (selected_grade_id, selected_class_id, selected_subject_id, selected_term_id)
        return (
            selected_grade_id or self.current_context.selected_grade_id,
            "" if "class" in self._invalidated_context_fields else (selected_class_id or self.current_context.selected_class_id),
            "" if "subject" in self._invalidated_context_fields else (selected_subject_id or self.current_context.selected_subject_id),
            selected_term_id or self.current_context.selected_term_id,
        )

    def _invalidate_context_combo(
        self,
        combo: ttk.Combobox,
        variable: tk.StringVar,
        label_map: Dict[str, str],
        placeholder: str,
    ) -> None:
        label_map.clear()
        combo["values"] = []
        variable.set(placeholder)

    def _sync_context_combo_states(self) -> None:
        combo_specs = (
            (self.grade_combo, False),
            (self.class_combo, "class" in self._invalidated_context_fields),
            (self.subject_combo, "subject" in self._invalidated_context_fields),
            (self.term_combo, False),
        )
        for combo, is_invalidated in combo_specs:
            try:
                combo.configure(state=("disabled" if self._busy or is_invalidated else "readonly"))
            except tk.TclError:
                continue

    def _invalidate_dependent_context_state(self, changed_field: str, selected_ids: tuple[str, str, str, str]) -> None:
        if self.current_context is None:
            return
        selected_grade_id, selected_class_id, _selected_subject_id, _selected_term_id = selected_ids
        current_grade_id, current_class_id, _current_subject_id, _current_term_id = self._last_applied_context_ids
        if changed_field == "grade" and selected_grade_id and selected_grade_id != current_grade_id:
            self._invalidated_context_fields.update({"class", "subject"})
            self._invalidate_context_combo(
                self.class_combo,
                self.class_var,
                self.class_label_to_id,
                "(đang cập nhật lớp...)",
            )
            self._invalidate_context_combo(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                "(đang cập nhật môn...)",
            )
            self._sync_context_combo_states()
        elif changed_field == "class" and selected_class_id and selected_class_id != current_class_id:
            self._invalidated_context_fields.add("subject")
            self._invalidate_context_combo(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                "(đang cập nhật môn...)",
            )
            self._sync_context_combo_states()

    def _cancel_context_autosync(self) -> None:
        if self._context_autosync_after_id is None:
            return
        try:
            self.root.after_cancel(self._context_autosync_after_id)
        except tk.TclError:
            pass
        self._context_autosync_after_id = None

    def _cancel_pending_auto_scan(self) -> None:
        if self._auto_scan_after_id is None:
            return
        try:
            self.root.after_cancel(self._auto_scan_after_id)
        except tk.TclError:
            pass
        self._auto_scan_after_id = None

    def _apply_password_visibility(self) -> None:
        try:
            self.password_entry.configure(show=password_entry_show_value(bool(self.show_password_var.get())))
        except tk.TclError:
            pass

    def _score_column_candidates(self, context: ScorebookContext) -> list[ScoreColumnSchema]:
        return [schema for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index) if schema.editable and schema.role_hint in {"score", "average"}]

    def _render_score_columns(self, context: ScorebookContext) -> None:
        self._suspend_target_score_autoscan = True
        try:
            self.target_score_label_to_key.clear()
            labels: list[str] = []
            for schema in self._score_column_candidates(context):
                label = schema.display_name.strip() or schema.column_key
                if label in self.target_score_label_to_key:
                    label = f"{label} ({schema.column_key})"
                self.target_score_label_to_key[label] = schema.column_key
                labels.append(label)
            self.target_score_combo["values"] = labels
            selected_key = self._selected_id(self.target_score_column_var, self.target_score_label_to_key)
            if selected_key not in self.target_score_label_to_key.values():
                preferred_key = context.detected_columns.preferred_score_column_key.strip()
                if preferred_key in self.target_score_label_to_key.values():
                    selected_key = preferred_key
                else:
                    selected_key = next(iter(self.target_score_label_to_key.values()), "")
            selected_label = next((label for label, key in self.target_score_label_to_key.items() if key == selected_key), "")
            self.target_score_column_var.set(selected_label or "(chưa dò)")
        finally:
            self._suspend_target_score_autoscan = False

    def _selected_target_score_key(self) -> str:
        return self.target_score_label_to_key.get(self.target_score_column_var.get().strip(), "").strip()

    def _on_context_combo_selected(self, _event: tk.Event | None = None) -> str | None:
        if self._suspend_context_autosync or self._busy:
            return "break"
        if self.current_context is None:
            return "break"
        selected_ids = self._selected_context_ids()
        changed_widget = getattr(_event, "widget", None)
        if changed_widget is self.grade_combo:
            self._invalidate_dependent_context_state("grade", selected_ids)
        elif changed_widget is self.class_combo:
            self._invalidate_dependent_context_state("class", selected_ids)
        request_ids = self._context_request_ids()
        if not request_ids[0] or not request_ids[3]:
            return "break"
        if request_ids == self._last_applied_context_ids:
            return "break"
        self._cancel_context_autosync()
        self._context_autosync_after_id = self.root.after(
            self._context_autosync_delay_ms,
            lambda expected_ids=request_ids: self._run_context_autosync(expected_ids),
        )
        return "break"

    def _run_context_autosync(self, expected_ids: tuple[str, str, str, str]) -> None:
        self._context_autosync_after_id = None
        if self._busy or self.current_context is None:
            return
        request_ids = self._context_request_ids()
        if request_ids != expected_ids:
            return
        if request_ids == self._last_applied_context_ids:
            return
        self.on_apply_selected_context()

    def _on_target_score_selected(self, _event: tk.Event | None = None) -> str | None:
        if self._suspend_target_score_autoscan or self._busy:
            return "break"
        if self.current_context is None:
            return "break"
        target_key = self._selected_target_score_key()
        if not target_key:
            return "break"
        if target_key == self._last_scanned_target_key and self._score_rows_by_key:
            self._focus_preview_tree()
            return "break"
        self.root.after_idle(self.on_scan_score_rows)
        return "break"

    def _clear_score_rows(self, reason: str = "", log_reason: bool = False) -> None:
        self._cancel_context_autosync()
        self._cancel_pending_auto_scan()
        self._ptt_roster_revision += 1
        # BUG-01 FIX: Acquire lock khi xóa dữ liệu chia sẻ với PTT worker
        with self._score_data_lock:
            self._score_rows_by_key.clear()
            self._tree_item_by_key.clear()
            self._student_full_index.clear()
            self._student_last_name_index.clear()
            self._student_last_two_index.clear()
            self._student_token_index.clear()
            self._student_phonetic_full_index.clear()
            self._student_phonetic_last_name_index.clear()
            self._student_phonetic_last_two_index.clear()
            self._student_phonetic_token_index.clear()
            self._voice_match_cache.clear()
            # BUG #15 FIX: _voice_recent_names cũng được mutate dưới
            # _score_data_lock ở các site khác (build_smart_hints,
            # _do_apply_voice_match) → clear() phải nằm trong lock.
            self._voice_recent_names.clear()
        self._voice_hints = []
        self._voice_hints_primary = []
        self._voice_hints_extended = []
        self._clear_voice_pending()
        self._clear_voice_tied_candidates()  # KHMER #B: roster đổi → reset picker
        self._undo_stack.clear()
        self._last_scanned_target_key = ""
        self._refresh_score_tree()
        self.voice_summary_var.set(reason or "Chưa có danh sách học sinh.")
        if reason and log_reason:
            self._log(reason)

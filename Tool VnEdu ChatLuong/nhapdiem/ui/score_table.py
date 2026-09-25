"""Bảng điểm học sinh (Treeview) và quét điểm."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import asdict
from tkinter import messagebox, ttk

from ..automation.permissions import _rewrite_access_message_for_score_ui
from ..matching import _entry_value
from ..models import ScoreStudentRow, UndoRecord
from ..scorebook_core import ScorebookContext
from ..scores import _format_score_value
from ..voice.phonetics import (
    _normalize_diacritic_text,
    _voice_phonetic_text,
    _voice_phonetic_text_strict,
)


class ScoreTableMixin:
    """Bảng điểm học sinh (Treeview) và quét điểm."""

    def _clone_score_rows(self, rows: dict[str, ScoreStudentRow] | None = None) -> dict[str, ScoreStudentRow]:
        source_rows = self._score_rows_by_key if rows is None else rows
        cloned_rows: dict[str, ScoreStudentRow] = {}
        for row_key, row in source_rows.items():
            row_payload = asdict(row)
            row_payload["normalized_token_set"] = frozenset(row_payload.get("normalized_token_set", ()))
            row_payload["phonetic_token_set"] = frozenset(row_payload.get("phonetic_token_set", ()))
            cloned_rows[row_key] = ScoreStudentRow(**row_payload)
        return cloned_rows

    def _replace_score_rows(
        self,
        rows: dict[str, ScoreStudentRow],
        *,
        undo_stack: list[UndoRecord] | None = None,
    ) -> tuple[int, int]:
        with self._score_data_lock:
            self._score_rows_by_key = dict(sorted(rows.items(), key=lambda item: item[1].row_index))
            if undo_stack is None:
                self._undo_stack.clear()
            else:
                self._undo_stack = list(undo_stack)
            self._rebuild_student_indices()
            pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
            scanned_count = len(self._score_rows_by_key)
        self._refresh_score_tree()
        self._build_voice_hints()
        self.voice_summary_var.set(f"Đã quét {scanned_count} học sinh. Đang có {pending_count} dòng chờ ghi.")
        return scanned_count, pending_count

    def _restore_repair_scan_backup(self, log_message: str) -> None:
        backup_rows = self._repair_scan_backup_rows
        backup_undo = self._repair_scan_backup_undo
        if backup_rows is not None:
            self._replace_score_rows(backup_rows, undo_stack=backup_undo or [])
        self._repair_scan_backup_rows = None
        self._repair_scan_backup_undo = None
        self._retry_apply_after_scan = False
        if log_message:
            self._log(log_message)

    def _hydrate_score_rows(self, entries: list[object]) -> None:
        self._ptt_roster_revision += 1
        previous_rows = self._score_rows_by_key
        preserve_pending_for_same_target = bool(
            self._last_scanned_target_key
            and self._last_scanned_target_key == self._selected_target_score_key()
        )
        previous_rows_by_student_code: dict[str, ScoreStudentRow] = {}
        previous_rows_by_name: dict[str, ScoreStudentRow] = {}
        if preserve_pending_for_same_target:
            for previous_row in previous_rows.values():
                if not previous_row.pending_score:
                    continue
                previous_student_code = previous_row.student_code.strip()
                if previous_student_code and previous_student_code not in previous_rows_by_student_code:
                    previous_rows_by_student_code[previous_student_code] = previous_row
                previous_normalized_name = previous_row.normalized_name or _normalize_diacritic_text(previous_row.student_name)
                if previous_normalized_name and previous_normalized_name not in previous_rows_by_name:
                    previous_rows_by_name[previous_normalized_name] = previous_row
        reused_previous_row_keys: set[str] = set()
        rows: dict[str, ScoreStudentRow] = {}
        for entry in entries:
            row_index = int(_entry_value(entry, "row_index", 0) or 0)
            row_id = str(_entry_value(entry, "row_id", "")).strip()
            student_code = str(_entry_value(entry, "student_code", "")).strip()
            student_name = str(_entry_value(entry, "student_name", "")).strip()
            current_score = _format_score_value(_entry_value(entry, "current_score", ""))
            target_input_name = str(_entry_value(entry, "target_input_name", "")).strip()
            row_key = row_id or f"{student_code}:{row_index}"
            normalized_name = _normalize_diacritic_text(student_name)
            name_parts = normalized_name.split()
            normalized_last_name = name_parts[-1] if name_parts else normalized_name
            normalized_last_two = " ".join(name_parts[-2:]) if len(name_parts) >= 2 else normalized_name
            normalized_sorted_name = " ".join(sorted(name_parts))
            normalized_token_set = frozenset(name_parts)
            phonetic_name = _voice_phonetic_text(normalized_name)
            phonetic_parts = phonetic_name.split()
            phonetic_last_name = phonetic_parts[-1] if phonetic_parts else phonetic_name
            phonetic_last_two = " ".join(phonetic_parts[-2:]) if len(phonetic_parts) >= 2 else phonetic_name
            phonetic_sorted_name = " ".join(sorted(phonetic_parts))
            phonetic_token_set = frozenset(phonetic_parts)
            # KHMER #A: pre-compute strict phonetic keys (chỉ dùng tier 3 fallback).
            strict_phonetic_name = _voice_phonetic_text_strict(normalized_name)
            strict_phonetic_parts = strict_phonetic_name.split()
            strict_phonetic_last_name = (
                strict_phonetic_parts[-1] if strict_phonetic_parts else strict_phonetic_name
            )
            strict_phonetic_last_two = (
                " ".join(strict_phonetic_parts[-2:])
                if len(strict_phonetic_parts) >= 2
                else strict_phonetic_name
            )
            preview_row = ScoreStudentRow(
                row_key=row_key,
                row_index=row_index,
                row_id=row_id,
                student_code=student_code,
                student_name=student_name,
                current_score=current_score,
                target_input_name=target_input_name,
                normalized_name=normalized_name,
                normalized_last_name=normalized_last_name,
                normalized_last_two=normalized_last_two,
                normalized_sorted_name=normalized_sorted_name,
                normalized_token_set=normalized_token_set,
                phonetic_name=phonetic_name,
                phonetic_last_name=phonetic_last_name,
                phonetic_last_two=phonetic_last_two,
                phonetic_sorted_name=phonetic_sorted_name,
                phonetic_token_set=phonetic_token_set,
                strict_phonetic_name=strict_phonetic_name,
                strict_phonetic_last_name=strict_phonetic_last_name,
                strict_phonetic_last_two=strict_phonetic_last_two,
            )
            previous_row = previous_rows.get(row_key)
            if previous_row is None and preserve_pending_for_same_target:
                fallback_row = previous_rows_by_student_code.get(student_code) or previous_rows_by_name.get(normalized_name)
                if fallback_row is not None and fallback_row.row_key not in reused_previous_row_keys:
                    previous_row = fallback_row
            if previous_row is not None and previous_row.pending_score and (
                previous_row.target_input_name == target_input_name or preserve_pending_for_same_target
            ):
                reused_previous_row_keys.add(previous_row.row_key)
                preview_row.pending_score = previous_row.pending_score
                preview_row.recognized_text = previous_row.recognized_text
                preview_row.match_score = previous_row.match_score
                preview_row.status = previous_row.status
            rows[row_key] = preview_row
        self._replace_score_rows(rows)

    def _rebuild_student_indices(self) -> None:
        self._student_full_index.clear()
        self._student_last_name_index.clear()
        self._student_last_two_index.clear()
        self._student_token_index.clear()
        self._student_phonetic_full_index.clear()
        self._student_phonetic_last_name_index.clear()
        self._student_phonetic_last_two_index.clear()
        self._student_phonetic_token_index.clear()
        self._voice_match_cache.clear()
        for row_key, row in self._score_rows_by_key.items():
            if row.normalized_name:
                self._student_full_index.setdefault(row.normalized_name, []).append(row_key)
            if row.normalized_last_name:
                self._student_last_name_index.setdefault(row.normalized_last_name, []).append(row_key)
            if row.normalized_last_two:
                self._student_last_two_index.setdefault(row.normalized_last_two, []).append(row_key)
            for token in row.normalized_token_set:
                if len(token) >= 2:
                    self._student_token_index.setdefault(token, set()).add(row_key)
            if row.phonetic_name:
                self._student_phonetic_full_index.setdefault(row.phonetic_name, []).append(row_key)
            if row.phonetic_last_name:
                self._student_phonetic_last_name_index.setdefault(row.phonetic_last_name, []).append(row_key)
            if row.phonetic_last_two:
                self._student_phonetic_last_two_index.setdefault(row.phonetic_last_two, []).append(row_key)
            for token in row.phonetic_token_set:
                if len(token) >= 2:
                    self._student_phonetic_token_index.setdefault(token, set()).add(row_key)
            # Thêm biệt danh vào các index tìm kiếm
            aliases = self._student_aliases.get(row.student_name, [])
            for alias in aliases:
                normalized_alias = _normalize_diacritic_text(alias)
                if not normalized_alias:
                    continue
                # Alias được coi như tên đầy đủ → score 100 khi exact match
                self._student_full_index.setdefault(normalized_alias, []).append(row_key)
                # Thêm từng token của alias vào token index
                alias_tokens = normalized_alias.split()
                for token in alias_tokens:
                    if len(token) >= 2:
                        self._student_token_index.setdefault(token, set()).add(row_key)
                # Nếu alias có ≥2 từ, thêm vào last_two index
                if len(alias_tokens) >= 2:
                    alias_last_two = " ".join(alias_tokens[-2:])
                    self._student_last_two_index.setdefault(alias_last_two, []).append(row_key)
                # Từ cuối của alias → last_name index
                if alias_tokens:
                    self._student_last_name_index.setdefault(alias_tokens[-1], []).append(row_key)
                phonetic_alias = _voice_phonetic_text(alias)
                phonetic_alias_tokens = phonetic_alias.split()
                if phonetic_alias:
                    self._student_phonetic_full_index.setdefault(phonetic_alias, []).append(row_key)
                for token in phonetic_alias_tokens:
                    if len(token) >= 2:
                        self._student_phonetic_token_index.setdefault(token, set()).add(row_key)
                if len(phonetic_alias_tokens) >= 2:
                    phonetic_last_two = " ".join(phonetic_alias_tokens[-2:])
                    self._student_phonetic_last_two_index.setdefault(phonetic_last_two, []).append(row_key)
                if phonetic_alias_tokens:
                    self._student_phonetic_last_name_index.setdefault(phonetic_alias_tokens[-1], []).append(row_key)

    def _tree_values_for_row(self, row: ScoreStudentRow) -> tuple[object, ...]:
        return (
            row.row_index,
            row.student_name,
            row.current_score,
            row.pending_score,
            row.recognized_text,
            f"{row.match_score}%" if row.match_score else "",
            row.status,
        )

    def _autosize_student_name_column(self) -> None:
        if not hasattr(self, "preview_tree"):
            return
        try:
            style = ttk.Style(self.root)
            font_spec = style.lookup("Treeview", "font") or ("Segoe UI", 10)
            tree_font = tkfont.Font(font=font_spec)
            header_width = tree_font.measure("Họ tên") + 28
            longest_name_width = max(
                (tree_font.measure(row.student_name or "") for row in self._score_rows_by_key.values()),
                default=0,
            )
            target_width = max(header_width, longest_name_width + 28, 150)
            target_width = min(target_width, 320)
            self.preview_tree.column("student_name", width=target_width, minwidth=max(140, min(target_width, 220)))
        except tk.TclError:
            return

    def _focus_preview_tree(self) -> None:
        if self._tree_editor is not None:
            return

        def _apply_focus() -> None:
            try:
                if hasattr(self, "preview_tree") and self.preview_tree.winfo_exists():
                    self.preview_tree.focus_set()
                else:
                    self.root.focus_set()
            except tk.TclError:
                pass

        try:
            self.root.after_idle(_apply_focus)
        except tk.TclError:
            pass

    def _bind_dynamic_wrap(
        self,
        widget: tk.Misc,
        *,
        reference: tk.Misc | None = None,
        padding: int = 24,
        min_width: int = 160,
    ) -> None:
        target = reference or widget

        def _update_wrap(_event: tk.Event | None = None) -> None:
            try:
                width = int(target.winfo_width())
                if width <= 1:
                    width = int(widget.winfo_width())
                widget.configure(wraplength=max(min_width, width - padding))
            except tk.TclError:
                pass

        try:
            target.bind("<Configure>", _update_wrap, add="+")
            self.root.after_idle(_update_wrap)
        except tk.TclError:
            pass

    def _row_tag(self, row: ScoreStudentRow) -> str:
        lowered = row.status.lower()
        if "lỗi" in lowered:
            return "error"
        if "chưa lưu" in lowered:
            return "filled"
        if "đã ghi" in lowered:
            return "saved"
        if row.pending_score:
            return "pending"
        return ""

    def _refresh_score_tree(self) -> None:
        if not hasattr(self, "preview_tree"):
            return
        self._stop_tree_editor(commit=False)
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        self._tree_item_by_key.clear()
        # IMP-B7: Lọc theo search filter nếu có
        search_text = self._search_var.get().strip().lower() if hasattr(self, "_search_var") else ""
        for row_key, row in self._score_rows_by_key.items():
            if search_text and search_text not in row.student_name.lower():
                continue
            item = self.preview_tree.insert("", tk.END, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))
            self._tree_item_by_key[row_key] = item
        self._autosize_student_name_column()

    def _filter_score_tree(self) -> None:
        """IMP-B7: Lọc treeview theo search text — gọi khi user gõ vào ô tìm kiếm."""
        self._refresh_score_tree()

    def on_scan_score_rows(self) -> None:
        if self._busy:
            return
        if not self._retry_apply_after_scan:
            self._repair_scan_backup_rows = None
            self._repair_scan_backup_undo = None
        if self._invalidated_context_fields:
            messagebox.showinfo("Ngữ cảnh đang cập nhật", "Hãy chờ app cập nhật xong Lớp/Môn cho ngữ cảnh mới rồi quét danh sách học sinh.")
            return
        if self.current_context is None:
            messagebox.showwarning("Chưa có ngữ cảnh", "Hãy load Sổ điểm trước khi quét danh sách học sinh.")
            return
        target_column_key = self._selected_target_score_key()
        if not target_column_key:
            messagebox.showwarning("Chưa chọn cột điểm", "Hãy chọn cột điểm đích trước khi app tải danh sách học sinh.")
            return
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi khởi tạo automation", str(error))
            self._log(f"Lỗi scan học sinh: {error}")
            return
        if not hasattr(automation, "scan_score_entries"):
            messagebox.showerror("Thiếu tính năng", "Automation hiện tại chưa có API scan_score_entries.")
            return
        grade_id, class_id, subject_id, term_id = self._context_request_ids()
        if not class_id or not subject_id:
            messagebox.showinfo(
                "Không có quyền nhập điểm",
                "Ngữ cảnh hiện tại không có lớp/môn nào được phép nhập điểm.",
            )
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        target_column_label = self.target_score_column_var.get().strip()
        self._run_background(
            "Đang quét toàn bộ học sinh và cột điểm...",
            lambda progress: automation.scan_score_entries(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                target_column_key=target_column_key,
                target_column_label=target_column_label,
                username=username,
                password=password,
                progress_callback=progress,
            ),
            self._handle_scan_score_rows_success,
            self._handle_scan_score_rows_error,
        )

    def _handle_scan_score_rows_success(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) < 4:
            raise RuntimeError("Kết quả scan_score_entries không hợp lệ.")
        context, entries, login_message, selection_message = result[:4]
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi quét học sinh.")
        repair_backup_rows = self._repair_scan_backup_rows
        backup_pending_count = sum(1 for row in repair_backup_rows.values() if row.pending_score) if repair_backup_rows else 0
        scanned_entries = list(entries or [])
        if login_message:
            self._log(login_message)
        rewritten_selection_message = _rewrite_access_message_for_score_ui(selection_message)
        if rewritten_selection_message:
            self._log(rewritten_selection_message)
        if repair_backup_rows is not None and (
            not scanned_entries
            or not any(str(_entry_value(entry, "target_input_name", "")).strip() for entry in scanned_entries)
        ):
            self._restore_repair_scan_backup(
                "Quét sửa không đọc được liên kết ô nhập live; app đã giữ nguyên toàn bộ điểm chờ cũ."
            )
            messagebox.showwarning(
                "Không khôi phục được liên kết live",
                "App không khôi phục được ô nhập live cho cột điểm này nên đã giữ nguyên toàn bộ Điểm chờ ghi cũ. Hãy quét lại thủ công hoặc tải lại Sổ điểm trước khi ghi.",
            )
            return
        context = self._copy_access_scope(context, self.current_context)
        self._apply_context(context, clear_score_rows=False)
        self._log_effective_context_identity(context)
        self._hydrate_score_rows(scanned_entries)
        if repair_backup_rows is not None:
            repaired_pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
            repaired_linked_pending_count = sum(
                1 for row in self._score_rows_by_key.values() if row.pending_score and row.target_input_name
            )
            if repaired_pending_count < backup_pending_count or (
                backup_pending_count > 0 and repaired_linked_pending_count == 0
            ):
                self._restore_repair_scan_backup(
                    "Quét sửa không bảo toàn được điểm chờ cũ; app đã khôi phục lại dữ liệu trước khi quét sửa."
                )
                messagebox.showwarning(
                    "Đã giữ lại điểm chờ cũ",
                    "Lần quét sửa vừa rồi không bảo toàn được toàn bộ Điểm chờ ghi, nên app đã tự khôi phục lại dữ liệu cũ để tránh mất điểm đã đọc.",
                )
                return
        self._last_scanned_target_key = self._selected_target_score_key()
        self._focus_preview_tree()
        self._log(f"Đã quét {len(self._score_rows_by_key)} học sinh cho cột {self.target_score_column_var.get()}.")
        if self._ptt_enabled:
            self._build_voice_hints()
        self._repair_scan_backup_rows = None
        self._repair_scan_backup_undo = None
        if self._retry_apply_after_scan:
            self._log("Đã quét lại xong; app đang thử ghi điểm lại.")
            self.root.after(0, self.on_apply_pending_scores)

    def _handle_scan_score_rows_error(self, error: Exception) -> None:
        self._repair_scan_backup_rows = None
        self._repair_scan_backup_undo = None
        self._retry_apply_after_scan = False
        messagebox.showerror("Lỗi quét học sinh", str(error))
        self._log(f"Lỗi quét học sinh: {error}")
        self._set_progress(0.0, "Quét học sinh thất bại")

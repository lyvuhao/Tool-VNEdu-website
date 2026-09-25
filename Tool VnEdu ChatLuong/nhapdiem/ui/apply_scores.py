"""Ghi điểm chờ lên VNEDU, xoá/làm tròn điểm chờ."""

from __future__ import annotations

from tkinter import messagebox

from ..matching import _entry_value
from ..models import LogTag, RowStatus, ScoreStudentRow
from ..scorebook_core import ScorebookContext
from ..scores import (
    _build_apply_confirmation_summary,
    _build_score_apply_payload,
    _round_score_to_one_decimal,
)


class ApplyScoresMixin:
    """Ghi điểm chờ lên VNEDU, xoá/làm tròn điểm chờ."""

    def _pending_rows(self, row_keys: list[str] | None = None) -> list[ScoreStudentRow]:
        candidates = self._score_rows_by_key.values() if row_keys is None else [self._score_rows_by_key[key] for key in row_keys if key in self._score_rows_by_key]
        return [row for row in candidates if row.pending_score and row.target_input_name]

    def _selected_preview_row_keys(self) -> list[str]:
        if not hasattr(self, "preview_tree"):
            return []
        selected_items = set(self.preview_tree.selection())
        if not selected_items:
            return []
        return [row_key for row_key, tree_item in self._tree_item_by_key.items() if tree_item in selected_items]

    def _build_apply_payload(self, row_keys: list[str] | None = None, *, score_field: str = "pending_score") -> list[dict[str, str]]:
        candidates = self._score_rows_by_key.values() if row_keys is None else [self._score_rows_by_key[key] for key in row_keys if key in self._score_rows_by_key]
        return _build_score_apply_payload(list(candidates), score_field=score_field)

    def on_apply_pending_scores(self) -> None:
        if not self._stop_tree_editor(commit=True):
            return
        if self._invalidated_context_fields:
            messagebox.showinfo("Ngữ cảnh đang cập nhật", "Hãy chờ app cập nhật xong Lớp/Môn cho ngữ cảnh mới rồi mới ghi điểm lên web.")
            return
        payload = self._build_apply_payload()
        apply_from_current_selection = False
        if not payload:
            if any(row.pending_score for row in self._score_rows_by_key.values()):
                if self._retry_apply_after_scan:
                    self._retry_apply_after_scan = False
                    messagebox.showinfo(
                        "Cần quét lại dữ liệu",
                        "Đang có Điểm chờ ghi nhưng app chưa gắn được ô nhập live cho cột điểm này. Hãy quét lại danh sách học sinh rồi bấm GHI lại.",
                    )
                    return
                if self.current_context is not None and self._selected_target_score_key():
                    self._repair_scan_backup_rows = self._clone_score_rows()
                    self._repair_scan_backup_undo = list(self._undo_stack)
                    self._retry_apply_after_scan = True
                    self._log("Phát hiện điểm chờ nhưng thiếu liên kết ô nhập live; app đang quét lại danh sách để khôi phục target_input_name.")
                    self.on_scan_score_rows()
                    return
                messagebox.showinfo(
                    "Cần quét lại dữ liệu",
                    "Đang có Điểm chờ ghi nhưng app chưa gắn được ô nhập live cho cột điểm này. Hãy quét lại danh sách học sinh rồi bấm GHI lại.",
                )
                return
            selected_row_keys = self._selected_preview_row_keys()
            payload = self._build_apply_payload(selected_row_keys, score_field="current_score")
            if payload:
                apply_from_current_selection = True
            else:
                messagebox.showinfo("Chưa có dữ liệu", "Chưa có Điểm chờ ghi. Nếu muốn ghi lại Điểm hiện tại, hãy chọn ít nhất 1 dòng có điểm trong Treeview rồi bấm lại.")
                return
        self._retry_apply_after_scan = False
        # IMP-A4: Confirm trước khi ghi điểm lên web, hiển thị tóm tắt
        summary_text = _build_apply_confirmation_summary(payload, self._score_rows_by_key)
        confirmation_message = (
            f"Chưa có Điểm chờ ghi. App sẽ dùng Điểm hiện tại của {len(payload)} dòng đang chọn để ghi lên VNEDU:\n\n{summary_text}\n\nBạn có chắc muốn tiếp tục?"
            if apply_from_current_selection
            else f"Sẽ ghi {len(payload)} điểm lên VNEDU:\n\n{summary_text}\n\nBạn có chắc muốn tiếp tục?"
        )
        if not messagebox.askyesno(
            "Xác nhận ghi điểm lên web",
            confirmation_message,
        ):
            return
        if self.current_context is None:
            messagebox.showwarning("Chưa có ngữ cảnh", "Hãy load Sổ điểm trước khi ghi điểm.")
            return
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi khởi tạo automation", str(error))
            self._log(f"Lỗi ghi điểm: {error}")
            return
        if not hasattr(automation, "apply_score_entries"):
            messagebox.showerror("Thiếu tính năng", "Automation hiện tại chưa có API apply_score_entries.")
            return
        grade_id, class_id, subject_id, term_id = self._context_request_ids()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        auto_save = bool(self.auto_save_scores_var.get())
        submitted_entries_by_input_name = {
            item["target_input_name"]: {
                "row_key": item["row_key"],
                "proposed_score": item["proposed_score"],
            }
            for item in payload
        }

        self._run_background(
            "Đang ghi điểm lên live VNEDU..." if apply_from_current_selection else "Đang ghi các điểm chờ lên live VNEDU...",
            lambda progress: automation.apply_score_entries(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                entries=payload,
                username=username,
                password=password,
                auto_save=auto_save,
                progress_callback=progress,
            ),
            lambda result: self._handle_apply_pending_scores_success(result, submitted_entries_by_input_name, auto_save=auto_save),
            lambda error: (messagebox.showerror("Lỗi ghi điểm", str(error)), self._log(f"Lỗi ghi điểm: {error}"), self._set_progress(0.0, "Ghi điểm thất bại")),
        )

    def _handle_apply_pending_scores_success(
        self,
        result: object,
        submitted_entries_by_input_name: dict[str, dict[str, str]],
        auto_save: bool = True,
    ) -> None:
        if not isinstance(result, tuple) or len(result) < 4:
            raise RuntimeError("Kết quả apply_score_entries không hợp lệ.")
        context, write_result, login_message, selection_message = result[:4]
        if isinstance(context, ScorebookContext):
            self._apply_context(context, clear_score_rows=False)
        if login_message:
            self._log(login_message)
        if selection_message:
            self._log(selection_message)

        verified_names = list(_entry_value(write_result, "verified_input_names", [])) if write_result is not None else []
        failed_names = list(_entry_value(write_result, "failed_input_names", [])) if write_result is not None else []
        attempted = int(_entry_value(write_result, "attempted", len(verified_names))) if write_result is not None else 0
        save_verified = bool(_entry_value(write_result, "save_verified", False)) if write_result is not None else False
        save_detail = str(_entry_value(write_result, "save_verification_detail", "")).strip() if write_result is not None else ""

        # H1 FIX: Một dòng chỉ được coi là "Đã ghi" (lưu lên VNEDU) khi đã bật tự
        # bấm Lưu VÀ server xác nhận. Nếu chỉ điền vào DOM mà chưa Lưu, giữ nguyên
        # pending_score để tránh mất điểm và đánh dấu trạng thái "Đã điền (chưa lưu)".
        truly_saved = bool(auto_save and save_verified)
        saved_count = 0
        filled_count = 0
        # LOCK FIX: mutate row data dưới _score_data_lock để PTT worker đọc nhất
        # quán (handler chạy main thread, worker đọc cùng field qua hint/match).
        # Tk widget update gom ra ngoài lock theo đúng pattern _apply_row_patch.
        rows_to_repaint: list[str] = []
        with self._score_data_lock:
            for input_name in verified_names:
                submitted_entry = submitted_entries_by_input_name.get(str(input_name).strip(), {})
                row_key = str(submitted_entry.get("row_key", "")).strip()
                row = self._score_rows_by_key.get(row_key)
                if row is None:
                    continue
                submitted_score = str(submitted_entry.get("proposed_score", "") or "").strip()
                if truly_saved:
                    if submitted_score:
                        row.current_score = submitted_score
                    row.pending_score = ""
                    row.status = RowStatus.SAVED
                    saved_count += 1
                else:
                    # Điểm đã được điền vào ô nhập trên trình duyệt nhưng CHƯA lưu lên
                    # server. Giữ pending_score để người dùng có thể Lưu lại sau.
                    if submitted_score:
                        row.pending_score = submitted_score
                    row.status = RowStatus.FILLED
                    filled_count += 1
                rows_to_repaint.append(row.row_key)

            for input_name in failed_names:
                submitted_entry = submitted_entries_by_input_name.get(str(input_name).strip(), {})
                row_key = str(submitted_entry.get("row_key", "")).strip()
                row = self._score_rows_by_key.get(row_key)
                if row is None:
                    continue
                row.status = RowStatus.ERROR
                rows_to_repaint.append(row.row_key)

            pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
            student_count = len(self._score_rows_by_key)

        for repaint_key in rows_to_repaint:
            row = self._score_rows_by_key.get(repaint_key)
            if row is None:
                continue
            item = self._tree_item_by_key.get(repaint_key)
            if item:
                self.preview_tree.item(item, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))

        self.voice_summary_var.set(f"Đã quét {student_count} học sinh. Còn {pending_count} dòng chờ ghi.")
        if truly_saved:
            self._log(
                f"Đã LƯU {saved_count}/{attempted} dòng điểm lên VNEDU. "
                f"Xác minh lưu server: có{f' ({save_detail})' if save_detail else ''}.",
                tag=LogTag.SUCCESS,
            )
        elif filled_count:
            self._log(
                f"Đã điền {filled_count}/{attempted} dòng vào ô nhập nhưng CHƯA lưu lên VNEDU. "
                "Hãy bật 'Tự bấm Lưu sau khi ghi' rồi ghi lại, hoặc bấm Lưu thủ công trên trình duyệt. "
                "Điểm chờ được giữ nguyên để tránh mất dữ liệu.",
                tag=LogTag.WARNING,
            )
        else:
            self._log(
                f"Ghi điểm chưa thành công ({attempted} dòng đã thử). "
                f"Xác minh lưu server: không{f' ({save_detail})' if save_detail else ''}.",
                tag=LogTag.WARNING,
            )

    def on_clear_pending_scores(self) -> None:
        if not self._stop_tree_editor(commit=True):
            return
        if not any(row.pending_score for row in self._score_rows_by_key.values()):
            return
        # IMP-A3: Confirm trước khi xóa tất cả điểm chờ
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        if not messagebox.askyesno(
            "Xác nhận xóa điểm chờ",
            f"Bạn có chắc muốn xóa {pending_count} điểm chờ ghi?\n\nThao tác này không thể hoàn tác.",
            icon="warning",
        ):
            return
        # BUG #20: snapshot row_keys trước khi mutate, tránh edge-case re-entry
        # nếu sau này _apply_row_patch trigger callback đụng vào dict gốc.
        rows_to_clear = [
            (row_key, row)
            for row_key, row in self._score_rows_by_key.items()
            if row.pending_score
        ]
        for row_key, row in rows_to_clear:
            self._apply_row_patch(
                row_key,
                pending_score="",
                status=RowStatus.READY,
                recognized_text=row.recognized_text,
                match_score=0,
                reason="xóa điểm chờ",
            )
        self._update_score_summary()

    def on_round_pending_scores(self) -> None:
        if not self._stop_tree_editor(commit=True):
            return
        candidate_rows = [
            (row_key, row)
            for row_key, row in self._score_rows_by_key.items()
            if row.pending_score or row.current_score
        ]
        if not candidate_rows:
            messagebox.showinfo("Chưa có dữ liệu", "Chưa có điểm chờ hoặc điểm hiện tại để làm tròn.")
            return

        rounded_count = 0
        for row_key, row in candidate_rows:
            source_score = row.pending_score or row.current_score
            rounded_score = _round_score_to_one_decimal(source_score)
            if rounded_score == source_score:
                continue
            self._apply_row_patch(
                row_key,
                pending_score=rounded_score,
                status=RowStatus.PENDING,
                recognized_text=row.recognized_text,
                match_score=row.match_score,
                reason=("làm tròn điểm chờ" if row.pending_score else "làm tròn từ điểm hiện tại"),
            )
            rounded_count += 1

        if rounded_count > 0:
            status_message = f"Đã làm tròn {rounded_count} điểm tới 1 chữ số thập phân."
        else:
            status_message = "Không có điểm chờ hoặc điểm hiện tại nào cần làm tròn."
        self.voice_status_var.set(status_message)
        self._log(status_message)

"""Quét quyền và ghi nhận xét lên VNEDU."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Dict, Tuple

from ..access import apply_access_entries_to_context
from ..config import PASSIVE_SCAN_SKIPPED
from ..models import CommentWriteResult, ScorebookContext, ScoreColumnSchema


class ApplyMixin:
    """Quét quyền và ghi nhận xét lên VNEDU."""

    def _context_has_comment_permission(self, context: ScorebookContext) -> bool:
        """Returns whether the current scorebook context allows comment editing."""
        normalized_permission = context.permission_text.strip().lower()
        if "không có quyền" in normalized_permission or "khong co quyen" in normalized_permission:
            return False
        return context.enabled_comment_input_count > 0

    def _schedule_access_scan_for_context(
        self,
        context: ScorebookContext,
        reason: str,
    ) -> None:
        """Starts a passive permission scan for the current grade-term so filtered lists can update later."""
        if not getattr(self, "_matrix_access_scan_enabled", True):
            return
        self._sync_access_cache_identity()
        scan_key = self._access_cache_key(context.selected_grade_id, context.selected_term_id)
        scan_grade_id = context.selected_grade_id.strip()
        scan_term_id = context.selected_term_id.strip()
        if not scan_grade_id or not scan_term_id:
            return
        if scan_key in self.access_entry_cache:
            return
        if scan_key in self._access_scan_inflight:
            return

        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001 - background warmup should stay non-blocking
            self._log(f"Bỏ qua dò quyền nền: {error}")
            return

        username = self.username_var.get().strip()
        password = self.password_var.get()
        expected_task_token = self._foreground_task_token
        self._access_scan_inflight.add(scan_key)
        self._log(
            f"Đang dò quyền lớp/môn nền cho khối {context.selected_grade_id}, học kỳ {context.selected_term_id}"
            f" ({reason})..."
        )

        def worker() -> object:
            if self._access_scan_block_reason(scan_key, expected_task_token):
                return PASSIVE_SCAN_SKIPPED
            if not self._automation_lock.acquire(blocking=False):
                return PASSIVE_SCAN_SKIPPED
            try:
                if self._access_scan_block_reason(scan_key, expected_task_token):
                    return PASSIVE_SCAN_SKIPPED
                return automation.discover_accessible_entries_for_current_context(
                    username=username,
                    password=password,
                    expected_grade_id=scan_grade_id,
                    expected_term_id=scan_term_id,
                )
            finally:
                self._automation_lock.release()

        def on_success(result: object) -> None:
            self._access_scan_inflight.discard(scan_key)
            skip_reason = self._access_scan_block_reason(scan_key, expected_task_token)
            if result is PASSIVE_SCAN_SKIPPED:
                if skip_reason:
                    self._log(
                        f"Bỏ qua dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."
                    )
                return
            if skip_reason:
                self._log(
                    f"Bỏ qua cập nhật kết quả dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."
                )
                return
            scanned_context, entries, login_message = result if isinstance(result, tuple) else (None, None, "")
            if not isinstance(scanned_context, ScorebookContext) or not isinstance(entries, list):
                raise RuntimeError("Không nhận được dữ liệu quét quyền nền hợp lệ.")
            if login_message:
                self._log(login_message)
            if not entries:
                self._log(
                    "Dò quyền nền chưa trả về ma trận lớp/môn đầy đủ; "
                    "app giữ danh sách live hiện tại và không cache kết quả rỗng."
                )
            else:
                self.access_entry_cache[scan_key] = list(entries)
                self._log(
                    f"Đã cập nhật nền quyền lớp/môn cho khối {scan_grade_id}, học kỳ {scan_term_id}: "
                    f"{len(entries)} tổ hợp hợp lệ."
                )
            if self.current_context is None:
                return
            if self._access_scan_block_reason(scan_key, expected_task_token):
                return

            scanned_context.selected_grade_id = self.current_context.selected_grade_id
            scanned_context.selected_class_id = self.current_context.selected_class_id
            scanned_context.selected_subject_id = self.current_context.selected_subject_id
            scanned_context.selected_term_id = self.current_context.selected_term_id
            if entries:
                scanned_context = apply_access_entries_to_context(
                    scanned_context,
                    entries,
                    grade_id=scan_grade_id,
                    term_id=scan_term_id,
                )
            self._apply_scorebook_context(scanned_context)

        def on_error(error: Exception) -> None:
            self._access_scan_inflight.discard(scan_key)
            self._log(f"Lỗi dò quyền nền: {error}")

        def launch_passive_scan() -> None:
            if scan_key not in self._access_scan_inflight:
                return
            skip_reason = self._access_scan_block_reason(scan_key, expected_task_token)
            if skip_reason:
                self._access_scan_inflight.discard(scan_key)
                self._log(
                    f"Bỏ qua khởi động dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."
                )
                return
            self._run_passive_background_task(worker=worker, on_success=on_success, on_error=on_error)

        try:
            self.root.after(self._passive_scan_delay_ms, launch_passive_scan)
        except tk.TclError:
            self._access_scan_inflight.discard(scan_key)
            return

    def _selected_scorebook_context_ids(self) -> Tuple[str, str, str, str]:
        """Reads the currently selected scorebook ids from the GUI with safe fallbacks."""
        if self.current_context is None:
            raise RuntimeError("Chưa có ScorebookContext để xác định ngữ cảnh hiện tại.")

        grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id) or self.current_context.selected_grade_id
        class_id = self._selected_option_id(self.class_var, self.class_label_to_id) or self.current_context.selected_class_id
        subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id) or self.current_context.selected_subject_id
        term_id = self._selected_option_id(self.term_var, self.term_label_to_id) or self.current_context.selected_term_id

        if not grade_id or not class_id or not subject_id or not term_id:
            raise RuntimeError("Ngữ cảnh Khối/Lớp/Môn/Học kỳ chưa đầy đủ.")
        return grade_id, class_id, subject_id, term_id

    def _resolve_selected_columns(
        self,
        context: ScorebookContext,
    ) -> Tuple[ScoreColumnSchema, ScoreColumnSchema]:
        """Returns the selected source score column and detected target comment column."""
        source_column_key = self._selected_score_source_key(context)
        detected = context.detected_columns
        source_schema = self._schema_by_key(context.column_schemas, source_column_key)
        comment_schema = self._schema_by_key(context.column_schemas, detected.preferred_comment_column_key)
        if source_schema is None:
            raise RuntimeError("Chưa chọn được cột điểm nguồn hợp lệ trên bảng điểm hiện tại.")
        if comment_schema is None:
            raise RuntimeError("Chưa tự nhận diện được cột nhận xét đích trên bảng điểm hiện tại.")
        return source_schema, comment_schema

    def _background_credentials(self) -> Tuple[str, str]:
        """Returns the current username/password pair for background automation calls."""
        return self.username_var.get().strip(), self.password_var.get()

    def _collect_comment_apply_request(self) -> Dict[str, object]:
        """Collects the UI state required for one apply-comments run."""
        if self.current_context is None:
            raise RuntimeError("Hãy đọc Sổ điểm trước khi ghi nhận xét.")

        automation = self._build_automation()
        rules = self._validate_comment_rules()
        grade_id, class_id, subject_id, term_id = self._selected_scorebook_context_ids()
        source_schema, comment_schema = self._resolve_selected_columns(self.current_context)
        username, password = self._background_credentials()
        return {
            "automation": automation,
            "rules": rules,
            "grade_id": grade_id,
            "class_id": class_id,
            "subject_id": subject_id,
            "term_id": term_id,
            "source_column_key": source_schema.column_key,
            "comment_column_key": comment_schema.column_key,
            "username": username,
            "password": password,
            "auto_save": bool(self.auto_save_var.get()),
            "allow_overwrite_existing_comment": bool(self.allow_comment_overwrite_var.get()),
        }

    def _run_comment_apply(self, request: Dict[str, object]) -> object:
        """Executes one full analyze-and-apply cycle for the current UI rule set."""
        automation = request["automation"]
        return automation.analyze_and_apply_comment_rows(
            grade_id=request["grade_id"],
            class_id=request["class_id"],
            subject_id=request["subject_id"],
            term_id=request["term_id"],
            source_column_key=request["source_column_key"],
            comment_column_key=request["comment_column_key"],
            rules=request["rules"],
            username=request["username"],
            password=request["password"],
            auto_save=bool(request["auto_save"]),
            allow_overwrite_existing_comment=bool(request["allow_overwrite_existing_comment"]),
            progress_callback=request.get("progress_callback"),
        )

    def _handle_comment_apply_success(
        self,
        result: object,
        auto_save: bool,
        allow_overwrite_existing_comment: bool,
    ) -> None:
        """Handles the UI/logging side after one apply-comments run completes."""
        (
            queue_context,
            write_rows,
            queue_login_message,
            queue_selection_message,
            apply_context,
            apply_result,
            apply_login_message,
            apply_selection_message,
        ) = result if isinstance(result, tuple) else (None, None, "", "", None, None, "", "")
        if (
            not isinstance(queue_context, ScorebookContext)
            or not isinstance(write_rows, list)
            or not isinstance(apply_context, ScorebookContext)
            or (apply_result is not None and not isinstance(apply_result, CommentWriteResult))
        ):
            raise RuntimeError("Không nhận được kết quả ghi nhận xét hợp lệ từ live Chrome.")
        if queue_login_message:
            self._log(queue_login_message)
        if queue_selection_message:
            self._log(queue_selection_message)
        counts = self._write_status_counts(write_rows)
        self._warn_students_with_numeric_comment_scores(
            write_rows,
            allow_overwrite_existing_comment=allow_overwrite_existing_comment,
        )
        self._log(
            "Đã phân tích dữ liệu trước khi ghi: "
            f"{len(write_rows)} dòng, "
            f"{counts.get('ready', 0)} sẵn sàng, "
            f"{counts.get('skip_no_score', 0)} chưa có điểm, "
            f"{counts.get('skip_existing_comment', 0)} đã có nhận xét chữ, "
            f"{counts.get('skip_unmatched', 0)} không khớp rule, "
            f"{counts.get('skip_same', 0)} đã giống."
        )
        if apply_result is None:
            self._apply_scorebook_context(apply_context)
            no_ready_lines = [
                "Không có học sinh nào sẵn sàng để ghi nhận xét.",
            ]
            if counts.get("skip_existing_comment", 0) and not allow_overwrite_existing_comment:
                no_ready_lines.append(
                    "Các ô đã có nhận xét chữ đang được giữ nguyên. "
                    "Muốn thay thế có chủ đích, hãy bật 'Cho phép ghi đè nhận xét chữ đã có'."
                )
            no_ready_lines.append(
                "Hãy kiểm tra lại rule, cột điểm nguồn, hoặc các ô nhận xét chữ đã có."
            )
            messagebox.showwarning(
                "Không có dòng để ghi",
                "\n".join(no_ready_lines),
            )
            self._log("Không có dòng nào sẵn sàng để ghi sau khi phân tích dữ liệu.")
            self._set_status_text("Không có dòng để ghi")
            return
        if apply_login_message:
            self._log(apply_login_message)
        if apply_selection_message:
            self._log(apply_selection_message)
        self._apply_scorebook_context(apply_context)
        if not apply_result.save_clicked and auto_save:
            self._log("Không tìm thấy nút Lưu để bấm tự động; dữ liệu mới chỉ được xác minh trong ô nhập hiện tại.")
        if auto_save and apply_result.save_clicked:
            if apply_result.save_verified:
                self._log(apply_result.save_verification_detail or "Đã xác minh Lưu thành công ở mức server-side.")
            else:
                self._log(
                    "Cảnh báo xác minh Lưu: "
                    + (apply_result.save_verification_detail or "Đã bấm Lưu nhưng chưa xác minh được phản hồi server.")
                )
        if not auto_save:
            self._log("Đã ghi vào ô nhận xét nhưng chưa bấm Lưu tự động; cần kiểm tra và lưu thủ công nếu VNEDU yêu cầu.")

        failed_suffix = ""
        if apply_result.failed_rows:
            failed_suffix = f" Lỗi/không xác minh được: {', '.join(apply_result.failed_rows[:5])}"
            if len(apply_result.failed_rows) > 5:
                failed_suffix += f" ... (+{len(apply_result.failed_rows) - 5})"
        self._log(
            "Kết quả ghi nhận xét: "
            f"đã thử {apply_result.attempted}, "
            f"điền vào {apply_result.updated}, "
            f"xác minh {apply_result.verified}, "
            f"bỏ qua {apply_result.skipped}."
            f"{failed_suffix}"
        )
        messagebox.showinfo(
            "Đã ghi nhận xét",
            (
                f"Đã thử ghi {apply_result.attempted} dòng.\n"
                f"Xác minh thành công: {apply_result.verified}\n"
                f"Bỏ qua: {apply_result.skipped}\n"
                f"Tự bấm Lưu: {'Có' if apply_result.save_clicked else 'Không'}\n"
                f"Xác minh Lưu server-side: {'Có' if apply_result.save_verified else 'Không'}"
            ),
        )

    def _handle_comment_apply_error(self, error: Exception) -> None:
        """Handles one apply-comments failure on the UI thread."""
        messagebox.showerror("Lỗi ghi nhận xét", str(error))
        self._log(f"Lỗi ghi nhận xét: {error}")
        self._set_status_text("Ghi nhận xét thất bại")

    def on_apply_comments(self) -> None:
        """Builds the write queue internally and writes comments back into the live scorebook."""
        if self.current_context is None:
            messagebox.showwarning("Chưa có dữ liệu", "Hãy đọc Sổ điểm trước khi ghi nhận xét.")
            self._log("Bỏ qua ghi nhận xét vì chưa có ScorebookContext.")
            return

        try:
            request = self._collect_comment_apply_request()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi ghi nhận xét", str(error))
            self._log(f"Lỗi ghi nhận xét: {error}")
            return

        self._run_background_task(
            "Đang phân tích dữ liệu và ghi nhận xét lên live Chrome...",
            worker=lambda progress: self._run_comment_apply(
                {
                    **request,
                    "progress_callback": progress,
                }
            ),
            on_success=lambda result: self._handle_comment_apply_success(
                result,
                auto_save=bool(request["auto_save"]),
                allow_overwrite_existing_comment=bool(request["allow_overwrite_existing_comment"]),
            ),
            on_error=self._handle_comment_apply_error,
        )

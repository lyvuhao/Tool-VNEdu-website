"""Tóm tắt và xuất dữ liệu."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from tkinter import messagebox

from ..config import PAYLOAD_FILE, SNAPSHOT_FILE
from ..storage import _write_json_atomic_file


class ExportMixin:
    """Tóm tắt và xuất dữ liệu."""

    def on_show_summary(self) -> None:
        """Hiển thị tóm tắt ngữ cảnh sổ điểm hiện tại."""
        try:
            summary = self._summary()
        except Exception as error:  # noqa: BLE001
            messagebox.showwarning("Chưa có ngữ cảnh", str(error))
            self._log(f"Không thể hiện tóm tắt: {error}")
            return
        messagebox.showinfo("Tóm tắt ngữ cảnh", summary)
        self._log("Đã hiển thị tóm tắt ngữ cảnh hiện tại.")

    def on_copy_summary(self) -> None:
        try:
            summary = self._summary()
        except Exception as error:  # noqa: BLE001
            messagebox.showwarning("Chưa có ngữ cảnh", str(error))
            self._log(f"Không thể sao chép tóm tắt: {error}")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(summary)
        self.root.update_idletasks()
        self._log("Đã sao chép tóm tắt ngữ cảnh vào clipboard.")
        self._set_progress(self._progress_value, "Đã sao chép tóm tắt ngữ cảnh")

    def on_export_snapshot(self) -> None:
        try:
            context = self._require_context()
            payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "summary": self._summary(),
                "context": asdict(context),
                "pending_rows": [
                    {
                        "row_index": row.row_index,
                        "student_code": row.student_code,
                        "student_name": row.student_name,
                        "current_score": row.current_score,
                        "pending_score": row.pending_score,
                        "recognized_text": row.recognized_text,
                        "match_score": row.match_score,
                        "status": row.status,
                    }
                    for row in self._score_rows_by_key.values()
                    if row.pending_score
                ],
            }
            _write_json_atomic_file(SNAPSHOT_FILE, payload)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi xuất snapshot", str(error))
            self._log(f"Lỗi xuất snapshot: {error}")
            return
        self._log(f"Đã xuất snapshot ngữ cảnh vào {SNAPSHOT_FILE}")
        self._set_progress(self._progress_value, "Đã xuất snapshot JSON")
        messagebox.showinfo("Đã xuất", f"Đã ghi snapshot vào {SNAPSHOT_FILE}")

    def on_export_feature_payload(self) -> None:
        try:
            context = self._require_context()
            payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "feature_name": self.feature_name_var.get().strip() or "PTT nhập điểm",
                "selected_context": {
                    "grade_text": self.grade_var.get(),
                    "class_text": self.class_var.get(),
                    "subject_text": self.subject_var.get(),
                    "term_text": self.term_var.get(),
                    "grade_id": self._selected_id(self.grade_var, self.grade_label_to_id) or context.selected_grade_id,
                    "class_id": self._selected_id(self.class_var, self.class_label_to_id) or context.selected_class_id,
                    "subject_id": self._selected_id(self.subject_var, self.subject_label_to_id) or context.selected_subject_id,
                    "term_id": self._selected_id(self.term_var, self.term_label_to_id) or context.selected_term_id,
                    "target_score_column_key": self._selected_target_score_key(),
                    "target_score_column_text": self.target_score_column_var.get(),
                },
                "window_title": context.window_title,
                "teacher_text": context.teacher_text,
                "permission_text": context.permission_text,
                "column_count": len(context.column_schemas),
                "enabled_comment_input_count": context.enabled_comment_input_count,
                "scanned_student_count": len(self._score_rows_by_key),
                "pending_score_count": sum(1 for row in self._score_rows_by_key.values() if row.pending_score),
            }
            _write_json_atomic_file(PAYLOAD_FILE, payload)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi xuất payload", str(error))
            self._log(f"Lỗi xuất payload: {error}")
            return
        self._log(f"Đã xuất payload tác vụ vào {PAYLOAD_FILE}")
        self._set_progress(self._progress_value, "Đã xuất payload tác vụ")
        messagebox.showinfo("Đã xuất", f"Đã ghi payload vào {PAYLOAD_FILE}")

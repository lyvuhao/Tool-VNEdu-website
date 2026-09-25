"""Sửa điểm trực tiếp trên bảng, undo."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..config import UNDO_STACK_MAX_SIZE
from ..models import RowStatus, ScoreStudentRow, UndoRecord
from ..scores import _format_score_value, parse_manual_score_text


class EditingMixin:
    """Sửa điểm trực tiếp trên bảng, undo."""

    def _row_snapshot(self, row: ScoreStudentRow) -> dict[str, object]:
        return {
            "pending_score": row.pending_score,
            "recognized_text": row.recognized_text,
            "match_score": row.match_score,
            "status": row.status,
            "current_score": row.current_score,
        }

    def _apply_row_state(self, row_key: str, snapshot: dict[str, object]) -> None:
        # BUG #15 hardening: mutate row data dưới _score_data_lock để PTT worker
        # đọc consistent. Tk widget update vẫn ở ngoài lock vì luôn chạy main thread.
        with self._score_data_lock:
            row = self._score_rows_by_key.get(row_key)
            if row is None:
                return
            row.pending_score = str(snapshot.get("pending_score", row.pending_score) or "")
            row.recognized_text = str(snapshot.get("recognized_text", row.recognized_text) or "")
            row.match_score = int(snapshot.get("match_score", row.match_score) or 0)
            row.status = str(snapshot.get("status", row.status) or row.status)
            row.current_score = str(snapshot.get("current_score", row.current_score) or "")
        item = self._tree_item_by_key.get(row_key)
        if item:
            self.preview_tree.item(item, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))

    def _apply_row_patch(
        self,
        row_key: str,
        *,
        pending_score: str,
        status: str,
        recognized_text: str,
        match_score: int,
        reason: str,
        current_score: str | None = None,
        push_undo: bool = True,
    ) -> None:
        # BUG #15 hardening: lock cho phần mutate row + undo append; UI update
        # ngoài lock vì là main thread và phụ thuộc snapshot row sau mutation.
        with self._score_data_lock:
            row = self._score_rows_by_key.get(row_key)
            if row is None:
                return
            before = self._row_snapshot(row)
            row.pending_score = pending_score
            row.status = status
            row.recognized_text = recognized_text
            row.match_score = match_score
            if current_score is not None:
                row.current_score = current_score
            after = self._row_snapshot(row)
            if push_undo and before != after:
                self._undo_stack.append(UndoRecord(row_key=row_key, before=before, after=after, reason=reason))
                # BUG-03 FIX: Giới hạn undo stack để tránh memory leak
                while len(self._undo_stack) > UNDO_STACK_MAX_SIZE:
                    self._undo_stack.pop(0)
        item = self._tree_item_by_key.get(row_key)
        if item:
            self.preview_tree.item(item, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))
        # IMP-B6: Cập nhật pending count + undo count trên summary
        self._update_score_summary()

    def _update_score_summary(self) -> None:
        """IMP-B6 + IMP-D8: Cập nhật voice_summary_var với pending count, undo count và phân bố điểm."""
        if not self._score_rows_by_key:
            return
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        undo_count = len(self._undo_stack)
        undo_hint = f"  •  Undo: {undo_count}" if undo_count > 0 else ""
        # IMP-D8: Tính phân bố điểm chờ (min/max/avg)
        pending_values = []
        for row in self._score_rows_by_key.values():
            if row.pending_score:
                try:
                    pending_values.append(float(row.pending_score.replace(",", ".")))
                except (ValueError, AttributeError):
                    pass
        stats_hint = ""
        if pending_values:
            min_v = min(pending_values)
            max_v = max(pending_values)
            avg_v = sum(pending_values) / len(pending_values)
            stats_hint = f"  •  Điểm: {min_v:.1f}↓ {avg_v:.1f}~ {max_v:.1f}↑"
        self.voice_summary_var.set(
            f"Đã quét {len(self._score_rows_by_key)} học sinh. Còn {pending_count} dòng chờ ghi.{undo_hint}{stats_hint}"
        )

    def _on_undo_shortcut(self, _event: tk.Event | None = None) -> str:
        focused = self.root.focus_get()
        if self._tree_editor is not None and focused is self._tree_editor:
            return "break"
        if not self._undo_stack:
            return "break"
        undo = self._undo_stack.pop()
        if undo.row_key in self._score_rows_by_key:
            self._apply_row_state(undo.row_key, undo.before)
            self._log(f"Undo: {self._score_rows_by_key[undo.row_key].student_name} ({undo.reason}).")
            self._update_score_summary()
        return "break"

    def _on_tree_delete(self, _event: tk.Event | None = None) -> str:
        selected_items = list(self.preview_tree.selection())
        # IMP-C7: Đếm số dòng có điểm chờ sẽ bị xóa
        rows_with_pending = []
        for item in selected_items:
            row_key = next((key for key, tree_item in self._tree_item_by_key.items() if tree_item == item), "")
            row = self._score_rows_by_key.get(row_key)
            if row is not None and row.pending_score:
                rows_with_pending.append((row_key, row))
        if not rows_with_pending:
            return "break"
        # IMP-C7: Xác nhận trước khi xóa nếu có ≥1 dòng
        if len(rows_with_pending) == 1:
            confirm_msg = f"Xóa điểm chờ ({rows_with_pending[0][1].pending_score}) của {rows_with_pending[0][1].student_name}?"
        else:
            confirm_msg = f"Xóa điểm chờ của {len(rows_with_pending)} học sinh?"
        if not messagebox.askyesno("Xác nhận xóa", confirm_msg, parent=self.root):
            return "break"
        for row_key, row in rows_with_pending:
            self._apply_row_patch(
                row_key,
                pending_score="",
                status=RowStatus.READY,
                recognized_text=row.recognized_text,
                match_score=0,
                reason="xóa bằng phím Delete",
            )
        self._update_score_summary()
        return "break"

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item = self.preview_tree.identify_row(event.y)
        column = self.preview_tree.identify_column(event.x)
        if not item or column != "#4":
            self._stop_tree_editor(commit=False)
            return
        row_key = next((key for key, tree_item in self._tree_item_by_key.items() if tree_item == item), "")
        if row_key:
            self._start_tree_editor(row_key, item, column)

    def _start_tree_editor(self, row_key: str, item: str, column: str) -> None:
        self._stop_tree_editor(commit=False)
        bbox = self.preview_tree.bbox(item, column)
        if not bbox:
            return
        x, y, width, height = bbox
        row = self._score_rows_by_key[row_key]
        editor = ttk.Entry(self.preview_tree)
        editor.insert(0, row.pending_score)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.select_range(0, tk.END)
        editor.bind("<Return>", lambda _e: self._stop_tree_editor(commit=True))
        editor.bind("<Escape>", lambda _e: self._stop_tree_editor(commit=False))
        editor.bind("<FocusOut>", lambda _e: self._stop_tree_editor(commit=True))
        # IMP-B3: Tab nhảy sang row kế, Shift+Tab quay lại row trước
        editor.bind("<Tab>", lambda _e: self._tree_editor_navigate(row_key, direction=1))
        editor.bind("<Shift-Tab>", lambda _e: self._tree_editor_navigate(row_key, direction=-1))
        self._tree_editor = editor
        self._tree_editor_info = {"row_key": row_key}

    def _tree_editor_navigate(self, current_row_key: str, *, direction: int) -> str:
        """IMP-B3: Tab/Shift+Tab navigate giữa các row trong tree editor.

        Commit giá trị hiện tại, rồi mở editor ở row kế (direction=1)
        hoặc row trước (direction=-1) trên cột pending_score (#4).
        """
        if not self._stop_tree_editor(commit=True):
            return "break"
        all_keys = list(self._score_rows_by_key.keys())
        if current_row_key not in all_keys:
            return "break"
        current_idx = all_keys.index(current_row_key)
        next_idx = current_idx + direction
        if next_idx < 0 or next_idx >= len(all_keys):
            return "break"
        next_key = all_keys[next_idx]
        next_item = self._tree_item_by_key.get(next_key)
        if next_item:
            self.preview_tree.see(next_item)
            self.preview_tree.selection_set(next_item)
            self._start_tree_editor(next_key, next_item, "#4")
        return "break"

    def _stop_tree_editor(self, commit: bool) -> bool:
        if self._tree_editor is None:
            return True
        editor = self._tree_editor
        row_key = self._tree_editor_info.get("row_key", "")
        value = editor.get().strip()
        row = self._score_rows_by_key.get(row_key)
        normalized_pending = ""
        should_clear_pending = False
        if commit and row is not None:
            if value:
                score_value = parse_manual_score_text(value)
                if score_value is None:
                    messagebox.showwarning("Điểm không hợp lệ", "Hãy nhập điểm từ 0 đến 10.")
                    try:
                        editor.focus_set()
                        editor.select_range(0, tk.END)
                    except tk.TclError:
                        pass
                    return False
                normalized_pending = _format_score_value(score_value)
            else:
                should_clear_pending = True
        try:
            editor.destroy()
        except tk.TclError:
            pass
        self._tree_editor = None
        self._tree_editor_info = {}
        if not commit or not row_key or row_key not in self._score_rows_by_key:
            return True
        row = self._score_rows_by_key[row_key]
        if not should_clear_pending:
            self._apply_row_patch(
                row_key,
                pending_score=normalized_pending,
                status=RowStatus.PENDING,
                recognized_text=row.recognized_text,
                match_score=row.match_score,
                reason="sửa tay trong Treeview",
            )
        else:
            self._apply_row_patch(
                row_key,
                pending_score="",
                status=RowStatus.READY,
                recognized_text=row.recognized_text,
                match_score=0,
                reason="xóa tay trong Treeview",
            )
        pending_count = sum(1 for preview_row in self._score_rows_by_key.values() if preview_row.pending_score)
        self.voice_summary_var.set(f"Đã quét {len(self._score_rows_by_key)} học sinh. Còn {pending_count} dòng chờ ghi.")
        return True

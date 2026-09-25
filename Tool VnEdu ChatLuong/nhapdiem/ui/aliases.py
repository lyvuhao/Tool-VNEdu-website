"""Biệt danh (alias) cho học sinh."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox

from ..config import ALIAS_FILE
from ..models import LogTag
from ..storage import _load_json_object_file, _write_json_atomic_file
from ..voice.phonetics import (
    _khmer_likely_aliases,
    _looks_like_khmer_name,
    _normalize_diacritic_text,
    _vietnamese_likely_aliases,
)


class AliasMixin:
    """Biệt danh (alias) cho học sinh."""

    # ---- Biệt danh (Alias) cho học sinh ----

    def _load_aliases(self) -> None:
        """Tải biệt danh học sinh từ file JSON."""
        if not ALIAS_FILE.exists():
            return
        data, backup_path, error = _load_json_object_file(ALIAS_FILE)
        if error is not None:
            self._log(f"Không tải được biệt danh: {error}")
            if backup_path is not None:
                self._log(f"Đã chuyển file biệt danh lỗi sang {backup_path}")
            return
        self._student_aliases = {
            str(k): [str(v) for v in vs] if isinstance(vs, list) else [str(vs)]
            for k, vs in data.items()
            if k and vs
        }
        self._log(f"Đã tải {sum(len(v) for v in self._student_aliases.values())} biệt danh học sinh.")

    def _save_aliases(self) -> None:
        """Lưu biệt danh học sinh ra file JSON theo atomic write pattern (IMP-A2)."""
        try:
            clean = {k: v for k, v in self._student_aliases.items() if v}
            _write_json_atomic_file(ALIAS_FILE, clean)
        except Exception as error:  # noqa: BLE001
            self._log(f"Không lưu được biệt danh: {error}")

    def _get_aliases_for_row(self, row_key: str) -> list[str]:
        """Lấy danh sách biệt danh của một học sinh theo row_key."""
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return []
        return list(self._student_aliases.get(row.student_name, []))

    def _on_tree_right_click(self, event: tk.Event) -> None:
        """Hiển thị context menu khi nhấn chuột phải vào treeview."""
        item = self.preview_tree.identify_row(event.y)
        if not item:
            return
        self.preview_tree.selection_set(item)
        self.preview_tree.focus(item)

        # Tìm row_key từ tree item
        row_key = None
        for key, tree_item in self._tree_item_by_key.items():
            if tree_item == item:
                row_key = key
                break
        if row_key is None:
            return
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return

        aliases = self._student_aliases.get(row.student_name, [])
        khmer_suggestions = (
            _khmer_likely_aliases(row.student_name) if _looks_like_khmer_name(row.student_name) else []
        )
        # Lọc gợi ý Khmer ra khỏi alias đã có (so qua normalize để tránh sai diacritics).
        existing_alias_keys = {_normalize_diacritic_text(a) for a in aliases}
        new_khmer_suggestions = [
            suggestion
            for suggestion in khmer_suggestions
            if _normalize_diacritic_text(suggestion) not in existing_alias_keys
        ]
        menu = tk.Menu(self.root, tearoff=0, font=("Segoe UI", 10))
        menu.add_command(
            label=f"📝 Đặt biệt danh cho \"{row.student_name}\"",
            command=lambda rk=row_key: self._show_alias_dialog(rk),
        )
        if new_khmer_suggestions:
            sample = ", ".join(new_khmer_suggestions[:3]) + (
                "..." if len(new_khmer_suggestions) > 3 else ""
            )
            menu.add_command(
                label=f"🌏 Gợi ý alias Khmer ({len(new_khmer_suggestions)}: {sample})",
                command=lambda rk=row_key, names=new_khmer_suggestions: self._apply_khmer_aliases(rk, names),
            )
        # Quick action: sinh alias Khmer cho TẤT CẢ row có họ Khmer trong roster.
        roster_khmer_count = sum(
            1
            for candidate in self._score_rows_by_key.values()
            if _looks_like_khmer_name(candidate.student_name)
        )
        if roster_khmer_count >= 2:
            menu.add_command(
                label=f"🌏 Auto sinh alias Khmer cho cả lớp ({roster_khmer_count} HS)",
                command=self._apply_khmer_aliases_for_all,
            )
        # Vietnamese phonetic aliases (Nhựt/Nhật, Quý/Quí, ...)
        vietnamese_suggestions = _vietnamese_likely_aliases(row.student_name)
        new_vn_suggestions = [
            suggestion
            for suggestion in vietnamese_suggestions
            if _normalize_diacritic_text(suggestion) not in existing_alias_keys
        ]
        if new_vn_suggestions:
            vn_sample = ", ".join(new_vn_suggestions[:3]) + (
                "..." if len(new_vn_suggestions) > 3 else ""
            )
            menu.add_command(
                label=f"🇻🇳 Gợi ý alias Việt ({len(new_vn_suggestions)}: {vn_sample})",
                command=lambda rk=row_key, names=new_vn_suggestions: self._apply_khmer_aliases(rk, names),
            )
        roster_vn_alias_count = sum(
            1
            for candidate in self._score_rows_by_key.values()
            if _vietnamese_likely_aliases(candidate.student_name)
        )
        if roster_vn_alias_count >= 2:
            menu.add_command(
                label=f"🇻🇳 Auto sinh alias Việt cho cả lớp ({roster_vn_alias_count} HS)",
                command=self._apply_vietnamese_aliases_for_all,
            )
        if aliases:
            alias_text = ", ".join(aliases)
            menu.add_command(
                label=f"🏷️ Biệt danh hiện tại: {alias_text}",
                state=tk.DISABLED,
            )
            menu.add_command(
                label="🗑️ Xóa tất cả biệt danh",
                command=lambda rk=row_key: self._remove_all_aliases(rk),
            )
        menu.post(event.x_root, event.y_root)
        # BUG-05 FIX: Destroy menu widget khi đóng để tránh memory leak
        menu.bind("<Unmap>", lambda _e: menu.after(50, menu.destroy))

    def _show_alias_dialog(self, row_key: str) -> None:
        """
        Hiển thị hộp thoại đặt biệt danh cho học sinh.

        Args:
            row_key: Key của hàng học sinh trong _score_rows_by_key.
        """
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return
        existing_aliases = self._student_aliases.get(row.student_name, [])

        dlg = tk.Toplevel(self.root)
        dlg.title("Đặt biệt danh")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg="#f0f4f8")

        body = tk.Frame(dlg, bg="#f0f4f8", padx=24, pady=18)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            body,
            text="🏷️  ĐẶT BIỆT DANH CHO HỌC SINH",
            font=("Segoe UI", 12, "bold"),
            bg="#f0f4f8",
            fg="#1a365d",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        # Wrapper frame tạo viền đen 1px đều 4 cạnh (workaround Tkinter relief bug trên Windows)
        info_border = tk.Frame(body, bg="#2d3748", bd=0, highlightthickness=0)
        info_border.pack(fill=tk.X, pady=(0, 12))
        info_frame = tk.Frame(info_border, bg="#e2e8f0", bd=0, highlightthickness=0, padx=12, pady=8)
        info_frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(
            info_frame,
            text=f"Họ tên:  {row.student_name}",
            font=("Segoe UI", 10, "bold"),
            bg="#e2e8f0",
            fg="#2d3748",
            anchor="w",
        ).pack(fill=tk.X)
        if existing_aliases:
            tk.Label(
                info_frame,
                text=f"Biệt danh hiện tại:  {', '.join(existing_aliases)}",
                font=("Segoe UI", 9),
                bg="#e2e8f0",
                fg="#4a5568",
                anchor="w",
            ).pack(fill=tk.X, pady=(4, 0))

        tk.Label(
            body,
            text="Nhập biệt danh mới (VD: Đức, em Đức, Minh Đức...):",
            font=("Segoe UI", 10),
            bg="#f0f4f8",
            fg="#4a5568",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))

        alias_var = tk.StringVar()
        alias_entry = tk.Entry(
            body,
            textvariable=alias_var,
            font=("Segoe UI", 11),
            relief=tk.SOLID,
            borderwidth=1,
        )
        alias_entry.pack(fill=tk.X, pady=(0, 4))
        alias_entry.focus_set()

        hint_label = tk.Label(
            body,
            text="💡 Mỗi biệt danh cách nhau bằng dấu phẩy. VD: Đức, em Đức",
            font=("Segoe UI", 9),
            bg="#f0f4f8",
            fg="#718096",
            anchor="w",
        )
        hint_label.pack(fill=tk.X, pady=(0, 12))

        error_var = tk.StringVar()
        error_label = tk.Label(
            body,
            textvariable=error_var,
            font=("Segoe UI", 9),
            bg="#f0f4f8",
            fg="#e53e3e",
            anchor="w",
        )
        error_label.pack(fill=tk.X, pady=(0, 8))

        btn_frame = tk.Frame(body, bg="#f0f4f8")
        btn_frame.pack(fill=tk.X)

        def on_save() -> None:
            raw = alias_var.get().strip()
            if not raw:
                error_var.set("⚠️ Vui lòng nhập ít nhất 1 biệt danh.")
                return
            new_aliases = [a.strip() for a in raw.split(",") if a.strip()]
            if not new_aliases:
                error_var.set("⚠️ Biệt danh không hợp lệ.")
                return
            # Kiểm tra biệt danh chỉ gồm số
            for alias in new_aliases:
                if re.fullmatch(r"\d+(?:[.,]\d+)?", alias):
                    error_var.set(f"⚠️ Biệt danh '{alias}' không được chỉ là số (sẽ nhầm với điểm).")
                    return
            duplicate_error = ""
            with self._score_data_lock:
                # Kiểm tra trùng với biệt danh học sinh khác
                for other_name, other_aliases in self._student_aliases.items():
                    if other_name == row.student_name:
                        continue
                    for alias in new_aliases:
                        normalized_alias = _normalize_diacritic_text(alias)
                        for existing in other_aliases:
                            if _normalize_diacritic_text(existing) == normalized_alias:
                                duplicate_error = f"⚠️ Biệt danh '{alias}' đã được dùng cho '{other_name}'."
                                break
                        if duplicate_error:
                            break
                    if duplicate_error:
                        break
                if not duplicate_error:
                    # Gộp vào danh sách hiện tại (không trùng)
                    current = list(self._student_aliases.get(row.student_name, []))
                    for alias in new_aliases:
                        normalized_new = _normalize_diacritic_text(alias)
                        already_exists = False
                        for existing in current:
                            if _normalize_diacritic_text(existing) == normalized_new:
                                already_exists = True
                                break
                        if not already_exists:
                            current.append(alias)
                    self._student_aliases[row.student_name] = current
                    self._rebuild_student_indices()
            if duplicate_error:
                error_var.set(duplicate_error)
                return
            self._save_aliases()
            if self._ptt_enabled:
                self._build_voice_hints()
            dlg.destroy()
            self._log(f"Đã đặt biệt danh cho {row.student_name}: {', '.join(current)}", tag=LogTag.SUCCESS)

        def on_cancel() -> None:
            dlg.destroy()

        tk.Button(
            btn_frame,
            text="✅ Lưu biệt danh",
            command=on_save,
            bg="#2fa34a",
            fg="#ffffff",
            activebackground="#23803a",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=16,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_frame,
            text="❌ Hủy",
            command=on_cancel,
            bg="#95a5a6",
            fg="#ffffff",
            activebackground="#7f8c8d",
            activeforeground="#ffffff",
            font=("Segoe UI", 10),
            relief=tk.SOLID,
            borderwidth=1,
            padx=16,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        alias_entry.bind("<Return>", lambda _e: on_save())
        alias_entry.bind("<Escape>", lambda _e: on_cancel())

        # Căn giữa dialog
        dlg.update_idletasks()
        dlg_w = dlg.winfo_width()
        dlg_h = dlg.winfo_height()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        pos_x = root_x + (root_w - dlg_w) // 2
        pos_y = root_y + (root_h - dlg_h) // 2
        dlg.geometry(f"+{pos_x}+{pos_y}")

    def _remove_all_aliases(self, row_key: str) -> None:
        """Xóa tất cả biệt danh của một học sinh."""
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return
        if row.student_name in self._student_aliases:
            with self._score_data_lock:
                removed = self._student_aliases.pop(row.student_name, [])
                self._rebuild_student_indices()
            self._save_aliases()
            if self._ptt_enabled:
                self._build_voice_hints()
            self._log(f"Đã xóa biệt danh của {row.student_name}: {', '.join(removed)}", tag=LogTag.WARNING)

    def _apply_khmer_aliases(self, row_key: str, suggested_aliases: list[str]) -> None:
        """KHMER #A: Áp dụng list alias gợi ý cho 1 học sinh Khmer."""
        row = self._score_rows_by_key.get(row_key)
        if row is None or not suggested_aliases:
            return
        added: list[str] = []
        with self._score_data_lock:
            current = list(self._student_aliases.get(row.student_name, []))
            existing_keys = {_normalize_diacritic_text(a) for a in current}
            # Cũng tránh đụng alias của học sinh khác (alias unique toàn lớp).
            other_keys: dict[str, str] = {}
            for other_name, other_aliases in self._student_aliases.items():
                if other_name == row.student_name:
                    continue
                for alias in other_aliases:
                    other_keys[_normalize_diacritic_text(alias)] = other_name
                other_keys[_normalize_diacritic_text(other_name)] = other_name
            for suggestion in suggested_aliases:
                key = _normalize_diacritic_text(suggestion)
                if not key or key in existing_keys:
                    continue
                if key in other_keys:
                    self._log(
                        f"Bỏ qua alias \"{suggestion}\" cho {row.student_name} vì trùng với HS khác: {other_keys[key]}",
                        tag=LogTag.WARNING,
                    )
                    continue
                current.append(suggestion)
                existing_keys.add(key)
                added.append(suggestion)
            if added:
                self._student_aliases[row.student_name] = current
                self._rebuild_student_indices()
        if added:
            self._save_aliases()
            if self._ptt_enabled:
                self._build_voice_hints()
            self._log(
                f"KHMER: thêm alias cho {row.student_name}: {', '.join(added)}",
                tag=LogTag.SUCCESS,
            )

    def _apply_khmer_aliases_for_all(self) -> None:
        """KHMER #A: Quét toàn bộ roster, sinh và apply alias Khmer cho HS phù hợp."""
        applied_total = 0
        skipped_already = 0
        with self._score_data_lock:
            khmer_rows = [
                row
                for row in self._score_rows_by_key.values()
                if _looks_like_khmer_name(row.student_name)
            ]
        if not khmer_rows:
            self._log("Không có HS nào có họ Khmer trong roster hiện tại.", tag=LogTag.INFO)
            return
        for row in khmer_rows:
            suggestions = _khmer_likely_aliases(row.student_name)
            if not suggestions:
                continue
            existing = self._student_aliases.get(row.student_name, [])
            existing_keys = {_normalize_diacritic_text(a) for a in existing}
            new_suggestions = [
                s for s in suggestions if _normalize_diacritic_text(s) not in existing_keys
            ]
            if not new_suggestions:
                skipped_already += 1
                continue
            self._apply_khmer_aliases(row.row_key, new_suggestions)
            applied_total += 1
        summary = (
            f"KHMER auto-alias: cập nhật {applied_total}/{len(khmer_rows)} HS"
            f"{f', bỏ qua {skipped_already} HS đã có đủ alias' if skipped_already else ''}."
        )
        self._log(summary, tag=LogTag.SUCCESS)
        try:
            messagebox.showinfo("Auto sinh alias Khmer", summary, parent=self.root)
        except tk.TclError:
            pass

    def _apply_vietnamese_aliases_for_all(self) -> None:
        """VIETNAMESE #A: Quét toàn bộ roster, sinh và apply alias Việt cho HS có cặp âm dễ lẫn (Nhựt/Nhật, Quý/Quí, ...)."""
        applied_total = 0
        skipped_already = 0
        with self._score_data_lock:
            vn_rows = [
                row
                for row in self._score_rows_by_key.values()
                if _vietnamese_likely_aliases(row.student_name)
            ]
        if not vn_rows:
            self._log("Không có HS nào có cặp âm Việt dễ lẫn trong roster hiện tại.", tag=LogTag.INFO)
            return
        for row in vn_rows:
            suggestions = _vietnamese_likely_aliases(row.student_name)
            if not suggestions:
                continue
            existing = self._student_aliases.get(row.student_name, [])
            existing_keys = {_normalize_diacritic_text(a) for a in existing}
            new_suggestions = [
                s for s in suggestions if _normalize_diacritic_text(s) not in existing_keys
            ]
            if not new_suggestions:
                skipped_already += 1
                continue
            # Tận dụng helper Khmer (đã có guard duplicate cross-row).
            self._apply_khmer_aliases(row.row_key, new_suggestions)
            applied_total += 1
        summary = (
            f"VIETNAMESE auto-alias: cập nhật {applied_total}/{len(vn_rows)} HS"
            f"{f', bỏ qua {skipped_already} HS đã có đủ alias' if skipped_already else ''}."
        )
        self._log(summary, tag=LogTag.SUCCESS)
        try:
            messagebox.showinfo("Auto sinh alias Việt", summary, parent=self.root)
        except tk.TclError:
            pass

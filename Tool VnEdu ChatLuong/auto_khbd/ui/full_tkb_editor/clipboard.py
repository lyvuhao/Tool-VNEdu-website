"""Phím tắt và thao tác copy / paste / xoá."""

from __future__ import annotations

from tkinter import messagebox

from ...engine.profile.models import ProfileSchemaError, SlotEntry


class ClipboardMixin:
    """Phím tắt và thao tác copy / paste / xoá."""

    # -----------------------------------------------------------
    # Keyboard shortcuts (Ctrl+C / Ctrl+V / Delete)
    # -----------------------------------------------------------

    def _bind_keyboard_shortcuts(self):
        """Bind Ctrl+C / Ctrl+V / Delete ở Toplevel với funcid để unbind clean.

        Handler check `focus_get()` — nếu focus là Entry/Combobox/Text thì
        skip để không hijack typing trong dialog con (vd SlotPickerDialog).
        """
        for seq, handler in (
            ("<Control-c>", self._on_kb_copy),
            ("<Control-C>", self._on_kb_copy),
            ("<Control-v>", self._on_kb_paste),
            ("<Control-V>", self._on_kb_paste),
            ("<Delete>", self._on_kb_delete),
        ):
            try:
                fid = self.bind(seq, handler, add="+")
                self._kb_bound_funcids.append((seq, fid))
            except Exception:
                pass

    def _focus_is_in_grid(self) -> bool:
        """Check xem focus có phải đang ở 1 cell button của grid này.

        Nếu focus là Entry/Combobox/Text → trả False để KB shortcut không
        hijack typing.
        """
        try:
            w = self.focus_get()
        except Exception:
            return False
        if w is None:
            return False
        # Skip text-entry widgets
        cls_name = w.winfo_class()
        if cls_name in (
            "Entry", "TEntry", "Combobox", "TCombobox",
            "Text", "Spinbox", "TSpinbox", "Listbox",
        ):
            return False
        # Walk up to check is descendant of self
        cur = w
        while cur is not None:
            if cur is self:
                return True
            try:
                cur = cur.master
            except Exception:
                return False
        return False

    def _on_kb_copy(self, _event=None):
        if not self._focus_is_in_grid():
            return None
        if self._selected_panel_idx < 0 or self._selected_key is None:
            return None
        self._action_copy()
        return "break"

    def _on_kb_paste(self, _event=None):
        if not self._focus_is_in_grid():
            return None
        if self._selected_panel_idx < 0 or self._selected_key is None:
            return None
        self._action_paste()
        return "break"

    def _on_kb_delete(self, _event=None):
        if not self._focus_is_in_grid():
            return None
        if self._selected_panel_idx < 0 or self._selected_key is None:
            return None
        self._action_delete()
        return "break"

    # -----------------------------------------------------------
    # Action primitives
    # -----------------------------------------------------------

    def _action_copy(self):
        """Copy ô selected vào wizard._slot_clipboard."""
        if self._selected_panel_idx < 0 or self._selected_key is None:
            return
        slot = self._slot_at_panel(
            self._selected_panel_idx, self._selected_key,
        )
        if slot is None:
            self._set_status("Ô đang chọn rỗng — không có gì để copy.")
            return
        # Share clipboard với wizard chính (cùng SlotEntry instance).
        self.wizard._slot_clipboard = slot
        # KHÔNG mark wizard dirty — copy không sửa state.
        self._set_status(
            f"📋 Đã copy: {slot.lop_text}/{slot.mon_text} "
            f"({self._human_pos_from_key(self._selected_key)})"
        )
        self.wizard._log(
            f"[Phóng to] Đã copy: {slot.lop_text}/{slot.mon_text}", "info",
        )

    def _action_paste(self):
        """Paste clipboard vào ô selected."""
        if self._selected_panel_idx < 0 or self._selected_key is None:
            return
        clip = self.wizard._slot_clipboard
        if clip is None:
            self._set_status("Clipboard trống — chưa có gì để dán.")
            return
        target_pi = self._selected_panel_idx
        target_tag = self._panels[target_pi]["tag"]
        target_key = self._selected_key
        try:
            t, b, ti = (int(x) for x in target_key.split("_"))
        except (ValueError, TypeError):
            return
        tpl = self._template_for_tag(target_tag)
        if tpl is None:
            return
        existing = tpl.slot_at(t, b, ti)
        if existing is not None:
            if not messagebox.askyesno(
                "Ô đã có dữ liệu",
                (
                    f"Ô {self._human_pos(t, b, ti)} đang là "
                    f"{existing.lop_text}/{existing.mon_text}.\n\n"
                    "Ghi đè bằng dữ liệu trong clipboard?"
                ),
                parent=self,
            ):
                return
        try:
            new_slot = SlotEntry(
                thu=t, buoi=b, tiet=ti,
                lop_id=clip.lop_id, lop_text=clip.lop_text,
                mon_id=clip.mon_id, mon_text=clip.mon_text,
                phan_mon_id=clip.phan_mon_id,
                phan_mon_text=clip.phan_mon_text,
            )
        except ProfileSchemaError as e:
            messagebox.showerror(
                "Dữ liệu không hợp lệ",
                f"Không paste được:\n{e.field}: {e.msg}",
                parent=self,
            )
            return
        tpl.upsert_slot(new_slot)
        self._sync_wizard_after_change()
        self._refresh_all_panels()
        self._set_status(
            f"📥 Đã dán {new_slot.lop_text}/{new_slot.mon_text} "
            f"vào {self._human_pos(t, b, ti)}"
        )
        self.wizard._log(
            f"[Phóng to/{target_tag}] Đã dán "
            f"{new_slot.lop_text}/{new_slot.mon_text} → "
            f"{self._human_pos(t, b, ti)}", "ok",
        )

    def _action_delete(self):
        """Xoá ô selected."""
        if self._selected_panel_idx < 0 or self._selected_key is None:
            return
        target_pi = self._selected_panel_idx
        target_tag = self._panels[target_pi]["tag"]
        target_key = self._selected_key
        try:
            t, b, ti = (int(x) for x in target_key.split("_"))
        except (ValueError, TypeError):
            return
        tpl = self._template_for_tag(target_tag)
        if tpl is None:
            return
        if not tpl.slot_at(t, b, ti):
            self._set_status("Ô đang chọn đã trống.")
            return
        tpl.delete_slot(t, b, ti)
        self._sync_wizard_after_change()
        self._refresh_all_panels()
        self._set_status(f"🗑 Đã xóa ô {self._human_pos(t, b, ti)}")
        self.wizard._log(
            f"[Phóng to/{target_tag}] Đã xóa tiết "
            f"{self._human_pos(t, b, ti)}", "warn",
        )

    def _copy_cell(
        self,
        src_pi: int, src_tag: str, src_key: str,
        tgt_pi: int, tgt_tag: str, tgt_key: str,
    ):
        """Drag-and-drop helper: copy data từ source → target.

        Hỗ trợ cross-template: ô lẻ → ô chẵn → write vào template_chan.
        """
        src_tpl = self._template_for_tag(src_tag)
        tgt_tpl = self._template_for_tag(tgt_tag)
        if src_tpl is None or tgt_tpl is None:
            return
        try:
            t_s, b_s, ti_s = (int(x) for x in src_key.split("_"))
            t_t, b_t, ti_t = (int(x) for x in tgt_key.split("_"))
        except (ValueError, TypeError):
            return
        source_slot = src_tpl.slot_at(t_s, b_s, ti_s)
        if source_slot is None:
            return  # Source rỗng (race)
        existing = tgt_tpl.slot_at(t_t, b_t, ti_t)
        if existing is not None:
            cross_note = ""
            if src_tag != tgt_tag:
                cross_note = (
                    f"\n\nLưu ý: copy GIỮA 2 mẫu khác nhau "
                    f"({self._tag_label(src_tag)} → {self._tag_label(tgt_tag)})."
                )
            if not messagebox.askyesno(
                "Ô đích đã có dữ liệu",
                (
                    f"Ô đích {self._tag_label(tgt_tag)} "
                    f"{self._human_pos(t_t, b_t, ti_t)} đang là "
                    f"{existing.lop_text}/{existing.mon_text}.\n\n"
                    f"Ghi đè bằng "
                    f"{source_slot.lop_text}/{source_slot.mon_text}?"
                    + cross_note
                ),
                parent=self,
            ):
                return
        try:
            new_slot = SlotEntry(
                thu=t_t, buoi=b_t, tiet=ti_t,
                lop_id=source_slot.lop_id, lop_text=source_slot.lop_text,
                mon_id=source_slot.mon_id, mon_text=source_slot.mon_text,
                phan_mon_id=source_slot.phan_mon_id,
                phan_mon_text=source_slot.phan_mon_text,
            )
        except ProfileSchemaError as e:
            messagebox.showerror(
                "Dữ liệu không hợp lệ",
                f"Không copy được:\n{e.field}: {e.msg}",
                parent=self,
            )
            return
        tgt_tpl.upsert_slot(new_slot)
        self._sync_wizard_after_change()
        # Set selection mới ở target để user dễ tiếp tục
        self._set_selection(tgt_pi, tgt_key)
        self._refresh_all_panels()
        cross_msg = (
            f" ({self._tag_label(src_tag)} → {self._tag_label(tgt_tag)})"
            if src_tag != tgt_tag else ""
        )
        self._set_status(
            f"📤 Đã copy {source_slot.lop_text}/{source_slot.mon_text} "
            f"→ {self._human_pos(t_t, b_t, ti_t)}{cross_msg}"
        )
        self.wizard._log(
            f"[Phóng to{cross_msg}] Đã copy "
            f"{self._human_pos(t_s, b_s, ti_s)} → "
            f"{self._human_pos(t_t, b_t, ti_t)}: "
            f"{source_slot.lop_text}/{source_slot.mon_text}", "ok",
        )

    def _sync_wizard_after_change(self):
        """Gọi sau mọi thay đổi template — sync với wizard chính."""
        try:
            self.wizard._refresh_grid()
            self.wizard._refresh_ppct_table()
            self.wizard._mark_dirty()
        except Exception:
            pass

    @staticmethod
    def _human_pos(thu: int, buoi: int, tiet: int) -> str:
        thu_lbl = "CN" if thu == 8 else f"Thứ {thu}"
        buoi_lbl = "Sáng" if buoi == 1 else "Chiều"
        return f"{thu_lbl} {buoi_lbl} tiết {tiet}"

    @classmethod
    def _human_pos_from_key(cls, key: str) -> str:
        try:
            t, b, ti = (int(x) for x in key.split("_"))
            return cls._human_pos(t, b, ti)
        except (ValueError, TypeError):
            return key

    @staticmethod
    def _tag_label(tag: str) -> str:
        return {
            "le": "TKB lẻ",
            "chan": "TKB chẵn",
            "chinh": "TKB chính",
        }.get(tag, tag)

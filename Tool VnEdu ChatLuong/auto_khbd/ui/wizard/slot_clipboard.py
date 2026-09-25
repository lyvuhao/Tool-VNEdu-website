"""Copy / paste / xoá tiết."""

from __future__ import annotations

from tkinter import messagebox

from ...engine.profile.models import ProfileSchemaError, SlotEntry


class SlotClipboardMixin:
    """Copy / paste / xoá tiết."""

    # -----------------------------------------------------------
    # Slot operations: copy / paste / delete
    # -----------------------------------------------------------

    def _copy_selected_slot(self):
        """Ctrl+C: copy slot đang select vào _slot_clipboard."""
        if not self._selected_slot_key:
            return
        try:
            t, b, ti = (int(x) for x in self._selected_slot_key.split("_"))
        except (ValueError, TypeError):
            return
        slot = self._slot_at(t, b, ti)
        if slot is None:
            return  # Ô rỗng — không có gì để copy
        # Lưu deep-copy để paste không share reference
        self._slot_clipboard = SlotEntry(
            thu=slot.thu, buoi=slot.buoi, tiet=slot.tiet,
            lop_id=slot.lop_id, lop_text=slot.lop_text,
            mon_id=slot.mon_id, mon_text=slot.mon_text,
            phan_mon_id=slot.phan_mon_id, phan_mon_text=slot.phan_mon_text,
        )
        self._log(
            f"📋 Đã sao chép tiết: {slot.lop_text} / {slot.mon_text}"
            f" / {slot.phan_mon_text}",
            "info",
        )

    def _paste_clipboard_to(self, target_key: str):
        """Ctrl+V hoặc menu Paste: paste clipboard vào ô có key này."""
        if not self._can_edit_slot():
            return
        if self._slot_clipboard is None:
            return
        try:
            t, b, ti = (int(x) for x in target_key.split("_"))
        except (ValueError, TypeError):
            return

        tpl = self._active_template()
        if tpl is None:
            return

        existing = tpl.slot_at(t, b, ti)
        if existing is not None:
            # Confirm overwrite
            if not messagebox.askyesno(
                "Ô đã có dữ liệu",
                (
                    f"Ô Thứ {t}-{('Sáng' if b == 1 else 'Chiều')}-tiết {ti} "
                    f"đang là {existing.lop_text} / {existing.mon_text}.\n\n"
                    "Ghi đè bằng dữ liệu trong clipboard?"
                ),
                parent=self,
            ):
                return

        # Tạo SlotEntry mới với vị trí target, các field còn lại từ clipboard
        clip = self._slot_clipboard
        try:
            new_slot = SlotEntry(
                thu=t, buoi=b, tiet=ti,
                lop_id=clip.lop_id, lop_text=clip.lop_text,
                mon_id=clip.mon_id, mon_text=clip.mon_text,
                phan_mon_id=clip.phan_mon_id, phan_mon_text=clip.phan_mon_text,
            )
        except ProfileSchemaError as e:
            messagebox.showerror(
                "Dữ liệu không hợp lệ",
                f"Không paste được vào ô này:\n{e.field}: {e.msg}",
                parent=self,
            )
            return

        tpl.upsert_slot(new_slot)
        self._refresh_grid()
        self._refresh_ppct_table()
        self._mark_dirty()
        self._log(
            f"📥 Đã dán tiết vào Thứ {t}-{('Sáng' if b == 1 else 'Chiều')}-{ti}: "
            f"{new_slot.lop_text} / {new_slot.mon_text}",
            "ok",
        )

    def _copy_slot_to(self, source_key: str, target_key: str):
        """Drag-and-drop helper: copy slot trực tiếp source → target.

        Khác `_paste_clipboard_to`: KHÔNG cần clipboard trung gian, dùng cho
        drag-and-drop mỗi click. Vẫn confirm nếu target có data.
        """
        if not self._can_edit_slot():
            return
        try:
            t_src, b_src, ti_src = (int(x) for x in source_key.split("_"))
            t_tgt, b_tgt, ti_tgt = (int(x) for x in target_key.split("_"))
        except (ValueError, TypeError):
            return

        tpl = self._active_template()
        if tpl is None:
            return

        source_slot = tpl.slot_at(t_src, b_src, ti_src)
        if source_slot is None:
            return  # Source rỗng (đã check trước khi vào drag, nhưng phòng race)

        existing = tpl.slot_at(t_tgt, b_tgt, ti_tgt)
        if existing is not None:
            if not messagebox.askyesno(
                "Ô đích đã có dữ liệu",
                (
                    f"Ô Thứ {t_tgt}-{('Sáng' if b_tgt == 1 else 'Chiều')}-"
                    f"tiết {ti_tgt} đang là "
                    f"{existing.lop_text} / {existing.mon_text}.\n\n"
                    f"Ghi đè bằng {source_slot.lop_text} / {source_slot.mon_text}?"
                ),
                parent=self,
            ):
                return

        try:
            new_slot = SlotEntry(
                thu=t_tgt, buoi=b_tgt, tiet=ti_tgt,
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

        tpl.upsert_slot(new_slot)
        self._refresh_grid()
        self._refresh_ppct_table()
        self._mark_dirty()
        # Selection mới ở target để user dễ tiếp tục thao tác
        self._set_selected_slot(target_key)
        self._log(
            f"📤 Đã copy tiết Thứ {t_src}-{('Sáng' if b_src == 1 else 'Chiều')}-"
            f"{ti_src} → Thứ {t_tgt}-{('Sáng' if b_tgt == 1 else 'Chiều')}-{ti_tgt}: "
            f"{source_slot.lop_text} / {source_slot.mon_text}",
            "ok",
        )

    def _delete_slot_at(self, thu: int, buoi: int, tiet: int,
                        ask_confirm: bool = False):
        """Xóa slot. Right-click menu pass `ask_confirm=False` (đã chọn rõ);
        Delete keyboard cũng pass False (UX nhanh, có log để rollback)."""
        if not self._can_edit_slot():
            return
        tpl = self._active_template()
        if tpl is None:
            return
        slot = tpl.slot_at(thu, buoi, tiet)
        if slot is None:
            return  # Ô rỗng — no-op

        if ask_confirm:
            if not messagebox.askyesno(
                "Xóa tiết",
                (
                    f"Xóa tiết Thứ {thu}-{('Sáng' if buoi == 1 else 'Chiều')}-{tiet}: "
                    f"{slot.lop_text} / {slot.mon_text}?"
                ),
                parent=self,
            ):
                return

        tpl.delete_slot(thu, buoi, tiet)
        self._refresh_grid()
        self._refresh_ppct_table()
        self._mark_dirty()
        self._log(
            f"🗑 Đã xóa tiết Thứ {thu}-{('Sáng' if buoi == 1 else 'Chiều')}-{tiet}: "
            f"{slot.lop_text} / {slot.mon_text}",
            "warn",
        )

    def _on_keyboard_copy(self, _event=None):
        """Ctrl+C handler — chỉ active khi widget focus là 1 slot button."""
        if not self._is_focus_in_grid():
            return None
        self._copy_selected_slot()
        return "break"

    def _on_keyboard_paste(self, _event=None):
        """Ctrl+V handler — chỉ active khi focus trong grid."""
        if not self._is_focus_in_grid():
            return None
        if not self._selected_slot_key:
            return None
        self._paste_clipboard_to(self._selected_slot_key)
        return "break"

    def _on_keyboard_delete(self, _event=None):
        """Delete handler — chỉ active khi focus trong grid."""
        if not self._is_focus_in_grid():
            return None
        if not self._selected_slot_key:
            return None
        try:
            t, b, ti = (int(x) for x in self._selected_slot_key.split("_"))
        except (ValueError, TypeError):
            return None
        self._delete_slot_at(t, b, ti, ask_confirm=False)
        return "break"

    def _is_focus_in_grid(self) -> bool:
        """True nếu widget đang focus là 1 slot button."""
        try:
            f = self.focus_get()
        except Exception:
            return False
        if f is None:
            return False
        for btn in self._slot_buttons.values():
            if btn is f:
                return True
        return False

    def _cancel_drag_if_stuck(self):
        """Reset `_drag_state` an toàn — restore visual của target highlight.

        Gọi từ:
        - `_on_window_focus_out` (alt-tab khi đang drag)
        - `_on_esc` (user nhấn Esc)
        - `_refresh_grid` (đã có inline)
        """
        if self._drag_state is None:
            return
        prev_target = self._drag_state.get("last_target_key")
        source_key = self._drag_state.get("source_key")
        self._drag_state = None
        # Restore visual cho target được highlight (nếu khác source)
        if prev_target and prev_target != source_key:
            if prev_target in self._slot_buttons:
                self._restyle_slot_button(prev_target)
        # Reset cursor cho source button (nếu vẫn còn)
        if source_key and source_key in self._slot_buttons:
            try:
                self._slot_buttons[source_key].configure(cursor="hand2")
            except Exception:
                pass

    def _on_window_focus_out(self, event):
        """Window mất focus (alt-tab, click app khác).

        Tk không đảm bảo fire `<ButtonRelease-1>` cho widget của app này khi
        user release chuột ngoài window → drag state có thể stuck. Reset
        ngay để đảm bảo state nhất quán.

        Lưu ý: `<FocusOut>` cũng fire khi focus đi giữa các widget trong
        cùng window — nhưng `event.widget` trong những trường hợp đó là
        widget rời focus, không phải toplevel. Filter để chỉ trigger khi
        toplevel mất focus thật.
        """
        try:
            if event.widget is self.winfo_toplevel():
                self._cancel_drag_if_stuck()
        except Exception:
            pass

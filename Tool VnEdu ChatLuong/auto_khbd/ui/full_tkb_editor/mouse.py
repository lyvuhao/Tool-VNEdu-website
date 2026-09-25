"""Sự kiện chuột trên ô TKB."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING

from ...engine.analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_NGHI
from ..dialogs.slot_picker import SlotPickerDialog
from ..theme import SLOT_DRAG_THRESHOLD_PX

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.profile.models import SlotEntry


class MouseEventsMixin:
    """Sự kiện chuột trên ô TKB."""

    # -----------------------------------------------------------
    # Event handlers — mouse
    # -----------------------------------------------------------

    def _on_cell_press(
        self, event, panel_idx: int, tag: str,
        thu: int, buoi: int, tiet: int,
    ):
        key = f"{thu}_{buoi}_{tiet}"
        try:
            event.widget.focus_set()
        except Exception:
            pass
        self._set_selection(panel_idx, key)

        # Check có data → chuẩn bị drag
        slot = self._slot_at_panel(panel_idx, key)
        if slot is not None:
            self._drag_state = {
                "source_panel_idx": panel_idx,
                "source_key": key,
                "source_tag": tag,
                "start_x": event.x_root,
                "start_y": event.y_root,
                "started": False,
                "last_target": None,
            }
        else:
            self._drag_state = None

    def _on_cell_drag_motion(self, event):
        ds = self._drag_state
        if ds is None:
            return
        dx = abs(event.x_root - ds["start_x"])
        dy = abs(event.y_root - ds["start_y"])
        if not ds["started"]:
            if dx < SLOT_DRAG_THRESHOLD_PX and dy < SLOT_DRAG_THRESHOLD_PX:
                return
            ds["started"] = True
            # Set cursor trên CELL FRAME (parent) thay vì event.widget
            # (có thể là Label child) — đảm bảo cursor "plus" hiển thị
            # nhất quán trên toàn bộ cell khi drag.
            source_info = self._find_panel_button(event.widget)
            if source_info is not None:
                pi, k = source_info
                cell_dict = self._panels[pi]["buttons"].get(k)
                if cell_dict:
                    try:
                        cell_dict["cell"].configure(cursor="plus")
                    except Exception:
                        pass

        try:
            widget_under = self.winfo_containing(
                event.x_root, event.y_root,
            )
        except Exception:
            widget_under = None
        target = self._find_panel_button(widget_under)

        prev_target = ds.get("last_target")
        if prev_target != target:
            # Restore prev target
            if prev_target is not None:
                ds["last_target"] = None
                self._restyle_button(*prev_target)
            # Set new target
            ds["last_target"] = target
            if target is not None:
                source = (ds["source_panel_idx"], ds["source_key"])
                if target != source:
                    self._restyle_button(*target)

    def _on_cell_release(self, event):
        ds = self._drag_state
        self._drag_state = None
        if ds is None or not ds.get("started"):
            return  # Click thường — release không có ý nghĩa drag
        # Restore cursor trên CELL FRAME (source) thay vì event.widget
        source_info = (ds.get("source_panel_idx"), ds.get("source_key"))
        if source_info[0] is not None and source_info[1] is not None:
            try:
                pi, k = source_info
                cell_dict = self._panels[pi]["buttons"].get(k)
                if cell_dict:
                    cell_dict["cell"].configure(cursor="hand2")
            except Exception:
                pass
        # Restore target visual
        target = ds.get("last_target")
        try:
            widget_under = self.winfo_containing(
                event.x_root, event.y_root,
            )
        except Exception:
            widget_under = None
        target_real = self._find_panel_button(widget_under)
        # Restore visual của last hover target
        if target is not None:
            self._restyle_button(*target)

        source = (ds["source_panel_idx"], ds["source_key"])
        if target_real is None or target_real == source:
            return  # Drop ngoài / lên chính source → no-op
        target_pi, target_key = target_real
        target_tag = self._panels[target_pi]["tag"]
        self._copy_cell(
            ds["source_panel_idx"], ds["source_tag"], ds["source_key"],
            target_pi, target_tag, target_key,
        )

    # -----------------------------------------------------------
    # Event handlers — actions
    # -----------------------------------------------------------

    def _on_cell_double_clicked(
        self, panel_idx: int, tag: str,
        thu: int, buoi: int, tiet: int,
    ):
        if not self.wizard._can_edit_slot():
            return
        tpl = self._template_for_tag(tag)
        if tpl is None:
            messagebox.showwarning(
                "Không tìm thấy template",
                f"Không xác định được TKB cho tab '{tag}'.",
                parent=self,
            )
            return
        current = tpl.slot_at(thu, buoi, tiet)

        def on_save(slot: "SlotEntry"):
            tpl.upsert_slot(slot)
            self._sync_wizard_after_change()
            self._refresh_all_panels()
            self._set_status(
                f"✓ Đã đặt {slot.lop_text}/{slot.mon_text} "
                f"vào ô {self._human_pos(thu, buoi, tiet)}"
            )
            self.wizard._log(
                f"[Phóng to/{tag}] Đã đặt tiết "
                f"{self._human_pos(thu, buoi, tiet)}: "
                f"{slot.lop_text} {slot.mon_text}", "ok",
            )

        def on_delete():
            tpl.delete_slot(thu, buoi, tiet)
            self._sync_wizard_after_change()
            self._refresh_all_panels()
            self._set_status(
                f"✓ Đã xóa ô {self._human_pos(thu, buoi, tiet)}"
            )
            self.wizard._log(
                f"[Phóng to/{tag}] Đã xóa tiết "
                f"{self._human_pos(thu, buoi, tiet)}", "warn",
            )

        SlotPickerDialog(
            self,
            thu=thu, buoi=buoi, tiet=tiet,
            current=current,
            bootstrap=self.wizard.bootstrap_data,
            on_save=on_save,
            on_delete=on_delete if current else None,
        )

    def _on_cell_right_click(
        self, event, panel_idx: int, tag: str,
        thu: int, buoi: int, tiet: int,
    ):
        if not self.wizard._can_edit_slot():
            return
        # Set selection trước khi mở menu
        key = f"{thu}_{buoi}_{tiet}"
        self._set_selection(panel_idx, key)
        # Cancel any drag
        self._drag_state = None

        slot = self._slot_at_panel(panel_idx, key)
        clip = self.wizard._slot_clipboard

        # Build single instance menu — destroy cũ rồi tạo mới để tránh stale
        # state khi rebuild.
        if self._context_menu is not None:
            try:
                self._context_menu.destroy()
            except Exception:
                pass
        m = tk.Menu(self, tearoff=False)
        self._context_menu = m
        # Lưu target để các handler menu biết đúng ô
        self._context_menu_target = (panel_idx, key)

        # Sửa / Thêm
        m.add_command(
            label=("✎ Sửa tiết…" if slot else "➕ Thêm tiết…"),
            command=lambda: self._on_cell_double_clicked(
                panel_idx, tag, thu, buoi, tiet,
            ),
        )
        m.add_separator()
        m.add_command(
            label="📋 Sao chép (Ctrl+C)",
            state=("normal" if slot else "disabled"),
            command=self._action_copy,
        )
        m.add_command(
            label=(
                "📥 Dán (Ctrl+V) — "
                + (
                    f"{clip.lop_text}/{clip.mon_text}"
                    if clip else "(clipboard trống)"
                )
            ),
            state=("normal" if clip else "disabled"),
            command=self._action_paste,
        )
        m.add_separator()
        m.add_command(
            label="Đánh dấu tiết Nghỉ...",
            state=("normal" if slot else "disabled"),
            command=lambda: self._open_schedule_event_dialog(
                panel_idx, key, TKB_EVENT_NGHI,
            ),
        )
        m.add_command(
            label="Thêm tiết Dạy bù...",
            state=("normal" if slot else "disabled"),
            command=lambda: self._open_schedule_event_dialog(
                panel_idx, key, TKB_EVENT_DAY_BU,
            ),
        )
        m.add_separator()
        m.add_command(
            label="🗑 Xóa tiết (Delete)",
            state=("normal" if slot else "disabled"),
            command=self._action_delete,
        )
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _on_cell_alt_click(
        self, event, panel_idx: int, tag: str,
        thu: int, buoi: int, tiet: int,
    ):
        """Alt-click mở nhanh lựa chọn Nghỉ/Dạy bù cho ô đang có tiết."""
        if not self.wizard._can_edit_slot():
            return "break"
        key = f"{thu}_{buoi}_{tiet}"
        self._set_selection(panel_idx, key)
        slot = self._slot_at_panel(panel_idx, key)
        m = tk.Menu(self, tearoff=False)
        m.add_command(
            label="Đánh dấu tiết Nghỉ...",
            state=("normal" if slot else "disabled"),
            command=lambda: self._open_schedule_event_dialog(
                panel_idx, key, TKB_EVENT_NGHI,
            ),
        )
        m.add_command(
            label="Thêm tiết Dạy bù...",
            state=("normal" if slot else "disabled"),
            command=lambda: self._open_schedule_event_dialog(
                panel_idx, key, TKB_EVENT_DAY_BU,
            ),
        )
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass
        return "break"

    def _open_schedule_event_dialog(
        self,
        panel_idx: int,
        key: str,
        default_kind: str,
    ):
        slot = self._slot_at_panel(panel_idx, key)
        self.wizard._open_schedule_event_dialog_from_slot(
            slot,
            default_kind,
            parent=self,
        )
        self._refresh_all_panels()

"""Tương tác lưới TKB: click, kéo thả, menu."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ...engine.analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_NGHI
from ...engine.profile.models import SlotEntry
from ..dialogs.slot_picker import SlotPickerDialog
from ..theme import (
    CLR_GRID_LINE,
    CLR_SLOT_DRAG_TARGET_BG,
    CLR_SLOT_EMPTY,
    CLR_SLOT_EMPTY_HOVER,
    CLR_SLOT_FILLED,
    CLR_SLOT_FILLED_BORDER,
    CLR_SLOT_FILLED_HOVER,
    CLR_SLOT_FILLED_TEXT,
    CLR_SLOT_HOVER_BORDER,
    CLR_SLOT_SELECTED_BG,
    CLR_SLOT_SELECTED_BORDER,
    SLOT_DRAG_THRESHOLD_PX,
)


class SlotGridMixin:
    """Tương tác lưới TKB: click, kéo thả, menu."""

    # -----------------------------------------------------------
    # Slot grid interaction — single click / double click / drag / menu
    # -----------------------------------------------------------

    @staticmethod
    def _slot_key(thu: int, buoi: int, tiet: int) -> str:
        """Compose key cho _slot_buttons + _selected_slot_key."""
        return f"{thu}_{buoi}_{tiet}"

    def _slot_at(self, thu: int, buoi: int, tiet: int) -> SlotEntry | None:
        """Lookup slot ở template active. Trả None nếu chưa kết nối profile."""
        tpl = self._active_template()
        if tpl is None:
            return None
        return tpl.slot_at(thu, buoi, tiet)

    def _can_edit_slot(self, show_msg: bool = True) -> bool:
        """Pre-check chung trước mọi thao tác sửa slot."""
        if self._block_if_executor_running("sửa lịch dạy"):
            return False
        if not self.bootstrap_data:
            if show_msg:
                messagebox.showinfo(
                    "Chưa đăng nhập VnEdu",
                    "Bạn cần bấm [Đăng nhập VnEdu] trước để tool lấy danh "
                    "sách lớp/môn/phân môn từ web.",
                    parent=self,
                )
            return False
        if self._active_template() is None:
            if show_msg:
                messagebox.showerror(
                    "Lỗi",
                    "Không xác định được mẫu TKB hiện tại.",
                    parent=self,
                )
            return False
        return True

    def _set_selected_slot(self, key: str | None):
        """Đổi ô đang select. Re-style ô cũ + ô mới."""
        # Restore ô cũ về trạng thái filled/empty bình thường
        old = self._selected_slot_key
        self._selected_slot_key = key
        for k in (old, key):
            if not k:
                continue
            self._restyle_slot_button(k)

    def _restyle_slot_button(self, key: str):
        """Áp lại style cho 1 button dựa vào: có data / đang select."""
        btn = self._slot_buttons.get(key)
        if btn is None:
            return
        try:
            t, b, ti = (int(x) for x in key.split("_"))
        except (ValueError, TypeError):
            return
        slot = self._slot_at(t, b, ti)
        is_selected = (key == self._selected_slot_key)
        if slot:
            mon_short = slot.mon_text
            if len(mon_short) > 18:
                mon_short = mon_short[:16] + "…"
            btn.configure(
                text=f"{slot.lop_text}\n{mon_short}",
                background=(
                    CLR_SLOT_SELECTED_BG if is_selected else CLR_SLOT_FILLED
                ),
                activebackground="#bcdaf2",
                foreground=CLR_SLOT_FILLED_TEXT,
                font=("Segoe UI", 10, "bold"),
                highlightthickness=2 if is_selected else 1,
                highlightbackground=(
                    CLR_SLOT_SELECTED_BORDER if is_selected else CLR_SLOT_FILLED_BORDER
                ),
            )
        else:
            btn.configure(
                text="+\nThêm",
                background=(
                    CLR_SLOT_SELECTED_BG if is_selected else CLR_SLOT_EMPTY
                ),
                activebackground="#eef2ff",
                foreground="#9ca3af",
                font=("Segoe UI", 11),
                highlightthickness=2 if is_selected else 1,
                highlightbackground=(
                    CLR_SLOT_SELECTED_BORDER if is_selected else CLR_GRID_LINE
                ),
            )

    def _on_slot_hover_enter(self, thu: int, buoi: int, tiet: int):
        """Làm nổi ô dưới chuột mà không phá trạng thái select/drag."""
        key = self._slot_key(thu, buoi, tiet)
        btn = self._slot_buttons.get(key)
        if btn is None:
            return
        ds = self._drag_state
        if ds is not None and ds.get("started"):
            return

        slot = self._slot_at(thu, buoi, tiet)
        is_selected = (key == self._selected_slot_key)
        btn.configure(
            background=CLR_SLOT_FILLED_HOVER if slot else CLR_SLOT_EMPTY_HOVER,
            activebackground=CLR_SLOT_FILLED_HOVER if slot else CLR_SLOT_EMPTY_HOVER,
            highlightthickness=2,
            highlightbackground=(
                CLR_SLOT_SELECTED_BORDER if is_selected else CLR_SLOT_HOVER_BORDER
            ),
        )

    def _on_slot_hover_leave(self, thu: int, buoi: int, tiet: int):
        """Trả ô về đúng style hiện tại khi chuột rời khỏi ô."""
        ds = self._drag_state
        if ds is not None and ds.get("started"):
            return
        self._restyle_slot_button(self._slot_key(thu, buoi, tiet))

    def _on_slot_press(self, event, thu: int, buoi: int, tiet: int):
        """Mouse pressed trên slot button.

        - Set selection ngay
        - Khởi tạo `_drag_state` nếu ô có data (chuẩn bị có thể drag)
        """
        key = self._slot_key(thu, buoi, tiet)
        # Cho focus vào widget này để global keyboard binds biết nên active
        try:
            event.widget.focus_set()
        except Exception:
            pass
        self._set_selected_slot(key)

        # Nếu ô có data → chuẩn bị drag (ghi nhận start coords)
        slot = self._slot_at(thu, buoi, tiet)
        if slot is not None:
            self._drag_state = {
                "source_key": key,
                "start_x": event.x_root,
                "start_y": event.y_root,
                "started": False,    # bật True khi vượt threshold
                "last_target_key": None,
            }
        else:
            self._drag_state = None

    def _on_slot_drag_motion(self, event, thu: int, buoi: int, tiet: int):
        """Mouse di chuyển khi giữ button-1.

        - Nếu chưa vượt threshold: KHÔNG làm gì (cho double-click có cơ hội)
        - Vượt threshold: set `started=True`, đổi cursor, highlight ô đích
        """
        ds = self._drag_state
        if ds is None:
            return
        dx = abs(event.x_root - ds["start_x"])
        dy = abs(event.y_root - ds["start_y"])
        if not ds["started"]:
            if dx < SLOT_DRAG_THRESHOLD_PX and dy < SLOT_DRAG_THRESHOLD_PX:
                return
            ds["started"] = True
            try:
                event.widget.configure(cursor="plus")
            except Exception:
                pass

        # Tìm widget dưới chuột — có thể là 1 button khác
        try:
            widget_under = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget_under = None

        target_key = self._key_for_widget(widget_under)
        prev_target = ds.get("last_target_key")
        if prev_target != target_key:
            # Restore prev target (nếu khác source)
            if prev_target and prev_target != ds["source_key"]:
                self._restyle_slot_button(prev_target)
            # Highlight new target (nếu hợp lệ và khác source)
            if target_key and target_key != ds["source_key"]:
                tbtn = self._slot_buttons.get(target_key)
                if tbtn is not None:
                    try:
                        tbtn.configure(background=CLR_SLOT_DRAG_TARGET_BG)
                    except Exception:
                        pass
            ds["last_target_key"] = target_key

    def _key_for_widget(self, widget) -> str | None:
        """Reverse lookup _slot_buttons → key. Trả None nếu widget không phải slot."""
        if widget is None:
            return None
        for k, btn in self._slot_buttons.items():
            if btn is widget:
                return k
        return None

    def _on_slot_release(self, event, thu: int, buoi: int, tiet: int):
        """Mouse release. Nếu drag đã bắt đầu → thực hiện copy."""
        ds = self._drag_state
        self._drag_state = None
        if ds is None or not ds.get("started"):
            return  # Không drag, không làm gì (double-click sẽ tự fire sau)

        try:
            event.widget.configure(cursor="hand2")
        except Exception:
            pass

        # Restore visual của target cũ (nếu có)
        prev_target = ds.get("last_target_key")
        if prev_target and prev_target != ds["source_key"]:
            self._restyle_slot_button(prev_target)

        # Tìm target khi release
        try:
            widget_under = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget_under = None
        target_key = self._key_for_widget(widget_under)
        source_key = ds["source_key"]
        if not target_key or target_key == source_key:
            return  # Drop ngoài grid hoặc lên chính ô gốc → no-op

        # Copy slot từ source sang target
        self._copy_slot_to(source_key, target_key)

    def _on_slot_double_clicked(self, thu: int, buoi: int, tiet: int):
        """Double-click → mở SlotPickerDialog (hành vi cũ)."""
        if not self._can_edit_slot():
            return
        tpl = self._active_template()
        if tpl is None:
            return

        current = tpl.slot_at(thu, buoi, tiet)

        def on_save(slot: SlotEntry):
            tpl.upsert_slot(slot)
            self._refresh_grid()
            self._refresh_ppct_table()
            self._mark_dirty()
            self._log(
                f"Đã đặt tiết Thứ {thu}-{('Sáng' if buoi == 1 else 'Chiều')}-{tiet}: "
                f"{slot.lop_text} {slot.mon_text}",
                "ok",
            )

        def on_delete():
            tpl.delete_slot(thu, buoi, tiet)
            self._refresh_grid()
            self._refresh_ppct_table()
            self._mark_dirty()
            self._log(
                f"Đã xóa tiết Thứ {thu}-{('Sáng' if buoi == 1 else 'Chiều')}-{tiet}",
                "warn",
            )

        SlotPickerDialog(
            self.winfo_toplevel(),
            thu=thu, buoi=buoi, tiet=tiet,
            current=current,
            bootstrap=self.bootstrap_data,
            on_save=on_save,
            on_delete=on_delete if current else None,
        )

    def _on_slot_right_click(self, event, thu: int, buoi: int, tiet: int):
        """Right-click trên slot → mở context menu.

        Cancel drag nếu đang drag.
        """
        if self._drag_state is not None:
            self._drag_state = None
            try:
                event.widget.configure(cursor="hand2")
            except Exception:
                pass
        # Set selection cho ô được click phải
        self._set_selected_slot(self._slot_key(thu, buoi, tiet))

        # Build menu (single instance, rebuild items mỗi lần để cập nhật state)
        if self._slot_context_menu is None:
            self._slot_context_menu = tk.Menu(self, tearoff=0)
        m = self._slot_context_menu
        m.delete(0, "end")

        slot = self._slot_at(thu, buoi, tiet)
        has_clip = self._slot_clipboard is not None
        m.add_command(
            label="✏ Sửa tiết… (double-click)",
            command=lambda: self._on_slot_double_clicked(thu, buoi, tiet),
        )
        m.add_separator()
        m.add_command(
            label="📋 Sao chép (Ctrl+C)",
            command=self._copy_selected_slot,
            state=("normal" if slot else "disabled"),
        )
        m.add_command(
            label="📥 Dán (Ctrl+V)",
            command=lambda: self._paste_clipboard_to(self._slot_key(thu, buoi, tiet)),
            state=("normal" if has_clip else "disabled"),
        )
        m.add_separator()
        m.add_command(
            label="Đánh dấu tiết Nghỉ...",
            command=lambda: self._open_schedule_event_dialog_from_slot(
                slot, TKB_EVENT_NGHI, parent=self,
            ),
            state=("normal" if slot else "disabled"),
        )
        m.add_command(
            label="Thêm tiết Dạy bù...",
            command=lambda: self._open_schedule_event_dialog_from_slot(
                slot, TKB_EVENT_DAY_BU, parent=self,
            ),
            state=("normal" if slot else "disabled"),
        )
        m.add_separator()
        m.add_command(
            label="🗑 Xóa tiết (Delete)",
            command=lambda: self._delete_slot_at(thu, buoi, tiet, ask_confirm=False),
            state=("normal" if slot else "disabled"),
        )
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _on_slot_alt_click(self, event, thu: int, buoi: int, tiet: int):
        """Alt-click trên grid chính để chọn nhanh Nghỉ/Dạy bù."""
        self._set_selected_slot(self._slot_key(thu, buoi, tiet))
        slot = self._slot_at(thu, buoi, tiet)
        m = tk.Menu(self, tearoff=0)
        m.add_command(
            label="Đánh dấu tiết Nghỉ...",
            command=lambda: self._open_schedule_event_dialog_from_slot(
                slot, TKB_EVENT_NGHI, parent=self,
            ),
            state=("normal" if slot else "disabled"),
        )
        m.add_command(
            label="Thêm tiết Dạy bù...",
            command=lambda: self._open_schedule_event_dialog_from_slot(
                slot, TKB_EVENT_DAY_BU, parent=self,
            ),
            state=("normal" if slot else "disabled"),
        )
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass
        return "break"

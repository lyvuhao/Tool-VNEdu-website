"""Log và làm mới trạng thái giao diện."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox

from ...engine.analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_NGHI
from ...engine.profile.models import SlotEntry, TKBTemplate
from ...engine.profile.profile import count_profile_holiday_rules, profile_schedule_events
from ..dialogs.schedule_events import TKBScheduleEventGridDialog, TKBScheduleEventsManagerDialog


class StateMixin:
    """Log và làm mới trạng thái giao diện."""

    # -----------------------------------------------------------
    # State helpers
    # -----------------------------------------------------------

    def _log(self, msg: str, level: str = "info"):
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] ", "dim")
        self.log_text.insert("end", f"{msg}\n", level)
        self.log_text.see("end")

    def _mark_dirty(self, *_):
        if self._loading_profile:
            return
        was_dirty = self._is_dirty
        self._is_dirty = True
        # Cập nhật dấu `*` trong nhãn file ngay khi vừa chuyển từ clean → dirty.
        # Tránh refresh mỗi lần keystroke (đã dirty từ trước → không cần đổi).
        if not was_dirty:
            try:
                self._refresh_profile_label()
            except Exception:
                pass

    def _active_template(self) -> TKBTemplate | None:
        """Trả template đang hiển thị trong grid (chinh / le / chan)."""
        if not self.profile:
            return None
        if not self.profile.tach_le_chan:
            return self.profile.template_chinh
        # Tách: dùng var_active_tab
        if self.var_active_tab.get() == "le":
            return self.profile.template_le
        return self.profile.template_chan

    def _refresh_grid(self):
        """Update text/màu của các slot button dựa trên template hiện tại.

        Selection state (`_selected_slot_key`) được preserve nếu key vẫn tồn
        tại trong _slot_buttons. Drag state luôn bị reset (không carry-over).
        """
        # Reset drag state qua helper — đảm bảo restore visual của target.
        self._cancel_drag_if_stuck()
        # Validate selection vẫn tồn tại
        if (
            self._selected_slot_key is not None
            and self._selected_slot_key not in self._slot_buttons
        ):
            self._selected_slot_key = None
        # Apply style cho từng button qua helper (đã handle selection)
        for key in list(self._slot_buttons.keys()):
            self._restyle_slot_button(key)
        # Sync cửa sổ phóng to nếu đang mở — đảm bảo grid lẻ/chẵn cạnh nhau
        # phản ánh đúng template sau mọi thay đổi từ wizard chính (load
        # profile, import TKB, copy/paste/delete slot...).
        try:
            full_win = getattr(self, "_full_tkb_window", None)
            if full_win is not None and full_win.winfo_exists():
                full_win._refresh_all_panels()
        except Exception:
            pass

    def _refresh_ppct_table(self):
        """Refresh bảng PPCT từ profile.ppct_starts.

        v2: Cột "PPCT sắp nhập" được tính theo priority:
          1. Live scan từ executor (`_executor._last_scan_progress`) — real-time
             khi đang chạy, đã include cả tiết bù.
          2. YearScanReport (sau khi user click "Tự phát hiện PPCT từ web")
             — đã static, không live nhưng đúng tại thời điểm scan.
          3. profile.ppct_starts — config tay (fallback).
        """
        if not hasattr(self, "tree_ppct"):
            return
        if not self.profile:
            return
        # Sync với template hiện tại — wrap try/except để UI không crash
        # khi profile bị hỏng schema (ví dụ load file cũ thiếu trường).
        try:
            self.profile.sync_ppct_starts_with_templates()
        except Exception as e:
            try:
                self._log(
                    f"⚠ Lỗi sync PPCT: {type(e).__name__}: {e}",
                    "err",
                )
            except Exception:
                pass
            return

        # v2: Build live scan map nếu executor đang chạy/đã chạy gần đây.
        # _last_scan_progress[tuan] = {group_key: last_ppct_int}
        # Lấy max last_ppct theo group across all scanned weeks → ppct_next = max + 1.
        live_next: dict[str, int] = {}
        executor = getattr(self, "_executor", None)
        if executor is not None and hasattr(executor, "_last_scan_progress"):
            scan_map = executor._last_scan_progress or {}
            for tuan, group_progress in scan_map.items():
                for group_key, last_ppct in group_progress.items():
                    if last_ppct > 0 and last_ppct + 1 > live_next.get(group_key, 0):
                        live_next[group_key] = last_ppct + 1

        # v2: Build static scan map từ YearScanReport (nếu user đã detect)
        report_next: dict[str, int] = {}
        rpt = getattr(self, "_year_report", None) or getattr(self, "scan_report", None)
        if rpt is not None and getattr(rpt, "subject_progress", None):
            for group_key, sp in rpt.subject_progress.items():
                if sp.last_ppct > 0:
                    report_next[group_key] = sp.last_ppct + 1

        for child in self.tree_ppct.get_children():
            self.tree_ppct.delete(child)

        if not self.profile.ppct_starts:
            # Placeholder row khi chưa có nhóm nào
            self.tree_ppct.insert(
                "", "end", iid="__empty__",
                values=("(chưa có)", "Hãy soạn ít nhất 1 tiết",
                       "ở lưới phía trên", "—", "—"),
                tags=("empty",),
            )
            self.tree_ppct.tag_configure(
                "empty", foreground="#888", font=("Segoe UI", 10, "italic"),
            )
            return

        for entry in self.profile.ppct_starts:
            iid = entry.group_key
            # Priority: live > report > config
            next_val = (
                live_next.get(entry.group_key)
                or report_next.get(entry.group_key)
                or entry.ppct_start
            )
            # Tag đặc biệt nếu next_val đến từ live scan (highlight cho user)
            tags = ()
            if entry.group_key in live_next:
                tags = ("live_next",)
            self.tree_ppct.insert(
                "", "end", iid=iid,
                values=(entry.lop_text, entry.mon_text,
                       entry.phan_mon_text, entry.ppct_start, next_val),
                tags=tags,
            )
        # Style cho row có live update
        self.tree_ppct.tag_configure(
            "live_next", foreground="#0c5cad", font=("Segoe UI", 9, "bold"),
        )

    def _refresh_tuan_hint(self):
        if not self.profile or not self.profile.tach_le_chan:
            self._tuan_hint.configure(
                text="Công cụ sẽ áp dụng cùng 1 lịch dạy cho mọi tuần trong khoảng.",
            )
        else:
            self._tuan_hint.configure(
                text="Tuần lẻ (1, 3, 5…) dùng TKB lẻ • Tuần chẵn (2, 4, 6…) dùng TKB chẵn",
            )

    def _refresh_holiday_summary(self):
        if not hasattr(self, "var_holiday_summary"):
            return
        if not self.profile:
            self.var_holiday_summary.set("Chưa có hồ sơ TKB.")
            return
        try:
            tuan_from = int(self.var_tuan_from.get())
            tuan_to = int(self.var_tuan_to.get())
        except (tk.TclError, ValueError):
            tuan_from, tuan_to = self.profile.tuan_from, self.profile.tuan_to
        n_nghi, n_bu = count_profile_holiday_rules(
            self.profile, tuan_from, tuan_to
        )
        total_rules = sum(
            1 for e in profile_schedule_events(self.profile)
            if getattr(e, "enabled", True)
        )
        if total_rules <= 0:
            self.var_holiday_summary.set(
                "Chưa có Nghỉ/Dạy bù. Chuột phải hoặc giữ Alt trên ô TKB đã có môn để thêm nhanh."
            )
        else:
            self.var_holiday_summary.set(
                f"Đã có {total_rules} dòng. Trong dải tuần đang chọn: "
                f"{n_nghi} Nghỉ, {n_bu} Dạy bù. Bấm để xem/xóa/tắt dòng."
            )

    def _on_holiday_rules_clicked(self):
        """Mở màn hình quản lý Nghỉ/Dạy bù theo event mới."""
        if self._block_if_executor_running("sửa lịch Nghỉ/Dạy bù"):
            return
        if not self.profile:
            messagebox.showwarning(
                "Chưa có hồ sơ",
                "Hãy tạo hoặc mở hồ sơ TKB trước khi khai báo Nghỉ/Dạy bù.",
                parent=self,
            )
            return
        dlg = TKBScheduleEventsManagerDialog(self)
        self.wait_window(dlg)
        if getattr(dlg, "result", None) is None:
            return
        self.profile.schedule_events = dlg.result
        self.profile.holiday_rules = []
        self._refresh_holiday_summary()
        self._refresh_grid()
        self._mark_dirty()
        self._log(
            f"Đã cập nhật {len(self.profile.schedule_events)} dòng Nghỉ/Dạy bù.",
            "ok",
        )

    def _open_schedule_event_dialog_from_slot(
        self,
        source_slot: SlotEntry | None,
        default_kind: str,
        *,
        parent=None,
    ):
        """Mở bảng chọn nhiều tuần/ngày/tiết cho một môn nguồn."""
        if self._block_if_executor_running("sửa lịch Nghỉ/Dạy bù"):
            return
        if not self.profile:
            messagebox.showwarning(
                "Chưa có hồ sơ",
                "Hãy tạo hoặc mở hồ sơ TKB trước khi khai báo Nghỉ/Dạy bù.",
                parent=parent or self,
            )
            return
        if source_slot is None:
            messagebox.showinfo(
                "Ô rỗng",
                "Hãy chọn một ô đã có lớp/môn/phân môn rồi mới đánh dấu Nghỉ hoặc Dạy bù.",
                parent=parent or self,
            )
            return
        dlg = TKBScheduleEventGridDialog(
            parent or self,
            self,
            source_slot,
            default_kind,
        )
        self.wait_window(dlg)
        events = getattr(dlg, "result", None)
        if not events:
            return
        current = profile_schedule_events(self.profile)
        self.profile.schedule_events = current + [e.clone() for e in events]
        self._refresh_holiday_summary()
        self._refresh_grid()
        self._mark_dirty()
        n_nghi = sum(1 for e in events if e.kind == TKB_EVENT_NGHI)
        n_bu = sum(1 for e in events if e.kind == TKB_EVENT_DAY_BU)
        self._log(
            f"Đã thêm {n_nghi} tiết Nghỉ và {n_bu} tiết Dạy bù từ lưới TKB.",
            "ok",
        )

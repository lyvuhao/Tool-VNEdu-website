"""Kiểm tra form lịch, preflight KHDH và tóm tắt kết quả."""

import re
import tkinter as tk
from tkinter import messagebox, ttk

from ..cdp.bridge import ChromeBridge
from ..compat import winsound
from ..config import SCHEDULE_MODE_KHDH, SCHEDULE_MODE_MANUAL


class ScheduleFormStateMixin:
    """Kiểm tra form lịch, preflight KHDH và tóm tắt kết quả."""

    def _reset_sched_form_state(self, clear_cache=True):
        """Xóa toàn bộ state quét form để tránh dùng option stale giữa các session."""
        self._sched_phan_mon_options = []
        self._sched_phan_mon_by_mon_hoc = {}
        self._sched_mon_hoc_options = []
        self._sched_mon_hoc_field = None
        self._sched_form_scan_session_id = None
        self._sched_form_scan_context_key = None
        if clear_cache:
            self._clear_sched_form_options_cache()
        self.var_sched_mon_hoc.set("")
        self.var_sched_phan_mon.set("")
        if hasattr(self, "cmb_sched_mon_hoc"):
            self.cmb_sched_mon_hoc["values"] = ()
            self.cmb_sched_mon_hoc.set("")
        if hasattr(self, "cmb_sched_phan_mon"):
            self.cmb_sched_phan_mon["values"] = ()
            self.cmb_sched_phan_mon.set("")

    def _mark_sched_form_session_changed(self, clear_cache=True, reason="", announce=False, log_level="warning"):
        """Đổi session-id cho form scan mỗi khi CDP context thay đổi."""
        self._sched_form_session_id += 1
        self._reset_sched_form_state(clear_cache=clear_cache)
        self._set_sched_form_rescan_reason(
            reason,
            announce=announce,
            log_level=log_level,
        )

    def _mark_sched_form_scan_ready(self, selection_info=None):
        """Đánh dấu dữ liệu form hiện tại đã được quét thành công trong session CDP này."""
        self._sched_form_scan_session_id = self._sched_form_session_id
        self._sched_form_scan_context_key = self._make_sched_form_options_cache_key(selection_info)
        self._sched_form_rescan_reason = ""
        self._sched_form_last_announced_issue = ""

    def _current_sched_form_context_lop(self):
        """Đọc lớp đang chọn trên panel schedule để validate dữ liệu form đã quét."""
        try:
            return str(self.var_sched_lop.get() or "").strip().lower()
        except Exception:
            return ""

    def _scanned_sched_form_context_lop(self):
        """Đọc lớp đã gắn với lần Quét Form gần nhất."""
        if isinstance(self._sched_form_scan_context_key, (tuple, list)) and len(self._sched_form_scan_context_key) >= 2:
            return str(self._sched_form_scan_context_key[1] or "").strip().lower()
        return ""

    def _set_sched_form_rescan_reason(self, reason="", announce=False, log_level="warning"):
        """Lưu lý do cần Quét Form lại và đồng bộ lại guidance trên panel."""
        reason = str(reason or "").strip()
        changed = reason != self._sched_form_rescan_reason
        self._sched_form_rescan_reason = reason
        if not reason:
            self._sched_form_last_announced_issue = ""
        if announce and reason and changed:
            self._sched_form_last_announced_issue = reason
            self._log(f"⚠ {reason}", log_level)
        if hasattr(self, "btn_sched_run"):
            self._set_schedule_button_states(
                running=self._schedule_running,
                can_resume=bool(self._schedule_resume_state),
            )
        else:
            self._refresh_sched_form_guidance()

    def _get_sched_form_invalid_reason(self):
        """Tạo thông điệp hành động rõ ràng khi dữ liệu form hiện tại không còn dùng được."""
        if self._sched_form_rescan_reason:
            return self._sched_form_rescan_reason

        current_lop_display = str(self.var_sched_lop.get() or "").strip()
        current_lop = current_lop_display.lower()
        scanned_lop = self._scanned_sched_form_context_lop()

        if not self._cdp_connected:
            return "Chưa kết nối CDP. Hãy kết nối lại rồi bấm [Quét Form] trước khi chạy schedule."
        if not self._sched_mon_hoc_options or not self._sched_phan_mon_by_mon_hoc:
            if current_lop_display:
                return (
                    f"Chưa có dữ liệu Môn học / Phân môn hợp lệ cho lớp '{current_lop_display}'. "
                    "Hãy bấm [Quét Form] trước khi chạy schedule."
                )
            return "Chưa có dữ liệu Môn học / Phân môn hợp lệ. Hãy bấm [Quét Form] trước khi chạy schedule."
        if self._sched_form_scan_session_id != self._sched_form_session_id:
            return "Session CDP đã thay đổi sau lần Quét Form trước. Hãy bấm [Quét Form] lại."
        if current_lop and scanned_lop and current_lop != scanned_lop:
            return (
                f"Bạn đã đổi Lớp từ '{scanned_lop.upper()}' sang '{current_lop_display}'. "
                "Hãy bấm [Quét Form] lại trước khi chạy schedule."
            )
        if current_lop and not scanned_lop:
            return (
                f"Không xác định được lớp của lần Quét Form trước trong khi bạn đang chọn "
                f"'{current_lop_display}'. Hãy bấm [Quét Form] lại."
            )
        return ""

    def _refresh_sched_form_guidance(self, subject_error=""):
        """Hiển thị guidance ngắn ngay trên panel để người dùng biết khi nào phải Quét Form lại."""
        if not hasattr(self, "lbl_sched_progress") or self._schedule_running:
            return

        if self._is_sched_khdh_mode():
            lop_list = self._get_sched_lop_list(SCHEDULE_MODE_KHDH)
            if len(lop_list) <= 1:
                lop_text = lop_list[0] if lop_list else "(chưa chọn lớp)"
            else:
                lop_text = f"{len(lop_list)} lớp: " + ", ".join(lop_list[:4])
                if len(lop_list) > 4:
                    lop_text += f"... (+{len(lop_list) - 4})"
            issue = self._get_sched_mode_invalid_reason(SCHEDULE_MODE_KHDH)
            if issue:
                self.lbl_sched_progress.config(text=f"⚠ {issue}")
                return
            self.lbl_sched_progress.config(
                text=(
                    f"✅ Mode KHDH: Lớp {lop_text}. App sẽ bỏ qua grid tiết + Môn/Phân môn/PPCT nhập tay, "
                    "bật Gợi ý theo KHDH live rồi chỉ nhập HS nghỉ / Nhận xét / Điểm vào các row chữ đỏ."
                )
            )
            return

        issue = self._get_sched_form_invalid_reason()
        if not issue and subject_error:
            issue = subject_error
        if issue:
            compact_issue = " ".join(str(issue).split())
            self.lbl_sched_progress.config(text=f"⚠ {compact_issue}")
            return

        self._sched_form_last_announced_issue = ""

        lop_text = str(self.var_sched_lop.get() or "").strip() or "(chưa chọn lớp)"
        mon_hoc_text = str(self.var_sched_mon_hoc.get() or "").strip() or "(chưa chọn môn học)"
        phan_mon_text = str(self.var_sched_phan_mon.get() or "").strip() or "(chưa chọn phân môn)"
        self.lbl_sched_progress.config(
            text=(
                f"✅ Đã sẵn sàng: Lớp {lop_text} | {mon_hoc_text} / {phan_mon_text}. "
                "Nếu đổi Lớp, đổi màn VnEdu hoặc reconnect CDP, hãy bấm [Quét Form] lại."
            )
        )

    def _has_valid_sched_form_scan(self):
        """Kiểm tra cache form hiện tại còn hợp lệ cho session CDP đang dùng hay không."""
        return not self._get_sched_form_invalid_reason()

    @staticmethod
    def _select_sched_phan_mon_options(mon_hoc_value, phan_mon_by_mon_hoc, phan_mon_options_all):
        """Lấy đúng danh sách Phân môn cho một Môn học; fail-closed nếu map bị thiếu."""
        if mon_hoc_value is None:
            return list(phan_mon_options_all or [])
        key = str(mon_hoc_value).strip()
        if not key:
            return list(phan_mon_options_all or [])
        if key in (phan_mon_by_mon_hoc or {}):
            return list((phan_mon_by_mon_hoc or {}).get(key) or [])
        return []

    @staticmethod
    def _extract_existing_ppct_value(row):
        """Parse số PPCT hiện có trên row để quyết định có consume progression hay không."""
        text = str((row or {}).get("ppct", "") or "").strip()
        match = re.search(r"\d+", text)
        if not match:
            return None
        try:
            return int(match.group())
        except Exception:
            return None

    def _resolve_current_sched_subject_selection(self):
        """Resolve subject/sub-subject IDs từ form scan hiện tại; fail-closed nếu state stale."""
        invalid_reason = self._get_sched_form_invalid_reason()
        if invalid_reason:
            return None, invalid_reason

        mon_hoc_display = self.var_sched_mon_hoc.get()
        mon_hoc_option = self._resolve_sched_option(
            self._sched_mon_hoc_options,
            mon_hoc_display,
        )
        if mon_hoc_option is None:
            return None, (
                "Không resolve được Môn học đã chọn từ cache CDP hiện tại.\n"
                "Có thể bạn vừa đổi lớp hoặc đổi màn VnEdu. Hãy bấm [Quét Form] lại rồi chọn lại Môn học."
            )
        mon_hoc_value = mon_hoc_option["value"]

        phan_mon_display = self.var_sched_phan_mon.get()
        phan_mon_options = self._get_sched_phan_mon_options_for_mon(mon_hoc_value)
        if not phan_mon_options:
            return None, (
                f"Không tải được danh sách Phân môn chuyên biệt cho Môn học "
                f"'{mon_hoc_display}'. Hãy bấm [Quét Form] lại trên đúng lớp và đúng màn VnEdu."
            )

        phan_mon_option = self._resolve_sched_option(
            phan_mon_options,
            phan_mon_display,
        )
        if phan_mon_option is None:
            return None, (
                "Phân môn hiện tại không thuộc Môn học đã chọn trong session CDP này.\n"
                "Hãy chọn lại Phân môn. Nếu bạn vừa đổi lớp, đổi màn VnEdu hoặc reconnect CDP, hãy bấm [Quét Form] lại."
            )

        return {
            "mon_hoc_value": mon_hoc_value,
            "mon_hoc_text": mon_hoc_option.get("text", ""),
            "phan_mon_value": phan_mon_option.get("value"),
            "phan_mon_text": phan_mon_option.get("text", ""),
            "mon_hoc_field": self._sched_mon_hoc_field,
        }, ""

    def _show_sched_form_blocked_warning(self, title, message):
        """Hiển thị thông báo block với hướng dẫn Quét Form rõ ràng và đồng bộ panel."""
        compact_message = " ".join(str(message).split())
        self._log(f"⚠ {compact_message}", "warning")
        self._refresh_sched_form_guidance(subject_error=message)
        messagebox.showwarning(title, message)

    def _preflight_khdh_schedule_context(self, port):
        """Kiểm tra live page có đúng context tối thiểu trước khi chạy/resume KHDH."""
        bridge = None
        try:
            bridge = ChromeBridge(port=port)
            ok, msg = bridge.connect()
            if not ok:
                return False, f"Kết nối CDP thất bại: {msg}"
            ok_probe, probe_msg, _probe = bridge.probe_khdh_schedule_context()
            if not ok_probe:
                return False, probe_msg
            return True, probe_msg
        except Exception as e:
            return False, f"Lỗi preflight KHDH: {type(e).__name__}: {str(e)[:120]}"
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _build_pending_schedule_items(self, params, resume_state):
        """Dựng danh sách slot còn lại khi schedule bị dừng giữa chừng."""
        pending = []
        if not params or not resume_state:
            return pending

        schedule_mode = str(params.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL)
        if schedule_mode == SCHEDULE_MODE_KHDH:
            tuan_to = int(params.get("tuan_to", 0) or 0)
            tuan_from = int(params.get("tuan_from", 1) or 1)
            lop_list = list(params.get("lop_list") or [params.get("lop")])
            next_lop_idx = int(resume_state.get("next_lop_idx", 0) or 0)
            next_tuan_num = resume_state.get("next_tuan_num")
            next_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
            next_row_key = str(resume_state.get("next_row_key", "") or "").strip()
            if next_tuan_num is None or next_lop_idx >= len(lop_list):
                return pending
            for lop_idx in range(next_lop_idx, len(lop_list)):
                lop_text = str(lop_list[lop_idx] or "").strip()
                if not lop_text:
                    continue
                start_week = int(next_tuan_num) if lop_idx == next_lop_idx else tuan_from
                for tuan_num in range(start_week, tuan_to + 1):
                    if lop_idx == next_lop_idx and tuan_num == int(next_tuan_num):
                        message = (
                            f"Lớp {lop_text}: tiếp tục từ row gợi ý KHDH #{next_slot_idx + 1}"
                            if next_slot_idx > 0 else
                            f"Lớp {lop_text}: chưa xử lý xong row gợi ý KHDH của tuần này"
                        )
                        if next_row_key:
                            message += f" | key={next_row_key}"
                    else:
                        message = f"Lớp {lop_text}: chưa quét row gợi ý KHDH của tuần này"
                    pending.append({
                        "week_text": f"Tuần {tuan_num}",
                        "slot_label": "Các row chữ đỏ theo KHDH",
                        "message": message,
                    })
            return pending

        slots = params.get("slots", [])
        tuan_to = int(params.get("tuan_to", 0) or 0)
        next_tuan_num = resume_state.get("next_tuan_num")
        next_slot_idx = resume_state.get("next_slot_idx", 0)
        if not slots or next_tuan_num is None or next_tuan_num > tuan_to:
            return pending

        for tuan_num in range(int(next_tuan_num), tuan_to + 1):
            start_idx = int(next_slot_idx) if tuan_num == int(next_tuan_num) else 0
            for slot_idx in range(start_idx, len(slots)):
                slot = slots[slot_idx]
                pending.append({
                    "week_text": f"Tuần {tuan_num}",
                    "slot_label": self._format_schedule_slot_label(
                        slot.get("thu", "?"),
                        slot.get("buoi", "?"),
                        slot.get("tiet", "?"),
                    ),
                    "message": "Chưa thực hiện xong trong phiên vừa rồi",
                })
        return pending

    def _show_schedule_summary(self, summary):
        """Hiển thị bảng tổng kết chi tiết sau khi schedule hoàn tất hoặc tạm dừng."""
        if self._schedule_summary_window and self._schedule_summary_window.winfo_exists():
            try:
                self._schedule_summary_window.destroy()
            except Exception:
                pass

        window = tk.Toplevel(self.root)
        window.title("Tổng kết Schedule")
        window.transient(self.root)
        window.geometry("820x520")
        window.minsize(700, 420)
        self._schedule_summary_window = window

        is_stopped = bool(summary.get("stopped"))
        completed = int(summary.get("completed", 0) or 0)
        skipped = int(summary.get("skipped", 0) or 0)
        error_count = int(summary.get("errors", 0) or 0)
        stop_reason = summary.get("stop_reason") or "người dùng"
        pending_items = summary.get("pending_items", []) or []
        next_ppct = summary.get("next_ppct")

        header = ttk.Frame(window, padding=10)
        header.pack(fill="x")
        title_text = "⏸ Schedule tạm dừng" if is_stopped else "✅ Schedule hoàn tất"
        title_fg = "#b00020" if (is_stopped or error_count > 0) else "#1f7a1f"
        tk.Label(
            header,
            text=title_text,
            font=("Segoe UI", 12, "bold"),
            fg=title_fg,
            anchor="w",
        ).pack(fill="x")

        summary_lines = [
            f"Thành công: {completed}",
            f"Đã có dữ liệu: {skipped}",
            f"Lỗi: {error_count}",
        ]
        if is_stopped:
            summary_lines.append(f"Dừng bởi: {stop_reason}")
        if next_ppct is not None:
            summary_lines.append(f"PPCT kế tiếp nội bộ: {next_ppct}")

        tk.Label(
            header,
            text=" | ".join(summary_lines),
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(4, 0))

        body = ttk.Frame(window, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)

        text = tk.Text(
            body,
            wrap="word",
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#222222",
            insertbackground="#000000",
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        text.tag_configure("section", font=("Segoe UI", 10, "bold"))
        text.tag_configure("success", foreground="#1f7a1f")
        text.tag_configure("skip", foreground="#666666")
        text.tag_configure("error", foreground="#b00020")
        text.tag_configure("pending", foreground="#b00020")

        results = summary.get("results", []) or []
        if results:
            text.insert("end", "Kết quả đã xử lý\n", "section")
            for item in results:
                week_text = item.get("week_text", "Tuần ?")
                slot_label = item.get("slot_label", "Slot ?")
                ppct = item.get("ppct")
                ppct_text = f" | PPCT {ppct}" if ppct not in (None, "") else ""
                message = item.get("message", "")
                status = item.get("status", "")
                if status == "success":
                    prefix = "[OK]"
                    tag = "success"
                elif status == "skipped_existing":
                    prefix = "[SKIP]"
                    tag = "skip"
                else:
                    prefix = "[LỖI]"
                    tag = "error"
                text.insert(
                    "end",
                    f"{prefix} {week_text} | {slot_label}{ppct_text} | {message}\n",
                    tag,
                )
            text.insert("end", "\n")

        if pending_items:
            text.insert("end", "Chưa hoàn thành / còn lại\n", "section")
            for item in pending_items:
                text.insert(
                    "end",
                    f"[CHƯA XONG] {item.get('week_text', 'Tuần ?')} | "
                    f"{item.get('slot_label', 'Slot ?')} | {item.get('message', '')}\n",
                    "pending",
                )

        text.config(state="disabled")

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="Đóng", command=window.destroy).pack(side="right")
        self._play_completion_notification()
        self._bring_window_to_front(window)

    def _play_completion_notification(self):
        """Phát âm báo ngắn khi schedule hiển thị bảng tổng kết."""
        try:
            if winsound is not None:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.root.bell()
        except Exception:
            try:
                self.root.bell()
            except Exception:
                pass

    def _bring_window_to_front(self, window):
        """Đưa dialog lên trước mặt người dùng mà không giữ app ở trạng thái always-on-top."""
        if not window or not window.winfo_exists():
            return
        try:
            window.deiconify()
        except Exception:
            pass
        try:
            window.lift()
        except Exception:
            pass
        try:
            window.focus_force()
        except Exception:
            pass
        try:
            window.attributes("-topmost", True)
            window.after(350, lambda: window.winfo_exists() and window.attributes("-topmost", False))
        except Exception:
            pass

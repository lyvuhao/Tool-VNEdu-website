"""Tiến độ PPCT của giáo viên."""

import copy
import re
import threading
import tkinter as tk
from tkinter import ttk

from ..cdp.bridge import ChromeBridge


class TeacherProgressMixin:
    """Tiến độ PPCT của giáo viên."""

    def _set_sched_teacher_progress_button(self, text, state="normal"):
        """Đồng bộ text/state của nút mở chi tiết tiến độ PPCT."""
        self.var_sched_teacher_progress_button.set(str(text or "").strip())
        if getattr(self, "btn_sched_teacher_progress", None) is not None:
            try:
                self.btn_sched_teacher_progress.config(state=state)
            except Exception:
                pass

    def _get_sched_teacher_progress_mode_label(self):
        """Trả về nhãn mode quét tiến độ PPCT hiện tại."""
        return "FAST" if bool(self.var_sched_teacher_progress_fast_mode.get()) else "CHUẨN"

    def _on_sched_teacher_progress_mode_changed(self):
        """Khi đổi mode quét tiến độ PPCT thì trigger prewarm lại cho lớp hiện tại."""
        mode_label = self._get_sched_teacher_progress_mode_label()
        self._log(f"Tiến độ PPCT: chuyển mode {mode_label}.", "info")
        self._on_sched_progress_context_changed()

    def _render_sched_teacher_progress_detail(self):
        """Render chi tiết tiến độ PPCT vào cửa sổ popup nếu đang mở."""
        text_widget = getattr(self, "_sched_teacher_progress_text", None)
        if text_widget is None or not text_widget.winfo_exists():
            return

        payload = self._sched_teacher_progress_payload or {}
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.tag_configure("title", foreground="#375a7f", font=("Segoe UI", 9, "bold"))
        text_widget.tag_configure("ok", foreground="#1f6d2d")
        text_widget.tag_configure("warn", foreground="#a86400")
        text_widget.tag_configure("error", foreground="#b00020")
        text_widget.tag_configure("muted", foreground="#666666")

        status = str(payload.get("status", "") or "").strip().lower()
        lop_text = str(payload.get("lop", "")).strip()
        upper_week = int(payload.get("upper_week", 0) or 0)
        teacher_name = str(payload.get("teacher_name", "")).strip() or "không rõ giáo viên"

        if not payload:
            text_widget.insert("end", "Chưa có dữ liệu tiến độ PPCT để hiển thị.\n", "muted")
        elif status == "loading":
            text_widget.insert("end", "Đang quét tiến độ PPCT...\n", "warn")
            text_widget.insert(
                "end",
                f"Lớp: {lop_text} | Đến Tuần {upper_week}\n",
                "muted",
            )
        elif status == "error":
            text_widget.insert("end", "Không đọc được tiến độ PPCT.\n", "error")
            text_widget.insert(
                "end",
                str(payload.get("message", "Lỗi không xác định")) + "\n",
                "error",
            )
        else:
            subjects = list(payload.get("subjects") or [])
            weeks_requested = int(payload.get("weeks_requested", 0) or 0)
            weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
            scan_stopped_early = bool(payload.get("scan_stopped_early", False))
            cache_hits = int(payload.get("cache_hits", 0) or 0)
            bulk_hits = int(payload.get("bulk_hits", 0) or 0)
            fallback_hits = int(payload.get("fallback_hits", 0) or 0)
            requested_concurrency = int(payload.get("requested_concurrency", 0) or 0)
            effective_concurrency = int(payload.get("effective_concurrency", 0) or 0)
            fast_mode = bool(payload.get("fast_mode", False))
            week_errors = list(payload.get("week_errors") or [])

            text_widget.insert("end", "Tiến độ PPCT các môn bạn đang dạy\n", "title")
            text_widget.insert(
                "end",
                (
                    f"Giáo viên: {teacher_name}\n"
                    f"Lớp: {lop_text}\n"
                    f"Tổng hợp đến: Tuần {upper_week}\n"
                    f"Mode quét: {'FAST' if fast_mode else 'CHUẨN'}\n\n"
                ),
                "muted",
            )

            if not subjects:
                text_widget.insert(
                    "end",
                    "Không phát hiện môn nào có Ký tên khớp với giáo viên hiện tại trong phạm vi đã quét.\n",
                    "warn",
                )
            else:
                for item in subjects:
                    ppct_text = str(item.get("ppct", "--"))
                    mon_hoc = str(item.get("mon_hoc", "(Không rõ môn)"))
                    week_text = str(item.get("week_text", ""))
                    slot_label = str(item.get("slot_label", ""))
                    text_widget.insert("end", f"• {mon_hoc}\n", "title")
                    text_widget.insert(
                        "end",
                        f"  PPCT cuối: {ppct_text}\n  Vị trí: {week_text} | {slot_label}\n\n",
                        "ok",
                    )

            if week_errors:
                text_widget.insert("end", "Tuần đọc lỗi\n", "title")
                for item in week_errors[:10]:
                    text_widget.insert(
                        "end",
                        f"• Tuần {item.get('week', '?')}: {item.get('message', '')}\n",
                        "warn",
                    )
                if len(week_errors) > 10:
                    text_widget.insert(
                        "end",
                        f"... còn {len(week_errors) - 10} tuần lỗi chưa liệt kê\n",
                        "warn",
                    )
                text_widget.insert("end", "\n")

            text_widget.insert(
                "end",
                (
                    f"Đã quét {weeks_scanned}/{weeks_requested or weeks_scanned} tuần"
                    + (" | dừng sớm" if scan_stopped_early else "")
                    + f" | Cache hit {cache_hits} | Bulk {bulk_hits} | Fallback {fallback_hits}"
                    + (
                        f" | Concurrency {effective_concurrency}/{requested_concurrency}"
                        if (requested_concurrency > 0 and effective_concurrency > 0) else ""
                    )
                    + "\n"
                ),
                "muted",
            )

        text_widget.config(state="disabled")

    def _open_sched_teacher_progress_dialog(self):
        """Mở popup chi tiết tiến độ PPCT theo lớp đang chọn."""
        if self._sched_teacher_progress_window and self._sched_teacher_progress_window.winfo_exists():
            self._sched_teacher_progress_window.deiconify()
            self._sched_teacher_progress_window.lift()
            self._sched_teacher_progress_window.focus_force()
            self._render_sched_teacher_progress_detail()
            return

        window = tk.Toplevel(self.root)
        window.title("Tiến độ PPCT môn đang dạy")
        window.transient(self.root)
        window.geometry("700x420")
        window.minsize(560, 320)
        self._sched_teacher_progress_window = window

        header = ttk.Frame(window, padding=10)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Tiến độ PPCT môn bạn đang dạy trong lớp đã chọn",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            textvariable=self.var_sched_teacher_progress_status,
            foreground="#666",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        text_widget = tk.Text(window, wrap="word", font=("Segoe UI", 9))
        text_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._sched_teacher_progress_text = text_widget

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="Đóng", command=window.destroy).pack(side="right")

        def _on_close():
            try:
                window.destroy()
            finally:
                self._sched_teacher_progress_window = None
                self._sched_teacher_progress_text = None

        window.protocol("WM_DELETE_WINDOW", _on_close)
        self._render_sched_teacher_progress_detail()

    def _on_sched_teacher_progress_button_click(self):
        """Mở chi tiết tiến độ hoặc chủ động refresh khi chưa có payload mới nhất."""
        current_lop = self.var_sched_lop.get().strip()
        if not current_lop:
            self._log("Chưa chọn lớp để xem tiến độ PPCT.", "warning")
            return
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return

        payload = self._sched_teacher_progress_payload or {}
        payload_lop = str(payload.get("lop", "")).strip()
        payload_status = str(payload.get("status", "") or "").strip().lower()

        if self._quick_prepare_running or self._schedule_running or self._auto_login_running:
            if payload:
                self._open_sched_teacher_progress_dialog()
            return

        if (not payload) or payload_lop != current_lop or payload_status in {"", "error"}:
            self._request_sched_teacher_progress_refresh()
        self._open_sched_teacher_progress_dialog()

    def _cancel_sched_teacher_progress_jobs(self):
        """Hủy các lịch refresh/prewarm đang chờ để tránh worker cũ ghi đè trạng thái mới."""
        if self._sched_teacher_progress_after_id:
            try:
                self.root.after_cancel(self._sched_teacher_progress_after_id)
            except Exception:
                pass
            self._sched_teacher_progress_after_id = None
        if self._sched_teacher_progress_prewarm_after_id:
            try:
                self.root.after_cancel(self._sched_teacher_progress_prewarm_after_id)
            except Exception:
                pass
            self._sched_teacher_progress_prewarm_after_id = None

    def _schedule_sched_teacher_progress_prewarm(self, delay_ms=350):
        """Lên lịch prewarm nền cho tiến độ PPCT và cache form của lớp đang chọn."""
        if self._closing or not self._root_exists():
            return
        self._cancel_sched_teacher_progress_jobs()

        try:
            delay_ms = max(0, int(delay_ms))
        except Exception:
            delay_ms = 0

        self._sched_teacher_progress_prewarm_after_id = self.root.after(
            delay_ms,
            self._request_sched_teacher_progress_prewarm,
        )

    def _render_sched_teacher_progress(self, payload):
        """Hiển thị kết quả tóm tắt tiến độ PPCT ngay trong panel schedule."""
        self._sched_teacher_progress_payload = copy.deepcopy(payload) if payload else None
        if not payload:
            default_state = "normal" if (self._cdp_connected and self.var_sched_lop.get().strip()) else "disabled"
            if default_state == "normal":
                status_text = "Đã sẵn sàng. Bấm nút bên dưới hoặc chọn lại lớp để quét tiến độ PPCT."
            else:
                status_text = "Quét FULL DỮ LIỆU rồi chọn lớp để xem tiến độ PPCT các môn bạn đang dạy."
            self.var_sched_teacher_progress_status.set(status_text)
            self._set_sched_teacher_progress_button(
                "📌 XEM / CẬP NHẬT TIẾN ĐỘ PPCT MÔN ĐANG DẠY",
                state=default_state,
            )
            self._render_sched_teacher_progress_detail()
            return

        status = str(payload.get("status", "") or "").strip().lower()
        lop_text = str(payload.get("lop", "")).strip()
        upper_week = int(payload.get("upper_week", 0) or 0)
        teacher_name = str(payload.get("teacher_name", "")).strip()

        if status == "loading":
            mode_label = "FAST" if bool(payload.get("fast_mode", False)) else "CHUẨN"
            if upper_week > 0:
                loading_text = (
                    f"Đang quét lớp {lop_text} đến Tuần {upper_week} "
                    f"(mode {mode_label}) cho giáo viên {teacher_name or 'hiện tại'}..."
                )
            else:
                loading_text = (
                    f"Đang quét lớp {lop_text} theo tuần mới nhất đang có trên VnEdu "
                    f"(mode {mode_label})..."
                )
            self.var_sched_teacher_progress_status.set(loading_text)
            self._set_sched_teacher_progress_button(
                "⏳ ĐANG QUÉT TIẾN ĐỘ PPCT...",
                state="disabled",
            )
            self._render_sched_teacher_progress_detail()
            return

        if status == "error":
            self.var_sched_teacher_progress_status.set(
                f"Không đọc được tiến độ PPCT của lớp {lop_text}."
            )
            self._set_sched_teacher_progress_button(
                "⚠ XEM LỖI QUÉT TIẾN ĐỘ PPCT",
                state="normal",
            )
            self._render_sched_teacher_progress_detail()
            return

        subjects = list(payload.get("subjects") or [])
        weeks_requested = int(payload.get("weeks_requested", 0) or 0)
        weeks_scanned = int(payload.get("weeks_scanned", 0) or 0)
        scan_stopped_early = bool(payload.get("scan_stopped_early", False))
        cache_hits = int(payload.get("cache_hits", 0) or 0)
        bulk_hits = int(payload.get("bulk_hits", 0) or 0)
        requested_concurrency = int(payload.get("requested_concurrency", 0) or 0)
        effective_concurrency = int(payload.get("effective_concurrency", 0) or 0)
        fast_mode = bool(payload.get("fast_mode", False))
        week_errors = list(payload.get("week_errors") or [])
        teacher_display = teacher_name or "không rõ giáo viên"
        mode_label = "FAST" if fast_mode else "CHUẨN"

        if not subjects:
            self.var_sched_teacher_progress_status.set(
                f"{teacher_display} | Lớp {lop_text} | Mode {mode_label} | Tổng hợp đến Tuần {upper_week} | 0 môn"
            )
            self._set_sched_teacher_progress_button(
                "📌 KHÔNG PHÁT HIỆN MÔN NÀO - BẤM ĐỂ XEM CHI TIẾT",
                state="normal",
            )
            self._render_sched_teacher_progress_detail()
            return

        self.var_sched_teacher_progress_status.set(
            f"{teacher_display} | Lớp {lop_text} | Mode {mode_label} | Tổng hợp đến Tuần {upper_week} | {len(subjects)} môn"
        )
        top_item = subjects[0]
        top_mon_hoc = str(top_item.get("mon_hoc", "(Không rõ môn)"))
        top_ppct = str(top_item.get("ppct", "--"))
        button_text = (
            f"📌 {top_mon_hoc} | PPCT {top_ppct}"
            f" | {top_item.get('week_text', '')}"
            f" | {top_item.get('slot_label', '')}"
        )
        if len(subjects) > 1:
            button_text += f" | +{len(subjects) - 1} môn"
        if week_errors:
            button_text += f" | lỗi tuần: {len(week_errors)}"
        button_text += f" | quét {weeks_scanned}/{weeks_requested or weeks_scanned} tuần"
        if scan_stopped_early:
            button_text += " | dừng sớm"
        if cache_hits:
            button_text += f" | cache {cache_hits}"
        if bulk_hits:
            button_text += f" | bulk {bulk_hits}"
        if requested_concurrency > 0 and effective_concurrency > 0:
            button_text += f" | c{effective_concurrency}/{requested_concurrency}"
        self._set_sched_teacher_progress_button(button_text, state="normal")
        self._render_sched_teacher_progress_detail()

    @staticmethod
    def _parse_week_number(text):
        """Tách số tuần từ text như 'Tuần 24'."""
        match = re.search(r"\d+", str(text or ""))
        if not match:
            return None
        try:
            return int(match.group())
        except Exception:
            return None

    def _resolve_sched_progress_latest_week(self, bridge):
        """Lấy tuần mới nhất đang có trên VnEdu, không phụ thuộc tuần nhập trên GUI."""
        ok_tuan, tuan_options_or_error = bridge.get_tuan_options()
        if ok_tuan:
            numbers = [
                num for num in
                (self._parse_week_number(item) for item in list(tuan_options_or_error or []))
                if num is not None
            ]
            if numbers:
                latest_week = max(numbers)
                self._sched_teacher_progress_latest_week = latest_week
                return latest_week

        ok_current, current_info_or_error = bridge.get_current_selection()
        if ok_current:
            latest_week = self._parse_week_number((current_info_or_error or {}).get("tuan", ""))
            if latest_week is not None:
                self._sched_teacher_progress_latest_week = latest_week
                return latest_week

        if self._sched_teacher_progress_latest_week is not None:
            return int(self._sched_teacher_progress_latest_week)
        return None

    def _on_sched_progress_context_changed(self, *_args):
        """Debounce prewarm nền vùng tiến độ PPCT khi đổi lớp hoặc sau các bước prepare."""
        lop_text = self.var_sched_lop.get().strip()
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )
        current_issue = self._get_sched_form_invalid_reason()
        if (
            current_issue.startswith("Bạn đã đổi Lớp")
            and current_issue != self._sched_form_last_announced_issue
        ):
            self._sched_form_last_announced_issue = current_issue
            self._log(f"⚠ {current_issue}", "warning")
        if not lop_text:
            self._cancel_sched_teacher_progress_jobs()
            self._render_sched_teacher_progress(None)
            return

        if not self._cdp_connected:
            self._cancel_sched_teacher_progress_jobs()
            self._render_sched_teacher_progress(None)
            return

        mode_label = self._get_sched_teacher_progress_mode_label()
        payload = self._sched_teacher_progress_payload or {}
        payload_lop = str(payload.get("lop", "")).strip()
        payload_status = str(payload.get("status", "") or "").strip().lower()
        if payload_lop != lop_text or payload_status in {"", "error"}:
            self.var_sched_teacher_progress_status.set(
                f"Đang chuẩn bị dữ liệu tiến độ PPCT nền ({mode_label}) cho lớp {lop_text}..."
            )
            self._set_sched_teacher_progress_button(
                "⏳ ĐANG CHUẨN BỊ TIẾN ĐỘ PPCT...",
                state="normal",
            )
        self._schedule_sched_teacher_progress_prewarm(delay_ms=350)

    def _request_sched_teacher_progress_prewarm(self):
        """Prewarm cache form và tiến độ PPCT trong nền, không khóa UI."""
        self._sched_teacher_progress_prewarm_after_id = None
        lop_text = self.var_sched_lop.get().strip()

        if not self._cdp_connected:
            return
        if self._schedule_running or self._quick_prepare_running or self._auto_login_running:
            return
        if not lop_text:
            return

        existing_payload = copy.deepcopy(self._sched_teacher_progress_payload or {})
        existing_lop = str(existing_payload.get("lop", "")).strip()
        existing_status = str(existing_payload.get("status", "") or "").strip().lower()
        fast_mode = bool(self.var_sched_teacher_progress_fast_mode.get())

        self._sched_teacher_progress_request_id += 1
        request_id = self._sched_teacher_progress_request_id

        def _work():
            bridge = None
            payload = None
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    return

                ok_current, current_info = bridge.get_current_selection()
                if ok_current and isinstance(current_info, dict):
                    current_lop = str(current_info.get("lop", "")).strip()
                    cached_form = self._get_sched_form_options_cache(current_info)
                    if current_lop and current_lop == lop_text and cached_form is None:
                        ok_form, form_payload = bridge.read_form_options(row_index=0)
                        if ok_form:
                            self._set_sched_form_options_cache(current_info, form_payload)

                resolved_upper_week = self._resolve_sched_progress_latest_week(bridge)
                if resolved_upper_week is None:
                    return

                resolved_upper_week_num = int(resolved_upper_week)
                self._sched_teacher_progress_latest_week = resolved_upper_week_num

                payload_upper_week = int(existing_payload.get("upper_week", 0) or 0)
                payload_fast_mode = bool(existing_payload.get("fast_mode", False))
                if (
                    existing_payload
                    and existing_lop == lop_text
                    and existing_status == "ok"
                    and payload_upper_week == resolved_upper_week_num
                    and payload_fast_mode == fast_mode
                ):
                    payload = copy.deepcopy(existing_payload)
                else:
                    ok_user, teacher_name_or_error = bridge.get_current_user_full_name()
                    if not ok_user:
                        return
                    payload = self._collect_sched_teacher_progress_report(
                        bridge=bridge,
                        lop_text=lop_text,
                        upper_week=resolved_upper_week_num,
                        teacher_name=str(teacher_name_or_error),
                        fast_mode=fast_mode,
                    )
                    payload["status"] = "ok"
            except Exception as e:
                self._post_ui(
                    lambda e=e: self._log(
                        f"Prewarm tiến độ PPCT lỗi: {type(e).__name__}: {str(e)[:160]}",
                        "warning",
                    )
                )
                return
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

            def _apply():
                if request_id != self._sched_teacher_progress_request_id:
                    return
                if self.var_sched_lop.get().strip() != lop_text:
                    return
                if payload:
                    self._render_sched_teacher_progress(payload)

            self._post_ui(_apply)

        threading.Thread(target=_work, daemon=True).start()

    def _request_sched_teacher_progress_refresh(self):
        """Khởi chạy worker quét nhanh các môn của giáo viên trong lớp đang chọn."""
        self._cancel_sched_teacher_progress_jobs()
        lop_text = self.var_sched_lop.get().strip()
        upper_week = int(self._sched_teacher_progress_latest_week or 0)
        fast_mode = bool(self.var_sched_teacher_progress_fast_mode.get())

        if not self._cdp_connected:
            self._render_sched_teacher_progress(None)
            return
        if self._schedule_running or self._quick_prepare_running or self._auto_login_running:
            return
        if not lop_text:
            self._render_sched_teacher_progress(None)
            return

        self._sched_teacher_progress_request_id += 1
        request_id = self._sched_teacher_progress_request_id
        self._render_sched_teacher_progress({
            "status": "loading",
            "lop": lop_text,
            "upper_week": upper_week,
            "fast_mode": fast_mode,
        })

        def _work():
            bridge = None
            upper_week_hint = upper_week
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    payload = {
                        "status": "error",
                        "lop": lop_text,
                        "upper_week": upper_week_hint,
                        "message": f"Kết nối CDP thất bại: {msg}",
                    }
                else:
                    ok_user, teacher_name_or_error = bridge.get_current_user_full_name()
                    if not ok_user:
                        payload = {
                            "status": "error",
                            "lop": lop_text,
                            "upper_week": upper_week_hint,
                            "message": str(teacher_name_or_error),
                        }
                    else:
                        resolved_upper_week = self._resolve_sched_progress_latest_week(bridge)
                        if resolved_upper_week is None:
                            payload = {
                                "status": "error",
                                "lop": lop_text,
                                "upper_week": upper_week_hint,
                                "message": "Không đọc được tuần mới nhất từ VnEdu",
                            }
                        else:
                            resolved_upper_week_num = int(resolved_upper_week)
                            self._sched_teacher_progress_latest_week = resolved_upper_week_num
                            payload = self._collect_sched_teacher_progress_report(
                                bridge=bridge,
                                lop_text=lop_text,
                                upper_week=resolved_upper_week_num,
                                teacher_name=str(teacher_name_or_error),
                                fast_mode=fast_mode,
                            )
                            payload["status"] = "ok"
            except Exception as e:
                payload = {
                    "status": "error",
                    "lop": lop_text,
                    "upper_week": upper_week_hint,
                    "message": f"Worker tiến độ PPCT lỗi: {type(e).__name__}: {str(e)[:160]}",
                }
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

            def _apply():
                if request_id != self._sched_teacher_progress_request_id:
                    return
                self._render_sched_teacher_progress(payload)

            self._post_ui(_apply)

        threading.Thread(target=_work, daemon=True).start()

    def _collect_sched_teacher_progress_report(self, bridge, lop_text, upper_week, teacher_name, fast_mode=False):
        """Quét từ tuần mới về cũ để tìm môn giáo viên đang dạy trong lớp."""
        teacher_key = self._normalize_person_name(teacher_name)
        teacher_subjects = {}
        latest_by_subject = {}
        fast_mode = bool(fast_mode)

        upper_week_num = int(upper_week)
        week_numbers_desc = list(range(upper_week_num, 0, -1))
        requested_weeks = len(week_numbers_desc)

        week_errors = []
        weeks_scanned = 0
        cache_hits = 0
        bulk_hits = 0
        fallback_hits = 0
        requested_concurrency = 0
        effective_concurrency = 0
        scan_stopped_early = False

        # Quét theo batch để tận dụng cache/fetch bulk và cho phép dừng sớm an toàn.
        batch_size = 8
        requested_bulk_concurrency = 5 if fast_mode else 4
        if fast_mode:
            min_weeks_before_early_stop = 12
            stable_batches_needed = 1
        else:
            min_weeks_before_early_stop = 28
            stable_batches_needed = 3
        stable_batches_without_progress = 0

        for batch_start in range(0, len(week_numbers_desc), batch_size):
            week_batch = week_numbers_desc[batch_start:batch_start + batch_size]
            payload_map, batch_week_errors, fetch_meta = self._fetch_class_stats_week_payloads_bulk(
                bridge,
                lop_text,
                week_batch,
                concurrency=requested_bulk_concurrency,
            )
            week_errors.extend(batch_week_errors)
            cache_hits += int(fetch_meta.get("cache_hits", 0) or 0)
            bulk_hits += int(fetch_meta.get("bulk_hits", 0) or 0)
            fallback_hits += int(fetch_meta.get("fallback_hits", 0) or 0)
            requested_concurrency = max(
                requested_concurrency,
                int(fetch_meta.get("requested_concurrency", 0) or 0),
            )
            effective_concurrency = max(
                effective_concurrency,
                int(fetch_meta.get("effective_concurrency", 0) or 0),
            )

            batch_has_progress_update = False
            for tuan_num in week_batch:
                payload = payload_map.get(tuan_num)
                if payload is None:
                    continue

                weeks_scanned += 1
                rows = list((payload or {}).get("rows") or [])
                for row in rows:
                    if not row.get("has_data"):
                        continue
                    mon_hoc = str(row.get("mon_hoc", "")).strip()
                    if not mon_hoc:
                        continue
                    subject_key = self._normalize_class_stats_subject(mon_hoc)
                    ky_ten = str(row.get("ky_ten", "")).strip()
                    if ky_ten and self._normalize_person_name(ky_ten) == teacher_key:
                        teacher_subjects.setdefault(subject_key, mon_hoc)

                    if subject_key not in teacher_subjects:
                        continue

                    ppct_text = str(row.get("ppct", "")).strip()
                    match = re.search(r"\d+", ppct_text)
                    if not match:
                        continue

                    occurrence = {
                        "mon_hoc": teacher_subjects.get(subject_key, mon_hoc),
                        "ppct": int(match.group()),
                        "ppct_text": ppct_text,
                        "week": tuan_num,
                        "week_text": f"Tuần {tuan_num}",
                        "thu": str(row.get("thu", "")).strip(),
                        "buoi": str(row.get("buoi", "")).strip(),
                        "tiet": str(row.get("tiet", "")).strip(),
                        "slot_label": self._format_schedule_slot_label(
                            row.get("thu", "?"),
                            row.get("buoi", "?"),
                            row.get("tiet", "?"),
                        ),
                        "thu_full": str(row.get("thu_full", "")).strip(),
                        "noi_dung_cong_viec": str(row.get("noi_dung_cong_viec", "")).strip(),
                        "nhan_xet_giao_vien": str(row.get("nhan_xet_giao_vien", "")).strip(),
                        "ky_ten": str(row.get("ky_ten", "")).strip(),
                        "sort_key": self._schedule_occurrence_sort_key({
                            "week": tuan_num,
                            "thu": row.get("thu", ""),
                            "buoi": row.get("buoi", ""),
                            "tiet": row.get("tiet", ""),
                        }),
                    }
                    current = latest_by_subject.get(subject_key)
                    if current is None:
                        latest_by_subject[subject_key] = occurrence
                        batch_has_progress_update = True
                        continue
                    if occurrence["ppct"] > current["ppct"]:
                        latest_by_subject[subject_key] = occurrence
                        batch_has_progress_update = True
                        continue
                    if occurrence["ppct"] == current["ppct"] and occurrence["sort_key"] > current["sort_key"]:
                        latest_by_subject[subject_key] = occurrence
                        batch_has_progress_update = True

            if batch_has_progress_update:
                stable_batches_without_progress = 0
            elif latest_by_subject:
                stable_batches_without_progress += 1

            if (
                latest_by_subject
                and weeks_scanned >= min_weeks_before_early_stop
                and stable_batches_without_progress >= stable_batches_needed
            ):
                scan_stopped_early = True
                break

        subjects = self._canonicalize_sched_teacher_subjects(
            list(latest_by_subject.values())
        )
        return {
            "teacher_name": teacher_name,
            "lop": lop_text,
            "upper_week": upper_week_num,
            "subjects": subjects,
            "weeks_requested": requested_weeks,
            "weeks_scanned": weeks_scanned,
            "scan_stopped_early": scan_stopped_early,
            "fast_mode": fast_mode,
            "cache_hits": cache_hits,
            "bulk_hits": bulk_hits,
            "fallback_hits": fallback_hits,
            "requested_concurrency": requested_concurrency,
            "effective_concurrency": effective_concurrency,
            "week_errors": week_errors,
        }

    def _canonicalize_sched_teacher_subjects(self, subjects):
        """Gộp các biến thể base/detailed của cùng một môn khi chỉ có một phân môn thật sự."""
        items = list(subjects or [])
        if not items:
            return []

        groups = {}
        for item in items:
            mon_hoc = str(item.get("mon_hoc", "")).strip()
            base_text = re.sub(r"\s*\([^)]*\)\s*", " ", mon_hoc).strip() or mon_hoc
            base_key = self._normalize_class_stats_subject(base_text)
            full_key = self._normalize_class_stats_subject(mon_hoc)
            groups.setdefault(base_key, []).append((full_key, item))

        merged = []
        for base_key, entries in groups.items():
            plain_entries = [item for full_key, item in entries if full_key == base_key]
            detailed_entries = [item for full_key, item in entries if full_key != base_key]

            if plain_entries and len(detailed_entries) == 1:
                candidates = plain_entries + detailed_entries
                best_item = max(
                    candidates,
                    key=lambda item: (
                        int(item.get("ppct", 0) or 0),
                        item.get("sort_key", (0, 0, 0, 0)),
                    ),
                )
                merged_item = dict(best_item)
                merged_item["mon_hoc"] = str(detailed_entries[0].get("mon_hoc", "")).strip() or str(best_item.get("mon_hoc", "")).strip()
                merged.append(merged_item)
                continue

            merged.extend(item for _full_key, item in entries)

        return sorted(
            merged,
            key=lambda item: (-int(item.get("ppct", 0) or 0), str(item.get("mon_hoc", ""))),
        )

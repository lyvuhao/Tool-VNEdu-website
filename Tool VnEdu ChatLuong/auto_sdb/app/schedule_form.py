"""Quét form, chọn môn/phân môn và dựng yêu cầu chạy lịch."""

import copy
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ..cdp.bridge import ChromeBridge
from ..config import SCHEDULE_DAYS, SCHEDULE_MODE_KHDH, SCHEDULE_MODE_MANUAL


class ScheduleFormMixin:
    """Quét form, chọn môn/phân môn và dựng yêu cầu chạy lịch."""

    def _ensure_bridge_detail_ready(self, bridge, action_name):
        """Xác nhận tab đang ở màn Chi tiết sổ đầu bài trước khi thao tác nghiệp vụ."""
        try:
            if bridge and bridge._is_v5_detail_ready():
                return True, ""
        except Exception:
            pass

        current_url = ""
        try:
            current_url = str(bridge.page.url or "")
        except Exception:
            current_url = ""
        return (
            False,
            f"{action_name}: tab hiện tại chưa ở màn Chi tiết sổ đầu bài. "
            f"Hãy dùng Đăng nhập và chuẩn bị trước. URL hiện tại: {current_url or '(không xác định)'}"
        )

    @staticmethod
    def _week_numbers_from_vars(var_from, var_to, *, require_multi_week=False):
        """Đọc khoảng tuần từ IntVar, trả về list tuần hợp lệ hoặc None nếu không đáng tin."""
        try:
            tuan_from = int(var_from.get())
            tuan_to = int(var_to.get())
        except Exception:
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        if not (1 <= tuan_from <= 52 and 1 <= tuan_to <= 52):
            return None
        if require_multi_week and tuan_from == tuan_to:
            return None
        return list(range(tuan_from, tuan_to + 1))

    def _on_sched_load_lop(self):
        """Tải danh sách Lớp từ VnEdu → populate schedule combobox."""
        if not self._cdp_connected:
            return

        tuan_nums = self._week_numbers_from_vars(
            self.var_sched_tuan_from,
            self.var_sched_tuan_to,
        )

        scan_scope = (
            f"Tuần {tuan_nums[0]}→{tuan_nums[-1]}" if tuan_nums else "toàn bộ tuần có trên web"
        )
        self._log(f"📥 Đang tải DS Lớp cho Lịch dạy ({scan_scope})...", "info")

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok_ready, ready_msg = self._ensure_bridge_detail_ready(
                        bridge,
                        "Tải DS Lớp",
                    )
                    if not ok_ready:
                        bridge.disconnect()
                        self._post_ui(lambda: self._log(ready_msg, "warning"))
                        return
                    ok2, lop_data = bridge.discover_lop_options_for_weeks(tuan_nums=tuan_nums)
                    bridge.disconnect()
                    if ok2:
                        options = self._sort_lop_options(lop_data.get("options", []))
                        weeks_scanned = len(lop_data.get("weeks_scanned", []) or [])
                        self._post_ui(lambda: self._populate_sched_lop(options))
                        self._post_ui(lambda: self._log(
                            f"Đã quét lớp theo {weeks_scanned} tuần ({scan_scope}), tìm thấy {len(options)} lớp.",
                            "success",
                        ))
                    else:
                        self._post_ui(lambda: self._log(f"Lỗi tải DS Lớp: {lop_data}", "error"))
                else:
                    self._post_ui(lambda: self._log(f"Lỗi kết nối: {msg}", "error"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(f"Exception tải DS: {e}", "error"))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_sched_lop(self, options):
        """Populate schedule Lớp combobox.

        Args:
            options: list[str] — danh sách tên lớp
        """
        options = self._sort_lop_options(options)
        prev_lop = self.var_sched_lop.get().strip()
        self.cmb_sched_lop['values'] = options
        if prev_lop and prev_lop in options:
            self.cmb_sched_lop.set(prev_lop)
        elif options:
            self.cmb_sched_lop.set(options[0])
        self._log(f"Đã tải {len(options)} lớp cho Lịch dạy", "success")
        if self.var_sched_lop.get().strip():
            self.var_sched_teacher_progress_status.set(
                "Đã sẵn sàng. App sẽ prewarm nền tiến độ PPCT cho lớp đang chọn."
            )
        self._render_sched_teacher_progress(None)
        if self._cdp_connected and self.var_sched_lop.get().strip():
            self._schedule_sched_teacher_progress_prewarm(delay_ms=350)

    def _on_sched_scan_form(self):
        """Quét form popup VnEdu → đọc Phân môn dropdown options → populate GUI.

        Flow: CDP mở form tạm (click ➕ row 0) → đọc combobox store → đóng form.
        """
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return

        self._log("🔍 Đang quét form dropdown options...", "info")
        self.btn_sched_scan_form.config(state="disabled")

        def _work():
            current_info = None
            ok_info = False
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok_ready, ready_msg = self._ensure_bridge_detail_ready(
                        bridge,
                        "Quét form",
                    )
                    if not ok_ready:
                        bridge.disconnect()
                        self._post_ui(lambda ready_msg=ready_msg: self._set_sched_form_rescan_reason(
                            f"{ready_msg} Hãy mở đúng màn sổ đầu bài của lớp hiện tại rồi bấm [Quét Form] lại."
                        ))
                        self._post_ui(lambda: self._log(ready_msg, "warning"))
                        return
                    ok_info, current_info = bridge.get_current_selection()
                    cached_data = self._get_sched_form_options_cache(current_info) if ok_info else None
                    if cached_data is not None:
                        bridge.disconnect()
                        self._post_ui(lambda: self._log("⚡ Quét form: dùng cache hiện có.", "info"))
                        self._post_ui(lambda: self._populate_sched_phan_mon(cached_data, current_info))
                        return

                    ok2, data = bridge.read_form_options(row_index=0)
                    bridge.disconnect()
                    if ok2:
                        if ok_info:
                            self._set_sched_form_options_cache(current_info, data)
                        self._post_ui(lambda: self._populate_sched_phan_mon(data, current_info if ok_info else None))
                    else:
                        self._post_ui(lambda: self._set_sched_form_rescan_reason(
                            "Quét Form chưa thành công. Hãy giữ đúng màn sổ đầu bài của lớp hiện tại rồi bấm [Quét Form] lại."
                        ))
                        self._post_ui(lambda: self._log(
                            f"❌ Quét form lỗi: {data}", "error"))
                else:
                    self._post_ui(lambda: self._set_sched_form_rescan_reason(
                        "Không kết nối được CDP để Quét Form. Hãy kiểm tra lại CDP rồi bấm [Quét Form] lại."
                    ))
                    self._post_ui(lambda: self._log(
                        f"❌ Kết nối CDP lỗi: {msg}", "error"))
            except Exception as e:
                self._post_ui(lambda: self._set_sched_form_rescan_reason(
                    "Quét Form bị gián đoạn. Hãy mở lại đúng màn sổ đầu bài rồi bấm [Quét Form] lại."
                ))
                self._post_ui(lambda e=e: self._log(
                    f"❌ Exception quét form: {e}", "error"))
            finally:
                self._post_ui(lambda: self.btn_sched_scan_form.config(
                    state="normal"))

        threading.Thread(target=_work, daemon=True).start()

    def _set_quick_prepare_button_state(self):
        """Đồng bộ trạng thái nút chuẩn bị nhanh."""
        if not hasattr(self, "btn_quick_prepare"):
            return
        enabled = self._cdp_connected and (not self._quick_prepare_running)
        self.btn_quick_prepare.config(state="normal" if enabled else "disabled")

    def _populate_sched_phan_mon(self, data, selection_info=None):
        """Populate Phân môn + Môn học combobox từ CDP scan data.

        Args:
            data: dict — {
                'phan_mon': [{value, text}, ...],
                'phan_mon_by_mon_hoc': {mon_hoc_value: [{value, text}, ...]},
                'xep_loai': [...],
                'mon_hoc': [{value, text}, ...],
                'mon_hoc_field': str (tên field trên VnEdu)
            }
        """
        prev_phan_mon = self.var_sched_phan_mon.get()
        prev_mon_hoc = self.var_sched_mon_hoc.get()

        # --- Cache options từ popup ---
        self._sched_phan_mon_options = list(data.get("phan_mon", []) or [])
        raw_pm_map = data.get("phan_mon_by_mon_hoc", {}) or {}
        self._sched_phan_mon_by_mon_hoc = {
            str(key): list(options or [])
            for key, options in raw_pm_map.items()
            if key is not None
        }

        mh_options = list(data.get("mon_hoc", []) or [])
        self._sched_mon_hoc_options = mh_options
        self._sched_mon_hoc_field = data.get("mon_hoc_field")
        self._mark_sched_form_scan_ready(selection_info)

        # --- Populate Môn học, ưu tiên giữ selection cũ nếu còn hợp lệ ---
        selected_mon_hoc = self._set_sched_combobox_selection(
            self.cmb_sched_mon_hoc,
            self.var_sched_mon_hoc,
            mh_options,
            preferred_text=prev_mon_hoc,
        )

        # --- Populate Phân môn theo Môn học đang chọn ---
        active_pm_options = self._refresh_sched_phan_mon_values(
            preferred_text=prev_phan_mon
        )

        self._log(
            f"✅ Đã tải {len(mh_options)} Môn học, "
            f"{len(active_pm_options)} Phân môn cho '{selected_mon_hoc or '(trống)'}'"
            + (" (đã map theo Môn học)" if self._sched_phan_mon_by_mon_hoc else ""),
            "success"
        )
        missing_map_subjects = [
            str(opt.get("text", "")).strip()
            for opt in mh_options
            if str(opt.get("text", "")).strip()
            and not self._select_sched_phan_mon_options(
                opt.get("value"),
                self._sched_phan_mon_by_mon_hoc,
                [],
            )
        ]
        if missing_map_subjects:
            self._log(
                "⚠ Một số Môn học chưa tải được Phân môn chuyên biệt: "
                + ", ".join(missing_map_subjects[:6])
                + ". Nếu bạn vừa đổi lớp hoặc đổi màn VnEdu, hãy bấm [Quét Form] lại.",
                "warning",
            )
        if selected_mon_hoc and not active_pm_options:
            self._log(
                f"⚠ Môn học '{selected_mon_hoc}' hiện chưa có Phân môn hợp lệ. "
                "Nút chạy sẽ bị khóa cho tới khi bạn bấm [Quét Form] lại hoặc quay về đúng màn VnEdu.",
                "warning",
            )
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )

    @staticmethod
    def _make_sched_form_options_cache_key(selection_info):
        """Sinh cache key cho dữ liệu quét form theo ngữ cảnh lớp/cấp hiện tại."""
        info = selection_info or {}
        cap = str(info.get("cap", "")).strip().lower()
        lop = str(info.get("lop", "")).strip().lower()
        if not cap and not lop:
            return None
        return (cap, lop)

    def _get_sched_form_options_cache(self, selection_info):
        """Đọc cache quét form theo lớp/cấp hiện tại."""
        cache_key = self._make_sched_form_options_cache_key(selection_info)
        if cache_key is None:
            return None
        with self._sched_form_options_cache_lock:
            cached = self._sched_form_options_cache.get(cache_key)
        return copy.deepcopy(cached) if cached is not None else None

    def _set_sched_form_options_cache(self, selection_info, data):
        """Lưu cache quét form theo lớp/cấp hiện tại."""
        cache_key = self._make_sched_form_options_cache_key(selection_info)
        if cache_key is None:
            return
        with self._sched_form_options_cache_lock:
            self._sched_form_options_cache[cache_key] = copy.deepcopy(data)

    def _clear_sched_form_options_cache(self):
        """Xóa cache dữ liệu quét form trong session hiện tại."""
        with self._sched_form_options_cache_lock:
            self._sched_form_options_cache.clear()

    @staticmethod
    def _normalize_sched_option_text(text):
        """Chuẩn hóa text option để so khớp ổn định."""
        return str(text or "").strip()

    def _resolve_sched_option(self, options, display_text):
        """Tìm option theo text hiển thị đã chọn trên combobox."""
        target = self._normalize_sched_option_text(display_text)
        if not target:
            return None

        for opt in options or []:
            if self._normalize_sched_option_text(opt.get("text")) == target:
                return opt
        return None

    def _get_sched_phan_mon_options_for_mon(self, mon_hoc_value=None):
        """Lấy danh sách Phân môn tương ứng với Môn học hiện tại."""
        target_value = mon_hoc_value
        if target_value is None:
            mon_opt = self._resolve_sched_option(
                self._sched_mon_hoc_options,
                self.var_sched_mon_hoc.get(),
            )
            if mon_opt is not None:
                target_value = mon_opt.get("value")

        return self._select_sched_phan_mon_options(
            target_value,
            self._sched_phan_mon_by_mon_hoc,
            self._sched_phan_mon_options,
        )

    def _set_sched_combobox_selection(self, combobox, variable, options,
                                      preferred_text=""):
        """Populate combobox từ options và giữ selection cũ nếu còn hợp lệ."""
        display_list = [
            opt.get("text", "")
            for opt in options or []
            if self._normalize_sched_option_text(opt.get("text"))
        ]
        combobox["values"] = display_list

        selected_text = ""
        preferred_opt = self._resolve_sched_option(options, preferred_text)
        if preferred_opt is not None:
            selected_text = preferred_opt.get("text", "")
        elif display_list:
            selected_text = display_list[0]

        variable.set(selected_text)
        combobox.set(selected_text)
        return selected_text

    def _refresh_sched_phan_mon_values(self, preferred_text=None):
        """Đồng bộ combobox Phân môn theo Môn học đang chọn."""
        pm_options = self._get_sched_phan_mon_options_for_mon()
        selected_text = preferred_text
        if selected_text is None:
            selected_text = self.var_sched_phan_mon.get()

        self._set_sched_combobox_selection(
            self.cmb_sched_phan_mon,
            self.var_sched_phan_mon,
            pm_options,
            preferred_text=selected_text,
        )
        if hasattr(self, "btn_sched_run"):
            self._set_schedule_button_states(
                running=self._schedule_running,
                can_resume=bool(self._schedule_resume_state),
            )
        return pm_options

    def _on_sched_mon_hoc_selected(self, _event=None):
        """Đổi Môn học → cập nhật lại danh sách Phân môn tương ứng."""
        self._refresh_sched_phan_mon_values()

    def _on_sched_phan_mon_selected(self, _event=None):
        """Đổi Phân môn → cập nhật lại state chạy/resume và guidance trên panel."""
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )

    def _get_schedule_slots(self):
        """Trích xuất danh sách (thu, buoi, tiet) từ grid checkboxes.

        Returns:
            list[dict] — mỗi dict: {thu: int, buoi: str, tiet: int}
            Sắp xếp theo thu → buoi → tiet.
        """
        slots = []
        for thu in SCHEDULE_DAYS:
            buoi_text = self._sched_buoi[thu].get()
            if buoi_text == "---":
                continue

            # Xác định buổi cần quét
            if buoi_text == "Sáng":
                buoi_list = [("S", "Sáng")]
            elif buoi_text == "Chiều":
                buoi_list = [("C", "Chiều")]
            elif buoi_text == "Cả hai":
                # "Cả hai": checkboxes bind vào grid "S", tạo slots cho cả Sáng và Chiều
                buoi_list = [("S", "Sáng"), ("S", "Chiều")]
            else:
                continue

            for buoi_code, buoi_name in buoi_list:
                for tiet_idx in range(5):
                    var = self._sched_grid[(thu, buoi_code)][tiet_idx]
                    if var.get():
                        slots.append({
                            "thu": thu,
                            "buoi": buoi_name,
                            "tiet": tiet_idx + 1
                        })

        # Sắp xếp: thu → buoi (Sáng trước Chiều) → tiet
        slots.sort(key=lambda s: (s["thu"], 0 if s["buoi"] == "Sáng" else 1, s["tiet"]))
        return slots

    def _format_schedule_slot_label(self, thu, buoi, tiet):
        """Format nhãn slot chuẩn để dùng cho log và bảng tổng kết."""
        thu_token = ChromeBridge._normalize_thu_token(thu)
        if thu_token == "CN":
            return f"Chủ nhật {buoi} Tiết {tiet}"
        thu_display = thu_token or str(thu)
        return f"Thứ {thu_display} {buoi} Tiết {tiet}"

    @staticmethod
    def _format_sched_live_progress_text(current, total, phase, detail="", state="idle"):
        """Tạo text tiến trình gọn, dễ đọc cho thanh live progress."""
        icons = {
            "idle": "●",
            "running": "🟢",
            "paused": "⏸",
            "success": "✅",
            "error": "✗",
        }
        safe_phase = str(phase or "Tiến trình").strip() or "Tiến trình"
        safe_detail = str(detail or "").strip()
        total_value = max(int(total or 0), 0)
        current_value = max(int(current or 0), 0)

        if total_value > 0:
            current_value = min(current_value, total_value)
            percent = int(round((current_value / total_value) * 100))
            text = f"{icons.get(state, '●')} {safe_phase}: {current_value}/{total_value} ({percent}%)"
            if state == "running" and current_value < total_value:
                text += f" | còn {total_value - current_value} bước"
        else:
            text = f"{icons.get(state, '●')} {safe_phase}"

        if safe_detail:
            text += f" | {safe_detail}"
        return text

    def _set_sched_live_progress(self, current=None, total=None, phase="Tiến trình", detail="", state="idle"):
        """Đồng bộ thanh progress xanh + text live để người dùng biết app còn đang chạy."""
        if not hasattr(self, "var_sched_live_progress"):
            return

        progressbars = []
        if hasattr(self, "cdp_live_progressbar"):
            progressbars.append(self.cdp_live_progressbar)
        if hasattr(self, "sched_progressbar"):
            progressbars.append(self.sched_progressbar)
        if hasattr(self, "compact_progressbar"):
            progressbars.append(self.compact_progressbar)

        if progressbars:
            maximum = total
            if maximum is None:
                try:
                    maximum = int(float(progressbars[0]["maximum"]))
                except Exception:
                    maximum = 0
            maximum = max(int(maximum or 0), 1)
            value = current
            if value is None:
                try:
                    value = int(float(progressbars[0]["value"]))
                except Exception:
                    value = 0
            value = max(0, min(int(value or 0), maximum))
            for progressbar in progressbars:
                progressbar["maximum"] = maximum
                progressbar["value"] = value
        else:
            maximum = max(int(total or 0), 0)
            value = max(int(current or 0), 0)

        self.var_sched_live_progress.set(
            self._format_sched_live_progress_text(
                current=value,
                total=maximum,
                phase=phase,
                detail=detail,
                state=state,
            )
        )

    def _build_schedule_request(self, show_messages=True):
        """Thu thập dữ liệu schedule ở mức request thô, chưa cần resolve ID từ CDP."""
        schedule_mode = self._get_sched_mode()
        lop_list = self._get_sched_lop_list(schedule_mode)
        sched_lop = lop_list[0] if lop_list else ""
        if not sched_lop:
            if show_messages:
                self._log("Chưa chọn Lớp!", "warning")
                messagebox.showwarning("Cảnh báo", "Chưa chọn Lớp trong panel Lịch dạy.")
            return None

        try:
            tuan_from = self.var_sched_tuan_from.get()
            tuan_to = self.var_sched_tuan_to.get()
        except (tk.TclError, ValueError):
            if show_messages:
                self._log("Tuần không hợp lệ!", "error")
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from

        if schedule_mode == SCHEDULE_MODE_KHDH:
            slots = []
            mon_hoc_text = ""
            phan_mon_text = ""
        else:
            slots = self._get_schedule_slots()
            if not slots:
                if show_messages:
                    self._log("Chưa cấu hình Lịch dạy! Check ít nhất 1 tiết.", "warning")
                    messagebox.showwarning(
                        "Cảnh báo",
                        "Chưa cấu hình tiết nào trong Lịch dạy.\nHãy chọn Buổi và check các tiết cần nhập."
                    )
                return None

            mon_hoc_text = self.var_sched_mon_hoc.get().strip()
            phan_mon_text = self.var_sched_phan_mon.get().strip()
            if not mon_hoc_text or not phan_mon_text:
                if show_messages:
                    self._show_sched_form_blocked_warning(
                        "Chưa sẵn sàng chạy",
                        self._get_sched_form_invalid_reason()
                        or (
                            "Chưa chọn đủ Môn học / Phân môn.\n"
                            "Hãy chọn lại dữ liệu. Nếu danh sách đang lệch hoặc bị trống, hãy bấm [Quét Form] lại."
                        ),
                    )
                return None

        try:
            ppct_start = int(self.var_sched_ppct_start.get())
        except (tk.TclError, ValueError):
            ppct_start = 1

        return {
            "port": self._cdp_port,
            "tuan_from": int(tuan_from),
            "tuan_to": int(tuan_to),
            "lop": sched_lop,
            "lop_list": lop_list,
            "slots": slots,
            "hs_nghi": self.var_sched_hs_nghi.get() or "0",
            "diem": self.var_sched_diem.get() or "10",
            "nhan_xet_raw": self.var_sched_nhan_xet.get() or "Lớp học chăm ngoan",
            "ppct_start": int(ppct_start),
            "mon_hoc_text": mon_hoc_text,
            "phan_mon_text": phan_mon_text,
            "schedule_mode": schedule_mode,
            "username": self.var_vnedu_username.get().strip(),
            "password": self.var_vnedu_password.get(),
        }

    def _show_auto_confirm_dialog(self, request_data):
        """Hiển thị dialog xác nhận auto-login + nhập dữ liệu với màu nhấn mạnh."""
        accepted = {"value": False}
        khdh_mode = str(
            request_data.get("schedule_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
        ).strip().lower() == SCHEDULE_MODE_KHDH
        slot_lines = []
        if khdh_mode:
            slot_lines.append("Mode KHDH: quét các row chữ đỏ theo gợi ý live của từng tuần")
        else:
            slot_lines = [
                self._format_schedule_slot_label(
                    slot.get("thu", "?"),
                    slot.get("buoi", "?"),
                    slot.get("tiet", "?"),
                )
                for slot in (request_data.get("slots") or [])
            ]
        win = tk.Toplevel(self.root)
        win.title("Xác nhận Auto Đăng nhập & Nhập dữ liệu")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Kiểm tra kỹ trước khi chạy tự động hoàn toàn",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 6))

        txt = tk.Text(win, width=68, height=15, wrap="word", font=("Segoe UI", 9))
        txt.pack(fill="both", expand=True, padx=12)
        txt.tag_configure("section", foreground="#0b5394", font=("Segoe UI", 9, "bold"))
        txt.tag_configure("subject", foreground="#38761d")
        txt.tag_configure("time", foreground="#b45f06")
        txt.tag_configure("slot", foreground="#674ea7")
        txt.tag_configure("warn", foreground="#cc0000", font=("Segoe UI", 9, "bold"))

        txt.insert("end", "Môn học / Phân môn\n", "section")
        if khdh_mode:
            txt.insert("end", "Theo dữ liệu gợi ý KHDH live từ từng row đỏ\n\n", "subject")
        else:
            txt.insert("end", f"Môn học: {request_data.get('mon_hoc_text', '')}\n", "subject")
            txt.insert("end", f"Phân môn: {request_data.get('phan_mon_text', '')}\n\n", "subject")
        txt.insert("end", "Thời gian / Lớp / PPCT\n", "section")
        txt.insert(
            "end",
            f"Từ tuần {request_data.get('tuan_from')} đến tuần {request_data.get('tuan_to')}\n",
            "time",
        )
        txt.insert("end", f"Lớp: {request_data.get('lop')}\n", "time")
        if khdh_mode:
            txt.insert("end", "PPCT: lấy trực tiếp từ từng row gợi ý KHDH\n\n", "time")
        else:
            txt.insert("end", f"PPCT bắt đầu: {request_data.get('ppct_start')}\n\n", "time")
        txt.insert("end", "Các slot sẽ nhập\n", "section")
        for line in slot_lines:
            txt.insert("end", f"• {line}\n", "slot")
        txt.insert("end", "\n")

        warnings = []
        if not request_data.get("username"):
            warnings.append("Thiếu tài khoản VnEdu")
        if not request_data.get("password"):
            warnings.append("Thiếu mật khẩu VnEdu")
        if warnings:
            txt.insert("end", "Cảnh báo\n", "section")
            for item in warnings:
                txt.insert("end", f"• {item}\n", "warn")

        txt.config(state="disabled")

        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=10)

        def _accept():
            accepted["value"] = True
            win.destroy()

        ttk.Button(row, text="Bắt đầu", style="QuickGreen.TButton", command=_accept).pack(side="right", padx=(6, 0))
        ttk.Button(row, text="Hủy", command=win.destroy).pack(side="right")

        try:
            self.root.wait_window(win)
        except Exception:
            pass
        return accepted["value"]

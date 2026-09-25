"""Chuẩn bị nhanh và tự đăng nhập + chạy."""

import threading
from tkinter import messagebox

from ..cdp.bridge import ChromeBridge


class QuickActionsMixin:
    """Chuẩn bị nhanh và tự đăng nhập + chạy."""

    def _on_quick_prepare(self):
        """Chạy tuần tự Inspect -> Tải lớp -> Quét form bằng một bridge."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return
        if self._schedule_running:
            self._log("Schedule đang chạy, không thể chuẩn bị nhanh cùng lúc.", "warning")
            return
        if self._quick_prepare_running:
            self._log("Chuẩn bị nhanh đang chạy rồi.", "warning")
            return

        self._quick_prepare_running = True
        self._set_quick_prepare_button_state()
        self.btn_inspect.config(state="disabled")
        self.btn_sched_load_lop.config(state="disabled")
        self.btn_sched_scan_form.config(state="disabled")
        self._set_sched_live_progress(
            current=0,
            total=3,
            phase="Quét full dữ liệu",
            detail="Chuẩn bị Inspect / Tải lớp / Quét Form",
            state="running",
        )
        self._log("🟢 Đang chuẩn bị nhanh: Inspect -> Tải lớp -> Quét form...", "info")

        def _finish():
            self._quick_prepare_running = False
            if self._cdp_connected:
                self.btn_inspect.config(state="normal")
                self.btn_sched_load_lop.config(state="normal")
                self.btn_sched_scan_form.config(state="normal")
            self._set_quick_prepare_button_state()

        def _work():
            bridge = None
            inspect_data = None
            lop_options = None
            form_data = None
            form_data_from_cache = False
            form_selection_info = None
            errors = []
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    errors.append(f"Kết nối CDP lỗi: {msg}")
                else:
                    ok_ready, ready_msg = self._ensure_bridge_detail_ready(
                        bridge,
                        "Quét dữ liệu lớp/tuần",
                    )
                    if not ok_ready:
                        errors.append(ready_msg)
                        return
                    ok_inspect, inspect_payload = bridge.inspect_page()
                    if ok_inspect:
                        inspect_data = inspect_payload
                        self._post_ui(lambda: self._set_sched_live_progress(
                            current=1,
                            total=3,
                            phase="Quét dữ liệu",
                            detail="Inspect xong, đang tải danh sách lớp",
                            state="running",
                        ))
                    else:
                        errors.append(f"Inspect lỗi: {inspect_payload}")

                    ok_lop, lop_payload = bridge.discover_lop_options_for_weeks()
                    if ok_lop:
                        lop_options = self._sort_lop_options(lop_payload.get("options", []))
                        self._post_ui(lambda: self._set_sched_live_progress(
                            current=2,
                            total=3,
                            phase="Quét full dữ liệu",
                            detail="Đã tải lớp, đang quét Môn học / Phân môn",
                            state="running",
                        ))
                    else:
                        errors.append(f"Tải DS Lớp lỗi: {lop_payload}")

                    ok_info, current_info = bridge.get_current_selection()
                    cached_form = self._get_sched_form_options_cache(current_info) if ok_info else None
                    if cached_form is not None:
                        form_data = cached_form
                        form_data_from_cache = True
                        form_selection_info = current_info if ok_info else None
                    else:
                        ok_form, form_payload = bridge.read_form_options(row_index=0)
                        if ok_form:
                            form_data = form_payload
                            form_selection_info = current_info if ok_info else None
                            if ok_info:
                                self._set_sched_form_options_cache(current_info, form_payload)
                        else:
                            errors.append(f"Quét form lỗi: {form_payload}")
            except Exception as e:
                errors.append(
                    f"Chuẩn bị nhanh exception: {type(e).__name__}: {str(e)[:140]}"
                )
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

                def _apply_results():
                    if inspect_data is not None:
                        self._show_inspect_result(inspect_data)
                    if lop_options is not None:
                        self._populate_sched_lop(lop_options)
                    if form_data is not None:
                        self._populate_sched_phan_mon(form_data, form_selection_info)
                        if form_data_from_cache:
                            self._log("Quét dữ liệu: dùng cache Quét form hiện có.", "info")

                    if errors:
                        if form_data is None:
                            self._set_sched_form_rescan_reason(
                                "Chuẩn bị nhanh chưa lấy được dữ liệu Môn học / Phân môn. "
                                "Hãy mở đúng màn sổ đầu bài của lớp hiện tại rồi bấm [Quét Form] lại."
                            )
                        for item in errors:
                            self._log(item, "error")
                        if inspect_data is not None or lop_options is not None or form_data is not None:
                            self._log("⚠ Chuẩn bị nhanh hoàn tất nhưng có bước lỗi.", "warning")
                        self._set_sched_live_progress(
                            current=(
                                int(inspect_data is not None)
                                + int(lop_options is not None)
                                + int(form_data is not None)
                            ),
                            total=3,
                            phase="Quét full dữ liệu",
                            detail="Có bước lỗi. Kiểm tra log để biết mục cần làm lại",
                            state="error",
                        )
                    else:
                        self._log(
                            "✅ Chuẩn bị nhanh hoàn tất: Inspect, Tải lớp, Quét form",
                            "success",
                        )
                        self._set_sched_live_progress(
                            current=3,
                            total=3,
                            phase="Quét full dữ liệu",
                            detail="Hoàn tất Inspect, tải lớp và Quét Form",
                            state="success",
                        )
                    _finish()

                self._post_ui(_apply_results)

        threading.Thread(target=_work, daemon=True).start()

    def _resolve_schedule_params_from_form_data(self, request_data, form_data):
        """Resolve ID Môn học / Phân môn từ dữ liệu form quét được sau auto-navigation."""
        mon_hoc_options = list(form_data.get("mon_hoc") or [])
        phan_mon_options_all = list(form_data.get("phan_mon") or [])
        raw_pm_map = form_data.get("phan_mon_by_mon_hoc", {}) or {}
        phan_mon_by_mon_hoc = {
            str(key): list(options or [])
            for key, options in raw_pm_map.items()
            if key is not None
        }

        mon_hoc_option = self._resolve_sched_option(
            mon_hoc_options, request_data.get("mon_hoc_text", "")
        )
        if mon_hoc_option is None:
            return None, (
                f"Không resolve được Môn học '{request_data.get('mon_hoc_text', '')}' "
                "từ dữ liệu form sau khi auto đăng nhập. "
                "Hãy mở đúng màn sổ đầu bài rồi bấm [Quét Form] lại."
            )

        mon_hoc_value = mon_hoc_option["value"]
        phan_mon_options = self._select_sched_phan_mon_options(
            mon_hoc_value,
            phan_mon_by_mon_hoc,
            phan_mon_options_all,
        )
        if not phan_mon_options:
            return None, (
                f"Không tải được Phân môn chuyên biệt cho Môn học "
                f"'{mon_hoc_option.get('text', '')}'. Hãy bấm [Quét Form] lại trên đúng lớp và đúng màn VnEdu."
            )
        phan_mon_option = self._resolve_sched_option(
            phan_mon_options, request_data.get("phan_mon_text", "")
        )
        if phan_mon_option is None:
            return None, (
                f"Không resolve được Phân môn '{request_data.get('phan_mon_text', '')}' "
                f"cho Môn học '{mon_hoc_option.get('text', '')}'. "
                "Nếu danh sách vừa bị lệch sau khi đổi lớp/màn VnEdu, hãy bấm [Quét Form] lại."
            )

        tuan_count = int(request_data["tuan_to"]) - int(request_data["tuan_from"]) + 1
        params = {
            "port": self._get_cdp_port(),
            "tuan_from": int(request_data["tuan_from"]),
            "tuan_to": int(request_data["tuan_to"]),
            "lop": str(request_data["lop"]),
            "slots": list(request_data.get("slots") or []),
            "hs_nghi": str(request_data.get("hs_nghi", "0")),
            "diem": str(request_data.get("diem", "10")),
            "nhan_xet_raw": str(request_data.get("nhan_xet_raw", "")),
            "phan_mon_value": phan_mon_option["value"],
            "phan_mon_text": phan_mon_option["text"],
            "mon_hoc_value": mon_hoc_value,
            "mon_hoc_text": mon_hoc_option["text"],
            "mon_hoc_field": form_data.get("mon_hoc_field"),
            "ppct_start": int(request_data["ppct_start"]),
            "planned_total": tuan_count * len(request_data.get("slots") or []),
        }
        return params, ""

    def _on_auto_login_and_run(self):
        """Tự mở Chrome Auto, đăng nhập VnEdu và vào Chi tiết sổ đầu bài."""
        if self._schedule_running:
            self._log("Schedule đang chạy, không thể auto-login cùng lúc.", "warning")
            return
        if self._auto_login_running:
            self._log("Auto-login đang chạy rồi.", "warning")
            return
        if self._delete_running or self._delete_scanning:
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có thao tác Xóa dữ liệu chạy. Hãy chờ xong rồi mới auto-login.",
            )
            return
        if self._class_stats_thread and self._class_stats_thread.is_alive():
            messagebox.showwarning(
                "Cảnh báo",
                "Đang có phiên thống kê lớp sử dụng CDP. Hãy chờ xong rồi mới chạy auto-login.",
            )
            return

        username = self.var_vnedu_username.get().strip()
        password = self.var_vnedu_password.get()

        self._auto_login_running = True
        self._set_auto_login_button_state()
        self._set_sched_live_progress(
            current=0,
            total=3,
            phase="Auto-login",
            detail="Chuẩn bị Chrome Auto",
            state="running",
        )
        self._log("🟢 Bắt đầu auto-login và điều hướng tới Chi tiết sổ đầu bài...", "info")

        def _work():
            bridge = None
            connect_info = ""
            nav_info = None
            errors = []
            try:
                ok_launch, launch_info, port = self._launch_or_reuse_chrome_auto_sync()
                if not ok_launch:
                    errors.append(f"Chrome Auto lỗi: {launch_info}")
                    return
                connect_info = launch_info
                self._post_ui(lambda: self._set_sched_live_progress(
                    current=1,
                    total=3,
                    phase="Auto-login",
                    detail="Chrome Auto đã sẵn sàng, đang nối CDP",
                    state="running",
                ))

                bridge = ChromeBridge(port=port)
                ok_connect, msg_connect = bridge.connect(allow_any_tab=True)
                if not ok_connect:
                    errors.append(f"Kết nối CDP lỗi: {msg_connect}")
                    return
                self._post_ui(lambda: self._set_sched_live_progress(
                    current=2,
                    total=3,
                    phase="Auto-login",
                    detail="Đã nối CDP, đang điều hướng tới Chi tiết sổ đầu bài",
                    state="running",
                ))

                ok_nav, nav_payload = bridge.ensure_chi_tiet_sodau_bai(
                    username,
                    password,
                )
                if not ok_nav:
                    errors.append(str(nav_payload))
                    return
                nav_info = nav_payload
            except Exception as e:
                errors.append(f"Auto-login exception: {type(e).__name__}: {str(e)[:180]}")
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass

                def _apply_results():
                    if connect_info:
                        self._cdp_connect_result(True, connect_info, self._get_cdp_port())
                    if nav_info:
                        self._log(
                            f"✅ Đã vào Chi tiết sổ đầu bài: {nav_info.get('url', '')}",
                            "success",
                        )

                    self._auto_login_running = False
                    self._set_auto_login_button_state()

                    if errors:
                        for item in errors:
                            self._log(item, "error")
                        self.lbl_sched_progress.config(text="✗ Auto-login/điều hướng chưa hoàn tất")
                        self._set_sched_live_progress(
                            current=0,
                            total=3,
                            phase="Auto-login",
                            detail="Chưa hoàn tất. Kiểm tra log để biết bước bị lỗi",
                            state="error",
                        )
                        return

                    self._log(
                        "✅ Auto-login hoàn tất. Bạn đang ở màn Chi tiết sổ đầu bài.",
                        "success",
                    )
                    self.lbl_sched_progress.config(
                        text="✅ Đã vào Chi tiết sổ đầu bài — sẵn sàng cho bước quét/nhập thủ công"
                    )
                    self._set_sched_live_progress(
                        current=3,
                        total=3,
                        phase="Auto-login",
                        detail="Đã vào màn Chi tiết sổ đầu bài",
                        state="success",
                    )

                self._post_ui(_apply_results)

        self._auto_login_thread = threading.Thread(target=_work, daemon=True)
        self._auto_login_thread.start()

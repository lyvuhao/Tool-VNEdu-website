"""Xem thông tin trang, khôi phục UI VnEdu."""

import threading

from ..cdp.bridge import ChromeBridge


class InspectMixin:
    """Xem thông tin trang, khôi phục UI VnEdu."""

    def _on_load_current_info(self):
        """Đọc Tuần + Lớp hiện tại trên VnEdu (background thread)."""
        if not self._cdp_connected:
            return

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok2, info = bridge.get_current_selection()
                    bridge.disconnect()
                    if ok2:
                        self._post_ui(lambda: self._show_current_info(info))
                else:
                    bridge.disconnect()
            except Exception as e:
                print(f"[CDP] Load current info error: {e}")

        threading.Thread(target=_work, daemon=True).start()

    def _show_current_info(self, info):
        """Hiển thị thông tin Tuần/Lớp hiện tại."""
        tuan = info.get("tuan", "?")
        lop = info.get("lop", "?")
        self.lbl_cdp_info.config(
            text=f"📌 Trang hiện tại: {tuan} — {lop}"
        )

    def _on_inspect_page(self):
        """Inspect cấu trúc trang VnEdu (background thread)."""
        if not self._cdp_connected:
            return

        self._log("🔍 Đang inspect trang VnEdu...", "info")

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if ok:
                    ok2, data = bridge.inspect_page()
                    bridge.disconnect()
                    if ok2:
                        self._post_ui(lambda: self._show_inspect_result(data))
                    else:
                        self._post_ui(lambda: self._log(f"Inspect lỗi: {data}", "error"))
                else:
                    bridge.disconnect()
                    self._post_ui(lambda: self._log(f"Inspect connect lỗi: {msg}", "error"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(f"Inspect exception: {e}", "error"))

        threading.Thread(target=_work, daemon=True).start()

    def _show_inspect_result(self, data):
        """Hiển thị kết quả inspect."""
        self._log(f"📋 URL: {data.get('url', '?')}", "info")

        # ExtJS Comboboxes (VnEdu v5)
        ext_combos = data.get("extCombos", [])
        if ext_combos:
            self._log(f"📋 ExtJS Comboboxes ({len(ext_combos)}):", "info")
            for c in ext_combos:
                self._log(
                    f"   ✓ {c.get('id','')} name={c.get('name','')} "
                    f"store={c.get('storeCount',0)} current=\"{c.get('rawValue','')}\"",
                    "info"
                )

        # HTML <select> (fallback)
        selects = data.get("selects", [])
        if selects:
            self._log(f"📋 HTML Selects ({len(selects)}):", "info")
            for s in selects:
                vis = "✓" if s.get("visible") else "✗"
                self._log(
                    f"   {vis} #{s.get('id','')} name={s.get('name','')} "
                    f"opts={s.get('optionCount',0)} current={s.get('currentText','')}",
                    "info"
                )

        if not ext_combos and not selects:
            self._log("📋 Dropdowns (0): Không tìm thấy", "warning")

        # Tables
        tables = data.get("tables", [])
        self._log(f"📋 Tables ({len(tables)}):", "info")
        for t in tables:
            rs = "rowspan" if t.get("hasRowspan") else "flat"
            self._log(
                f"   #{t.get('id','')} class={t.get('className','')} "
                f"rows={t.get('rows',0)} [{rs}]",
                "info"
            )

        # Add buttons count
        add_count = data.get("addButtons", 0)
        if add_count > 0:
            self._log(f"📋 Nút ➕ (a.add_tiet_so_dau_bai): {add_count}", "info")

    def _format_vnedu_ui_state(self, data):
        """Tóm tắt trạng thái tương tác chính của trang VNEDU để log ngắn gọn."""
        if not isinstance(data, dict):
            return str(data)
        if data.get("khdh_checked") is True:
            mode = "Gợi ý KHBD/KHDH"
        elif data.get("view_checked") is True:
            mode = "Xem"
        else:
            mode = "không rõ"

        combo_bits = []
        for combo in list(data.get("combos") or []):
            name = str(combo.get("name", "") or "").strip()
            label = {
                "cboCapHoc": "Cấp",
                "cboTuanHoc": "Tuần",
                "cboLopHoc": "Lớp",
            }.get(name, name or "combo")
            raw = str(combo.get("raw", "") or "").strip() or "--"
            flags = []
            if combo.get("expanded"):
                flags.append("đang mở")
            if combo.get("disabled"):
                flags.append("disabled")
            if combo.get("readOnly"):
                flags.append("readOnly")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            combo_bits.append(f"{label}={raw}{flag_text}")

        combo_text = "; ".join(combo_bits) if combo_bits else "không đọc được dropdown"
        return (
            f"mode={mode}; AJAX={'đang chạy' if data.get('ajax_loading') else 'rảnh'}; "
            f"mask={int(data.get('visible_masks') or 0)}; "
            f"popup_chặn={int(data.get('blocking_windows') or 0)}; "
            f"dropdown_mở={int(data.get('visible_boundlists') or 0)}; "
            f"bảng={'có' if data.get('has_sdb_table') else 'chưa thấy'}; {combo_text}"
        )

    def _on_recover_vnedu_ui(self):
        """Khôi phục trạng thái thao tác VNEDU sau automation bị kẹt."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return
        if (
            self._schedule_running
            or self._quick_prepare_running
            or self._class_stats_running
            or self._delete_running
            or getattr(self, "_delete_scanning", False)
            or getattr(self, "_auto_login_running", False)
        ):
            self._log(
                "Đang có worker dùng CDP. Chờ worker xong rồi mới khôi phục web.",
                "warning",
            )
            return

        self._log("Đang khôi phục trạng thái web VNEDU...", "info")
        self.btn_recover_web.config(state="disabled")

        def _work():
            bridge = None
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    self._post_ui(lambda: self._log(f"Khôi phục web connect lỗi: {msg}", "error"))
                    return

                ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=True)
                ok_state, state = bridge.diagnose_ui_state()
                if ok_cleanup:
                    self._post_ui(lambda: self._log(f"Đã khôi phục web: {msg_cleanup}", "success"))
                else:
                    self._post_ui(lambda: self._log(f"Khôi phục web lỗi: {msg_cleanup}", "warning"))
                if ok_state:
                    self._post_ui(lambda: self._log(
                        "Trạng thái web sau khôi phục: " + self._format_vnedu_ui_state(state),
                        "info",
                    ))
                else:
                    self._post_ui(lambda: self._log(f"Không đọc được trạng thái sau khôi phục: {state}", "warning"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(
                    f"Khôi phục web exception: {type(e).__name__}: {str(e)[:120]}",
                    "error",
                ))
            finally:
                if bridge:
                    try:
                        bridge.disconnect()
                    except Exception:
                        pass
                self._post_ui(lambda: self.btn_recover_web.config(
                    state="normal" if self._cdp_connected else "disabled"
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _on_discover_delete_controls(self):
        """[READ-ONLY] Dò cấu trúc nút Xóa trên dòng đã có dữ liệu (background thread)."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            return

        self._log("🧪 Đang dò cấu trúc nút Xóa (chỉ đọc, không click)...", "info")

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    bridge.disconnect()
                    self._post_ui(lambda: self._log(f"Kiểm tra nút xóa connect lỗi: {msg}", "error"))
                    return
                ok2, data = bridge.discover_delete_controls(max_rows=8)
                bridge.disconnect()
                if ok2:
                    self._post_ui(lambda: self._show_delete_discovery_result(data))
                else:
                    self._post_ui(lambda: self._log(f"Kiểm tra nút xóa lỗi: {data}", "error"))
            except Exception as e:
                self._post_ui(lambda e=e: self._log(f"Kiểm tra nút xóa exception: {e}", "error"))

        threading.Thread(target=_work, daemon=True).start()

    def _show_delete_discovery_result(self, data):
        """Log chi tiết các control tìm được để xác định selector nút Xóa thật."""
        self._log(
            f"🧪 Bảng: id='{data.get('table_id','')}' class='{data.get('table_class','')}'",
            "info",
        )

        global_selectors = data.get("global_selectors", {}) or {}
        if global_selectors:
            self._log("🧪 Thống kê control trong bảng (class/onclick fn → số lượng):", "info")
            for key, count in sorted(
                global_selectors.items(), key=lambda kv: kv[1], reverse=True
            ):
                self._log(f"   • [{count}x] {key}", "info")

        rows = data.get("rows", []) or []
        if not rows:
            self._log(
                "🧪 Không thấy dòng nào có control. Hãy mở Chi tiết sổ đầu bài "
                "của lớp/tuần ĐÃ CÓ dữ liệu rồi bấm lại.",
                "warning",
            )
            return

        for row in rows:
            row_idx = row.get("rowIdx", "?")
            texts = " | ".join(row.get("row_texts", [])[:8])
            attrs = row.get("action_cell_attrs", {}) or {}
            attr_summary = ", ".join(
                f"{k}={v}" for k, v in attrs.items()
                if k in ("thu", "buoi", "tiet", "mon_hoc_id", "phan_mon_id",
                         "tiet_ppct", "id", "so_dau_bai_id", "chi_tiet_id")
            )
            self._log(f"🧪 Row#{row_idx}: {texts}", "info")
            if attr_summary:
                self._log(f"     action_cell attrs: {attr_summary}", "info")
            for ctrl in row.get("controls", []):
                tag = ctrl.get("tag", "")
                cls = ctrl.get("className", "")
                onclick = ctrl.get("onclick", "")
                href = ctrl.get("href", "")
                title = ctrl.get("title", "")
                color = ctrl.get("color", "")
                data_attrs = ctrl.get("dataAttrs", {}) or {}
                bits = [f"<{tag}>"]
                if cls:
                    bits.append(f"class='{cls}'")
                if title:
                    bits.append(f"title='{title}'")
                if onclick:
                    bits.append(f"onclick=\"{onclick}\"")
                if href and href != "#":
                    bits.append(f"href='{href}'")
                if color:
                    bits.append(f"color={color}")
                if data_attrs:
                    bits.append("attrs=" + ", ".join(f"{k}={v}" for k, v in data_attrs.items()))
                self._log("       ↳ " + " ".join(bits), "info")
        self._log(
            "🧪 XONG. Hãy gửi lại log này để tôi xác định selector nút Xóa chính xác "
            "trước khi viết chức năng xóa.",
            "success",
        )

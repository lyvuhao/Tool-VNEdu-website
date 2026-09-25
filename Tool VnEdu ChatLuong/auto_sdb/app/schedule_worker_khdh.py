"""Worker nhập theo lịch ở chế độ KHDH."""

import copy
import random
import re
import time
import unicodedata

from ..cdp.bridge import ChromeBridge
from ..cdp.health import is_cdp_target_closed_error


class ScheduleWorkerKHDHMixin:
    """Worker nhập theo lịch ở chế độ KHDH."""

    def _schedule_worker_khdh(self, params):
        """Worker thread cho mode KHDH: quét row đỏ live rồi fill theo dữ liệu gợi ý."""
        bridge = None
        q = self._schedule_queue
        tuan_from = int(params["tuan_from"])
        tuan_to = int(params["tuan_to"])
        lop_list = [
            str(item or "").strip()
            for item in list(params.get("lop_list") or [params.get("lop")])
            if str(item or "").strip()
        ]
        if not lop_list:
            lop_list = [str(params.get("lop", "")).strip()]
        resume_state = params.get("resume_state") or {}
        completed = int(resume_state.get("completed", 0) or 0)
        skipped = int(resume_state.get("skipped", 0) or 0)
        errors = int(resume_state.get("errors", 0) or 0)
        last_success_ppct = resume_state.get("last_success_ppct")
        start_lop_idx = int(resume_state.get("next_lop_idx", 0) or 0)
        start_tuan_num = int(resume_state.get("next_tuan_num", tuan_from) or tuan_from)
        start_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
        start_row_key = str(resume_state.get("next_row_key", "") or "").strip()
        stopped = False
        checkpoint_state = {
            "next_lop_idx": start_lop_idx,
            "next_tuan_num": start_tuan_num,
            "next_slot_idx": start_slot_idx,
            "next_row_key": start_row_key or None,
            "next_ppct": None,
            "completed": completed,
            "skipped": skipped,
            "errors": errors,
            "last_success_ppct": last_success_ppct,
        }

        hs_nghi = params["hs_nghi"]
        diem = params["diem"]
        nhan_xet_raw = params["nhan_xet_raw"]
        nhan_xet_items = [x.strip() for x in nhan_xet_raw.split("|") if x.strip()]
        if not nhan_xet_items:
            nhan_xet_items = ["Lớp học chăm ngoan"]

        def _digits(value):
            text = str(value or "").strip()
            match = re.search(r"\d+", text)
            return match.group() if match else ""

        def _normalize_text(value):
            text = str(value or "").replace("\n", " ")
            text = unicodedata.normalize("NFD", text)
            text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
            return re.sub(r"\s+", " ", text).strip().casefold()

        def _normalize_date_token(value):
            text = str(value or "").strip()
            match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
            if not match:
                return ""
            day, month, year = match.group(1).split("/")
            return f"{int(day):02d}/{int(month):02d}/{year}"

        def _build_checkpoint(next_lop_idx, next_tuan_num, next_slot_idx, next_row_key=None):
            return {
                "next_lop_idx": int(next_lop_idx),
                "next_tuan_num": int(next_tuan_num),
                "next_slot_idx": int(next_slot_idx),
                "next_row_key": str(next_row_key or "").strip() or None,
                "next_ppct": None,
                "completed": int(completed),
                "skipped": int(skipped),
                "errors": int(errors),
                "last_success_ppct": last_success_ppct,
            }

        def _row_resume_key(row_info):
            parts = [
                str(row_info.get("thu", "") or "").strip(),
                str(row_info.get("buoi", "") or "").strip().casefold(),
                str(row_info.get("tiet", "") or "").strip(),
                str(row_info.get("mon_hoc_id", "") or "").strip(),
                str(row_info.get("phan_mon_id", "") or "").strip(),
                _digits(row_info.get("ppct_hint")),
                _normalize_text(row_info.get("noi_dung_hint", "")),
            ]
            return "|".join(parts)

        def _next_checkpoint_after_week(lop_idx, tuan_num):
            if int(tuan_num) < tuan_to:
                return _build_checkpoint(lop_idx, int(tuan_num) + 1, 0, None)
            return _build_checkpoint(int(lop_idx) + 1, tuan_from, 0, None)

        def _emit_slot_result(status, lop_text, tuan_num, row_info, message, ppct_value=None):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": (
                    f"Lớp {lop_text} | "
                    + self._format_schedule_slot_label(
                        row_info.get("thu", "?"),
                        row_info.get("buoi", "?"),
                        row_info.get("tiet", "?"),
                    )
                ),
                "ppct": ppct_value,
                "message": message,
            }))

        def _emit_system_result(status, lop_text, tuan_num, message):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": f"Lớp {lop_text} | Quét row đỏ KHDH",
                "ppct": None,
                "message": message,
            }))

        def _mark_cdp_closed_stop(lop_idx, tuan_num, row_idx, message):
            nonlocal errors, stopped, checkpoint_state
            errors += 1
            stopped = True
            checkpoint_state = _build_checkpoint(lop_idx, tuan_num, row_idx, None)
            if not self._schedule_stop_reason:
                self._schedule_stop_reason = "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng"
            q.put(("checkpoint", checkpoint_state))
            q.put(("error", (
                "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
                f"Worker KHDH dừng tại Lớp {lop_list[lop_idx] if 0 <= lop_idx < len(lop_list) else '?'}, "
                f"Tuần {tuan_num}. Chi tiết: {message}"
            )))

        def _select_dropdown_retry(label, text, attempts=3):
            last_msg = ""
            for attempt in range(max(int(attempts), 1)):
                ok_select, msg_select = bridge.select_dropdown(label, text)
                if ok_select:
                    return True, msg_select
                last_msg = str(msg_select)
                if is_cdp_target_closed_error(last_msg):
                    return False, last_msg
                if attempt < attempts - 1:
                    time.sleep(0.35 + attempt * 0.35)
            return False, last_msg

        def _extract_form_value(snapshot, field_name, use_raw=False):
            fields = (snapshot or {}).get("fields", {}) or {}
            field_info = fields.get(field_name, {}) or {}
            key = "raw" if use_raw else "value"
            return str(field_info.get(key, "") or "").strip()

        def _extract_popup_khdh_values(snapshot):
            popup_ppct_raw = _extract_form_value(snapshot, "tiet_ppct", use_raw=True) or _extract_form_value(snapshot, "tiet_ppct")
            popup_noi_dung = _extract_form_value(snapshot, "noi_dung", use_raw=True) or _extract_form_value(snapshot, "noi_dung")
            return {
                "thu": _digits(_extract_form_value(snapshot, "thu")),
                "tiet": _digits(_extract_form_value(snapshot, "tiet")),
                "ngay": _normalize_date_token(
                    _extract_form_value(snapshot, "ngay", use_raw=True)
                    or _extract_form_value(snapshot, "ngay")
                ),
                "mon_hoc_id": _extract_form_value(snapshot, "mon_hoc_id"),
                "mon_hoc_text": _extract_form_value(snapshot, "mon_hoc_id", use_raw=True),
                "phan_mon_id": _extract_form_value(snapshot, "phan_mon_id"),
                "phan_mon_text": _extract_form_value(snapshot, "phan_mon_id", use_raw=True),
                "ppct": _digits(popup_ppct_raw),
                "ppct_raw": str(popup_ppct_raw or "").strip(),
                "noi_dung": str(popup_noi_dung or "").strip(),
            }

        def _verify_snapshot_matches_row(snapshot, row_info):
            hard_issues = []
            soft_issues = []
            popup = _extract_popup_khdh_values(snapshot)

            row_thu = _digits(row_info.get("thu"))
            row_tiet = _digits(row_info.get("tiet"))
            row_ngay = _normalize_date_token(row_info.get("ngay"))
            row_mon_hoc_id = str(row_info.get("mon_hoc_id", "") or "").strip()
            row_phan_mon_id = str(row_info.get("phan_mon_id", "") or "").strip()
            row_ppct = _digits(row_info.get("ppct_hint"))
            row_noi_dung = str(row_info.get("noi_dung_hint", "") or "").strip()

            if row_thu and popup["thu"] and row_thu != popup["thu"]:
                hard_issues.append(f"thu mismatch {popup['thu']} != {row_thu}")
            if row_tiet and popup["tiet"] and row_tiet != popup["tiet"]:
                hard_issues.append(f"tiet mismatch {popup['tiet']} != {row_tiet}")
            if row_ngay and popup["ngay"] and row_ngay != popup["ngay"]:
                hard_issues.append(f"ngay mismatch {popup['ngay']} != {row_ngay}")

            if row_mon_hoc_id and popup["mon_hoc_id"] and row_mon_hoc_id != popup["mon_hoc_id"]:
                soft_issues.append(f"mon_hoc_id mismatch {popup['mon_hoc_id']} != {row_mon_hoc_id}")
            if row_phan_mon_id and popup["phan_mon_id"] and row_phan_mon_id != popup["phan_mon_id"]:
                soft_issues.append(f"phan_mon_id mismatch {popup['phan_mon_id']} != {row_phan_mon_id}")
            if row_ppct and popup["ppct"] and row_ppct != popup["ppct"]:
                soft_issues.append(f"PPCT mismatch {popup['ppct']} != {row_ppct}")
            if row_noi_dung and popup["noi_dung"]:
                expected = _normalize_text(row_noi_dung)
                actual = _normalize_text(popup["noi_dung"])
                if expected and actual and expected not in actual and actual not in expected:
                    soft_issues.append("noi_dung mismatch")

            return len(hard_issues) == 0, hard_issues, soft_issues, popup

        discovered_total = completed + skipped + errors

        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("error", f"Kết nối CDP thất bại: {msg}"))
                errors += 1
            else:
                bridge.setup_dialog_auto_accept()
                q.put(("log", "CDP connected (KHDH worker)", "success"))

                week_lop_cache = {}
                work_items = []
                for lop_idx in range(max(start_lop_idx, 0), len(lop_list)):
                    lop_text = lop_list[lop_idx]
                    first_week = start_tuan_num if lop_idx == start_lop_idx else tuan_from
                    for tuan_num in range(first_week, tuan_to + 1):
                        work_items.append((lop_idx, lop_text, tuan_num))

                for lop_idx, lop_text, tuan_num in work_items:
                    is_resume_anchor = (
                        lop_idx == start_lop_idx and tuan_num == start_tuan_num
                    )
                    row_begin_idx = start_slot_idx if is_resume_anchor else 0
                    if self._schedule_stop_event.is_set():
                        stopped = True
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        break

                    tuan_text = f"Tuần {tuan_num}"
                    q.put(("log", f"📅 Chuyển đến {tuan_text}...", "info"))
                    ok, msg = _select_dropdown_retry("tuan", tuan_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_select_tuan", lop_text, tuan_num, f"Lỗi chọn tuần: {msg}")
                        q.put(("log", f"❌ Lỗi chọn {tuan_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    available_lop_keys = week_lop_cache.get(tuan_num)
                    if available_lop_keys is None:
                        ok_lops, lops_or_error = bridge.get_lop_options()
                        if not ok_lops:
                            if is_cdp_target_closed_error(lops_or_error):
                                _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, lops_or_error)
                                break
                            q.put((
                                "log",
                                f"⚠ {tuan_text}: Không đọc được danh sách lớp để lọc trước: {lops_or_error}. "
                                "App sẽ thử chọn lớp trực tiếp.",
                                "warning",
                            ))
                            available_lop_keys = None
                        else:
                            available_lop_keys = {
                                str(item or "").strip().casefold()
                                for item in list(lops_or_error or [])
                                if str(item or "").strip()
                            }
                            week_lop_cache[tuan_num] = available_lop_keys

                    if available_lop_keys is not None and lop_text.casefold() not in available_lop_keys:
                        skipped += 1
                        checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        _emit_system_result(
                            "skipped_unavailable_class",
                            lop_text,
                            tuan_num,
                            "Lớp này không xuất hiện trong dropdown của tuần hiện tại, bỏ qua hợp lệ.",
                        )
                        q.put((
                            "log",
                            f"⏭ Lớp {lop_text} | {tuan_text}: lớp không có trong tuần này, bỏ qua.",
                            "info",
                        ))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log", f"🏫 Chọn Lớp {lop_text}...", "info"))
                    ok, msg = _select_dropdown_retry("lop", lop_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_select_lop", lop_text, tuan_num, f"Lỗi chọn lớp {lop_text}: {msg}")
                        q.put(("log", f"❌ Lỗi chọn Lớp {lop_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    ok, msg = bridge.set_goi_y_khdh_mode(True)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_set_khdh_mode", lop_text, tuan_num, msg)
                        q.put(("log", f"❌ {tuan_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    ok_rows, khdh_rows = bridge.read_khdh_suggested_rows()
                    if not ok_rows:
                        if is_cdp_target_closed_error(khdh_rows):
                            _mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, khdh_rows)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            row_begin_idx,
                            start_row_key if is_resume_anchor else None,
                        )
                        _emit_system_result("error_read_khdh_rows", lop_text, tuan_num, str(khdh_rows))
                        q.put(("log", f"❌ {tuan_text}: Không đọc được row đỏ KHDH: {khdh_rows}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    khdh_rows = [
                        row for row in (khdh_rows or [])
                        if row.get("has_add_btn") and row.get("rowIdx") is not None
                    ]
                    if is_resume_anchor and start_row_key:
                        matched_idx = next(
                            (
                                idx for idx, row in enumerate(khdh_rows)
                                if _row_resume_key(row) == start_row_key
                            ),
                            None,
                        )
                        if matched_idx is not None:
                            row_begin_idx = matched_idx
                        else:
                            q.put((
                                "log",
                                f"⚠ {tuan_text}: Không còn thấy row checkpoint key={start_row_key}. "
                                "App sẽ quét lại từ row đỏ đầu tiên hiện còn để tránh skip nhầm.",
                                "warning",
                            ))
                            row_begin_idx = 0
                    discovered_total += len(khdh_rows)
                    q.put(("progress_total", max(discovered_total, 1)))
                    if not khdh_rows:
                        q.put(("log", f"ℹ Lớp {lop_text} | {tuan_text}: Không có row đỏ KHDH nào cần nhập.", "info"))
                        checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    if row_begin_idx >= len(khdh_rows):
                        q.put(("log", f"ℹ Lớp {lop_text} | {tuan_text}: Không còn row KHDH nào để tiếp tục ở checkpoint hiện tại.", "info"))
                        checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log",
                           f"📊 Lớp {lop_text} | {tuan_text}: phát hiện {len(khdh_rows)} row đỏ KHDH cần xử lý.",
                           "info"))

                    for red_idx in range(row_begin_idx, len(khdh_rows)):
                        if self._schedule_stop_event.is_set():
                            stopped = True
                            next_row = khdh_rows[red_idx] if red_idx < len(khdh_rows) else None
                            checkpoint_state = _build_checkpoint(
                                lop_idx,
                                tuan_num,
                                red_idx,
                                _row_resume_key(next_row) if next_row else None,
                            )
                            break

                        row_info = khdh_rows[red_idx]
                        slot_label = self._format_schedule_slot_label(
                            row_info.get("thu", "?"),
                            row_info.get("buoi", "?"),
                            row_info.get("tiet", "?"),
                        )
                        row_ppct_hint = _digits(row_info.get("ppct_hint"))
                        checkpoint_state = _build_checkpoint(
                            lop_idx,
                            tuan_num,
                            red_idx,
                            _row_resume_key(row_info),
                        )
                        q.put(("checkpoint", checkpoint_state))
                        q.put((
                            "progress",
                            f"Lớp {lop_text} | {tuan_text} | {slot_label} | KHDH PPCT {row_ppct_hint or '--'}",
                            completed + skipped + errors,
                        ))

                        if not row_info.get("has_add_btn"):
                            skipped += 1
                            _emit_slot_result(
                                "skipped_existing",
                                lop_text,
                                tuan_num,
                                row_info,
                                "Row KHDH không còn nút + (có thể đã nhập trước đó)",
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"⏭ {slot_label}: Không còn nút +, bỏ qua.", "info"))
                            continue

                        ok, msg = bridge.click_add_button(
                            row_index=int(row_info.get("add_btn_index", -1) or -1),
                            row_dom_index=int(row_info.get("rowIdx", -1) or -1),
                        )
                        if not ok:
                            errors += 1
                            _emit_slot_result(
                                "error_click_add",
                                lop_text,
                                tuan_num,
                                row_info,
                                f"Click + thất bại: {msg}",
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Click + thất bại: {msg}", "error"))
                            continue

                        ok_form, msg_form = bridge.wait_for_lesson_form(
                            timeout_s=4.0,
                            poll_interval=0.12,
                        )
                        if not ok_form:
                            errors += 1
                            _emit_slot_result(
                                "error_open_form",
                                lop_text,
                                tuan_num,
                                row_info,
                                f"Form chưa mở sẵn sàng: {msg_form}",
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Form chưa mở sẵn sàng: {msg_form}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            continue

                        ok_snapshot, snapshot = bridge.get_open_lesson_form_snapshot()
                        if not ok_snapshot:
                            errors += 1
                            stopped = True
                            if not self._schedule_stop_reason:
                                self._schedule_stop_reason = "không đọc được popup KHDH để verify"
                            _emit_slot_result(
                                "error_verify_popup",
                                lop_text,
                                tuan_num,
                                row_info,
                                str(snapshot),
                                row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Không đọc được popup để verify: {snapshot}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            break

                        matched, issues, soft_issues, popup_values = _verify_snapshot_matches_row(snapshot, row_info)
                        popup_ppct = popup_values.get("ppct", "")
                        if not matched:
                            errors += 1
                            stopped = True
                            if not self._schedule_stop_reason:
                                self._schedule_stop_reason = "popup mở sai row KHDH"
                            error_message = "Popup không khớp row KHDH: " + "; ".join(issues)
                            _emit_slot_result(
                                "error_verify_popup",
                                lop_text,
                                tuan_num,
                                row_info,
                                error_message,
                                popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: {error_message} | Dừng để tránh nhập nhầm row", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            break

                        if soft_issues:
                            q.put((
                                "log",
                                f"⚠ {slot_label}: hint row đỏ lệch popup KHDH ({'; '.join(soft_issues)}). "
                                "App sẽ ưu tiên dữ liệu popup đang mở để tránh nhập sai.",
                                "warning",
                            ))

                        target_mon_hoc_id = str(
                            popup_values.get("mon_hoc_id")
                            or row_info.get("mon_hoc_id", "")
                            or ""
                        ).strip()
                        target_phan_mon_id = str(
                            popup_values.get("phan_mon_id")
                            or row_info.get("phan_mon_id", "")
                            or ""
                        ).strip()
                        target_ppct = str(
                            popup_values.get("ppct_raw")
                            or popup_values.get("ppct")
                            or row_info.get("ppct_hint", "")
                            or ""
                        ).strip()
                        target_noi_dung = str(
                            popup_values.get("noi_dung")
                            or row_info.get("noi_dung_hint", "")
                            or ""
                        ).strip()
                        target_mon_hoc_text = str(
                            popup_values.get("mon_hoc_text")
                            or row_info.get("mon_hoc_text_hint")
                            or row_info.get("mon_hoc_hint")
                            or ""
                        ).strip()
                        target_phan_mon_text = str(
                            popup_values.get("phan_mon_text")
                            or row_info.get("phan_mon_text_hint", "")
                            or ""
                        ).strip()

                        missing_fields = []
                        if not target_ppct:
                            missing_fields.append("PPCT")
                        if not target_noi_dung:
                            missing_fields.append("nội dung")
                        if missing_fields:
                            errors += 1
                            error_message = (
                                "Row KHDH thiếu dữ liệu để fill popup: "
                                + ", ".join(missing_fields)
                            )
                            _emit_slot_result(
                                "error_khdh_missing_data",
                                lop_text,
                                tuan_num,
                                row_info,
                                error_message,
                                popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: {error_message}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            continue

                        nhan_xet = random.choice(nhan_xet_items)
                        popup_has_khdh_payload = bool(
                            str(
                                popup_values.get("ppct_raw")
                                or popup_values.get("ppct")
                                or ""
                            ).strip()
                            and str(popup_values.get("noi_dung") or "").strip()
                        )
                        if popup_has_khdh_payload:
                            q.put(("log",
                                   f"📝 {slot_label}: giữ dữ liệu popup KHDH | MH={target_mon_hoc_text or target_mon_hoc_id or '--'}, PPCT={target_ppct}, NX={nhan_xet[:30]}...",
                                   "info"))
                            ok_fill, msg_fill = bridge.fill_form_minimal(
                                hs_nghi=hs_nghi,
                                nhan_xet=nhan_xet,
                                diem=diem,
                            )
                            q.put(("log", f"   📋 Fill KHDH tối thiểu: {msg_fill}", "info"))
                        else:
                            q.put(("log",
                                   f"📝 {slot_label}: fallback fill KHDH từ hint row | MH={target_mon_hoc_id or '--'}, PM={target_phan_mon_id or '--'}, PPCT={target_ppct}, NX={nhan_xet[:30]}...",
                                   "info"))
                            ok_fill, msg_fill = bridge.fill_form(
                                ppct=target_ppct,
                                hs_nghi=hs_nghi,
                                nhan_xet=nhan_xet,
                                diem=diem,
                                phan_mon_index=target_phan_mon_id or None,
                                mon_hoc_index=target_mon_hoc_id or None,
                                phan_mon_text=target_phan_mon_text or None,
                                mon_hoc_text=target_mon_hoc_text or None,
                                mon_hoc_field="mon_hoc_id",
                                noi_dung=target_noi_dung,
                            )
                            q.put(("log", f"   📋 Fill KHDH fallback: {msg_fill}", "info"))
                        if not ok_fill:
                            errors += 1
                            _emit_slot_result(
                                "error_fill",
                                lop_text,
                                tuan_num,
                                row_info,
                                f"Fill KHDH lỗi: {msg_fill}",
                                popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log", f"❌ {slot_label}: Fill KHDH lỗi: {msg_fill}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                            except Exception:
                                pass
                            continue

                        bridge.wait_for_form_ready_to_save(timeout_s=0.8, poll_interval=0.06)
                        ok_save, msg_save, save_meta = bridge.save_form_auto(
                            buoi_hoc=row_info.get("buoi", ""),
                        )
                        if save_meta.get("fallback_used"):
                            q.put((
                                "log",
                                "   ↩ API save không dùng được, đã fallback sang click-save",
                                "warning",
                            ))

                        ok_commit = False
                        commit_msg = ""
                        _saved_row = None
                        if ok_save:
                            ok_commit = True
                            commit_msg = msg_save
                        else:
                            ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data_fetch(
                                lop_text,
                                tuan_num,
                                row_info.get("thu", ""),
                                row_info.get("buoi", ""),
                                row_info.get("tiet", ""),
                                timeout_s=4.5,
                                poll_interval=0.3,
                            )
                            if not ok_commit:
                                ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data(
                                    row_info.get("thu", ""),
                                    row_info.get("buoi", ""),
                                    row_info.get("tiet", ""),
                                    timeout_s=3.0,
                                    poll_interval=0.25,
                                )

                        if ok_commit:
                            completed += 1
                            popup_ppct_int = None
                            try:
                                popup_ppct_int = int(str(target_ppct or popup_ppct or row_ppct_hint or "").strip())
                            except Exception:
                                popup_ppct_int = None
                            if popup_ppct_int is not None:
                                last_success_ppct = popup_ppct_int
                                q.put(("ppct_sync", last_success_ppct, None))
                            if not ok_save:
                                q.put(("log",
                                       f"⚠ {slot_label}: Save báo lỗi nhưng bảng đã cập nhật ({commit_msg})",
                                       "warning"))
                                try:
                                    bridge.close_form()
                                    bridge.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                                except Exception:
                                    pass
                            _emit_slot_result(
                                "success",
                                lop_text,
                                tuan_num,
                                row_info,
                                "Đã lưu thành công",
                                target_ppct or popup_ppct or row_ppct_hint or None,
                            )
                            q.put(("log",
                                   f"✅ {slot_label} | KHBD/KHDH PPCT={target_ppct or popup_ppct or row_ppct_hint or '--'}: OK",
                                   "success"))
                        else:
                            errors += 1
                            error_message = (
                                f"Save lỗi: {msg_save}"
                                + (f" | Verify: {commit_msg}" if commit_msg else "")
                            )
                            request_sent = bool(save_meta.get("request_sent", False))
                            if request_sent:
                                stopped = True
                                if not self._schedule_stop_reason:
                                    self._schedule_stop_reason = "save KHBD/KHDH mơ hồ cần xác minh"
                                _emit_slot_result(
                                    "error_save_ambiguous",
                                    lop_text,
                                    tuan_num,
                                    row_info,
                                    error_message,
                                    popup_ppct or row_ppct_hint or None,
                                )
                                q.put(("log",
                                       f"❌ {slot_label}: {error_message} | Dừng để tránh lệch checkpoint",
                                       "error"))
                            else:
                                _emit_slot_result(
                                    "error_save",
                                    lop_text,
                                    tuan_num,
                                    row_info,
                                    error_message,
                                    popup_ppct or row_ppct_hint or None,
                                )
                                q.put(("log", f"❌ {slot_label}: {error_message}", "error"))
                            try:
                                bridge.close_form()
                                bridge.wait_for_lesson_form_closed(timeout_s=1.2, poll_interval=0.06)
                            except Exception:
                                pass

                        if stopped:
                            break

                        next_row_idx = red_idx + 1
                        if next_row_idx < len(khdh_rows):
                            checkpoint_state = _build_checkpoint(lop_idx, tuan_num, next_row_idx)
                        else:
                            checkpoint_state = _next_checkpoint_after_week(lop_idx, tuan_num)
                        q.put(("checkpoint", checkpoint_state))
                        q.put((
                            "progress",
                            f"Lớp {lop_text} | {tuan_text} | {slot_label}",
                            completed + skipped + errors,
                        ))

                    q.put(("log",
                           f"📅 Lớp {lop_text} | {tuan_text} xong (KHDH): "
                           f"{completed} nhập, {skipped} skip, {errors} lỗi",
                           "info"))
                    if stopped:
                        break

        except Exception as e:
            errors += 1
            stopped = True
            q.put(("error",
                   f"Schedule worker KHDH exception: {type(e).__name__}: "
                   f"{str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=True)
                    q.put((
                        "log",
                        f"Cleanup sau KHDH: {msg_cleanup}" if ok_cleanup else f"Cleanup sau KHDH lỗi: {msg_cleanup}",
                        "info" if ok_cleanup else "warning",
                    ))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass
            has_remaining = (
                int(checkpoint_state.get("next_lop_idx", len(lop_list)) or 0) < len(lop_list)
                if checkpoint_state else False
            )
            resume_payload = copy.deepcopy(checkpoint_state) if (stopped and has_remaining) else None
            q.put(("done", {
                "completed": completed,
                "skipped": skipped,
                "errors": errors,
                "stopped": bool(stopped and has_remaining),
                "stop_reason": self._schedule_stop_reason,
                "resume_state": resume_payload,
                "last_success_ppct": last_success_ppct,
                "next_ppct": None,
            }))

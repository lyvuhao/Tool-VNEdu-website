"""Worker nhập theo lịch (thủ công) và kết thúc phiên."""

import copy
import random
import time

from ..cdp.bridge import ChromeBridge
from ..cdp.health import is_cdp_target_closed_error


class ScheduleWorkerMixin:
    """Worker nhập theo lịch (thủ công) và kết thúc phiên."""

    def _schedule_worker(self, params):
        """Worker thread schedule với checkpoint resume và báo cáo chi tiết từng slot."""
        bridge = None
        q = self._schedule_queue
        slots = params["slots"]
        tuan_from = int(params["tuan_from"])
        tuan_to = int(params["tuan_to"])
        lop_text = params["lop"]
        resume_state = params.get("resume_state") or {}
        completed = int(resume_state.get("completed", 0) or 0)
        skipped = int(resume_state.get("skipped", 0) or 0)
        errors = int(resume_state.get("errors", 0) or 0)
        ppct_counter = int(
            resume_state.get("next_ppct", params["ppct_start"]) or params["ppct_start"]
        )
        last_success_ppct = resume_state.get("last_success_ppct")
        start_tuan_num = int(resume_state.get("next_tuan_num", tuan_from) or tuan_from)
        start_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
        stopped = False
        checkpoint_state = {
            "next_tuan_num": start_tuan_num,
            "next_slot_idx": start_slot_idx,
            "next_ppct": ppct_counter,
            "completed": completed,
            "skipped": skipped,
            "errors": errors,
            "last_success_ppct": last_success_ppct,
        }

        hs_nghi = params["hs_nghi"]
        diem = params["diem"]
        nhan_xet_raw = params["nhan_xet_raw"]
        phan_mon_value = params.get("phan_mon_value")
        mon_hoc_value = params.get("mon_hoc_value")
        phan_mon_text = params.get("phan_mon_text")
        mon_hoc_text = params.get("mon_hoc_text")
        mon_hoc_field = params.get("mon_hoc_field")

        nhan_xet_items = [x.strip() for x in nhan_xet_raw.split("|") if x.strip()]
        if not nhan_xet_items:
            nhan_xet_items = ["Lớp học chăm ngoan"]

        def _build_checkpoint(next_tuan_num, next_slot_idx):
            return {
                "next_tuan_num": int(next_tuan_num),
                "next_slot_idx": int(next_slot_idx),
                "next_ppct": int(ppct_counter),
                "completed": int(completed),
                "skipped": int(skipped),
                "errors": int(errors),
                "last_success_ppct": last_success_ppct,
            }

        def _next_position(tuan_num, slot_idx):
            if slot_idx + 1 < len(slots):
                return tuan_num, slot_idx + 1
            return tuan_num + 1, 0

        def _emit_slot_result(status, tuan_num, slot, message, ppct_value=None):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": self._format_schedule_slot_label(
                    slot.get("thu", "?"),
                    slot.get("buoi", "?"),
                    slot.get("tiet", "?"),
                ),
                "ppct": ppct_value,
                "message": message,
            }))

        def _emit_system_result(status, tuan_num, message):
            q.put(("slot_result", {
                "status": status,
                "week_num": int(tuan_num),
                "week_text": f"Tuần {tuan_num}",
                "slot_label": "Khởi tạo tuần/lớp",
                "ppct": None,
                "message": message,
            }))

        week_table_rows = []
        week_row_lookup = {}

        def _slot_key(thu, buoi, tiet):
            # Chuẩn hoá thu/buoi về token thống nhất (CN/8, Sáng/Chiều có dấu)
            # để slot từ UI khớp đúng row từ read_table, kể cả Chủ nhật.
            return (
                ChromeBridge._normalize_thu_token(thu),
                ChromeBridge._normalize_buoi_token(buoi),
                str(tiet).strip(),
            )

        def _set_week_snapshot(rows):
            nonlocal week_table_rows, week_row_lookup
            week_table_rows = list(rows or [])
            week_row_lookup = {}
            for row in week_table_rows:
                key = _slot_key(row.get("thu", ""), row.get("buoi", ""), row.get("tiet", ""))
                week_row_lookup[key] = row

        def _refresh_week_snapshot():
            ok_table, table_data = bridge.read_table()
            if ok_table:
                _set_week_snapshot(table_data)
            return ok_table, table_data

        def _get_cached_row(slot, refresh_if_missing=True):
            key = _slot_key(slot.get("thu", ""), slot.get("buoi", ""), slot.get("tiet", ""))
            matched = week_row_lookup.get(key)
            if matched is None and refresh_if_missing:
                ok_table, table_data = _refresh_week_snapshot()
                if not ok_table:
                    return None, f"Không đọc được bảng: {table_data}"
                matched = week_row_lookup.get(key)
            return matched, None

        def _mark_cached_slot_saved(slot, ppct_value, saved_row=None):
            key = _slot_key(slot.get("thu", ""), slot.get("buoi", ""), slot.get("tiet", ""))
            if saved_row:
                week_row_lookup[key] = saved_row
                return
            matched = week_row_lookup.get(key)
            if matched is None:
                return
            matched["has_data"] = True
            matched["ppct"] = str(ppct_value)
            if mon_hoc_text and phan_mon_text:
                matched["mon_hoc"] = f"{mon_hoc_text} ({phan_mon_text})"
            elif mon_hoc_text:
                matched["mon_hoc"] = str(mon_hoc_text)

        def _mark_cdp_closed_stop(tuan_num, slot_idx, message):
            nonlocal errors, stopped, checkpoint_state
            errors += 1
            stopped = True
            checkpoint_state = _build_checkpoint(tuan_num, slot_idx)
            if not self._schedule_stop_reason:
                self._schedule_stop_reason = "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng"
            q.put(("checkpoint", checkpoint_state))
            q.put(("error", (
                "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
                f"Worker schedule dừng tại Tuần {tuan_num}. Chi tiết: {message}"
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

        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("error", f"Kết nối CDP thất bại: {msg}"))
                errors += 1
            else:
                bridge.setup_dialog_auto_accept()
                q.put(("log", "CDP connected (schedule worker)", "success"))

                for tuan_num in range(start_tuan_num, tuan_to + 1):
                    slot_begin_idx = start_slot_idx if tuan_num == start_tuan_num else 0
                    if self._schedule_stop_event.is_set():
                        stopped = True
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        break

                    tuan_text = f"Tuần {tuan_num}"
                    q.put(("log", f"📅 Chuyển đến {tuan_text}...", "info"))
                    ok, msg = _select_dropdown_retry("tuan", tuan_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(tuan_num, slot_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        _emit_system_result("error_select_tuan", tuan_num, f"Lỗi chọn tuần: {msg}")
                        q.put(("log", f"❌ Lỗi chọn {tuan_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log", f"🏫 Chọn Lớp {lop_text}...", "info"))
                    ok, msg = _select_dropdown_retry("lop", lop_text, attempts=3)
                    if not ok:
                        if is_cdp_target_closed_error(msg):
                            _mark_cdp_closed_stop(tuan_num, slot_begin_idx, msg)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        _emit_system_result("error_select_lop", tuan_num, f"Lỗi chọn lớp {lop_text}: {msg}")
                        q.put(("log", f"❌ Lỗi chọn Lớp {lop_text}: {msg}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    ok, table_data = _refresh_week_snapshot()
                    if not ok:
                        if is_cdp_target_closed_error(table_data):
                            _mark_cdp_closed_stop(tuan_num, slot_begin_idx, table_data)
                            break
                        errors += 1
                        checkpoint_state = _build_checkpoint(tuan_num, slot_begin_idx)
                        _emit_system_result(
                            "error_read_week_table",
                            tuan_num,
                            f"Lỗi đọc bảng tuần: {table_data}",
                        )
                        q.put(("log", f"❌ {tuan_text}: Lỗi đọc bảng tuần: {table_data}", "error"))
                        q.put(("checkpoint", checkpoint_state))
                        continue

                    q.put(("log",
                           f"📊 Bắt đầu quét {tuan_text} — {lop_text} "
                           f"({len(slots)} slots, {len(week_table_rows)} rows)...", "info"))

                    for slot_idx in range(slot_begin_idx, len(slots)):
                        if self._schedule_stop_event.is_set():
                            stopped = True
                            checkpoint_state = _build_checkpoint(tuan_num, slot_idx)
                            break

                        slot = slots[slot_idx]
                        s_thu = slot["thu"]
                        s_buoi = slot["buoi"]
                        s_tiet = slot["tiet"]
                        slot_label = self._format_schedule_slot_label(s_thu, s_buoi, s_tiet)
                        current_ppct = ppct_counter
                        checkpoint_state = _build_checkpoint(tuan_num, slot_idx)
                        q.put(("checkpoint", checkpoint_state))

                        matched_row, cache_error = _get_cached_row(slot, refresh_if_missing=True)
                        if cache_error:
                            errors += 1
                            _emit_slot_result(
                                "error_read_table",
                                tuan_num,
                                slot,
                                cache_error,
                                current_ppct,
                            )
                            q.put(("log", f"❌ {slot_label}: {cache_error}", "error"))
                        else:
                            if matched_row is None:
                                errors += 1
                                _emit_slot_result(
                                    "error_not_found",
                                    tuan_num,
                                    slot,
                                    "Không tìm thấy row trên bảng VnEdu",
                                    current_ppct,
                                )
                                q.put(("log", f"❌ {slot_label}: Không tìm thấy row", "error"))
                            else:
                                row_error_emitted = False
                                row_index = matched_row.get("add_btn_index", -1)
                                row_dom_index = matched_row.get("rowIdx")
                                has_data = bool(matched_row.get("has_data", False))
                                has_add_btn = bool(matched_row.get("has_add_btn", False))

                                if (
                                    not has_data and
                                    (
                                        not has_add_btn or
                                        (row_index < 0 and (row_dom_index is None or row_dom_index < 0))
                                    )
                                ):
                                    ok_refresh, refresh_data = _refresh_week_snapshot()
                                    if not ok_refresh:
                                        errors += 1
                                        _emit_slot_result(
                                            "error_read_table",
                                            tuan_num,
                                            slot,
                                            f"Không đọc được bảng sau refresh: {refresh_data}",
                                            current_ppct,
                                        )
                                        q.put((
                                            "log",
                                            f"❌ {slot_label}: Không đọc được bảng sau refresh: {refresh_data}",
                                            "error",
                                        ))
                                        row_error_emitted = True
                                        matched_row = None
                                    else:
                                        matched_row = week_row_lookup.get(
                                            _slot_key(s_thu, s_buoi, s_tiet)
                                        )
                                        if matched_row is not None:
                                            row_index = matched_row.get("add_btn_index", -1)
                                            row_dom_index = matched_row.get("rowIdx")
                                            has_data = bool(matched_row.get("has_data", False))
                                            has_add_btn = bool(matched_row.get("has_add_btn", False))

                                if matched_row is None:
                                    if not row_error_emitted:
                                        errors += 1
                                        _emit_slot_result(
                                            "error_not_found",
                                            tuan_num,
                                            slot,
                                            "Không tìm thấy row sau khi refresh bảng",
                                            current_ppct,
                                        )
                                        q.put((
                                            "log",
                                            f"❌ {slot_label}: Không tìm thấy row sau khi refresh bảng",
                                            "error",
                                        ))
                                elif has_data:
                                    existing_ppct_value = self._extract_existing_ppct_value(matched_row)
                                    if existing_ppct_value is None:
                                        errors += 1
                                        stopped = True
                                        if not self._schedule_stop_reason:
                                            self._schedule_stop_reason = (
                                                "row đã có dữ liệu nhưng không đọc được PPCT"
                                            )
                                        _emit_slot_result(
                                            "error_existing_ppct",
                                            tuan_num,
                                            slot,
                                            "Row đã có dữ liệu nhưng không đọc được PPCT hiện có",
                                            current_ppct,
                                        )
                                        q.put((
                                            "log",
                                            f"❌ {slot_label}: Row đã có dữ liệu nhưng không đọc được PPCT hiện có",
                                            "error",
                                        ))
                                    else:
                                        skipped += 1
                                        if existing_ppct_value >= current_ppct:
                                            last_success_ppct = existing_ppct_value
                                            ppct_counter = existing_ppct_value + 1
                                            q.put(("ppct_sync", last_success_ppct, ppct_counter))
                                        _emit_slot_result(
                                            "skipped_existing",
                                            tuan_num,
                                            slot,
                                            f"Đã có dữ liệu — PPCT hiện có {existing_ppct_value}",
                                            existing_ppct_value,
                                        )
                                        q.put((
                                            "log",
                                            f"⏭ {slot_label}: Đã có dữ liệu — PPCT hiện có {existing_ppct_value}",
                                            "info",
                                        ))
                                elif not has_add_btn:
                                    errors += 1
                                    _emit_slot_result(
                                        "error_no_add_button",
                                        tuan_num,
                                        slot,
                                        "Không có nút + để mở form",
                                        current_ppct,
                                    )
                                    q.put(("log", f"❌ {slot_label}: Không có nút +", "error"))
                                elif row_index < 0 and (row_dom_index is None or row_dom_index < 0):
                                    errors += 1
                                    _emit_slot_result(
                                        "error_row_index",
                                        tuan_num,
                                        slot,
                                        "Row không có add_btn_index/rowIdx hợp lệ",
                                        current_ppct,
                                    )
                                    q.put((
                                        "log",
                                        f"❌ {slot_label}: Row không có add_btn_index/rowIdx hợp lệ",
                                        "error",
                                    ))
                                else:
                                    q.put((
                                        "progress",
                                        f"{tuan_text} | {slot_label} | PPCT {current_ppct}",
                                        completed + skipped + errors,
                                    ))
                                    ok, msg = bridge.click_add_button(
                                        row_index=row_index,
                                        row_dom_index=row_dom_index,
                                    )
                                    if not ok:
                                        errors += 1
                                        _emit_slot_result(
                                            "error_click_add",
                                            tuan_num,
                                            slot,
                                            f"Click + thất bại: {msg}",
                                            current_ppct,
                                        )
                                        q.put(("log",
                                               f"❌ {slot_label}: Click + thất bại: {msg}",
                                               "error"))
                                    else:
                                        ok_form, msg_form = bridge.wait_for_lesson_form(
                                            timeout_s=4.0,
                                            poll_interval=0.12,
                                        )
                                        if not ok_form:
                                            errors += 1
                                            _emit_slot_result(
                                                "error_open_form",
                                                tuan_num,
                                                slot,
                                                f"Form chưa mở sẵn sàng: {msg_form}",
                                                current_ppct,
                                            )
                                            q.put((
                                                "log",
                                                f"❌ {slot_label}: Form chưa mở sẵn sàng: {msg_form}",
                                                "error",
                                            ))
                                            try:
                                                bridge.close_form()
                                                bridge.wait_for_lesson_form_closed(
                                                    timeout_s=1.0,
                                                    poll_interval=0.06,
                                                )
                                            except Exception:
                                                pass
                                            continue

                                        nhan_xet = random.choice(nhan_xet_items)
                                        q.put(("log",
                                               f"📝 {slot_label}: PPCT={current_ppct}, "
                                               f"MH={mon_hoc_value}, PM={phan_mon_value}, "
                                               f"NX={nhan_xet[:20]}...",
                                               "info"))

                                        ok_fill, msg_fill = bridge.fill_form(
                                            ppct=str(current_ppct),
                                            hs_nghi=hs_nghi,
                                            nhan_xet=nhan_xet,
                                            diem=diem,
                                            phan_mon_index=phan_mon_value,
                                            mon_hoc_index=mon_hoc_value,
                                            phan_mon_text=phan_mon_text,
                                            mon_hoc_text=mon_hoc_text,
                                            mon_hoc_field=mon_hoc_field,
                                            noi_dung=None,
                                        )
                                        q.put(("log", f"   📋 Fill: {msg_fill}", "info"))

                                        if not ok_fill:
                                            errors += 1
                                            _emit_slot_result(
                                                "error_fill",
                                                tuan_num,
                                                slot,
                                                f"Fill form lỗi: {msg_fill}",
                                                current_ppct,
                                            )
                                            q.put(("log",
                                                   f"❌ {slot_label}: Fill form lỗi: {msg_fill}",
                                                   "error"))
                                            try:
                                                bridge.close_form()
                                                bridge.wait_for_lesson_form_closed(
                                                    timeout_s=1.0,
                                                    poll_interval=0.06,
                                                )
                                            except Exception:
                                                pass
                                        else:
                                            bridge.wait_for_form_ready_to_save(
                                                timeout_s=0.8,
                                                poll_interval=0.06,
                                            )
                                            ok_save, msg_save, save_meta = bridge.save_form_auto(
                                                buoi_hoc=s_buoi,
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
                                                    s_thu,
                                                    s_buoi,
                                                    s_tiet,
                                                    timeout_s=4.5,
                                                    poll_interval=0.3,
                                                )
                                                if not ok_commit:
                                                    ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data(
                                                        s_thu,
                                                        s_buoi,
                                                        s_tiet,
                                                        timeout_s=3.0,
                                                        poll_interval=0.25,
                                                    )

                                            if ok_commit:
                                                _mark_cached_slot_saved(
                                                    slot,
                                                    current_ppct,
                                                    saved_row=_saved_row,
                                                )
                                                if not ok_save:
                                                    q.put(("log",
                                                           f"⚠ {slot_label}: Save báo lỗi nhưng bảng đã cập nhật "
                                                           f"({commit_msg})",
                                                           "warning"))
                                                    try:
                                                        bridge.close_form()
                                                        bridge.wait_for_lesson_form_closed(
                                                            timeout_s=1.0,
                                                            poll_interval=0.06,
                                                        )
                                                    except Exception:
                                                        pass

                                                completed += 1
                                                last_success_ppct = current_ppct
                                                ppct_counter = current_ppct + 1
                                                q.put(("ppct_sync", last_success_ppct, ppct_counter))
                                                _emit_slot_result(
                                                    "success",
                                                    tuan_num,
                                                    slot,
                                                    "Đã lưu thành công",
                                                    current_ppct,
                                                )
                                                q.put(("log",
                                                       f"✅ {slot_label} PPCT={current_ppct}: OK",
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
                                                        self._schedule_stop_reason = "save mơ hồ cần xác minh"
                                                    _emit_slot_result(
                                                        "error_save_ambiguous",
                                                        tuan_num,
                                                        slot,
                                                        error_message,
                                                        current_ppct,
                                                    )
                                                    q.put(("log",
                                                           f"❌ {slot_label}: {error_message} | Dừng để tránh lệch checkpoint",
                                                           "error"))
                                                else:
                                                    _emit_slot_result(
                                                        "error_save",
                                                        tuan_num,
                                                        slot,
                                                        error_message,
                                                        current_ppct,
                                                    )
                                                    q.put(("log",
                                                           f"❌ {slot_label}: {error_message}",
                                                           "error"))
                                                try:
                                                    bridge.close_form()
                                                    bridge.wait_for_lesson_form_closed(
                                                        timeout_s=1.2,
                                                        poll_interval=0.06,
                                                    )
                                                except Exception:
                                                    pass

                        if stopped:
                            break

                        next_tuan_num, next_slot_idx = _next_position(tuan_num, slot_idx)
                        checkpoint_state = _build_checkpoint(next_tuan_num, next_slot_idx)
                        q.put(("checkpoint", checkpoint_state))
                        q.put((
                            "progress",
                            f"{tuan_text} | {slot_label}",
                            completed + skipped + errors,
                        ))

                    q.put(("log",
                           f"📅 {tuan_text} xong: {completed} nhập, "
                           f"{skipped} skip, {errors} lỗi",
                           "info"))

        except Exception as e:
            errors += 1
            stopped = True
            q.put(("error",
                   f"Schedule worker exception: {type(e).__name__}: "
                   f"{str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    ok_cleanup, msg_cleanup = bridge.cleanup_after_automation(restore_view_mode=False)
                    q.put((
                        "log",
                        f"Cleanup sau schedule: {msg_cleanup}" if ok_cleanup else f"Cleanup sau schedule lỗi: {msg_cleanup}",
                        "info" if ok_cleanup else "warning",
                    ))
                except Exception:
                    pass
                try:
                    bridge.disconnect()
                except Exception:
                    pass
            has_remaining = (
                checkpoint_state.get("next_tuan_num", tuan_to + 1) <= tuan_to
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
                "next_ppct": resume_payload.get("next_ppct") if resume_payload else ppct_counter,
            }))

    def _poll_schedule_queue(self):
        """Poll schedule queue cho progress updates (chạy trong UI thread)."""
        try:
            while not self._schedule_queue.empty():
                msg = self._schedule_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "log":
                    self._log(msg[1], msg[2] if len(msg) > 2 else "info")

                elif msg_type == "progress":
                    text = msg[1]
                    count = msg[2] if len(msg) > 2 else 0
                    self.lbl_sched_progress.config(text=f"⏳ {text}")
                    current_total = int(float(self.sched_progressbar["maximum"] or 1))
                    self._set_sched_live_progress(
                        current=count,
                        total=current_total,
                        phase="Schedule",
                        detail=text,
                        state="running",
                    )

                elif msg_type == "progress_total":
                    try:
                        self.sched_progressbar["maximum"] = max(
                            int(msg[1]),
                            int(float(self.sched_progressbar["maximum"] or 1)),
                        )
                    except Exception:
                        pass

                elif msg_type == "ppct_sync":
                    self._update_sched_ppct_runtime(
                        last_success=msg[1],
                        next_ppct=msg[2] if len(msg) > 2 else None,
                        status="running",
                    )

                elif msg_type == "slot_result":
                    self._schedule_results.append(msg[1])

                elif msg_type == "checkpoint":
                    self._update_schedule_resume_snapshot(msg[1], persist=True)

                elif msg_type == "error":
                    self._log(msg[1], "error")

                elif msg_type == "done":
                    self._schedule_finished(msg[1])
                    return

        except Exception as e:
            print(f"[SCHED_POLL] Error: {e}")

        if self._schedule_running and self._root_exists():
            self.root.after(150, self._poll_schedule_queue)

    def _schedule_finished(self, summary):
        """Xử lý khi schedule hoàn tất hoặc tạm dừng."""
        completed = int(summary.get("completed", 0) or 0)
        skipped_count = int(summary.get("skipped", 0) or 0)
        error_count = int(summary.get("errors", 0) or 0)
        resume_state = summary.get("resume_state")
        stopped = bool(summary.get("stopped"))
        next_ppct = summary.get("next_ppct")
        last_success_ppct = summary.get("last_success_ppct")

        self._schedule_running = False
        self._update_schedule_resume_snapshot(
            resume_state=resume_state,
            params=self._schedule_resume_params if resume_state else None,
            persist=True,
        )
        self._set_schedule_button_states(
            running=False,
            can_resume=bool(self._schedule_resume_state),
        )
        self._set_auto_login_button_state()

        processed_count = completed + skipped_count + error_count
        if stopped:
            self.sched_progressbar["value"] = min(
                processed_count, self.sched_progressbar["maximum"]
            )
            self.lbl_sched_progress.config(
                text=(
                    f"⏸ Tạm dừng: {completed} nhập, {skipped_count} skip, "
                    f"{error_count} lỗi"
                )
            )
            self._update_sched_ppct_runtime(
                last_success=last_success_ppct,
                next_ppct=next_ppct,
                status="paused",
            )
            self._log(
                f"⏸ Schedule tạm dừng: {completed} thành công, "
                f"{skipped_count} đã có, {error_count} lỗi",
                "warning",
            )
            self._set_sched_live_progress(
                current=processed_count,
                total=int(float(self.sched_progressbar["maximum"] or 1)),
                phase="Schedule",
                detail="Đã tạm dừng an toàn, có thể bấm Tiếp tục từ checkpoint gần nhất",
                state="paused",
            )
        else:
            self.sched_progressbar["value"] = self.sched_progressbar["maximum"]
            self.lbl_sched_progress.config(
                text=(
                    f"✅ Hoàn tất: {completed} nhập, {skipped_count} skip, "
                    f"{error_count} lỗi"
                )
            )
            self._update_sched_ppct_runtime(
                last_success=last_success_ppct,
                next_ppct=next_ppct,
                status="done",
            )
            self._log(
                f"🏁 Schedule hoàn tất: {completed} thành công, "
                f"{skipped_count} đã có, {error_count} lỗi",
                "success" if error_count == 0 else "warning",
            )
            self._set_sched_live_progress(
                current=processed_count,
                total=int(float(self.sched_progressbar["maximum"] or 1)),
                phase="Schedule",
                detail="Đã xử lý hết các slot trong kế hoạch hiện tại",
                state="success" if error_count == 0 else "error",
            )

        pending_items = self._build_pending_schedule_items(
            self._schedule_resume_params,
            self._schedule_resume_state,
        )
        summary_payload = {
            **summary,
            "results": list(self._schedule_results),
            "pending_items": pending_items,
        }
        self._schedule_last_summary = summary_payload
        if completed > 0:
            self._invalidate_class_stats_cache()
            self._on_sched_progress_context_changed()
        if self._closing:
            return
        self._show_schedule_summary(summary_payload)

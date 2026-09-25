"""Worker nhập theo lịch ở chế độ KHDH.

`_schedule_worker_khdh` chạy trong thread nền; toàn bộ logic nằm ở `KhdhScheduleJob`:

    run()                       kết nối CDP -> _process_all_weeks() -> _finish() (dọn dẹp + gửi "done")
    _process_week()             chọn tuần -> lọc lớp -> chọn lớp -> bật "Gợi ý theo KHDH" -> đọc hàng đỏ
    _process_row()              một hàng đỏ: mở form -> đọc & kiểm tra popup -> điền -> lưu & xác nhận
        _open_row_form()
        _read_verified_popup()
        _fill_row_form()
        _save_and_confirm_row()

Mọi sự kiện gửi về giao diện qua `app._schedule_queue` ("log", "checkpoint", "progress", "slot_result",
"ppct_sync", "error", "done") giữ nguyên như trước khi tách.
"""

import copy
import random
import time

from ..cdp.bridge import ChromeBridge
from ..cdp.health import is_cdp_target_closed_error
from .khdh_rows import (
    as_index,
    build_work_items,
    digits,
    missing_fill_fields,
    parse_nhan_xet_items,
    popup_has_khdh_payload,
    resolve_fill_targets,
    row_resume_key,
    verify_snapshot_matches_row,
)

# Kết quả xử lý một hàng đỏ (thay cho continue/break trong vòng lặp cũ)
ROW_SKIP = "skip"    # sang hàng kế, KHÔNG cập nhật checkpoint "hàng kế tiếp"
ROW_STOP = "stop"    # dừng tuần này và dừng cả lần chạy
ROW_DONE = "done"    # đã xử lý xong (thành công hoặc lỗi lưu) -> cập nhật checkpoint hàng kế tiếp


class ScheduleWorkerKHDHMixin:
    """Worker nhập theo lịch ở chế độ KHDH."""

    def _schedule_worker_khdh(self, params):
        """Worker thread cho mode KHDH: quét row đỏ live rồi fill theo dữ liệu gợi ý."""
        KhdhScheduleJob(self, params).run()


class _RowContext:
    """Thông tin của hàng đỏ đang xử lý."""

    __slots__ = ("lop_idx", "lop_text", "tuan_num", "row_info", "slot_label", "row_ppct_hint")

    def __init__(self, lop_idx, lop_text, tuan_num, row_info, slot_label, row_ppct_hint):
        self.lop_idx = lop_idx
        self.lop_text = lop_text
        self.tuan_num = tuan_num
        self.row_info = row_info
        self.slot_label = slot_label
        self.row_ppct_hint = row_ppct_hint


class KhdhScheduleJob:
    """Một lần chạy nhập Sổ đầu bài theo KHDH: tham số, bộ đếm và checkpoint để chạy tiếp."""

    def __init__(self, app, params):
        self.app = app
        self.params = params
        self.bridge = None
        self.q = app._schedule_queue
        self.tuan_from = int(params["tuan_from"])
        self.tuan_to = int(params["tuan_to"])
        lop_list = [
            str(item or "").strip()
            for item in list(params.get("lop_list") or [params.get("lop")])
            if str(item or "").strip()
        ]
        if not lop_list:
            lop_list = [str(params.get("lop", "")).strip()]
        self.lop_list = lop_list
        resume_state = params.get("resume_state") or {}
        self.completed = int(resume_state.get("completed", 0) or 0)
        self.skipped = int(resume_state.get("skipped", 0) or 0)
        self.errors = int(resume_state.get("errors", 0) or 0)
        self.last_success_ppct = resume_state.get("last_success_ppct")
        self.start_lop_idx = int(resume_state.get("next_lop_idx", 0) or 0)
        self.start_tuan_num = int(resume_state.get("next_tuan_num", self.tuan_from) or self.tuan_from)
        self.start_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
        self.start_row_key = str(resume_state.get("next_row_key", "") or "").strip()
        self.stopped = False
        self.checkpoint_state = {
            "next_lop_idx": self.start_lop_idx,
            "next_tuan_num": self.start_tuan_num,
            "next_slot_idx": self.start_slot_idx,
            "next_row_key": self.start_row_key or None,
            "next_ppct": None,
            "completed": self.completed,
            "skipped": self.skipped,
            "errors": self.errors,
            "last_success_ppct": self.last_success_ppct,
        }

        self.hs_nghi = params["hs_nghi"]
        self.diem = params["diem"]
        self.nhan_xet_items = parse_nhan_xet_items(params["nhan_xet_raw"])
        self.discovered_total = self.completed + self.skipped + self.errors
        self._week_lop_cache = {}

    # ------------------------------------------------------------------
    # Checkpoint & sự kiện gửi về giao diện
    # ------------------------------------------------------------------

    def _build_checkpoint(self, next_lop_idx, next_tuan_num, next_slot_idx, next_row_key=None):
        return {
            "next_lop_idx": int(next_lop_idx),
            "next_tuan_num": int(next_tuan_num),
            "next_slot_idx": int(next_slot_idx),
            "next_row_key": str(next_row_key or "").strip() or None,
            "next_ppct": None,
            "completed": int(self.completed),
            "skipped": int(self.skipped),
            "errors": int(self.errors),
            "last_success_ppct": self.last_success_ppct,
        }

    def _next_checkpoint_after_week(self, lop_idx, tuan_num):
        if int(tuan_num) < self.tuan_to:
            return self._build_checkpoint(lop_idx, int(tuan_num) + 1, 0, None)
        return self._build_checkpoint(int(lop_idx) + 1, self.tuan_from, 0, None)

    def _emit_slot_result(self, status, lop_text, tuan_num, row_info, message, ppct_value=None):
        self.q.put(("slot_result", {
            "status": status,
            "week_num": int(tuan_num),
            "week_text": f"Tuần {tuan_num}",
            "slot_label": (
                f"Lớp {lop_text} | "
                + self.app._format_schedule_slot_label(
                    row_info.get("thu", "?"),
                    row_info.get("buoi", "?"),
                    row_info.get("tiet", "?"),
                )
            ),
            "ppct": ppct_value,
            "message": message,
        }))

    def _emit_row_result(self, status, ctx, message, ppct_value=None):
        self._emit_slot_result(status, ctx.lop_text, ctx.tuan_num, ctx.row_info, message, ppct_value)

    def _emit_system_result(self, status, lop_text, tuan_num, message):
        self.q.put(("slot_result", {
            "status": status,
            "week_num": int(tuan_num),
            "week_text": f"Tuần {tuan_num}",
            "slot_label": f"Lớp {lop_text} | Quét row đỏ KHDH",
            "ppct": None,
            "message": message,
        }))

    def _set_stop_reason(self, reason):
        if not self.app._schedule_stop_reason:
            self.app._schedule_stop_reason = reason

    def _mark_cdp_closed_stop(self, lop_idx, tuan_num, row_idx, message):
        self.errors += 1
        self.stopped = True
        self.checkpoint_state = self._build_checkpoint(lop_idx, tuan_num, row_idx, None)
        self._set_stop_reason("CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng")
        self.q.put(("checkpoint", self.checkpoint_state))
        lop_list = self.lop_list
        self.q.put(("error", (
            "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
            f"Worker KHDH dừng tại Lớp {lop_list[lop_idx] if 0 <= lop_idx < len(lop_list) else '?'}, "
            f"Tuần {tuan_num}. Chi tiết: {message}"
        )))

    # ------------------------------------------------------------------
    # Tiện ích thao tác trình duyệt
    # ------------------------------------------------------------------

    def _select_dropdown_retry(self, label, text, attempts=3):
        last_msg = ""
        for attempt in range(max(int(attempts), 1)):
            ok_select, msg_select = self.bridge.select_dropdown(label, text)
            if ok_select:
                return True, msg_select
            last_msg = str(msg_select)
            if is_cdp_target_closed_error(last_msg):
                return False, last_msg
            if attempt < attempts - 1:
                time.sleep(0.35 + attempt * 0.35)
        return False, last_msg

    def _close_form_quietly(self, timeout_s=1.0):
        try:
            self.bridge.close_form()
            self.bridge.wait_for_lesson_form_closed(timeout_s=timeout_s, poll_interval=0.06)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Luồng chính
    # ------------------------------------------------------------------

    def run(self):
        q = self.q
        try:
            self.bridge = ChromeBridge(port=self.params["port"])
            ok, msg = self.bridge.connect()
            if not ok:
                q.put(("error", f"Kết nối CDP thất bại: {msg}"))
                self.errors += 1
            else:
                self.bridge.setup_dialog_auto_accept()
                q.put(("log", "CDP connected (KHDH worker)", "success"))
                self._process_all_weeks()
        except Exception as e:
            self.errors += 1
            self.stopped = True
            q.put(("error",
                   f"Schedule worker KHDH exception: {type(e).__name__}: "
                   f"{str(e)[:140]}"))
        finally:
            self._finish()

    def _process_all_weeks(self):
        work_items = build_work_items(
            self.lop_list, self.start_lop_idx, self.start_tuan_num, self.tuan_from, self.tuan_to
        )
        for lop_idx, lop_text, tuan_num in work_items:
            if not self._process_week(lop_idx, lop_text, tuan_num):
                break

    def _finish(self):
        """Dọn dẹp trình duyệt và gửi sự kiện "done" (kèm checkpoint nếu còn việc dang dở)."""
        q = self.q
        bridge = self.bridge
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
        checkpoint_state = self.checkpoint_state
        has_remaining = (
            int(checkpoint_state.get("next_lop_idx", len(self.lop_list)) or 0) < len(self.lop_list)
            if checkpoint_state else False
        )
        resume_payload = copy.deepcopy(checkpoint_state) if (self.stopped and has_remaining) else None
        q.put(("done", {
            "completed": self.completed,
            "skipped": self.skipped,
            "errors": self.errors,
            "stopped": bool(self.stopped and has_remaining),
            "stop_reason": self.app._schedule_stop_reason,
            "resume_state": resume_payload,
            "last_success_ppct": self.last_success_ppct,
            "next_ppct": None,
        }))

    # ------------------------------------------------------------------
    # Một tuần của một lớp
    # ------------------------------------------------------------------

    def _week_step_failed(self, lop_idx, lop_text, tuan_num, row_begin_idx, anchor_key,
                          raw_error, status, system_message, log_message):
        """Một bước chuẩn bị tuần bị lỗi. Returns True = sang tuần kế, False = dừng hẳn (Chrome đã đóng)."""
        if is_cdp_target_closed_error(raw_error):
            self._mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, raw_error)
            return False
        self.errors += 1
        self.checkpoint_state = self._build_checkpoint(lop_idx, tuan_num, row_begin_idx, anchor_key)
        self._emit_system_result(status, lop_text, tuan_num, system_message)
        self.q.put(("log", log_message, "error"))
        self.q.put(("checkpoint", self.checkpoint_state))
        return True

    def _available_lop_keys(self, lop_idx, tuan_num, tuan_text, row_begin_idx):
        """Lớp có trong dropdown của tuần (cache theo tuần). Returns (tiếp_tục, tập_lớp_hoặc_None)."""
        available_lop_keys = self._week_lop_cache.get(tuan_num)
        if available_lop_keys is None:
            ok_lops, lops_or_error = self.bridge.get_lop_options()
            if not ok_lops:
                if is_cdp_target_closed_error(lops_or_error):
                    self._mark_cdp_closed_stop(lop_idx, tuan_num, row_begin_idx, lops_or_error)
                    return False, None
                self.q.put((
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
                self._week_lop_cache[tuan_num] = available_lop_keys
        return True, available_lop_keys

    def _process_week(self, lop_idx, lop_text, tuan_num):
        """Returns True = sang tuần/lớp kế tiếp, False = dừng lần chạy."""
        q = self.q
        is_resume_anchor = (
            lop_idx == self.start_lop_idx and tuan_num == self.start_tuan_num
        )
        row_begin_idx = self.start_slot_idx if is_resume_anchor else 0
        anchor_key = self.start_row_key if is_resume_anchor else None
        if self.app._schedule_stop_event.is_set():
            self.stopped = True
            self.checkpoint_state = self._build_checkpoint(lop_idx, tuan_num, row_begin_idx, anchor_key)
            return False

        tuan_text = f"Tuần {tuan_num}"
        q.put(("log", f"📅 Chuyển đến {tuan_text}...", "info"))
        ok, msg = self._select_dropdown_retry("tuan", tuan_text, attempts=3)
        if not ok:
            return self._week_step_failed(
                lop_idx, lop_text, tuan_num, row_begin_idx, anchor_key, msg,
                "error_select_tuan", f"Lỗi chọn tuần: {msg}", f"❌ Lỗi chọn {tuan_text}: {msg}",
            )

        keep_going, available_lop_keys = self._available_lop_keys(lop_idx, tuan_num, tuan_text, row_begin_idx)
        if not keep_going:
            return False

        if available_lop_keys is not None and lop_text.casefold() not in available_lop_keys:
            self.skipped += 1
            self.checkpoint_state = self._next_checkpoint_after_week(lop_idx, tuan_num)
            self._emit_system_result(
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
            q.put(("checkpoint", self.checkpoint_state))
            return True

        q.put(("log", f"🏫 Chọn Lớp {lop_text}...", "info"))
        ok, msg = self._select_dropdown_retry("lop", lop_text, attempts=3)
        if not ok:
            return self._week_step_failed(
                lop_idx, lop_text, tuan_num, row_begin_idx, anchor_key, msg,
                "error_select_lop", f"Lỗi chọn lớp {lop_text}: {msg}", f"❌ Lỗi chọn Lớp {lop_text}: {msg}",
            )

        ok, msg = self.bridge.set_goi_y_khdh_mode(True)
        if not ok:
            return self._week_step_failed(
                lop_idx, lop_text, tuan_num, row_begin_idx, anchor_key, msg,
                "error_set_khdh_mode", msg, f"❌ {tuan_text}: {msg}",
            )

        ok_rows, khdh_rows = self.bridge.read_khdh_suggested_rows()
        if not ok_rows:
            return self._week_step_failed(
                lop_idx, lop_text, tuan_num, row_begin_idx, anchor_key, khdh_rows,
                "error_read_khdh_rows", str(khdh_rows),
                f"❌ {tuan_text}: Không đọc được row đỏ KHDH: {khdh_rows}",
            )

        khdh_rows = [
            row for row in (khdh_rows or [])
            if row.get("has_add_btn") and row.get("rowIdx") is not None
        ]
        if is_resume_anchor and self.start_row_key:
            row_begin_idx = self._resume_row_index(khdh_rows, tuan_text)
        self.discovered_total += len(khdh_rows)
        q.put(("progress_total", max(self.discovered_total, 1)))
        if not khdh_rows:
            q.put(("log", f"ℹ Lớp {lop_text} | {tuan_text}: Không có row đỏ KHDH nào cần nhập.", "info"))
            self.checkpoint_state = self._next_checkpoint_after_week(lop_idx, tuan_num)
            q.put(("checkpoint", self.checkpoint_state))
            return True

        if row_begin_idx >= len(khdh_rows):
            q.put(("log", f"ℹ Lớp {lop_text} | {tuan_text}: Không còn row KHDH nào để tiếp tục ở checkpoint hiện tại.", "info"))
            self.checkpoint_state = self._next_checkpoint_after_week(lop_idx, tuan_num)
            q.put(("checkpoint", self.checkpoint_state))
            return True

        q.put(("log",
               f"📊 Lớp {lop_text} | {tuan_text}: phát hiện {len(khdh_rows)} row đỏ KHDH cần xử lý.",
               "info"))

        self._process_rows(lop_idx, lop_text, tuan_num, tuan_text, khdh_rows, row_begin_idx)

        q.put(("log",
               f"📅 Lớp {lop_text} | {tuan_text} xong (KHDH): "
               f"{self.completed} nhập, {self.skipped} skip, {self.errors} lỗi",
               "info"))
        return not self.stopped

    def _resume_row_index(self, khdh_rows, tuan_text):
        """Vị trí hàng checkpoint trong danh sách hàng đỏ hiện tại (không thấy -> quét lại từ đầu)."""
        matched_idx = next(
            (
                idx for idx, row in enumerate(khdh_rows)
                if row_resume_key(row) == self.start_row_key
            ),
            None,
        )
        if matched_idx is not None:
            return matched_idx
        self.q.put((
            "log",
            f"⚠ {tuan_text}: Không còn thấy row checkpoint key={self.start_row_key}. "
            "App sẽ quét lại từ row đỏ đầu tiên hiện còn để tránh skip nhầm.",
            "warning",
        ))
        return 0

    def _process_rows(self, lop_idx, lop_text, tuan_num, tuan_text, khdh_rows, row_begin_idx):
        q = self.q
        for red_idx in range(row_begin_idx, len(khdh_rows)):
            if self.app._schedule_stop_event.is_set():
                self.stopped = True
                next_row = khdh_rows[red_idx] if red_idx < len(khdh_rows) else None
                self.checkpoint_state = self._build_checkpoint(
                    lop_idx,
                    tuan_num,
                    red_idx,
                    row_resume_key(next_row) if next_row else None,
                )
                break

            outcome = self._process_row(lop_idx, lop_text, tuan_num, tuan_text, khdh_rows[red_idx], red_idx)
            if outcome == ROW_SKIP:
                continue
            if outcome == ROW_STOP or self.stopped:
                break

            next_row_idx = red_idx + 1
            if next_row_idx < len(khdh_rows):
                self.checkpoint_state = self._build_checkpoint(lop_idx, tuan_num, next_row_idx)
            else:
                self.checkpoint_state = self._next_checkpoint_after_week(lop_idx, tuan_num)
            q.put(("checkpoint", self.checkpoint_state))
            q.put((
                "progress",
                f"Lớp {lop_text} | {tuan_text} | {self._slot_label(khdh_rows[red_idx])}",
                self.completed + self.skipped + self.errors,
            ))

    # ------------------------------------------------------------------
    # Một hàng đỏ
    # ------------------------------------------------------------------

    def _slot_label(self, row_info):
        return self.app._format_schedule_slot_label(
            row_info.get("thu", "?"),
            row_info.get("buoi", "?"),
            row_info.get("tiet", "?"),
        )

    def _process_row(self, lop_idx, lop_text, tuan_num, tuan_text, row_info, red_idx):
        """Returns ROW_SKIP / ROW_STOP / ROW_DONE."""
        q = self.q
        slot_label = self._slot_label(row_info)
        row_ppct_hint = digits(row_info.get("ppct_hint"))
        self.checkpoint_state = self._build_checkpoint(
            lop_idx,
            tuan_num,
            red_idx,
            row_resume_key(row_info),
        )
        q.put(("checkpoint", self.checkpoint_state))
        q.put((
            "progress",
            f"Lớp {lop_text} | {tuan_text} | {slot_label} | KHDH PPCT {row_ppct_hint or '--'}",
            self.completed + self.skipped + self.errors,
        ))
        ctx = _RowContext(lop_idx, lop_text, tuan_num, row_info, slot_label, row_ppct_hint)

        outcome = self._open_row_form(ctx)
        if outcome is not None:
            return outcome
        outcome, popup_values = self._read_verified_popup(ctx)
        if outcome is not None:
            return outcome
        targets = resolve_fill_targets(popup_values, row_info)
        outcome = self._fill_row_form(ctx, popup_values, targets)
        if outcome is not None:
            return outcome
        self._save_and_confirm_row(ctx, popup_values, targets)
        return ROW_DONE

    def _open_row_form(self, ctx):
        """Bấm "+" của hàng và chờ popup mở. Returns None khi popup đã sẵn sàng."""
        q = self.q
        row_info = ctx.row_info
        # Phòng hờ: danh sách hàng đỏ đã được lọc chỉ còn hàng có nút "+" nên nhánh này hiện không xảy ra.
        if not row_info.get("has_add_btn"):
            self.skipped += 1
            self._emit_row_result(
                "skipped_existing", ctx,
                "Row KHDH không còn nút + (có thể đã nhập trước đó)",
                ctx.row_ppct_hint or None,
            )
            q.put(("log", f"⏭ {ctx.slot_label}: Không còn nút +, bỏ qua.", "info"))
            return ROW_SKIP

        ok, msg = self.bridge.click_add_button(
            row_index=as_index(row_info.get("add_btn_index")),
            row_dom_index=as_index(row_info.get("rowIdx")),
        )
        if not ok:
            self.errors += 1
            self._emit_row_result("error_click_add", ctx, f"Click + thất bại: {msg}", ctx.row_ppct_hint or None)
            q.put(("log", f"❌ {ctx.slot_label}: Click + thất bại: {msg}", "error"))
            return ROW_SKIP

        ok_form, msg_form = self.bridge.wait_for_lesson_form(
            timeout_s=4.0,
            poll_interval=0.12,
        )
        if not ok_form:
            self.errors += 1
            self._emit_row_result(
                "error_open_form", ctx, f"Form chưa mở sẵn sàng: {msg_form}", ctx.row_ppct_hint or None
            )
            q.put(("log", f"❌ {ctx.slot_label}: Form chưa mở sẵn sàng: {msg_form}", "error"))
            self._close_form_quietly()
            return ROW_SKIP
        return None

    def _read_verified_popup(self, ctx):
        """Đọc popup và kiểm tra đúng hàng. Popup lỗi/mở sai hàng -> dừng để tránh nhập nhầm.

        Returns (None, giá_trị_popup) khi hợp lệ, (ROW_STOP, None) khi phải dừng.
        """
        q = self.q
        ok_snapshot, snapshot = self.bridge.get_open_lesson_form_snapshot()
        if not ok_snapshot:
            self.errors += 1
            self.stopped = True
            self._set_stop_reason("không đọc được popup KHDH để verify")
            self._emit_row_result("error_verify_popup", ctx, str(snapshot), ctx.row_ppct_hint or None)
            q.put(("log", f"❌ {ctx.slot_label}: Không đọc được popup để verify: {snapshot}", "error"))
            self._close_form_quietly()
            return ROW_STOP, None

        matched, issues, soft_issues, popup_values = verify_snapshot_matches_row(snapshot, ctx.row_info)
        popup_ppct = popup_values.get("ppct", "")
        if not matched:
            self.errors += 1
            self.stopped = True
            self._set_stop_reason("popup mở sai row KHDH")
            error_message = "Popup không khớp row KHDH: " + "; ".join(issues)
            self._emit_row_result("error_verify_popup", ctx, error_message, popup_ppct or ctx.row_ppct_hint or None)
            q.put(("log", f"❌ {ctx.slot_label}: {error_message} | Dừng để tránh nhập nhầm row", "error"))
            self._close_form_quietly()
            return ROW_STOP, None

        if soft_issues:
            q.put((
                "log",
                f"⚠ {ctx.slot_label}: hint row đỏ lệch popup KHDH ({'; '.join(soft_issues)}). "
                "App sẽ ưu tiên dữ liệu popup đang mở để tránh nhập sai.",
                "warning",
            ))
        return None, popup_values

    def _fill_row_form(self, ctx, popup_values, targets):
        """Điền popup: tối thiểu nếu popup đã có dữ liệu KHDH, ngược lại điền đủ từ gợi ý hàng đỏ.

        Returns None khi điền xong, ROW_SKIP khi thiếu dữ liệu / điền lỗi.
        """
        q = self.q
        popup_ppct = popup_values.get("ppct", "")
        missing_fields = missing_fill_fields(targets)
        if missing_fields:
            self.errors += 1
            error_message = (
                "Row KHDH thiếu dữ liệu để fill popup: "
                + ", ".join(missing_fields)
            )
            self._emit_row_result(
                "error_khdh_missing_data", ctx, error_message, popup_ppct or ctx.row_ppct_hint or None
            )
            q.put(("log", f"❌ {ctx.slot_label}: {error_message}", "error"))
            self._close_form_quietly()
            return ROW_SKIP

        nhan_xet = random.choice(self.nhan_xet_items)
        if popup_has_khdh_payload(popup_values):
            q.put(("log",
                   f"📝 {ctx.slot_label}: giữ dữ liệu popup KHDH | MH={targets['mon_hoc_text'] or targets['mon_hoc_id'] or '--'}, PPCT={targets['ppct']}, NX={nhan_xet[:30]}...",
                   "info"))
            ok_fill, msg_fill = self.bridge.fill_form_minimal(
                hs_nghi=self.hs_nghi,
                nhan_xet=nhan_xet,
                diem=self.diem,
            )
            q.put(("log", f"   📋 Fill KHDH tối thiểu: {msg_fill}", "info"))
        else:
            q.put(("log",
                   f"📝 {ctx.slot_label}: fallback fill KHDH từ hint row | MH={targets['mon_hoc_id'] or '--'}, PM={targets['phan_mon_id'] or '--'}, PPCT={targets['ppct']}, NX={nhan_xet[:30]}...",
                   "info"))
            ok_fill, msg_fill = self.bridge.fill_form(
                ppct=targets["ppct"],
                hs_nghi=self.hs_nghi,
                nhan_xet=nhan_xet,
                diem=self.diem,
                phan_mon_index=targets["phan_mon_id"] or None,
                mon_hoc_index=targets["mon_hoc_id"] or None,
                phan_mon_text=targets["phan_mon_text"] or None,
                mon_hoc_text=targets["mon_hoc_text"] or None,
                mon_hoc_field="mon_hoc_id",
                noi_dung=targets["noi_dung"],
            )
            q.put(("log", f"   📋 Fill KHDH fallback: {msg_fill}", "info"))
        if not ok_fill:
            self.errors += 1
            self._emit_row_result(
                "error_fill", ctx, f"Fill KHDH lỗi: {msg_fill}", popup_ppct or ctx.row_ppct_hint or None
            )
            q.put(("log", f"❌ {ctx.slot_label}: Fill KHDH lỗi: {msg_fill}", "error"))
            self._close_form_quietly()
            return ROW_SKIP
        return None

    def _save_and_confirm_row(self, ctx, popup_values, targets):
        """Lưu popup; nếu báo lỗi thì kiểm tra bảng xem tiết đã có dữ liệu chưa.

        Lưu lỗi nhưng request đã gửi (kết quả mơ hồ) -> dừng để tránh lệch checkpoint.
        """
        q = self.q
        bridge = self.bridge
        row_info = ctx.row_info
        slot_label = ctx.slot_label
        popup_ppct = popup_values.get("ppct", "")
        target_ppct = targets["ppct"]

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
        if ok_save:
            ok_commit = True
            commit_msg = msg_save
        else:
            ok_commit, commit_msg, _saved_row = bridge.wait_for_slot_data_fetch(
                ctx.lop_text,
                ctx.tuan_num,
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
            self.completed += 1
            popup_ppct_int = None
            try:
                popup_ppct_int = int(str(target_ppct or popup_ppct or ctx.row_ppct_hint or "").strip())
            except Exception:
                popup_ppct_int = None
            if popup_ppct_int is not None:
                self.last_success_ppct = popup_ppct_int
                q.put(("ppct_sync", self.last_success_ppct, None))
            if not ok_save:
                q.put(("log",
                       f"⚠ {slot_label}: Save báo lỗi nhưng bảng đã cập nhật ({commit_msg})",
                       "warning"))
                self._close_form_quietly()
            self._emit_row_result(
                "success", ctx, "Đã lưu thành công", target_ppct or popup_ppct or ctx.row_ppct_hint or None
            )
            q.put(("log",
                   f"✅ {slot_label} | KHBD/KHDH PPCT={target_ppct or popup_ppct or ctx.row_ppct_hint or '--'}: OK",
                   "success"))
            return

        self.errors += 1
        error_message = (
            f"Save lỗi: {msg_save}"
            + (f" | Verify: {commit_msg}" if commit_msg else "")
        )
        request_sent = bool(save_meta.get("request_sent", False))
        if request_sent:
            self.stopped = True
            self._set_stop_reason("save KHBD/KHDH mơ hồ cần xác minh")
            self._emit_row_result(
                "error_save_ambiguous", ctx, error_message, popup_ppct or ctx.row_ppct_hint or None
            )
            q.put(("log",
                   f"❌ {slot_label}: {error_message} | Dừng để tránh lệch checkpoint",
                   "error"))
        else:
            self._emit_row_result("error_save", ctx, error_message, popup_ppct or ctx.row_ppct_hint or None)
            q.put(("log", f"❌ {slot_label}: {error_message}", "error"))
        self._close_form_quietly(timeout_s=1.2)

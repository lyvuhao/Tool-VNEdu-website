"""Worker nhập Sổ đầu bài theo lịch (chế độ thủ công): một lần chạy = một `ScheduleJob`.

`_schedule_worker` (schedule_worker.py) chạy trong thread nền và gọi `ScheduleJob(app, params).run()`:

    run()                       kết nối CDP -> _process_all_weeks() -> _finish() (dọn dẹp + gửi "done")
    _process_week()             chọn tuần -> chọn lớp -> đọc bảng tuần -> từng slot trong lịch
    _process_slot()             một slot (thứ/buổi/tiết)
        _resolve_slot_row()           tìm hàng trên bảng tuần (đọc lại bảng nếu cần)
        _handle_existing_row()        hàng đã có dữ liệu -> bỏ qua, đồng bộ PPCT
        _open_slot_form()             bấm "+", chờ form
        _fill_slot_form()             điền PPCT, HS nghỉ, nhận xét, điểm, môn/phân môn
        _save_and_confirm_slot()      lưu, xác nhận trên bảng, dừng nếu lưu mơ hồ

Mọi sự kiện gửi về giao diện qua `app._schedule_queue` ("log", "checkpoint", "progress", "slot_result",
"ppct_sync", "error", "done") giữ nguyên như trước khi tách.
"""

import copy
import random
import time

from ..cdp.bridge import ChromeBridge
from ..cdp.health import is_cdp_target_closed_error
from .khdh_rows import parse_nhan_xet_items

# Kết quả xử lý một slot (thay cho continue trong vòng lặp cũ)
SLOT_DONE = "done"    # đã xử lý (thành công / bỏ qua / lỗi) -> cập nhật checkpoint "slot kế tiếp"
SLOT_SKIP = "skip"    # form không mở được -> sang slot kế, KHÔNG cập nhật checkpoint/progress
                      # (giữ đúng hành vi cũ: slot kế sẽ tự gửi checkpoint của nó)
FORM_OPEN = "open"    # _open_slot_form: form đã mở, điền tiếp


def slot_key(thu, buoi, tiet):
    """Khoá so khớp slot với hàng trên bảng tuần.

    Chuẩn hoá thứ/buổi về token thống nhất (CN/8, Sáng/Chiều có dấu) để slot từ giao diện khớp đúng
    hàng đọc từ `read_table`, kể cả Chủ nhật.
    """
    return (
        ChromeBridge._normalize_thu_token(thu),
        ChromeBridge._normalize_buoi_token(buoi),
        str(tiet).strip(),
    )


def slot_key_of(item):
    """`slot_key` của một slot / một hàng bảng (dict có thu, buoi, tiet)."""
    return slot_key(item.get("thu", ""), item.get("buoi", ""), item.get("tiet", ""))


def has_no_valid_index(row_index, row_dom_index):
    """Hàng không có add_btn_index lẫn rowIdx hợp lệ (không bấm được nút "+")."""
    return row_index < 0 and (row_dom_index is None or row_dom_index < 0)


def row_fields(row):
    """(add_btn_index, rowIdx, đã có dữ liệu, có nút "+") của một hàng bảng tuần."""
    return (
        row.get("add_btn_index", -1),
        row.get("rowIdx"),
        bool(row.get("has_data", False)),
        bool(row.get("has_add_btn", False)),
    )


class WeekRowCache:
    """Bảng của tuần đang chạy, tra theo `slot_key` (đọc bảng một lần cho cả tuần)."""

    def __init__(self):
        self.rows = []
        self.lookup = {}

    def set_rows(self, rows):
        self.rows = list(rows or [])
        self.lookup = {}
        for row in self.rows:
            self.lookup[slot_key_of(row)] = row

    def get(self, slot):
        return self.lookup.get(slot_key_of(slot))

    def mark_saved(self, slot, ppct_value, saved_row=None, mon_hoc_text=None, phan_mon_text=None):
        """Ghi nhận slot vừa lưu: dùng hàng đọc lại từ web nếu có, không thì sửa hàng trong cache."""
        key = slot_key_of(slot)
        if saved_row:
            self.lookup[key] = saved_row
            return
        matched = self.lookup.get(key)
        if matched is None:
            return
        matched["has_data"] = True
        matched["ppct"] = str(ppct_value)
        if mon_hoc_text and phan_mon_text:
            matched["mon_hoc"] = f"{mon_hoc_text} ({phan_mon_text})"
        elif mon_hoc_text:
            matched["mon_hoc"] = str(mon_hoc_text)


class _SlotContext:
    """Thông tin của slot đang xử lý."""

    __slots__ = ("tuan_num", "slot", "thu", "buoi", "tiet", "slot_label", "current_ppct")

    def __init__(self, tuan_num, slot, slot_label, current_ppct):
        self.tuan_num = tuan_num
        self.slot = slot
        self.thu = slot["thu"]
        self.buoi = slot["buoi"]
        self.tiet = slot["tiet"]
        self.slot_label = slot_label
        self.current_ppct = current_ppct

    @property
    def tuan_text(self):
        return f"Tuần {self.tuan_num}"


class ScheduleJob:
    """Một lần chạy nhập Sổ đầu bài theo lịch: tham số, bộ đếm, PPCT và checkpoint để chạy tiếp."""

    def __init__(self, app, params):
        self.app = app
        self.params = params
        self.bridge = None
        self.q = app._schedule_queue
        self.slots = params["slots"]
        self.tuan_from = int(params["tuan_from"])
        self.tuan_to = int(params["tuan_to"])
        self.lop_text = params["lop"]
        resume_state = params.get("resume_state") or {}
        self.completed = int(resume_state.get("completed", 0) or 0)
        self.skipped = int(resume_state.get("skipped", 0) or 0)
        self.errors = int(resume_state.get("errors", 0) or 0)
        self.ppct_counter = int(
            resume_state.get("next_ppct", params["ppct_start"]) or params["ppct_start"]
        )
        self.last_success_ppct = resume_state.get("last_success_ppct")
        self.start_tuan_num = int(resume_state.get("next_tuan_num", self.tuan_from) or self.tuan_from)
        self.start_slot_idx = int(resume_state.get("next_slot_idx", 0) or 0)
        self.stopped = False
        self.checkpoint_state = {
            "next_tuan_num": self.start_tuan_num,
            "next_slot_idx": self.start_slot_idx,
            "next_ppct": self.ppct_counter,
            "completed": self.completed,
            "skipped": self.skipped,
            "errors": self.errors,
            "last_success_ppct": self.last_success_ppct,
        }

        self.hs_nghi = params["hs_nghi"]
        self.diem = params["diem"]
        self.phan_mon_value = params.get("phan_mon_value")
        self.mon_hoc_value = params.get("mon_hoc_value")
        self.phan_mon_text = params.get("phan_mon_text")
        self.mon_hoc_text = params.get("mon_hoc_text")
        self.mon_hoc_field = params.get("mon_hoc_field")
        self.nhan_xet_items = parse_nhan_xet_items(params["nhan_xet_raw"])
        self.week = WeekRowCache()

    # ------------------------------------------------------------------
    # Checkpoint & sự kiện gửi về giao diện
    # ------------------------------------------------------------------

    def _build_checkpoint(self, next_tuan_num, next_slot_idx):
        return {
            "next_tuan_num": int(next_tuan_num),
            "next_slot_idx": int(next_slot_idx),
            "next_ppct": int(self.ppct_counter),
            "completed": int(self.completed),
            "skipped": int(self.skipped),
            "errors": int(self.errors),
            "last_success_ppct": self.last_success_ppct,
        }

    def _next_position(self, tuan_num, slot_idx):
        if slot_idx + 1 < len(self.slots):
            return tuan_num, slot_idx + 1
        return tuan_num + 1, 0

    def _emit_slot_result(self, status, tuan_num, slot, message, ppct_value=None):
        self.q.put(("slot_result", {
            "status": status,
            "week_num": int(tuan_num),
            "week_text": f"Tuần {tuan_num}",
            "slot_label": self.app._format_schedule_slot_label(
                slot.get("thu", "?"),
                slot.get("buoi", "?"),
                slot.get("tiet", "?"),
            ),
            "ppct": ppct_value,
            "message": message,
        }))

    def _slot_error(self, ctx, status, message, log_message=None, ppct_value=None):
        """Đếm lỗi, gửi kết quả slot và log "❌ <slot>: ..." (mặc định log = message)."""
        self.errors += 1
        self._emit_slot_result(
            status,
            ctx.tuan_num,
            ctx.slot,
            message,
            ctx.current_ppct if ppct_value is None else ppct_value,
        )
        self.q.put(("log", f"❌ {ctx.slot_label}: {message if log_message is None else log_message}", "error"))

    def _emit_system_result(self, status, tuan_num, message):
        self.q.put(("slot_result", {
            "status": status,
            "week_num": int(tuan_num),
            "week_text": f"Tuần {tuan_num}",
            "slot_label": "Khởi tạo tuần/lớp",
            "ppct": None,
            "message": message,
        }))

    def _set_stop_reason(self, reason):
        if not self.app._schedule_stop_reason:
            self.app._schedule_stop_reason = reason

    def _mark_cdp_closed_stop(self, tuan_num, slot_idx, message):
        self.errors += 1
        self.stopped = True
        self.checkpoint_state = self._build_checkpoint(tuan_num, slot_idx)
        self._set_stop_reason("CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng")
        self.q.put(("checkpoint", self.checkpoint_state))
        self.q.put(("error", (
            "CDP/Chrome đã đóng hoặc tab VnEdu không còn khả dụng. "
            f"Worker schedule dừng tại Tuần {tuan_num}. Chi tiết: {message}"
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

    def _refresh_week_snapshot(self):
        ok_table, table_data = self.bridge.read_table()
        if ok_table:
            self.week.set_rows(table_data)
        return ok_table, table_data

    def _get_cached_row(self, slot, refresh_if_missing=True):
        matched = self.week.get(slot)
        if matched is None and refresh_if_missing:
            ok_table, table_data = self._refresh_week_snapshot()
            if not ok_table:
                return None, f"Không đọc được bảng: {table_data}"
            matched = self.week.get(slot)
        return matched, None

    # ------------------------------------------------------------------
    # Luồng chính
    # ------------------------------------------------------------------

    def run(self):
        try:
            self.bridge = ChromeBridge(port=self.params["port"])
            ok, msg = self.bridge.connect()
            if not ok:
                self.q.put(("error", f"Kết nối CDP thất bại: {msg}"))
                self.errors += 1
            else:
                self.bridge.setup_dialog_auto_accept()
                self.q.put(("log", "CDP connected (schedule worker)", "success"))
                self._process_all_weeks()
        except Exception as e:
            self.errors += 1
            self.stopped = True
            self.q.put(("error",
                        f"Schedule worker exception: {type(e).__name__}: "
                        f"{str(e)[:140]}"))
        finally:
            self._finish()

    def _process_all_weeks(self):
        for tuan_num in range(self.start_tuan_num, self.tuan_to + 1):
            if not self._process_week(tuan_num):
                break

    def _finish(self):
        """Dọn dẹp trình duyệt và gửi sự kiện "done" (kèm checkpoint nếu còn việc dang dở)."""
        q = self.q
        bridge = self.bridge
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
        checkpoint_state = self.checkpoint_state
        has_remaining = (
            checkpoint_state.get("next_tuan_num", self.tuan_to + 1) <= self.tuan_to
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
            "next_ppct": resume_payload.get("next_ppct") if resume_payload else self.ppct_counter,
        }))

    # ------------------------------------------------------------------
    # Một tuần
    # ------------------------------------------------------------------

    def _week_step_failed(self, tuan_num, slot_begin_idx, msg, status, result_message, log_message):
        """Bước chuẩn bị tuần thất bại.

        Returns:
            False nếu Chrome/CDP đã đóng (dừng hẳn), True nếu chỉ bỏ qua tuần này.
        """
        if is_cdp_target_closed_error(msg):
            self._mark_cdp_closed_stop(tuan_num, slot_begin_idx, msg)
            return False
        self.errors += 1
        self.checkpoint_state = self._build_checkpoint(tuan_num, slot_begin_idx)
        self._emit_system_result(status, tuan_num, result_message)
        self.q.put(("log", log_message, "error"))
        self.q.put(("checkpoint", self.checkpoint_state))
        return True

    def _process_week(self, tuan_num):
        """Xử lý một tuần. Trả về False khi phải dừng hẳn (người dùng dừng / Chrome đóng)."""
        q = self.q
        lop_text = self.lop_text
        slot_begin_idx = self.start_slot_idx if tuan_num == self.start_tuan_num else 0
        if self.app._schedule_stop_event.is_set():
            self.stopped = True
            self.checkpoint_state = self._build_checkpoint(tuan_num, slot_begin_idx)
            return False

        tuan_text = f"Tuần {tuan_num}"
        q.put(("log", f"📅 Chuyển đến {tuan_text}...", "info"))
        ok, msg = self._select_dropdown_retry("tuan", tuan_text, attempts=3)
        if not ok:
            return self._week_step_failed(
                tuan_num, slot_begin_idx, msg,
                "error_select_tuan", f"Lỗi chọn tuần: {msg}", f"❌ Lỗi chọn {tuan_text}: {msg}",
            )

        q.put(("log", f"🏫 Chọn Lớp {lop_text}...", "info"))
        ok, msg = self._select_dropdown_retry("lop", lop_text, attempts=3)
        if not ok:
            return self._week_step_failed(
                tuan_num, slot_begin_idx, msg,
                "error_select_lop", f"Lỗi chọn lớp {lop_text}: {msg}", f"❌ Lỗi chọn Lớp {lop_text}: {msg}",
            )

        ok, table_data = self._refresh_week_snapshot()
        if not ok:
            return self._week_step_failed(
                tuan_num, slot_begin_idx, table_data,
                "error_read_week_table", f"Lỗi đọc bảng tuần: {table_data}",
                f"❌ {tuan_text}: Lỗi đọc bảng tuần: {table_data}",
            )

        q.put(("log",
               f"📊 Bắt đầu quét {tuan_text} — {lop_text} "
               f"({len(self.slots)} slots, {len(self.week.rows)} rows)...", "info"))

        self._process_slots(tuan_num, slot_begin_idx)

        q.put(("log",
               f"📅 {tuan_text} xong: {self.completed} nhập, "
               f"{self.skipped} skip, {self.errors} lỗi",
               "info"))
        return True

    def _process_slots(self, tuan_num, slot_begin_idx):
        q = self.q
        for slot_idx in range(slot_begin_idx, len(self.slots)):
            if self.app._schedule_stop_event.is_set():
                self.stopped = True
                self.checkpoint_state = self._build_checkpoint(tuan_num, slot_idx)
                break

            slot = self.slots[slot_idx]
            ctx = _SlotContext(
                tuan_num,
                slot,
                self.app._format_schedule_slot_label(slot["thu"], slot["buoi"], slot["tiet"]),
                self.ppct_counter,
            )
            self.checkpoint_state = self._build_checkpoint(tuan_num, slot_idx)
            q.put(("checkpoint", self.checkpoint_state))

            if self._process_slot(ctx) == SLOT_SKIP:
                continue

            if self.stopped:
                break

            next_tuan_num, next_slot_idx = self._next_position(tuan_num, slot_idx)
            self.checkpoint_state = self._build_checkpoint(next_tuan_num, next_slot_idx)
            q.put(("checkpoint", self.checkpoint_state))
            q.put((
                "progress",
                f"{ctx.tuan_text} | {ctx.slot_label}",
                self.completed + self.skipped + self.errors,
            ))

    # ------------------------------------------------------------------
    # Một slot
    # ------------------------------------------------------------------

    def _process_slot(self, ctx):
        """Một slot trong lịch: tìm hàng -> (bỏ qua nếu đã có) -> mở form -> điền -> lưu."""
        matched_row = self._resolve_slot_row(ctx)
        if matched_row is None:
            return SLOT_DONE

        row_index, row_dom_index, has_data, has_add_btn = row_fields(matched_row)
        if has_data:
            self._handle_existing_row(ctx, matched_row)
        elif not has_add_btn:
            self._slot_error(ctx, "error_no_add_button", "Không có nút + để mở form", "Không có nút +")
        elif has_no_valid_index(row_index, row_dom_index):
            self._slot_error(ctx, "error_row_index", "Row không có add_btn_index/rowIdx hợp lệ")
        else:
            opened = self._open_slot_form(ctx, row_index, row_dom_index)
            if opened != FORM_OPEN:
                return opened
            if self._fill_slot_form(ctx):
                self._save_and_confirm_slot(ctx)
        return SLOT_DONE

    def _resolve_slot_row(self, ctx):
        """Hàng của slot trên bảng tuần; đọc lại bảng nếu hàng chưa có dữ liệu mà không bấm "+" được.

        Trả về None (đã báo lỗi) nếu không đọc được bảng hoặc không tìm thấy hàng.
        """
        matched_row, cache_error = self._get_cached_row(ctx.slot, refresh_if_missing=True)
        if cache_error:
            self._slot_error(ctx, "error_read_table", cache_error)
            return None
        if matched_row is None:
            self._slot_error(ctx, "error_not_found", "Không tìm thấy row trên bảng VnEdu", "Không tìm thấy row")
            return None

        row_index, row_dom_index, has_data, has_add_btn = row_fields(matched_row)
        if not has_data and (not has_add_btn or has_no_valid_index(row_index, row_dom_index)):
            ok_refresh, refresh_data = self._refresh_week_snapshot()
            if not ok_refresh:
                self._slot_error(
                    ctx, "error_read_table", f"Không đọc được bảng sau refresh: {refresh_data}",
                )
                return None
            matched_row = self.week.lookup.get(slot_key(ctx.thu, ctx.buoi, ctx.tiet))

        if matched_row is None:
            self._slot_error(ctx, "error_not_found", "Không tìm thấy row sau khi refresh bảng")
        return matched_row

    def _handle_existing_row(self, ctx, matched_row):
        """Hàng đã có dữ liệu: bỏ qua và đồng bộ PPCT; không đọc được PPCT thì dừng để tránh lệch."""
        existing_ppct_value = self.app._extract_existing_ppct_value(matched_row)
        if existing_ppct_value is None:
            self.stopped = True
            self._set_stop_reason("row đã có dữ liệu nhưng không đọc được PPCT")
            self._slot_error(ctx, "error_existing_ppct", "Row đã có dữ liệu nhưng không đọc được PPCT hiện có")
            return

        self.skipped += 1
        if existing_ppct_value >= ctx.current_ppct:
            self.last_success_ppct = existing_ppct_value
            self.ppct_counter = existing_ppct_value + 1
            self.q.put(("ppct_sync", self.last_success_ppct, self.ppct_counter))
        message = f"Đã có dữ liệu — PPCT hiện có {existing_ppct_value}"
        self._emit_slot_result("skipped_existing", ctx.tuan_num, ctx.slot, message, existing_ppct_value)
        self.q.put(("log", f"⏭ {ctx.slot_label}: {message}", "info"))

    def _open_slot_form(self, ctx, row_index, row_dom_index):
        """Bấm "+" và chờ form.

        Returns:
            FORM_OPEN nếu form đã mở; SLOT_DONE nếu bấm "+" lỗi; SLOT_SKIP nếu form không mở.
        """
        self.q.put((
            "progress",
            f"{ctx.tuan_text} | {ctx.slot_label} | PPCT {ctx.current_ppct}",
            self.completed + self.skipped + self.errors,
        ))
        ok, msg = self.bridge.click_add_button(
            row_index=row_index,
            row_dom_index=row_dom_index,
        )
        if not ok:
            # Như bản cũ: lỗi bấm "+" vẫn cập nhật checkpoint sang slot kế.
            self._slot_error(ctx, "error_click_add", f"Click + thất bại: {msg}")
            return SLOT_DONE

        ok_form, msg_form = self.bridge.wait_for_lesson_form(
            timeout_s=4.0,
            poll_interval=0.12,
        )
        if not ok_form:
            self._slot_error(ctx, "error_open_form", f"Form chưa mở sẵn sàng: {msg_form}")
            self._close_form_quietly(timeout_s=1.0)
            return SLOT_SKIP
        return FORM_OPEN

    def _fill_slot_form(self, ctx):
        """Điền form đang mở. Trả về True nếu điền được (form lỗi đã được đóng)."""
        nhan_xet = random.choice(self.nhan_xet_items)
        self.q.put(("log",
                    f"📝 {ctx.slot_label}: PPCT={ctx.current_ppct}, "
                    f"MH={self.mon_hoc_value}, PM={self.phan_mon_value}, "
                    f"NX={nhan_xet[:20]}...",
                    "info"))

        ok_fill, msg_fill = self.bridge.fill_form(
            ppct=str(ctx.current_ppct),
            hs_nghi=self.hs_nghi,
            nhan_xet=nhan_xet,
            diem=self.diem,
            phan_mon_index=self.phan_mon_value,
            mon_hoc_index=self.mon_hoc_value,
            phan_mon_text=self.phan_mon_text,
            mon_hoc_text=self.mon_hoc_text,
            mon_hoc_field=self.mon_hoc_field,
            noi_dung=None,
        )
        self.q.put(("log", f"   📋 Fill: {msg_fill}", "info"))

        if not ok_fill:
            self._slot_error(ctx, "error_fill", f"Fill form lỗi: {msg_fill}")
            self._close_form_quietly(timeout_s=1.0)
            return False
        return True

    def _confirm_saved_on_table(self, ctx):
        """Lưu báo lỗi: kiểm tra bảng xem slot đã có dữ liệu chưa (qua API, rồi qua DOM)."""
        ok_commit, commit_msg, saved_row = self.bridge.wait_for_slot_data_fetch(
            self.lop_text,
            ctx.tuan_num,
            ctx.thu,
            ctx.buoi,
            ctx.tiet,
            timeout_s=4.5,
            poll_interval=0.3,
        )
        if not ok_commit:
            ok_commit, commit_msg, saved_row = self.bridge.wait_for_slot_data(
                ctx.thu,
                ctx.buoi,
                ctx.tiet,
                timeout_s=3.0,
                poll_interval=0.25,
            )
        return ok_commit, commit_msg, saved_row

    def _save_and_confirm_slot(self, ctx):
        """Lưu form; lưu báo lỗi thì xác nhận lại trên bảng. Lưu mơ hồ (đã gửi request) -> dừng."""
        q = self.q
        self.bridge.wait_for_form_ready_to_save(
            timeout_s=0.8,
            poll_interval=0.06,
        )
        ok_save, msg_save, save_meta = self.bridge.save_form_auto(
            buoi_hoc=ctx.buoi,
        )
        if save_meta.get("fallback_used"):
            q.put((
                "log",
                "   ↩ API save không dùng được, đã fallback sang click-save",
                "warning",
            ))
        saved_row = None
        if ok_save:
            ok_commit, commit_msg = True, msg_save
        else:
            ok_commit, commit_msg, saved_row = self._confirm_saved_on_table(ctx)

        if ok_commit:
            self.week.mark_saved(
                ctx.slot,
                ctx.current_ppct,
                saved_row=saved_row,
                mon_hoc_text=self.mon_hoc_text,
                phan_mon_text=self.phan_mon_text,
            )
            if not ok_save:
                q.put(("log",
                       f"⚠ {ctx.slot_label}: Save báo lỗi nhưng bảng đã cập nhật "
                       f"({commit_msg})",
                       "warning"))
                self._close_form_quietly(timeout_s=1.0)

            self.completed += 1
            self.last_success_ppct = ctx.current_ppct
            self.ppct_counter = ctx.current_ppct + 1
            q.put(("ppct_sync", self.last_success_ppct, self.ppct_counter))
            self._emit_slot_result("success", ctx.tuan_num, ctx.slot, "Đã lưu thành công", ctx.current_ppct)
            q.put(("log",
                   f"✅ {ctx.slot_label} PPCT={ctx.current_ppct}: OK",
                   "success"))
            return

        error_message = (
            f"Save lỗi: {msg_save}"
            + (f" | Verify: {commit_msg}" if commit_msg else "")
        )
        request_sent = bool(save_meta.get("request_sent", False))
        if request_sent:
            self.stopped = True
            self._set_stop_reason("save mơ hồ cần xác minh")
            self._slot_error(
                ctx, "error_save_ambiguous", error_message,
                f"{error_message} | Dừng để tránh lệch checkpoint",
            )
        else:
            self._slot_error(ctx, "error_save", error_message)
        self._close_form_quietly(timeout_s=1.2)

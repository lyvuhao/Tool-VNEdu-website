"""Xác minh sau khi lưu và lưu điểm resume."""

from __future__ import annotations

import json

from ...log import logger
from ..planner import FillOp, PlanReport
from .errors import format_row_with_context
from .js import _JS_READ_FIELDS
from .models import ExecutorReport


class ResumeMixin:
    """Xác minh sau khi lưu và lưu điểm resume."""

    # -----------------------------------------------------------
    # Resume
    # -----------------------------------------------------------

    def _verify_week_after_save(
        self,
        tuan: int,
        active_ops: list[FillOp],
        max_retry: int = 3,
        wait_s: float = 1.5,
    ) -> tuple[bool, list[str]]:
        """(#1/#4/#6) Đọc lại web sau save + so khớp với ý định (active_ops).

        RUN thường trước đây CHỈ tin `save_res.success` từ server. Nhưng
        success=true KHÔNG đảm bảo dữ liệu đúng: web autofill có thể ghi đè
        tên bài sau debounce, hoặc autofill-blocker over/under-block. Verify
        biến lỗi-im-lặng thành lỗi-ồn-ào (đúng triết lý halt-on-error).

        So khớp field-by-field cho mỗi op (skip op.skip / ppct<=0):
          - lop_id / mon_id / phan_mon_id: bắt buộc khớp
          - ppct: bắt buộc khớp
          - trang_thai: bắt buộc khớp
          - ten_bai: chấp nhận nếu web có nội dung (web tự fill đúng theo
            PPCT là OK). CHỈ báo lỗi khi web RỖNG mà op kỳ vọng có tên bài
            VÀ op KHÔNG dùng fallback dấu cách (vì fallback cố tình ghi " ").

        Args:
            tuan: tuần vừa save.
            active_ops: các FillOp đã thực sự ghi (chưa skip).
            max_retry: số lần đọc lại (web cần thời gian debounce).
            wait_s: thời gian chờ giữa các lần đọc.

        Returns:
            (verified, diff_lines). diff_lines rỗng khi verified=True.
        """
        # Chỉ verify op thực sự ghi: không skip + có ppct > 0.
        targets: dict[str, FillOp] = {}
        for op in active_ops:
            if op.skip:
                continue
            try:
                ppct_int = int(op.ppct) if op.ppct else 0
            except (TypeError, ValueError):
                ppct_int = 0
            if ppct_int <= 0:
                continue
            targets[op.row_key] = op
        if not targets:
            return True, []

        diff_lines: list[str] = []
        for attempt in range(1, max_retry + 1):
            if self.stop_event.is_set():
                return False, ["Đã dừng theo yêu cầu."]
            # Chờ web settle (debounce autofill + XHR commit) trước khi đọc.
            if not self._interruptible_sleep(wait_s):
                return False, ["Đã dừng theo yêu cầu."]
            try:
                wd = self.client.fetch_week(tuan, is_edit=1)
            except Exception as e:
                self._emit("warning", tuan=tuan,
                          message=f"Verify lần {attempt}: đọc lại web lỗi ({e}), thử lại…")
                continue

            actual_by_rk = {s.row_key: s for s in wd.slots}
            diff_lines = []
            mismatch = False
            for rk, op in targets.items():
                actual = actual_by_rk.get(rk)
                label = format_row_with_context(
                    rk, op.lop_text, op.mon_text, op.phan_mon_text,
                )
                if actual is None or not actual.has_lop:
                    diff_lines.append(
                        f"  • {label}: web mất ô (đáng lẽ PPCT={op.ppct})"
                    )
                    mismatch = True
                    continue
                if (str(actual.lop_id) != str(op.lop_id)
                        or str(actual.mon_id) != str(op.mon_id)
                        or str(actual.phan_mon_id) != str(op.phan_mon_id)):
                    diff_lines.append(
                        f"  • {label}: lớp/môn/phân môn lệch — "
                        f"web={actual.lop_text}/{actual.mon_text}/{actual.phan_mon_text}"
                    )
                    mismatch = True
                    continue
                try:
                    a_ppct = int(actual.ppct) if actual.ppct else 0
                except (TypeError, ValueError):
                    a_ppct = 0
                if a_ppct != int(op.ppct):
                    diff_lines.append(
                        f"  • {label}: PPCT lệch — web={a_ppct}, cần={op.ppct}"
                    )
                    mismatch = True
                    continue
                want_tt = str(getattr(op, "trang_thai", "0") or "0")
                got_tt = str(actual.trang_thai or "0")
                if got_tt != want_tt:
                    diff_lines.append(
                        f"  • {label}: trạng thái lệch — "
                        f"web={actual.trang_thai_text or got_tt}, cần={want_tt}"
                    )
                    mismatch = True
                    continue
                # Tên bài: web RỖNG mà op kỳ vọng có tên bài thật → mất tiết.
                # Bỏ qua nếu op dùng fallback (" ") vì khi đó web rỗng là
                # đúng ý định (ô không có CSDL tên bài).
                a_tb = (actual.ten_bai or "").strip()
                op_tb = (op.ten_bai or "").strip()
                if op_tb and not a_tb:
                    diff_lines.append(
                        f"  • {label}: web RỖNG tên bài "
                        f"(đáng lẽ \"{op_tb[:40]}\") — web chưa auto-fill xong "
                        "hoặc ghi đè"
                    )
                    mismatch = True
                    continue

            if not mismatch:
                return True, []
            self._emit("warning", tuan=tuan,
                      message=(
                          f"Verify lần {attempt}/{max_retry}: {len(diff_lines)} ô "
                          "lệch, chờ rồi đọc lại…"
                      ))
        return False, diff_lines

    def _cleanup_stale_log_entries_for_week(self, tuan: int) -> int:
        """Đọc DOM tuần hiện tại + bỏ khỏi log các entry stale.

        Một entry được coi là "stale" nếu:
        - Raw value của `txtTenBai_<row_key>` khác " " (kể cả rỗng "" hay
          tên bài thật) → user / RUN khác đã sửa, log không còn ý nghĩa.
        - HOẶC context (lop/mon/pm/ppct) khác với entry đã ghi → user
          đã đổi cấu hình row, entry cũ không còn áp dụng.

        Returns: số entry đã bỏ.
        """
        if self.fallback_log is None:
            return 0
        log = self.fallback_log
        entries_in_week = [
            e for e in log.all_entries() if int(e.tuan) == int(tuan)
        ]
        # Loại bỏ các entry vừa được ghi trong phiên này (chúng đang là " "
        # đúng theo thiết kế, không phải stale).
        pending_keys = set(self._week_pending_log_entries)
        candidates = [
            e for e in entries_in_week
            if (int(e.tuan), str(e.row_key or "")) not in pending_keys
        ]
        if not candidates:
            return 0
        # Reload table để DOM phản ánh chính xác state sau save (server-side
        # changes vd web tự fill tên bài, web reset trạng thái...). Không
        # reload thì DOM có thể stale → cleanup nhầm.
        try:
            self.client.reload_table_and_wait(
                floor_s=1.0, max_extra_s=5.0, stop_event=self.stop_event,
            )
        except Exception:
            pass
        ids: list[str] = []
        for e in candidates:
            ids.extend([
                f"txtTenBai_{e.row_key}",
                f"cboLopHoc_{e.row_key}",
                f"cboMonHoc_{e.row_key}",
                f"cboPhanMon_{e.row_key}",
                f"txtTietPPCT_{e.row_key}",
            ])
        try:
            raw_state = self.client.page.evaluate(_JS_READ_FIELDS, ids) or {}
        except Exception:
            return 0
        removed = 0
        for entry in candidates:
            state_tb = raw_state.get(f"txtTenBai_{entry.row_key}")
            state_lop = raw_state.get(f"cboLopHoc_{entry.row_key}")
            state_mon = raw_state.get(f"cboMonHoc_{entry.row_key}")
            state_pm = raw_state.get(f"cboPhanMon_{entry.row_key}")
            state_ppct = raw_state.get(f"txtTietPPCT_{entry.row_key}")
            # DOM thiếu control nào → coi là stale luôn
            if any(s is None for s in (
                state_tb, state_lop, state_mon, state_pm, state_ppct
            )):
                if log.remove(entry.tuan, entry.row_key):
                    removed += 1
                continue
            raw_ten_bai = str((state_tb or {}).get("value") or "")
            raw_lop = str((state_lop or {}).get("value") or "")
            raw_mon = str((state_mon or {}).get("value") or "")
            raw_pm = str((state_pm or {}).get("value") or "")
            raw_ppct_text = str((state_ppct or {}).get("value") or "").strip()
            try:
                raw_ppct = int(raw_ppct_text) if raw_ppct_text else 0
            except ValueError:
                raw_ppct = 0
            # Context drift → stale
            context_ok = (
                raw_lop == str(entry.lop_id or "")
                and raw_mon == str(entry.mon_id or "")
                and raw_pm == str(entry.phan_mon_id or "")
                and raw_ppct == int(entry.ppct or 0)
            )
            if not context_ok:
                if log.remove(entry.tuan, entry.row_key):
                    removed += 1
                continue
            # Tên bài đã khác " " → stale (user/RUN khác đã sửa)
            if raw_ten_bai != " ":
                if log.remove(entry.tuan, entry.row_key):
                    removed += 1
        if removed:
            try:
                log.save()
            except Exception as e:
                self._emit(
                    "warning", tuan=tuan,
                    message=(
                        f"Không lưu được fallback log sau cleanup "
                        f"({removed} ô): {e}. Log có thể chưa cập nhật."
                    ),
                )
            self._emit(
                "info", tuan=tuan,
                message=(
                    f"Cleanup fallback log: {removed} ô đã có Tên bài thật "
                    "hoặc đổi context → bỏ khỏi log."
                ),
            )
        return removed

    def _persist_resume(self, plan: PlanReport, report: ExecutorReport):
        if not self.resume_path:
            return
        try:
            data = {
                "request": {
                    "tuan_from": plan.request.tuan_from,
                    "tuan_to": plan.request.tuan_to,
                    "strategy": plan.request.strategy.value,
                    "mode": plan.request.mode.value,
                },
                "completed_weeks": report.successful_weeks,
                "last_tuan_attempted": report.week_results[-1].tuan if report.week_results else 0,
                "started_at": report.started_at,
                "stopped": report.stopped,
            }
            self.resume_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"persist resume failed: {e}")

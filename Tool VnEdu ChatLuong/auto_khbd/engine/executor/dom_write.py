"""Vòng chạy chính và ghi trường PPCT lên DOM."""

from __future__ import annotations

import time

from ..client.js import _JS_SET_BLOCK_AUTOFILL
from ..parser import assess_week_structure
from ..planner import FillOp, PlanReport
from .errors import classify_save_error_msg, ERROR_HINTS, format_save_errors_for_user
from .js import _JS_READ_FIELDS, _JS_SET_PPCT_TEN_BAI
from .models import ExecutorReport


class DomWriteMixin:
    """Vòng chạy chính và ghi trường PPCT lên DOM."""

    def execute(self, plan: PlanReport, dry_run: bool = False) -> ExecutorReport:
        """Chạy plan. Trả ExecutorReport."""
        report = ExecutorReport(started_at=time.time())
        # Expose report ra ngoài để caller có thể đọc partial state nếu
        # execute() raise giữa chừng (W1 fix — wizard ExecutorWorker dùng).
        self._report = report
        # Reset fallback counter ở đầu mỗi phiên execute (đảm bảo idempotent
        # nếu cùng instance được reuse).
        self._fallback_count = 0
        self._emit("plan_start", message=f"strategy={plan.request.strategy.value} weeks={plan.request.tuan_from}-{plan.request.tuan_to} dry_run={dry_run}"
                   + (" ten_bai_fallback=on" if self.ten_bai_fallback else "")
                   + (" fast_safe=on" if self.fast_safe_mode else ""))

        # Lấy context 1 lần để cache token + nam_hoc dùng cho fetch_ten_bai
        try:
            self._ctx = self.client.fetch_context()
        except Exception as e:
            self._emit("error", message=f"fetch_context: {e}")
            self._ctx = None

        # v2: Init scan progress cache cho phiên này.
        # _last_scan_progress[tuan] = dict[group_key, last_ppct_int] —
        # set bởi _scan_and_diff_week(), đọc bởi UI để render PPCT next.
        self._last_scan_progress = {}

        # v2.2: Store reference plan để _apply_inflight_ppct_shift có thể
        # patch các tuần kế tiếp khi phát hiện dạy bù in-flight.
        self._current_plan = plan

        # (#2) CANARY CHECK — trước khi RUN THẬT hàng loạt, đọc 1 tuần mẫu
        # và xác minh cấu trúc web còn parse đúng. Tool tự động hóa endpoint/
        # UI nội bộ VnEdu (không hợp đồng); nếu web đổi cấu trúc, parse sẽ
        # trả rỗng/warning → ghi sai 35 tuần. Canary biến rủi ro im lặng đó
        # thành cảnh báo SỚM. Skip cho dry_run (không ghi gì).
        if not dry_run and self.verify_after_save and plan.week_plans:
            try:
                sample_tuan = plan.week_plans[0].tuan
                sample_wd = self.client.fetch_week(sample_tuan, is_edit=1)
                ok, problems = assess_week_structure(sample_wd)
                if not ok:
                    detail = "\n".join(f"  • {p}" for p in problems)
                    self._emit(
                        "error", tuan=sample_tuan,
                        message=(
                            f"⚠ Kiểm tra cấu trúc web (tuần {sample_tuan}) "
                            f"phát hiện bất thường:\n{detail}\n\n"
                            "Có thể VnEdu đã cập nhật giao diện/dữ liệu. "
                            "Tool DỪNG để tránh ghi sai hàng loạt. Hãy mở web "
                            "kiểm tra thủ công; nếu web vẫn bình thường, có "
                            "thể tuần mẫu trống — thử chọn dải tuần khác."
                        ),
                    )
                    self._emit("halt", tuan=sample_tuan,
                              message="Canary check thất bại — dừng trước khi ghi.")
                    report.finished_at = time.time()
                    report.stopped = True
                    self._emit("plan_done",
                              message="completed=False (canary check thất bại)")
                    return report
            except Exception as e:
                # Canary fetch lỗi (mạng/session) → cảnh báo nhưng KHÔNG halt:
                # _execute_week sẽ tự xử lý lỗi fetch của từng tuần. Canary
                # là lớp phát hiện sớm, không phải gate cứng cho network blip.
                self._emit("warning",
                          message=f"Không chạy được kiểm tra cấu trúc web: {e}")

        # v2.1 (FIX): Đã rollback _seed_progress_from_web vì nó override
        # ppct_start mà user setup tay. User là source-of-truth tuyệt đối.
        # Tool chỉ scan để DETECT extras + skip-if-match, KHÔNG patch PPCT plan.

        # NOTE: Trong lúc fill, PlanExecutor bật blocker cho endpoint
        # autofill PPCT của web rồi tự set PPCT/tên bài. Verify sau save
        # vẫn là safety net cuối cùng.

        for wp in plan.week_plans:
            if self.stop_event.is_set():
                self._emit("stop", message="user_stop")
                break

            wr = self._execute_week(wp, dry_run=dry_run)
            report.week_results.append(wr)
            if not wr.save_ok and not wr.skipped:
                report.error_count += 1
                # Yêu cầu của user: nếu 1 tuần fail, DỪNG ngay. Người dùng tự
                # xem lỗi (trên web hoặc trong nhật ký), sửa rồi chạy lại từ
                # đầu — KHÔNG tự chạy tiếp các tuần kế tiếp tránh lỗi liên tiếp.
                if self.halt_on_error and not dry_run:
                    # Build message giàu thông tin: server msg + danh sách
                    # ô vàng (nếu có) + hint cụ thể theo loại lỗi.
                    parts = [f"Tuần {wp.tuan} không lưu được."]
                    server_msg = str(wr.save_msg or "").strip()
                    if server_msg:
                        parts.append(f'Server báo: "{server_msg}"')
                    if wr.save_errors:
                        detail = format_save_errors_for_user(
                            wr.save_errors, wp.fill_ops, max_lines=15,
                        )
                        if detail:
                            parts.append(
                                f"Có {len(wr.save_errors)} ô bị flag:\n{detail}"
                            )
                    err_kind = classify_save_error_msg(server_msg)
                    parts.append(ERROR_HINTS.get(err_kind, ERROR_HINTS["unknown"]))
                    parts.append(
                        f"Sau khi sửa, hãy chạy lại từ tuần {wp.tuan} "
                        "(KHÔNG cần chạy lại các tuần đã thành công ở trước)."
                    )
                    halt_msg = "\n\n".join(parts)
                    self._emit("halt", tuan=wp.tuan, message=halt_msg)
                    self.stop_event.set()
                    if self.resume_path:
                        self._persist_resume(plan, report)
                    break

            # Persist resume snapshot
            if self.resume_path:
                self._persist_resume(plan, report)

        report.finished_at = time.time()
        report.completed = not self.stop_event.is_set()
        report.stopped = self.stop_event.is_set()
        # Truyền số fallback đã áp dụng vào report để UI render summary.
        report.ten_bai_fallback_count = int(self._fallback_count)
        self._emit("plan_done",
                   message=f"completed={report.completed} ok_weeks={len(report.successful_weeks)} errors={report.error_count}"
                           + (f" fallback={self._fallback_count}" if self._fallback_count else ""))
        return report

    def _ten_bai_value_for_dom(self, op: FillOp) -> str:
        """Return exact txtTenBai value PlanExecutor writes for an op."""
        ten_bai_to_write = op.ten_bai or ""
        if (
            self.ten_bai_fallback
            and not ten_bai_to_write
            and op.ppct
            and op.ppct > 0
        ):
            return " "
        return ten_bai_to_write

    def _write_ppct_fields_for_ops(
        self,
        ops: list[FillOp],
        failed_ops: set[str],
    ) -> int:
        """Re-write PPCT/name/status fields for ops that passed dropdown phases."""
        written = 0
        for op in ops:
            if self.stop_event.is_set():
                break
            if op.row_key in failed_ops:
                continue
            try:
                self.client.page.evaluate(
                    _JS_SET_PPCT_TEN_BAI,
                    {
                        "rk": op.row_key,
                        "ppct": op.ppct if op.ppct else "",
                        "ten_bai": self._ten_bai_value_for_dom(op),
                        "ghi_chu": getattr(op, "ghi_chu", "") or "",
                        "trang_thai": getattr(op, "trang_thai", "0") or "0",
                    },
                )
                written += 1
            except Exception:
                # Final verification catches missed writes. This helper is
                # best-effort because it also runs as a pre-save re-confirm.
                pass
        return written

    def _verify_fill_ops_dom(
        self,
        ops: list[FillOp],
        failed_ops: set[str],
        max_diffs: int = 12,
    ) -> tuple[bool, list[str]]:
        """Verify DOM still matches intended values immediately before save."""
        check_ops = [op for op in ops if op.row_key not in failed_ops]
        ids: list[str] = []
        for op in check_ops:
            rk = op.row_key
            ids.extend([
                f"cboLopHoc_{rk}",
                f"cboMonHoc_{rk}",
                f"cboPhanMon_{rk}",
                f"txtTietPPCT_{rk}",
                f"txtTenBai_{rk}",
                f"txtGhiChu_{rk}",
                f"cboTrangThai_{rk}",
            ])
        if not ids:
            return True, []
        try:
            raw_state = self.client.page.evaluate(_JS_READ_FIELDS, ids) or {}
        except Exception as e:
            return False, [f"Không đọc được DOM trước khi lưu: {e}"]

        diffs: list[str] = []

        def raw_value(field_id: str) -> str | None:
            state = raw_state.get(field_id)
            if state is None:
                return None
            return str((state or {}).get("value") or "")

        for op in check_ops:
            rk = op.row_key
            expected = {
                f"cboLopHoc_{rk}": str(op.lop_id or ""),
                f"cboMonHoc_{rk}": str(op.mon_id or ""),
                f"cboPhanMon_{rk}": str(op.phan_mon_id or "0"),
                f"txtTietPPCT_{rk}": str(op.ppct if op.ppct else ""),
                f"txtTenBai_{rk}": self._ten_bai_value_for_dom(op),
                f"txtGhiChu_{rk}": str(getattr(op, "ghi_chu", "") or ""),
                f"cboTrangThai_{rk}": str(getattr(op, "trang_thai", "0") or "0"),
            }
            for field_id, want in expected.items():
                got = raw_value(field_id)
                if got is None:
                    diffs.append(f"{field_id}: thiếu control")
                elif got != want:
                    diffs.append(f"{field_id}: web={got!r}, expected={want!r}")
                if len(diffs) >= max_diffs:
                    return False, diffs
        return not diffs, diffs

    def _disable_autofill_blocker_best_effort(self):
        """Turn off KHDH autofill blocker before an early return."""
        try:
            self.client.page.evaluate(_JS_SET_BLOCK_AUTOFILL, False)
        except Exception:
            pass

    def _emit_blocker_stats(self, tuan: int, label: str = ""):
        """Đọc số XHR/fetch/script đã bị blocker abort + emit qua event.

        Caller pass `label` để phân biệt context (vd "phase_a_done",
        "before_save", "after_save").
        """
        try:
            stats = self.client.get_blocked_autofill_stats()
            if stats and stats.get("ok"):
                count = int(stats.get("count") or 0)
                by = stats.get("by_transport") or {}
                self._emit(
                    "blocker_stats", tuan=tuan,
                    message=f"label={label} blocked_total={count} "
                            f"xhr={by.get('xhr', 0)} "
                            f"fetch={by.get('fetch', 0)} "
                            f"script={by.get('script', 0)}",
                    count=count, by_transport=by, label=label,
                )
        except Exception:
            pass

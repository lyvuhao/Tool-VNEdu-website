"""Thực thi một tuần KHDH.

`_execute_week` gọi lần lượt các bước; bước nào kết thúc tuần sớm (lỗi / bỏ qua / dừng) thì tự gửi
"week_done" và trả về False:

    _switch_week_for_run()        1. đổi tuần trên web + xác minh đúng tuần
    _enable_edit_mode_for_run()   1b. bật chế độ "Sửa" + reload bảng
    _scan_and_diff_for_run()      1c. so với web: đủ cả tuần -> bỏ qua; thiếu một phần -> chỉ điền ô thiếu
    _stop_before_pre_action()     dừng trước pre-action nếu người dùng bấm Dừng
    _run_pre_action()             2. gen từ tuần trước / theo TKB
    _fill_week_ops()              3. điền ô, Phase A → D2 (week_fill.py)
    _save_week_for_run()          4. lưu + kiểm chứng sau khi lưu
    _commit_week_fallback_log()   5. ghi / hoàn tác fallback log theo kết quả lưu
"""

from __future__ import annotations

import time

from ..client.js import _JS_SET_BLOCK_AUTOFILL
from ..planner import WeekPlan
from .errors import classify_save_error_msg, ERROR_HINTS, format_save_errors_for_user
from .models import WeekResult


class _WeekRun:
    """Trạng thái một lần chạy tuần: kế hoạch, kết quả, ô đang điền và ô đã lỗi."""

    __slots__ = ("wp", "wr", "dry_run", "t0", "active_ops", "failed_ops")

    def __init__(self, wp, wr, dry_run, t0):
        self.wp = wp
        self.wr = wr
        self.dry_run = dry_run
        self.t0 = t0
        self.active_ops = []
        # row_key của các ô đã lỗi ở phase trước -> bỏ qua ở phase sau
        self.failed_ops: set[str] = set()


class WeekExecutionMixin:
    """Thực thi một tuần KHDH."""

    def _execute_week(self, wp: WeekPlan, dry_run: bool) -> WeekResult:
        wr = WeekResult(tuan=wp.tuan)
        if wp.skip_reason:
            wr.skipped = True
            wr.skip_reason = wp.skip_reason
            self._emit("week_skip", tuan=wp.tuan, message=wp.skip_reason)
            return wr

        # Reset danh sách entry log pending cho tuần này — nếu save tuần
        # thất bại, mình sẽ rollback các entry đã ghi để tránh log bị "phình"
        # bởi các phiên không thật sự lưu được.
        self._week_pending_log_entries = []

        run = _WeekRun(wp, wr, dry_run, time.time())
        self._emit("week_start", tuan=wp.tuan,
                   message=f"strategy={wp.strategy.value} pre_action={wp.pre_action} ops={len(wp.fill_ops)}")

        if not dry_run:
            if not self._switch_week_for_run(run):
                return wr
            self._enable_edit_mode_for_run(run)
            if not self._scan_and_diff_for_run(run):
                return wr

        if self._stop_before_pre_action(run):
            return wr
        self._run_pre_action(run)

        run.active_ops = [op for op in wp.fill_ops if not op.skip]
        if run.active_ops and not dry_run:
            if not self._fill_week_ops(run):
                return wr

        if (run.active_ops or wp.pre_action) and not dry_run:
            if not self._save_week_for_run(run):
                return wr

        self._commit_week_fallback_log(run)
        self._finish_week(run, f"ok={wr.save_ok} errors={len(wr.save_errors)}")
        return wr

    def _finish_week(self, run, summary):
        """Ghi thời gian chạy và gửi "week_done" (`summary` + took=...ms)."""
        run.wr.duration_ms = int((time.time() - run.t0) * 1000)
        self._emit("week_done", tuan=run.wp.tuan,
                   message=f"{summary} took={run.wr.duration_ms}ms")

    # ------------------------------------------------------------------
    # 1. Đổi tuần, bật chế độ Sửa, so với web
    # ------------------------------------------------------------------

    def _switch_week_for_run(self, run) -> bool:
        """Đổi tuần trên UI + xác minh đã đúng tuần.

        Rất quan trọng: tránh ghi nhầm dữ liệu vào tuần khác. Polling tối đa 8s, sau 8s vẫn không đúng
        tuần → fail luôn tuần đó (trả về False).
        """
        wp = run.wp
        wr = run.wr
        verified_tuan = self._switch_and_verify_week(wp.tuan)
        if verified_tuan == wp.tuan:
            return True
        wr.save_ok = False
        wr.save_msg = (
            f"Không chuyển được sang tuần {wp.tuan} (web vẫn ở "
            f"tuần {verified_tuan} sau 3 lần thử). "
            "Có thể bạn đã click sang tuần khác trên web cùng lúc, "
            "hoặc combobox bị treo. Hãy reload tab VnEdu rồi chạy lại."
        )
        self._emit("error", tuan=wp.tuan, message=wr.save_msg)
        self._disable_autofill_blocker_best_effort()
        self._finish_week(run, "ok=False errors=1")
        return False

    def _enable_edit_mode_for_run(self, run):
        """BẮT BUỘC bật mode "Sửa" (rdoEdit) TRƯỚC KHI set fields.

        Nếu web đang ở rdoEdit2 (Gợi ý theo TKB), các ô hiển thị data suggested từ TKB — khi set
        lop/mon/pm, web có thể override hoặc reject. Bật rdoEdit + reload table để có form trống sạch.
        """
        try:
            self.client.enable_edit_mode()
            time.sleep(0.3)
            # Reload table sau khi đổi mode để DOM render đúng mode mới.
            # (#3) Chờ DOM render thật thay vì sleep cứng 2s — mạng chậm
            # 2s có thể không đủ → scan-and-diff đọc DOM trống → sai.
            self.client.reload_table_and_wait(
                floor_s=2.0, max_extra_s=6.0, stop_event=self.stop_event,
            )
        except Exception as e:
            self._emit("warning", tuan=run.wp.tuan,
                       message=f"enable_edit_mode: {e}")

    def _scan_and_diff_for_run(self, run) -> bool:
        """v2: Scan-and-diff tuần này TRƯỚC KHI fill ops.

        - Mọi op đã có sẵn trên web → SKIP toàn tuần (idempotent) → trả về False (tuần đã xong).
        - Thiếu một phần → chỉ giữ ops cần fill (đánh dấu `op.skip` cho ô đã khớp).
        - Phát hiện tiết bù (cboTrangThai = "Dạy bù"/"Chèn lịch"...) → báo UI và shift PPCT các tuần sau.
        Scan lỗi → fill như bình thường, không chặn.
        """
        wp = run.wp
        try:
            skip_full, ops_to_keep, extras, scan_progress = \
                self._scan_and_diff_week(wp)
            # Cache scan_progress để UI có thể đọc PPCT next real-time
            if not hasattr(self, "_last_scan_progress"):
                self._last_scan_progress = {}
            self._last_scan_progress[wp.tuan] = scan_progress

            if extras:
                self._report_extra_lessons(wp, extras)

            if skip_full:
                self._finish_week_already_complete(run)
                return False

            if len(ops_to_keep) < sum(1 for o in wp.fill_ops if not o.skip):
                # Partial supplement: web đã có 1 phần, chỉ fill ô thiếu
                skipped_count = sum(1 for o in wp.fill_ops if not o.skip) - len(ops_to_keep)
                self._emit("week_partial_supplement", tuan=wp.tuan,
                           message=f"Bổ sung {len(ops_to_keep)} ô thiếu "
                                   f"(đã có sẵn {skipped_count} ô khớp web).")
                # Mark ops không cần fill bằng skip
                keep_keys = {op.row_key for op in ops_to_keep}
                for op in wp.fill_ops:
                    if op.row_key not in keep_keys:
                        op.skip = True
        except Exception as e:
            # Scan thất bại → fallback: fill như bình thường, không block
            self._emit("warning", tuan=wp.tuan,
                       message=f"scan_and_diff exception: {e} — fallback fill all")
        return True

    def _report_extra_lessons(self, wp, extras):
        """Tiết dạy bù / chèn lịch trên web: shift PPCT cho các tuần KẾ TIẾP và báo UI."""
        extras_summary = ", ".join(
            f"({e['thu']}/{e['buoi']}/{e['tiet']}:PPCT={e['ppct']})"
            for e in extras[:5]
        )
        # v2.2: Group extras theo (lop, mon, pm) để biết mỗi
        # group có bao nhiêu tiết bù trong tuần này — shift PPCT
        # cho các tuần KẾ TIẾP (không động tuần này và trước nó).
        extras_by_group: dict[str, int] = {}
        for e in extras:
            gk = f"{e['lop_id']}|{e['mon_id']}|{e['phan_mon_id']}"
            extras_by_group[gk] = extras_by_group.get(gk, 0) + 1

        shifted = self._apply_inflight_ppct_shift(
            current_tuan=wp.tuan,
            extras_by_group=extras_by_group,
        )

        extra_msg = (
            f"Phát hiện {len(extras)} tiết dạy bù/chèn lịch trên web "
            f"tuần {wp.tuan}: {extras_summary}"
            + ("…" if len(extras) > 5 else "")
            + (f". Đã shift +PPCT cho {shifted} ô ở các tuần kế tiếp."
               if shifted > 0 else "")
        )
        self._emit("extra_detected", tuan=wp.tuan,
                   message=extra_msg, extras=extras,
                   shifted=shifted)
        # Bump counter trong report (nếu đang được tracked)
        if self._report is not None:
            self._report.extras_detected_count += len(extras)

    def _finish_week_already_complete(self, run):
        wp = run.wp
        wr = run.wr
        wr.skipped = True
        wr.skip_reason = (
            f"Tuần {wp.tuan} đã đầy đủ và đúng trên web "
            f"({len([o for o in wp.fill_ops if not o.skip])} ô khớp). "
            "Bỏ qua tuần này."
        )
        wr.save_ok = True
        self._emit("week_already_complete", tuan=wp.tuan,
                   message=wr.skip_reason)
        if self._report is not None:
            self._report.weeks_already_complete += 1
        self._finish_week(run, "ok=True skipped=True")

    # ------------------------------------------------------------------
    # 2. Pre-action (gọi API gen)
    # ------------------------------------------------------------------

    def _stop_before_pre_action(self, run) -> bool:
        """Stop check trước pre_action — tránh chạy 1 tuần thừa khi user dừng ngay sau check đầu vòng."""
        wp = run.wp
        if (self.stop_event is not None and self.stop_event.is_set()
                and not run.dry_run and wp.pre_action):
            self._emit("stop", tuan=wp.tuan,
                       message="Bỏ qua pre_action vì stop_event")
            self._finish_week(run, "ok=False errors=1")
            return True
        return False

    def _run_pre_action(self, run):
        """gen_prev: genLichBaoGiangTuan từ tuần X-1; gen_tkb: genLichBaoGiangTuanTheoTKB. Rồi reload bảng."""
        wp = run.wp
        wr = run.wr
        if run.dry_run or wp.pre_action not in ("gen_prev", "gen_tkb"):
            return
        report_errors = wp.pre_action == "gen_prev"   # gen_tkb: lỗi chỉ ghi vào kết quả tuần
        if report_errors:
            self._emit("pre_action", tuan=wp.tuan, message="genLichBaoGiangTuan từ tuần X-1")
        else:
            self._emit("pre_action", tuan=wp.tuan, message="genLichBaoGiangTuanTheoTKB")
        try:
            if report_errors:
                res = self.client.gen_from_prev_week(wp.tuan)
            else:
                res = self.client.gen_from_tkb(wp.tuan)
            wr.pre_action = wp.pre_action
            wr.pre_action_ok = bool(res.ok and res.data and res.data.get("success", True))
            wr.pre_action_msg = (res.data and res.data.get("msg")) or ""
            if report_errors and not wr.pre_action_ok:
                self._emit("error", tuan=wp.tuan, message=f"gen_prev failed: {wr.pre_action_msg}")
        except Exception as e:
            wr.pre_action_ok = False
            wr.pre_action_msg = f"{type(e).__name__}: {e}"
            if report_errors:
                self._emit("error", tuan=wp.tuan, message=f"gen_prev exception: {e}")
        self._interruptible_sleep(self.wait_after_pre_action_s)
        # Reload sau gen
        try:
            self.client.reload_table_and_wait(
                floor_s=1.5, max_extra_s=6.0, stop_event=self.stop_event,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 4. Lưu + kiểm chứng
    # ------------------------------------------------------------------

    def _save_week_for_run(self, run) -> bool:
        """Lưu tuần ở chế độ "Sửa" (rdoEdit) — KHÔNG phải "Sửa (Gợi ý theo TKB)" (rdoEdit2, thường bị web
        khoá lưu). Trả về False nếu hủy lưu (sai tuần / người dùng dừng) — tuần đã kết thúc.
        """
        wp = run.wp
        wr = run.wr
        # Kiểm tra lần cuối: combobox vẫn đúng tuần? Nếu UI bị nhảy tuần
        # giữa chừng (do user click hoặc reload), KHÔNG được save.
        cur = self._read_current_week()
        if cur != wp.tuan:
            wr.save_ok = False
            wr.save_msg = (
                f"Ngay trước khi nhấn Lưu, web đang ở tuần {cur} thay "
                f"vì tuần {wp.tuan}. Có vẻ bạn vừa click sang tuần "
                "khác trên Chrome trong lúc tool đang điền. Tool hủy "
                "thao tác Lưu để KHÔNG ghi nhầm dữ liệu vào tuần khác. "
                "Hãy giữ tab VnEdu yên trong lúc tool chạy, rồi chạy "
                f"lại từ tuần {wp.tuan}."
            )
            self._emit("error", tuan=wp.tuan, message=wr.save_msg)
            self._finish_week(run, "ok=False errors=1")
            return False
        try:
            self.client.enable_edit_mode()
            time.sleep(0.3)
        except Exception as e:
            self._emit("warning", tuan=wp.tuan,
                       message=f"enable_edit_mode: {e}")
        # Stop check ngay TRƯỚC save — tránh ghi 1 tuần thừa khi user
        # đã bấm Dừng trong khi đang fill ops.
        if self.stop_event is not None and self.stop_event.is_set():
            wr.save_ok = False
            wr.save_msg = "Đã dừng trước khi lưu"
            self._emit("stop", tuan=wp.tuan,
                       message="Bỏ qua save vì stop_event")
            self._disable_autofill_blocker_best_effort()
            self._finish_week(run, "ok=False errors=1")
            return False

        self._click_save_week(run)
        time.sleep(0.5)
        # v3.2: TẮT XHR autofill blocker sau khi save xong — để bước
        # verify-after-save fetch_week và scan tuần kế không bị block.
        # v3.3: Emit số XHR đã chặn để UI hiện real-time.
        try:
            self._emit_blocker_stats(wp.tuan, label="after_save")
            self.client.page.evaluate(_JS_SET_BLOCK_AUTOFILL, False)
        except Exception:
            pass

        if (wr.save_ok and self.verify_after_save
                and not self.stop_event.is_set()):
            self._verify_saved_week(run)
        return True

    def _click_save_week(self, run):
        """Bấm Lưu. Server báo fail HOẶC có ô lỗi → save_ok = False (không chỉ là cảnh báo)."""
        wp = run.wp
        wr = run.wr
        self._emit("save", tuan=wp.tuan, message="Click Lưu")
        try:
            save_res = self.client.save_week_via_button(wait_s=self.wait_after_save_s)
            wr.save_ok = save_res.ok and save_res.success and not save_res.errors
            wr.save_msg = save_res.msg
            wr.save_errors = list(save_res.errors)
            if save_res.errors:
                # Render TẤT CẢ ô lỗi với label tiếng Việt + context lớp/môn
                detail = format_save_errors_for_user(
                    save_res.errors, wp.fill_ops, max_lines=20,
                )
                self._emit(
                    "error", tuan=wp.tuan,
                    message=(
                        f"Web báo {len(save_res.errors)} ô lỗi:\n"
                        f"{detail}"
                    ),
                )
            if not save_res.success and save_res.msg:
                # Server trả msg rõ — phân loại để có hint cụ thể.
                err_kind = classify_save_error_msg(save_res.msg)
                hint = ERROR_HINTS.get(err_kind, ERROR_HINTS["unknown"])
                self._emit(
                    "error", tuan=wp.tuan,
                    message=(
                        f"Web từ chối lưu cả tuần.\n"
                        f"Server báo: \"{save_res.msg}\"\n{hint}"
                    ),
                )
                self._emit(
                    "warning", tuan=wp.tuan,
                    message=(
                        "Lưu ý: web KHDH lưu all-or-nothing — chỉ cần "
                        "1 ô lỗi là CẢ TUẦN không lưu được. Sửa các ô "
                        "vàng rồi chạy lại từ tuần này."
                    ),
                )
        except Exception as e:
            wr.save_ok = False
            wr.save_msg = f"{type(e).__name__}: {e}"
            self._emit("error", tuan=wp.tuan, message=f"save exception: {e}")

    def _verify_saved_week(self, run):
        """(#1/#4/#6) VERIFY-AFTER-SAVE: đọc lại web kiểm chứng dữ liệu thật khớp ý định.

        Bắt được: web autofill ghi đè tên bài sau debounce, autofill-blocker chặn thừa/thiếu, server trả
        success=true nhưng không lưu trọn vẹn. Lệch → coi như tuần FAIL (halt-on-error sẽ dừng).
        """
        wp = run.wp
        wr = run.wr
        self._emit("save", tuan=wp.tuan,
                   message="Đọc lại web để kiểm chứng…")
        verified, diffs = self._verify_week_after_save(
            wp.tuan, run.active_ops,
        )
        if verified:
            self._emit("save", tuan=wp.tuan,
                       message="✓ Đã kiểm chứng: web khớp đúng ý định.")
            return
        wr.save_ok = False
        detail = "\n".join(diffs[:12])
        if len(diffs) > 12:
            detail += f"\n  • … và {len(diffs) - 12} ô khác"
        wr.save_msg = (
            f"Web báo lưu thành công nhưng kiểm chứng lại thấy "
            f"{len(diffs)} ô lệch."
        )
        self._emit(
            "error", tuan=wp.tuan,
            message=(
                f"⚠ Tuần {wp.tuan}: web nói đã lưu nhưng dữ liệu "
                f"thực tế KHÔNG khớp:\n{detail}\n\n"
                "Nguyên nhân thường gặp: web tự điền lại tên bài "
                "sau khi tool lưu, hoặc mạng chậm khiến lưu chưa "
                "trọn. Hãy mở web tuần này kiểm tra rồi chạy lại."
            ),
        )

    # ------------------------------------------------------------------
    # 5. Fallback log
    # ------------------------------------------------------------------

    def _commit_week_fallback_log(self, run):
        """Persist hoặc rollback fallback log theo kết quả lưu tuần, rồi dọn entry cũ của tuần.

        - Lưu OK + có entry pending → ghi log xuống đĩa (commit).
        - Lưu FAIL + có entry pending → bỏ entry khỏi log (rollback, tránh log "phình" bởi các phiên
          không thực sự lưu được).
        - Lưu OK: bỏ các entry của tuần mà ô đã có Tên bài thật.
        """
        wp = run.wp
        wr = run.wr
        dry_run = run.dry_run
        if self._week_pending_log_entries and self.fallback_log is not None and not dry_run:
            try:
                if wr.save_ok:
                    self.fallback_log.save()
                else:
                    for tuan_k, row_k in self._week_pending_log_entries:
                        self.fallback_log.remove(tuan_k, row_k)
                    # Sau rollback vẫn save xuống đĩa để file consistent
                    # (vd phiên trước đã có entry hợp lệ thì giữ nguyên).
                    self.fallback_log.save()
            except Exception as log_e:
                self._emit(
                    "warning", tuan=wp.tuan,
                    message=f"Không lưu được fallback log: {log_e}",
                )
        # Cleanup tự động các entry stale ở tuần này: nếu ô đã có Tên bài
        # thật (raw value khác " " và không rỗng) thì bỏ entry khỏi log.
        # An toàn: chỉ chạy khi save tuần thành công và log non-empty.
        if (
            wr.save_ok
            and self.fallback_log is not None
            and not dry_run
            and any(int(e.tuan) == int(wp.tuan)
                    for e in self.fallback_log.all_entries())
        ):
            try:
                self._cleanup_stale_log_entries_for_week(wp.tuan)
            except Exception as cleanup_e:
                self._emit(
                    "warning", tuan=wp.tuan,
                    message=f"Cleanup log: {cleanup_e}",
                )
        self._week_pending_log_entries = []

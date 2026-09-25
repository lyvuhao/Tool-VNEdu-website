"""Worker khôi phục KHDH từ file JSON."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from ...engine.backup.models import BackupFile, BackupMetadata, BackupSlot, BackupWeek
from ...engine.backup.snapshots import SnapshotManager
from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from ...engine.executor.executor import PlanExecutor
from ...engine.parser import SlotData, WeekData
from ...engine.planner import FillOp, PlanMode, PlanReport, PlanRequest, PlanStrategy, WeekPlan
from ...log import logger
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — RestoreWorker (khôi phục KHDH từ JSON backup)
# =====================================================================

# Restore mode giữ để tương thích hồ sơ/UI cũ. Luồng hiện tại luôn xóa tuần
# đã chọn trên VnEdu trước rồi ghi lại từ tệp sao lưu để tránh trùng cuốn chiếu.
RESTORE_MODE_OVERWRITE = "overwrite"


RESTORE_MODE_MERGE = "merge"


RESTORE_MODE_SKIP = "skip"


@dataclass
class RestoreWeekResult:
    """Kết quả restore 1 tuần."""
    tuan: int
    save_ok: bool = True
    save_msg: str = ""
    save_errors: list = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    ops_count: int = 0
    duration_ms: int = 0


@dataclass
class RestoreReport:
    """Tổng hợp kết quả restore."""
    started_at: float = 0
    finished_at: float = 0
    completed: bool = False
    stopped: bool = False
    week_results: list[RestoreWeekResult] = field(default_factory=list)
    error_count: int = 0

    @property
    def total_duration_ms(self) -> int:
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at) * 1000)
        return 0

    @property
    def successful_weeks(self) -> list[int]:
        return [r.tuan for r in self.week_results if r.save_ok and not r.skipped]


def _backup_slot_to_fill_op(bs: BackupSlot, tuan: int) -> "FillOp":
    """Convert BackupSlot → FillOp.

    Quan trọng: gắn `trang_thai` + `ghi_chu` vào FillOp (Section 5 sẽ
    extend FillOp + _JS_SET_PPCT_TEN_BAI để honor 2 field này).
    """
    op = FillOp(
        tuan=tuan,
        row_key=bs.row_key,
        lop_id=bs.lop_id,
        lop_text=bs.lop_text,
        mon_id=bs.mon_id,
        mon_text=bs.mon_text,
        phan_mon_id=bs.phan_mon_id,
        phan_mon_text=bs.phan_mon_text,
        ppct=int(bs.ppct) if bs.ppct and bs.ppct.lstrip("-").isdigit() else 0,
        ten_bai=bs.ten_bai or "",
        source="backup",
        notes="Khôi phục từ tệp sao lưu",
    )
    # Extension fields (Section 5 thêm vào FillOp dataclass)
    op.ghi_chu = bs.ghi_chu or ""
    op.trang_thai = bs.trang_thai or "0"
    return op


class RestoreWorker(threading.Thread):
    """Khôi phục KHDH từ BackupFile + danh sách tuần user chọn.

    SIẾT LOGIC:
    - Auto-snapshot trước khi restore (nếu enabled). Snapshot ghi vào
      `<excel>.snapshots/snapshot-<ts>-before-restore.khdh-backup.json`
      hoặc thư mục snapshot global khi hồ sơ chưa lưu.
    - Mọi mode đều xóa các tuần đã chọn trên VnEdu trước rồi ghi lại từ
      backup. Cách này tránh dữ liệu cuốn chiếu còn sót làm trùng PPCT /
      trạng thái khi chỉ một tuần thay đổi.
    - Conflict mode chỉ còn giữ để tương thích UI/hồ sơ cũ; sau khi xóa,
      kế hoạch ghi được xử lý như ghi lại toàn bộ slot hợp lệ trong backup.
    - Halt-on-error: 1 tuần fail save → DỪNG ngay.
    - Reuse PlanExecutor pipeline để set fields + click Lưu (đã ổn định).

    Events qua queue:
        ("status", text)
        ("event", ExecutorEvent)
        ("week_result", RestoreWeekResult)
        ("done", RestoreReport)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        backup: BackupFile,
        target_weeks: list[int],         # Tuần user chọn restore
        conflict_mode: str,              # OVERWRITE | MERGE | SKIP
        excel_path: str | Path | None,   # Để auto-snapshot
        auto_snapshot: bool,
        fast_safe_mode: bool,
        event_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        super().__init__(daemon=True, name="KHDH-Restore")
        self.port = port
        self.backup = backup
        self.target_weeks = sorted(set(int(w) for w in target_weeks))
        self.conflict_mode = conflict_mode
        self.excel_path = Path(excel_path) if excel_path else None
        self.auto_snapshot = bool(auto_snapshot)
        self.fast_safe_mode = bool(fast_safe_mode)
        self.q = event_queue
        self.stop_event = stop_event
        self._pre_deleted_weeks = False

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi khôi phục: {e}\n\n{tb}"))

    def _run_inner(self):
        if not self.target_weeks:
            self.q.put(("error", "Không có tuần nào được chọn để khôi phục."))
            return

        with sync_playwright() as pw:
            self.q.put(("status", "Đang kết nối Chrome…"))
            try:
                browser = pw.chromium.connect_over_cdp(
                    f"http://localhost:{self.port}", timeout=5000,
                )
            except Exception as e:
                self.q.put(("error", format_chrome_connect_error(self.port, e)))
                return
            page = BootstrapWorker._find_vnedu_page(browser)
            if not page:
                self.q.put(("error", format_no_vnedu_tab_error()))
                return
            try:
                page.bring_to_front()
            except Exception:
                pass

            client = KHDHClient(page)
            try:
                client.fetch_context()
            except Exception as e:
                self.q.put(("error", format_context_read_error(e)))
                return

            # Auto-snapshot trước khi restore.
            # CRITICAL: mọi chế độ khôi phục đều xóa tuần được chọn trước
            # khi ghi lại. Nếu snapshot đang bật nhưng fail, phải DỪNG để
            # tránh mất dữ liệu web hiện có.
            if self.auto_snapshot:
                snap_ok = self._do_auto_snapshot(client)
                if not snap_ok:
                    self.q.put((
                        "error",
                        "❌ Không tạo được bản sao lưu nhanh — TUYỆT ĐỐI "
                        "không xóa khi chưa có safety net.\n\n"
                        "Hãy kiểm tra:\n"
                        "  • Hồ sơ Excel đã lưu chưa\n"
                        "  • Ổ đĩa còn dung lượng không\n"
                        "  • Quyền ghi vào thư mục hồ sơ",
                    ))
                    return
            elif self.excel_path is None:
                self.q.put((
                    "status",
                    "⚠ Bạn đã tắt bản sao lưu nhanh. Công cụ vẫn sẽ xóa "
                    "các tuần đã chọn trước khi ghi lại.",
                ))
            else:
                self.q.put((
                    "status",
                    "⚠ Bạn đã tắt bản sao lưu nhanh. Công cụ vẫn sẽ xóa "
                    "các tuần đã chọn trước khi ghi lại.",
                ))

            # Delete-before-restore: VnEdu lưu KHBD theo kiểu cuốn chiếu.
            # Nếu còn dữ liệu cũ ở tuần đã chọn, chỉ một thay đổi nhỏ cũng
            # có thể làm trùng PPCT / trạng thái khi ghi lại. Vì vậy mọi
            # chế độ đều phải xóa các tuần target trước rồi mới nhập lại
            # từ tệp backup.
            ok = self._delete_target_weeks_before_restore(client)
            if not ok:
                return  # error đã put
            self._pre_deleted_weeks = True

            # Build PlanReport từ backup
            plan = self._build_plan_from_backup(client)
            if plan is None:
                return  # error đã put

            # Execute qua PlanExecutor — VERIFY-AFTER-SAVE per week.
            # Logic siết:
            #   - Chạy từng tuần riêng lẻ qua mini-PlanReport.
            #   - Sau mỗi tuần save thành công → đợi web settle + fetch lại
            #     để verify đã lưu đúng theo backup.
            #   - Mismatch (vd web mất tên bài do debounce) → retry verify
            #     tối đa 3 lần. Vẫn fail → DỪNG, báo cho user biết tuần
            #     nào lệch để xử lý tay.
            #   - Halt-on-error: 1 tuần fail save HOẶC verify → dừng cả
            #     loop. User dùng snapshot để khôi phục.
            self.q.put((
                "status",
                f"🚀 Bắt đầu khôi phục {len(plan.week_plans)} tuần "
                "(verify mỗi tuần"
                + (", chế độ nhanh an toàn" if self.fast_safe_mode else "")
                + ")…",
            ))

            # Aggregate report
            agg_started = time.time()
            all_week_results: list = []
            agg_error_count = 0
            stopped = False

            for wp in plan.week_plans:
                if self.stop_event.is_set():
                    stopped = True
                    break
                # Mini plan chỉ có 1 tuần — reuse PlanExecutor pipeline
                mini_plan = PlanReport(request=plan.request)
                mini_plan.week_plans = [wp]
                executor = PlanExecutor(
                    client,
                    on_event=lambda ev: self.q.put(("event", ev)),
                    stop_event=self.stop_event,
                    wait_after_pre_action_s=2.0,
                    # Chế độ thường giữ nhịp cũ 2.5s. Chế độ nhanh an toàn
                    # ngủ ngắn hơn nhưng PlanExecutor sẽ verify DOM 100%
                    # ngay trước save và tự fallback chậm nếu còn race.
                    wait_after_set_fields_s=(
                        0.8 if self.fast_safe_mode else 2.5
                    ),
                    wait_after_save_s=10.0,
                    halt_on_error=True,
                    ten_bai_fallback=True,
                    fallback_log=None,
                    fast_safe_mode=self.fast_safe_mode,
                )
                try:
                    week_report = executor.execute(mini_plan, dry_run=False)
                except Exception as e:
                    tb = traceback.format_exc()
                    self.q.put((
                        "error",
                        f"❌ Lỗi tuần {wp.tuan}: {e}\n\n"
                        "Khôi phục đã DỪNG. Mở tab 📸 Snapshots để "
                        f"khôi phục lại các tuần đã ghi đè.\n\n{tb}",
                    ))
                    return
                all_week_results.extend(week_report.week_results)
                # Check tuần này có save_ok không
                this_wr = (week_report.week_results[0]
                           if week_report.week_results else None)
                if this_wr and not this_wr.save_ok and not this_wr.skipped:
                    agg_error_count += 1
                    self.q.put((
                        "error",
                        f"❌ Tuần {wp.tuan} không lưu được — DỪNG khôi phục.\n\n"
                        f"Server báo: {this_wr.save_msg}\n\n"
                        "Mở tab 📸 Snapshots để khôi phục các tuần "
                        "đã ghi đè trước đó.",
                    ))
                    break
                # Skip verify nếu tuần đã skip
                if this_wr and this_wr.skipped:
                    continue
                # VERIFY: fetch lại tuần và so sánh với backup
                backup_week = self.backup.week_by_num(wp.tuan)
                if backup_week is None or not backup_week.slots:
                    continue
                self.q.put((
                    "status",
                    f"  🔍 Đang kiểm tra lại tuần {wp.tuan}…",
                ))
                verified, diff_lines = self._verify_week_after_save(
                    client, wp.tuan, backup_week.slots, max_retry=3,
                )
                if not verified:
                    diff_text = "\n".join(diff_lines[:10])
                    if len(diff_lines) > 10:
                        diff_text += f"\n  • … và {len(diff_lines) - 10} ô khác"
                    self.q.put((
                        "error",
                        f"❌ Tuần {wp.tuan}: web có ô bị lệch sau khi lưu — "
                        f"DỪNG để bạn kiểm tra.\n\n"
                        f"Web auto-fill tên bài chậm hoặc save không trọn "
                        f"vẹn. Chi tiết:\n{diff_text}\n\n"
                        f"CÁCH XỬ LÝ:\n"
                        f"  1. Mở web tuần {wp.tuan} kiểm tra trực tiếp\n"
                        f"  2. Nếu mất tiết → mở tab 📸 Snapshots, "
                        f"khôi phục lại tuần {wp.tuan} từ snapshot trước restore",
                    ))
                    agg_error_count += 1
                    # Mark tuần này fail
                    if this_wr:
                        this_wr.save_ok = False
                        this_wr.save_msg = (
                            f"Verify lệch sau lưu: {len(diff_lines)} ô khác backup"
                        )
                    break
                self.q.put((
                    "status",
                    f"  ✓ Tuần {wp.tuan} đã lưu đúng theo backup",
                ))

            # Build aggregate ExecutorReport-like object
            class _AggregateReport:
                pass
            exec_report = _AggregateReport()
            exec_report.started_at = agg_started
            exec_report.finished_at = time.time()
            exec_report.completed = (
                not stopped and not self.stop_event.is_set()
                and agg_error_count == 0
            )
            exec_report.stopped = stopped or self.stop_event.is_set()
            exec_report.error_count = agg_error_count
            exec_report.week_results = all_week_results
            exec_report.successful_weeks = [
                wr.tuan for wr in all_week_results
                if wr.save_ok and not wr.skipped
            ]

            # Build RestoreReport từ ExecutorReport
            restore_report = RestoreReport(
                started_at=exec_report.started_at,
                finished_at=exec_report.finished_at,
                completed=exec_report.completed,
                stopped=exec_report.stopped,
                error_count=exec_report.error_count,
            )
            for wr in exec_report.week_results:
                restore_report.week_results.append(RestoreWeekResult(
                    tuan=wr.tuan,
                    save_ok=wr.save_ok,
                    save_msg=wr.save_msg,
                    save_errors=list(wr.save_errors),
                    skipped=wr.skipped,
                    skip_reason=wr.skip_reason,
                    duration_ms=wr.duration_ms,
                ))
            self.q.put(("done", restore_report))

    def _verify_week_after_save(
        self, client: KHDHClient, tuan: int,
        expected_slots: list["BackupSlot"],
        max_retry: int = 3,
    ) -> tuple[bool, list[str]]:
        """Đợi web settle + verify tuần đã lưu đúng theo backup.

        Logic:
          - Sau khi save 1 tuần, web cần ~1-2s để debounce auto-fill tên bài
            và XHR ghi xuống server hoàn tất.
          - Tool fetch lại tuần đó qua API (mode "Sửa") + so sánh từng slot.
          - Mismatch → retry max_retry lần (mỗi lần wait 1.5s).
          - Cuối cùng vẫn mismatch → trả False + danh sách diff để caller
            biết ô nào sai.

        So sánh field-by-field theo `BackupSlot`:
          - lop_id, mon_id, phan_mon_id (bắt buộc khớp)
          - ppct numeric (bắt buộc khớp)
          - ten_bai (so case-insensitive strip; rỗng vs " " coi là giống —
            web có thể auto-fill tên bài đúng sau debounce, nên ưu tiên
            chấp nhận tên bài web nếu không rỗng)
          - trang_thai (bắt buộc khớp)

        Returns:
            (verified, diff_lines) — diff_lines chỉ có khi verified=False.
        """
        if not expected_slots:
            return True, []

        # Build expected map — chỉ verify ô được PLAN đã actual write.
        # Filter cùng bộ với `_build_plan_from_backup`:
        #   - Chỉ slot có lop_id (has_lop)
        #   - Slot có PPCT numeric > 0 (web reject ppct=0 nên plan đã skip)
        # Nếu không filter → verify check ô plan đã skip → false-positive
        # "có ô bị lệch sau khi lưu" mặc dù tool đã skip ô đó cố ý.
        expected_by_rk: dict[str, "BackupSlot"] = {}
        for s in expected_slots:
            if not s.has_lop:
                continue
            try:
                ppct_int = int(s.ppct) if s.ppct else 0
            except (TypeError, ValueError):
                ppct_int = 0
            if ppct_int <= 0:
                # Plan đã skip slot này → đừng verify
                continue
            expected_by_rk[s.row_key] = s
        if not expected_by_rk:
            return True, []

        diff_lines: list[str] = []
        for attempt in range(1, max_retry + 1):
            if self.stop_event.is_set():
                return False, ["Đã dừng theo yêu cầu."]
            # Đợi web settle — debounce 1s + XHR commit + safety margin
            time.sleep(1.5)

            try:
                wd = client.fetch_week(tuan, is_edit=1)
            except Exception as e:
                self.q.put((
                    "status",
                    f"  ⚠ Verify tuần {tuan} lần {attempt}: fetch fail ({e}), retry…",
                ))
                continue

            actual_by_rk = {s.row_key: s for s in wd.slots}
            diff_lines = []
            mismatch = False

            for rk, expected in expected_by_rk.items():
                actual = actual_by_rk.get(rk)
                if actual is None or not actual.has_lop:
                    diff_lines.append(
                        f"  • Ô {rk}: web mất ô (đáng lẽ {expected.lop_text}/"
                        f"{expected.mon_text} PPCT={expected.ppct})"
                    )
                    mismatch = True
                    continue
                # ID bắt buộc khớp
                if (str(actual.lop_id) != str(expected.lop_id)
                        or str(actual.mon_id) != str(expected.mon_id)
                        or str(actual.phan_mon_id) != str(expected.phan_mon_id)):
                    diff_lines.append(
                        f"  • Ô {rk}: lớp/môn/phân môn lệch — "
                        f"web={actual.lop_text}/{actual.mon_text}, "
                        f"backup={expected.lop_text}/{expected.mon_text}"
                    )
                    mismatch = True
                    continue
                # PPCT bắt buộc khớp
                try:
                    a_ppct = int(actual.ppct) if actual.ppct else 0
                except (TypeError, ValueError):
                    a_ppct = 0
                try:
                    e_ppct = int(expected.ppct) if expected.ppct else 0
                except (TypeError, ValueError):
                    e_ppct = 0
                if a_ppct != e_ppct:
                    diff_lines.append(
                        f"  • Ô {rk}: PPCT lệch — web={a_ppct}, backup={e_ppct}"
                    )
                    mismatch = True
                    continue
                # Trạng thái bắt buộc khớp
                if str(actual.trang_thai or "0") != str(expected.trang_thai or "0"):
                    diff_lines.append(
                        f"  • Ô {rk}: trạng thái lệch — "
                        f"web={actual.trang_thai_text or actual.trang_thai}, "
                        f"backup={expected.trang_thai_text or expected.trang_thai}"
                    )
                    mismatch = True
                    continue
                # Tên bài: web có thể auto-fill khác (đúng theo PPCT) — chấp
                # nhận nếu cả 2 cùng rỗng/dấu cách HOẶC web có nội dung
                # (web đã debounce hoàn tất). Chỉ cảnh báo nếu web RỖNG mà
                # backup CÓ nội dung — đây là case "mất tiết".
                a_tb = (actual.ten_bai or "").strip()
                e_tb = (expected.ten_bai or "").strip()
                if e_tb and not a_tb:
                    diff_lines.append(
                        f"  • Ô {rk}: web RỖNG tên bài "
                        f"(đáng lẽ \"{e_tb[:40]}\") — có thể web chưa "
                        "auto-fill xong"
                    )
                    mismatch = True
                    continue

            if not mismatch:
                return True, []

            self.q.put((
                "status",
                f"  ⚠ Verify tuần {tuan} lần {attempt}/{max_retry}: "
                f"có {len(diff_lines)} ô lệch, đợi 1.5s rồi retry…",
            ))

        return False, diff_lines

    def _delete_target_weeks_before_restore(self, client: KHDHClient) -> bool:
        """Xóa các tuần target trước khi nhập lại.

        Logic:
          - Áp dụng cho mọi cách khôi phục vì web KHDH tham chiếu PPCT
            cuốn chiếu. Nếu giữ dữ liệu cũ, dữ liệu mới dễ bị trùng/lệch.
          - Xóa từ tuần lớn về tuần nhỏ để hạn chế xung đột cross-week.
          - Nếu user chọn các tuần KHÔNG liên tiếp (vd 31, 33, 35) → vẫn
            xóa từng tuần riêng (web cho phép xóa 1 tuần đơn lẻ).
          - Halt-on-error: 1 tuần xóa fail → DỪNG ngay, user có snapshot.

        Returns:
            True nếu xóa OK hoặc skip vì không có tuần. False nếu fail.
        """
        if not self.target_weeks:
            return True
        # Sort giảm dần (lớn → nhỏ) để xóa an toàn cross-week
        weeks_sorted_desc = sorted(self.target_weeks, reverse=True)
        self.q.put((
            "status",
            f"🗑 Xóa {len(weeks_sorted_desc)} tuần "
            f"({weeks_sorted_desc[0]}→{weeks_sorted_desc[-1]}) "
            "trước khi nhập lại để tránh trùng dữ liệu cuốn chiếu…",
        ))

        deleted = []
        for tuan in weeks_sorted_desc:
            if self.stop_event.is_set():
                self.q.put(("status", "Đã dừng theo yêu cầu."))
                return False
            try:
                res = client.delete_week(tuan)
            except Exception as e:
                self.q.put((
                    "error",
                    f"Lỗi xóa tuần {tuan}: {e}\n\n"
                    f"Đã xóa được: {deleted}.\n"
                    "Hãy mở tab 📸 Snapshots để khôi phục thủ công "
                    "nếu cần.",
                ))
                return False
            success = bool(
                res and res.ok and res.data
                and isinstance(res.data, dict) and res.data.get("success")
            )
            if not success:
                msg = ""
                if res and res.data and isinstance(res.data, dict):
                    msg = str(res.data.get("msg") or "")
                if not msg and res:
                    msg = str(res.error or f"status={res.status}")
                self.q.put((
                    "error",
                    f"Web từ chối xóa tuần {tuan}: {msg}\n\n"
                    f"Đã xóa được: {deleted}.\n"
                    "Hãy mở tab 📸 Snapshots để khôi phục thủ công "
                    "nếu cần. Sau đó sửa các vấn đề trên web rồi chạy lại.",
                ))
                return False
            deleted.append(tuan)
            self.q.put((
                "status",
                f"  ✓ Đã xóa tuần {tuan} ({len(deleted)}/{len(weeks_sorted_desc)})",
            ))
        # Đợi 1s sau delete cuối để web settle
        time.sleep(1.0)
        return True

    def _do_auto_snapshot(self, client: KHDHClient) -> bool:
        """Quét nhanh các tuần đang restore + dump snapshot trước restore.

        Returns:
            True nếu snapshot lưu thành công, False nếu fail. Caller phải
            abort nếu False vì mọi mode restore đều xóa tuần trước khi ghi.
        """
        try:
            self.q.put(("status", "📸 Tạo bản sao lưu nhanh tự động trước khi khôi phục…"))
            weeks_data = client.fetch_weeks_parallel(
                self.target_weeks,
                is_edit=1,
                batch_size=6,
                stop_event=self.stop_event,
            )
            if self.stop_event.is_set():
                return False
            ctx = client.fetch_context()
            metadata = BackupMetadata(
                created_at=datetime.now().isoformat(timespec="seconds"),
                tool_version="auto_khbd_pro",
                nam_hoc=int(ctx.nam_hoc or 0),
                cap_hoc=int(ctx.cap_hoc or 0),
                cap_hoc_text=str(ctx.cap_hoc_text or ""),
                giao_vien_id=int(ctx.giao_vien_id or 0),
                giao_vien_name=str(ctx.giao_vien_name or ""),
                ma_truong=str(ctx.site_id or ""),
                tuan_from=min(self.target_weeks),
                tuan_to=max(self.target_weeks),
                note=f"Tự động trước khi khôi phục {len(self.target_weeks)} tuần",
            )
            weeks: list[BackupWeek] = []
            for tuan in sorted(self.target_weeks):
                wd = weeks_data.get(tuan)
                if wd is None:
                    weeks.append(BackupWeek(tuan=tuan, fetched_at=""))
                else:
                    weeks.append(BackupWeek.from_week_data(wd))
            bf = BackupFile(metadata=metadata, weeks=weeks)
            mgr = SnapshotManager(
                self.excel_path,
                gv_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
                nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
            )
            saved = mgr.save_snapshot(bf, reason="before-restore")
            if saved:
                self.q.put((
                    "status",
                    f"📸 Đã lưu bản sao lưu nhanh: {saved.name}",
                ))
                return True
            return False
        except Exception as e:
            logger.warning(f"auto_snapshot failed: {e}")
            self.q.put((
                "status",
                f"⚠ Không tạo được bản sao lưu nhanh ({e}) — đã hủy trước khi xóa.",
            ))
            return False

    def _build_plan_from_backup(
        self, client: KHDHClient,
    ) -> "PlanReport | None":
        """Build PlanReport từ backup + apply conflict mode.

        Trả None nếu không dựng được kế hoạch ghi.
        """
        # Strategy enum reuse — dùng FILL_FROM_SCRATCH semantics nhưng
        # không cần catalog (data đã có sẵn trong backup).
        request = PlanRequest(
            tuan_from=min(self.target_weeks),
            tuan_to=max(self.target_weeks),
            strategy=PlanStrategy.FILL_FROM_SCRATCH,
            mode=PlanMode.AUTO,
        )
        plan = PlanReport(request=request)

        # Sau bước xóa trước, các tuần target được xem là sạch. Các lựa
        # chọn MERGE/SKIP cũ không còn dùng để quyết định bỏ qua, vì bỏ qua
        # sau khi xóa sẽ không phục hồi đủ dữ liệu cho user.
        effective_conflict_mode = (
            RESTORE_MODE_OVERWRITE if self._pre_deleted_weeks
            else self.conflict_mode
        )

        # MERGE/SKIP cần fetch web để compare nếu chưa xóa trước.
        # OVERWRITE bỏ qua step này.
        web_weeks: dict[int, WeekData] = {}
        if effective_conflict_mode in (RESTORE_MODE_MERGE, RESTORE_MODE_SKIP):
            self.q.put(("status", "🔍 Đang quét web để so sánh…"))
            try:
                web_weeks = client.fetch_weeks_parallel(
                    self.target_weeks, is_edit=1, batch_size=6,
                    stop_event=self.stop_event,
                )
            except Exception as e:
                self.q.put((
                    "error",
                    f"Không quét được web để so sánh: {e}\n"
                    "Hãy thử chế độ 'Ghi đè toàn bộ' hoặc reload tab "
                    "VnEdu rồi thử lại.",
                ))
                return None

        for tuan in self.target_weeks:
            if self.stop_event.is_set():
                break
            backup_week = self.backup.week_by_num(tuan)
            wp = WeekPlan(
                tuan=tuan,
                strategy=PlanStrategy.FILL_FROM_SCRATCH,
            )

            if backup_week is None or not backup_week.slots:
                wp.skip_reason = (
                    f"Tuần {tuan} không có data trong file backup."
                )
                plan.week_plans.append(wp)
                continue

            # Conflict mode logic
            if effective_conflict_mode == RESTORE_MODE_SKIP:
                cur_wd = web_weeks.get(tuan)
                if cur_wd and any(s.is_filled for s in cur_wd.slots):
                    wp.skip_reason = (
                        f"Tuần {tuan} trên web đã có data — "
                        "skip theo lựa chọn của user."
                    )
                    plan.week_plans.append(wp)
                    continue

            # Build ops từ backup
            ops: list[FillOp] = []
            if effective_conflict_mode == RESTORE_MODE_MERGE:
                cur_wd = web_weeks.get(tuan)
                cur_slot_by_rk: dict[str, SlotData] = {}
                if cur_wd:
                    cur_slot_by_rk = {s.row_key: s for s in cur_wd.slots}
                for bs in backup_week.slots:
                    web_s = cur_slot_by_rk.get(bs.row_key)
                    if web_s and self._slot_matches_backup(web_s, bs):
                        continue  # đã giống → skip
                    ops.append(_backup_slot_to_fill_op(bs, tuan))
            else:
                # OVERWRITE: tất cả slot trong backup đều phải write.
                # CRITICAL: filter slot có lop nhưng ppct ≤ 0 — web reject
                # save vì slot có lop bắt buộc phải có ppct. Bỏ qua chúng
                # cũng không sao vì sau delete slot đó vẫn rỗng.
                for bs in backup_week.slots:
                    try:
                        ppct_int = int(bs.ppct) if bs.ppct else 0
                    except (TypeError, ValueError):
                        ppct_int = 0
                    if ppct_int <= 0:
                        # Skip slot không có ppct hợp lệ
                        continue
                    ops.append(_backup_slot_to_fill_op(bs, tuan))

            wp.fill_ops = ops
            wp.estimated_changes = len([o for o in ops if not o.skip])
            if not ops:
                wp.skip_reason = (
                    f"Tuần {tuan}: tất cả ô đã khớp với web (merge mode)."
                )
            plan.week_plans.append(wp)

        return plan

    @staticmethod
    def _slot_matches_backup(web: SlotData, bs: BackupSlot) -> bool:
        """So sánh 1 slot web vs backup. True = đã giống, không cần restore.

        Compare: lop_id, mon_id, phan_mon_id, ppct, ten_bai (case-insensitive
        strip), trang_thai. KHÔNG so sánh ghi_chu (thường để trống).
        """
        try:
            web_ppct = int(web.ppct) if web.ppct else 0
        except (TypeError, ValueError):
            web_ppct = 0
        try:
            bs_ppct = int(bs.ppct) if bs.ppct else 0
        except (TypeError, ValueError):
            bs_ppct = 0
        return (
            str(web.lop_id) == str(bs.lop_id)
            and str(web.mon_id) == str(bs.mon_id)
            and str(web.phan_mon_id) == str(bs.phan_mon_id)
            and web_ppct == bs_ppct
            and (web.ten_bai or "").strip().lower()
                == (bs.ten_bai or "").strip().lower()
            and str(web.trang_thai or "0") == str(bs.trang_thai or "0")
        )

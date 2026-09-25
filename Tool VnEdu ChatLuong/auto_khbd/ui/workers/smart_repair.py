"""Worker Smart Repair (sửa PPCT lệch)."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.sync_api import sync_playwright

from ...engine.analyzer.models import TT_COUNTED, TT_EXTRA, TT_NGHI
from ...engine.backup.models import BackupFile, BackupMetadata, BackupWeek
from ...engine.backup.smart_repair import (
    smart_repair_build_groups,
    SmartRepairAction,
    SmartRepairReport,
    SR_PATTERN_AMBIGUOUS,
    SR_PATTERN_EVEN_ONLY,
    SR_PATTERN_ODD_ONLY,
)
from ...engine.backup.snapshots import SnapshotManager
from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from ...engine.executor.executor import PlanExecutor
from ...engine.executor.js import _JS_FETCH_TEN_BAI
from ...engine.parser import SlotData
from ...engine.planner import FillOp, PlanMode, PlanReport, PlanRequest, PlanStrategy, WeekPlan
from ...log import logger
from .bootstrap import BootstrapWorker

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.parser import WeekData


# =====================================================================
# Worker — SmartRepairWorker (phân tích + sửa PPCT lệch sau anchor)
# =====================================================================

class SmartRepairWorker(threading.Thread):
    """Phân tích PPCT lệch + sinh action sửa.

    2 phase:
      Phase 1 (analyze): fetch tất cả tuần từ tuan_from→tuan_to,
        build groups + detect pattern + compute proposed_ppct cho mỗi
        slot ở các tuần `> anchor_tuan`. Emit `("done_analyze", report)`.
      Phase 2 (apply): nhận lại danh sách action user đã filter, fetch
        tên bài cho proposed_ppct, gọi PlanExecutor restore.

    Worker chạy 1 trong 2 phase tùy `mode`:
      - mode="analyze": chỉ chạy Phase 1.
      - mode="apply": cần `actions_to_apply` đã set từ ngoài.

    Events qua queue:
        ("status", text)
        ("progress", done, total, label)
        ("done_analyze", SmartRepairReport)
        ("event", ExecutorEvent)        # forward từ apply phase
        ("done_apply", ExecutorReport)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        mode: str,                     # "analyze" | "apply"
        anchor_tuan: int,
        tuan_from: int,
        tuan_to: int,
        excel_path: str | Path | None,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        # Chỉ dùng khi mode="apply"
        actions_to_apply: list[SmartRepairAction] | None = None,
        auto_snapshot: bool = True,
    ):
        super().__init__(daemon=True, name=f"KHDH-SmartRepair-{mode}")
        self.port = port
        self.mode = mode
        self.anchor_tuan = int(anchor_tuan)
        self.tuan_from = int(tuan_from)
        self.tuan_to = int(tuan_to)
        self.excel_path = Path(excel_path) if excel_path else None
        self.q = event_queue
        self.stop_event = stop_event
        self.actions_to_apply = list(actions_to_apply or [])
        self.auto_snapshot = bool(auto_snapshot)

    def run(self):
        try:
            if self.mode == "analyze":
                self._run_analyze()
            elif self.mode == "apply":
                self._run_apply()
            else:
                self.q.put(("error", f"Unknown mode: {self.mode}"))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi sửa thông minh: {e}\n\n{tb}"))

    # ----------------------------------------------------------------
    # Phase 1: Analyze
    # ----------------------------------------------------------------

    def _run_analyze(self):
        if not (1 <= self.tuan_from <= self.anchor_tuan <= self.tuan_to <= 52):
            self.q.put((
                "error",
                f"Khoảng tuần không hợp lệ: from={self.tuan_from}, "
                f"anchor={self.anchor_tuan}, to={self.tuan_to}. "
                "Phải có 1 ≤ from ≤ anchor ≤ to ≤ 52.",
            ))
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
            try:
                client.enable_edit_mode()
                time.sleep(0.4)
            except Exception:
                pass

            tuans = list(range(self.tuan_from, self.tuan_to + 1))

            def _on_batch(done, total, batch):
                label = (
                    f"tuần {batch[0]}–{batch[-1]}"
                    if len(batch) > 1
                    else f"tuần {batch[0]}"
                )
                self.q.put(("progress", done, total, label))

            self.q.put((
                "status",
                f"Đang quét tuần {self.tuan_from}–{self.tuan_to} "
                f"(anchor=T{self.anchor_tuan})…",
            ))
            try:
                weeks_data = client.fetch_weeks_parallel(
                    tuans, is_edit=1, batch_size=6,
                    on_batch=_on_batch, stop_event=self.stop_event,
                )
            except Exception as e:
                self.q.put(("error", f"Lỗi fetch: {e}"))
                return
            if self.stop_event.is_set():
                self.q.put(("status", "Đã dừng theo yêu cầu"))
                return

            # Build groups + detect pattern. Chỉ học chu kỳ từ vùng tin cậy
            # <= anchor; các tuần sau anchor có thể đã bị PPCT lệch.
            groups = smart_repair_build_groups(
                weeks_data, pattern_tuan_to=self.anchor_tuan,
            )
            report = SmartRepairReport(
                anchor_tuan=self.anchor_tuan,
                tuan_from=self.tuan_from,
                tuan_to=self.tuan_to,
                groups=groups,
            )

            # Validate anchor: mỗi group phải có PPCT tại anchor_tuan,
            # nếu không thì không thể propose cho tuần > anchor.
            for key, g in groups.items():
                if self.anchor_tuan not in g.ppct_by_week:
                    if g.is_active_in_week(self.anchor_tuan):
                        report.warnings.append(
                            f"Group {g.lop_text}/{g.phan_mon_text}: "
                            f"không có data ở tuần anchor {self.anchor_tuan} "
                            "→ không thể đề xuất sửa."
                        )

            # Sinh actions cho các tuần > anchor
            for tuan in range(self.anchor_tuan + 1, self.tuan_to + 1):
                wd = weeks_data.get(tuan)
                if wd is None:
                    continue
                for slot in wd.filled_slots:
                    if not slot.lop_id or slot.lop_id == "0":
                        continue
                    if not slot.phan_mon_id or slot.phan_mon_id == "0":
                        continue
                    try:
                        cur_ppct = int(slot.ppct)
                    except (TypeError, ValueError):
                        continue
                    if cur_ppct <= 0:
                        continue
                    tt = (slot.trang_thai or "").strip()
                    # Skip slot extra (Dạy bù/Chèn lịch) — không cuốn chiếu
                    if tt in TT_EXTRA:
                        continue
                    # Slot Nghỉ vẫn process (PPCT đặt chỗ, không advance counter).
                    # Slot non-counted-non-Nghỉ (Phụ đạo/Bồi dưỡng/Dạy thêm) skip.
                    is_nghi = (tt == TT_NGHI)
                    is_counted = (not tt) or (tt in TT_COUNTED)
                    if not is_nghi and not is_counted:
                        continue
                    key = f"{slot.lop_id}|{slot.mon_id}|{slot.phan_mon_id}"
                    g = groups.get(key)
                    if g is None:
                        continue
                    if g.pattern == SR_PATTERN_AMBIGUOUS:
                        continue
                    proposed = g.proposed_ppct(tuan, self.anchor_tuan)
                    if proposed is None or proposed <= 0:
                        continue
                    notes_parts = []
                    if g.pattern == SR_PATTERN_ODD_ONLY:
                        notes_parts.append("chỉ tuần lẻ")
                    elif g.pattern == SR_PATTERN_EVEN_ONLY:
                        notes_parts.append("chỉ tuần chẵn")
                    if is_nghi:
                        notes_parts.append("Nghỉ — đặt chỗ PPCT")
                    notes = " · ".join(notes_parts) if notes_parts else ""
                    action = SmartRepairAction(
                        tuan=tuan,
                        row_key=slot.row_key,
                        group_key=key,
                        lop_id=slot.lop_id,
                        lop_text=slot.lop_text,
                        mon_id=slot.mon_id,
                        mon_text=slot.mon_text,
                        phan_mon_id=slot.phan_mon_id,
                        phan_mon_text=slot.phan_mon_text,
                        current_ppct=cur_ppct,
                        proposed_ppct=int(proposed),
                        current_ten_bai=slot.ten_bai or "",
                        # PHẢI giữ trang_thai gốc — Nghỉ → vẫn re-insert là Nghỉ.
                        current_trang_thai=tt or "0",
                        notes=notes,
                        # Default skip nếu PPCT đã đúng
                        skip=(int(proposed) == cur_ppct),
                    )
                    report.actions.append(action)

            report.actions.sort(key=lambda a: (a.tuan, a.row_key))
            self.q.put(("done_analyze", report))

    # ----------------------------------------------------------------
    # Phase 2: Apply (gọi sau khi user duyệt actions)
    # ----------------------------------------------------------------

    def _run_apply(self):
        actions = [a for a in self.actions_to_apply if not a.skip]
        if not actions:
            self.q.put(("error", "Không có ô nào để áp dụng."))
            return

        # Tính range delete: [anchor+1, max_tuan_action]
        # Logic: web KHDH lưu cross-week PPCT, không sửa được 1 ô đơn lẻ
        # → phải xóa toàn dải tuần [anchor+1..max_tuan_action] rồi nhập lại
        # toàn bộ. Xóa từ tuần lớn về nhỏ (an toàn cross-week).
        max_action_tuan = max(a.tuan for a in actions)
        delete_range = list(range(self.anchor_tuan + 1, max_action_tuan + 1))
        if not delete_range:
            self.q.put((
                "error",
                "Khoảng tuần cần sửa rỗng. Vui lòng kiểm tra lại tuần mốc.",
            ))
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
                ctx = client.fetch_context()
            except Exception as e:
                self.q.put(("error", format_context_read_error(e)))
                return

            # 1. Fetch TẤT CẢ tuần trong delete_range — cần để biết slot
            # hiện tại trên web (kể cả slot không cần sửa). Sau khi DELETE,
            # mọi slot đều mất → phải re-insert toàn bộ.
            self.q.put((
                "status",
                f"📥 Đang đọc dữ liệu hiện tại của {len(delete_range)} tuần…",
            ))
            try:
                pre_data = client.fetch_weeks_parallel(
                    delete_range, is_edit=1, batch_size=6,
                    stop_event=self.stop_event,
                )
            except Exception as e:
                self.q.put((
                    "error",
                    f"Không đọc được dữ liệu hiện tại: {e}",
                ))
                return
            if self.stop_event.is_set():
                return

            # CRITICAL: validate pre_data ĐẦY ĐỦ trước khi xóa.
            # Nếu fetch thiếu tuần → ABORT, KHÔNG xóa. Lý do: nếu xóa rồi
            # mà không có data tuần đó để re-insert → mất data vĩnh viễn.
            missing = [t for t in delete_range if t not in pre_data]
            if missing:
                self.q.put((
                    "error",
                    f"Đọc được {len(pre_data)}/{len(delete_range)} tuần — "
                    f"thiếu: {missing}.\n\n"
                    "TUYỆT ĐỐI KHÔNG xóa khi chưa đọc đủ — sẽ mất dữ liệu.\n"
                    "Hãy reload tab VnEdu rồi thử lại.",
                ))
                return

            # 2. Auto-snapshot từ data đã fetch (không cần fetch lần 2).
            # CRITICAL: snapshot fail → ABORT (không cho delete). Lý do: nếu
            # delete xong nhưng re-insert fail giữa chừng mà không có snapshot
            # để khôi phục → mất dữ liệu vĩnh viễn.
            if self.auto_snapshot:
                if self.excel_path is None:
                    self.q.put((
                        "status",
                        "ℹ Hồ sơ chưa lưu — bản sao lưu nhanh sẽ ghi vào "
                        "thư mục global (%APPDATA%/KHDH_Pro/snapshots-global/).",
                    ))
                snap_ok = self._do_snapshot_from_data(ctx, pre_data, delete_range)
                if not snap_ok:
                    self.q.put((
                        "error",
                        "❌ Không tạo được bản sao lưu nhanh — TUYỆT ĐỐI "
                        "không xóa khi chưa có safety net.\n\n"
                        "Hãy kiểm tra:\n"
                        "  • Hồ sơ Excel đã được lưu chưa, hoặc Chrome đã đọc "
                        "được giáo viên để lưu snapshot global chưa\n"
                        "  • Ổ đĩa còn dung lượng không\n"
                        "  • Quyền ghi vào thư mục hồ sơ / %APPDATA%",
                    ))
                    return
            elif not self.auto_snapshot:
                # User đã tắt auto-snapshot → cảnh báo nhưng vẫn cho tiếp
                self.q.put((
                    "status",
                    "⚠ Đang chạy KHÔNG có sao lưu nhanh — fail giữa chừng "
                    "sẽ không khôi phục được.",
                ))

            # 3. DELETE range — từ tuần lớn về nhỏ
            ok = self._delete_range_reverse(client, delete_range)
            if not ok:
                return  # error đã put

            # 4. Build map action: (tuan, row_key) → SmartRepairAction
            action_map: dict[tuple[int, str], SmartRepairAction] = {
                (a.tuan, a.row_key): a for a in actions
            }

            # 5. Fetch tên bài CHO MỖI Ô CẦN RE-INSERT.
            # Slot có action (PPCT mới) → fetch theo PPCT mới.
            # Slot không có action → giữ tên bài cũ từ snapshot.
            # Slot không có ten_bai → bắt buộc bật fallback dấu cách
            # (BẮT BUỘC vì web reject save khi ô có lop+ppct nhưng tb rỗng).
            slots_to_insert = self._collect_slots_to_insert(
                pre_data, action_map,
            )
            if not slots_to_insert:
                self.q.put((
                    "error",
                    "Không có slot nào để nhập lại sau khi xóa.",
                ))
                return

            # Fetch tên bài cho slot có PPCT thay đổi (action)
            self._fetch_ten_bai_for_actions(client, ctx, actions)

            # 6. Build PlanReport với TẤT CẢ slot (cả changed + kept)
            plan = self._build_reinsert_plan(slots_to_insert, action_map)

            # 7. Execute với ten_bai_fallback=True bắt buộc
            executor = PlanExecutor(
                client,
                on_event=lambda ev: self.q.put(("event", ev)),
                stop_event=self.stop_event,
                halt_on_error=True,
                # BẮT BUỘC bật fallback — phân môn không có CSDL tên bài
                # (HĐTN-Chủ-đề, SHL, Chào cờ...) sẽ rỗng → web reject save.
                ten_bai_fallback=True,
                fallback_log=None,
                wait_after_set_fields_s=0.8,
                fast_safe_mode=True,
            )
            self.q.put((
                "status",
                f"🚀 Nhập lại {sum(len(wp.fill_ops) for wp in plan.week_plans)} ô "
                f"trên {len(plan.week_plans)} tuần…",
            ))
            try:
                exec_report = executor.execute(plan, dry_run=False)
            except Exception as e:
                tb = traceback.format_exc()
                self.q.put((
                    "error",
                    f"❌ Lỗi nhập lại: {e}\n\n"
                    "⚠ QUAN TRỌNG: Đã xóa các tuần nhưng nhập lại bị lỗi.\n"
                    "Để KHÔI PHỤC dữ liệu đã mất:\n"
                    "  1. Đóng dialog này\n"
                    "  2. Mở lại 📦 Sao lưu / rà soát → tab 📸 Bản lưu nhanh\n"
                    "  3. Nháy đúp vào bản sao lưu vừa tạo "
                    "(snapshot-...-before-smart-repair...)\n"
                    "  4. Trong tab 📥 Khôi phục, đánh dấu tất cả tuần\n"
                    "  5. Chọn chế độ '🔁 Ghi đè toàn bộ' → bấm khôi phục\n\n"
                    f"Chi tiết lỗi:\n{tb}",
                ))
                return
            self.q.put(("done_apply", exec_report))

    def _do_snapshot_from_data(
        self, ctx, weeks_data: dict[int, "WeekData"],
        delete_range: list[int],
    ) -> bool:
        """Build + save BackupFile từ data đã fetch.

        Returns:
            True nếu lưu thành công, False nếu fail (caller phải abort
            trước khi delete để tránh mất dữ liệu vĩnh viễn).
        """
        try:
            metadata = BackupMetadata(
                created_at=datetime.now().isoformat(timespec="seconds"),
                tool_version="auto_khbd_pro",
                nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
                cap_hoc=int(getattr(ctx, "cap_hoc", 0) or 0),
                cap_hoc_text=str(getattr(ctx, "cap_hoc_text", "") or ""),
                giao_vien_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
                giao_vien_name=str(getattr(ctx, "giao_vien_name", "") or ""),
                ma_truong=str(getattr(ctx, "site_id", "") or ""),
                tuan_from=min(delete_range),
                tuan_to=max(delete_range),
                note=(
                    f"Tự động trước Sửa thông minh "
                    f"T{min(delete_range)}–T{max(delete_range)}"
                ),
            )
            weeks: list[BackupWeek] = []
            for t in delete_range:
                wd = weeks_data.get(t)
                weeks.append(
                    BackupWeek(tuan=t, fetched_at="")
                    if wd is None else BackupWeek.from_week_data(wd)
                )
            bf = BackupFile(metadata=metadata, weeks=weeks)
            mgr = SnapshotManager(
                self.excel_path,
                gv_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
                nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
            )
            saved = mgr.save_snapshot(bf, reason="before-smart-repair")
            if saved:
                self.q.put((
                    "status",
                    f"📸 Đã lưu bản sao lưu nhanh: {saved.name}",
                ))
                return True
            self.q.put((
                "status",
                "❌ Không tạo được bản sao lưu nhanh: không xác định được "
                "thư mục lưu snapshot.",
            ))
            return False
        except Exception as e:
            logger.warning(f"smart-repair snapshot failed: {e}")
            self.q.put((
                "status",
                f"❌ Không tạo được bản sao lưu nhanh: {e}",
            ))
            return False

    def _delete_range_reverse(
        self, client: KHDHClient, delete_range: list[int],
    ) -> bool:
        """Xóa các tuần trong range, từ tuần lớn về tuần nhỏ.

        Halt-on-error: 1 tuần fail → DỪNG ngay, user có snapshot vừa tạo
        để khôi phục thủ công.
        """
        if not delete_range:
            return True
        weeks_desc = sorted(delete_range, reverse=True)
        self.q.put((
            "status",
            f"🗑 Xóa {len(weeks_desc)} tuần "
            f"(T{weeks_desc[0]}→T{weeks_desc[-1]}) trước khi nhập lại…",
        ))
        deleted = []
        for tuan in weeks_desc:
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
                    "Hãy mở tab 📸 Snapshots để khôi phục thủ công.",
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
                    "Hãy mở tab 📸 Snapshots để khôi phục.",
                ))
                return False
            deleted.append(tuan)
            self.q.put((
                "status",
                f"  ✓ Đã xóa tuần {tuan} ({len(deleted)}/{len(weeks_desc)})",
            ))
        time.sleep(1.0)
        return True

    def _collect_slots_to_insert(
        self,
        pre_data: dict[int, "WeekData"],
        action_map: dict[tuple[int, str], "SmartRepairAction"],
    ) -> list[tuple[int, "SlotData", "SmartRepairAction | None"]]:
        """Build danh sách slot cần re-insert sau khi delete range.

        Mỗi slot có lop trên web tại thời điểm pre_data → đều phải nhập lại.
        Slot có action → dùng PPCT mới + tên bài fetch theo PPCT mới.
        Slot không có action → giữ nguyên PPCT + tên bài cũ.

        Slot không có lop / ppct rỗng → BỎ QUA (không cần nhập lại).

        Returns: list[(tuan, slot_data_cũ, action_nếu_có)]
        """
        out: list[tuple[int, SlotData, SmartRepairAction | None]] = []
        for tuan in sorted(pre_data.keys()):
            wd = pre_data[tuan]
            for slot in wd.slots:
                if not slot.has_lop:
                    continue
                # Slot có lop nhưng không có ppct → tool cũ thường không
                # save (vì web reject). Bỏ qua nhưng cảnh báo.
                try:
                    cur_ppct = int(slot.ppct) if slot.ppct else 0
                except (TypeError, ValueError):
                    cur_ppct = 0
                if cur_ppct <= 0:
                    self.q.put((
                        "status",
                        f"⚠ Bỏ qua tuần {tuan} ô {slot.row_key}: "
                        "có lớp nhưng PPCT rỗng (web không lưu được).",
                    ))
                    continue
                action = action_map.get((tuan, slot.row_key))
                out.append((tuan, slot, action))
        return out

    def _fetch_ten_bai_for_actions(
        self, client: KHDHClient, ctx,
        actions: list["SmartRepairAction"],
    ) -> None:
        """Fetch tên bài cho mỗi action có PPCT mới (qua API getByTiet)."""
        if not actions:
            return
        self.q.put((
            "status",
            f"Đang lấy {len(actions)} tên bài mới qua API…",
        ))
        for i, a in enumerate(actions):
            if self.stop_event.is_set():
                return
            if i % 5 == 0:
                self.q.put((
                    "progress", i, len(actions),
                    f"đang lấy tên bài {i+1}/{len(actions)}",
                ))
            try:
                res = client.page.evaluate(
                    _JS_FETCH_TEN_BAI,
                    {
                        "rk": a.row_key,
                        "khoi": "",
                        "lop_id": a.lop_id,
                        "mon_id": a.mon_id,
                        "pm_id": a.phan_mon_id,
                        "ppct": a.proposed_ppct,
                        "token": ctx.my_token,
                        "user_id": ctx.my_user_id,
                        "nam_hoc": str(ctx.nam_hoc),
                    },
                )
                if res and res.get("ok"):
                    tb = (res.get("ten_bai") or "").strip()
                    # CRITICAL: nếu API trả rỗng (HĐTN/SHL/Chào cờ không có CSDL),
                    # PHẢI để rỗng — để PlanExecutor fallback chèn " " hợp lệ.
                    # KHÔNG fallback về current_ten_bai vì PPCT đã đổi → tên cũ
                    # đang ứng với PPCT cũ → ghi tên cũ với PPCT mới = SAI.
                    a.proposed_ten_bai = tb
                else:
                    # API fail (network, etc.) → để rỗng, fallback dấu cách
                    a.proposed_ten_bai = ""
            except Exception:
                a.proposed_ten_bai = ""

    def _build_reinsert_plan(
        self,
        slots_to_insert: list[tuple[int, "SlotData", "SmartRepairAction | None"]],
        action_map: dict[tuple[int, str], "SmartRepairAction"],
    ) -> "PlanReport":
        """Build PlanReport gồm TẤT CẢ slot trong range cần re-insert."""
        from collections import defaultdict
        by_week: dict[int, list[FillOp]] = defaultdict(list)
        for tuan, slot, action in slots_to_insert:
            if action is not None and action.needs_change:
                # Slot có PPCT mới → dùng action data
                ppct_new = action.proposed_ppct
                ten_bai_new = action.proposed_ten_bai or ""
                trang_thai_new = action.current_trang_thai or "0"
            else:
                # Slot giữ nguyên
                try:
                    ppct_new = int(slot.ppct) if slot.ppct else 0
                except (TypeError, ValueError):
                    ppct_new = 0
                ten_bai_new = slot.ten_bai or ""
                trang_thai_new = slot.trang_thai or "0"
            op = FillOp(
                tuan=tuan,
                row_key=slot.row_key,
                lop_id=slot.lop_id, lop_text=slot.lop_text,
                mon_id=slot.mon_id, mon_text=slot.mon_text,
                phan_mon_id=slot.phan_mon_id,
                phan_mon_text=slot.phan_mon_text,
                ppct=ppct_new,
                ten_bai=ten_bai_new,
                source=("smart_repair_change" if action and action.needs_change
                        else "smart_repair_keep"),
                notes=(
                    "PPCT cuốn chiếu" if action and action.needs_change
                    else "Giữ nguyên sau xóa"
                ),
            )
            op.ghi_chu = slot.ghi_chu or ""
            op.trang_thai = trang_thai_new
            by_week[tuan].append(op)

        plan = PlanReport(request=PlanRequest(
            tuan_from=min(by_week.keys()),
            tuan_to=max(by_week.keys()),
            strategy=PlanStrategy.FILL_FROM_SCRATCH,
            mode=PlanMode.AUTO,
        ))
        for tuan in sorted(by_week.keys()):
            wp = WeekPlan(tuan=tuan, strategy=PlanStrategy.FILL_FROM_SCRATCH)
            wp.fill_ops = by_week[tuan]
            wp.estimated_changes = len(wp.fill_ops)
            plan.week_plans.append(wp)
        return plan

    def _do_snapshot(self, client, ctx, actions):
        try:
            self.q.put(("status", "📸 Tạo bản sao lưu nhanh trước khi sửa…"))
            tuans = sorted({a.tuan for a in actions})
            weeks_data = client.fetch_weeks_parallel(
                tuans, is_edit=1, batch_size=6,
                stop_event=self.stop_event,
            )
            metadata = BackupMetadata(
                created_at=datetime.now().isoformat(timespec="seconds"),
                tool_version="auto_khbd_pro",
                nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
                cap_hoc=int(getattr(ctx, "cap_hoc", 0) or 0),
                cap_hoc_text=str(getattr(ctx, "cap_hoc_text", "") or ""),
                giao_vien_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
                giao_vien_name=str(getattr(ctx, "giao_vien_name", "") or ""),
                ma_truong=str(getattr(ctx, "site_id", "") or ""),
                tuan_from=min(tuans), tuan_to=max(tuans),
                note=f"Tự động trước khi sửa thông minh {len(actions)} ô",
            )
            weeks: list[BackupWeek] = []
            for t in tuans:
                wd = weeks_data.get(t)
                weeks.append(
                    BackupWeek(tuan=t, fetched_at="")
                    if wd is None else BackupWeek.from_week_data(wd)
                )
            bf = BackupFile(metadata=metadata, weeks=weeks)
            mgr = SnapshotManager(
                self.excel_path,
                gv_id=int(getattr(ctx, "giao_vien_id", 0) or 0),
                nam_hoc=int(getattr(ctx, "nam_hoc", 0) or 0),
            )
            saved = mgr.save_snapshot(bf, reason="before-smart-repair")
            if saved:
                self.q.put(("status", f"📸 Snapshot: {saved.name}"))
        except Exception as e:
            logger.warning(f"smart-repair snapshot failed: {e}")

"""Chuyển tuần có xác minh và quét chênh lệch."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ...log import logger
from ..analyzer.models import TT_EXTRA
from ..analyzer.pattern_analyzer import PatternAnalyzer
from .js import _JS_GET_CURRENT_WEEK

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..parser import SlotData
    from ..planner import WeekPlan


class WeekNavigationMixin:
    """Chuyển tuần có xác minh và quét chênh lệch."""

    # -----------------------------------------------------------
    # Week-switch with verification
    # -----------------------------------------------------------

    def _read_current_week(self) -> int:
        """Đọc tuần đang chọn trên combobox UI. 0 nếu không xác định."""
        try:
            r = self.client.page.evaluate(_JS_GET_CURRENT_WEEK)
            if r and r.get("ok"):
                return int(r.get("tuan") or 0)
        except Exception as e:
            logger.warning(f"read_current_week: {e}")
        return 0

    def _apply_inflight_ppct_shift(
        self, current_tuan: int,
        extras_by_group: dict[str, int],
    ) -> int:
        """v2.2: Shift PPCT cho các tuần SAU current_tuan khi phát hiện
        tiết bù trong tuần đang chạy.

        Logic đúng theo intent user:
          - Tôn trọng tuyệt đối ppct_start mà user setup tay (không động tuần
            <= current_tuan).
          - Nhưng nếu trong tuần đang chạy phát hiện thêm N tiết bù ở group X
            → các op của group X ở tuần T+1, T+2, ... cần shift +N PPCT để
            tránh collision.

        Args:
            current_tuan: tuần đang được fill — KHÔNG động tới tuần này và
                các tuần trước nó.
            extras_by_group: dict[group_key, count] — số tiết extra phát hiện
                được trong current_tuan, group theo (lop, mon, pm).

        Returns: số ops đã shift PPCT.
        """
        plan = getattr(self, "_current_plan", None)
        if plan is None or not plan.week_plans or not extras_by_group:
            return 0

        shifted = 0
        for wp in plan.week_plans:
            if wp.tuan <= current_tuan:
                continue  # KHÔNG động tuần đã/đang chạy
            for op in wp.fill_ops:
                if op.skip:
                    continue
                gk = f"{op.lop_id}|{op.mon_id}|{op.phan_mon_id}"
                delta = extras_by_group.get(gk, 0)
                if delta > 0:
                    op.ppct += delta
                    shifted += 1
        return shifted

    def _scan_and_diff_week(
        self, wp: "WeekPlan"
    ) -> tuple[bool, list, list, dict]:
        """v2: Scan web tuần hiện tại + diff với plan.

        Gọi NGAY SAU khi đã switch + verify week + reload table ở edit mode.

        Returns:
            (skip_full, ops_to_keep, extras_detected, scan_progress)

            skip_full: bool — True nếu mọi op trong plan đã có sẵn trên web
                khớp đúng → có thể skip toàn tuần.
            ops_to_keep: list[FillOp] — chỉ giữ ops cần fill (ô thiếu hoặc
                khác với web). Ops đã match web sẽ được loại.
            extras_detected: list[dict] — slot trên web có trạng thái dạy bù.
            scan_progress: dict[group_key, last_ppct] — tổng PPCT thực tế.

        Logic match: 1 op coi như "đã có" nếu web có slot cùng row_key với:
          lop_id, mon_id, phan_mon_id khớp + ppct khớp + ten_bai khớp.

        Errors khi fetch_week swallow → return (False, wp.fill_ops, [], {}).
        """
        if not wp.fill_ops:
            return False, [], [], {}
        try:
            cur_wd = self.client.fetch_week(wp.tuan)
        except Exception as e:
            self._emit("warning", tuan=wp.tuan,
                      message=f"scan_and_diff: fetch_week failed ({e}), bỏ qua scan")
            return False, list(wp.fill_ops), [], {}

        # Build map row_key → SlotData từ web
        web_slots: dict[str, "SlotData"] = {s.row_key: s for s in cur_wd.slots}

        # Detect extras
        extras_detected: list[dict] = []
        for slot in cur_wd.filled_slots:
            tt = (slot.trang_thai or "").strip()
            if tt in TT_EXTRA:
                try:
                    ppct_int = int(slot.ppct)
                except (ValueError, TypeError):
                    ppct_int = 0
                extras_detected.append({
                    "tuan": wp.tuan,
                    "thu": slot.thu,
                    "buoi": slot.buoi_idx,
                    "tiet": slot.tiet_idx,
                    "lop_id": slot.lop_id,
                    "mon_id": slot.mon_id,
                    "phan_mon_id": slot.phan_mon_id,
                    "ppct": ppct_int,
                    "trang_thai": tt,
                    "ten_bai": slot.ten_bai,
                    "source": "trang_thai",
                })

        # Diff plan vs web
        ops_to_keep = []
        matched_count = 0

        def _norm(s: str) -> str:
            return (s or "").strip().lower()

        for op in wp.fill_ops:
            if op.skip:
                continue
            web_slot = web_slots.get(op.row_key)
            if web_slot is None or not web_slot.is_filled:
                ops_to_keep.append(op)
                continue
            try:
                web_ppct = int(web_slot.ppct)
            except (ValueError, TypeError):
                web_ppct = 0
            same = (
                str(web_slot.lop_id) == str(op.lop_id)
                and str(web_slot.mon_id) == str(op.mon_id)
                and str(web_slot.phan_mon_id) == str(op.phan_mon_id)
                and web_ppct == int(op.ppct)
                and _norm(web_slot.ten_bai) == _norm(op.ten_bai)
            )
            if same:
                matched_count += 1
            else:
                ops_to_keep.append(op)

        active_total = sum(1 for op in wp.fill_ops if not op.skip)
        skip_full = (matched_count == active_total and active_total > 0)

        # Compute scan_progress for this week (cho UI hiển thị PPCT next)
        wd_for_progress = {wp.tuan: cur_wd}
        # Merge với weeks đã fetch trước đó nếu planner cache có
        scan_progress_full = PatternAnalyzer.compute_progress(wd_for_progress)
        scan_progress: dict[str, int] = {
            k: sp.last_ppct for k, sp in scan_progress_full.items()
            if sp.last_ppct > 0
        }

        return skip_full, ops_to_keep, extras_detected, scan_progress

    def _switch_and_verify_week(self, target_tuan: int,
                                 max_attempts: int = 3,
                                 timeout_per_attempt_s: float = 4.0) -> int:
        """Chuyển sang tuần `target_tuan` + xác minh combobox đã đổi.

        Lý do: select_week_in_ui chỉ trigger event ExtJS, đôi lúc reload bảng
        bị nuốt (race với XHR khác) hoặc combobox không update. Phải verify
        bằng cách đọc lại value của cboTuanHoc.

        Returns:
            tuần thực sự đang hiển thị (= target_tuan nếu thành công, khác
            nếu thất bại).
        """
        # Nếu đã đúng tuần rồi, vẫn reload để có DOM mới nhất
        current = self._read_current_week()
        if current == target_tuan:
            try:
                self.client.reload_table_and_wait(
                    floor_s=1.5, max_extra_s=6.0, stop_event=self.stop_event,
                )
            except Exception:
                pass
            # Explicit check: nếu _read_current_week() trả 0 (page error,
            # ExtJS crash), KHÔNG fallback về `current` cũ — vì sau reload
            # combobox có thể đã thay đổi mà mình không biết. Trả về 0 để
            # caller phát hiện và xử lý (X3 fix).
            v = self._read_current_week()
            return v if v != 0 else current

        # Cần switch
        for attempt in range(1, max_attempts + 1):
            try:
                self.client.select_week_in_ui(target_tuan)
            except Exception as e:
                self._emit("warning", tuan=target_tuan,
                          message=f"select_week (lần {attempt}): {e}")
            # Poll combobox value
            deadline = time.monotonic() + timeout_per_attempt_s
            while time.monotonic() < deadline:
                if self.stop_event.is_set():
                    return self._read_current_week()
                time.sleep(0.3)
                cur = self._read_current_week()
                if cur == target_tuan:
                    # Đã đúng tuần → đợi DOM stabilize thêm chút
                    time.sleep(0.8)
                    return cur
            # Attempt này fail, thử lại
            self._emit("warning", tuan=target_tuan,
                      message=f"Chuyển tuần lần {attempt} chưa thành công, thử lại…")
        return self._read_current_week()

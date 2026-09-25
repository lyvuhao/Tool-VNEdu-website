"""Lập kế hoạch điền tiết theo tuần (WeekPlanner)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from ..log import logger
from .analyzer.models import (
    TKB_EVENT_DAY_BU,
    TKB_EVENT_NGHI,
    TT_BINH_THUONG,
    TT_COUNTED,
    TT_DAY_BU,
    TT_NGHI,
    YearTKBPattern,
)
from .analyzer.pattern_analyzer import PatternAnalyzer
from .analyzer.reports import YearScanReport
from .catalog import LessonCatalog
from .parser import WeekData
from .profile.models import ProfileSchemaError, SlotEntry, TKBScheduleEvent
from .profile.profile import profile_schedule_events

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from .analyzer.reports import SubjectProgress


# ######################################################################
# Section: planner
# ######################################################################





# ---------------------------------------------------------------
# Strategy enums
# ---------------------------------------------------------------

class PlanStrategy(Enum):
    GEN_FROM_PREV_WEEK = "gen_prev"     # Gọi API genLichBaoGiangTuan
    GEN_FROM_TKB = "gen_tkb"            # Gọi API genLichBaoGiangTuanTheoTKB
    FILL_FROM_SCRATCH = "fill_scratch"  # Tự fill từng slot
    UPDATE_EMPTY_ONLY = "update_empty"  # Chỉ điền slot trống
    EVEN_ODD_TEMPLATES = "even_odd_tpl" # Dùng KHDHProfile (1 hoặc 2 mẫu lẻ/chẵn)


class PlanMode(Enum):
    AUTO = "auto"                       # Chạy thẳng, không hỏi
    PREVIEW_APPLY = "preview"           # Generate ops, user xem rồi apply


# ---------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------

@dataclass
class FillOp:
    """1 operation: điền data cho 1 slot trên 1 tuần."""
    tuan: int
    row_key: str          # "thu_buoi_tiet"
    lop_id: str
    lop_text: str = ""
    mon_id: str = ""
    mon_text: str = ""
    phan_mon_id: str = ""
    phan_mon_text: str = ""
    ppct: int = 0
    ten_bai: str = ""

    # Optional metadata
    source: str = ""      # "catalog" | "prev_week" | "tkb" | "manual" | "backup"
    notes: str = ""       # ghi chú cho user xem
    skip: bool = False    # user uncheck → skip op này

    # v3 (backup/restore): các field mở rộng để giữ trạng thái non-BT.
    # Default rỗng → executor giữ nguyên hành vi cũ (Bình thường + ghi_chu trống).
    ghi_chu: str = ""     # txtGhiChu_<rk>
    trang_thai: str = "0" # cboTrangThai_<rk> (default = "0" = Bình thường)

    def field_dict(self) -> dict[str, str]:
        """Trả ra dict các field DOM cần set."""
        return {
            f"cboLopHoc_{self.row_key}": self.lop_id,
            f"cboMonHoc_{self.row_key}": self.mon_id,
            f"cboPhanMon_{self.row_key}": self.phan_mon_id,
            f"txtTietPPCT_{self.row_key}": str(self.ppct) if self.ppct else "",
            f"txtTenBai_{self.row_key}": self.ten_bai,
            f"txtGhiChu_{self.row_key}": self.ghi_chu or "",
            f"cboTrangThai_{self.row_key}": self.trang_thai or "0",
        }


@dataclass
class PlanRequest:
    """Yêu cầu lập kế hoạch."""
    tuan_from: int
    tuan_to: int
    strategy: PlanStrategy = PlanStrategy.GEN_FROM_PREV_WEEK
    mode: PlanMode = PlanMode.PREVIEW_APPLY

    # Filter: chỉ chạy với các (lop_id, phan_mon_id). Empty = all.
    only_lop_ids: list[str] = field(default_factory=list)
    only_phan_mon_ids: list[str] = field(default_factory=list)

    # Skip tuần đã có data (≥ N filled slots)
    skip_weeks_with_data: bool = False
    skip_threshold: int = 1

    # Auto-skip nếu đã chạy gen_prev (server tự copy đầy đủ)
    auto_skip_filled_after_gen: bool = True

    def __post_init__(self):
        if self.tuan_from > self.tuan_to:
            raise ValueError("tuan_from > tuan_to")


@dataclass
class WeekPlan:
    """Plan cho 1 tuần."""
    tuan: int
    strategy: PlanStrategy
    pre_action: str = ""           # "gen_prev" | "gen_tkb" | "" (no-op)
    pre_action_args: dict = field(default_factory=dict)
    fill_ops: list[FillOp] = field(default_factory=list)
    skip_reason: str = ""
    estimated_changes: int = 0     # số fill_ops không skip


@dataclass
class PlanReport:
    """Toàn bộ plan trên range tuần."""
    request: PlanRequest
    week_plans: list[WeekPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_ops(self) -> int:
        return sum(p.estimated_changes for p in self.week_plans)

    @property
    def total_pre_actions(self) -> int:
        return sum(1 for p in self.week_plans if p.pre_action)


# ---------------------------------------------------------------
# WeekPlanner
# ---------------------------------------------------------------

class WeekPlanner:
    """Sinh kế hoạch dựa trên YearScanReport + LessonCatalog hoặc KHDHProfile.

    2 chế độ chính:

    1. **Theo profile (mới — EVEN_ODD_TEMPLATES)**:
       Truyền `profile=KHDHProfile` (đã có TKB lẻ/chẵn + ppct_starts).
       Strategy = `EVEN_ODD_TEMPLATES`. `report` và `catalog` optional.

    2. **Theo phân tích web (cũ — các strategy khác)**:
       Truyền `report=YearScanReport` + `catalog=LessonCatalog`.
       Dùng cho tab Nâng cao.
    """

    def __init__(
        self,
        report: YearScanReport | None = None,
        catalog: LessonCatalog | None = None,
        weeks_data: dict[int, WeekData] | None = None,
        profile=None,  # Optional[KHDHProfile] — type hint string để tránh circular import
        client=None,   # Optional[KHDHClient] — dùng cho scan-before-fill (v2)
    ):
        self.report = report
        self.catalog = catalog
        self.weeks_data = weeks_data or {}
        self.profile = profile
        self.client = client
        # v2: Cache progress được scan từ web ngay trước khi build plan.
        # Key = tuan, Value = {group_key: SubjectProgress} computed từ
        # mọi tuần ≤ tuan đã scan. Reset đầu mỗi `generate_plan()`.
        self._scan_cache: dict[int, dict[str, "SubjectProgress"]] = {}
        # Cache WeekData đã fetch để tránh fetch trùng nếu cùng tuần
        # được referenced nhiều lần (perf optimization).
        self._fetched_weeks: dict[int, WeekData] = {}

    # -----------------------------------------------------------
    # Plan generation
    # -----------------------------------------------------------

    def generate_plan(self, request: PlanRequest) -> PlanReport:
        """Sinh PlanReport cho range tuần."""
        report = PlanReport(request=request)

        # Strategy EVEN_ODD_TEMPLATES dùng path riêng vì cần persist
        # progress_by_group xuyên qua các tuần (PPCT cuốn chiếu)
        if request.strategy == PlanStrategy.EVEN_ODD_TEMPLATES:
            return self._generate_even_odd_plan(request, report)

        # Strategy cũ — mỗi tuần độc lập
        if self.report is None:
            raise ValueError(
                "Strategy này yêu cầu YearScanReport. "
                "Hãy scan toàn năm trước hoặc chuyển sang EVEN_ODD_TEMPLATES."
            )
        weeks = list(range(request.tuan_from, request.tuan_to + 1))
        for tuan in weeks:
            wp = self._plan_for_week(tuan, request, report)
            report.week_plans.append(wp)
        return report

    def _generate_even_odd_plan(
        self, request: PlanRequest, report: PlanReport
    ) -> PlanReport:
        """Path riêng cho strategy EVEN_ODD_TEMPLATES.

        PPCT cuốn chiếu xuyên qua tuần, không reset mỗi tuần. Phải xử lý
        ở 1 chỗ tập trung.

        v2.1 (FIX): Profile.ppct_starts là source-of-truth TUYỆT ĐỐI.
        User đã setup PPCT bắt đầu cho tuần X bằng tay (có thể tính bù khoảng
        trống cho các tuần nghỉ), tool KHÔNG ĐƯỢC tự ý override bằng web scan.

        Logic cũ (đã rollback): scan tuần 1..tuan_from-1 → patch ppct_starts.
        → SAI vì user setup PPCT 72 cho tuần 26 (đã tính bù), web max=62 →
        tool override thành 63 → save sai PPCT.
        """
        if self.profile is None:
            raise ValueError(
                "Strategy EVEN_ODD_TEMPLATES yêu cầu KHDHProfile. "
                "Hãy truyền profile vào WeekPlanner."
            )

        # Init progress map từ ppct_starts — TUYỆT ĐỐI tôn trọng setup tay
        progress_by_group: dict[str, int] = {}
        for entry in self.profile.ppct_starts:
            progress_by_group[entry.group_key] = entry.ppct_start

        weeks = list(range(request.tuan_from, request.tuan_to + 1))
        for tuan in weeks:
            wp = self._plan_for_week_even_odd(tuan, request, progress_by_group)
            report.week_plans.append(wp)
        self._validate_counted_ppct_unique(report)
        return report

    @staticmethod
    def _validate_counted_ppct_unique(report: PlanReport) -> None:
        """Chặn trùng PPCT active trong cùng lớp/môn/phân môn trước khi ghi web."""
        seen: dict[tuple[str, int], FillOp] = {}
        for wp in report.week_plans:
            for op in wp.fill_ops:
                if op.skip or op.trang_thai not in TT_COUNTED or not op.ppct:
                    continue
                group_key = f"{op.lop_id}|{op.mon_id}|{op.phan_mon_id}"
                key = (group_key, int(op.ppct))
                old = seen.get(key)
                if old is not None:
                    raise ProfileSchemaError(
                        "PPCT.trung_active",
                        f"Trùng PPCT đang dạy {op.ppct} của "
                        f"{op.lop_text} / {op.phan_mon_text or op.mon_text}: "
                        f"tuần {old.tuan} ô {old.row_key} và tuần {op.tuan} ô {op.row_key}. "
                        "VnEdu thường sẽ không cho lưu, hãy đổi lịch Nghỉ/Dạy bù hoặc PPCT bắt đầu.",
                    )
                seen[key] = op

    def _plan_for_week_even_odd(
        self,
        tuan: int,
        request: PlanRequest,
        progress_by_group: dict[str, int],
    ) -> WeekPlan:
        """Sinh WeekPlan cho 1 tuần dùng template từ profile.

        Logic:
        1. Lấy template active: `profile.get_active_template(tuan)`
        2. Sort slots theo (thu, buoi, tiet) — đảm bảo PPCT chạy đúng thứ tự
        3. Mỗi slot: PPCT từ progress_by_group, +1 sau khi dùng
        4. Lookup tên bài từ catalog (nếu có); để rỗng nếu không có
        """
        wp = WeekPlan(tuan=tuan, strategy=PlanStrategy.EVEN_ODD_TEMPLATES)
        template = self.profile.get_active_template(tuan)

        if not template or not template.slots:
            # Tuần này không có slot trong template → skip
            wp.skip_reason = (
                f"Mẫu TKB cho tuần {tuan} "
                f"({'chẵn' if self.profile.is_chan(tuan) else 'lẻ'}) "
                "rỗng — không có tiết nào để nhập."
            )
            return wp

        # Sort slots để PPCT cuốn chiếu đúng theo thời gian thực.
        # Quy tắc Nghỉ/Dạy bù được đưa vào cùng timeline để tránh trùng
        # row_key và tránh tăng PPCT sai khi một tiết nghỉ chưa được dạy lại.
        sorted_slots = sorted(
            template.slots,
            key=lambda s: (s.thu, s.buoi, s.tiet),
        )
        template_by_key = {s.slot_key: s for s in sorted_slots}

        events_this_week = [
            e for e in profile_schedule_events(self.profile)
            if e.enabled and e.tuan == tuan
        ]
        nghi_by_key: dict[str, TKBScheduleEvent] = {}
        day_bu_by_key: dict[str, TKBScheduleEvent] = {}
        seen_positions: dict[str, TKBScheduleEvent] = {}
        for event in events_this_week:
            existing = seen_positions.get(event.row_key)
            if existing is not None:
                raise ProfileSchemaError(
                    "NghiDayBu.trung_o",
                    f"Tuần {tuan}, {event.label} bị trùng vị trí "
                    f"thứ {event.thu}, buổi {event.buoi}, tiết {event.tiet} "
                    f"với {existing.label}.",
                )
            seen_positions[event.row_key] = event
            if event.kind == TKB_EVENT_NGHI:
                nghi_by_key[event.row_key] = event
            elif event.kind == TKB_EVENT_DAY_BU:
                day_bu_by_key[event.row_key] = event

        def _slot_key_sort(row_key: str) -> tuple[int, int, int]:
            try:
                thu, buoi, tiet = (int(x) for x in row_key.split("_", 2))
                return thu, buoi, tiet
            except Exception:
                return 99, 99, 99

        def _make_op(
            *,
            slot: SlotEntry,
            row_key: str,
            ppct: int,
            trang_thai: str,
            ghi_chu: str = "",
            notes: str = "",
            source: str = "tkb",
        ) -> FillOp:
            ten_bai = self._lookup_ten_bai(
                slot.lop_id, slot.mon_id, slot.phan_mon_id, ppct
            )
            if not ten_bai and not notes:
                notes = "Catalog chưa có tên bài cho PPCT này"
            return FillOp(
                tuan=tuan,
                row_key=row_key,
                lop_id=slot.lop_id,
                lop_text=slot.lop_text,
                mon_id=slot.mon_id,
                mon_text=slot.mon_text,
                phan_mon_id=slot.phan_mon_id,
                phan_mon_text=slot.phan_mon_text,
                ppct=ppct,
                ten_bai=ten_bai,
                source="catalog" if ten_bai and source == "tkb" else source,
                notes=notes,
                ghi_chu=ghi_chu,
                trang_thai=trang_thai,
            )

        row_keys = set(template_by_key) | set(day_bu_by_key)
        for row_key in sorted(row_keys, key=_slot_key_sort):
            day_bu_event = day_bu_by_key.get(row_key)
            if day_bu_event is not None:
                if row_key in template_by_key and not day_bu_event.replace_normal_slot:
                    raise ProfileSchemaError(
                        "NghiDayBu.day_bu_trung_tkb",
                        f"Tiết Dạy bù tuần {tuan}, thứ {day_bu_event.thu}, "
                        f"buổi {day_bu_event.buoi}, tiết {day_bu_event.tiet} "
                        "đang trùng ô TKB thường nhưng chưa được xác nhận ghi đè.",
                    )
                source_slot = day_bu_event.to_slot_at_target()
                group_key = day_bu_event.group_key
                ppct = progress_by_group.get(group_key, 1)
                note = day_bu_event.note or "Dạy bù"
                notes = "Dạy bù: PPCT được tính tiếp và tăng sau tiết này"
                if row_key in template_by_key:
                    notes += "; vị trí này dùng cho tiết bù thay tiết TKB thường"
                wp.fill_ops.append(_make_op(
                    slot=source_slot,
                    row_key=row_key,
                    ppct=ppct,
                    trang_thai=TT_DAY_BU,
                    ghi_chu=note,
                    notes=notes,
                    source="tkb_day_bu",
                ))
                progress_by_group[group_key] = ppct + 1
                continue

            slot = template_by_key.get(row_key)
            if slot is None:
                continue
            group_key = slot.group_key
            ppct = progress_by_group.get(group_key, 1)
            nghi_event = nghi_by_key.get(row_key)
            if nghi_event is not None:
                if not nghi_event.source_matches_slot(slot):
                    raise ProfileSchemaError(
                        "NghiDayBu.nghi_khong_khop",
                        f"Tiết Nghỉ tuần {tuan}, thứ {slot.thu}, buổi {slot.buoi}, "
                        f"tiết {slot.tiet} không còn đúng môn/lớp nguồn đã chọn.",
                    )
                wp.fill_ops.append(_make_op(
                    slot=slot,
                    row_key=row_key,
                    ppct=ppct,
                    trang_thai=TT_NGHI,
                    ghi_chu=nghi_event.note or "Nghỉ",
                    notes="Nghỉ: vẫn ghi PPCT nhưng không tăng số",
                    source="tkb_nghi",
                ))
                continue

            wp.fill_ops.append(_make_op(
                slot=slot,
                row_key=row_key,
                ppct=ppct,
                trang_thai=TT_BINH_THUONG,
            ))
            # Increment PPCT cho slot kế tiếp cùng group
            progress_by_group[group_key] = ppct + 1

        # Apply filter only_lop_ids / only_phan_mon_ids
        if request.only_lop_ids or request.only_phan_mon_ids:
            for op in wp.fill_ops:
                if request.only_lop_ids and op.lop_id not in request.only_lop_ids:
                    op.skip = True
                if (request.only_phan_mon_ids
                        and op.phan_mon_id not in request.only_phan_mon_ids):
                    op.skip = True

        wp.estimated_changes = sum(1 for op in wp.fill_ops if not op.skip)
        return wp

    def _lookup_ten_bai(self, lop_id: str, mon_id: str,
                        phan_mon_id: str, ppct: int) -> str:
        """Tìm tên bài trong catalog (nếu có). Trả "" nếu không có."""
        if self.catalog is None:
            return ""
        try:
            return self.catalog.lookup_with_fallback(
                lop_id, mon_id, phan_mon_id, ppct
            ) or ""
        except Exception:
            return ""

    def _plan_for_week(self, tuan: int, request: PlanRequest, report: PlanReport) -> WeekPlan:
        """Plan cho 1 tuần."""
        wp = WeekPlan(tuan=tuan, strategy=request.strategy)

        cur_data = self.weeks_data.get(tuan)
        if cur_data and request.skip_weeks_with_data:
            filled_count = len(cur_data.filled_slots)
            if filled_count >= request.skip_threshold:
                wp.skip_reason = f"Đã có {filled_count} slot, skip"
                return wp

        # Build expected slots cho tuần này
        # Dùng pattern tuần X-1 nếu có, fallback pattern hiện tại của report
        ref_pattern = self._find_reference_pattern(tuan)
        if ref_pattern is None:
            wp.skip_reason = "Không tìm được TKB pattern reference"
            return wp

        # ---- STRATEGY ----
        if request.strategy == PlanStrategy.GEN_FROM_PREV_WEEK:
            # Pre-action: gọi API gen từ tuần trước
            wp.pre_action = "gen_prev"
            wp.pre_action_args = {"tuan": tuan}
            # Sau khi gen, server sẽ copy đầy đủ. Nếu auto_skip_filled_after_gen=True
            # thì không cần fill thêm. Nếu user muốn override, sinh ops:
            if not request.auto_skip_filled_after_gen:
                wp.fill_ops = self._build_fill_ops_for_pattern(tuan, ref_pattern, request)

        elif request.strategy == PlanStrategy.GEN_FROM_TKB:
            wp.pre_action = "gen_tkb"
            wp.pre_action_args = {"tuan": tuan}
            # Gen từ TKB không có PPCT/ten_bai → cần fill
            wp.fill_ops = self._build_fill_ops_for_pattern(tuan, ref_pattern, request)

        elif request.strategy == PlanStrategy.FILL_FROM_SCRATCH:
            wp.fill_ops = self._build_fill_ops_for_pattern(tuan, ref_pattern, request)

        elif request.strategy == PlanStrategy.UPDATE_EMPTY_ONLY:
            # Chỉ điền slot đang trống
            if cur_data is None:
                wp.skip_reason = "Chưa scan tuần này — bỏ qua chế độ update_empty"
                return wp
            wp.fill_ops = self._build_fill_ops_for_empty_slots(tuan, cur_data, request)

        # Apply lọc only_lop_ids, only_phan_mon_ids
        if request.only_lop_ids or request.only_phan_mon_ids:
            for op in wp.fill_ops:
                if request.only_lop_ids and op.lop_id not in request.only_lop_ids:
                    op.skip = True
                if request.only_phan_mon_ids and op.phan_mon_id not in request.only_phan_mon_ids:
                    op.skip = True

        wp.estimated_changes = sum(1 for op in wp.fill_ops if not op.skip)
        return wp

    def _find_reference_pattern(self, tuan: int) -> YearTKBPattern | None:
        """Tìm pattern TKB phù hợp cho tuần này:
        1. Nếu tuan đã có pattern (đã từng có data) → dùng pattern đó
        2. Fallback: lấy pattern của tuần liền kề có data (tuần trước hoặc sau)
        """
        for p in self.report.patterns:
            if tuan in p.weeks:
                return p
        # Tìm tuần liền kề có pattern
        all_weeks_with_pattern = sorted(set(t for p in self.report.patterns for t in p.weeks))
        if not all_weeks_with_pattern:
            return None
        # Closest week
        closest = min(all_weeks_with_pattern, key=lambda t: abs(t - tuan))
        for p in self.report.patterns:
            if closest in p.weeks:
                return p
        return None

    def _build_fill_ops_for_pattern(
        self, tuan: int, pattern: YearTKBPattern, request: PlanRequest
    ) -> list[FillOp]:
        """Sinh ops dựa trên TKB pattern. PPCT cuốn chiếu từ progress."""
        ops: list[FillOp] = []
        # Để increment PPCT đúng, cần biết tuần này so với các tuần đã có data
        # → Chia theo (lop, mon, phan_mon) và giả định mỗi slot tuần = 1 PPCT
        # Sort slots theo (thu, buoi, tiet) — phần cứng để PPCT chạy
        slots_sorted = sorted(pattern.slots, key=lambda s: (s.thu, s.buoi_idx, s.tiet_idx))

        # Group theo (lop, mon) để increment PPCT đúng cho mỗi nhóm trong 1 tuần
        # Tính delta tuần: nếu có pattern data ở tuần khác, lấy mức PPCT đó + (tuan - tuan_đó) * slots/tuần
        # Đơn giản hơn: lấy progress.next_ppct và increment per slot trong tuần này

        # PPCT counter cho từng group trong tuần này
        group_counter: dict[str, int] = {}
        # Đầu tiên copy next_ppct từ progress (trừ đi 1 vì sẽ +1 ở slot đầu)
        for slot_key in slots_sorted:
            # Tìm phan_mon từ data hiện có (vì pattern không lưu phan_mon)
            phan_mon_id = self._guess_phan_mon_id(slot_key.lop_id, slot_key.mon_id)
            group = f"{slot_key.lop_id}|{slot_key.mon_id}|{phan_mon_id}"
            if group not in group_counter:
                # Tính next_ppct cho group này TẠI TUẦN tuan
                group_counter[group] = self._compute_starting_ppct(
                    slot_key.lop_id, slot_key.mon_id, phan_mon_id, tuan
                )
            ppct = group_counter[group]

            # Lookup tên bài từ catalog
            ten_bai = self.catalog.lookup_with_fallback(
                slot_key.lop_id, slot_key.mon_id, phan_mon_id, ppct
            ) or ""

            ops.append(FillOp(
                tuan=tuan,
                row_key=f"{slot_key.thu}_{slot_key.buoi_idx}_{slot_key.tiet_idx}",
                lop_id=slot_key.lop_id,
                mon_id=slot_key.mon_id,
                phan_mon_id=phan_mon_id,
                ppct=ppct,
                ten_bai=ten_bai,
                source="catalog" if ten_bai else "tkb",
                notes="" if ten_bai else "Catalog chưa có tên bài",
            ))
            group_counter[group] = ppct + 1
        return ops

    def _build_fill_ops_for_empty_slots(
        self, tuan: int, cur_data: WeekData, request: PlanRequest
    ) -> list[FillOp]:
        """Chỉ sinh ops cho slot có lop nhưng chưa có ppct/ten_bai."""
        ops: list[FillOp] = []
        empty_slots = [s for s in cur_data.tkb_slots if not s.is_filled]
        for slot in empty_slots:
            # Phan_mon hiện tại nếu đã có, còn không thì guess
            pm_id = slot.phan_mon_id
            if not pm_id or pm_id == "0":
                pm_id = self._guess_phan_mon_id(slot.lop_id, slot.mon_id)

            try:
                cur_ppct = int(slot.ppct) if slot.ppct else 0
            except ValueError:
                cur_ppct = 0
            ppct = cur_ppct if cur_ppct > 0 else self._compute_starting_ppct(
                slot.lop_id, slot.mon_id, pm_id, tuan
            )
            ten_bai = slot.ten_bai or self.catalog.lookup_with_fallback(
                slot.lop_id, slot.mon_id, pm_id, ppct
            ) or ""

            ops.append(FillOp(
                tuan=tuan, row_key=slot.row_key,
                lop_id=slot.lop_id, mon_id=slot.mon_id,
                phan_mon_id=pm_id, ppct=ppct, ten_bai=ten_bai,
                source="catalog" if ten_bai else "tkb",
                notes="" if ten_bai else "Catalog chưa có tên bài",
            ))
        return ops

    # -----------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------

    def _guess_phan_mon_id(self, lop_id: str, mon_id: str) -> str:
        """Đoán phan_mon_id thường dùng cho (lop, mon) — tìm từ progress."""
        # Group theo lop/mon, lấy phan_mon được dùng nhiều nhất
        best_pm = ""
        best_count = 0
        for key, p in self.report.subject_progress.items():
            if p.lop_id == lop_id and p.mon_id == mon_id:
                cnt = len(p.ppct_history)
                if cnt > best_count:
                    best_count = cnt
                    best_pm = p.phan_mon_id
        return best_pm

    def _compute_starting_ppct(
        self, lop_id: str, mon_id: str, phan_mon_id: str, target_tuan: int
    ) -> int:
        """Tính PPCT bắt đầu cho group (lop, mon, pm) tại target_tuan.

        Priority order (v2.1 — profile-first, scan chỉ là backup):
          1. Profile ppct_starts: TUYỆT ĐỐI tôn trọng config tay của user.
             User có thể tính bù khoảng trống cho tuần nghỉ → đặt PPCT lớn hơn
             max trên web. Tool KHÔNG ĐƯỢC tự ý đoán.
          2. Scan-based: chỉ dùng khi profile chưa có (group mới).
          3. Legacy report.subject_progress + delta tuần.
          4. Fallback 1.

        Lý do đảo priority (sửa bug v2): trước đây scan ưu tiên hơn profile,
        gây bug user setup PPCT 72 cho tuần 26 (đã tính bù) nhưng web max=62
        → tool override thành 63. Đây là source-of-truth bị đảo.
        """
        if not phan_mon_id:
            return 1
        key = f"{lop_id}|{mon_id}|{phan_mon_id}"

        # 1. Profile ppct_starts — ƯU TIÊN CAO NHẤT (config user tay)
        if self.profile is not None:
            try:
                start = self.profile.get_ppct_start(lop_id, mon_id, phan_mon_id)
                if start and start > 0:
                    return start
            except Exception:
                pass

        # 2. Scan-based — fallback nếu profile chưa có group này
        scan_progress = self._scan_cache.get(target_tuan)
        if scan_progress is not None:
            sp = scan_progress.get(key)
            if sp is not None and sp.last_ppct > 0:
                return sp.last_ppct + 1

        # 3. Legacy: report.subject_progress + delta
        if self.report is None:
            return 1
        p = self.report.subject_progress.get(key)
        if not p or not p.ppct_history:
            return 1
        if target_tuan <= p.last_tuan:
            return p.next_ppct
        weeks_count = max(1, len({t for t, _, _ in p.ppct_history}))
        ppct_count = len(p.ppct_history)
        slots_per_week = max(1, ppct_count // weeks_count) if ppct_count > 0 else 1
        delta = target_tuan - p.last_tuan
        return p.last_ppct + 1 + (delta - 1) * slots_per_week

    # -----------------------------------------------------------
    # v2: Scan-before-fill helpers
    # -----------------------------------------------------------

    def _scan_progress_up_to(self, target_tuan: int,
                             tuan_from: int = 1) -> dict[str, "SubjectProgress"]:
        """Scan web cho mọi tuần [tuan_from..target_tuan-1] → compute progress.

        Idempotent + cached: nếu đã có `_scan_cache[target_tuan]` → return.

        Return: dict[group_key, SubjectProgress] đã filter theo TT_COUNTED và
        track extras qua `compute_progress`. Nếu không có client → return {}.

        Errors khi fetch_week được swallow (log warning) để 1 tuần fail không
        block toàn plan — fallback sang legacy logic ở `_compute_starting_ppct`.
        """
        if target_tuan in self._scan_cache:
            return self._scan_cache[target_tuan]
        if self.client is None:
            self._scan_cache[target_tuan] = {}
            return {}

        scanned: dict[int, WeekData] = {}
        for t in range(tuan_from, target_tuan):
            if t in self._fetched_weeks:
                scanned[t] = self._fetched_weeks[t]
                continue
            try:
                wd = self.client.fetch_week(t)
                if wd is not None:
                    scanned[t] = wd
                    self._fetched_weeks[t] = wd
            except Exception as e:
                logger.warning("scan tuần %s failed: %s", t, e)
                continue

        progress = PatternAnalyzer.compute_progress(scanned)
        self._scan_cache[target_tuan] = progress
        return progress

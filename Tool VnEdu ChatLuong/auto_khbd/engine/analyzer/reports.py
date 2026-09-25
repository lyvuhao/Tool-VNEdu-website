"""Báo cáo tiến độ môn và kết quả quét sức khoẻ (health scan)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models import YearTKBPattern

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..parser import WeekData


@dataclass
class SubjectProgress:
    """Tiến độ PPCT của 1 (lop, mon, phan_mon) trên năm."""
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str

    # Tiến độ hiện tại
    last_ppct: int = 0          # PPCT cuối cùng đã có
    last_ten_bai: str = ""      # Tên bài cuối cùng
    last_tuan: int = 0          # Tuần ghi PPCT cuối
    next_ppct: int = 1          # Đề xuất PPCT tiếp theo

    # Lịch sử
    ppct_history: list[tuple[int, int, str]] = field(default_factory=list)
    # list[(tuan, ppct, ten_bai)]

    # --- v2: Tracking tiết "extra" (dạy bù / chèn lịch / dạy chung) ---
    # Dùng để: (1) báo cáo cho user biết PPCT đã shift bao nhiêu vì có dạy bù,
    # (2) phát hiện sớm nếu PPCT next bị lệch do tool không quét lại web.
    extra_count: int = 0
    # list[(tuan, thu, buoi_idx, tiet_idx, ppct, trang_thai)]
    extras: list[tuple[int, int, int, int, int, str]] = field(default_factory=list)


@dataclass
class YearScanReport:
    """Tổng hợp scan toàn năm."""
    weeks_scanned: list[int] = field(default_factory=list)
    weeks_with_data: list[int] = field(default_factory=list)
    weeks_empty: list[int] = field(default_factory=list)
    patterns: list[YearTKBPattern] = field(default_factory=list)
    subject_progress: dict[str, SubjectProgress] = field(default_factory=dict)
    # key = "lop_id|mon_id|phan_mon_id"
    a_phan_mon_global: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------
# Health scan structures — chỉ trong scope group user có quyền nhập
# ---------------------------------------------------------------

HEALTH_KIND_PPCT_GAP = "ppct_gap"


HEALTH_KIND_PPCT_DUPLICATE = "ppct_duplicate"


HEALTH_KIND_SLOT_MISSING = "slot_missing"


HEALTH_KIND_SLOT_EXTRA = "slot_extra"


HEALTH_KIND_GROUP_NO_DATA = "group_no_data"


@dataclass
class HealthIssue:
    """1 issue trong health report cho 1 group cụ thể.

    severity:
      - ok     : không có issue (chỉ dùng khi user bật show all)
      - warn   : issue nhẹ
      - error  : issue nặng
    """
    kind: str
    severity: str
    group_key: str
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str
    week_from: int = 0
    week_to: int = 0
    ppct_from: int = 0
    ppct_to: int = 0
    missing_ppcts: list[int] = field(default_factory=list)
    duplicate_ppct: int = 0
    expected_slots: int = 0
    actual_slots: int = 0
    message: str = ""


@dataclass
class HealthScanResult:
    """Kết quả quét tình trạng KHDH trong scope allowed_groups."""
    allowed_groups: set[str] = field(default_factory=set)
    scanned_weeks: list[int] = field(default_factory=list)
    issues: list[HealthIssue] = field(default_factory=list)
    weeks_data: dict[int, "WeekData"] = field(default_factory=dict)
    actual_groups_seen: set[str] = field(default_factory=set)
    total_slots_scanned: int = 0

    @property
    def total_issue_count(self) -> int:
        return len([x for x in self.issues if x.severity != "ok"])

    @property
    def total_missing_ppcts(self) -> int:
        total = 0
        for issue in self.issues:
            total += len(issue.missing_ppcts or [])
        return total

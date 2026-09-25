"""Cấu trúc dữ liệu và hằng số trạng thái tiết cho bộ phân tích TKB."""

from __future__ import annotations

from dataclasses import dataclass, field


# ######################################################################
# Section: analyzer
# ######################################################################





# ---------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------

@dataclass(frozen=True)
class _SlotKey:
    """Slot identity dùng để compare TKB pattern (không tính ten_bai/ppct)."""
    thu: int
    buoi_idx: int
    tiet_idx: int
    lop_id: str
    mon_id: str


@dataclass
class YearTKBPattern:
    """1 pattern TKB — set các slot (lop, mon) ở từng (thu, buoi, tiet)."""
    pattern_id: int
    weeks: list[int] = field(default_factory=list)
    slots: list[_SlotKey] = field(default_factory=list)
    sample_data: dict[str, list] = field(default_factory=dict)  # row_key -> [WeekData refs]

    def short_label(self) -> str:
        if not self.weeks:
            return f"P{self.pattern_id} (empty)"
        if len(self.weeks) == 1:
            return f"P{self.pattern_id} (Tuần {self.weeks[0]}) — {len(self.slots)} slot"
        first, last = min(self.weeks), max(self.weeks)
        cont = list(range(first, last + 1)) == sorted(self.weeks)
        if cont:
            return f"P{self.pattern_id} (Tuần {first}-{last}) — {len(self.slots)} slot"
        return f"P{self.pattern_id} ({len(self.weeks)} tuần) — {len(self.slots)} slot"


# Constants cho pattern lẻ/chẵn — single source of truth.
PATTERN_BOTH = "both"             # Group xuất hiện cả tuần lẻ và chẵn


PATTERN_LE_ONLY = "le_only"       # Chỉ tuần lẻ


PATTERN_CHAN_ONLY = "chan_only"   # Chỉ tuần chẵn


PATTERN_NONE = "none"             # Không có data


# Severity levels cho health warnings — sort priority cao→thấp.
SEVERITY_ERROR = "error"


SEVERITY_WARNING = "warning"


SEVERITY_INFO = "info"


_SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


# ---------------------------------------------------------------
# Trạng thái slot (cboTrangThai value mapping — verified từ web VnEdu)
# ---------------------------------------------------------------
# value="-1" --- (placeholder)
# value="0"  Bình thường   ← TKB chuẩn
# value="1"  Nghỉ          ← không tính PPCT
# value="2"  Dạy thay      ← tính PPCT (giáo viên dạy giúp)
# value="3"  Dạy bù        ← TIẾT BÙ (extra)
# value="4"  Chèn lịch     ← TIẾT BÙ (extra)
# value="5"  Dạy chung     ← tính PPCT
# value="6"  Phụ đạo       ← ngoài chương trình, không tính PPCT
# value="7"  Bồi dưỡng     ← ngoài chương trình
# value="8"  Dạy thêm      ← ngoài chương trình
TT_BINH_THUONG = "0"


TT_NGHI = "1"


TT_DAY_THAY = "2"


TT_DAY_BU = "3"


TT_CHEN_LICH = "4"


TT_DAY_CHUNG = "5"


# Các trạng thái có tính PPCT vào tiến độ chương trình
TT_COUNTED = frozenset({TT_BINH_THUONG, TT_DAY_THAY, TT_DAY_BU,
                        TT_CHEN_LICH, TT_DAY_CHUNG})


# Các trạng thái đánh dấu "tiết bù / chèn thêm" (ngoài TKB chuẩn)
TT_EXTRA = frozenset({TT_DAY_BU, TT_CHEN_LICH, TT_DAY_CHUNG})


# Loại sự kiện TKB do giáo viên tự đánh dấu trong hồ sơ.
TKB_EVENT_NGHI = "nghi"


TKB_EVENT_DAY_BU = "day_bu"


TKB_EVENT_LABELS = {
    TKB_EVENT_NGHI: "Nghỉ",
    TKB_EVENT_DAY_BU: "Dạy bù",
}

"""Smart Repair — phân tích và đề xuất sửa PPCT lệch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, TYPE_CHECKING

from ..analyzer.models import TT_COUNTED, TT_EXTRA, TT_NGHI
from ..profile.profile import KHDHProfile

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..parser import WeekData


# ---------------------------------------------------------------
# SmartRepair — phân tích + đề xuất sửa PPCT lệch
# ---------------------------------------------------------------
#
# Bài toán: User lỡ nhập sai PPCT 1 tuần (vd nhập 86 thay 83) → các
# tuần sau bị shift theo. Tool quét toàn bộ tuần, học pattern PPCT của
# mỗi group từ vùng tin cậy (tuần <= anchor), tính PPCT đúng cuốn chiếu,
# đề xuất sửa.
#
# Web KHDH tự reject duplicate PPCT cùng group → KHÔNG cần lo trùng,
# chỉ cần lo gap (PPCT[i+1] - PPCT[i] != 1) và lệch base.

# Pattern xuất hiện theo tuần
SR_PATTERN_EVERY_WEEK = "every_week"   # Xuất hiện mọi tuần


SR_PATTERN_ODD_ONLY = "odd_only"       # Chỉ tuần lẻ (1,3,5,7,9)


SR_PATTERN_EVEN_ONLY = "even_only"     # Chỉ tuần chẵn (0,2,4,6,8)


SR_PATTERN_AMBIGUOUS = "ambiguous"     # Lẫn lộn — không xác định


@dataclass
class GroupAppearance:
    """Phân tích 1 group `(lop, mon, pm)` qua các tuần.

    Sau khi build từ web data, dùng `proposed_ppct(tuan)` để hỏi
    PPCT đúng cuốn chiếu cho 1 tuần bất kỳ.

    Logic Nghỉ (v3):
      - Tiết counted (BT, Dạy thay, ...) → counter += 1, dùng counter
      - Tiết Nghỉ → ghi PPCT = counter + 1, KHÔNG advance counter
        (tiết counted kế tiếp sẽ "reuse" số này khi advance)
    """
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str

    # tuần → ppct hiện tại trên web (chỉ slot có lop + ppct > 0 + tt counted)
    ppct_by_week: dict[int, int] = field(default_factory=dict)
    # Tuần có trạng thái = Nghỉ (TT_NGHI). Vẫn có ppct trên web nhưng
    # KHÔNG advance counter — tiết counted kế tiếp dùng số này.
    nghi_weeks: dict[int, int] = field(default_factory=dict)
    # Pattern detect qua các tuần xuất hiện (cả counted + Nghỉ)
    pattern: str = SR_PATTERN_EVERY_WEEK

    @property
    def group_key(self) -> str:
        return f"{self.lop_id}|{self.mon_id}|{self.phan_mon_id}"

    @property
    def appeared_weeks(self) -> list[int]:
        """Hợp các tuần có data (counted + Nghỉ) — dùng cho pattern detect."""
        return sorted(set(self.ppct_by_week.keys()) | set(self.nghi_weeks.keys()))

    def is_nghi_in_week(self, tuan: int) -> bool:
        """Group có trạng thái Nghỉ ở tuần này không?"""
        return tuan in self.nghi_weeks

    @staticmethod
    def _looks_like_every_week(weeks: list[int]) -> bool:
        """Heuristic nhận diện tiết xuất hiện hằng tuần.

        Yêu cầu có bằng chứng dày: với 2 mẫu thì phải là 2 tuần liền nhau;
        với >=3 mẫu thì coverage trong span phải đủ cao và có ít nhất một
        cặp tuần liền nhau. Nếu dữ liệu thưa/lẫn lộn → để ambiguous.
        """
        if len(weeks) < 2:
            return False
        gaps = [b - a for a, b in zip(weeks, weeks[1:])]
        if len(weeks) == 2:
            return gaps[0] == 1
        span = weeks[-1] - weeks[0] + 1
        coverage = len(weeks) / span if span > 0 else 0.0
        adjacent_pairs = sum(1 for g in gaps if g == 1)
        return coverage >= 0.75 and adjacent_pairs >= 1

    @staticmethod
    def _looks_like_parity_cycle(weeks: list[int]) -> bool:
        """Heuristic nhận diện chu kỳ chẵn/lẻ.

        Toàn bộ mẫu đã cùng parity. Với 2 mẫu chỉ nhận nếu cách nhau đúng
        2 tuần. Với >=3 mẫu cho phép thiếu một vài tuần nhưng phải có
        coverage >= 75% trên chuỗi parity và ít nhất một cặp cách 2 tuần.
        """
        if len(weeks) < 2:
            return False
        gaps = [b - a for a, b in zip(weeks, weeks[1:])]
        if len(weeks) == 2:
            return gaps[0] == 2
        expected_count = ((weeks[-1] - weeks[0]) // 2) + 1
        coverage = len(weeks) / expected_count if expected_count > 0 else 0.0
        return coverage >= 0.75 and any(g == 2 for g in gaps)

    def detect_pattern(self, sample_weeks: Iterable[int] | None = None) -> None:
        """Set self.pattern dựa vào tuần xuất hiện.

        Nếu truyền `sample_weeks`, chỉ học pattern từ các tuần đó. Smart
        Repair dùng tuần <= anchor làm vùng tin cậy, nhưng vẫn giữ data sau
        anchor trong group để biết tuần Nghỉ khi tính PPCT cuốn chiếu.
        """
        weeks = self.appeared_weeks
        if sample_weeks is not None:
            allowed = {int(w) for w in sample_weeks}
            weeks = [w for w in weeks if w in allowed]
        if not weeks:
            self.pattern = SR_PATTERN_AMBIGUOUS
            return
        odd_weeks = [w for w in weeks if not KHDHProfile.is_chan(w)]
        even_weeks = [w for w in weeks if KHDHProfile.is_chan(w)]
        if odd_weeks and not even_weeks:
            self.pattern = (
                SR_PATTERN_ODD_ONLY
                if self._looks_like_parity_cycle(weeks)
                else SR_PATTERN_AMBIGUOUS
            )
        elif even_weeks and not odd_weeks:
            self.pattern = (
                SR_PATTERN_EVEN_ONLY
                if self._looks_like_parity_cycle(weeks)
                else SR_PATTERN_AMBIGUOUS
            )
        elif odd_weeks and even_weeks:
            self.pattern = (
                SR_PATTERN_EVERY_WEEK
                if self._looks_like_every_week(weeks)
                else SR_PATTERN_AMBIGUOUS
            )
        else:
            self.pattern = SR_PATTERN_AMBIGUOUS

    def is_active_in_week(self, tuan: int) -> bool:
        """Group có dạy trong tuần này theo pattern không?"""
        if self.pattern == SR_PATTERN_EVERY_WEEK:
            return True
        if self.pattern == SR_PATTERN_ODD_ONLY:
            return not KHDHProfile.is_chan(tuan)
        if self.pattern == SR_PATTERN_EVEN_ONLY:
            return KHDHProfile.is_chan(tuan)
        return False  # AMBIGUOUS: caller tự xử

    def proposed_ppct(self, tuan: int, anchor_tuan: int) -> int | None:
        """Tính PPCT đề xuất cho `tuan` dựa trên anchor.

        Logic v3 (Nghỉ-aware):
          - counter = ppct[anchor_tuan] (tuần anchor phải counted, không Nghỉ)
          - Duyệt từ anchor+1 → tuan:
            * Tiết Nghỉ: ppct = counter + 1, KHÔNG advance
            * Tiết counted: counter += 1, ppct = counter
            * Tiết không active (theo pattern): skip
          - Trả về ppct[tuan] hoặc None nếu group không active ở `tuan`.
        """
        if not self.is_active_in_week(tuan):
            return None
        anchor_ppct = self.ppct_by_week.get(anchor_tuan)
        if anchor_ppct is None:
            # Anchor có thể là tuần Nghỉ → vẫn cho phép, lấy ppct từ nghi_weeks
            anchor_ppct_nghi = self.nghi_weeks.get(anchor_tuan)
            if anchor_ppct_nghi is None:
                return None
            # Nếu anchor là Nghỉ, "counter ảo" ở 1 dưới ppct nghi (tiết Nghỉ
            # đặt chỗ ở counter+1 nhưng counter chưa advance)
            anchor_ppct = anchor_ppct_nghi - 1
        if tuan == anchor_tuan:
            # Trả về ppct hiện tại (counted hoặc nghi đều OK)
            return self.ppct_by_week.get(tuan) or self.nghi_weeks.get(tuan)
        if tuan < anchor_tuan:
            # Đếm ngược: phức tạp hơn — tạm bỏ qua, gắn None
            return None
        # tuan > anchor: cuốn chiếu Nghỉ-aware
        counter = anchor_ppct
        for t in range(anchor_tuan + 1, tuan + 1):
            if not self.is_active_in_week(t):
                continue
            if self.is_nghi_in_week(t):
                # Tiết Nghỉ: đặt chỗ ở counter + 1, không advance
                if t == tuan:
                    return counter + 1
                # Không advance counter
            else:
                # Tiết counted: advance + dùng
                counter += 1
                if t == tuan:
                    return counter
        # Nếu tuan không active trong loop → không chạm to t==tuan
        return None


@dataclass
class SmartRepairAction:
    """1 ô cần sửa — dữ liệu user xem trong preview tree."""
    tuan: int
    row_key: str
    group_key: str
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str
    current_ppct: int
    proposed_ppct: int
    current_ten_bai: str = ""
    proposed_ten_bai: str = ""    # fetch từ API getByTiet sau
    current_trang_thai: str = "0"
    notes: str = ""               # Cảnh báo/explain
    skip: bool = False            # User uncheck → bỏ qua

    @property
    def needs_change(self) -> bool:
        return int(self.current_ppct) != int(self.proposed_ppct)


@dataclass
class SmartRepairReport:
    """Toàn bộ phân tích + danh sách action đề xuất."""
    anchor_tuan: int
    tuan_from: int
    tuan_to: int
    groups: dict[str, GroupAppearance] = field(default_factory=dict)
    actions: list[SmartRepairAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def actions_to_apply(self) -> list[SmartRepairAction]:
        return [a for a in self.actions if not a.skip and a.needs_change]


def smart_repair_build_groups(
    weeks_data: dict[int, "WeekData"],
    pattern_tuan_to: int | None = None,
) -> dict[str, GroupAppearance]:
    """Build map group_key → GroupAppearance từ data fetch về.

    Lọc:
      - Slot không có lop_id / phan_mon_id → bỏ
      - Slot không có ppct numeric > 0 → bỏ
      - Slot trang_thai ∈ TT_EXTRA (Dạy bù/Chèn lịch/Dạy chung) → BỎ KHỎI
        pattern (đó là tiết bù, không cuốn chiếu liên tục).
      - Slot trang_thai = Nghỉ → ghi vào `nghi_weeks` (không advance counter).
      - Slot trang_thai ∈ TT_COUNTED (BT, Dạy thay) → ghi vào `ppct_by_week`.
      - Slot trang_thai non-counted khác (Phụ đạo, Bồi dưỡng, Dạy thêm) → bỏ.

    `pattern_tuan_to` giới hạn vùng học chu kỳ. Smart Repair truyền tuần
    anchor để chỉ học chẵn/lẻ/hằng tuần từ phần dữ liệu user tin là đúng.
    """
    groups: dict[str, GroupAppearance] = {}
    for tuan, wd in weeks_data.items():
        for s in wd.filled_slots:
            if not s.lop_id or s.lop_id == "0":
                continue
            if not s.phan_mon_id or s.phan_mon_id == "0":
                continue
            try:
                ppct = int(s.ppct)
            except (TypeError, ValueError):
                continue
            if ppct <= 0:
                continue
            tt = (s.trang_thai or "").strip()
            if tt in TT_EXTRA:
                continue
            # Phân loại Nghỉ riêng — vẫn track để cuốn chiếu Nghỉ-aware.
            is_nghi = (tt == TT_NGHI)
            is_counted = (not tt) or (tt in TT_COUNTED)
            if not is_nghi and not is_counted:
                # Phụ đạo / Bồi dưỡng / Dạy thêm → ngoài chương trình, skip
                continue
            key = f"{s.lop_id}|{s.mon_id}|{s.phan_mon_id}"
            g = groups.get(key)
            if g is None:
                g = GroupAppearance(
                    lop_id=s.lop_id, lop_text=s.lop_text or s.lop_id,
                    mon_id=s.mon_id, mon_text=s.mon_text or s.mon_id,
                    phan_mon_id=s.phan_mon_id,
                    phan_mon_text=s.phan_mon_text or s.phan_mon_id,
                )
                groups[key] = g
            # 1 ô/tuần là chuẩn (web đảm bảo unique). Defensive: lấy max.
            if is_nghi:
                existing = g.nghi_weeks.get(tuan)
                if existing is None or ppct > existing:
                    g.nghi_weeks[tuan] = ppct
            else:
                existing = g.ppct_by_week.get(tuan)
                if existing is None or ppct > existing:
                    g.ppct_by_week[tuan] = ppct
    # Detect pattern cho mỗi group (dùng cả counted + nghi). Nếu có anchor,
    # chỉ học chu kỳ từ vùng tin cậy <= anchor; data sau anchor vẫn được giữ
    # trong group để tính tuần Nghỉ khi cuốn chiếu PPCT.
    pattern_sample_weeks = None
    if pattern_tuan_to is not None:
        max_tuan = int(pattern_tuan_to)
        pattern_sample_weeks = [t for t in weeks_data.keys() if int(t) <= max_tuan]
    for g in groups.values():
        g.detect_pattern(pattern_sample_weeks)
    return groups

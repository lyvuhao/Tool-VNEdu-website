"""Gom nhóm mẫu TKB quét từ web."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...engine.parser import SlotData
from ...engine.profile.models import ProfileSchemaError, SlotEntry
from ...engine.profile.profile import KHDHProfile

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.parser import WeekData


# =====================================================================
# Worker — TKBScanWorker (quét TKB toàn năm + group theo pattern)
# =====================================================================

@dataclass
class TKBPattern:
    """1 mẫu TKB phát hiện được trên web — gom các tuần giống hệt nhau.

    Identity = `fingerprint` (tuple bất biến của các slot có lop). 2 tuần
    có cùng fingerprint nghĩa là TKB đã xếp y hệt nhau (lop × mon × pm
    ở cùng vị trí thu/buoi/tiet).

    `pattern_id` là nhãn ngắn ('A', 'B', 'C'...) chỉ dùng cho UI hiển thị,
    không persist xuống file.
    """
    pattern_id: str
    fingerprint: tuple
    weeks: list[int] = field(default_factory=list)
    slots: list[SlotEntry] = field(default_factory=list)

    @property
    def le_count(self) -> int:
        return sum(1 for w in self.weeks if not KHDHProfile.is_chan(w))

    @property
    def chan_count(self) -> int:
        return sum(1 for w in self.weeks if KHDHProfile.is_chan(w))

    @property
    def slot_count(self) -> int:
        return len(self.slots)

    @property
    def group_count(self) -> int:
        return len({(s.lop_id, s.mon_id, s.phan_mon_id) for s in self.slots})


@dataclass
class ImportTKBPatterns:
    """Kết quả phân nhóm TKB toàn năm.

    Properties phục vụ UX:
    - `has_le_chan_split`: True khi đúng 2 patterns + tách rõ lẻ/chẵn
      (mỗi pattern thuần 1 phía). Đây là điều kiện nghiêm ngặt — KHÔNG
      đề xuất split nếu có lẫn (vd 1 tuần lẻ ăn pattern chẵn).
    - `pattern_for_le` / `pattern_for_chan`: chỉ non-None khi
      `has_le_chan_split=True`.
    """
    patterns: list[TKBPattern] = field(default_factory=list)
    weeks_with_data: list[int] = field(default_factory=list)
    weeks_empty: list[int] = field(default_factory=list)
    fetch_errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def total_weeks_scanned(self) -> int:
        return (
            len(self.weeks_with_data) + len(self.weeks_empty)
            + len(self.fetch_errors)
        )

    @property
    def has_le_chan_split(self) -> bool:
        """Đề xuất "Tách lẻ/chẵn" CHỈ khi:
        - Có đúng 2 pattern non-empty.
        - Mỗi pattern thuần 1 phía (lẻ HOẶC chẵn).
        - Cả 2 pattern đều có ít nhất 2 tuần (tránh nhầm khi 1 tuần "ngoại
          lệ" tạo pattern riêng).
        """
        non_empty = [p for p in self.patterns if p.slot_count > 0]
        if len(non_empty) != 2:
            return False
        a, b = non_empty
        a_pure_le = a.le_count > 0 and a.chan_count == 0
        a_pure_chan = a.chan_count > 0 and a.le_count == 0
        b_pure_le = b.le_count > 0 and b.chan_count == 0
        b_pure_chan = b.chan_count > 0 and b.le_count == 0
        if not ((a_pure_le and b_pure_chan) or (a_pure_chan and b_pure_le)):
            return False
        if min(len(a.weeks), len(b.weeks)) < 2:
            return False
        return True

    @property
    def pattern_for_le(self) -> "TKBPattern | None":
        if not self.has_le_chan_split:
            return None
        for p in self.patterns:
            if p.slot_count > 0 and p.le_count > 0 and p.chan_count == 0:
                return p
        return None

    @property
    def pattern_for_chan(self) -> "TKBPattern | None":
        if not self.has_le_chan_split:
            return None
        for p in self.patterns:
            if p.slot_count > 0 and p.chan_count > 0 and p.le_count == 0:
                return p
        return None


@dataclass
class ImportTKBReport:
    """Output cuối cùng của TKBScanWorker, truyền qua queue."""
    patterns: ImportTKBPatterns
    duration_ms: int = 0
    completed: bool = False
    stopped: bool = False


def _slot_data_to_entry(s: SlotData) -> "SlotEntry | None":
    """Convert SlotData từ web → SlotEntry để insert vào TKBTemplate.

    Trả None khi slot không hợp lệ (lop/mon/pm rỗng hoặc "0"). Đây là
    cách bỏ qua "ô trống" mà không crash do `SlotEntry.__post_init__`
    raise ProfileSchemaError.
    """
    try:
        if not s.has_lop:
            return None
        # Phan_mon có thể rỗng cho 1 số môn (vd Sinh hoạt dưới cờ) — VnEdu
        # vẫn cho phép. SlotEntry hiện tại require phan_mon_id != "0" nên
        # các môn không có pm sẽ KHÔNG vào template. Đây là tradeoff: ưu
        # tiên tính nhất quán dataset profile thay vì 100% coverage.
        if (
            not s.lop_id or s.lop_id == "0"
            or not s.mon_id or s.mon_id == "0"
            or not s.phan_mon_id or s.phan_mon_id == "0"
        ):
            return None
        return SlotEntry(
            thu=int(s.thu),
            buoi=int(s.buoi_idx),
            tiet=int(s.tiet_idx),
            lop_id=str(s.lop_id),
            lop_text=str(s.lop_text or ""),
            mon_id=str(s.mon_id),
            mon_text=str(s.mon_text or ""),
            phan_mon_id=str(s.phan_mon_id),
            phan_mon_text=str(s.phan_mon_text or ""),
        )
    except (ProfileSchemaError, Exception):
        return None


def _build_pattern_fingerprint(slots: list[SlotEntry]) -> tuple:
    """Tuple bất biến đại diện 1 TKB pattern — dùng để group theo identity.

    QUAN TRỌNG: chỉ dùng (thu, buoi, tiet, lop_id, mon_id, phan_mon_id),
    KHÔNG dùng *_text để tránh false-different khi text hơi khác nhau
    (vd có space thừa) trên cùng dữ liệu.
    """
    return tuple(sorted(
        (s.thu, s.buoi, s.tiet, s.lop_id, s.mon_id, s.phan_mon_id)
        for s in slots
    ))


def group_tkb_patterns(
    weeks_data: dict[int, "WeekData"],
    fetch_errors: list[tuple[int, str]] | None = None,
) -> ImportTKBPatterns:
    """Group các tuần đã fetch theo fingerprint TKB.

    Args:
        weeks_data: dict {tuan: WeekData} từ fetch_weeks_parallel.
        fetch_errors: danh sách (tuan, msg) các tuần fetch fail.

    Returns:
        ImportTKBPatterns đã sort: weeks tăng dần, patterns theo
        số tuần giảm dần (dominant pattern xuất hiện đầu).
    """
    if fetch_errors is None:
        fetch_errors = []

    weeks_with_data: list[int] = []
    weeks_empty: list[int] = []
    # fp -> (slots_template, [tuan])
    by_fp: dict[tuple, tuple[list[SlotEntry], list[int]]] = {}

    for tuan in sorted(weeks_data.keys()):
        wd = weeks_data[tuan]
        # Convert + filter các slot hợp lệ (skip slot rỗng / phan_mon=0)
        entries: list[SlotEntry] = []
        for s in wd.slots:
            e = _slot_data_to_entry(s)
            if e is not None:
                entries.append(e)
        if not entries:
            weeks_empty.append(tuan)
            continue
        weeks_with_data.append(tuan)
        fp = _build_pattern_fingerprint(entries)
        if fp in by_fp:
            by_fp[fp][1].append(tuan)
        else:
            by_fp[fp] = (entries, [tuan])

    # Sort patterns theo số tuần giảm dần — dominant first
    sorted_fps = sorted(
        by_fp.items(), key=lambda kv: (-len(kv[1][1]), kv[1][1][0]),
    )
    patterns: list[TKBPattern] = []
    for idx, (fp, (slots, weeks)) in enumerate(sorted_fps):
        # pattern_id: A, B, ..., Z, AA, AB, ... (đề phòng >26 patterns)
        pid = ""
        n = idx
        while True:
            pid = chr(ord('A') + (n % 26)) + pid
            n = n // 26 - 1
            if n < 0:
                break
        patterns.append(TKBPattern(
            pattern_id=pid,
            fingerprint=fp,
            weeks=sorted(weeks),
            slots=slots,
        ))

    return ImportTKBPatterns(
        patterns=patterns,
        weeks_with_data=weeks_with_data,
        weeks_empty=weeks_empty,
        fetch_errors=list(fetch_errors),
    )

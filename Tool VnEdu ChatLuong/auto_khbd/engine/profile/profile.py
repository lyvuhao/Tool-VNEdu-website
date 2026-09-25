"""KHDHProfile — toàn bộ hồ sơ và kiểm tra hợp lệ."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analyzer.hdtn import is_hdtn_chu_de_group
from ..analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_NGHI
from .models import (
    PPCTStartEntry,
    ProfileSchemaError,
    TKBHolidayRule,
    TKBScheduleEvent,
    TKBTemplate,
)


# =====================================================================
# KHDHProfile — toàn bộ hồ sơ
# =====================================================================

@dataclass
class KHDHProfile:
    """Toàn bộ hồ sơ KHDH — load từ Excel hoặc khởi tạo mới."""

    # === ThongTin ===
    ho_ten_gv: str
    nam_hoc: int                 # 2025 nghĩa là năm 2025-2026
    cap_hoc: int                 # 1=TH, 2=THCS, 3=THPT
    cap_hoc_text: str = ""       # "THCS"
    ma_truong: str = ""
    ngay_tao: str = ""           # ISO date
    ghi_chu: str = ""

    # === TKB ===
    tach_le_chan: bool = False
    template_chinh: TKBTemplate | None = None     # khi tach_le_chan=False
    template_le: TKBTemplate | None = None        # khi tach_le_chan=True
    template_chan: TKBTemplate | None = None      # khi tach_le_chan=True

    # === PPCT ===
    ppct_starts: list[PPCTStartEntry] = field(default_factory=list)

    # === Nghỉ / Dạy bù ===
    holiday_rules: list[TKBHolidayRule] = field(default_factory=list)
    schedule_events: list[TKBScheduleEvent] = field(default_factory=list)

    # === Dải tuần áp dụng ===
    tuan_from: int = 1
    tuan_to: int = 35

    # ----------------------------------------------------------
    # Validation
    # ----------------------------------------------------------

    def __post_init__(self):
        # ho_ten_gv
        if not self.ho_ten_gv or not self.ho_ten_gv.strip():
            raise ProfileSchemaError(
                "ThongTin.ho_ten_gv",
                "Họ tên giáo viên không được để trống.",
            )
        self.ho_ten_gv = self.ho_ten_gv.strip()

        # nam_hoc
        try:
            self.nam_hoc = int(self.nam_hoc)
        except (TypeError, ValueError):
            raise ProfileSchemaError(
                "ThongTin.nam_hoc",
                f"Năm học phải là số (vd 2025). Nhận: {self.nam_hoc!r}",
            )
        if not (2000 <= self.nam_hoc <= 2100):
            raise ProfileSchemaError(
                "ThongTin.nam_hoc",
                f"Năm học không hợp lệ: {self.nam_hoc}. Phải trong khoảng 2000-2100.",
            )

        # cap_hoc
        try:
            self.cap_hoc = int(self.cap_hoc)
        except (TypeError, ValueError):
            raise ProfileSchemaError(
                "ThongTin.cap_hoc",
                f"Cấp học phải là số (1=TH, 2=THCS, 3=THPT). Nhận: {self.cap_hoc!r}",
            )
        if self.cap_hoc not in (1, 2, 3):
            raise ProfileSchemaError(
                "ThongTin.cap_hoc",
                f"Cấp học phải là 1 (TH), 2 (THCS), hoặc 3 (THPT). Nhận: {self.cap_hoc}",
            )

        # tach_le_chan invariant
        if self.tach_le_chan:
            if self.template_chinh is not None:
                raise ProfileSchemaError(
                    "TKB.tach_le_chan",
                    "Đã tích 'tách lẻ/chẵn' nhưng vẫn có TKB chính. "
                    "Phải có TKB lẻ + TKB chẵn (không có TKB chính).",
                )
            if self.template_le is None or self.template_chan is None:
                raise ProfileSchemaError(
                    "TKB.tach_le_chan",
                    "Đã tích 'tách lẻ/chẵn' nhưng thiếu TKB lẻ hoặc TKB chẵn.",
                )
        else:
            if self.template_le is not None or self.template_chan is not None:
                raise ProfileSchemaError(
                    "TKB.tach_le_chan",
                    "Chưa tích 'tách lẻ/chẵn' nhưng có TKB lẻ/chẵn. "
                    "Phải có TKB chính (không có lẻ/chẵn).",
                )
            # Nếu chưa có template_chinh, tạo rỗng (giáo viên sẽ điền sau)
            if self.template_chinh is None:
                self.template_chinh = TKBTemplate(slots=[])

        # tuan_from / tuan_to
        try:
            self.tuan_from = int(self.tuan_from)
            self.tuan_to = int(self.tuan_to)
        except (TypeError, ValueError):
            raise ProfileSchemaError(
                "TuanRange",
                f"Tuần phải là số. Nhận: from={self.tuan_from!r}, to={self.tuan_to!r}",
            )
        if not (1 <= self.tuan_from <= 52):
            raise ProfileSchemaError(
                "TuanRange.tuan_from",
                f"Tuần bắt đầu phải từ 1 đến 52. Nhận: {self.tuan_from}",
            )
        if not (1 <= self.tuan_to <= 52):
            raise ProfileSchemaError(
                "TuanRange.tuan_to",
                f"Tuần kết thúc phải từ 1 đến 52. Nhận: {self.tuan_to}",
            )
        if self.tuan_from > self.tuan_to:
            raise ProfileSchemaError(
                "TuanRange",
                f"Tuần bắt đầu ({self.tuan_from}) phải <= Tuần kết thúc ({self.tuan_to}).",
            )

        # holiday_rules forward-compatible: file cũ không có sheet này → [].
        clean_rules: list[TKBHolidayRule] = []
        for idx, rule in enumerate(self.holiday_rules or [], 1):
            if isinstance(rule, TKBHolidayRule):
                clean_rules.append(rule)
                continue
            if isinstance(rule, dict):
                try:
                    clean_rules.append(TKBHolidayRule(**rule))
                    continue
                except ProfileSchemaError as e:
                    raise ProfileSchemaError(
                        f"NghiDayBu.row{idx}",
                        e.msg,
                    )
            raise ProfileSchemaError(
                f"NghiDayBu.row{idx}",
                "Quy tắc nghỉ/dạy bù không đúng cấu trúc.",
            )
        self.holiday_rules = clean_rules

        # schedule_events là model mới. Nếu hồ sơ cũ chỉ có holiday_rules,
        # chuyển được quy tắc nào thì đưa sang event để planner dùng chung.
        clean_events: list[TKBScheduleEvent] = []
        for idx, event in enumerate(self.schedule_events or [], 1):
            if isinstance(event, TKBScheduleEvent):
                clean_events.append(event)
                continue
            if isinstance(event, dict):
                try:
                    clean_events.append(TKBScheduleEvent(**event))
                    continue
                except ProfileSchemaError as e:
                    raise ProfileSchemaError(
                        f"NghiDayBu.event{idx}",
                        e.msg,
                    )
            raise ProfileSchemaError(
                f"NghiDayBu.event{idx}",
                "Sự kiện Nghỉ/Dạy bù không đúng cấu trúc.",
            )
        if clean_events:
            self.schedule_events = clean_events
        else:
            self.schedule_events = _legacy_holiday_rules_to_events(
                self, clean_rules, skip_invalid=True
            )

    # ----------------------------------------------------------
    # Public helpers
    # ----------------------------------------------------------

    @staticmethod
    def is_chan(tuan: int) -> bool:
        """Phân loại tuần chẵn/lẻ theo chữ số CUỐI của số tuần.

        - 0, 2, 4, 6, 8 → True (chẵn)
        - 1, 3, 5, 7, 9 → False (lẻ)

        Ví dụ:
            is_chan(10) → True (cuối là 0)
            is_chan(11) → False (cuối là 1)
            is_chan(22) → True
        """
        last_digit = abs(int(tuan)) % 10
        return last_digit % 2 == 0

    @staticmethod
    def is_le(tuan: int) -> bool:
        return not KHDHProfile.is_chan(tuan)

    def get_active_template(self, tuan: int) -> TKBTemplate:
        """Trả về template phù hợp cho tuần `tuan`.

        - tach_le_chan=False → luôn template_chinh
        - tach_le_chan=True  → template_le hoặc template_chan theo `is_chan`
        """
        if not self.tach_le_chan:
            assert self.template_chinh is not None
            return self.template_chinh
        if self.is_chan(tuan):
            assert self.template_chan is not None
            return self.template_chan
        assert self.template_le is not None
        return self.template_le

    def all_templates(self) -> list[TKBTemplate]:
        """Trả tất cả template không None — dùng cho validation chung."""
        out = []
        for t in (self.template_chinh, self.template_le, self.template_chan):
            if t is not None:
                out.append(t)
        return out

    def auto_compute_hdtn_chu_de_starts(
        self, tuan_from: int | None = None
    ) -> list[dict]:
        """Tính PPCT bắt đầu cho group HĐTN-Chủ-đề dựa trên tuan_from + tiết/tuần.

        Logic:
          - Cho mỗi group có (mon HĐTN + phân môn 'theo chủ đề'):
            • Đếm số tiết/tuần lẻ + tiết/tuần chẵn từ template
            • Số tiết PPCT đã dạy bởi GV trước (1..tuan_from-1):
                = sum_le_weeks * tiet_per_le_week + sum_chan_weeks * tiet_per_chan_week
              trong đó sum_le_weeks = số tuần lẻ trong [1..tuan_from-1]
                       sum_chan_weeks = số tuần chẵn tương tự
            • PPCT_start cho user (bắt đầu tuần tuan_from) = total + 1

        Args:
            tuan_from: tuần user bắt đầu dạy. Mặc định self.tuan_from.

        Returns:
            list[dict] — mỗi item:
              {group_key, lop_id, lop_text, mon_id, mon_text,
               phan_mon_id, phan_mon_text, tiet_le, tiet_chan,
               weeks_before_le, weeks_before_chan, total_before, suggested_ppct,
               current_ppct}
            Empty list nếu không tìm group nào.
        """
        if tuan_from is None:
            tuan_from = self.tuan_from
        if tuan_from is None or tuan_from <= 1:
            # Tuần 1 → GV trước không dạy gì → suggest 1 cho mọi group
            tuan_from = max(1, tuan_from or 1)

        # Tính tiết/tuần cho mỗi group ở template lẻ + chẵn
        # group_key → (tiet_le, tiet_chan, lop_text, mon_text, pm_text)
        per_group: dict[str, dict] = {}

        def _count_in_template(tmpl: TKBTemplate, slot_kind: str) -> None:
            """slot_kind: 'le', 'chan', or 'chinh'."""
            if tmpl is None:
                return
            for slot in tmpl.slots:
                if not is_hdtn_chu_de_group(slot.mon_text, slot.phan_mon_text):
                    continue
                gk = slot.group_key
                if gk not in per_group:
                    per_group[gk] = {
                        "group_key": gk,
                        "lop_id": slot.lop_id,
                        "lop_text": slot.lop_text,
                        "mon_id": slot.mon_id,
                        "mon_text": slot.mon_text,
                        "phan_mon_id": slot.phan_mon_id,
                        "phan_mon_text": slot.phan_mon_text,
                        "tiet_le": 0,
                        "tiet_chan": 0,
                    }
                if slot_kind == "le":
                    per_group[gk]["tiet_le"] += 1
                elif slot_kind == "chan":
                    per_group[gk]["tiet_chan"] += 1
                else:
                    # tach_le_chan=False → tiết áp cho cả lẻ và chẵn
                    per_group[gk]["tiet_le"] += 1
                    per_group[gk]["tiet_chan"] += 1

        if self.tach_le_chan:
            _count_in_template(self.template_le, "le")
            _count_in_template(self.template_chan, "chan")
        else:
            _count_in_template(self.template_chinh, "chinh")

        if not per_group:
            return []

        # Đếm số tuần lẻ + chẵn trong [1..tuan_from-1]
        weeks_before = list(range(1, tuan_from))
        weeks_le = sum(1 for t in weeks_before if not self.is_chan(t))
        weeks_chan = sum(1 for t in weeks_before if self.is_chan(t))

        # Build map current_ppct cho mỗi group (đọc từ ppct_starts hiện tại)
        cur_map: dict[str, int] = {
            e.group_key: e.ppct_start for e in self.ppct_starts
        }

        # Build kết quả
        results: list[dict] = []
        for gk, info in per_group.items():
            total_before = (weeks_le * info["tiet_le"]
                          + weeks_chan * info["tiet_chan"])
            suggested = total_before + 1
            results.append({
                **info,
                "weeks_before_le": weeks_le,
                "weeks_before_chan": weeks_chan,
                "total_before": total_before,
                "suggested_ppct": suggested,
                "current_ppct": cur_map.get(gk, 0),
            })
        # Sort theo (lop_text, phan_mon_text) cho UI
        results.sort(key=lambda x: (x["lop_text"], x["phan_mon_text"]))
        return results

    def all_groups_across_templates(self) -> list[dict[str, str]]:
        """Tổng hợp groups xuất hiện trong các template. Mỗi group hiện 1 lần."""
        seen: set[tuple[str, str, str]] = set()
        out: list[dict[str, str]] = []
        for tpl in self.all_templates():
            for g in tpl.all_groups_with_text():
                key = (g["lop_id"], g["mon_id"], g["phan_mon_id"])
                if key not in seen:
                    seen.add(key)
                    out.append(g)
        return out

    def sync_ppct_starts_with_templates(self) -> None:
        """Đảm bảo `ppct_starts` khớp với groups thực tế trong template:

        - Group có trong template nhưng chưa có entry → thêm với ppct_start=1
        - Group có entry nhưng không còn trong template → xóa
        - Giữ nguyên thứ tự + giá trị các entry còn hợp lệ
        """
        current_groups = self.all_groups_across_templates()
        current_keys = {(g["lop_id"], g["mon_id"], g["phan_mon_id"])
                       for g in current_groups}

        # 1) Bỏ entry không còn group
        self.ppct_starts = [
            e for e in self.ppct_starts
            if (e.lop_id, e.mon_id, e.phan_mon_id) in current_keys
        ]

        # 2) Thêm entry mới với ppct_start=1
        existing_keys = {(e.lop_id, e.mon_id, e.phan_mon_id) for e in self.ppct_starts}
        for g in current_groups:
            key = (g["lop_id"], g["mon_id"], g["phan_mon_id"])
            if key not in existing_keys:
                self.ppct_starts.append(PPCTStartEntry(
                    lop_id=g["lop_id"], lop_text=g["lop_text"],
                    mon_id=g["mon_id"], mon_text=g["mon_text"],
                    phan_mon_id=g["phan_mon_id"], phan_mon_text=g["phan_mon_text"],
                    ppct_start=1,
                ))

    def get_ppct_start(self, lop_id: str, mon_id: str, phan_mon_id: str) -> int:
        """Trả PPCT bắt đầu cho 1 group. Mặc định 1 nếu chưa cấu hình."""
        for e in self.ppct_starts:
            if (e.lop_id == lop_id and e.mon_id == mon_id
                    and e.phan_mon_id == phan_mon_id):
                return e.ppct_start
        return 1

    def templates_equal(self) -> bool:
        """True khi tach_le_chan=True và 2 template lẻ/chẵn giống y hệt nhau.

        Dùng cho cảnh báo Requirement 2.3.
        """
        if not self.tach_le_chan:
            return False
        return self.template_le == self.template_chan

    def toggle_tach_le_chan(self, on: bool, prefer: str = "le") -> None:
        """Bật/tắt chế độ tách lẻ/chẵn.

        Args:
            on: True để bật, False để tắt
            prefer: khi tắt — giữ "le" hay "chan" làm template_chinh

        Raises:
            ValueError: prefer không phải "le"/"chan"
        """
        if on and not self.tach_le_chan:
            # Bật: clone template_chinh thành 2 mẫu
            base = self.template_chinh.clone() if self.template_chinh else TKBTemplate()
            self.template_chan = base.clone()    # Mẫu gốc → chẵn
            self.template_le = base.clone()      # Bản clone → lẻ
            self.template_chinh = None
            self.tach_le_chan = True
        elif not on and self.tach_le_chan:
            if prefer not in ("le", "chan"):
                raise ValueError(f"prefer phải là 'le' hoặc 'chan'. Nhận: {prefer!r}")
            kept = self.template_le if prefer == "le" else self.template_chan
            self.template_chinh = kept.clone() if kept else TKBTemplate()
            self.template_le = None
            self.template_chan = None
            self.tach_le_chan = False

    @classmethod
    def empty(
        cls,
        ho_ten_gv: str,
        nam_hoc: int,
        cap_hoc: int,
        cap_hoc_text: str = "",
        ma_truong: str = "",
    ) -> "KHDHProfile":
        """Khởi tạo hồ sơ rỗng — TKB chính chưa có slot, ppct_starts rỗng."""
        return cls(
            ho_ten_gv=ho_ten_gv,
            nam_hoc=nam_hoc,
            cap_hoc=cap_hoc,
            cap_hoc_text=cap_hoc_text,
            ma_truong=ma_truong,
            ngay_tao="",
            ghi_chu="",
            tach_le_chan=False,
            template_chinh=TKBTemplate(slots=[]),
        )


def _legacy_holiday_rules_to_events(
    profile: KHDHProfile,
    rules: list[TKBHolidayRule],
    *,
    skip_invalid: bool,
) -> list[TKBScheduleEvent]:
    """Chuyển quy tắc cũ sang event mới, giữ file Excel cũ còn dùng được."""
    events: list[TKBScheduleEvent] = []
    for rule in rules or []:
        if not getattr(rule, "enabled", True):
            continue
        try:
            source_template = profile.get_active_template(rule.holiday_tuan)
            source_slot = source_template.slot_at(
                rule.holiday_thu, rule.holiday_buoi, rule.holiday_tiet
            )
            if source_slot is None:
                raise ProfileSchemaError(
                    "NghiDayBu.legacy",
                    "Quy tắc cũ không khớp ô TKB nguồn.",
                )
            events.append(TKBScheduleEvent.from_slot(
                kind=TKB_EVENT_NGHI,
                source_slot=source_slot,
                tuan=rule.holiday_tuan,
                thu=rule.holiday_thu,
                buoi=rule.holiday_buoi,
                tiet=rule.holiday_tiet,
                note=rule.note or "Nghỉ",
                enabled=rule.enabled,
            ))
            if rule.has_makeup:
                events.append(TKBScheduleEvent.from_slot(
                    kind=TKB_EVENT_DAY_BU,
                    source_slot=source_slot,
                    tuan=rule.makeup_tuan,
                    thu=rule.makeup_thu,
                    buoi=rule.makeup_buoi,
                    tiet=rule.makeup_tiet,
                    note=rule.note or "Dạy bù",
                    enabled=rule.enabled,
                    replace_normal_slot=True,
                ))
        except ProfileSchemaError:
            if skip_invalid:
                continue
            raise
    return events


def profile_schedule_events(profile: KHDHProfile) -> list[TKBScheduleEvent]:
    """Trả danh sách sự kiện canonical, có adapter cho hồ sơ đời cũ."""
    events = getattr(profile, "schedule_events", None) or []
    if events:
        return [
            e.clone() if isinstance(e, TKBScheduleEvent) else TKBScheduleEvent(**e)
            for e in events
        ]
    return _legacy_holiday_rules_to_events(
        profile,
        getattr(profile, "holiday_rules", []) or [],
        skip_invalid=True,
    )


def count_profile_holiday_rules(
    profile: KHDHProfile, tuan_from: int | None = None, tuan_to: int | None = None
) -> tuple[int, int]:
    """Đếm số tiết Nghỉ và Dạy bù đang bật trong dải tuần."""
    tuan_from = profile.tuan_from if tuan_from is None else int(tuan_from)
    tuan_to = profile.tuan_to if tuan_to is None else int(tuan_to)
    n_nghi = 0
    n_bu = 0
    for event in profile_schedule_events(profile):
        if not event.enabled:
            continue
        if not (tuan_from <= event.tuan <= tuan_to):
            continue
        if event.kind == TKB_EVENT_NGHI:
            n_nghi += 1
        elif event.kind == TKB_EVENT_DAY_BU:
            n_bu += 1
    return n_nghi, n_bu


def validate_profile_schedule_events(
    profile: KHDHProfile, tuan_from: int | None = None, tuan_to: int | None = None
) -> list[tuple[str, str]]:
    """Rà soát sự kiện Nghỉ/Dạy bù trước khi chạy web."""
    tuan_from = profile.tuan_from if tuan_from is None else int(tuan_from)
    tuan_to = profile.tuan_to if tuan_to is None else int(tuan_to)
    warnings: list[tuple[str, str]] = []
    seen_targets: dict[tuple[int, int, int, int], TKBScheduleEvent] = {}

    for event in profile_schedule_events(profile):
        if not event.enabled:
            continue
        pos = event.position_tuple()
        if pos in seen_targets:
            old = seen_targets[pos]
            warnings.append((
                "error",
                f"Trùng vị trí tuần {event.tuan}, thứ {event.thu}, "
                f"buổi {event.buoi}, tiết {event.tiet}: "
                f"{old.label} và {event.label}. Mỗi ô chỉ được có một trạng thái.",
            ))
        seen_targets[pos] = event

        try:
            target_template = profile.get_active_template(event.tuan)
            target_slot = target_template.slot_at(event.thu, event.buoi, event.tiet)
        except Exception:
            target_slot = None

        if event.kind == TKB_EVENT_NGHI:
            if target_slot is None:
                warnings.append((
                    "error",
                    f"Tiết Nghỉ tuần {event.tuan}, thứ {event.thu}, "
                    f"buổi {event.buoi}, tiết {event.tiet} không khớp ô TKB nào.",
                ))
            elif not event.source_matches_slot(target_slot):
                warnings.append((
                    "error",
                    f"Tiết Nghỉ tuần {event.tuan}, thứ {event.thu}, "
                    f"buổi {event.buoi}, tiết {event.tiet} đang không còn đúng "
                    f"{event.source_lop_text} / {event.source_phan_mon_text or event.source_mon_text}.",
                ))
        elif event.kind == TKB_EVENT_DAY_BU:
            if target_slot is not None and not event.replace_normal_slot:
                warnings.append((
                    "error",
                    f"Tiết Dạy bù tuần {event.tuan}, thứ {event.thu}, "
                    f"buổi {event.buoi}, tiết {event.tiet} đang trùng một tiết TKB thường. "
                    "Hãy bật lựa chọn dùng ô này cho Dạy bù hoặc chọn ô trống.",
                ))

        if not (tuan_from <= event.tuan <= tuan_to):
            warnings.append((
                "warn",
                f"{event.label} tuần {event.tuan}, thứ {event.thu}, "
                f"buổi {event.buoi}, tiết {event.tiet} nằm ngoài dải tuần đang chạy.",
            ))
    return warnings


def validate_profile_holiday_rules(
    profile: KHDHProfile, tuan_from: int | None = None, tuan_to: int | None = None
) -> list[tuple[str, str]]:
    """Tên cũ giữ tương thích; logic mới dùng `TKBScheduleEvent`."""
    warnings = validate_profile_schedule_events(profile, tuan_from, tuan_to)
    if not getattr(profile, "schedule_events", None):
        for rule in getattr(profile, "holiday_rules", []) or []:
            if not getattr(rule, "enabled", True):
                continue
            try:
                template = profile.get_active_template(rule.holiday_tuan)
                source_slot = template.slot_at(
                    rule.holiday_thu, rule.holiday_buoi, rule.holiday_tiet
                )
            except Exception:
                source_slot = None
            if source_slot is None:
                warnings.append((
                    "error",
                    f"Quy tắc cũ tuần {rule.holiday_tuan}, thứ {rule.holiday_thu}, "
                    f"buổi {rule.holiday_buoi}, tiết {rule.holiday_tiet} không khớp ô TKB nào.",
                ))
    return warnings

"""Các thành phần của hồ sơ KHDH: tiết TKB, quy tắc nghỉ/dạy bù, mẫu TKB, PPCT."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_LABELS, TKB_EVENT_NGHI


# ######################################################################
# Section: profile
# ######################################################################



# =====================================================================
# Constants
# =====================================================================

# Thứ trong tuần: 2..7 + 8 (CN). Khớp convention của VnEdu.
ALLOWED_THU = (2, 3, 4, 5, 6, 7, 8)


ALLOWED_BUOI = (1, 2)        # 1=Sáng, 2=Chiều


ALLOWED_TIET = (1, 2, 3, 4, 5)


# =====================================================================
# Exception
# =====================================================================

class ProfileSchemaError(Exception):
    """Hồ sơ Excel/dữ liệu sai cấu trúc.

    Attributes:
        field: tên field/cell/sheet bị lỗi (vd "TKB lẻ.row3.Lớp")
        msg:   mô tả lỗi tiếng Việt cho giáo viên đọc
    """

    def __init__(self, field: str, msg: str):
        self.field = field
        self.msg = msg
        super().__init__(f"[{field}] {msg}")


# =====================================================================
# SlotEntry — 1 tiết trong TKB
# =====================================================================

@dataclass
class SlotEntry:
    """1 tiết được giáo viên dạy trong tuần (mẫu TKB).

    Identity = (thu, buoi, tiet). Slot ở cùng vị trí trong 2 mẫu lẻ/chẵn
    là 2 SlotEntry độc lập (không share).
    """
    thu: int                 # 2..8 (8 = CN)
    buoi: int                # 1=Sáng, 2=Chiều
    tiet: int                # 1..5
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str

    def __post_init__(self):
        if self.thu not in ALLOWED_THU:
            raise ProfileSchemaError(
                "SlotEntry.thu",
                f"Thứ phải là 2-7 hoặc 8 (CN). Nhận: {self.thu!r}",
            )
        if self.buoi not in ALLOWED_BUOI:
            raise ProfileSchemaError(
                "SlotEntry.buoi",
                f"Buổi phải là 1 (Sáng) hoặc 2 (Chiều). Nhận: {self.buoi!r}",
            )
        if self.tiet not in ALLOWED_TIET:
            raise ProfileSchemaError(
                "SlotEntry.tiet",
                f"Tiết phải từ 1 đến 5. Nhận: {self.tiet!r}",
            )
        # Validate ID không rỗng / không là "0"
        for fname in ("lop_id", "mon_id", "phan_mon_id"):
            v = getattr(self, fname)
            if not v or str(v).strip() == "" or str(v).strip() == "0":
                raise ProfileSchemaError(
                    f"SlotEntry.{fname}",
                    f"Trường '{fname}' không được để trống hoặc bằng 0. "
                    f"Slot Thứ {self.thu} - Buổi {self.buoi} - Tiết {self.tiet}.",
                )
        # Strip text fields
        self.lop_text = (self.lop_text or "").strip()
        self.mon_text = (self.mon_text or "").strip()
        self.phan_mon_text = (self.phan_mon_text or "").strip()

    @property
    def slot_key(self) -> str:
        """Key duy nhất theo vị trí — `thu_buoi_tiet`."""
        return f"{self.thu}_{self.buoi}_{self.tiet}"

    @property
    def buoi_text(self) -> str:
        return "Sáng" if self.buoi == 1 else "Chiều"

    @property
    def thu_text(self) -> str:
        return "CN" if self.thu == 8 else f"Thứ {self.thu}"

    @property
    def group_key(self) -> str:
        """Key nhóm (lop, mon, phan_mon) — dùng để cuốn chiếu PPCT."""
        return f"{self.lop_id}|{self.mon_id}|{self.phan_mon_id}"


# =====================================================================
# TKBHolidayRule — quy tắc Nghỉ / Dạy bù cho hồ sơ TKB
# =====================================================================

@dataclass
class TKBHolidayRule:
    """1 quy tắc đánh dấu một tiết Nghỉ và tùy chọn một tiết Dạy bù."""
    holiday_tuan: int
    holiday_thu: int
    holiday_buoi: int
    holiday_tiet: int
    makeup_tuan: int = 0
    makeup_thu: int = 0
    makeup_buoi: int = 0
    makeup_tiet: int = 0
    note: str = ""
    enabled: bool = True

    def __post_init__(self):
        for field_name in ("holiday_tuan", "holiday_thu", "holiday_buoi",
                           "holiday_tiet", "makeup_tuan", "makeup_thu",
                           "makeup_buoi", "makeup_tiet"):
            value = getattr(self, field_name)
            try:
                setattr(self, field_name, int(value or 0))
            except (TypeError, ValueError):
                raise ProfileSchemaError(
                    f"TKBHolidayRule.{field_name}",
                    f"Giá trị phải là số nguyên. Nhận: {value!r}",
                )

        if not (1 <= self.holiday_tuan <= 52):
            raise ProfileSchemaError(
                "TKBHolidayRule.holiday_tuan",
                f"Tuần nghỉ phải từ 1 đến 52. Nhận: {self.holiday_tuan}",
            )
        if self.holiday_thu not in ALLOWED_THU:
            raise ProfileSchemaError(
                "TKBHolidayRule.holiday_thu",
                f"Thứ nghỉ phải là 2-7 hoặc 8 (CN). Nhận: {self.holiday_thu}",
            )
        if self.holiday_buoi not in ALLOWED_BUOI:
            raise ProfileSchemaError(
                "TKBHolidayRule.holiday_buoi",
                f"Buổi nghỉ phải là 1 (Sáng) hoặc 2 (Chiều). Nhận: {self.holiday_buoi}",
            )
        if self.holiday_tiet not in ALLOWED_TIET:
            raise ProfileSchemaError(
                "TKBHolidayRule.holiday_tiet",
                f"Tiết nghỉ phải từ 1 đến 5. Nhận: {self.holiday_tiet}",
            )

        has_any_makeup = any((
            self.makeup_tuan, self.makeup_thu,
            self.makeup_buoi, self.makeup_tiet,
        ))
        if has_any_makeup and not self.has_makeup:
            raise ProfileSchemaError(
                "TKBHolidayRule.makeup",
                "Nếu có dạy bù thì phải nhập đủ tuần, thứ, buổi và tiết bù.",
            )
        if self.has_makeup:
            if not (1 <= self.makeup_tuan <= 52):
                raise ProfileSchemaError(
                    "TKBHolidayRule.makeup_tuan",
                    f"Tuần bù phải từ 1 đến 52. Nhận: {self.makeup_tuan}",
                )
            if self.makeup_thu not in ALLOWED_THU:
                raise ProfileSchemaError(
                    "TKBHolidayRule.makeup_thu",
                    f"Thứ bù phải là 2-7 hoặc 8 (CN). Nhận: {self.makeup_thu}",
                )
            if self.makeup_buoi not in ALLOWED_BUOI:
                raise ProfileSchemaError(
                    "TKBHolidayRule.makeup_buoi",
                    f"Buổi bù phải là 1 (Sáng) hoặc 2 (Chiều). Nhận: {self.makeup_buoi}",
                )
            if self.makeup_tiet not in ALLOWED_TIET:
                raise ProfileSchemaError(
                    "TKBHolidayRule.makeup_tiet",
                    f"Tiết bù phải từ 1 đến 5. Nhận: {self.makeup_tiet}",
                )
        self.note = (self.note or "").strip()
        self.enabled = bool(self.enabled)

    @property
    def holiday_slot_key(self) -> str:
        return f"{self.holiday_thu}_{self.holiday_buoi}_{self.holiday_tiet}"

    @property
    def makeup_slot_key(self) -> str:
        if not self.has_makeup:
            return ""
        return f"{self.makeup_thu}_{self.makeup_buoi}_{self.makeup_tiet}"

    @property
    def has_makeup(self) -> bool:
        return all((
            self.makeup_tuan > 0,
            self.makeup_thu > 0,
            self.makeup_buoi > 0,
            self.makeup_tiet > 0,
        ))

    def clone(self) -> "TKBHolidayRule":
        return TKBHolidayRule(
            holiday_tuan=self.holiday_tuan,
            holiday_thu=self.holiday_thu,
            holiday_buoi=self.holiday_buoi,
            holiday_tiet=self.holiday_tiet,
            makeup_tuan=self.makeup_tuan,
            makeup_thu=self.makeup_thu,
            makeup_buoi=self.makeup_buoi,
            makeup_tiet=self.makeup_tiet,
            note=self.note,
            enabled=self.enabled,
        )

    def holiday_position_tuple(self) -> tuple[int, int, int, int]:
        return (
            self.holiday_tuan, self.holiday_thu,
            self.holiday_buoi, self.holiday_tiet,
        )

    def makeup_position_tuple(self) -> tuple[int, int, int, int] | None:
        if not self.has_makeup:
            return None
        return (
            self.makeup_tuan, self.makeup_thu,
            self.makeup_buoi, self.makeup_tiet,
        )


# =====================================================================
# TKBScheduleEvent — sự kiện Nghỉ / Dạy bù độc lập
# =====================================================================

@dataclass
class TKBScheduleEvent:
    """Một lần áp dụng Nghỉ hoặc Dạy bù tại đúng tuần/thứ/buổi/tiết.

    Khác `TKBHolidayRule` cũ, mỗi event đứng độc lập. Nhờ vậy một tiết
    Nghỉ không tự ép phải có một tiết Dạy bù kèm theo, và một môn có thể
    có nhiều tiết Dạy bù ở các tuần sau để bù đủ PPCT.
    """
    kind: str
    tuan: int
    thu: int
    buoi: int
    tiet: int
    source_lop_id: str
    source_lop_text: str
    source_mon_id: str
    source_mon_text: str
    source_phan_mon_id: str
    source_phan_mon_text: str
    source_slot_key: str = ""
    note: str = ""
    enabled: bool = True
    replace_normal_slot: bool = False

    def __post_init__(self):
        self.kind = str(self.kind or "").strip().lower()
        if self.kind not in (TKB_EVENT_NGHI, TKB_EVENT_DAY_BU):
            raise ProfileSchemaError(
                "TKBScheduleEvent.kind",
                "Loại sự kiện phải là 'Nghỉ' hoặc 'Dạy bù'.",
            )
        for field_name in ("tuan", "thu", "buoi", "tiet"):
            value = getattr(self, field_name)
            try:
                setattr(self, field_name, int(value or 0))
            except (TypeError, ValueError):
                raise ProfileSchemaError(
                    f"TKBScheduleEvent.{field_name}",
                    f"Giá trị phải là số nguyên. Nhận: {value!r}",
                )
        if not (1 <= self.tuan <= 52):
            raise ProfileSchemaError(
                "TKBScheduleEvent.tuan",
                f"Tuần phải từ 1 đến 52. Nhận: {self.tuan}",
            )
        if self.thu not in ALLOWED_THU:
            raise ProfileSchemaError(
                "TKBScheduleEvent.thu",
                f"Thứ phải là 2-7 hoặc 8 (CN). Nhận: {self.thu}",
            )
        if self.buoi not in ALLOWED_BUOI:
            raise ProfileSchemaError(
                "TKBScheduleEvent.buoi",
                f"Buổi phải là 1 (Sáng) hoặc 2 (Chiều). Nhận: {self.buoi}",
            )
        if self.tiet not in ALLOWED_TIET:
            raise ProfileSchemaError(
                "TKBScheduleEvent.tiet",
                f"Tiết phải từ 1 đến 5. Nhận: {self.tiet}",
            )
        for fname in ("source_lop_id", "source_mon_id", "source_phan_mon_id"):
            value = str(getattr(self, fname) or "").strip()
            if not value or value == "0":
                raise ProfileSchemaError(
                    f"TKBScheduleEvent.{fname}",
                    "Thông tin lớp/môn/phân môn nguồn không được để trống.",
                )
            setattr(self, fname, value)
        self.source_lop_text = str(self.source_lop_text or "").strip()
        self.source_mon_text = str(self.source_mon_text or "").strip()
        self.source_phan_mon_text = str(self.source_phan_mon_text or "").strip()
        self.source_slot_key = str(self.source_slot_key or "").strip()
        self.note = str(self.note or "").strip()
        self.enabled = bool(self.enabled)
        self.replace_normal_slot = bool(self.replace_normal_slot)

    @property
    def row_key(self) -> str:
        return f"{self.thu}_{self.buoi}_{self.tiet}"

    @property
    def group_key(self) -> str:
        return (
            f"{self.source_lop_id}|{self.source_mon_id}|"
            f"{self.source_phan_mon_id}"
        )

    @property
    def label(self) -> str:
        return TKB_EVENT_LABELS.get(self.kind, self.kind)

    def position_tuple(self) -> tuple[int, int, int, int]:
        return self.tuan, self.thu, self.buoi, self.tiet

    def source_matches_slot(self, slot: "SlotEntry | None") -> bool:
        if slot is None:
            return False
        return (
            slot.lop_id == self.source_lop_id
            and slot.mon_id == self.source_mon_id
            and slot.phan_mon_id == self.source_phan_mon_id
        )

    def to_slot_at_target(self) -> "SlotEntry":
        return SlotEntry(
            thu=self.thu,
            buoi=self.buoi,
            tiet=self.tiet,
            lop_id=self.source_lop_id,
            lop_text=self.source_lop_text,
            mon_id=self.source_mon_id,
            mon_text=self.source_mon_text,
            phan_mon_id=self.source_phan_mon_id,
            phan_mon_text=self.source_phan_mon_text,
        )

    def clone(self) -> "TKBScheduleEvent":
        return TKBScheduleEvent(
            kind=self.kind,
            tuan=self.tuan,
            thu=self.thu,
            buoi=self.buoi,
            tiet=self.tiet,
            source_lop_id=self.source_lop_id,
            source_lop_text=self.source_lop_text,
            source_mon_id=self.source_mon_id,
            source_mon_text=self.source_mon_text,
            source_phan_mon_id=self.source_phan_mon_id,
            source_phan_mon_text=self.source_phan_mon_text,
            source_slot_key=self.source_slot_key,
            note=self.note,
            enabled=self.enabled,
            replace_normal_slot=self.replace_normal_slot,
        )

    @classmethod
    def from_slot(
        cls,
        *,
        kind: str,
        source_slot: "SlotEntry",
        tuan: int,
        thu: int,
        buoi: int,
        tiet: int,
        note: str = "",
        enabled: bool = True,
        replace_normal_slot: bool = False,
    ) -> "TKBScheduleEvent":
        return cls(
            kind=kind,
            tuan=tuan,
            thu=thu,
            buoi=buoi,
            tiet=tiet,
            source_lop_id=source_slot.lop_id,
            source_lop_text=source_slot.lop_text,
            source_mon_id=source_slot.mon_id,
            source_mon_text=source_slot.mon_text,
            source_phan_mon_id=source_slot.phan_mon_id,
            source_phan_mon_text=source_slot.phan_mon_text,
            source_slot_key=source_slot.slot_key,
            note=note,
            enabled=enabled,
            replace_normal_slot=replace_normal_slot,
        )


# =====================================================================
# TKBTemplate — 1 mẫu TKB (chính / lẻ / chẵn)
# =====================================================================

@dataclass
class TKBTemplate:
    """1 mẫu TKB: tập hợp các SlotEntry."""
    slots: list[SlotEntry] = field(default_factory=list)

    def slot_at(self, thu: int, buoi: int, tiet: int) -> SlotEntry | None:
        for s in self.slots:
            if s.thu == thu and s.buoi == buoi and s.tiet == tiet:
                return s
        return None

    def all_groups(self) -> list[tuple[str, str, str]]:
        """Trả các (lop_id, mon_id, phan_mon_id) duy nhất, ổn định thứ tự."""
        seen: set[tuple[str, str, str]] = set()
        out: list[tuple[str, str, str]] = []
        for s in self.slots:
            key = (s.lop_id, s.mon_id, s.phan_mon_id)
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def all_groups_with_text(self) -> list[dict[str, str]]:
        """Trả groups kèm text label, ổn định thứ tự xuất hiện."""
        seen: set[tuple[str, str, str]] = set()
        out: list[dict[str, str]] = []
        for s in self.slots:
            key = (s.lop_id, s.mon_id, s.phan_mon_id)
            if key not in seen:
                seen.add(key)
                out.append({
                    "lop_id": s.lop_id, "lop_text": s.lop_text,
                    "mon_id": s.mon_id, "mon_text": s.mon_text,
                    "phan_mon_id": s.phan_mon_id, "phan_mon_text": s.phan_mon_text,
                })
        return out

    def slot_count(self) -> int:
        return len(self.slots)

    def slot_count_by_group(self) -> dict[str, int]:
        """Số slot mỗi group_key — dùng cho cuốn chiếu PPCT giữa 2 mẫu."""
        out: dict[str, int] = {}
        for s in self.slots:
            out[s.group_key] = out.get(s.group_key, 0) + 1
        return out

    def upsert_slot(self, slot: SlotEntry) -> None:
        """Insert hoặc replace slot tại (thu, buoi, tiet)."""
        for i, existing in enumerate(self.slots):
            if (existing.thu == slot.thu and existing.buoi == slot.buoi
                    and existing.tiet == slot.tiet):
                self.slots[i] = slot
                return
        self.slots.append(slot)

    def delete_slot(self, thu: int, buoi: int, tiet: int) -> bool:
        """Xóa slot tại vị trí. Trả True nếu có xóa."""
        for i, s in enumerate(self.slots):
            if s.thu == thu and s.buoi == buoi and s.tiet == tiet:
                del self.slots[i]
                return True
        return False

    def clone(self) -> "TKBTemplate":
        """Deep copy template."""
        new_slots = [
            SlotEntry(
                thu=s.thu, buoi=s.buoi, tiet=s.tiet,
                lop_id=s.lop_id, lop_text=s.lop_text,
                mon_id=s.mon_id, mon_text=s.mon_text,
                phan_mon_id=s.phan_mon_id, phan_mon_text=s.phan_mon_text,
            )
            for s in self.slots
        ]
        return TKBTemplate(slots=new_slots)

    def __eq__(self, other: object) -> bool:
        """So sánh 2 mẫu — dùng để cảnh báo Requirement 2.3.

        2 mẫu equal khi tập slot identity giống nhau VÀ data slot giống nhau.
        Bỏ qua thứ tự `slots` list.
        """
        if not isinstance(other, TKBTemplate):
            return NotImplemented
        return self._fingerprint() == other._fingerprint()

    def __hash__(self):
        return hash(self._fingerprint())

    def _fingerprint(self) -> frozenset[tuple]:
        """Tuple bất biến đại diện slot → dùng cho hash/eq."""
        return frozenset(
            (s.thu, s.buoi, s.tiet, s.lop_id, s.mon_id, s.phan_mon_id)
            for s in self.slots
        )

    def has_slot_at(self, thu: int, buoi: int, tiet: int) -> bool:
        return self.slot_at(thu, buoi, tiet) is not None


# =====================================================================
# PPCTStartEntry — PPCT bắt đầu cho 1 nhóm (lớp × phân môn)
# =====================================================================

@dataclass
class PPCTStartEntry:
    """PPCT bắt đầu của 1 nhóm trong tuần `tuan_from`.

    PPCT của tuần kế = ppct_start + tổng số slot của nhóm đó từ tuần đầu
    đến trước tuần kế.
    """
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str
    ppct_start: int = 1
    ghi_chu: str = ""

    def __post_init__(self):
        for fname in ("lop_id", "mon_id", "phan_mon_id"):
            v = getattr(self, fname)
            if not v or str(v).strip() == "" or str(v).strip() == "0":
                raise ProfileSchemaError(
                    f"PPCTStartEntry.{fname}",
                    f"Trường '{fname}' không được để trống hoặc 0.",
                )
        # Coerce int + validate ≥ 1
        try:
            self.ppct_start = int(self.ppct_start)
        except (TypeError, ValueError):
            raise ProfileSchemaError(
                "PPCTStartEntry.ppct_start",
                f"PPCT bắt đầu phải là số nguyên. Nhận: {self.ppct_start!r}",
            )
        if self.ppct_start < 1:
            raise ProfileSchemaError(
                "PPCTStartEntry.ppct_start",
                f"PPCT bắt đầu phải >= 1. Nhận: {self.ppct_start}",
            )
        self.lop_text = (self.lop_text or "").strip()
        self.mon_text = (self.mon_text or "").strip()
        self.phan_mon_text = (self.phan_mon_text or "").strip()
        self.ghi_chu = (self.ghi_chu or "").strip()

    @property
    def group_key(self) -> str:
        return f"{self.lop_id}|{self.mon_id}|{self.phan_mon_id}"

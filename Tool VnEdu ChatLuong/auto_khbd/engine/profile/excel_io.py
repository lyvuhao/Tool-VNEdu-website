"""Đọc/ghi hồ sơ KHDH ra file Excel."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..analyzer.hdtn import normalize_text_loose
from ..analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_NGHI
from .models import (
    PPCTStartEntry,
    ProfileSchemaError,
    SlotEntry,
    TKBHolidayRule,
    TKBScheduleEvent,
    TKBTemplate,
)
from .profile import KHDHProfile, profile_schedule_events


# =====================================================================
# I/O — Excel + JSON (sẽ implement ở Task 2.3 và 2.4)
# =====================================================================

# (4 stub functions removed — real implementations defined below)


# =====================================================================
# Task 2.3 — Excel I/O implementation
# =====================================================================


try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# Map int → text cho Excel
_BUOI_TO_TEXT = {1: "Sáng", 2: "Chiều"}


_BUOI_FROM_TEXT = {"sáng": 1, "sang": 1, "chiều": 2, "chieu": 2,
                   "1": 1, "2": 2}


_THU_TO_TEXT = {2: "Thứ 2", 3: "Thứ 3", 4: "Thứ 4", 5: "Thứ 5",
                6: "Thứ 6", 7: "Thứ 7", 8: "Chủ nhật"}


_CAP_HOC_TO_TEXT = {1: "Tiểu học", 2: "THCS", 3: "THPT"}


# Excel header constants
_SHEET_THONGTIN = "ThongTin"


_SHEET_TKB = "TKB"


_SHEET_TKB_LE = "TKB lẻ"


_SHEET_TKB_CHAN = "TKB chẵn"


_SHEET_PPCT = "PPCT bắt đầu"


_SHEET_NGHI_BU = "Nghỉ - Dạy bù"


_HEADER_TKB = ["Thứ", "Buổi", "Tiết", "Lớp", "Lớp ID",
               "Môn", "Môn ID", "Phân môn", "Phân môn ID"]


_HEADER_PPCT = ["Lớp", "Lớp ID", "Môn", "Môn ID",
                "Phân môn", "Phân môn ID", "PPCT bắt đầu", "Ghi chú"]


_HEADER_NGHI_BU_LEGACY = [
    "Bật", "Tuần nghỉ", "Thứ nghỉ", "Buổi nghỉ", "Tiết nghỉ",
    "Tuần bù", "Thứ bù", "Buổi bù", "Tiết bù", "Ghi chú",
]


_HEADER_NGHI_BU = [
    "Bật", "Loại", "Tuần", "Thứ", "Buổi", "Tiết",
    "Lớp", "Lớp ID", "Môn", "Môn ID", "Phân môn", "Phân môn ID",
    "Ô nguồn", "Ghi đè tiết thường", "Ghi chú",
]


# Style — màu nhẹ nhàng, dễ đọc
_HEADER_FONT = ("Segoe UI", 11, True)        # bold


_HEADER_FILL = "DCE9F4"                       # xanh nhạt


_BORDER_GRAY = "BFC9D2"


def _check_openpyxl():
    if not HAS_OPENPYXL:
        raise RuntimeError(
            "Thiếu thư viện openpyxl. Hãy chạy: pip install openpyxl"
        )


def _write_cell(ws, row: int, col: int, value, *, bold=False, fill=None,
                align="center", border=False):
    """Helper ghi 1 cell với style."""
    cell = ws.cell(row=row, column=col, value=value)
    if bold or fill:
        cell.font = Font(name="Segoe UI", size=10, bold=bold)
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
    return cell


def _write_header_row(ws, row: int, headers: list[str]):
    for col_idx, header in enumerate(headers, 1):
        _write_cell(ws, row, col_idx, header, bold=True, fill=_HEADER_FILL,
                   align="center")


def _autosize_columns(ws, headers: list[str], data_widths: dict[int, int] | None = None):
    """Set chiều rộng cột tối thiểu."""
    data_widths = data_widths or {}
    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        # Min = len(header) + 4
        width = max(len(header) + 4, data_widths.get(col_idx, 12))
        ws.column_dimensions[col_letter].width = min(width, 50)


def _write_template(ws, template: TKBTemplate, sheet_label: str):
    """Ghi 1 template vào sheet. Sheet đã được tạo trước."""
    _write_header_row(ws, 1, _HEADER_TKB)
    # Sort slots theo (thu, buoi, tiet) để Excel dễ đọc
    sorted_slots = sorted(
        template.slots,
        key=lambda s: (s.thu, s.buoi, s.tiet),
    )
    for r_idx, s in enumerate(sorted_slots, 2):
        _write_cell(ws, r_idx, 1, _THU_TO_TEXT.get(s.thu, str(s.thu)))
        _write_cell(ws, r_idx, 2, _BUOI_TO_TEXT.get(s.buoi, str(s.buoi)))
        _write_cell(ws, r_idx, 3, s.tiet)
        _write_cell(ws, r_idx, 4, s.lop_text, align="center")
        _write_cell(ws, r_idx, 5, s.lop_id, align="left")
        _write_cell(ws, r_idx, 6, s.mon_text, align="left")
        _write_cell(ws, r_idx, 7, s.mon_id, align="left")
        _write_cell(ws, r_idx, 8, s.phan_mon_text, align="left")
        _write_cell(ws, r_idx, 9, s.phan_mon_id, align="left")
    _autosize_columns(ws, _HEADER_TKB, data_widths={
        1: 10, 2: 10, 3: 8, 4: 12, 5: 16, 6: 28, 7: 10, 8: 28, 9: 14,
    })
    ws.row_dimensions[1].height = 22


def _write_ppct_sheet(ws, ppct_starts: list[PPCTStartEntry]):
    _write_header_row(ws, 1, _HEADER_PPCT)
    for r_idx, e in enumerate(ppct_starts, 2):
        _write_cell(ws, r_idx, 1, e.lop_text, align="center")
        _write_cell(ws, r_idx, 2, e.lop_id, align="left")
        _write_cell(ws, r_idx, 3, e.mon_text, align="left")
        _write_cell(ws, r_idx, 4, e.mon_id, align="left")
        _write_cell(ws, r_idx, 5, e.phan_mon_text, align="left")
        _write_cell(ws, r_idx, 6, e.phan_mon_id, align="left")
        _write_cell(ws, r_idx, 7, e.ppct_start, align="center")
        _write_cell(ws, r_idx, 8, e.ghi_chu, align="left")
    _autosize_columns(ws, _HEADER_PPCT, data_widths={
        1: 12, 2: 16, 3: 28, 4: 10, 5: 28, 6: 14, 7: 14, 8: 24,
    })
    ws.row_dimensions[1].height = 22


def _write_schedule_events_sheet(ws, events: list[TKBScheduleEvent]):
    """Ghi sheet Nghỉ/Dạy bù theo model event mới."""
    _write_header_row(ws, 1, _HEADER_NGHI_BU)
    for r_idx, event in enumerate(events, 2):
        _write_cell(ws, r_idx, 1, "Có" if event.enabled else "Không")
        _write_cell(ws, r_idx, 2, event.label)
        _write_cell(ws, r_idx, 3, event.tuan)
        _write_cell(ws, r_idx, 4, _THU_TO_TEXT.get(event.thu, event.thu))
        _write_cell(ws, r_idx, 5, _BUOI_TO_TEXT.get(event.buoi, event.buoi))
        _write_cell(ws, r_idx, 6, event.tiet)
        _write_cell(ws, r_idx, 7, event.source_lop_text, align="center")
        _write_cell(ws, r_idx, 8, event.source_lop_id, align="left")
        _write_cell(ws, r_idx, 9, event.source_mon_text, align="left")
        _write_cell(ws, r_idx, 10, event.source_mon_id, align="left")
        _write_cell(ws, r_idx, 11, event.source_phan_mon_text, align="left")
        _write_cell(ws, r_idx, 12, event.source_phan_mon_id, align="left")
        _write_cell(ws, r_idx, 13, event.source_slot_key, align="center")
        _write_cell(ws, r_idx, 14, "Có" if event.replace_normal_slot else "Không")
        _write_cell(ws, r_idx, 15, event.note, align="left")
    _autosize_columns(ws, _HEADER_NGHI_BU, data_widths={
        1: 8, 2: 12, 3: 10, 4: 12, 5: 10,
        6: 8, 7: 12, 8: 16, 9: 24, 10: 10,
        11: 24, 12: 14, 13: 12, 14: 18, 15: 34,
    })
    ws.row_dimensions[1].height = 22


def _write_holiday_rules_sheet(ws, rules: list[TKBHolidayRule]):
    """Hàm cũ giữ tương thích nội bộ; khi lưu mới nên dùng event sheet."""
    _write_header_row(ws, 1, _HEADER_NGHI_BU_LEGACY)
    for r_idx, rule in enumerate(rules, 2):
        _write_cell(ws, r_idx, 1, "Có" if rule.enabled else "Không")
        _write_cell(ws, r_idx, 2, rule.holiday_tuan)
        _write_cell(ws, r_idx, 3, _THU_TO_TEXT.get(rule.holiday_thu, rule.holiday_thu))
        _write_cell(ws, r_idx, 4, _BUOI_TO_TEXT.get(rule.holiday_buoi, rule.holiday_buoi))
        _write_cell(ws, r_idx, 5, rule.holiday_tiet)
        _write_cell(ws, r_idx, 6, rule.makeup_tuan or "")
        _write_cell(
            ws, r_idx, 7,
            _THU_TO_TEXT.get(rule.makeup_thu, rule.makeup_thu) if rule.has_makeup else "",
        )
        _write_cell(
            ws, r_idx, 8,
            _BUOI_TO_TEXT.get(rule.makeup_buoi, rule.makeup_buoi) if rule.has_makeup else "",
        )
        _write_cell(ws, r_idx, 9, rule.makeup_tiet or "")
        _write_cell(ws, r_idx, 10, rule.note, align="left")


def _write_thongtin_sheet(ws, profile: KHDHProfile):
    """Ghi sheet ThongTin dạng (label, value) 2 cột."""
    rows = [
        ("Họ tên giáo viên", profile.ho_ten_gv),
        ("Năm học", f"{profile.nam_hoc} - {profile.nam_hoc + 1}"),
        ("Năm học (số)", profile.nam_hoc),
        ("Cấp", profile.cap_hoc_text or _CAP_HOC_TO_TEXT.get(profile.cap_hoc, "")),
        ("Cấp (mã)", profile.cap_hoc),
        ("Mã trường", profile.ma_truong),
        ("Ngày tạo", profile.ngay_tao or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Ghi chú", profile.ghi_chu),
        ("Tách lẻ chẵn", "Có" if profile.tach_le_chan else "Không"),
        ("Tuần áp dụng", f"{profile.tuan_from} đến {profile.tuan_to}"),
    ]
    for r_idx, (label, value) in enumerate(rows, 1):
        _write_cell(ws, r_idx, 1, label, bold=True, fill=_HEADER_FILL, align="left")
        _write_cell(ws, r_idx, 2, value, align="left")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 40


def save_profile_xlsx(profile: KHDHProfile, path) -> None:
    """Ghi hồ sơ ra file Excel `.xlsx`.

    Cấu trúc:
        ThongTin    — metadata (10 row dạng key-value)
        TKB / TKB lẻ + TKB chẵn
        PPCT bắt đầu

    Args:
        profile: hồ sơ cần lưu
        path: đường dẫn file `.xlsx` (str hoặc Path)
    """
    _check_openpyxl()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    # Sheet mặc định bị xóa, mình tạo lại với tên đúng
    default = wb.active
    wb.remove(default)

    # Sheet 1: ThongTin
    ws_info = wb.create_sheet(_SHEET_THONGTIN)
    _write_thongtin_sheet(ws_info, profile)

    # Sheet 2/3: TKB
    if profile.tach_le_chan:
        assert profile.template_le is not None and profile.template_chan is not None
        ws_le = wb.create_sheet(_SHEET_TKB_LE)
        _write_template(ws_le, profile.template_le, _SHEET_TKB_LE)
        ws_chan = wb.create_sheet(_SHEET_TKB_CHAN)
        _write_template(ws_chan, profile.template_chan, _SHEET_TKB_CHAN)
    else:
        assert profile.template_chinh is not None
        ws_tkb = wb.create_sheet(_SHEET_TKB)
        _write_template(ws_tkb, profile.template_chinh, _SHEET_TKB)

    # Sheet PPCT
    ws_ppct = wb.create_sheet(_SHEET_PPCT)
    _write_ppct_sheet(ws_ppct, profile.ppct_starts)

    # Sheet Nghỉ/Dạy bù
    ws_nghi_bu = wb.create_sheet(_SHEET_NGHI_BU)
    _write_schedule_events_sheet(ws_nghi_bu, profile_schedule_events(profile))

    # Atomic write: ghi vào temp file rồi rename
    tmp_path = p.with_suffix(".xlsx.tmp")
    wb.save(tmp_path)
    if p.exists():
        p.unlink()
    tmp_path.rename(p)


def _read_thongtin_sheet(ws) -> dict:
    """Đọc sheet ThongTin → dict các field."""
    info = {}
    for row in ws.iter_rows(min_row=1, max_col=2, values_only=True):
        if not row:
            continue
        label, value = row[0], (row[1] if len(row) > 1 else None)
        if label is None:
            continue
        key = str(label).strip()
        info[key] = value
    return info


def _parse_thu(value) -> int:
    """Parse cột Thứ — chấp nhận 'Thứ 2', '2', 'Chủ nhật', 'CN', 8."""
    if value is None:
        raise ValueError("Thứ trống")
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s in ("cn", "chủ nhật", "chu nhat", "8"):
        return 8
    # "thứ 2" → 2
    for tok in s.replace("thứ", "").replace("thu", "").split():
        if tok.isdigit():
            return int(tok)
    if s.isdigit():
        return int(s)
    raise ValueError(f"Không hiểu giá trị Thứ: {value!r}")


def _parse_buoi(value) -> int:
    if value is None:
        raise ValueError("Buổi trống")
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s in _BUOI_FROM_TEXT:
        return _BUOI_FROM_TEXT[s]
    raise ValueError(f"Không hiểu giá trị Buổi: {value!r}")


def _read_template_sheet(ws, sheet_label: str) -> TKBTemplate:
    """Đọc 1 sheet TKB → TKBTemplate.

    Raises:
        ProfileSchemaError nếu thiếu cột header hoặc sai data.
    """
    # Verify header
    first_row = [c.value for c in ws[1]] if ws.max_row >= 1 else []
    expected_headers = set(_HEADER_TKB)
    have_headers = set(str(h).strip() if h else "" for h in first_row)
    missing = expected_headers - have_headers
    if missing:
        raise ProfileSchemaError(
            f"{sheet_label}.header",
            f"Sheet '{sheet_label}' thiếu cột: {', '.join(sorted(missing))}",
        )

    # Map header → column index
    col_idx = {}
    for i, h in enumerate(first_row, 1):
        key = str(h).strip() if h else ""
        if key:
            col_idx[key] = i

    slots = []
    for r in range(2, ws.max_row + 1):
        row = [ws.cell(row=r, column=col_idx[h]).value for h in _HEADER_TKB]
        # Nếu hàng rỗng hoàn toàn, skip
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        try:
            thu = _parse_thu(row[0])
            buoi = _parse_buoi(row[1])
            tiet = int(row[2]) if row[2] is not None else 0
            lop_text = str(row[3] or "").strip()
            lop_id = str(row[4] or "").strip()
            mon_text = str(row[5] or "").strip()
            mon_id = str(row[6] or "").strip()
            pm_text = str(row[7] or "").strip()
            pm_id = str(row[8] or "").strip()
        except (ValueError, TypeError) as e:
            raise ProfileSchemaError(
                f"{sheet_label}.row{r}",
                f"Hàng {r} trong sheet '{sheet_label}' có giá trị không hợp lệ: {e}",
            )
        try:
            slot = SlotEntry(
                thu=thu, buoi=buoi, tiet=tiet,
                lop_id=lop_id, lop_text=lop_text,
                mon_id=mon_id, mon_text=mon_text,
                phan_mon_id=pm_id, phan_mon_text=pm_text,
            )
        except ProfileSchemaError as e:
            raise ProfileSchemaError(
                f"{sheet_label}.row{r}",
                f"Hàng {r}: {e.msg}",
            )
        slots.append(slot)

    return TKBTemplate(slots=slots)


def _read_ppct_sheet(ws) -> list[PPCTStartEntry]:
    first_row = [c.value for c in ws[1]] if ws.max_row >= 1 else []
    expected_headers = set(_HEADER_PPCT)
    have_headers = set(str(h).strip() if h else "" for h in first_row)
    missing = expected_headers - have_headers
    if missing:
        raise ProfileSchemaError(
            f"{_SHEET_PPCT}.header",
            f"Sheet '{_SHEET_PPCT}' thiếu cột: {', '.join(sorted(missing))}",
        )
    col_idx = {}
    for i, h in enumerate(first_row, 1):
        key = str(h).strip() if h else ""
        if key:
            col_idx[key] = i

    out = []
    for r in range(2, ws.max_row + 1):
        row = [ws.cell(row=r, column=col_idx[h]).value for h in _HEADER_PPCT]
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        try:
            entry = PPCTStartEntry(
                lop_id=str(row[1] or "").strip(),
                lop_text=str(row[0] or "").strip(),
                mon_id=str(row[3] or "").strip(),
                mon_text=str(row[2] or "").strip(),
                phan_mon_id=str(row[5] or "").strip(),
                phan_mon_text=str(row[4] or "").strip(),
                ppct_start=int(row[6]) if row[6] is not None else 1,
                ghi_chu=str(row[7] or "").strip(),
            )
        except ProfileSchemaError as e:
            raise ProfileSchemaError(
                f"{_SHEET_PPCT}.row{r}",
                f"Hàng {r}: {e.msg}",
            )
        except (ValueError, TypeError) as e:
            raise ProfileSchemaError(
                f"{_SHEET_PPCT}.row{r}",
                f"Hàng {r}: giá trị không hợp lệ — {e}",
            )
        out.append(entry)
    return out


def _parse_excel_bool(value, default: bool = True) -> bool:
    """Parse Có/Không trong sheet cấu hình."""
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("có", "co", "true", "yes", "y", "1", "bật", "bat"):
        return True
    if s in ("không", "khong", "false", "no", "n", "0", "tắt", "tat"):
        return False
    return default


def _parse_optional_int(value) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        s = str(value).strip()
        nums = re.findall(r"\d+", s)
        return int(nums[0]) if nums else 0


def _parse_optional_thu(value) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return _parse_thu(value)


def _parse_optional_buoi(value) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return _parse_buoi(value)


def _parse_event_kind(value) -> str:
    s = normalize_text_loose(str(value or ""))
    if s in ("nghi", "tiet nghi"):
        return TKB_EVENT_NGHI
    if s in ("day bu", "tiet day bu", "bu"):
        return TKB_EVENT_DAY_BU
    raise ValueError(f"Không hiểu loại Nghỉ/Dạy bù: {value!r}")


def _sheet_has_headers(first_row: list, headers: list[str]) -> bool:
    have = set(str(h).strip() if h else "" for h in first_row)
    return set(headers).issubset(have)


def _read_schedule_events_sheet(ws) -> list[TKBScheduleEvent]:
    first_row = [c.value for c in ws[1]] if ws.max_row >= 1 else []
    expected_headers = set(_HEADER_NGHI_BU)
    have_headers = set(str(h).strip() if h else "" for h in first_row)
    missing = expected_headers - have_headers
    if missing:
        raise ProfileSchemaError(
            f"{_SHEET_NGHI_BU}.header",
            f"Sheet '{_SHEET_NGHI_BU}' thiếu cột: {', '.join(sorted(missing))}",
        )

    col_idx = {}
    for i, h in enumerate(first_row, 1):
        key = str(h).strip() if h else ""
        if key:
            col_idx[key] = i

    out: list[TKBScheduleEvent] = []
    for r in range(2, ws.max_row + 1):
        row = [ws.cell(row=r, column=col_idx[h]).value for h in _HEADER_NGHI_BU]
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        try:
            event = TKBScheduleEvent(
                enabled=_parse_excel_bool(row[0], default=True),
                kind=_parse_event_kind(row[1]),
                tuan=int(row[2]) if row[2] is not None else 0,
                thu=_parse_thu(row[3]),
                buoi=_parse_buoi(row[4]),
                tiet=int(row[5]) if row[5] is not None else 0,
                source_lop_text=str(row[6] or "").strip(),
                source_lop_id=str(row[7] or "").strip(),
                source_mon_text=str(row[8] or "").strip(),
                source_mon_id=str(row[9] or "").strip(),
                source_phan_mon_text=str(row[10] or "").strip(),
                source_phan_mon_id=str(row[11] or "").strip(),
                source_slot_key=str(row[12] or "").strip(),
                replace_normal_slot=_parse_excel_bool(row[13], default=False),
                note=str(row[14] or "").strip(),
            )
        except ProfileSchemaError as e:
            raise ProfileSchemaError(
                f"{_SHEET_NGHI_BU}.row{r}",
                f"Hàng {r}: {e.msg}",
            )
        except (ValueError, TypeError) as e:
            raise ProfileSchemaError(
                f"{_SHEET_NGHI_BU}.row{r}",
                f"Hàng {r}: giá trị không hợp lệ — {e}",
            )
        out.append(event)
    return out


def _read_holiday_rules_sheet(ws) -> list[TKBHolidayRule]:
    first_row = [c.value for c in ws[1]] if ws.max_row >= 1 else []
    expected_headers = set(_HEADER_NGHI_BU_LEGACY)
    have_headers = set(str(h).strip() if h else "" for h in first_row)
    missing = expected_headers - have_headers
    if missing:
        raise ProfileSchemaError(
            f"{_SHEET_NGHI_BU}.header",
            f"Sheet '{_SHEET_NGHI_BU}' thiếu cột: {', '.join(sorted(missing))}",
        )

    col_idx = {}
    for i, h in enumerate(first_row, 1):
        key = str(h).strip() if h else ""
        if key:
            col_idx[key] = i

    out: list[TKBHolidayRule] = []
    for r in range(2, ws.max_row + 1):
        row = [ws.cell(row=r, column=col_idx[h]).value for h in _HEADER_NGHI_BU_LEGACY]
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        try:
            rule = TKBHolidayRule(
                enabled=_parse_excel_bool(row[0], default=True),
                holiday_tuan=int(row[1]) if row[1] is not None else 0,
                holiday_thu=_parse_thu(row[2]),
                holiday_buoi=_parse_buoi(row[3]),
                holiday_tiet=int(row[4]) if row[4] is not None else 0,
                makeup_tuan=_parse_optional_int(row[5]),
                makeup_thu=_parse_optional_thu(row[6]),
                makeup_buoi=_parse_optional_buoi(row[7]),
                makeup_tiet=_parse_optional_int(row[8]),
                note=str(row[9] or "").strip(),
            )
        except ProfileSchemaError as e:
            raise ProfileSchemaError(
                f"{_SHEET_NGHI_BU}.row{r}",
                f"Hàng {r}: {e.msg}",
            )
        except (ValueError, TypeError) as e:
            raise ProfileSchemaError(
                f"{_SHEET_NGHI_BU}.row{r}",
                f"Hàng {r}: giá trị không hợp lệ — {e}",
            )
        out.append(rule)
    return out


def load_profile_xlsx(path) -> KHDHProfile:
    """Load hồ sơ từ file Excel `.xlsx`.

    Raises:
        ProfileSchemaError nếu sheet/cột thiếu hoặc data sai.
        FileNotFoundError nếu file không tồn tại.
    """
    _check_openpyxl()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    wb = load_workbook(p, data_only=True)
    sheet_names = wb.sheetnames

    # Sheet ThongTin
    if _SHEET_THONGTIN not in sheet_names:
        raise ProfileSchemaError(
            "Sheet.ThongTin",
            f"Hồ sơ thiếu sheet '{_SHEET_THONGTIN}'.",
        )
    info = _read_thongtin_sheet(wb[_SHEET_THONGTIN])

    # Parse field bắt buộc
    ho_ten = info.get("Họ tên giáo viên") or ""
    if not ho_ten or not str(ho_ten).strip():
        raise ProfileSchemaError(
            "ThongTin.ho_ten_gv",
            "Sheet 'ThongTin' thiếu hoặc trống ô 'Họ tên giáo viên'.",
        )
    nam_hoc_raw = info.get("Năm học (số)") or info.get("Năm học")
    if nam_hoc_raw is None:
        raise ProfileSchemaError(
            "ThongTin.nam_hoc",
            "Sheet 'ThongTin' thiếu ô 'Năm học (số)' hoặc 'Năm học'.",
        )
    nam_hoc = _coerce_nam_hoc(nam_hoc_raw)

    cap_hoc_raw = info.get("Cấp (mã)") or info.get("Cấp")
    cap_hoc, cap_hoc_text = _coerce_cap_hoc(cap_hoc_raw)
    if not cap_hoc_text:
        cap_hoc_text = _CAP_HOC_TO_TEXT.get(cap_hoc, "")

    # Detect tách lẻ/chẵn
    tach = (str(info.get("Tách lẻ chẵn") or "").strip().lower()
            in ("có", "co", "true", "yes", "1"))

    # Tuần áp dụng
    tuan_from, tuan_to = _coerce_tuan_range(info.get("Tuần áp dụng"))

    # Sheets TKB
    template_chinh = template_le = template_chan = None
    if _SHEET_TKB_LE in sheet_names and _SHEET_TKB_CHAN in sheet_names:
        template_le = _read_template_sheet(wb[_SHEET_TKB_LE], _SHEET_TKB_LE)
        template_chan = _read_template_sheet(wb[_SHEET_TKB_CHAN], _SHEET_TKB_CHAN)
        tach = True
    elif _SHEET_TKB in sheet_names:
        template_chinh = _read_template_sheet(wb[_SHEET_TKB], _SHEET_TKB)
        tach = False
    else:
        raise ProfileSchemaError(
            "Sheet.TKB",
            "Hồ sơ phải có sheet 'TKB' hoặc cả hai 'TKB lẻ' + 'TKB chẵn'.",
        )

    # Sheet PPCT
    if _SHEET_PPCT not in sheet_names:
        raise ProfileSchemaError(
            f"Sheet.{_SHEET_PPCT}",
            f"Hồ sơ thiếu sheet '{_SHEET_PPCT}'.",
        )
    ppct_starts = _read_ppct_sheet(wb[_SHEET_PPCT])
    holiday_rules: list[TKBHolidayRule] = []
    schedule_events: list[TKBScheduleEvent] = []
    if _SHEET_NGHI_BU in sheet_names:
        ws_nghi_bu = wb[_SHEET_NGHI_BU]
        first_row = [c.value for c in ws_nghi_bu[1]] if ws_nghi_bu.max_row >= 1 else []
        if _sheet_has_headers(first_row, _HEADER_NGHI_BU):
            schedule_events = _read_schedule_events_sheet(ws_nghi_bu)
        else:
            holiday_rules = _read_holiday_rules_sheet(ws_nghi_bu)

    profile = KHDHProfile(
        ho_ten_gv=str(ho_ten).strip(),
        nam_hoc=nam_hoc,
        cap_hoc=cap_hoc,
        cap_hoc_text=cap_hoc_text,
        ma_truong=str(info.get("Mã trường") or "").strip(),
        ngay_tao=str(info.get("Ngày tạo") or "").strip(),
        ghi_chu=str(info.get("Ghi chú") or "").strip(),
        tach_le_chan=tach,
        template_chinh=template_chinh,
        template_le=template_le,
        template_chan=template_chan,
        ppct_starts=ppct_starts,
        holiday_rules=holiday_rules,
        schedule_events=schedule_events,
        tuan_from=tuan_from,
        tuan_to=tuan_to,
    )
    return profile


def _coerce_nam_hoc(raw) -> int:
    """Parse 'Năm học (số)' hoặc 'Năm học' (có thể là 2025 hoặc '2025 - 2026')."""
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip()
    if "-" in s:
        s = s.split("-", 1)[0].strip()
    try:
        return int(s)
    except ValueError:
        raise ProfileSchemaError(
            "ThongTin.nam_hoc",
            f"'Năm học' không phải số: {raw!r}",
        )


def _coerce_cap_hoc(raw) -> tuple[int, str]:
    """Parse 'Cấp (mã)' hoặc 'Cấp' → (cap_hoc_int, cap_hoc_text)."""
    if isinstance(raw, (int, float)):
        return int(raw), _CAP_HOC_TO_TEXT.get(int(raw), "")
    s = str(raw or "").strip()
    s_low = s.lower()
    if s_low in ("th", "tiểu học", "tieu hoc", "1"):
        return 1, "Tiểu học"
    if s_low in ("thcs", "trung học cơ sở", "2"):
        return 2, "THCS"
    if s_low in ("thpt", "trung học phổ thông", "3"):
        return 3, "THPT"
    raise ProfileSchemaError(
        "ThongTin.cap_hoc",
        f"Cấp không hợp lệ: {raw!r}. Phải là TH/THCS/THPT hoặc 1/2/3.",
    )


def _coerce_tuan_range(raw) -> tuple[int, int]:
    """Parse 'Tuần áp dụng' dạng '1 đến 35'."""
    if raw is None:
        return 1, 35
    s = str(raw).strip()
    if not s:
        return 1, 35
    # Tìm 2 số nguyên
    import re as _re
    nums = _re.findall(r"\d+", s)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    if len(nums) == 1:
        n = int(nums[0])
        return n, n
    return 1, 35

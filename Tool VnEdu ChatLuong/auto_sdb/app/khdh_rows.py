"""Xử lý dữ liệu hàng đỏ KHDH và popup "Chi tiết tiết học" — hàm thuần, không cần trình duyệt.

Dùng bởi `KhdhScheduleJob` (schedule_worker_khdh.py). Tách riêng để đọc và test độc lập.
"""

import re
import unicodedata


def digits(value):
    """Cụm chữ số đầu tiên trong chuỗi ("Tiết 3" -> "3"), không có thì ""."""
    text = str(value or "").strip()
    match = re.search(r"\d+", text)
    return match.group() if match else ""


def normalize_text(value):
    """Bỏ dấu, gộp khoảng trắng, chữ thường — để so nội dung bài."""
    text = str(value or "").replace("\n", " ")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_date_token(value):
    """Ngày dạng d/m/yyyy trong chuỗi -> dd/mm/yyyy, không có thì ""."""
    text = str(value or "").strip()
    match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
    if not match:
        return ""
    day, month, year = match.group(1).split("/")
    return f"{int(day):02d}/{int(month):02d}/{year}"


def row_resume_key(row_info):
    """Khoá nhận diện một hàng đỏ KHDH để chạy tiếp (resume) đúng hàng, kể cả khi thứ tự hàng đổi."""
    parts = [
        str(row_info.get("thu", "") or "").strip(),
        str(row_info.get("buoi", "") or "").strip().casefold(),
        str(row_info.get("tiet", "") or "").strip(),
        str(row_info.get("mon_hoc_id", "") or "").strip(),
        str(row_info.get("phan_mon_id", "") or "").strip(),
        digits(row_info.get("ppct_hint")),
        normalize_text(row_info.get("noi_dung_hint", "")),
    ]
    return "|".join(parts)


def extract_form_value(snapshot, field_name, use_raw=False):
    """Giá trị (hoặc text hiển thị nếu use_raw) của một field trong snapshot popup."""
    fields = (snapshot or {}).get("fields", {}) or {}
    field_info = fields.get(field_name, {}) or {}
    key = "raw" if use_raw else "value"
    return str(field_info.get(key, "") or "").strip()


def extract_popup_khdh_values(snapshot):
    """Các giá trị KHDH đang có trong popup: thứ, tiết, ngày, môn, phân môn, PPCT, nội dung."""
    popup_ppct_raw = extract_form_value(snapshot, "tiet_ppct", use_raw=True) or extract_form_value(snapshot, "tiet_ppct")
    popup_noi_dung = extract_form_value(snapshot, "noi_dung", use_raw=True) or extract_form_value(snapshot, "noi_dung")
    return {
        "thu": digits(extract_form_value(snapshot, "thu")),
        "tiet": digits(extract_form_value(snapshot, "tiet")),
        "ngay": normalize_date_token(
            extract_form_value(snapshot, "ngay", use_raw=True)
            or extract_form_value(snapshot, "ngay")
        ),
        "mon_hoc_id": extract_form_value(snapshot, "mon_hoc_id"),
        "mon_hoc_text": extract_form_value(snapshot, "mon_hoc_id", use_raw=True),
        "phan_mon_id": extract_form_value(snapshot, "phan_mon_id"),
        "phan_mon_text": extract_form_value(snapshot, "phan_mon_id", use_raw=True),
        "ppct": digits(popup_ppct_raw),
        "ppct_raw": str(popup_ppct_raw or "").strip(),
        "noi_dung": str(popup_noi_dung or "").strip(),
    }


def verify_snapshot_matches_row(snapshot, row_info):
    """So popup đang mở với hàng đỏ đã bấm "+".

    Returns:
        (khớp, lỗi_nặng, lỗi_nhẹ, giá_trị_popup). Lỗi nặng (thứ/tiết/ngày lệch) = popup mở sai hàng
        -> phải dừng. Lỗi nhẹ (môn/phân môn/PPCT/nội dung lệch) -> chỉ cảnh báo, ưu tiên dữ liệu popup.
    """
    hard_issues = []
    soft_issues = []
    popup = extract_popup_khdh_values(snapshot)

    row_thu = digits(row_info.get("thu"))
    row_tiet = digits(row_info.get("tiet"))
    row_ngay = normalize_date_token(row_info.get("ngay"))
    row_mon_hoc_id = str(row_info.get("mon_hoc_id", "") or "").strip()
    row_phan_mon_id = str(row_info.get("phan_mon_id", "") or "").strip()
    row_ppct = digits(row_info.get("ppct_hint"))
    row_noi_dung = str(row_info.get("noi_dung_hint", "") or "").strip()

    if row_thu and popup["thu"] and row_thu != popup["thu"]:
        hard_issues.append(f"thu mismatch {popup['thu']} != {row_thu}")
    if row_tiet and popup["tiet"] and row_tiet != popup["tiet"]:
        hard_issues.append(f"tiet mismatch {popup['tiet']} != {row_tiet}")
    if row_ngay and popup["ngay"] and row_ngay != popup["ngay"]:
        hard_issues.append(f"ngay mismatch {popup['ngay']} != {row_ngay}")

    if row_mon_hoc_id and popup["mon_hoc_id"] and row_mon_hoc_id != popup["mon_hoc_id"]:
        soft_issues.append(f"mon_hoc_id mismatch {popup['mon_hoc_id']} != {row_mon_hoc_id}")
    if row_phan_mon_id and popup["phan_mon_id"] and row_phan_mon_id != popup["phan_mon_id"]:
        soft_issues.append(f"phan_mon_id mismatch {popup['phan_mon_id']} != {row_phan_mon_id}")
    if row_ppct and popup["ppct"] and row_ppct != popup["ppct"]:
        soft_issues.append(f"PPCT mismatch {popup['ppct']} != {row_ppct}")
    if row_noi_dung and popup["noi_dung"]:
        expected = normalize_text(row_noi_dung)
        actual = normalize_text(popup["noi_dung"])
        if expected and actual and expected not in actual and actual not in expected:
            soft_issues.append("noi_dung mismatch")

    return len(hard_issues) == 0, hard_issues, soft_issues, popup


def resolve_fill_targets(popup_values, row_info):
    """Dữ liệu sẽ điền: ưu tiên giá trị popup đang có, thiếu thì lấy gợi ý từ hàng đỏ."""
    return {
        "mon_hoc_id": str(
            popup_values.get("mon_hoc_id")
            or row_info.get("mon_hoc_id", "")
            or ""
        ).strip(),
        "phan_mon_id": str(
            popup_values.get("phan_mon_id")
            or row_info.get("phan_mon_id", "")
            or ""
        ).strip(),
        "ppct": str(
            popup_values.get("ppct_raw")
            or popup_values.get("ppct")
            or row_info.get("ppct_hint", "")
            or ""
        ).strip(),
        "noi_dung": str(
            popup_values.get("noi_dung")
            or row_info.get("noi_dung_hint", "")
            or ""
        ).strip(),
        "mon_hoc_text": str(
            popup_values.get("mon_hoc_text")
            or row_info.get("mon_hoc_text_hint")
            or row_info.get("mon_hoc_hint")
            or ""
        ).strip(),
        "phan_mon_text": str(
            popup_values.get("phan_mon_text")
            or row_info.get("phan_mon_text_hint", "")
            or ""
        ).strip(),
    }


def missing_fill_fields(targets):
    """Trường bắt buộc còn thiếu để điền popup (PPCT, nội dung)."""
    missing_fields = []
    if not targets["ppct"]:
        missing_fields.append("PPCT")
    if not targets["noi_dung"]:
        missing_fields.append("nội dung")
    return missing_fields


def popup_has_khdh_payload(popup_values):
    """Popup đã có sẵn PPCT + nội dung từ KHDH -> chỉ cần điền tối thiểu (HS nghỉ, nhận xét, điểm)."""
    return bool(
        str(
            popup_values.get("ppct_raw")
            or popup_values.get("ppct")
            or ""
        ).strip()
        and str(popup_values.get("noi_dung") or "").strip()
    )


def parse_nhan_xet_items(nhan_xet_raw):
    """Danh sách nhận xét (ngăn bằng `|`) — mỗi tiết chọn ngẫu nhiên một câu."""
    nhan_xet_items = [x.strip() for x in nhan_xet_raw.split("|") if x.strip()]
    if not nhan_xet_items:
        nhan_xet_items = ["Lớp học chăm ngoan"]
    return nhan_xet_items


def build_work_items(lop_list, start_lop_idx, start_tuan_num, tuan_from, tuan_to):
    """Danh sách (chỉ số lớp, tên lớp, tuần) cần chạy, bắt đầu từ checkpoint."""
    work_items = []
    for lop_idx in range(max(start_lop_idx, 0), len(lop_list)):
        lop_text = lop_list[lop_idx]
        first_week = start_tuan_num if lop_idx == start_lop_idx else tuan_from
        for tuan_num in range(first_week, tuan_to + 1):
            work_items.append((lop_idx, lop_text, tuan_num))
    return work_items

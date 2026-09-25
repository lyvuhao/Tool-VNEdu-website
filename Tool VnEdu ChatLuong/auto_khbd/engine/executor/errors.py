"""Định dạng thông điệp lỗi cho người dùng."""

from __future__ import annotations

from typing import Any

from ..fallback_log import _FB_ROW_KEY_RE


# ---------------------------------------------------------------
# Error message helpers — format thông điệp lỗi cho người dùng
# ---------------------------------------------------------------

# Mapping thứ trong tuần → text hiển thị (web dùng 8 = CN).
_THU_LABELS = {
    2: "Thứ 2", 3: "Thứ 3", 4: "Thứ 4", 5: "Thứ 5",
    6: "Thứ 6", 7: "Thứ 7", 8: "Chủ nhật",
}


_BUOI_LABELS = {1: "sáng", 2: "chiều"}


def format_row_label(row_key: str) -> str:
    """Parse `<thu>_<buoi>_<tiet>` thành label Việt: 'Thứ 2 sáng tiết 3'.

    Trả về row_key gốc nếu không parse được — caller có thể join thêm
    text lớp/môn/phân môn để có context đầy đủ.
    """
    m = _FB_ROW_KEY_RE.match(str(row_key or ""))
    if not m:
        return str(row_key or "")
    thu, buoi, tiet = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (
        f"{_THU_LABELS.get(thu, f'Thứ {thu}')} "
        f"{_BUOI_LABELS.get(buoi, f'buổi {buoi}')} "
        f"tiết {tiet}"
    )


def format_row_with_context(
    row_key: str,
    lop_text: str = "",
    mon_text: str = "",
    phan_mon_text: str = "",
) -> str:
    """Format đầy đủ row + thông tin lớp/môn/phân môn:
    'Thứ 2 sáng tiết 3 (6A4 / Ngoại ngữ / TC Ngoại ngữ)'.

    Phần trong dấu ngoặc chỉ hiện các trường non-empty, bỏ qua "---".
    """
    base = format_row_label(row_key)
    parts = [
        str(t or "").strip()
        for t in (lop_text, mon_text, phan_mon_text)
    ]
    parts = [t for t in parts if t and t != "---"]
    if parts:
        return f"{base} ({' / '.join(parts)})"
    return base


def classify_save_error_msg(msg: str) -> str:
    """Phân loại nguyên nhân lỗi từ message server để gợi ý next step.

    Pattern matching đơn giản theo các message thường gặp của VnEdu KHDH.
    Trả về key ngắn — caller dùng để chọn hint thân thiện cho user.
    """
    text = str(msg or "").lower()
    if not text:
        return "unknown"
    if any(k in text for k in ("trùng", "trùng tiết", "trung tiet", "đã có gv")):
        return "duplicate_teacher"
    if any(k in text for k in ("ppct", "phân phối")):
        return "ppct_invalid"
    if any(k in text for k in ("phiên", "session", "đăng nhập", "token")):
        return "session_expired"
    if any(k in text for k in ("kết nối", "mạng", "timeout", "không phản hồi")):
        return "network"
    if any(k in text for k in ("lớp", "môn", "phân môn", "chưa chọn")):
        return "missing_field"
    return "unknown"


# Hint actionable next steps theo loại lỗi.
ERROR_HINTS = {
    "duplicate_teacher": (
        "→ Có ô bị trùng tiết với giáo viên khác. Hãy mở web, vào tuần "
        "này, kiểm tra ô được đánh dấu vàng, sửa lớp/môn/PPCT rồi chạy "
        "lại từ tuần này."
    ),
    "ppct_invalid": (
        "→ PPCT không hợp lệ (vượt số tiết của phân phối chương trình). "
        "Mở [3. PPCT bắt đầu] kiểm tra số PPCT cho nhóm bị lỗi, hoặc "
        "bật tùy chọn [🛟 Tự nhấn dấu cách...] để cho qua bước validate."
    ),
    "session_expired": (
        "→ Phiên đăng nhập VnEdu đã hết hạn. Hãy đăng nhập lại trên "
        "Chrome (cùng tab VnEdu), rồi chạy lại."
    ),
    "network": (
        "→ Mạng kém hoặc web không phản hồi. Hãy thử reload tab VnEdu "
        "rồi chạy lại."
    ),
    "missing_field": (
        "→ Thiếu lớp / môn / phân môn cho 1 ô nào đó. Mở web kiểm tra "
        "các ô vàng, đảm bảo đã chọn đầy đủ."
    ),
    "unknown": (
        "→ Hãy mở web, vào tuần này, đặt chuột lên các ô vàng để xem "
        "lỗi cụ thể, sửa rồi chạy lại từ tuần này."
    ),
}


def format_save_errors_for_user(
    save_errors: list,
    fill_ops: list,
    max_lines: int = 20,
) -> str:
    """Render danh sách SaveError thành text Việt dễ đọc cho user.

    Mỗi dòng:
        • Thứ X sáng tiết Y (Lớp / Môn / Phân môn) — server báo: <message>

    Args:
        save_errors: list[SaveError]
        fill_ops: list[FillOp] để tra context lop/mon/pm theo row_key
        max_lines: tối đa số dòng hiển thị, dòng còn lại tóm tắt
    """
    by_rk: dict[str, Any] = {}
    for op in fill_ops or []:
        rk = str(getattr(op, "row_key", "") or "")
        if rk:
            by_rk[rk] = op
    lines: list[str] = []
    for err in save_errors or []:
        rk = str(getattr(err, "row_key", "") or "")
        msg = str(getattr(err, "message", "") or "").strip()
        op = by_rk.get(rk)
        if op is not None:
            label = format_row_with_context(
                rk,
                getattr(op, "lop_text", ""),
                getattr(op, "mon_text", ""),
                getattr(op, "phan_mon_text", ""),
            )
        else:
            label = format_row_label(rk) if rk else "(ô không xác định)"
        lines.append(f"  • {label} — {msg}" if msg else f"  • {label}")
    if not lines:
        return ""
    if len(lines) > max_lines:
        head = lines[:max_lines]
        head.append(f"  • … và {len(lines) - max_lines} ô khác")
        return "\n".join(head)
    return "\n".join(lines)


def format_chrome_connect_error(port: int, exc: Exception) -> str:
    """Format lỗi kết nối Chrome CDP với hướng dẫn cụ thể."""
    detail = f"{type(exc).__name__}: {str(exc)[:200]}"
    return (
        f"Không kết nối được Chrome trên cổng {port}.\n"
        f"Chi tiết: {detail}\n\n"
        "Cách khắc phục:\n"
        f"  1. Đảm bảo Chrome đã mở với cờ --remote-debugging-port={port}\n"
        "  2. Mở tab https://vnedu.vn và đăng nhập\n"
        "  3. Bấm lại [Đăng nhập VnEdu] trên tool"
    )


def format_no_vnedu_tab_error() -> str:
    """Format lỗi không tìm thấy tab VnEdu với hướng dẫn cụ thể."""
    return (
        "Không tìm thấy tab VnEdu trên Chrome đang debug.\n\n"
        "Cách khắc phục:\n"
        "  1. Mở tab mới trên Chrome (cùng cửa sổ debug đã mở)\n"
        "  2. Vào https://vnedu.vn và đăng nhập\n"
        "  3. Mở module Kế hoạch dạy học\n"
        "  4. Bấm lại nút trên tool"
    )


def format_context_read_error(exc: Exception) -> str:
    """Format lỗi không đọc được context KHDH với gợi ý."""
    detail = f"{type(exc).__name__}: {str(exc)[:200]}"
    return (
        f"Không đọc được context KHDH (token, năm học, ID giáo viên).\n"
        f"Chi tiết: {detail}\n\n"
        "Có thể do:\n"
        "  • Tab hiện tại không phải module Kế hoạch dạy học\n"
        "  • Phiên đăng nhập đã hết hạn — hãy đăng nhập lại\n"
        "  • Trang KHDH chưa load xong — hãy chờ và thử lại"
    )

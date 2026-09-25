"""Nhận diện môn HĐTN (Hoạt động trải nghiệm) và chuẩn hoá tên chủ đề."""

from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------
# Helpers nhận diện môn HĐTN — Hoạt động giáo dục theo chủ đề
# ---------------------------------------------------------------
# Dùng cho tính năng "Tự tính PPCT HĐTN Chủ đề" — nút trên wizard
# tính PPCT bắt đầu cho group HĐTN dựa vào tuan_from + số tiết/tuần
# (HĐTN-chủ đề: 1 tiết/tuần, có thể có lẻ/chẵn riêng).

def normalize_text_loose(text: str) -> str:
    """Lowercase + strip + bỏ dấu tiếng Việt cho match không phân biệt dấu."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", str(text))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "d")
    return s.lower().strip()


def is_hdtn_mon(mon_text: str) -> bool:
    """Match môn HĐTN: 'Hoạt động trải nghiệm' / 'Hoạt động trải nghiệm, hướng nghiệp'."""
    n = normalize_text_loose(mon_text)
    return ("trai nghiem" in n) or ("hdtn" in n)


def is_hdtn_chu_de_phan_mon(phan_mon_text: str) -> bool:
    """Match phân môn 'Hoạt động giáo dục theo chủ đề'."""
    n = normalize_text_loose(phan_mon_text)
    return "chu de" in n


def is_hdtn_chu_de_group(mon_text: str, phan_mon_text: str) -> bool:
    """Group HĐTN-chủ-đề = môn HĐTN + phân môn 'theo chủ đề'."""
    return is_hdtn_mon(mon_text) and is_hdtn_chu_de_phan_mon(phan_mon_text)


def _compact_spaces(text: str) -> str:
    """Chuẩn hóa khoảng trắng nhưng giữ nguyên chữ tiếng Việt."""
    return " ".join(str(text or "").replace("\xa0", " ").split())


def _extract_grade_from_lop_text(lop_text: str) -> int | None:
    """Lấy khối từ tên lớp như 6A1/7A2. Không đoán khi thiếu dữ liệu."""
    m = re.search(r"\b([6-9])\s*[A-ZÀ-Ỹ]?", str(lop_text or ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _classify_hdtn_part(mon_text: str, phan_mon_text: str) -> str:
    """Phân loại phân môn HĐTN dùng cho kho tên bài Word.

    Không được suy đoán chỉ từ tên môn "Hoạt động trải nghiệm". VnEdu có lúc
    render phân môn chậm/rỗng; nếu đoán mặc định là "Chủ đề" thì có thể ghi
    nhầm tên bài vào SHDC/SHL. Chỉ trả về khi text nói rõ phân môn.
    """
    combined = normalize_text_loose(f"{mon_text} {phan_mon_text}")
    if "sinh hoat lop" in combined or "shl" in combined:
        return "shl"
    if "sinh hoat duoi co" in combined or "chao co" in combined or "shdc" in combined:
        return "shdc"
    if "chu de" in combined or "giao duc theo" in combined or "hdgd" in combined:
        return "chu_de"
    return ""


def _ensure_sentence_period(text: str) -> str:
    """Thêm dấu chấm cuối câu cho tên bài SHDC nếu nguồn chưa có."""
    text = _compact_spaces(text)
    return text if not text or text[-1] in ".!?:;…" else f"{text}."


def _clean_hdtn_topic_text(text: str) -> str:
    """Sửa vài lỗi gõ rõ ràng trong nguồn Word trước khi ghi lên web."""
    s = _compact_spaces(text)
    replacements = {
        "lưa tuổi": "lứa tuổi",
        "Phát triển bề vững": "Phát triển bền vững",
        "người tốt việ tốt": "người tốt việc tốt",
    }
    for bad, good in replacements.items():
        s = s.replace(bad, good)
    return s


def _format_hdtn_topic_heading(text: str) -> str:
    """Chuẩn hóa tiêu đề chủ đề HĐTN trước khi làm tên bài."""
    s = _clean_hdtn_topic_text(text)
    s = re.sub(r"^CHỦ\s*ĐỀ\s*(\d+)\s*[\.:]?\s*", r"Chủ đề \1: ", s, flags=re.I)
    s = re.sub(r"^Chủ\s*đề\s*(\d+)\s*\.\s*", r"Chủ đề \1: ", s, flags=re.I)
    s = re.sub(r"^Chủ\s*đề\s*(\d+)\s*:\s*", r"Chủ đề \1: ", s, flags=re.I)
    s = re.sub(r"^(Chủ đề\s*\d+):\s*", lambda m: f"{m.group(1)}: ", s, flags=re.I)
    if ":" in s:
        prefix, rest = s.split(":", 1)
        rest = _compact_spaces(rest)
        letters = [ch for ch in rest if ch.isalpha()]
        if letters and sum(ch.isupper() for ch in letters) / max(len(letters), 1) > 0.7:
            rest = rest.lower()
            rest = rest[:1].upper() + rest[1:]
        s = f"{prefix.strip()}: {rest}".strip()
    return _compact_spaces(s)

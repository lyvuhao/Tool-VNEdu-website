"""Danh sách tool mặc định (script, tiêu đề, mô tả)."""

from __future__ import annotations


# "script":   file launcher nằm trong thư mục tool (thư mục làm việc).
# "packages": các package mà launcher cần (nằm cạnh launcher). Tool không có key này là
#             file .py đơn lẻ. Bản nhúng dự phòng của tool dạng package là bản đơn file cũ
#             (chỉ dùng khi thiếu file launcher), không bị ghi đè bằng nội dung launcher.
TOOL_FILES = {
    "nhapdiem": {
        "script": "nhapdiem_pro.py",
        "packages": ["nhapdiem", "nhanxet", "vnedu_common"],
        "title": "Nhập điểm giọng nói",
        "description": "Đọc Sổ điểm, nhận giọng nói và ghi điểm.",
    },
    "nhanxet": {
        "script": "nhanxet_pro.py",
        "packages": ["nhanxet", "vnedu_common"],
        "title": "Ghi nhận xét",
        "description": "Tạo nhận xét theo rule và ghi vào Sổ điểm.",
    },
    "locdiem": {
        "script": "locdiem.py",
        "title": "Lọc học lực",
        "description": "Lọc học sinh theo học lực.",
    },
    "sodaubai": {
        "script": "auto_SĐB.py",
        "packages": ["auto_sdb", "vnedu_common"],
        "title": "Sổ đầu bài",
        "description": "Nhập Chi tiết sổ đầu bài qua Chrome CDP.",
    },
}

# Dấu nhận diện file launcher (xem các file *_pro.py / auto_SĐB.py ở thư mục tool).
LAUNCHER_MARKER = b"VNEDU_TOOL_LAUNCHER"


def tool_packages(tool_name: str) -> list[str]:
    """Return the package folders a default tool's launcher needs."""

    return list(TOOL_FILES.get(tool_name, {}).get("packages", []))


def is_launcher_source(source: bytes) -> bool:
    """Return whether a script is a thin launcher (needs its packages next to it)."""

    return LAUNCHER_MARKER in source[:4096]

"""Danh sách tool mặc định (script, tiêu đề, mô tả)."""

from __future__ import annotations


TOOL_FILES = {
    "nhapdiem": {
        "script": "nhapdiem21.py",
        "title": "Nhập điểm giọng nói",
        "description": "Đọc Sổ điểm, nhận giọng nói và ghi điểm.",
    },
    "nhanxet": {
        "script": "nhanxet_pro.py",
        "title": "Ghi nhận xét",
        "description": "Tạo nhận xét theo rule và ghi vào Sổ điểm.",
    },
    "locdiem": {
        "script": "locdiem.py",
        "title": "Lọc học lực",
        "description": "Lọc học sinh theo học lực.",
    },
    "sodaubai": {
        "script": "auto_danang8.py",
        "title": "Sổ đầu bài",
        "description": "Nhập Chi tiết sổ đầu bài qua Chrome CDP.",
    },
}

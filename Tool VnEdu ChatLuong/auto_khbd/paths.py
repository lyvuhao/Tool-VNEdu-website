"""Đường dẫn gốc của tool."""

from pathlib import Path

# Thư mục chứa launcher (thư mục cha của package): nơi đặt file cấu hình, dữ liệu, ảnh hướng dẫn...
# (tương đương thư mục của file .py đơn lẻ trước khi tách module).
TOOL_DIR = Path(__file__).resolve().parent.parent

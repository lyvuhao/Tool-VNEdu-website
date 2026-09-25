"""VNEDU Nhập điểm giọng nói — launcher.

Mã nguồn nằm trong package `nhapdiem/` (dùng chung lõi Sổ điểm với package `nhanxet/`).
Chạy:  python nhapdiem_pro.py [--debug] [--config-file PATH] [--self-test]
hoặc   python -m nhapdiem ...
"""

import os
import sys

# Cho phép import các package nằm cạnh file này (kể cả khi chạy từ thư mục khác
# hoặc được control panel gọi qua runpy.run_path).
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

# Giữ tương thích với code cũ kiểu `from nhapdiem_pro import ...`
from nhapdiem.main import main  # noqa: E402
from nhapdiem.ui.app import VnEduStandaloneApp  # noqa: E402

__all__ = ["VnEduStandaloneApp", "main"]

if __name__ == "__main__":
    main()

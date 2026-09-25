"""VNEDU Auto Nhận Xét V2 — launcher.

Mã nguồn nằm trong package `nhanxet/` (xem README.md ở thư mục gốc repo).
Chạy:  python nhanxet_pro.py      hoặc   python -m nhanxet
"""

import os
import sys

# Cho phép import các package nằm cạnh file này (kể cả khi chạy từ thư mục khác
# hoặc được control panel gọi qua runpy.run_path).
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

# Giữ tương thích với code cũ kiểu `from nhanxet_pro import ...`
from nhanxet.automation.client import VnEduScoreAutomation  # noqa: E402
from nhanxet.main import main  # noqa: E402
from nhanxet.ui.app import AutoNhanXetV2App  # noqa: E402

__all__ = ["AutoNhanXetV2App", "VnEduScoreAutomation", "main"]

if __name__ == "__main__":
    main()

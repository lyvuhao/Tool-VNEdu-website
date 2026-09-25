"""Tool nhập KHDH VnEdu — launcher.

Mã nguồn nằm trong package `auto_khbd/`:
    auto_khbd/engine/  — nghiệp vụ (parser, analyzer, client, planner, profile, executor, backup)
    auto_khbd/ui/      — giao diện Tkinter (wizard, dialog, worker)
Chạy:  python auto_khbd_pro.py      hoặc   python -m auto_khbd
Import: from auto_khbd_pro import KHDHWizard
"""

import os
import sys

# Cho phép import các package nằm cạnh file này (kể cả khi chạy từ thư mục khác
# hoặc được control panel gọi qua runpy.run_path).
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

# Giữ tương thích với code cũ kiểu `from auto_khbd_pro import KHDHWizard`
from auto_khbd.main import main  # noqa: E402
from auto_khbd.ui.wizard.wizard import KHDHWizard  # noqa: E402

__all__ = ["KHDHWizard", "main"]

if __name__ == "__main__":
    main()

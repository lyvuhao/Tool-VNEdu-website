"""Auto Sổ đầu bài VnEdu (CDP) — launcher.

Mã nguồn nằm trong package `auto_sdb/`:
    auto_sdb/cdp/  — ChromeBridge (điều khiển Chrome qua CDP)
    auto_sdb/app/  — AutoDaNangApp (giao diện Tkinter)
Chạy:  python auto_SĐB.py      hoặc   python -m auto_sdb
"""

# VNEDU_TOOL_LAUNCHER: auto_sdb — file này chỉ là launcher, cần package `auto_sdb/` nằm cạnh.

import os
import sys

# Cho phép import các package nằm cạnh file này (kể cả khi chạy từ thư mục khác
# hoặc được control panel gọi qua runpy.run_path).
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

# Giữ tương thích với code cũ kiểu `from auto_SĐB import ...`
from auto_sdb.app.app import AutoDaNangApp  # noqa: E402
from auto_sdb.cdp.bridge import ChromeBridge  # noqa: E402
from auto_sdb.main import main  # noqa: E402

__all__ = ["AutoDaNangApp", "ChromeBridge", "main"]

if __name__ == "__main__":
    main()

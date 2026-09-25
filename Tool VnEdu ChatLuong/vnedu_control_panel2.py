"""VNEDU Control Panel — launcher.

Mã nguồn nằm trong package `control_panel/`.
Chạy:  python vnedu_control_panel2.py [--self-test] [--skip-login] [--tool TÊN] [--custom-tool ID]
hoặc   python -m control_panel ...

Lưu ý: control panel tự gọi lại file này (kèm `--tool ...`) để mở từng tool trong tiến trình riêng.
"""

# VNEDU_TOOL_LAUNCHER: control_panel — file này chỉ là launcher, cần package `control_panel/` nằm cạnh.

import os
import sys

# Cho phép import các package nằm cạnh file này (kể cả khi chạy từ thư mục khác
# hoặc được control panel gọi qua runpy.run_path).
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

# Giữ tương thích với code cũ kiểu `from vnedu_control_panel2 import ...`
from control_panel.main import main  # noqa: E402
from control_panel.ui.app import ControlPanelApp  # noqa: E402

__all__ = ["ControlPanelApp", "main"]

if __name__ == "__main__":
    raise SystemExit(main())

"""Logger dùng chung cho toàn bộ tool KHDH."""

from __future__ import annotations

import logging


# Logger module-level DUY NHẤT cho toàn bộ tool single-file.
# (#1) Trước đây `logger` bị gán đè 3 lần ở các section client/bootstrap/
# executor → vì là global rebind, mọi `logger.` tại runtime dùng binding
# CUỐI (executor) → log của client/bootstrap ghi sai tên. File không cấu
# hình handler riêng cho từng tên, nên 1 logger là đủ và đúng idiom.
logger = logging.getLogger("auto_khbd_pro")

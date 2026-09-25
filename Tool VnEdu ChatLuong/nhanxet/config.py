"""Hằng số cấu hình và đường dẫn file của tool Ghi nhận xét."""

from __future__ import annotations

import re
from typing import Callable

from .paths import TOOL_DIR


_SCRIPT_DIR = TOOL_DIR


CONFIG_FILE = _SCRIPT_DIR / "nhanxet_v2_config.json"


DEFAULT_RULE_EXPORT_DIR = _SCRIPT_DIR / "nhanxet_mac_dinh"


APP_TITLE = "AUTO Ghi Nhận Xét Học Sinh - Developed by Vu Hao"


WINDOW_SIZE = "1060x760"


NUMERIC_COMMENT_SCORE_RE = re.compile(r"^\s*(?:10(?:[.,]0+)?|[0-9](?:[.,]\d+)?)\s*$")


PASSIVE_SCAN_SKIPPED = object()


ProgressCallback = Callable[[float, str], None]

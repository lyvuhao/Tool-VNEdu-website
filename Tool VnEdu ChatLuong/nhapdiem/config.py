"""Hằng số ứng dụng: tiêu đề, đường dẫn file dữ liệu, màu giao diện, giới hạn."""

from __future__ import annotations

from typing import Callable

from .paths import TOOL_DIR


APP_TITLE = "Auto Nhập điểm bằng giọng nói"


APP_VERSION = "1.4.0"  # IMP-D4: Version number hiển thị trên title bar


WINDOW_SIZE = "1500x920"


CONFIG_FILE = TOOL_DIR / "vnedu_standalone_config.json"


ACCESS_CACHE_FILE = TOOL_DIR / "vnedu_access_cache.json"


ALIAS_FILE = TOOL_DIR / "student_aliases.json"


SNAPSHOT_FILE = TOOL_DIR / "vnedu_scorebook_snapshot.json"


PAYLOAD_FILE = TOOL_DIR / "vnedu_feature_payload.json"


ACCESS_CACHE_SCHEMA_VERSION = 1


ProgressCallback = Callable[[float, str], None]


AUDIO_SAMPLE_RATE = 16000


APP_BG = "#f4f7fb"


APP_PANEL_BG = "#ffffff"


APP_PANEL_BORDER = "#d7dee8"


APP_TEXT = "#172033"


APP_MUTED = "#64748b"


APP_PRIMARY = "#2563eb"


APP_SUCCESS = "#16a34a"


APP_WARNING = "#facc15"


APP_DANGER = "#dc2626"


APP_TABLE_HEADER = "#eaf1fb"


APP_TABLE_LINE = "#cbd5e1"


APP_COMPACT_WIDTH = 1440


APP_SIDEBAR_MIN_WIDTH = 420


APP_SIDEBAR_WIDTH = 520


# --- Bug-fix Constants ---
UNDO_STACK_MAX_SIZE = 200                # Giới hạn undo stack để tránh memory leak (BUG-03)


VOICE_MATCH_CACHE_MAX_SIZE = 500         # Giới hạn voice match cache (BUG-04)


LOG_MAX_LINES = 2000                     # Giới hạn dòng log hiển thị (BUG-12)


LOG_TRIM_LINES = 500                     # Số dòng xóa khi vượt LOG_MAX_LINES


MIC_CONSECUTIVE_ERROR_MAX = 3            # Tự tắt PTT sau N lần lỗi mic liên tiếp (IMP-C2)


VOICE_PENDING_CONFIDENCE_DECAY_RATE = 2.0  # Giảm confidence 2 điểm/giây kể từ pending (IMP-C8)

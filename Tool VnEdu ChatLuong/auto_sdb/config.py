"""Hằng số ứng dụng: file cấu hình/cache, kích thước cửa sổ, màu giao diện, chế độ lịch."""

from .compat import HAS_PLAYWRIGHT


_HAS_CDP = HAS_PLAYWRIGHT


# File cấu hình
CONFIG_FILE = "auto_danang_config.json"


CLASS_STATS_CACHE_FILE = "auto_danang_cache.json"


SCHEDULE_RESUME_FILE = "auto_danang_resume.json"


CLASS_STATS_CACHE_TTL_SECONDS = 6 * 3600


CLASS_STATS_CACHE_MAX_ENTRIES = 180


UI_TASK_POLL_MS = 50


CLOSE_GRACE_PERIOD_MS = 3500


# Kích thước cửa sổ
EXPANDED_WIDTH = 585            # Panel trái đủ rộng cho cụm nút CDP không bị chen


RIGHT_PANEL_WIDTH = 505         # Panel phải đủ rộng cho lịch + nút schedule


EXPANDED_TOTAL_WIDTH = EXPANDED_WIDTH + RIGHT_PANEL_WIDTH


EXPANDED_HEIGHT = 680           # Nội dung dài dùng scroll; không kéo cửa sổ xuống taskbar


COMPACT_WIDTH = 430


COMPACT_HEIGHT = 220


# UI tokens: giữ Tkinter/ttk nhưng chuẩn hóa lại hierarchy cho dễ đọc.
UI_BG_APP = "#f6f8fb"


UI_SURFACE = "#ffffff"


UI_SURFACE_ALT = "#f9fafb"


UI_BORDER = "#d1d5db"


UI_TEXT = "#111827"


UI_TEXT_MUTED = "#6b7280"


UI_PRIMARY = "#2563eb"


UI_PRIMARY_ACTIVE = "#1d4ed8"


UI_SUCCESS = "#16a34a"


UI_SUCCESS_ACTIVE = "#15803d"


UI_WARNING = "#d97706"


UI_DANGER = "#dc2626"


UI_DANGER_ACTIVE = "#b91c1c"


UI_DISABLED_BG = "#f3f4f6"


UI_DISABLED_TEXT = "#9ca3af"


UI_LOG_BG = "#111827"


UI_LOG_TEXT = "#d1d5db"


# Lịch dạy — danh sách ngày (Thứ 2→7 + CN)
# thu=8 đại diện cho Chủ nhật. Label hiển thị = "CN".
SCHEDULE_DAYS = [2, 3, 4, 5, 6, 7, 8]


SCHEDULE_DAY_LABELS = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "CN"}


SCHEDULE_MODE_MANUAL = "manual"


SCHEDULE_MODE_KHDH = "khdh"

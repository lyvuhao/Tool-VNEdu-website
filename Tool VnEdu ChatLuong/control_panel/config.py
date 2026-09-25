"""Hằng số: tiêu đề, URL, màu giao diện, tên file cấu hình, mutex."""

from __future__ import annotations


APP_TITLE = "VNEDU Control Panel"


APP_VERSION = "1.0.0"


DEFAULT_VNEDU_URL = "https://vemzezsoasgdsoctrang.vnedu.vn/v5/"


DEFAULT_DEBUG_PORT = 9224


UI_FONT_FAMILY = "Segoe UI"


UI_FONT_SEMIBOLD = "Segoe UI Semibold"


SURFACE_COLOR = "#f4f7fb"


PANEL_COLOR = "#ffffff"


BORDER_COLOR = "#d8e0ea"


CARD_BORDER_COLOR = "#b8c6d6"


TEXT_COLOR = "#111827"


MUTED_TEXT_COLOR = "#475569"


SUBTLE_TEXT_COLOR = "#64748b"


BUTTON_BORDER_COLOR = "#b8c2cc"


BUTTON_HOVER_COLOR = "#eef3f8"


TOOL_ACCENTS = {
    "nhapdiem": "#2563eb",
    "nhanxet": "#16a34a",
    "locdiem": "#d97706",
    "sodaubai": "#7c3aed",
}


ACCENT_CHOICES = {
    "Xanh dương": "#2563eb",
    "Xanh lá": "#16a34a",
    "Cam": "#d97706",
    "Tím": "#7c3aed",
    "Xanh ngọc": "#0891b2",
    "Hồng": "#db2777",
}


TOOL_STATUS_COLORS = {
    "external": ("Đang dùng: File ngoài", "#e8f5ee", "#166534"),
    "embedded": ("Đang dùng: Bản nhúng", "#eef2ff", "#3730a3"),
    "missing": ("Đang dùng: Thiếu", "#fee2e2", "#991b1b"),
}


EMBEDDED_STATUS_COLORS = {
    "ok": ("Nhúng: OK", "#ecfdf5", "#047857"),
    "missing": ("Nhúng: Thiếu", "#fff7ed", "#9a3412"),
}


RUNNING_STATUS_COLORS = {
    "running": ("Đang mở", "#e0f2fe", "#0369a1"),
    "idle": ("Sẵn sàng", "#f8fafc", "#475569"),
    "missing": ("Không rõ", "#fee2e2", "#991b1b"),
}


CUSTOM_TOOLS_CONFIG_NAME = "custom_tools.json"


DEFAULT_TOOL_OVERRIDES_CONFIG_NAME = "default_tool_overrides.json"


APP_CONFIG_NAME = "app_config.json"


CUSTOM_TOOLS_DIR_NAME = "custom_tools"


CUSTOM_TOOL_ID_PREFIX = "custom_"


VALID_DASHBOARD_MODES = {"basic", "advanced"}


HEALTH_CHECK_CACHE_SECONDS = 60.0


HEALTH_CHECK_DELAY_MS = 350


TOOL_PROCESS_POLL_MS = 1000


SINGLE_INSTANCE_MUTEX_NAME = "Local\\VNEDUControlPanelSingleInstance_v1"


TOOL_PROCESS_MUTEX_PREFIX = "Local\\VNEDUControlPanelTool_v1_"


ALLOW_MULTIPLE_ENV = "VNEDU_ALLOW_MULTIPLE"

"""ControlPanelApp — cửa sổ dashboard điều phối các tool."""

from __future__ import annotations

import atexit
import subprocess
import tkinter as tk
from tkinter import ttk

from ..config import APP_TITLE, APP_VERSION, DEFAULT_DEBUG_PORT, DEFAULT_VNEDU_URL
from ..custom_tools import load_custom_tools
from ..events import _EventBus
from ..storage import load_app_config
from .dashboard import DashboardMixin
from .health import HealthMixin
from .lifecycle import LifecycleMixin
from .maintenance import MaintenanceMixin
from .processes import ToolProcessesMixin
from .screens import ScreensMixin
from .tool_cards import ToolCardsMixin
from .tool_dialogs import ToolDialogsMixin
from .tool_manager import ToolManagerMixin
from .widgets import WidgetsMixin


class ControlPanelApp(
    WidgetsMixin,
    HealthMixin,
    ToolProcessesMixin,
    DashboardMixin,
    ScreensMixin,
    ToolDialogsMixin,
    ToolCardsMixin,
    ToolManagerMixin,
    MaintenanceMixin,
    LifecycleMixin,
):
    """Main dashboard that launches each VNEDU utility."""

    _active_scroll_canvas: tk.Canvas | None = None

    def __init__(
        self,
        root: tk.Tk,
        session: dict[str, object] | None = None,
        *,
        skip_login: bool = False,
    ) -> None:
        self.root = root
        self.session = session or {
            "username": "",
            "target_url": DEFAULT_VNEDU_URL,
            "debug_port": DEFAULT_DEBUG_PORT,
            "message": "Sẵn sàng đăng nhập.",
        }
        self.processes: list[subprocess.Popen[bytes]] = []
        self.tool_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._dashboard_cards_frame: ttk.Frame | None = None
        self._tool_process_poll_after_id: str | None = None
        self._running_tool_keys_snapshot: set[str] = set()
        self.skip_login = skip_login
        self._busy = False
        self.app_config = load_app_config()
        self.custom_tools = load_custom_tools()
        self.health_result: dict[str, object] = {}
        self._health_check_token = 0
        self._health_cache_at = 0.0
        self._health_cache_key = ""
        self._health_in_progress = False
        self._health_after_id: str | None = None
        self.bus = _EventBus(root)
        self._file_mtimes: dict[str, int] = {}
        self._prev_card_fingerprint: str = ""

        self.url_var = tk.StringVar(value=str(self.session.get("target_url") or DEFAULT_VNEDU_URL))
        self.port_var = tk.StringVar(value=str(self.session.get("debug_port") or DEFAULT_DEBUG_PORT))
        self.username_var = tk.StringVar(value=str(self.session.get("username") or ""))
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        initial_message = "Chế độ xem giao diện: chưa đăng nhập VNEDU." if skip_login else str(
            self.session.get("message") or "Sẵn sàng đăng nhập."
        )
        self.status_var = tk.StringVar(value=initial_message)
        self.smart_status_var = tk.StringVar(value="VNEDU: ĐANG KIỂM TRA | CDP: ĐANG KIỂM TRA | TOOL: ĐANG KIỂM TRA")
        self.session_caption_var = tk.StringVar()
        self.tool_filter_var = tk.StringVar()
        self.tool_count_var = tk.StringVar()
        self.advanced_mode_var = tk.BooleanVar(value=self.app_config.get("dashboard_mode") == "advanced")

        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        self.root.minsize(520, 320)
        if skip_login:
            self._build_dashboard_screen()
        else:
            self._build_login_screen()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        atexit.register(self._cleanup_all_children)

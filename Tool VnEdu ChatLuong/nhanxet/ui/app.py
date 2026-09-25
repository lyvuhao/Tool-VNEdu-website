"""Cửa sổ chính AutoNhanXetV2App (Tkinter)."""

from __future__ import annotations

import threading
import tkinter as tk
from queue import Queue
from typing import Callable, Dict, List, Tuple

from ..config import APP_TITLE, WINDOW_SIZE
from ..models import ScorebookAccessEntry, ScorebookContext
from .access_cache import AccessCacheMixin
from .apply import ApplyMixin
from .columns import ColumnsMixin
from .config import ConfigMixin
from .context import ContextMixin
from .layout import LayoutMixin
from .progress import ProgressMixin
from .rules import RulesMixin


class AutoNhanXetV2App(
    LayoutMixin,
    ProgressMixin,
    ColumnsMixin,
    RulesMixin,
    ApplyMixin,
    AccessCacheMixin,
    ConfigMixin,
    ContextMixin,
):
    """Tkinter skeleton for the Playwright/CDP-based VNEDU comment tool."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(980, 680)

        self.port_var = tk.StringVar(value="9224")
        self.url_var = tk.StringVar(value="https://vemzezsoasgdsoctrang.vnedu.vn/v5/")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Chưa kết nối")

        self.grade_var = tk.StringVar()
        self.class_var = tk.StringVar()
        self.subject_var = tk.StringVar()
        self.term_var = tk.StringVar()
        self.detected_score_var = tk.StringVar(value="(chưa dò)")
        self.detected_comment_var = tk.StringVar(value="(chưa dò)")
        self.detected_candidates_var = tk.StringVar(value="")
        self.detected_reason_var = tk.StringVar(value="")
        self.score_source_var = tk.StringVar(value="(chưa dò)")
        self.num_forms_var = tk.StringVar(value="7")
        self.auto_save_var = tk.BooleanVar(value=True)
        self.allow_comment_overwrite_var = tk.BooleanVar(value=False)

        self.grade_label_to_id: Dict[str, str] = {}
        self.class_label_to_id: Dict[str, str] = {}
        self.subject_label_to_id: Dict[str, str] = {}
        self.term_label_to_id: Dict[str, str] = {}
        self.score_source_label_to_key: Dict[str, str] = {}
        self.current_context: ScorebookContext | None = None
        self.score_forms: List[Dict[str, tk.StringVar]] = []
        self.access_entry_cache: Dict[Tuple[str, str, str, str], List[ScorebookAccessEntry]] = {}
        self._access_scan_inflight: set[Tuple[str, str, str, str]] = set()
        self._access_cache_identity: Tuple[str, str] | None = None
        self._matrix_access_scan_enabled = False
        self.preferred_score_source_key = ""
        self._suspend_context_events = False
        self._suspend_config_autosave = True
        self._config_autosave_after_id: str | None = None
        self._rule_busy_widgets: List[tk.Misc] = []
        self._log_history: List[str] = []
        self._progress_value = 0.0
        self._progress_message = "Sẵn sàng"

        self._busy = False
        self._foreground_task_token = 0
        self._context_apply_after_id: str | None = None
        self._context_apply_delay_ms = 350
        self._poll_after_id: str | None = None
        self._automation_lock = threading.Lock()
        self._passive_scan_delay_ms = 900
        self._result_queue: Queue[
            Tuple[
                object | None,
                Exception | None,
                Callable[[object], None],
                Callable[[Exception], None] | None,
            ]
        ] = Queue()
        self._passive_result_queue: Queue[
            Tuple[
                object | None,
                Exception | None,
                Callable[[object], None],
                Callable[[Exception], None] | None,
            ]
        ] = Queue()
        self._progress_queue: Queue[Tuple[int, float, str]] = Queue()
        self._busy_widgets: List[Tuple[tk.Misc, str]] = []

        self._build_ui()
        self._load_config()
        self._sync_access_cache_identity()
        self._bind_config_autosave_traces()
        self._suspend_config_autosave = False
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self._poll_after_id = self.root.after(50, self._poll_background_results)

"""Cửa sổ chính VnEduStandaloneApp (Tkinter)."""

from __future__ import annotations

import threading
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from tkinter import ttk
from typing import Any, Callable, Dict

from ..config import APP_BG, APP_TITLE, APP_VERSION
from ..models import ScoreStudentRow, UndoRecord
from ..scorebook_core import ScorebookContext
from ..voice.audio import PTTCaptureStream
from ..voice.constants import VOICE_HINT_RECENT_LIMIT
from .access_cache import AccessCacheMixin
from .aliases import AliasMixin
from .apply_scores import ApplyScoresMixin
from .context import ContextMixin
from .editing import EditingMixin
from .excel_import import ExcelImportMixin
from .export import ExportMixin
from .layout import LayoutMixin
from .progress import ProgressMixin
from .ptt import PushToTalkMixin
from .score_table import ScoreTableMixin
from .scorebook import ScorebookLoadMixin
from .settings import SettingsMixin
from .voice_matching import VoiceMatchingMixin
from .voice_recognition import VoiceRecognitionMixin
from .voice_state import VoiceStateMixin
from .window import fit_geometry_to_work_area, get_tk_work_area


class VnEduStandaloneApp(
    LayoutMixin,
    ProgressMixin,
    ContextMixin,
    AccessCacheMixin,
    SettingsMixin,
    AliasMixin,
    ScorebookLoadMixin,
    ScoreTableMixin,
    ApplyScoresMixin,
    EditingMixin,
    VoiceStateMixin,
    PushToTalkMixin,
    VoiceRecognitionMixin,
    VoiceMatchingMixin,
    ExportMixin,
    ExcelImportMixin,
):
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")  # IMP-D4
        self.root.configure(background=APP_BG)
        target_w, target_h, target_x, target_y = fit_geometry_to_work_area(
            self.root,
            preferred_w=1500,
            preferred_h=920,
            margin=12,
            min_w=1040,
            min_h=660,
            chrome_h=52,
        )
        self.root.geometry(f"{target_w}x{target_h}+{target_x}+{target_y}")
        self.root.minsize(min(1120, target_w), min(700, target_h))
        _work_x, _work_y, _work_w, _work_h = get_tk_work_area(self.root)
        self.root.maxsize(_work_w, _work_h)

        self.port_var = tk.StringVar(value="9224")
        self.url_var = tk.StringVar(value="https://vemzezsoasgdsoctrang.vnedu.vn/v5/")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Chưa kết nối")
        self.feature_name_var = tk.StringVar(value="PTT nhập điểm")

        self.grade_var = tk.StringVar(value="(chưa đọc)")
        self.class_var = tk.StringVar(value="(chưa đọc)")
        self.subject_var = tk.StringVar(value="(chưa đọc)")
        self.term_var = tk.StringVar(value="(chưa đọc)")
        self.window_title_var = tk.StringVar(value="(chưa có)")
        self.teacher_var = tk.StringVar(value="(chưa có)")
        self.permission_var = tk.StringVar(value="(chưa có)")
        self.column_count_var = tk.StringVar(value="0")
        self.comment_input_var = tk.StringVar(value="0")

        self.target_score_column_var = tk.StringVar(value="(chưa dò)")
        self.auto_scan_rows_var = tk.BooleanVar(value=True)  # always True (auto-apply on combo change)
        self.auto_save_scores_var = tk.BooleanVar(value=True)
        self.voice_status_var = tk.StringVar(value="⏸️ Bộ đàm đang tắt")
        self.voice_summary_var = tk.StringVar(value="Hãy load Sổ điểm rồi chọn cột điểm đích để bắt đầu.")

        self.grade_label_to_id: Dict[str, str] = {}
        self.class_label_to_id: Dict[str, str] = {}
        self.subject_label_to_id: Dict[str, str] = {}
        self.term_label_to_id: Dict[str, str] = {}
        self.target_score_label_to_key: Dict[str, str] = {}
        self.current_context: ScorebookContext | None = None
        self._full_access_scope_cache: dict[tuple[str, str], list[object]] = {}
        self._subject_access_scope_cache: dict[tuple[str, str, str], list[object]] = {}
        self._empty_access_scope_target_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self._loaded_access_cache_namespace = ""
        self._suspend_context_autosync = False
        self._context_autosync_delay_ms = 650
        self._context_autosync_after_id: str | None = None
        self._last_applied_context_ids: tuple[str, str, str, str] = ("", "", "", "")
        self._invalidated_context_fields: set[str] = set()
        self._suspend_target_score_autoscan = False
        self._last_scanned_target_key = ""
        self._auto_scan_after_id: str | None = None
        self._retry_apply_after_scan = False
        self._repair_scan_backup_rows: dict[str, ScoreStudentRow] | None = None
        self._repair_scan_backup_undo: list[UndoRecord] | None = None

        self._busy = False
        self._busy_widgets: list[tuple[tk.Misc, str]] = []
        self._foreground_task_token = 0
        self._progress_value = 0.0
        self._progress_message = "Sẵn sàng"
        self._automation_lock = threading.Lock()
        self._score_data_lock = threading.Lock()  # BUG-01 FIX: bảo vệ _score_rows_by_key + indices giữa main thread và PTT worker
        self._voice_state_lock = threading.Lock()
        # THREAD-SAFETY INVARIANTS:
        # - _score_data_lock bảo vệ: _score_rows_by_key, _student_*_index, _voice_match_cache
        # - Mọi READ/WRITE từ PTT worker thread PHẢI acquire lock trước
        # - Main thread PHẢI acquire lock khi WRITE (clear, replace, rebuild indices)
        # - Main thread read-only (UI callbacks) được bảo vệ bởi GIL cho atomic reads
        self._progress_queue: Queue[tuple[int, float, str]] = Queue()
        self._result_queue: Queue[
            tuple[object | None, Exception | None, Callable[[object], None], Callable[[Exception], None] | None]
        ] = Queue()
        self._poll_after_id: str | None = None

        self._score_rows_by_key: dict[str, ScoreStudentRow] = {}
        self._tree_item_by_key: dict[str, str] = {}
        self._student_full_index: dict[str, list[str]] = {}
        self._student_last_name_index: dict[str, list[str]] = {}
        self._student_last_two_index: dict[str, list[str]] = {}
        self._student_token_index: dict[str, set[str]] = {}
        self._student_phonetic_full_index: dict[str, list[str]] = {}
        self._student_phonetic_last_name_index: dict[str, list[str]] = {}
        self._student_phonetic_last_two_index: dict[str, list[str]] = {}
        self._student_phonetic_token_index: dict[str, set[str]] = {}
        self._undo_stack: list[UndoRecord] = []
        self._tree_editor: ttk.Entry | None = None
        self._tree_editor_info: dict[str, str] = {}

        self._voice_hints: list[str] = []
        self._voice_hints_primary: list[str] = []
        self._voice_hints_extended: list[str] = []
        self._voice_recent_names: deque[str] = deque(maxlen=VOICE_HINT_RECENT_LIMIT)  # PERF #4: LRU tên dùng gần đây
        self._voice_match_cache: dict[str, tuple[str, int]] = {}
        self._student_aliases: dict[str, list[str]] = {}  # student_name → [alias1, alias2, ...]
        self._recognizer: Any | None = None
        self._recognizer_phrase_list_supported: bool | None = None
        self._ptt_capture: PTTCaptureStream | None = None
        self._space_pressed = False
        self._ptt_enabled = False
        self._ptt_recording = False
        self._ptt_audio_buffer: Any | None = None
        self._ptt_lock = threading.Lock()
        self._ptt_queue: Queue[tuple[int, int, Any, float] | None] = Queue()
        self._ptt_worker: threading.Thread | None = None
        self._ptt_worker_shutdown = False
        self._ptt_request_seq = 0
        self._ptt_latest_request_id = 0
        self._mic_consecutive_errors = 0  # IMP-C2: Đếm lỗi mic liên tiếp
        self._ptt_roster_revision = 0
        self._ptt_bind_ids: dict[str, tuple[str, str]] = {}
        # PERF #1: Pool 2 thread để chạy 2 attempt Google Speech song song.
        self._voice_recognize_executor: ThreadPoolExecutor | None = None
        self._voice_executor_lock = threading.Lock()
        self._voice_meter_level = 0.0
        self._voice_meter_peak = 0.0
        self._voice_pending_row_key: str | None = None
        self._voice_pending_at: float = 0.0
        self._voice_pending_roster_revision: int = 0
        # KHMER #B: Voice picker state cho cặp tên trùng phonetic (Trâm/Trăm).
        # Lưu danh sách ứng viên khi `_match_student` phát hiện tied; PTT tiếp theo
        # user nói "một"/"hai"/"ba"... để chọn từ list này.
        self._voice_tied_candidates: list[str] = []
        self._voice_tied_at: float = 0.0
        self._voice_tied_revision: int = 0
        # L3 FIX: chặn PTT result mới khi dialog xung đột điểm đang mở (dialog
        # grab_set nhưng không set _busy, nên cần cờ riêng để không apply chồng
        # hoặc mở dialog thứ hai từ một kết quả PTT đã queue trước đó).
        self._voice_conflict_dialog_open = False
        self._compact_layout = False

        self._build_ui()
        self._load_config()
        self._load_aliases()
        self._ensure_access_cache_loaded()
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self.root.bind("<Control-z>", self._on_undo_shortcut, add="+")
        # IMP-B1: Ctrl+S shortcut để ghi điểm lên web
        self.root.bind("<Control-s>", lambda _e: self.on_apply_pending_scores(), add="+")
        # IMP-B2: Escape hủy recording khi đang giữ Space
        self.root.bind("<Escape>", self._on_escape_cancel_recording, add="+")
        # IMP-D6: F1 hiện dialog phím tắt
        self.root.bind("<F1>", self._show_help_dialog, add="+")
        # Theo dõi trạng thái cửa sổ để ẩn/hiện left panel khi Restore Down / Maximize
        self._prev_window_state = "normal"
        self.root.bind("<Configure>", self._on_window_state_change, add="+")
        self._poll_after_id = self.root.after(50, self._poll_results)
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.after_idle(self._sync_responsive_layout)

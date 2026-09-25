"""AutoDaNangApp — cửa sổ chính của tool nhập Sổ đầu bài."""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from ..cdp.config import DEFAULT_CDP_PORT
from ..config import SCHEDULE_DAYS, SCHEDULE_MODE_MANUAL, UI_BG_APP, UI_TASK_POLL_MS
from .chrome import ChromeConnectionMixin
from .class_stats_dialog import ClassStatsDialogMixin
from .class_stats_render import ClassStatsRenderMixin
from .class_stats_run import ClassStatsRunMixin
from .class_stats_workers import ClassStatsWorkersMixin
from .delete_dialog import DeleteDialogMixin
from .inspect import InspectMixin
from .layout import LayoutMixin
from .quick_actions import QuickActionsMixin
from .schedule_form import ScheduleFormMixin
from .schedule_form_state import ScheduleFormStateMixin
from .schedule_panel import SchedulePanelMixin
from .schedule_resume import ScheduleResumeMixin
from .schedule_run import ScheduleRunMixin
from .schedule_state import ScheduleStateMixin
from .schedule_worker import ScheduleWorkerMixin
from .schedule_worker_khdh import ScheduleWorkerKHDHMixin
from .scroll import ScrollMixin
from .settings import SettingsMixin
from .styles import StylesMixin
from .teacher_progress import TeacherProgressMixin
from .toolbar_log import ToolbarLogMixin
from .window import WindowMixin


class AutoDaNangApp(
    StylesMixin,
    LayoutMixin,
    SchedulePanelMixin,
    TeacherProgressMixin,
    ScheduleFormMixin,
    ScheduleStateMixin,
    ScheduleResumeMixin,
    ScheduleFormStateMixin,
    ClassStatsDialogMixin,
    DeleteDialogMixin,
    ClassStatsRunMixin,
    ClassStatsWorkersMixin,
    ClassStatsRenderMixin,
    ToolbarLogMixin,
    ChromeConnectionMixin,
    InspectMixin,
    QuickActionsMixin,
    ScheduleRunMixin,
    ScheduleWorkerKHDHMixin,
    ScheduleWorkerMixin,
    ScrollMixin,
    SettingsMixin,
    WindowMixin,
):
    """GUI Tkinter cho app nhập liệu VnEdu theo Chrome CDP."""

    def __init__(self):
        """Khởi tạo app: state → GUI → load config."""

        # ===== State variables =====

        # UI state
        self.is_compact = False

        # ===== CDP State (Chrome DevTools Protocol) =====
        self._cdp_connected = False
        self._cdp_port = DEFAULT_CDP_PORT

        # ===== Root window =====
        self.root = tk.Tk()
        self.root.title("Sổ đầu bài tự động")
        self.root.configure(bg=UI_BG_APP)
        self.root.resizable(True, True)
        self.style = ttk.Style(self.root)
        self._setup_styles()
        self._init_schedule_checkbox_images()

        # ===== Tkinter variables (phải tạo sau root) =====
        self.var_cdp_port = tk.IntVar(value=DEFAULT_CDP_PORT)

        # ===== Schedule Panel State (Lịch dạy) =====
        self._schedule_thread = None
        self._schedule_queue = queue.Queue()
        self._schedule_running = False
        self._schedule_stop_event = threading.Event()
        self._schedule_stop_reason = None
        self._schedule_results = []
        self._schedule_resume_state = None
        self._schedule_resume_params = None
        self._schedule_resume_dirty = False
        self._schedule_last_success_ppct = None
        self._schedule_next_ppct = None
        self._schedule_last_summary = None
        self._schedule_summary_window = None
        self._class_stats_thread = None
        self._class_stats_queue = queue.Queue()
        self._class_stats_running = False
        self._auto_login_thread = None
        self._auto_login_running = False
        self._class_stats_dialog = None
        self._class_stats_status_label = None
        self._class_stats_result_text = None
        self._class_stats_buttons_frame = None
        self._class_stats_buttons = {}
        self._class_stats_mon_hoc_options = []
        self._class_stats_lop_records = []
        self.cmb_stats_mon_hoc = None
        self._class_stats_lop_options = []
        self._class_stats_lop_records = []
        self._class_stats_tuan_options = []
        self._class_stats_selected_lop = None
        self._class_stats_fetch_cache = {}
        self._class_stats_fetch_cache_saved_at = {}
        self._class_stats_fetch_cache_lock = threading.Lock()
        self._quick_prepare_running = False
        # ===== Delete (Xóa dữ liệu sổ đầu bài) state =====
        self._delete_thread = None
        self._delete_queue = queue.Queue()
        self._delete_running = False
        self._delete_stop_event = threading.Event()
        self._delete_dialog = None
        self._delete_status_label = None
        self._delete_result_text = None
        self._delete_run_button = None
        self._delete_stop_button = None
        self._delete_scan_button = None
        self._delete_class_combo = None
        self._delete_scanned_entries = []
        self._delete_scanning = False
        # Khóa ngữ cảnh của lần scan gần nhất để chống xóa lệch khi user đổi
        # Lớp / khoảng Tuần / filter sau khi đã quét.
        self._delete_scan_signature = None
        self.var_delete_tuan_from = tk.IntVar(value=1)
        self.var_delete_tuan_to = tk.IntVar(value=1)
        self.var_delete_lop = tk.StringVar(value="")
        self.var_delete_only_mine = tk.BooleanVar(value=True)
        self.var_delete_confirm = tk.StringVar(value="")
        self.var_stats_tuan_from = tk.IntVar(value=1)
        self.var_stats_tuan_to = tk.IntVar(value=1)
        self.var_stats_mon_hoc = tk.StringVar(value="")
        self.var_vnedu_username = tk.StringVar(value="")
        self.var_vnedu_password = tk.StringVar(value="")
        self.var_show_vnedu_password = tk.BooleanVar(value=False)

        # Schedule Tkinter vars — Tuần range + Lớp (single select)
        self.var_sched_tuan_from = tk.IntVar(value=1)
        self.var_sched_tuan_to = tk.IntVar(value=1)
        self.var_sched_lop = tk.StringVar(value="")
        self.var_sched_lop_multi = tk.StringVar(value="")
        # Schedule grid vars — 7 ngày (Thứ 2-7 + CN) × 2 buổi × 5 tiết = 70 checkboxes
        # dict key = (thu, buoi) → list[BooleanVar] cho tiết 1-5
        # thu: 2,3,4,5,6,7,8(CN)  buoi: "S" (Sáng), "C" (Chiều)
        self._sched_grid = {}    # {(thu, buoi): [BooleanVar × 5]}
        self._sched_buoi = {}    # {thu: StringVar} — buổi cho mỗi thứ
        for thu in SCHEDULE_DAYS:
            self._sched_buoi[thu] = tk.StringVar(value="---")
            for buoi_code in ("S", "C"):
                self._sched_grid[(thu, buoi_code)] = [
                    tk.BooleanVar(value=False) for _ in range(5)
                ]
        self._sched_checkbuttons = {}    # {(thu, tiet_idx): Checkbutton widget}

        # Schedule nhập liệu vars — riêng cho CDP fill_form
        # Lưu text hiển thị trên combobox; value thực được resolve từ cache options.
        self.var_sched_mode = tk.StringVar(value=SCHEDULE_MODE_MANUAL)
        self.var_sched_phan_mon = tk.StringVar(value="")
        self.var_sched_mon_hoc = tk.StringVar(value="")
        self.var_sched_ppct_start = tk.IntVar(value=1)    # Tiết PPCT bắt đầu
        self.var_sched_hs_nghi = tk.StringVar(value="0")
        self.var_sched_diem = tk.StringVar(value="10")
        self.var_sched_nhan_xet = tk.StringVar(value="Lớp học chăm ngoan")
        self.var_sched_ppct_runtime = tk.StringVar(value="")
        self.var_sched_teacher_progress_status = tk.StringVar(
            value="Quét dữ liệu lớp/tuần rồi chọn lớp để xem tiến độ PPCT các môn bạn đang dạy."
        )
        self.var_sched_teacher_progress_button = tk.StringVar(
            value="Xem tiến độ PPCT môn đang dạy"
        )
        self.var_sched_teacher_progress_fast_mode = tk.BooleanVar(value=False)
        self.var_sched_live_progress = tk.StringVar(value="● Tiến trình live: chưa có tác vụ nào chạy")
        # Lưu danh sách options đã fetch từ CDP
        self._sched_phan_mon_options = []  # [{value, text}, ...]
        self._sched_phan_mon_by_mon_hoc = {}  # {mon_hoc_value: [{value, text}, ...]}
        self._sched_mon_hoc_options = []   # [{value, text}, ...]
        self._sched_mon_hoc_field = None   # Tên field Môn học trên VnEdu
        self._sched_form_options_cache = {}
        self._sched_form_options_cache_lock = threading.Lock()
        self._sched_form_session_id = 0
        self._sched_form_scan_session_id = None
        self._sched_form_scan_context_key = None
        self._sched_form_rescan_reason = ""
        self._sched_form_last_announced_issue = ""
        self._sched_teacher_progress_after_id = None
        self._sched_teacher_progress_prewarm_after_id = None
        self._sched_teacher_progress_request_id = 0
        self._sched_teacher_progress_payload = None
        self._sched_teacher_progress_window = None
        self._sched_teacher_progress_text = None
        self._sched_teacher_progress_latest_week = None
        self._closing = False
        self._close_started_at = None
        self._ui_task_queue = queue.Queue()
        self._ui_task_pump_after_id = None

        # ===== Build UI =====
        self._setup_ui()
        self._ui_task_pump_after_id = self.root.after(UI_TASK_POLL_MS, self._pump_ui_tasks)

        # ===== Load config =====
        self._load_config()
        self._apply_sched_mode_state()
        self._update_sched_ppct_runtime(
            last_success=(
                self._schedule_resume_state.get("last_success_ppct")
                if self._schedule_resume_state else None
            ),
            next_ppct=(
                self._schedule_resume_state.get("next_ppct", self.var_sched_ppct_start.get())
                if self._schedule_resume_state else self.var_sched_ppct_start.get()
            ),
            status="paused" if self._schedule_resume_state else "idle",
        )
        self._set_schedule_button_states(
            running=False,
            can_resume=bool(self._schedule_resume_state),
        )
        self._set_auto_login_button_state()
        self._set_sched_live_progress(
            current=0,
            total=1,
            phase="Tiến trình live",
            detail="Chưa có tác vụ nào chạy",
            state="idle",
        )

        # ===== Window position =====
        self._position_window()
        self.root.after(250, lambda: self._ensure_window_visible(force=True))
        self.root.after(1200, lambda: self._ensure_window_visible(force=True))

        # ===== Save on close =====
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind_all("<Escape>", self._on_hotkey_escape, add="+")

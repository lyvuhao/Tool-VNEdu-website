"""KHDHWizard — cửa sổ chính của tool KHDH."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from ...engine.bootstrap import BootstrapData
from ...engine.profile.models import SlotEntry
from ...engine.profile.profile import KHDHProfile
from ..dialogs.progress_overlay import ProgressOverlayWindow
from ..styles import apply_wizard_styles
from ..theme import DEFAULT_CDP_PORT
from ..workers.detect_ppct import DetectPPCTWorker
from ..workers.executor import ExecutorWorker
from ..workers.login import LoginWorker
from ..workers.tkb_scan import HealthScanWorker
from .connect import ConnectMixin
from .delete_weeks import DeleteWeeksMixin
from .guards import WorkerGuardMixin
from .layout import WizardLayoutMixin
from .ppct import PPCTMixin
from .profile import ProfileMixin
from .refresh_fallback import RefreshFallbackMixin
from .run import RunMixin
from .sections import SectionsMixin
from .slot_clipboard import SlotClipboardMixin
from .slot_grid import SlotGridMixin
from .state import StateMixin
from .templates import TemplatesMixin

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..advanced_window import AdvancedWindow
    from ..backup_restore.dialog import BackupRestoreDialog
    from ..dialogs.huong_dan_chi_tiet import HuongDanChiTietDialog
    from ..dialogs.import_tkb import ImportTKBDialog
    from ..full_tkb_editor.window import FullTKBEditorWindow
    from ..workers.delete_weeks import DeleteWeeksWorker
    from ..workers.fill_titles import FillMissingHDTNTitlesWorker
    from ..workers.refresh_fallback import RefreshFallbackWorker


class KHDHWizard(
    WorkerGuardMixin,
    WizardLayoutMixin,
    SectionsMixin,
    StateMixin,
    TemplatesMixin,
    SlotGridMixin,
    SlotClipboardMixin,
    PPCTMixin,
    ConnectMixin,
    ProfileMixin,
    RunMixin,
    RefreshFallbackMixin,
    DeleteWeeksMixin,
    ttk.Frame,
):
    """Main view của tool nhập KHDH.

    Embed dạng `ttk.Frame` vào parent (window/notebook). 1 màn hình tổng
    chứa 6 vùng dọc: Header, Hồ sơ+Kết nối, TKB grid, PPCT, Dải tuần,
    Bắt đầu+Tiến độ.
    """

    # Recent files config
    RECENT_FILES_PATH = Path.home() / ".khdh_wizard_recent.json"

    MAX_RECENT_FILES = 5

    def __init__(self, parent, port: int = DEFAULT_CDP_PORT):
        super().__init__(parent, padding=10, style="Wiz.TFrame")
        apply_wizard_styles(ttk.Style(self))

        # ---- State ----
        self.port = port
        self.profile: KHDHProfile | None = None
        self.bootstrap_data: BootstrapData | None = None
        self.ctx_info = None
        self._profile_path: Path | None = None
        self._is_dirty = False

        # Workers
        self._bootstrap_worker: LoginWorker | None = None
        self._bootstrap_queue: queue.Queue = queue.Queue()
        self._exec_worker: ExecutorWorker | None = None
        self._exec_queue: queue.Queue = queue.Queue()
        self._exec_stop_event = threading.Event()
        # Detect PPCT worker (quét nhanh để tìm PPCT max)
        self._detect_worker: DetectPPCTWorker | None = None
        self._detect_queue: queue.Queue = queue.Queue()
        self._detect_stop = threading.Event()
        # Health scan worker (quét tình trạng KHDH trong scope user)
        self._health_worker: HealthScanWorker | None = None
        self._health_queue: queue.Queue = queue.Queue()
        self._health_stop = threading.Event()
        # Refresh fallback worker (cập nhật tên bài đã chèn dấu cách)
        self._refresh_fb_worker: "RefreshFallbackWorker | None" = None
        self._refresh_fb_queue: queue.Queue = queue.Queue()
        self._refresh_fb_stop_event = threading.Event()
        self._refresh_fb_processed_count: int = 0
        self._fill_titles_worker: "FillMissingHDTNTitlesWorker | None" = None
        self._fill_titles_queue: queue.Queue = queue.Queue()
        self._fill_titles_stop_event = threading.Event()
        self._fill_titles_processed_count: int = 0
        # Delete weeks worker (xóa tuần KHDH — siết logic chống nhầm cấp THCS)
        self._delete_worker: "DeleteWeeksWorker | None" = None
        self._delete_queue: queue.Queue = queue.Queue()
        self._delete_stop_event = threading.Event()
        # Import TKB dialog tracking — dialog tự manage worker, wizard chỉ
        # cần handle khi dialog đóng để check imported result.
        self._import_tkb_dialog: "ImportTKBDialog | None" = None
        # Full-TKB editor singleton (chỉ cho phép mở 1 cửa sổ tại 1 lúc)
        self._full_tkb_window: "FullTKBEditorWindow | None" = None
        # Backup/Restore dialog singleton — tránh mở 2 cùng lúc
        self._backup_restore_dialog: "BackupRestoreDialog | None" = None
        # HDSD dialog singleton — tránh mở chồng nhiều cửa sổ hướng dẫn
        self._help_dialog: "HuongDanChiTietDialog | None" = None
        self._poll_after_id: str | None = None

        # Executor run state — khai báo tường minh tránh dynamic attribute
        self._halted_during_run: bool = False
        self._exec_completed_count: int = 0

        # After IDs — track để cancel khi destroy
        self._pending_after_ids: list[str] = []

        # Suppress dirty mark khi đang load profile (tránh trace fire false dirty)
        self._loading_profile: bool = False

        # Track AdvancedWindow đang mở (singleton — tránh mở 2 cửa sổ song song)
        self._advanced_window: "AdvancedWindow | None" = None

        # ---- Tk vars ----
        self.var_port = tk.IntVar(value=port)
        self.var_username = tk.StringVar(value="")
        self.var_password = tk.StringVar(value="")
        self.var_user_info = tk.StringVar(value="(chưa đăng nhập)")
        # Hiển thị tên file hồ sơ đang mở (hoặc "(chưa lưu)") để user
        # dễ kiểm soát đang chỉnh sửa file nào trước khi bấm Lưu/Lưu mới.
        self.var_profile_label = tk.StringVar(value="📁 (chưa lưu)")
        self.var_connect_status = tk.StringVar(value="○ Chưa đăng nhập VnEdu")
        self.var_tach_le_chan = tk.BooleanVar(value=False)
        self.var_active_tab = tk.StringVar(value="chinh")  # "chinh" | "le" | "chan"
        self.var_tuan_from = tk.IntVar(value=1)
        self.var_tuan_to = tk.IntVar(value=35)
        self.var_progress = tk.IntVar(value=0)
        self.var_progress_text = tk.StringVar(value="")
        self.var_status = tk.StringVar(value="")
        self.var_summary = tk.StringVar(value="Hãy nhập tài khoản VnEdu và bấm [Đăng nhập].")
        self.var_holiday_summary = tk.StringVar(
            value="Chưa khai báo Nghỉ/Dạy bù."
        )

        # Fallback dấu cách cho ô Tên bài dạy khi web không tự sinh tên bài
        # (PPCT vượt phân phối chương trình, hoặc phân môn không có CSDL).
        # Default OFF — người dùng chủ động bật mỗi phiên. Lock theo phiên:
        # giá trị được snapshot khi bấm RUN, không đọc lại UI giữa chừng.
        self.var_ten_bai_fallback = tk.BooleanVar(value=False)
        # Số ô đang chờ cập nhật trong fallback log (refresh khi mở wizard
        # và sau mỗi RUN / Refresh).
        self.var_fallback_pending_count = tk.IntVar(value=0)
        self.var_fallback_button_text = tk.StringVar(
            value="🔄 Cập nhật Tên bài đã fallback"
        )

        # v2: Floating progress overlay (góc dưới-phải, always-on-top).
        # Default ON — user có thể tắt bằng checkbox trong run section.
        self.var_show_overlay = tk.BooleanVar(value=True)
        self._progress_overlay: ProgressOverlayWindow | None = None
        # Counters cho overlay (cập nhật theo mỗi event)
        self._overlay_done_count = 0
        self._overlay_error_count = 0
        self._overlay_extras_count = 0
        self._overlay_skipped_count = 0
        self._overlay_total_weeks = 0
        # Reference tới executor đang chạy (set bởi ExecutorWorker callback)
        # — dùng cho _refresh_ppct_table đọc _last_scan_progress.
        self._executor = None

        # Init profile rỗng
        self.profile = KHDHProfile.empty(
            ho_ten_gv="(chưa nhập)", nam_hoc=2025, cap_hoc=2,
            cap_hoc_text="THCS",
        )
        # Trace dirty
        self.var_tach_le_chan.trace_add("write", lambda *_: self._on_tach_changed())
        self.var_tuan_from.trace_add("write", lambda *_: (self._mark_dirty(), self._refresh_holiday_summary()))
        self.var_tuan_to.trace_add("write", lambda *_: (self._mark_dirty(), self._refresh_holiday_summary()))

        # Internal — track widgets cần update
        self._slot_buttons: dict[str, tk.Widget] = {}  # f"{which}|{thu}_{buoi}_{tiet}"

        # Slot grid interaction state (copy/paste/drag/select).
        # Key format giống _slot_buttons: f"{thu}_{buoi}_{tiet}" (KHÔNG có
        # prefix `which` — _slot_buttons được rebuild khi switch tab).
        self._selected_slot_key: str | None = None
        self._slot_clipboard: SlotEntry | None = None
        # _drag_state là dict {source_key, start_x, start_y, started}.
        # `started=False` tới khi mouse di chuyển vượt SLOT_DRAG_THRESHOLD_PX.
        self._drag_state: dict | None = None
        # Single instance Menu để tránh leak Tk objects.
        self._slot_context_menu: tk.Menu | None = None
        # Track funcid của các keyboard binding để unbind khi destroy
        # (tránh stale handler gọi vào instance đã chết khi user mở lại app).
        # Format: list[tuple[sequence, funcid]]
        self._kb_bound_funcids: list[tuple[str, str]] = []

        self._tab_main = None  # Notebook khi tách lẻ/chẵn

        self._build_ui()
        self._refresh_grid()
        self._refresh_ppct_table()
        self._refresh_holiday_summary()
        self._refresh_fallback_button_state()
        self._refresh_profile_label()

        # Bind Esc to stop
        self.bind_all("<Escape>", self._on_esc, add="+")

        # Slot grid keyboard shortcuts — bind ở TOPLEVEL (không phải bind_all)
        # để có thể unbind chính xác qua funcid khi widget destroy. Thực tế
        # bind_all không hỗ trợ unbind theo funcid → dễ leak handler nếu
        # wizard được khởi tạo nhiều lần (vd embed nested context).
        # Handler self-check qua `_is_focus_in_grid` — không nuốt event của
        # widget khác (Entry, Text...).
        toplevel = self.winfo_toplevel()
        for seq, handler in (
            ("<Control-c>", self._on_keyboard_copy),
            ("<Control-C>", self._on_keyboard_copy),
            ("<Control-v>", self._on_keyboard_paste),
            ("<Control-V>", self._on_keyboard_paste),
            ("<Delete>", self._on_keyboard_delete),
        ):
            try:
                fid = toplevel.bind(seq, handler, add="+")
                self._kb_bound_funcids.append((seq, fid))
            except Exception:
                pass
        # Cancel stuck drag khi window mất focus toàn bộ (vd alt-tab).
        # Tk không đảm bảo fire `<ButtonRelease-1>` trong app này khi user
        # release ngoài window → drag state có thể stuck.
        try:
            fid = toplevel.bind(
                "<FocusOut>", self._on_window_focus_out, add="+",
            )
            self._kb_bound_funcids.append(("<FocusOut>", fid))
        except Exception:
            pass

        # Bind destroy để cleanup after callbacks + signal stop workers
        self.bind("<Destroy>", self._on_destroy)

"""BackupRestoreDialog: sao lưu / khôi phục / rà soát KHBD."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any, TYPE_CHECKING

from ...engine.backup.models import BACKUP_FILE_EXT, BackupFile
from ...engine.backup.snapshots import SnapshotInfo
from ..styles import apply_wizard_styles
from ..theme import CLR_PANEL_BG, fit_geometry_to_work_area, get_tk_work_area
from ..workers.backup import BackupWorker
from ..workers.restore import RESTORE_MODE_OVERWRITE, RestoreWorker
from .backup_actions import BackupActionsMixin
from .backup_tab import BackupTabMixin
from .lifecycle import DialogLifecycleMixin
from .logic_tab import LogicTabMixin
from .restore_actions import RestoreActionsMixin
from .restore_tab import RestoreTabMixin
from .smart_repair_tab import SmartRepairTabMixin
from .snapshots_tab import SnapshotsTabMixin

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..wizard.wizard import KHDHWizard


class BackupRestoreDialog(
    BackupTabMixin,
    RestoreTabMixin,
    SnapshotsTabMixin,
    SmartRepairTabMixin,
    LogicTabMixin,
    RestoreActionsMixin,
    BackupActionsMixin,
    DialogLifecycleMixin,
    tk.Toplevel,
):
    """Dialog sao lưu, khôi phục và rà soát dữ liệu KHBD.

    SIẾT LOGIC:
    - Tab Sao lưu: chọn dải tuần + note + path → spawn BackupWorker.
    - Tab Khôi phục: chọn file JSON → preview tuần → user check tuần
      muốn restore + chọn conflict mode → spawn RestoreWorker.
    - Tab Bản lưu nhanh: liệt kê snapshot trong `<excel>.snapshots/`,
      load lại như file backup bình thường.

    Modal nhưng không grab tuyệt đối — cho user xem main wizard log
    nếu cần đối chiếu.
    """

    def __init__(self, parent: "KHDHWizard"):
        super().__init__(parent.winfo_toplevel())
        self.withdraw()
        self.wizard = parent
        self.title("📦 Sao lưu & rà soát KHBD")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent.winfo_toplevel())
        self.resizable(True, True)
        apply_wizard_styles(ttk.Style(self))

        # Auto-fit to Windows work area so the dialog never opens behind the taskbar.
        _, _, work_w, work_h = get_tk_work_area(self)
        target_w = min(int(work_w * 0.92), 1280)
        target_h = min(int(work_h * 0.94), 900)
        target_w, target_h, x, y = fit_geometry_to_work_area(
            self,
            target_w,
            target_h,
            margin=10,
            min_w=900,
            min_h=560,
            anchor="top_center",
            chrome_h=32,
        )
        self.geometry(f"{target_w}x{target_h}+{x}+{y}")
        self.minsize(min(980, target_w), min(620, target_h))
        self.maxsize(work_w, work_h)

        # Worker state — backup + restore worker dùng cùng dialog,
        # nhưng KHÔNG bao giờ chạy đồng thời (UI lock checking).
        self._backup_worker: BackupWorker | None = None
        self._backup_queue: queue.Queue = queue.Queue()
        self._backup_stop = threading.Event()
        self._restore_worker: RestoreWorker | None = None
        self._restore_queue: queue.Queue = queue.Queue()
        self._restore_stop = threading.Event()
        self._poll_after_id: str | None = None

        # Backup tab vars
        self.var_backup_from = tk.IntVar(value=1)
        self.var_backup_to = tk.IntVar(value=36)
        self.var_backup_path = tk.StringVar(value="")
        self.var_backup_note = tk.StringVar(value="")
        self.var_backup_status = tk.StringVar(value="Chọn khoảng tuần và bấm [Bắt đầu sao lưu].")
        self.var_backup_progress = tk.IntVar(value=0)

        # Restore tab vars
        self.var_restore_path = tk.StringVar(value="")
        self.var_restore_conflict = tk.StringVar(value=RESTORE_MODE_OVERWRITE)
        self.var_restore_status = tk.StringVar(value="Chọn tệp sao lưu để xem chi tiết các tuần.")
        self.var_restore_progress = tk.IntVar(value=0)
        self.var_auto_snapshot = tk.BooleanVar(value=True)
        self.var_restore_fast_safe = tk.BooleanVar(value=False)
        # v3.3: real-time số XHR autofill blocker đã chặn
        self.var_blocker_status = tk.StringVar(value="🛡 Bộ chặn tự điền: sẵn sàng")
        # Cache khi load file
        self._loaded_backup: BackupFile | None = None
        # Map tuần → check state (BooleanVar)
        self._week_check_vars: dict[int, tk.BooleanVar] = {}
        self._week_row_widgets: dict[int, dict[str, Any]] = {}
        self._week_row_meta: dict[int, dict[str, Any]] = {}

        # Bản lưu nhanh — populated khi switch tab
        self._snapshot_list: list[SnapshotInfo] = []

        # Init backup default path từ profile excel (nếu có)
        if self.wizard._profile_path is not None:
            try:
                stem = self.wizard._profile_path.stem
                ts = datetime.now().strftime("%Y%m%d-%H%M")
                default = (
                    self.wizard._profile_path.parent
                    / f"{stem}-backup-{ts}{BACKUP_FILE_EXT}"
                )
                self.var_backup_path.set(str(default))
            except Exception:
                pass

        self._build_ui()

        self.bind("<Escape>", self._on_esc)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        self.deiconify()
        self.lift()

"""Khung chính và tab Sao lưu."""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk

from ...engine.backup.models import BACKUP_FILE_EXT


class BackupTabMixin:
    """Khung chính và tab Sao lưu."""

    def _build_ui(self):
        outer = ttk.Frame(self, padding=10, style="Wiz.TFrame")
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # Header
        ttk.Label(
            outer, text="📦 Sao lưu & rà soát KHBD",
            style="WizTitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        nb = ttk.Notebook(outer)
        nb.grid(row=1, column=0, sticky="nsew")

        tab_backup = ttk.Frame(nb, padding=12, style="Wiz.TFrame")
        nb.add(tab_backup, text="  📦  Sao lưu  ")
        self._build_backup_tab(tab_backup)

        tab_restore = ttk.Frame(nb, padding=12, style="Wiz.TFrame")
        nb.add(tab_restore, text="  📥  Khôi phục  ")
        self._build_restore_tab(tab_restore)

        tab_snap = ttk.Frame(nb, padding=12, style="Wiz.TFrame")
        nb.add(tab_snap, text="  📸  Bản lưu nhanh  ")
        self._build_snapshots_tab(tab_snap)

        tab_smart = ttk.Frame(nb, padding=12, style="Wiz.TFrame")
        nb.add(tab_smart, text="  🔧  Sửa trên VnEdu  ")
        self._build_smart_repair_tab(tab_smart)

        tab_logic = ttk.Frame(nb, padding=12, style="Wiz.TFrame")
        nb.add(tab_logic, text="  🔎  Rà soát PPCT  ")
        self._build_khbd_logic_tab(tab_logic)

        # Cập nhật bản lưu nhanh khi chuyển sang tab tương ứng
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._notebook = nb

    def _on_tab_changed(self, _event=None):
        try:
            idx = self._notebook.index(self._notebook.select())
            if idx == 2:  # Bản lưu nhanh
                self._refresh_snapshots_list()
        except Exception:
            pass

    # -----------------------------------------------------------
    # Tab 1: Sao lưu
    # -----------------------------------------------------------

    def _build_backup_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(99, weight=1)

        # Hint
        ttk.Label(
            parent,
            text=(
                "Công cụ sẽ chỉ ĐỌC toàn bộ KHDH trong khoảng tuần đã chọn "
                "rồi xuất ra tệp sao lưu. KHÔNG ghi gì lên VnEdu.\n"
                "Tệp sao lưu giữ NGUYÊN BẢN cả PPCT, tên bài, ghi chú và "
                "TRẠNG THÁI (Bình thường / Dạy bù / Chèn lịch / …)."
            ),
            wraplength=920, justify="left",
            style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        # Range
        rng = ttk.LabelFrame(
            parent, text=" Khoảng tuần sao lưu ",
            padding=10, style="Wiz.TLabelframe",
        )
        rng.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        r1 = ttk.Frame(rng, style="Wiz.TFrame")
        r1.pack(fill="x")
        ttk.Label(r1, text="Từ tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52, textvariable=self.var_backup_from,
            width=5, font=("Segoe UI", 10),
        ).pack(side="left", padx=(4, 12))
        ttk.Label(r1, text="đến tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52, textvariable=self.var_backup_to,
            width=5, font=("Segoe UI", 10),
        ).pack(side="left", padx=(4, 12))
        ttk.Label(
            r1, text="(mặc định 1–36 = full năm học)",
            style="WizHint.TLabel",
        ).pack(side="left")

        # Path
        path_frm = ttk.LabelFrame(
            parent, text=" Nơi lưu tệp sao lưu ",
            padding=10, style="Wiz.TLabelframe",
        )
        path_frm.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        path_frm.columnconfigure(0, weight=1)
        ttk.Entry(
            path_frm, textvariable=self.var_backup_path,
            font=("Consolas", 9),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(
            path_frm, text="📂 Chọn…",
            command=self._on_pick_backup_path,
            style="WizSubtle.TButton",
        ).grid(row=0, column=1)

        # Note
        note_frm = ttk.LabelFrame(
            parent, text=" Ghi chú (tùy chọn) ",
            padding=10, style="Wiz.TLabelframe",
        )
        note_frm.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        note_frm.columnconfigure(0, weight=1)
        ttk.Entry(
            note_frm, textvariable=self.var_backup_note,
            font=("Segoe UI", 10),
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            note_frm,
            text="Ví dụ: \"Trước khi sửa T31\", \"Cuối kỳ I\"… "
                 "Ghi chú giúp bạn nhận ra tệp này khi nhiều bản sao lưu chồng nhau.",
            style="WizHint.TLabel",
            wraplength=900, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        # Action
        action = ttk.Frame(parent, style="Wiz.TFrame")
        action.grid(row=4, column=0, sticky="ew", pady=(4, 8))
        self.btn_backup_run = ttk.Button(
            action, text="📦 Bắt đầu sao lưu",
            command=self._on_backup_clicked,
            style="WizSuccess.TButton",
        )
        self.btn_backup_run.pack(side="left")
        self.btn_backup_stop = ttk.Button(
            action, text="■ Dừng",
            command=self._on_backup_stop,
            state="disabled",
            style="WizDanger.TButton",
        )
        self.btn_backup_stop.pack(side="left", padx=(8, 0))
        # Progress + status
        prog = ttk.Frame(parent, style="Wiz.TFrame")
        prog.grid(row=5, column=0, sticky="ew")
        prog.columnconfigure(0, weight=1)
        self._backup_progress_bar = ttk.Progressbar(
            prog, mode="determinate", variable=self.var_backup_progress,
            style="WizBlue.Horizontal.TProgressbar",
        )
        self._backup_progress_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            parent, textvariable=self.var_backup_status,
            style="Wiz.TLabel", wraplength=920, justify="left",
        ).grid(row=6, column=0, sticky="w", pady=(6, 0))

    def _on_pick_backup_path(self):
        from tkinter import filedialog
        cur = self.var_backup_path.get()
        initial_dir = ""
        initial_file = ""
        if cur:
            try:
                p = Path(cur)
                initial_dir = str(p.parent) if p.parent.exists() else ""
                initial_file = p.name
            except Exception:
                pass
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Chọn nơi lưu tệp sao lưu",
            defaultextension=BACKUP_FILE_EXT,
            initialdir=initial_dir,
            initialfile=initial_file,
            filetypes=[("Tệp sao lưu KHBD", f"*{BACKUP_FILE_EXT}"),
                       ("Tệp JSON", "*.json"), ("Tất cả tệp", "*.*")],
        )
        if path:
            self.var_backup_path.set(path)

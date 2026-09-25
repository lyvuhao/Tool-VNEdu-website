"""Dựng khung chính, header và các cửa sổ phụ."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from ..advanced_window import AdvancedWindow
from ..backup_restore.dialog import BackupRestoreDialog
from ..dialogs.huong_dan_chi_tiet import HuongDanChiTietDialog
from ..theme import CLR_PANEL_BG


class WizardLayoutMixin:
    """Dựng khung chính, header và các cửa sổ phụ."""

    # -----------------------------------------------------------
    # UI BUILD
    # -----------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Layout 3 vùng:
        #   Row 0: Header
        #   Row 1: Hồ sơ + Kết nối
        #   Row 2: Outer canvas có scroll dọc — chứa toàn bộ phần dưới:
        #          TKB grid + PPCT + Dải tuần + Bắt đầu + Log
        # Lý do dùng outer canvas thay vì PanedWindow vertical:
        #   - Khi window nhỏ, PanedWindow ép các section nhỏ lại → nút "Bắt
        #     đầu nhập" có thể bị che ngoài tầm nhìn, không scroll được.
        #   - Outer canvas đảm bảo người dùng LUÔN scroll xuống được mọi
        #     section (cả mouse wheel và scrollbar).

        self._build_header(row=0)
        self._build_connect_section(row=1)

        # Outer canvas + vertical scrollbar
        scroll_wrap = ttk.Frame(self, style="Wiz.TFrame")
        scroll_wrap.grid(row=2, column=0, sticky="nsew", pady=(0, 0))
        scroll_wrap.columnconfigure(0, weight=1)
        scroll_wrap.rowconfigure(0, weight=1)

        outer_canvas = tk.Canvas(
            scroll_wrap, background=CLR_PANEL_BG,
            highlightthickness=0, bd=0,
        )
        outer_canvas.grid(row=0, column=0, sticky="nsew")
        outer_sb = ttk.Scrollbar(
            scroll_wrap, orient="vertical", command=outer_canvas.yview,
        )
        outer_sb.grid(row=0, column=1, sticky="ns")
        outer_canvas.configure(yscrollcommand=outer_sb.set)

        # Inner frame chứa tất cả section
        content = ttk.Frame(outer_canvas, style="Wiz.TFrame")
        content_window = outer_canvas.create_window(
            (0, 0), window=content, anchor="nw",
        )
        content.columnconfigure(0, weight=1)

        # 4 vùng nối tiếp nhau theo chiều dọc, mỗi vùng có chiều cao đủ
        # để hiển thị nội dung đầy đủ (không co rút).
        tkb_pane = ttk.Frame(content, style="Wiz.TFrame")
        tkb_pane.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 4))
        self._build_tkb_section_in(tkb_pane)

        bottom = ttk.Frame(content, style="Wiz.TFrame")
        bottom.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 4))
        bottom.columnconfigure(0, weight=1)
        self._build_ppct_section_in(bottom, row=0)
        self._build_tuan_section_in(bottom, row=1)
        self._build_run_section_in(bottom, row=2)
        self._build_log_section_in(bottom, row=3)

        # Update scrollregion khi content thay đổi size
        def _on_content_configure(_event):
            outer_canvas.configure(scrollregion=outer_canvas.bbox("all"))

        content.bind("<Configure>", _on_content_configure)

        # Khi canvas resize → resize content ngang để fit
        def _on_canvas_configure(event):
            outer_canvas.itemconfig(content_window, width=event.width)

        outer_canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse wheel scroll dọc cho outer canvas. TKB inner canvas có
        # logic riêng — dùng 1 global handler thông minh route đúng canvas
        # dựa trên vị trí mouse (winfo_containing), tránh bind_all conflict.
        self._inner_tkb_canvas = None  # sẽ set trong _build_grid_canvas

        def _on_global_wheel(event):
            """Single global wheel handler — route đến đúng canvas."""
            try:
                # Xác định widget dưới mouse
                widget_under = self.winfo_containing(event.x_root, event.y_root)
                if widget_under is None:
                    return

                # Normalize delta: Windows = ±120 per notch, macOS khác
                if sys.platform == "darwin":
                    delta = -event.delta
                else:
                    delta = int(-event.delta / 120)

                # Check xem mouse có đang trên TKB inner canvas không
                inner = self._inner_tkb_canvas
                if inner and self._is_child_of(widget_under, inner):
                    inner.yview_scroll(delta, "units")
                else:
                    outer_canvas.yview_scroll(delta, "units")
            except Exception:
                pass

        self._safe_after(100, lambda: self.bind_all("<MouseWheel>", _on_global_wheel))

        self._outer_canvas = outer_canvas
        self._global_wheel_handler = _on_global_wheel

    def _set_default_sash(self):
        """Giữ lại để compat — không còn dùng PanedWindow nên không cần làm gì."""
        pass

    def _build_header(self, row):
        title_row = ttk.Frame(self, style="Wiz.TFrame")
        title_row.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        title_row.columnconfigure(0, weight=1)

        ttk.Label(
            title_row, text="KHDH tự động",
            style="WizTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")

        btn_box = ttk.Frame(title_row, style="Wiz.TFrame")
        btn_box.grid(row=0, column=1, sticky="e")

        ttk.Button(
            btn_box, text="Sao lưu",
            command=self._on_backup_restore_clicked,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            btn_box, text="Công cụ",
            command=self._on_advanced_clicked,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            btn_box, text="Trợ giúp",
            command=self._on_help_clicked,
            style="WizSubtle.TButton",
        ).pack(side="left")

        ttk.Label(
            title_row,
            text="Soạn TKB, kiểm PPCT, xem trước rồi nhập lên VnEdu.",
            style="WizSubtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _on_advanced_clicked(self):
        """Mở cửa sổ Nâng cao — quét toàn năm + xem patterns/sổ tên bài."""
        if not self.bootstrap_data:
            messagebox.showinfo(
                "Chưa đăng nhập VnEdu",
                "Bạn cần đăng nhập VnEdu trước khi mở chế độ nâng cao.",
                parent=self,
            )
            return
        # Singleton: nếu đã có cửa sổ Nâng cao đang mở, focus nó thay vì mở mới
        existing = self._advanced_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                    return
            except Exception:
                # Reference cũ không còn hợp lệ → cho phép mở mới
                self._advanced_window = None

        adv = AdvancedWindow(
            self.winfo_toplevel(),
            port=int(self.var_port.get()),
            wizard=self,
        )
        self._advanced_window = adv
        # Khi user đóng cửa sổ → clear reference
        adv.bind(
            "<Destroy>",
            lambda e, a=adv: self._on_advanced_destroyed(e, a),
            add="+",
        )
        adv.lift()

    def _on_advanced_destroyed(self, event, adv):
        """Clear reference khi AdvancedWindow đóng."""
        if event.widget is adv and self._advanced_window is adv:
            self._advanced_window = None

    def _on_backup_restore_clicked(self):
        """Mở `BackupRestoreDialog` — singleton để tránh mở 2 cửa sổ song song."""
        existing = self._backup_restore_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                    return
            except Exception:
                self._backup_restore_dialog = None
        # Cảnh báo nếu chưa kết nối Chrome — nhưng KHÔNG block hoàn toàn,
        # vì user có thể chỉ muốn xem snapshot list (tab 3) mà chưa cần CDP.
        dlg = BackupRestoreDialog(self)
        self._backup_restore_dialog = dlg
        dlg.bind(
            "<Destroy>",
            lambda e, d=dlg: self._on_backup_restore_destroyed(e, d),
            add="+",
        )

    def _on_backup_restore_destroyed(self, event, dlg):
        if event.widget is dlg and self._backup_restore_dialog is dlg:
            self._backup_restore_dialog = None

    def _on_help_clicked(self):
        """Mở hướng dẫn sử dụng chi tiết — singleton để tránh mở chồng."""
        existing = self._help_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                    return
            except Exception:
                self._help_dialog = None
        dlg = HuongDanChiTietDialog(self)
        self._help_dialog = dlg
        dlg.bind(
            "<Destroy>",
            lambda e, d=dlg: self._on_help_destroyed(e, d),
            add="+",
        )

    def _on_help_destroyed(self, event, dlg):
        if event.widget is dlg and self._help_dialog is dlg:
            self._help_dialog = None

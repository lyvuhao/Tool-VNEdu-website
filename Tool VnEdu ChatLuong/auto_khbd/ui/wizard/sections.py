"""Dựng các khu: kết nối, TKB, PPCT, tuần, chạy, log."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..theme import (
    CLR_GRID_HEADER_BG,
    CLR_GRID_HEADER_BORDER,
    CLR_GRID_HEADER_FG,
    CLR_GRID_LINE,
    CLR_PANEL_BG,
    CLR_SLOT_EMPTY,
    DAYS,
    TIETS_BY_BUOI,
)


class SectionsMixin:
    """Dựng các khu: kết nối, TKB, PPCT, tuần, chạy, log."""

    def _build_connect_section(self, row):
        frm = ttk.LabelFrame(
            self, text=" 1. Đăng nhập VnEdu & Hồ sơ ",
            padding=(10, 8), style="Wiz.TLabelframe",
        )
        frm.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=0)

        # Row 1 — tài khoản + mật khẩu + nút đăng nhập
        r1 = ttk.Frame(frm, style="Wiz.TFrame")
        r1.grid(row=0, column=0, sticky="w")

        ttk.Label(r1, text="Tài khoản:", style="Wiz.TLabel").pack(side="left")
        self._entry_username = ttk.Entry(
            r1, textvariable=self.var_username,
            width=18, font=("Segoe UI", 10),
        )
        self._entry_username.pack(side="left", padx=(4, 8))

        ttk.Label(r1, text="Mật khẩu:", style="Wiz.TLabel").pack(side="left")
        self._entry_password = ttk.Entry(
            r1, textvariable=self.var_password,
            width=16, font=("Segoe UI", 10), show="●",
        )
        self._entry_password.pack(side="left", padx=(4, 10))

        self.btn_connect = ttk.Button(
            r1, text="Đăng nhập VnEdu",
            command=self._on_connect_clicked,
            style="WizPrimary.TButton",
        )
        self.btn_connect.pack(side="left")

        # Bind Enter trên cả 2 field → trigger login
        self._entry_username.bind("<Return>", lambda e: self._on_connect_clicked())
        self._entry_password.bind("<Return>", lambda e: self._on_connect_clicked())

        profile_bar = ttk.Frame(frm, style="WizToolbar.TFrame")
        profile_bar.grid(row=0, column=1, sticky="e", padx=(16, 0))
        ttk.Button(
            profile_bar, text="Mở hồ sơ", command=self._on_open_profile,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            profile_bar, text="Lưu hồ sơ", command=self._on_save_profile,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            profile_bar, text="Lưu mới...", command=self._on_save_profile_as,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 4))
        self.btn_recent = ttk.Menubutton(
            profile_bar, text="Mới mở", style="WizSubtle.TButton",
        )
        self._recent_menu = tk.Menu(self.btn_recent, tearoff=False)
        self.btn_recent["menu"] = self._recent_menu
        self.btn_recent.pack(side="left")
        self._refresh_recent_menu()

        # Row 2 — info line
        ttk.Label(
            frm, textvariable=self.var_user_info,
            style="WizAccent.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Row 3 — tên file hồ sơ đang mở (hoặc "(chưa lưu)").
        # Dấu `*` cuối nhãn = có thay đổi chưa lưu.
        ttk.Label(
            frm, textvariable=self.var_profile_label,
            style="WizMicro.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w")

        ttk.Label(
            frm, textvariable=self.var_connect_status,
            style="WizMicro.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w")

    def _build_tkb_section_in(self, parent):
        frm = ttk.LabelFrame(
            parent,
            text=" 2. Soạn lịch dạy ",
            padding=10, style="Wiz.TLabelframe",
        )
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(3, weight=1)

        # Top row — checkbox tách lẻ/chẵn
        r1 = ttk.Frame(frm, style="Wiz.TFrame")
        r1.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        r1.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            r1, variable=self.var_tach_le_chan,
            text="Tôi dạy KHÁC nhau giữa tuần lẻ và tuần chẵn (ví dụ tuần 1 ≠ tuần 2)",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            r1,
            text="Double-click để thêm/sửa, kéo để copy, Ctrl+C/V để sao chép.",
            style="WizMicro.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(18, 0))

        # Nút quét + import TKB từ web — nằm cùng frame "Soạn lịch dạy"
        # vì đây là cách nhanh để khởi tạo TKB từ data web thực, sau đó
        # giáo viên có thể tinh chỉnh ngay trên grid.
        ttk.Button(
            r1, text="Nhập TKB từ web",
            command=self._on_import_tkb_clicked,
            style="WizSubtle.TButton",
        ).grid(row=0, column=3, sticky="e")
        # Nút phóng to grid TKB — mở cửa sổ riêng full screen, có chế độ
        # xem cả lẻ + chẵn cạnh nhau khi profile tách lẻ-chẵn.
        ttk.Button(
            r1, text="Phóng to TKB",
            command=self._on_full_tkb_clicked,
            style="WizSubtle.TButton",
        ).grid(row=0, column=2, sticky="e", padx=(0, 6))

        engine_row = ttk.Frame(frm, style="Wiz.TFrame")
        engine_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        engine_row.columnconfigure(1, weight=1)
        ttk.Button(
            engine_row, text="Nghỉ / dạy bù...",
            command=self._on_holiday_rules_clicked,
            style="WizSubtle.TButton",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(
            engine_row,
            textvariable=self.var_holiday_summary,
            style="WizHint.TLabel",
            wraplength=980,
        ).grid(row=0, column=1, sticky="w")

        # Tab area (chỉ hiện khi tách)
        self._tab_frame = ttk.Frame(frm, style="Wiz.TFrame")
        self._tab_frame.grid(row=2, column=0, sticky="ew")

        # Grid container — fill toàn bộ phần còn lại
        self._grid_container = ttk.Frame(frm, style="Wiz.TFrame")
        self._grid_container.grid(row=3, column=0, sticky="nsew")
        self._build_grid_canvas()

    def _build_grid_canvas(self):
        """Build canvas chứa lưới TKB. Re-buildable khi switch tab.

        Layout: scrollable canvas chứa lưới nội bộ, cho phép kéo xuống
        khi cửa sổ nhỏ. Mỗi row có chiều cao cố định để ô không bị crush.
        """
        for w in self._grid_container.winfo_children():
            w.destroy()
        self._slot_buttons = {}

        self._grid_container.rowconfigure(0, weight=1)
        self._grid_container.columnconfigure(0, weight=1)

        # Pixel sizes — ép chặt
        ROW_HEADER_H = 36
        ROW_BODY_H = 60     # đủ cho 2 dòng "Lớp / Môn"
        COL_BUOI_W = 80
        COL_TIET_W = 50
        COL_DAY_W = 150     # mỗi cột ngày
        TOTAL_INNER_W = COL_BUOI_W + COL_TIET_W + COL_DAY_W * len(DAYS)

        # Canvas + Scrollbars
        # Canvas tự co giãn theo TKB pane (user kéo divider).
        # Default height vừa đủ ~5 row khi mới mở.
        canvas = tk.Canvas(
            self._grid_container, background=CLR_PANEL_BG,
            highlightthickness=0, bd=0,
            height=ROW_HEADER_H + ROW_BODY_H * 4,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        v_sb = ttk.Scrollbar(
            self._grid_container, orient="vertical", command=canvas.yview,
        )
        v_sb.grid(row=0, column=1, sticky="ns")
        h_sb = ttk.Scrollbar(
            self._grid_container, orient="horizontal", command=canvas.xview,
        )
        h_sb.grid(row=1, column=0, sticky="ew")
        canvas.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)

        # Inner frame chứa lưới thật. Dùng nền đậm hơn làm gutter để các
        # đường kẻ ô vẫn rõ trên Windows, nơi tk.Button không cho đổi màu border.
        outer = tk.Frame(canvas, background=CLR_GRID_LINE)
        canvas_window = canvas.create_window(
            (0, 0), window=outer, anchor="nw",
        )

        # Setup column widths CỐ ĐỊNH
        outer.columnconfigure(0, minsize=COL_BUOI_W)
        outer.columnconfigure(1, minsize=COL_TIET_W)
        for col_idx in range(len(DAYS)):
            outer.columnconfigure(col_idx + 2, weight=1, minsize=COL_DAY_W)

        # Row heights CỐ ĐỊNH
        outer.rowconfigure(0, minsize=ROW_HEADER_H)
        for r in range(1, 11):
            outer.rowconfigure(r, minsize=ROW_BODY_H)

        # Header row
        tk.Label(
            outer, text="Buổi · Tiết",
            background=CLR_GRID_HEADER_BG, foreground=CLR_GRID_HEADER_FG,
            font=("Segoe UI", 10, "bold"),
            relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=CLR_GRID_HEADER_BORDER,
        ).grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(1, 0), pady=(1, 0))

        for col_idx, (thu, label) in enumerate(DAYS):
            tk.Label(
                outer, text=label,
                background=CLR_GRID_HEADER_BG, foreground=CLR_GRID_HEADER_FG,
                font=("Segoe UI", 11, "bold"),
                relief="flat", borderwidth=0,
                highlightthickness=1, highlightbackground=CLR_GRID_HEADER_BORDER,
            ).grid(row=0, column=col_idx + 2, sticky="nsew", padx=(1, 0), pady=(1, 0))

        # Body rows
        row_idx = 1
        for buoi_label, buoi_idx, tiet_list in TIETS_BY_BUOI:
            n = len(tiet_list)
            buoi_bg = "#fff8e1" if buoi_idx == 1 else "#e3f2fd"
            buoi_fg = "#a86400" if buoi_idx == 1 else "#0d47a1"
            tk.Label(
                outer, text=buoi_label,
                background=buoi_bg, foreground=buoi_fg,
                font=("Segoe UI", 13, "bold"),
                relief="flat", borderwidth=0,
                highlightthickness=1, highlightbackground=CLR_GRID_HEADER_BORDER,
            ).grid(row=row_idx, column=0, rowspan=n, sticky="nsew", padx=(1, 0), pady=(1, 0))

            for tiet in tiet_list:
                tk.Label(
                    outer, text=str(tiet),
                    background=CLR_GRID_HEADER_BG, foreground=CLR_GRID_HEADER_FG,
                    font=("Segoe UI", 12, "bold"),
                    relief="flat", borderwidth=0,
                    highlightthickness=1, highlightbackground=CLR_GRID_HEADER_BORDER,
                ).grid(row=row_idx, column=1, sticky="nsew", padx=(1, 0), pady=(1, 0))

                for col_idx, (thu, _) in enumerate(DAYS):
                    btn = self._make_slot_button(outer, thu, buoi_idx, tiet)
                    btn.grid(row=row_idx, column=col_idx + 2, sticky="nsew",
                            padx=(1, 0), pady=(1, 0))
                    key = f"{thu}_{buoi_idx}_{tiet}"
                    self._slot_buttons[key] = btn

                row_idx += 1

        # Update scrollregion khi inner frame thay đổi size
        def _on_inner_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        outer.bind("<Configure>", _on_inner_configure)

        # Khi canvas resize → resize inner frame ngang để fit
        def _on_canvas_configure(event):
            canvas.itemconfig(
                canvas_window, width=max(event.width, TOTAL_INNER_W)
            )

        canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse wheel scroll dọc khi hover trên TKB grid.
        # Routing được xử lý bởi _on_global_wheel trong _build_ui —
        # chỉ cần set reference để global handler biết canvas nào là TKB.
        self._inner_tkb_canvas = canvas

    def _make_slot_button(self, parent, thu: int, buoi: int, tiet: int):
        """Tạo 1 ô slot button với đầy đủ tương tác:

        - **Single-click** → select ô (highlight viền vàng)
        - **Double-click** → mở SlotPickerDialog (tương đương click cũ)
        - **Right-click** → context menu (Sao chép, Dán, Xóa, Sửa)
        - **Drag** từ ô có data → thả ô đích để **copy** slot (giữ nguyên ô gốc)
        - **Ctrl+C** trên ô đang select → copy
        - **Ctrl+V** trên ô đang select → paste
        - **Delete** trên ô đang select → xóa

        Threshold drag = `SLOT_DRAG_THRESHOLD_PX` để tránh click thường bị
        nhận nhầm thành drag.
        """
        btn = tk.Button(
            parent, text="+\nThêm", anchor="center",
            font=("Segoe UI", 11),
            wraplength=130, justify="center",
            relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=CLR_GRID_LINE,
            background=CLR_SLOT_EMPTY, activebackground="#eef4fb",
            foreground="#9ca3af",
            cursor="hand2",
        )
        # Single-click → select (KHÔNG mở dialog nữa)
        btn.bind(
            "<Button-1>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_press(e, t, b, ti),
        )
        btn.bind(
            "<B1-Motion>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_drag_motion(e, t, b, ti),
        )
        btn.bind(
            "<ButtonRelease-1>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_release(e, t, b, ti),
        )
        # Double-click → mở dialog edit
        btn.bind(
            "<Double-Button-1>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_double_clicked(t, b, ti),
        )
        # Right-click → context menu
        btn.bind(
            "<Button-3>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_right_click(e, t, b, ti),
        )
        btn.bind(
            "<Alt-Button-1>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_alt_click(e, t, b, ti),
        )
        btn.bind(
            "<Enter>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_hover_enter(t, b, ti),
        )
        btn.bind(
            "<Leave>",
            lambda e, t=thu, b=buoi, ti=tiet: self._on_slot_hover_leave(t, b, ti),
        )
        return btn

    def _build_ppct_section_in(self, parent, row):
        frm = ttk.LabelFrame(
            parent, text=" 3. PPCT bắt đầu (cho mỗi Lớp × Phân môn) ",
            padding=10, style="Wiz.TLabelframe",
        )
        frm.grid(row=row, column=0, sticky="nsew", pady=(6, 6))
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(1, weight=1)

        # Header row: hint + nút auto-detect + progress bar
        hdr = ttk.Frame(frm, style="Wiz.TFrame")
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        hdr.columnconfigure(0, weight=1)

        ttk.Label(
            hdr,
            text="Tự liệt kê các nhóm theo lịch bạn vừa soạn. "
                 "Double-click cột PPCT để sửa.",
            style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w")

        # v2: Button "Tự tính PPCT HĐTN Chủ đề" (bên trái nút detect web)
        self.btn_auto_hdtn = ttk.Button(
            hdr, text="Tính PPCT HĐTN",
            command=self._on_auto_hdtn_clicked,
            style="WizSubtle.TButton",
        )
        self.btn_auto_hdtn.grid(row=0, column=1, sticky="e", padx=(8, 4))

        self.btn_health_scan = ttk.Button(
            hdr, text="Kiểm tra lỗi",
            command=self._on_health_scan_clicked,
            style="WizSubtle.TButton",
        )
        self.btn_health_scan.grid(row=0, column=2, sticky="e", padx=(0, 4))

        self.btn_detect_ppct = ttk.Button(
            hdr, text="Lấy PPCT từ web",
            command=self._on_detect_ppct_clicked,
            style="WizSubtle.TButton",
        )
        self.btn_detect_ppct.grid(row=0, column=3, sticky="e", padx=(0, 0))

        # Hàng tiến trình quét — chỉ visible khi đang chạy
        self.var_detect_progress = tk.IntVar(value=0)
        self.var_detect_progress_text = tk.StringVar(value="")
        self._detect_progress_row = ttk.Frame(frm, style="Wiz.TFrame")
        # Đặt grid trên row=2 (dưới tree). KHÔNG hiện ban đầu.
        self._detect_progress_row.columnconfigure(0, weight=1)

        self.progress_detect = ttk.Progressbar(
            self._detect_progress_row,
            variable=self.var_detect_progress,
            mode="determinate",
            length=400,
        )
        self.progress_detect.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(
            self._detect_progress_row,
            textvariable=self.var_detect_progress_text,
            style="Wiz.TLabel",
        ).grid(row=0, column=1, sticky="w")

        wrap = ttk.Frame(frm, style="Wiz.TFrame")
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.tree_ppct = ttk.Treeview(
            wrap, columns=("lop", "mon", "phan_mon", "ppct", "ppct_next"),
            show="headings", style="Wiz.Treeview", height=6,
        )
        # v2: Compact column widths (giảm ~20%) để có chỗ cho cột "PPCT sắp nhập".
        # ppct_next = PPCT thực tế tool sẽ nhập tiếp theo lên web (cập nhật
        # real-time từ scan-based last_ppct trong executor).
        for col, label, w, anchor, stretch in [
            ("lop", "Lớp", 60, "center", False),
            ("mon", "Môn học", 150, "w", True),
            ("phan_mon", "Phân môn", 170, "w", True),
            ("ppct", "PPCT bắt đầu", 92, "center", False),
            ("ppct_next", "PPCT sắp nhập", 100, "center", False),
        ]:
            self.tree_ppct.heading(col, text=label)
            self.tree_ppct.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_ppct.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree_ppct.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_ppct.configure(yscrollcommand=sb.set)

        # Double-click to edit PPCT
        self.tree_ppct.bind("<Double-1>", self._on_ppct_dbl_click)

    def _build_tuan_section_in(self, parent, row):
        frm = ttk.LabelFrame(
            parent, text=" 4. Chọn dải tuần áp dụng ",
            padding=10, style="Wiz.TLabelframe",
        )
        frm.grid(row=row, column=0, sticky="ew", pady=(0, 6))

        r1 = ttk.Frame(frm, style="Wiz.TFrame")
        r1.pack(fill="x")

        ttk.Label(r1, text="Từ tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52, textvariable=self.var_tuan_from,
            width=5, font=("Segoe UI", 10),
        ).pack(side="left", padx=(4, 14))
        ttk.Label(r1, text="đến tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52, textvariable=self.var_tuan_to,
            width=5, font=("Segoe UI", 10),
        ).pack(side="left", padx=(4, 14))

        self._tuan_hint = ttk.Label(
            r1, text="", style="WizHint.TLabel",
        )
        self._tuan_hint.pack(side="left", padx=(8, 0))
        self._refresh_tuan_hint()

    def _build_run_section_in(self, parent, row):
        frm = ttk.LabelFrame(
            parent, text=" 5. Bắt đầu nhập ",
            padding=10, style="Wiz.TLabelframe",
        )
        frm.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        frm.columnconfigure(0, weight=1)

        top_row = ttk.Frame(frm, style="Wiz.TFrame")
        top_row.grid(row=0, column=0, sticky="ew")
        top_row.columnconfigure(1, weight=1)
        # Force biến True để khớp với behavior thực tế
        try:
            self.var_ten_bai_fallback.set(True)
        except Exception:
            pass
        ttk.Label(
            top_row,
            text="Bảo vệ tên bài: đang bật",
            style="WizAccent.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            top_row,
            text="Tự chèn dấu cách khi web chưa có tên bài để tránh mất tiết.",
            style="WizHint.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Checkbutton(
            top_row,
            text="Hiện tiến trình nổi",
            variable=self.var_show_overlay,
        ).grid(row=0, column=2, sticky="e", padx=(12, 0))

        r1 = ttk.Frame(frm, style="Wiz.TFrame")
        r1.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.btn_preview = ttk.Button(
            r1, text="Xem trước",
            command=lambda: self._on_run_clicked(dry_run_default=True),
            style="WizSubtle.TButton",
        )
        self.btn_preview.pack(side="left", padx=(0, 6))

        self.btn_run = ttk.Button(
            r1, text="Bắt đầu nhập",
            command=lambda: self._on_run_clicked(dry_run_default=False),
            style="WizSuccess.TButton",
        )
        self.btn_run.pack(side="left", padx=(0, 6))

        self.btn_stop = ttk.Button(
            r1, text="Dừng an toàn", command=self._on_stop_clicked,
            state="disabled", style="WizDanger.TButton",
        )
        self.btn_stop.pack(side="left")

        # Hàng phụ: nút cập nhật tên bài cho các ô đã fallback dấu cách.
        # Hiện đếm số ô đang chờ cập nhật ngay trên text nút.
        r1b = ttk.Frame(frm, style="Wiz.TFrame")
        r1b.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.btn_refresh_fallback = ttk.Button(
            r1b,
            textvariable=self.var_fallback_button_text,
            command=self._on_refresh_fallback_clicked,
            style="WizSubtle.TButton",
            state="disabled",
        )
        self.btn_refresh_fallback.pack(side="left")
        self.btn_fill_missing_titles = ttk.Button(
            r1b,
            text="Điền tên bài HĐTN thiếu",
            command=self._on_fill_missing_titles_clicked,
            style="WizSubtle.TButton",
        )
        self.btn_fill_missing_titles.pack(side="left", padx=(6, 0))
        ttk.Label(
            r1b,
            text=(
                "Bên trái: cập nhật ô đã fallback. Bên phải: quét dải tuần "
                "đang chọn và chỉ điền các tên bài HĐTN lớp 6/8 còn trống."
            ),
            wraplength=500,
            justify="left",
            style="WizHint.TLabel",
        ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Hàng nguy hiểm: Xóa tuần KHDH — TÁCH RIÊNG khỏi các nút khác để
        # giảm rủi ro click nhầm. Có separator + label cảnh báo trên đầu.
        sep = ttk.Separator(frm, orient="horizontal")
        sep.grid(row=3, column=0, sticky="ew", pady=(10, 4))
        r1c = ttk.Frame(frm, style="Wiz.TFrame")
        r1c.grid(row=4, column=0, sticky="ew", pady=(0, 0))
        self.btn_delete_weeks = ttk.Button(
            r1c,
            text="Xóa tuần KHDH...",
            command=self._on_delete_weeks_clicked,
            style="WizDanger.TButton",
        )
        self.btn_delete_weeks.pack(side="left")
        tk.Label(
            r1c,
            text=(
                "Xóa 1 tuần hoặc dải tuần KHDH của BẠN. "
                "Tool dùng endpoint riêng cho 1 tuần — KHÔNG thể nhầm "
                "sang \"Xoá KHDH cấp THCS\"."
            ),
            wraplength=520,
            justify="left",
            font=("Segoe UI", 8),
            foreground="#a07000",
            background=CLR_PANEL_BG,
        ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Progress (đẩy xuống row 6+)
        prog_row = ttk.Frame(frm, style="Wiz.TFrame")
        prog_row.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        prog_row.columnconfigure(0, weight=1)

        self.progress_bar = ttk.Progressbar(
            prog_row, mode="determinate", maximum=100,
            variable=self.var_progress,
            style="WizBlue.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            prog_row, textvariable=self.var_progress_text,
            style="WizAccent.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(
            frm, textvariable=self.var_status, style="Wiz.TLabel",
        ).grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            frm, textvariable=self.var_summary, style="WizAccent.TLabel",
        ).grid(row=7, column=0, sticky="w", pady=(2, 0))

    def _build_log_section_in(self, parent, row):
        frm = ttk.LabelFrame(
            parent, text=" Nhật ký ", padding=6, style="Wiz.TLabelframe",
        )
        frm.grid(row=row, column=0, sticky="nsew")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        wrap = ttk.Frame(frm, style="Wiz.TFrame")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            wrap, height=6, wrap="none", font=("Consolas", 10),
            background="#111827", foreground="#d1d5db",
            relief="flat", borderwidth=0, highlightthickness=0,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.tag_config("info", foreground="#9cdcfe")
        self.log_text.tag_config("ok", foreground="#4ec9b0")
        self.log_text.tag_config("warn", foreground="#dcdcaa")
        self.log_text.tag_config("err", foreground="#f48771")
        self.log_text.tag_config("dim", foreground="#7e7e7e")

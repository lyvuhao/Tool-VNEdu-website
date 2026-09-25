"""Dựng giao diện chính và khu Chrome CDP."""

import tkinter as tk
from tkinter import ttk

from ..config import RIGHT_PANEL_WIDTH, UI_TEXT_MUTED


class LayoutMixin:
    """Dựng giao diện chính và khu Chrome CDP."""

    def _setup_ui(self):
        """Dựng giao diện 2 panel: trái (CDP + log), phải (schedule)."""

        # === Body frame: chia 2 panel (trái + phải) bằng grid layout ===
        # Grid đảm bảo cả 2 panel luôn có đúng kích thước, không phụ thuộc pack order.
        self._body_frame = ttk.Frame(self.root)
        self._body_frame.pack(fill="both", expand=True)
        self._body_frame.columnconfigure(0, weight=1)   # Panel trái co giãn
        self._body_frame.columnconfigure(1, weight=0, minsize=RIGHT_PANEL_WIDTH)  # Panel phải cố định
        self._body_frame.rowconfigure(0, weight=1)       # Full chiều cao

        # Compact shell nằm ngoài scroll-canvas để không bị mất khỏi viewport.
        self._compact_shell = ttk.Frame(self._body_frame, padding=12)
        self._compact_shell.columnconfigure(0, weight=1)
        self._build_compact_status_panel(self._compact_shell)

        # --- Panel trái: Scrollable container chứa các section chính ---
        self._left_panel = ttk.Frame(self._body_frame)
        self._left_panel.grid(row=0, column=0, sticky="nsew")

        self._canvas = tk.Canvas(self._left_panel, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(
            self._left_panel, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # Frame bên trong canvas chứa toàn bộ widgets
        self.main_frame = ttk.Frame(self._canvas, padding=6)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self.main_frame, anchor="nw"
        )

        # Cập nhật scroll region khi nội dung thay đổi kích thước
        self.main_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Hỗ trợ cuộn bằng chuột (mouse wheel)
        # Chỉ bind mousewheel cho canvas (tránh ảnh hưởng Spinbox)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.main_frame.bind("<MouseWheel>", self._on_mousewheel)

        # --- 0A. Frame Chrome CDP Connection ---
        self.frame_cdp = ttk.LabelFrame(
            self.main_frame, text="Trình duyệt và đăng nhập", padding=8
        )
        self.frame_cdp.pack(fill="x", pady=(0, 4))
        self._build_cdp_section(self.frame_cdp)

        # --- 1. Buttons ---
        self.frame_buttons = ttk.Frame(self.main_frame)
        self.frame_buttons.pack(anchor="e", pady=(0, 4))
        self._build_buttons(self.frame_buttons)

        self.frame_class_stats = ttk.Frame(self.main_frame)
        self.frame_class_stats.pack(fill="x", pady=(0, 6))
        self._build_class_stats_toolbar(self.frame_class_stats)

        # --- 2. Frame Log ---
        self.frame_log = ttk.LabelFrame(
            self.main_frame, text="Nhật ký", padding=6
        )
        self.frame_log.pack(fill="x", expand=False, pady=(0, 0))
        self._build_log(self.frame_log)

        # === Panel phải: Schedule (Lịch dạy) ===
        # Grid column=1, width cố định = RIGHT_PANEL_WIDTH
        self._right_panel = ttk.Frame(self._body_frame, width=RIGHT_PANEL_WIDTH)
        self._right_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self._right_panel.grid_propagate(False)  # Giữ width cố định, không co/giãn

        # Scrollable container cho panel phải (tương tự panel trái)
        self._right_canvas = tk.Canvas(self._right_panel, highlightthickness=0)
        self._right_scrollbar = ttk.Scrollbar(
            self._right_panel, orient="vertical", command=self._right_canvas.yview
        )
        self._right_canvas.configure(yscrollcommand=self._right_scrollbar.set)
        self._right_scrollbar.pack(side="right", fill="y")
        self._right_canvas.pack(side="left", fill="both", expand=True)

        # Frame bên trong canvas phải
        self._right_inner = ttk.Frame(self._right_canvas, padding=3)
        self._right_canvas_window = self._right_canvas.create_window(
            (0, 0), window=self._right_inner, anchor="nw"
        )
        # Cập nhật scroll region + width khi nội dung thay đổi
        self._right_inner.bind(
            "<Configure>",
            lambda e: self._right_canvas.configure(
                scrollregion=self._right_canvas.bbox("all")
            )
        )
        self._right_canvas.bind(
            "<Configure>",
            lambda e: self._right_canvas.itemconfig(
                self._right_canvas_window, width=e.width
            )
        )
        # Mouse wheel scroll cho panel phải
        self._right_canvas.bind("<MouseWheel>", self._on_right_mousewheel)
        self._right_inner.bind("<MouseWheel>", self._on_right_mousewheel)

        self.frame_schedule = ttk.LabelFrame(
            self._right_inner, text="Lịch dạy và dữ liệu nhập", padding=8
        )
        self.frame_schedule.pack(fill="both", expand=True, pady=0)
        self._build_schedule_panel(self.frame_schedule)

        # Bind mousewheel cho TẤT CẢ widget con trong panel phải
        # (để scroll hoạt động khi chuột hover lên bất kỳ widget nào)
        self._bind_mousewheel_recursive(self._right_inner, self._on_right_mousewheel)

    def _build_compact_status_panel(self, parent):
        """Mini dashboard dùng riêng cho compact mode.

        Compact mode trước đây ẩn gần hết widget và dễ để lại vùng trống.
        Panel này giữ các thông tin sống còn: trạng thái, progress, dừng an
        toàn và nút mở lại đầy đủ.
        """
        parent.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text="Sổ đầu bài tự động",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            textvariable=self.var_sched_live_progress,
            style="LiveGreen.TLabel",
            wraplength=360,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 6))

        self.compact_progressbar = ttk.Progressbar(
            parent,
            mode="determinate",
            length=360,
            style="LiveGreen.Horizontal.TProgressbar",
        )
        self.compact_progressbar.grid(row=2, column=0, sticky="ew")

        action_row = ttk.Frame(parent)
        action_row.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        action_row.columnconfigure(0, weight=1)

        ttk.Button(
            action_row,
            text="Mở đầy đủ",
            style="Primary.TButton",
            command=self._toggle_compact,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            action_row,
            text="Dừng an toàn",
            style="Danger.TButton",
            command=self._on_schedule_stop,
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))

    # ----- Chrome CDP Section -----

    def _build_cdp_section(self, parent):
        """Tạo phần kết nối Chrome CDP.

        Gồm: port input, Connect/Disconnect, status indicator,
        hướng dẫn mở Chrome, nút Inspect Page.
        """
        # Row 1: Port + Connect
        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=1)
        row1.columnconfigure(2, weight=1)
        row1.columnconfigure(3, weight=1)
        row1.columnconfigure(4, weight=1)
        ttk.Label(row1, text="Port:").grid(row=0, column=0, sticky="w")
        self.ent_cdp_port = ttk.Entry(
            row1, width=6, textvariable=self.var_cdp_port,
            state="readonly", justify="center"
        )
        self.ent_cdp_port.grid(row=0, column=1, sticky="w", padx=(4, 6))
        ttk.Label(row1, text="(cố định)").grid(row=0, column=2, sticky="w", padx=(0, 6))

        self.btn_cdp_connect = ttk.Button(
            row1, text="Kết nối trình duyệt", command=self._on_cdp_connect,
            style="Subtle.TButton",
        )
        self.btn_cdp_connect.grid(row=0, column=3, sticky="ew", padx=3)

        self.btn_cdp_disconnect = ttk.Button(
            row1, text="Ngắt", command=self._on_cdp_disconnect,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_cdp_disconnect.grid(row=0, column=4, sticky="ew", padx=(3, 0))

        self.btn_quick_prepare = ttk.Button(
            row1,
            text="Quét",
            command=self._on_quick_prepare,
            style="Primary.TButton",
            state="disabled",
        )
        self.btn_quick_prepare.grid(row=0, column=5, sticky="ew", padx=(6, 0))

        # Row 2: Status indicator
        self.lbl_cdp_status = ttk.Label(
            parent, text="○ Chưa kết nối",
            font=("Segoe UI", 10), foreground=UI_TEXT_MUTED
        )
        self.lbl_cdp_status.pack(fill="x", pady=(1, 0))

        row_login = ttk.Frame(parent)
        row_login.pack(fill="x", pady=(5, 0))
        row_login.columnconfigure(1, weight=1)
        row_login.columnconfigure(3, weight=1)
        ttk.Label(row_login, text="TK:").grid(row=0, column=0, sticky="w")
        self.ent_vnedu_username = ttk.Entry(
            row_login, textvariable=self.var_vnedu_username, width=18
        )
        self.ent_vnedu_username.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Label(row_login, text="MK:").grid(row=0, column=2, sticky="w")
        self.ent_vnedu_password = ttk.Entry(
            row_login, textvariable=self.var_vnedu_password, width=18, show="*"
        )
        self.ent_vnedu_password.grid(row=0, column=3, sticky="ew", padx=(4, 8))
        self.chk_show_vnedu_password = ttk.Checkbutton(
            row_login,
            text="Hiển thị mật khẩu",
            variable=self.var_show_vnedu_password,
            command=self._on_toggle_vnedu_password_visibility,
        )
        self.chk_show_vnedu_password.grid(row=0, column=4, sticky="w")

        # Row 3: Hướng dẫn + Inspect
        row3 = ttk.Frame(parent)
        row3.pack(fill="x", pady=(5, 0))
        for col_idx in range(5):
            row3.columnconfigure(col_idx, weight=1, uniform="cdp_actions")

        self.btn_launch_chrome = ttk.Button(
            row3, text="Mở Chrome",
            command=self._on_launch_chrome_debug,
            style="Subtle.TButton",
        )
        self.btn_launch_chrome.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.btn_copy_chrome_cmd = ttk.Button(
            row3, text="Copy lệnh",
            command=self._copy_chrome_cmd,
            style="Subtle.TButton",
        )
        self.btn_copy_chrome_cmd.grid(row=0, column=1, sticky="ew", padx=3)

        self.btn_inspect = ttk.Button(
            row3, text="Kiểm tra", command=self._on_inspect_page,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_inspect.grid(row=0, column=2, sticky="ew", padx=3)

        self.btn_recover_web = ttk.Button(
            row3, text="Khôi phục", command=self._on_recover_vnedu_ui,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_recover_web.grid(row=0, column=3, sticky="ew", padx=3)

        self.btn_discover_delete = ttk.Button(
            row3, text="Nút xóa", command=self._on_discover_delete_controls,
            state="disabled", style="Subtle.TButton",
        )
        self.btn_discover_delete.grid(row=0, column=4, sticky="ew", padx=(3, 0))

        row_auto = ttk.Frame(parent)
        row_auto.pack(fill="x", pady=(3, 0))
        self.btn_auto_login_run = ttk.Button(
            row_auto,
            text="Đăng nhập và chuẩn bị",
            command=self._on_auto_login_and_run,
            style="QuickGreen.TButton",
        )
        self.btn_auto_login_run.pack(fill="x")
        ttk.Label(
            parent,
            text="Flow: SSO → Quản lý trường học → Sổ đầu bài → Chi tiết sổ đầu bài",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(1, 0))

        self.cdp_live_progressbar = ttk.Progressbar(
            parent,
            mode="determinate",
            length=320,
            style="LiveGreen.Horizontal.TProgressbar",
        )
        self.cdp_live_progressbar.pack(fill="x", pady=(3, 0))
        self.lbl_cdp_live_progress = ttk.Label(
            parent,
            textvariable=self.var_sched_live_progress,
            style="LiveGreen.TLabel",
        )
        self.lbl_cdp_live_progress.pack(fill="x", pady=(1, 0))

        row_delete = ttk.LabelFrame(parent, text="Thao tác rủi ro", padding=6)
        row_delete.pack(fill="x", pady=(8, 0))
        self.btn_open_delete = ttk.Button(
            row_delete,
            text="Xóa dữ liệu sổ đầu bài...",
            command=self._on_open_delete_dialog,
            style="Danger.TButton",
        )
        self.btn_open_delete.pack(fill="x")
        ttk.Label(
            row_delete,
            text="Chỉ dùng sau khi đã quét preview và xác nhận rõ phạm vi xóa.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # Row 4: Thông tin Tuần + Lớp hiện tại trên VnEdu
        self.lbl_cdp_info = ttk.Label(
            parent, text="",
            font=("Segoe UI", 9, "italic"), foreground=UI_TEXT_MUTED
        )
        self.lbl_cdp_info.pack(fill="x", pady=(1, 0))

    def _on_toggle_vnedu_password_visibility(self):
        """Hiện/ẩn mật khẩu VnEdu để hạn chế nhập sai khi thao tác thủ công."""
        if not hasattr(self, "ent_vnedu_password"):
            return
        self.ent_vnedu_password.config(
            show="" if self.var_show_vnedu_password.get() else "*"
        )

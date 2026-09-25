"""Dựng giao diện, tooltip và bố cục responsive."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..config import (
    APP_COMPACT_WIDTH,
    APP_SIDEBAR_MIN_WIDTH,
    APP_SIDEBAR_WIDTH,
    APP_SUCCESS,
    APP_WARNING,
)
from ..scorebook_core import build_progress_caption, password_entry_show_value


class LayoutMixin:
    """Dựng giao diện, tooltip và bố cục responsive."""

    def _build_ui(self) -> None:
        section_padding = (10, 7, 10, 8)
        main = ttk.Frame(self.root, padding=(14, 12, 14, 10))
        self._main_frame = main
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=0, minsize=APP_SIDEBAR_WIDTH)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left_panel = ttk.Frame(main)
        self._left_panel = left_panel
        left_panel.configure(width=APP_SIDEBAR_WIDTH)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_panel.grid_propagate(False)
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(2, weight=0)

        right_panel = ttk.Frame(main)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(0, weight=6)
        right_panel.rowconfigure(1, weight=1)

        session = ttk.LabelFrame(left_panel, text="1. Phiên VNEDU", padding=section_padding)
        session.grid(row=0, column=0, sticky="ew")
        ttk.Label(session, text="CDP Port:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        port_entry = ttk.Entry(session, textvariable=self.port_var, width=12)
        port_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self._register_busy(port_entry, "normal")
        self._create_tooltip(port_entry, "Chrome DevTools debug port (mặc định: 9222)")
        ttk.Label(session, text="URL:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        url_entry = ttk.Entry(session, textvariable=self.url_var, width=28, state="readonly")
        url_entry.grid(row=1, column=1, columnspan=3, padx=6, pady=6, sticky="we")
        self._create_tooltip(url_entry, "URL trang VNEDU — tự động lấy từ trình duyệt Chrome")
        credentials_row = ttk.Frame(session)
        credentials_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=6, pady=6)
        credentials_row.columnconfigure(0, weight=1)

        account_row = ttk.Frame(credentials_row)
        account_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        account_row.columnconfigure(1, weight=1)
        ttk.Label(account_row, text="Tài khoản:").grid(row=0, column=0, padx=(0, 8), pady=0, sticky="w")
        user_entry = ttk.Entry(account_row, textvariable=self.username_var, width=20)
        user_entry.grid(row=0, column=1, sticky="ew")
        self._register_busy(user_entry, "normal")
        self._create_tooltip(user_entry, "Tên đăng nhập VNEDU (thường là SĐT giáo viên)")
        user_entry.bind("<Return>", lambda _e: self.on_load_scorebook_shell())

        password_group = ttk.Frame(credentials_row)
        password_group.grid(row=1, column=0, sticky="ew")
        password_group.columnconfigure(1, weight=1)
        ttk.Label(password_group, text="Mật khẩu:").grid(row=0, column=0, padx=(0, 8), pady=0, sticky="w")
        pw_row = ttk.Frame(password_group)
        pw_row.grid(row=0, column=1, sticky="ew")
        pw_row.columnconfigure(0, weight=1)
        self.password_entry = ttk.Entry(
            pw_row,
            textvariable=self.password_var,
            width=20,
            show=password_entry_show_value(False),
        )
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self._register_busy(self.password_entry, "normal")
        self.password_entry.bind("<Return>", lambda _e: self.on_load_scorebook_shell())
        show_check = ttk.Checkbutton(
            pw_row,
            text="Hiện mật khẩu",
            variable=self.show_password_var,
            command=self._apply_password_visibility,
        )
        show_check.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self._register_busy(show_check, "normal")
        button_row = ttk.Frame(session)
        button_row.grid(row=3, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="we")
        button_row.columnconfigure(1, weight=1)
        load_button = tk.Button(
            button_row,
            text="ĐĂNG NHẬP + VÀO SỔ ĐIỂM",
            command=self.on_load_scorebook_shell,
            bg=APP_WARNING,
            fg="#111827",
            activebackground="#eab308",
            activeforeground="#111827",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=14,
            pady=2,
            highlightthickness=0,
        )
        load_button.pack(side=tk.LEFT, padx=(0, 6))
        self._bind_hover(load_button, "#eab308")
        self._register_busy(load_button, "normal")
        self.session_status_label = ttk.Label(
            button_row,
            textvariable=self.status_var,
            foreground="#2f5d50",
            wraplength=210,
            justify=tk.LEFT,
        )
        self.session_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        self._bind_dynamic_wrap(self.session_status_label, reference=button_row, padding=245, min_width=160)
        session.columnconfigure(1, weight=1)
        session.columnconfigure(3, weight=1)

        context = ttk.LabelFrame(left_panel, text="2. Ngữ cảnh Sổ điểm", padding=section_padding)
        context.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(context, text="Khối:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.grade_combo = ttk.Combobox(context, textvariable=self.grade_var, state="disabled", width=18)
        self.grade_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")
        self.grade_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.grade_combo, "readonly")
        ttk.Label(context, text="Lớp:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.class_combo = ttk.Combobox(context, textvariable=self.class_var, state="disabled", width=18)
        self.class_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")
        self.class_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.class_combo, "readonly")
        ttk.Label(context, text="Môn:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.subject_combo = ttk.Combobox(context, textvariable=self.subject_var, state="disabled", width=18)
        self.subject_combo.grid(row=1, column=1, padx=6, pady=6, sticky="we")
        self.subject_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.subject_combo, "readonly")
        ttk.Label(context, text="Học kỳ:").grid(row=1, column=2, padx=6, pady=6, sticky="w")
        self.term_combo = ttk.Combobox(context, textvariable=self.term_var, state="disabled", width=18)
        self.term_combo.grid(row=1, column=3, padx=6, pady=6, sticky="we")
        self.term_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.term_combo, "readonly")
        self.progress_canvas = tk.Canvas(
            context,
            height=28,
            background="#eefdf3",
            highlightthickness=1,
            highlightbackground="#bbf7d0",
            relief=tk.FLAT,
        )
        self.progress_canvas.grid(row=2, column=0, columnspan=4, padx=6, pady=(6, 4), sticky="ew")
        self._progress_fill_id = self.progress_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")
        self._progress_text_id = self.progress_canvas.create_text(
            8,
            14,
            anchor="w",
            fill="#1f4729",
            font=("Segoe UI", 9, "bold"),
            text=build_progress_caption(0.0, self._progress_message),
        )
        self.progress_canvas.bind("<Configure>", lambda _e: self._render_progress())
        context.columnconfigure(1, weight=1)
        context.columnconfigure(3, weight=1)

        info = ttk.LabelFrame(left_panel, text="3. Thông tin phiên", padding=section_padding)
        info.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        for row, title, var in [
            (0, "Cửa sổ:", self.window_title_var),
            (1, "Giáo viên:", self.teacher_var),
            (2, "Quyền:", self.permission_var),
            (3, "Số cột:", self.column_count_var),
            (4, "Ô nhận xét:", self.comment_input_var),
        ]:
            ttk.Label(info, text=title).grid(row=row, column=0, padx=6, pady=4, sticky="nw")
            info_value = ttk.Label(info, textvariable=var, justify=tk.LEFT, wraplength=320)
            info_value.grid(
                row=row,
                column=1,
                padx=6,
                pady=4,
                sticky="ew",
            )
            self._bind_dynamic_wrap(info_value, reference=info, padding=54, min_width=200)
        info_help = ttk.Label(
            info,
            text="App sẽ quét danh sách học sinh, nhận dạng tên + điểm và chuẩn bị hàng chờ ghi xuống live browser.",
            wraplength=320,
            justify=tk.LEFT,
        )
        info.rowconfigure(6, weight=1)
        info.columnconfigure(1, weight=1)

        score_frame = ttk.LabelFrame(right_panel, text="4. Nhập Điểm Bằng Giọng Nói", padding=(10, 8, 10, 10))
        score_frame.grid(row=0, column=0, sticky="nsew")
        score_frame.columnconfigure(0, weight=1)

        controls = ttk.Frame(score_frame)
        controls.pack(fill=tk.X, pady=(2, 0))
        for column in range(4):
            controls.columnconfigure(column, weight=1, uniform="score_controls")
        ttk.Label(controls, text="Cột điểm đích:").grid(row=0, column=0, padx=(0, 6), pady=4, sticky="w")
        self.target_score_combo = ttk.Combobox(
            controls,
            textvariable=self.target_score_column_var,
            state="readonly",
            width=24,
        )
        self.target_score_combo.grid(row=0, column=1, columnspan=2, padx=(0, 6), pady=4, sticky="ew")
        self.target_score_combo.bind("<<ComboboxSelected>>", self._on_target_score_selected, add="+")
        self._register_busy(self.target_score_combo, "readonly")
        import_button = ttk.Button(controls, text="📄 NHẬP TỪ EXCEL", command=self.on_import_scores_from_file)
        import_button.grid(row=0, column=3, padx=(0, 0), pady=4, sticky="ew")
        self._register_busy(import_button, "normal")
        self.btn_ptt_toggle = tk.Button(
            controls,
            text="🎤 BỘ ĐÀM: TẮT",
            command=self.toggle_ptt,
            bg="#64748b",
            fg="#ffffff",
            activebackground="#475569",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=12,
            pady=2,
            highlightthickness=0,
        )
        self.btn_ptt_toggle.grid(row=1, column=0, padx=(0, 6), pady=(8, 4), sticky="ew")
        self._bind_hover(self.btn_ptt_toggle, "#475569")
        self._register_busy(self.btn_ptt_toggle, "normal")
        clear_button = ttk.Button(controls, text="Xóa điểm chờ", command=self.on_clear_pending_scores)
        clear_button.grid(row=1, column=1, padx=(0, 6), pady=(8, 4), sticky="ew")
        self._register_busy(clear_button, "normal")
        round_button = ttk.Button(controls, text="LÀM TRÒN ĐIỂM", command=self.on_round_pending_scores)
        round_button.grid(row=1, column=2, padx=(0, 6), pady=(8, 4), sticky="ew")
        self._register_busy(round_button, "normal")
        apply_score_button = tk.Button(
            controls,
            text="GHI ĐIỂM LÊN WEB",
            command=self.on_apply_pending_scores,
            bg=APP_SUCCESS,
            fg="#ffffff",
            activebackground="#15803d",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=12,
            pady=2,
            highlightthickness=0,
        )
        apply_score_button.grid(row=1, column=3, padx=(0, 0), pady=(8, 4), sticky="ew")
        self._bind_hover(apply_score_button, "#15803d")
        self._register_busy(apply_score_button, "normal")
        auto_save_check = ttk.Checkbutton(
            controls,
            text="Tự bấm Lưu sau khi ghi",
            variable=self.auto_save_scores_var,
        )
        auto_save_check.grid(row=2, column=0, padx=(0, 12), pady=(4, 0), sticky="w")
        self._register_busy(auto_save_check, "normal")
        self.voice_meter_canvas = tk.Canvas(
            controls,
            height=28,
            background="#eefdf3",
            highlightthickness=1,
            highlightbackground="#bbf7d0",
            relief=tk.FLAT,
        )
        self.voice_meter_canvas.grid(row=2, column=1, columnspan=3, padx=(6, 0), pady=(4, 0), sticky="ew")
        self._voice_meter_fill_id = self.voice_meter_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")
        self._voice_meter_peak_id = self.voice_meter_canvas.create_line(0, 4, 0, 24, fill="#176a34", width=2)
        self._voice_meter_text_id = self.voice_meter_canvas.create_text(
            10,
            14,
            anchor="w",
            fill="#20532b",
            font=("Segoe UI", 9, "bold"),
            text="Micro realtime: bộ đàm đang tắt",
        )
        self.voice_meter_canvas.bind("<Configure>", lambda _e: self._render_voice_meter())
        controls_help = ttk.Label(
            controls,
            text="Chọn cột điểm để app tự quét danh sách. Bạn vẫn có thể sửa trực tiếp cột Điểm chờ trong bảng xem trước.",
            wraplength=520,
            justify=tk.LEFT,
        )

        voice_row = ttk.Frame(score_frame)
        voice_row.pack(fill=tk.X, pady=(6, 6))
        voice_row.columnconfigure(0, weight=1)
        voice_status = ttk.Label(voice_row, textvariable=self.voice_status_var, foreground="#1f6f43", justify=tk.LEFT)
        voice_status.grid(
            row=0,
            column=0,
            sticky="ew",
        )
        self._bind_dynamic_wrap(voice_status, reference=voice_row, padding=24, min_width=180)
        voice_help = ttk.Label(
            voice_row,
            text="Giữ Space: đọc 'Tên + điểm' hoặc tách riêng 'Tên' rồi 'điểm'. Nói 'xóa' để undo. Ctrl+Z / Delete / Double-click để sửa.",
            wraplength=540,
            justify=tk.LEFT,
        )

        summary_panel = tk.Frame(
            score_frame,
            bg="#eff6ff",
            highlightthickness=1,
            highlightbackground="#bfdbfe",
            bd=0,
        )
        summary_panel.pack(fill=tk.X, pady=(0, 8))
        summary_panel.grid_columnconfigure(0, weight=1)
        summary_label = tk.Label(
            summary_panel,
            textvariable=self.voice_summary_var,
            bg="#eff6ff",
            fg="#1e3a8a",
            anchor="w",
            justify=tk.LEFT,
            wraplength=580,
            padx=10,
            pady=8,
        )
        summary_label.grid(row=0, column=0, sticky="ew")
        self._bind_dynamic_wrap(summary_label, reference=summary_panel, padding=24, min_width=260)

        tree_frame = ttk.Frame(score_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(1, weight=1)
        # IMP-B7: Search/filter box để tìm học sinh nhanh
        search_row = ttk.Frame(tree_frame)
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="🔍 Tìm:").grid(row=0, column=0, padx=(0, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_args: self._filter_score_tree())
        search_entry = ttk.Entry(search_row, textvariable=self._search_var, width=30)
        search_entry.grid(row=0, column=1, sticky="ew")
        tree_container = ttk.Frame(tree_frame)
        tree_container.grid(row=1, column=0, sticky="nsew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)
        columns = (
            "row_index",
            "student_name",
            "current_score",
            "pending_score",
            "recognized_text",
            "match_score",
            "status",
        )
        self.preview_tree = ttk.Treeview(
            tree_container,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=17,
        )
        headings = {
            "row_index": "STT",
            "student_name": "Họ tên",
            "current_score": "Điểm hiện tại",
            "pending_score": "Điểm chờ ghi",
            "recognized_text": "Lần nhận dạng gần nhất",
            "match_score": "Khớp",
            "status": "Trạng thái",
        }
        widths = {
            "row_index": 56,
            "student_name": 320,
            "current_score": 100,
            "pending_score": 108,
            "recognized_text": 300,
            "match_score": 72,
            "status": 160,
        }
        min_widths = {
            "row_index": 48,
            "student_name": 240,
            "current_score": 88,
            "pending_score": 96,
            "recognized_text": 210,
            "match_score": 64,
            "status": 100,
        }
        anchors = {
            "row_index": tk.CENTER,
            "student_name": tk.W,
            "current_score": tk.CENTER,
            "pending_score": tk.CENTER,
            "recognized_text": tk.W,
            "match_score": tk.CENTER,
            "status": tk.W,
        }
        for column in columns:
            self.preview_tree.heading(column, text=headings[column], anchor=anchors[column])
            self.preview_tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                anchor=anchors[column],
                stretch=column in {"student_name", "recognized_text", "status"},
            )
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.preview_tree.yview)
        tree_scroll_y.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        tree_scroll_x = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.preview_tree.xview)
        tree_scroll_x.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.preview_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.preview_tree.bind("<Double-1>", self._on_tree_double_click)
        self.preview_tree.bind("<Delete>", self._on_tree_delete)
        self.preview_tree.bind("<Button-3>", self._on_tree_right_click)
        self.preview_tree.tag_configure("pending", foreground="#9a3412", background="#fff7ed")
        self.preview_tree.tag_configure("saved", foreground="#166534", background="#ecfdf5")
        self.preview_tree.tag_configure("filled", foreground="#854d0e", background="#fef9c3")
        self.preview_tree.tag_configure("error", foreground="#991b1b", background="#fee2e2")
        self._autosize_student_name_column()

        log_frame = ttk.LabelFrame(right_panel, text="6. Nhật ký", padding=section_padding)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=7, wrap="word", font=("Consolas", 10), state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("log_success", background="#d4edda")
        self.log_text.tag_configure("log_warning", background="#f8d7da")
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.log_text.configure(yscrollcommand=scroll.set)
        self._render_progress()

    def _register_busy(self, widget: tk.Misc, normal_state: str) -> None:
        self._busy_widgets.append((widget, normal_state))

    def _bind_hover(self, btn: tk.Button, hover_bg: str) -> None:
        """Adds state-aware mouse-enter/leave hover color to a tk.Button."""
        def on_enter(_event: tk.Event) -> None:
            try:
                btn._hover_prev_bg = btn.cget("bg")  # type: ignore[attr-defined]
                btn.configure(bg=hover_bg)
            except tk.TclError:
                pass

        def on_leave(_event: tk.Event) -> None:
            try:
                btn.configure(bg=getattr(btn, "_hover_prev_bg", btn.cget("bg")))
            except tk.TclError:
                pass

        btn.bind("<Enter>", on_enter, add="+")
        btn.bind("<Leave>", on_leave, add="+")

    def _on_root_destroy(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self.root:
            return
        self._stop_tree_editor(commit=False)
        self._disable_ptt()
        self._stop_ptt_worker()
        self._shutdown_voice_recognize_executor()  # PERF #1: dọn pool song song
        self._cancel_context_autosync()
        # BUG-07 FIX: Cancel pending auto-scan để tránh TclError sau khi root bị destroy
        self._cancel_pending_auto_scan()
        # BUG-11 FIX: Lưu config và access cache trước khi đóng app
        try:
            self._save_config()
        except Exception as _save_err:
            print(f"[DEBUG] Config save on close failed: {_save_err}")
        try:
            self._save_access_cache()
        except Exception as _cache_err:
            print(f"[DEBUG] Access cache save on close failed: {_cache_err}")
        if self._poll_after_id is None:
            return
        try:
            self.root.after_cancel(self._poll_after_id)
        except (tk.TclError, RuntimeError):
            pass
        self._poll_after_id = None

    def _on_window_state_change(self, event: tk.Event) -> None:
        """Switch to a compact workspace only when the actual window width is narrow."""
        if event.widget is not self.root:
            return
        self._sync_responsive_layout()

    def _sync_responsive_layout(self) -> None:
        """Show/hide secondary panels based on the current usable window width."""
        try:
            current_state = self.root.state()
            current_width = int(self.root.winfo_width())
        except tk.TclError:
            return
        if current_width <= 1:
            try:
                self.root.after(50, self._sync_responsive_layout)
            except tk.TclError:
                pass
            return
        compact = current_width < APP_COMPACT_WIDTH
        if (
            current_state == self._prev_window_state
            and compact == self._compact_layout
            and bool(self._left_panel.winfo_ismapped())
        ):
            return
        self._prev_window_state = current_state
        self._compact_layout = compact
        # Giữ sidebar luôn thấy được; chỉ rút gọn bảng khi cửa sổ thật sự hẹp.
        self._left_panel.grid()
        sidebar_width = max(
            APP_SIDEBAR_MIN_WIDTH,
            min(APP_SIDEBAR_WIDTH, int(current_width * 0.34)),
        )
        try:
            self._left_panel.configure(width=sidebar_width)
        except tk.TclError:
            pass
        self._main_frame.columnconfigure(0, weight=0, minsize=sidebar_width, uniform="")
        self._main_frame.columnconfigure(1, weight=1, uniform="")
        self._apply_tree_headings(compact=compact)

    def _apply_tree_headings(self, *, compact: bool = False) -> None:
        """IMP-D2: Cập nhật column headings — rút gọn khi compact (restore-down)."""
        if not hasattr(self, "preview_tree"):
            return
        full_headings = {
            "row_index": "STT",
            "student_name": "Họ tên",
            "current_score": "Điểm hiện tại",
            "pending_score": "Điểm chờ ghi",
            "recognized_text": "Lần nhận dạng gần nhất",
            "match_score": "Khớp",
            "status": "Trạng thái",
        }
        compact_headings = {
            "row_index": "#",
            "student_name": "Họ tên",
            "current_score": "HT",
            "pending_score": "Chờ",
            "recognized_text": "Nhận dạng",
            "match_score": "%",
            "status": "TT",
        }
        headings = compact_headings if compact else full_headings
        for col, text in headings.items():
            try:
                self.preview_tree.heading(col, text=text)
            except tk.TclError:
                pass

    def _create_tooltip(self, widget: tk.Widget, text: str) -> None:
        """IMP-D5: Tạo tooltip đơn giản cho widget — hiện sau 600ms hover."""
        tooltip_window = [None]  # dùng list để có thể thay đổi trong closure

        def _show(event: tk.Event) -> None:
            if tooltip_window[0] is not None:
                return
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
            label = tk.Label(tw, text=text, justify=tk.LEFT, background="#ffffdd",
                             relief=tk.SOLID, borderwidth=1, font=("Segoe UI", 9), padx=6, pady=4)
            label.pack()
            tooltip_window[0] = tw

        def _hide(_event: tk.Event | None = None) -> None:
            tw = tooltip_window[0]
            if tw is not None:
                tw.destroy()
                tooltip_window[0] = None

        def _schedule(event: tk.Event) -> None:
            widget._tooltip_after_id = widget.after(600, lambda: _show(event))  # type: ignore[attr-defined]

        def _cancel(_event: tk.Event | None = None) -> None:
            after_id = getattr(widget, "_tooltip_after_id", None)
            if after_id is not None:
                widget.after_cancel(after_id)
                widget._tooltip_after_id = None  # type: ignore[attr-defined]
            _hide()

        widget.bind("<Enter>", _schedule, add="+")
        widget.bind("<Leave>", _cancel, add="+")
        widget.bind("<ButtonPress>", _cancel, add="+")

"""Dựng các khung giao diện chính."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict

from ..automation.client import VnEduScoreAutomation
from ..progress import build_progress_caption, password_entry_show_value


class LayoutMixin:
    """Dựng các khung giao diện chính."""

    def _register_busy_widget(self, widget: tk.Misc, normal_state: str) -> tk.Misc:
        """Tracks one widget so the busy-state guard can disable and restore it later."""
        self._busy_widgets.append((widget, normal_state))
        return widget

    def _on_root_destroy(self, event: tk.Event | None = None) -> None:
        """Cancels pending Tk callbacks when the root window is being destroyed."""
        if event is not None and event.widget is not self.root:
            return
        for attr_name in ("_poll_after_id", "_context_apply_after_id", "_config_autosave_after_id"):
            after_id = getattr(self, attr_name, None)
            if not after_id:
                continue
            setattr(self, attr_name, None)
            try:
                self.root.after_cancel(after_id)
            except (tk.TclError, RuntimeError):
                continue

    def _primary_action_button_options(self) -> Dict[str, object]:
        """Returns the shared visual styling for the main live-action buttons."""
        return {
            "bg": "#f0c93d",
            "fg": "#000000",
            "activebackground": "#ddb62f",
            "activeforeground": "#000000",
            "disabledforeground": "#a6924a",
            "font": ("Segoe UI", 10, "bold"),
            "relief": tk.SOLID,
            "borderwidth": 1,
            "padx": 14,
            "pady": 2,
            "highlightthickness": 0,
        }

    def _apply_comments_button_options(self) -> Dict[str, object]:
        """Returns the visual styling for the apply-comments action button."""
        return {
            "bg": "#58b957",
            "fg": "#000000",
            "activebackground": "#499f49",
            "activeforeground": "#000000",
            "disabledforeground": "#6f9c6f",
            "font": ("Segoe UI", 10, "bold"),
            "relief": tk.SOLID,
            "borderwidth": 1,
            "padx": 14,
            "pady": 2,
            "highlightthickness": 0,
        }

    def _build_session_frame(self, parent: tk.Misc) -> None:
        """Builds the VNEDU session/config controls at the top of the window."""
        session_frame = ttk.LabelFrame(parent, text="1. Phiên VNEDU")
        session_frame.pack(fill=tk.X)

        ttk.Label(session_frame, text="CDP Port:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.port_entry = ttk.Entry(session_frame, textvariable=self.port_var, width=10)
        self.port_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self._register_busy_widget(self.port_entry, "normal")

        ttk.Label(session_frame, text="URL:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.url_entry = ttk.Entry(session_frame, textvariable=self.url_var, width=58)
        self.url_entry.grid(row=0, column=3, padx=6, pady=6, sticky="we")
        self._register_busy_widget(self.url_entry, "normal")

        ttk.Label(session_frame, text="Tài khoản:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.username_entry = ttk.Entry(session_frame, textvariable=self.username_var, width=26)
        self.username_entry.grid(row=1, column=1, padx=6, pady=6, sticky="w")
        self._register_busy_widget(self.username_entry, "normal")

        ttk.Label(session_frame, text="Mật khẩu:").grid(row=1, column=2, padx=6, pady=6, sticky="w")
        password_row = ttk.Frame(session_frame)
        password_row.grid(row=1, column=3, padx=6, pady=6, sticky="w")
        self.password_entry = ttk.Entry(
            password_row,
            textvariable=self.password_var,
            width=26,
            show=password_entry_show_value(bool(self.show_password_var.get())),
        )
        self.password_entry.pack(side=tk.LEFT)
        self._register_busy_widget(self.password_entry, "normal")
        self.show_password_check = ttk.Checkbutton(
            password_row,
            text="Hiện mật khẩu",
            variable=self.show_password_var,
            command=self._apply_password_visibility,
        )
        self.show_password_check.pack(side=tk.LEFT, padx=(8, 0))
        self._register_busy_widget(self.show_password_check, "normal")

        button_row = ttk.Frame(session_frame)
        button_row.grid(row=2, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="we")

        self.open_button = ttk.Button(button_row, text="Mở VNEDU", command=self.on_open_web)
        self.open_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.open_button, "normal")

        self.save_session_button = ttk.Button(
            button_row,
            text="Lưu cấu hình",
            command=self.on_save_config,
        )
        self.save_session_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.save_session_button, "normal")

        self.load_shell_button = tk.Button(
            button_row,
            text="ĐĂNG NHẬP & LOAD DATA",
            command=self.on_load_scorebook_shell,
            **self._primary_action_button_options(),
        )
        self.load_shell_button.pack(side=tk.LEFT)
        self._register_busy_widget(self.load_shell_button, "normal")

        self.progress_canvas = tk.Canvas(
            button_row,
            height=24,
            background="#edf5ed",
            highlightthickness=1,
            highlightbackground="#b5cbb5",
            relief=tk.FLAT,
        )
        self.progress_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(18, 0))
        self._progress_fill_id = self.progress_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")
        self._progress_text_id = self.progress_canvas.create_text(
            8,
            12,
            anchor="w",
            fill="#1f4729",
            font=("Segoe UI", 9, "bold"),
            text=build_progress_caption(self._progress_value, self._progress_message),
        )
        self.progress_canvas.bind("<Configure>", self._on_progress_canvas_configure)
        self._render_progress_bar()

        ttk.Label(session_frame, textvariable=self.status_var, foreground="#2f5d50").grid(
            row=3, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="w"
        )
        session_frame.columnconfigure(3, weight=1)

    def _build_context_frame(self, parent: tk.Misc) -> None:
        """Builds the scorebook context selectors and apply button."""
        context_frame = ttk.LabelFrame(parent, text="2. Ngữ cảnh Sổ điểm (beta)")
        context_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        ttk.Label(context_frame, text="Khối:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.grade_combo = ttk.Combobox(context_frame, textvariable=self.grade_var, state="readonly", width=20)
        self.grade_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(context_frame, text="Lớp:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.class_combo = ttk.Combobox(context_frame, textvariable=self.class_var, state="readonly", width=20)
        self.class_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")

        ttk.Label(context_frame, text="Môn:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.subject_combo = ttk.Combobox(context_frame, textvariable=self.subject_var, state="readonly", width=20)
        self.subject_combo.grid(row=1, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(context_frame, text="Học kỳ:").grid(row=1, column=2, padx=6, pady=6, sticky="w")
        self.term_combo = ttk.Combobox(context_frame, textvariable=self.term_var, state="readonly", width=20)
        self.term_combo.grid(row=1, column=3, padx=6, pady=6, sticky="we")

        context_button_row = ttk.Frame(context_frame)
        context_button_row.grid(row=2, column=0, columnspan=4, padx=6, pady=(0, 6), sticky="w")

        self.apply_context_button = ttk.Button(
            context_button_row,
            text="Áp ngữ cảnh lên web",
            command=self.on_apply_selected_context,
        )
        self.apply_context_button.pack(side=tk.LEFT)

        self._register_busy_widget(self.grade_combo, "readonly")
        self._register_busy_widget(self.class_combo, "readonly")
        self._register_busy_widget(self.subject_combo, "readonly")
        self._register_busy_widget(self.term_combo, "readonly")
        self._register_busy_widget(self.apply_context_button, "normal")

        self.grade_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        self.class_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        self.subject_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        self.term_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        context_frame.columnconfigure(1, weight=1)
        context_frame.columnconfigure(3, weight=1)

    def _build_detected_columns_frame(self, parent: tk.Misc) -> None:
        """Builds the read-only panel showing detected score/comment columns."""
        detected_frame = ttk.LabelFrame(parent, text="3. Cột tự nhận diện")
        detected_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        ttk.Label(detected_frame, text="Cột điểm dùng để xét:").grid(row=0, column=0, padx=6, pady=4, sticky="nw")
        self.score_source_combo = ttk.Combobox(
            detected_frame,
            textvariable=self.score_source_var,
            state="readonly",
            width=28,
        )
        self.score_source_combo.grid(row=0, column=1, padx=6, pady=4, sticky="we")
        self.score_source_combo.bind("<<ComboboxSelected>>", self.on_score_source_selection_changed)
        self._register_busy_widget(self.score_source_combo, "readonly")
        ttk.Label(detected_frame, text="Cột điểm đề xuất:").grid(row=1, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_score_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(
            row=1, column=1, padx=6, pady=4, sticky="w"
        )
        ttk.Label(detected_frame, text="Cột nhận xét:").grid(row=2, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_comment_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(
            row=2, column=1, padx=6, pady=4, sticky="w"
        )
        ttk.Label(detected_frame, text="Ứng viên điểm:").grid(row=3, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_candidates_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(row=3, column=1, padx=6, pady=4, sticky="w")
        ttk.Label(detected_frame, text="Lý do chọn:").grid(row=4, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_reason_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(row=4, column=1, padx=6, pady=4, sticky="w")
        detected_frame.columnconfigure(1, weight=1)

    def _build_rules_frame(self, parent: tk.Misc) -> None:
        """Builds the rule editor, apply controls, and scrollable rule form area."""
        rules_frame = ttk.LabelFrame(parent, text="4. Rule nhận xét")
        rules_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        rule_ctrl_row = ttk.Frame(rules_frame)
        rule_ctrl_row.pack(fill=tk.X, padx=6, pady=(6, 4))
        ttk.Label(rule_ctrl_row, text="Số rule:").pack(side=tk.LEFT)
        self.num_forms_entry = ttk.Entry(rule_ctrl_row, textvariable=self.num_forms_var, width=6)
        self.num_forms_entry.pack(side=tk.LEFT, padx=(6, 8))
        self._register_busy_widget(self.num_forms_entry, "normal")

        self.build_rules_button = ttk.Button(
            rule_ctrl_row,
            text="Tạo form rule",
            command=self.on_build_rule_forms,
        )
        self.build_rules_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.build_rules_button, "normal")

        self.export_default_rules_button = ttk.Button(
            rule_ctrl_row,
            text="Nhận xét mặc định",
            command=self.on_export_default_rules,
        )
        self.export_default_rules_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.export_default_rules_button, "normal")

        self.apply_comments_button = tk.Button(
            rule_ctrl_row,
            text="GHI NHẬN XÉT",
            command=self.on_apply_comments,
            **self._apply_comments_button_options(),
        )
        self.apply_comments_button.pack(side=tk.RIGHT)
        self._register_busy_widget(self.apply_comments_button, "normal")

        self.auto_save_check = ttk.Checkbutton(
            rule_ctrl_row,
            text="Tự bấm Lưu",
            variable=self.auto_save_var,
        )
        self.auto_save_check.pack(side=tk.LEFT, padx=(12, 0))
        self._register_busy_widget(self.auto_save_check, "normal")

        self.allow_comment_overwrite_check = ttk.Checkbutton(
            rule_ctrl_row,
            text="Cho phép ghi đè nhận xét chữ đã có",
            variable=self.allow_comment_overwrite_var,
        )
        self.allow_comment_overwrite_check.pack(side=tk.LEFT, padx=(12, 0))
        self._register_busy_widget(self.allow_comment_overwrite_check, "normal")

        rule_hint = ttk.Label(
            rules_frame,
            text=(
                "Rule đầu tiên khớp sẽ được dùng. "
                "App dùng cột điểm bạn chọn ở trên để ghi trực tiếp lên web."
            ),
            justify=tk.LEFT,
        )
        rule_hint.pack(fill=tk.X, padx=6, pady=(0, 4))

        self.rule_canvas = tk.Canvas(rules_frame, height=180, highlightthickness=0)
        self.rule_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=(0, 6))
        self.rule_scrollbar = ttk.Scrollbar(rules_frame, orient=tk.VERTICAL, command=self.rule_canvas.yview)
        self.rule_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=(0, 6))
        self.rule_canvas.configure(yscrollcommand=self.rule_scrollbar.set)
        self.rule_form_container = ttk.Frame(self.rule_canvas)
        self.rule_canvas_window = self.rule_canvas.create_window(
            (0, 0),
            window=self.rule_form_container,
            anchor="nw",
        )
        self.rule_form_container.bind(
            "<Configure>",
            lambda _event: self.rule_canvas.configure(scrollregion=self.rule_canvas.bbox("all")),
        )
        self.rule_canvas.bind(
            "<Configure>",
            lambda event: self.rule_canvas.itemconfigure(self.rule_canvas_window, width=event.width),
        )

    def _build_ui(self) -> None:
        """Builds the initial skeleton GUI."""
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)
        self._build_session_frame(main)

        top_panels_row = ttk.Frame(main)
        top_panels_row.pack(fill=tk.X, pady=(10, 0))
        self._build_context_frame(top_panels_row)
        self._build_detected_columns_frame(top_panels_row)
        self._build_rules_frame(main)

    def _build_automation(self) -> VnEduScoreAutomation:
        """Builds the CDP automation object from current UI state."""
        self._sync_access_cache_identity()
        try:
            port = int(self.port_var.get().strip())
        except ValueError as error:
            raise ValueError("CDP Port phải là số nguyên hợp lệ.") from error

        url = self.url_var.get().strip()
        if not url:
            raise ValueError("URL VNEDU không được để trống.")
        return VnEduScoreAutomation(debug_port=port, target_url=url)

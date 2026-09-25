"""Khu Lịch dạy: dựng panel và chế độ lịch."""

import re
import tkinter as tk
import unicodedata
from tkinter import ttk

from ..config import (
    _HAS_CDP,
    SCHEDULE_DAY_LABELS,
    SCHEDULE_DAYS,
    SCHEDULE_MODE_KHDH,
    SCHEDULE_MODE_MANUAL,
    UI_PRIMARY,
)


class SchedulePanelMixin:
    """Khu Lịch dạy: dựng panel và chế độ lịch."""

    # ----- Schedule Panel (Lịch dạy) -----

    def _build_schedule_panel(self, parent):
        """Tạo panel Lịch dạy — cấu hình Thứ/Buổi/Tiết + Quét & Nhập.

        Layout:
        - Row 1: Tuần range + Lớp single-select combobox
        - Row 2: Nút tải DS Lớp cho schedule
        - Grid: Thứ 2→7, mỗi thứ: Buổi combobox + 5 checkbox Tiết
        - Buttons: Quét & Nhập / Dừng
        - Status label
        """
        if not _HAS_CDP:
            ttk.Label(
                parent,
                text="⚠ Module chrome_bridge.py không tìm thấy.",
                foreground="red", wraplength=300
            ).pack(fill="x")
            return

        # Row 1: Tuần range + Lớp
        row_top = ttk.Frame(parent)
        row_top.pack(fill="x", pady=2)
        row_top.columnconfigure(5, weight=1)
        row_top.columnconfigure(6, weight=0)

        ttk.Label(row_top, text="Tuần:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            row_top, from_=1, to=52, width=4,
            textvariable=self.var_sched_tuan_from
        ).grid(row=0, column=1, sticky="w", padx=(4, 2))
        ttk.Label(row_top, text="→").grid(row=0, column=2, sticky="w", padx=2)
        ttk.Spinbox(
            row_top, from_=1, to=52, width=4,
            textvariable=self.var_sched_tuan_to
        ).grid(row=0, column=3, sticky="w", padx=2)

        ttk.Label(row_top, text="Lớp:").grid(row=0, column=4, sticky="w", padx=(10, 2))
        self.cmb_sched_lop = ttk.Combobox(
            row_top, textvariable=self.var_sched_lop,
            state="readonly", width=10
        )
        self.cmb_sched_lop.grid(row=0, column=5, sticky="ew", padx=(2, 6))
        self.cmb_sched_lop.bind(
            "<<ComboboxSelected>>", self._on_sched_progress_context_changed
        )

        self.btn_sched_load_lop = ttk.Button(
            row_top, text="Tải lớp",
            command=self._on_sched_load_lop, state="disabled"
        )
        self.btn_sched_load_lop.grid(row=0, column=6, sticky="ew")

        row_multi_lop = ttk.Frame(parent)
        row_multi_lop.pack(fill="x", pady=(0, 3))
        ttk.Label(row_multi_lop, text="Lớp KHDH:").pack(side="left")
        self.ent_sched_lop_multi = ttk.Entry(
            row_multi_lop,
            textvariable=self.var_sched_lop_multi,
            width=32,
        )
        self.ent_sched_lop_multi.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Label(
            parent,
            text="💡 Mode KHDH: nhập nhiều lớp cách nhau bằng dấu phẩy. Để trống = dùng lớp đang chọn.",
            style="Hint.TLabel",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(0, 2))

        # Grid: Thứ × Buổi × Tiết. Ẩn khi chạy theo KHDH vì mode này
        # đọc trực tiếp row đỏ live, không dùng lịch TKB nhập tay.
        self.frame_sched_manual_grid = ttk.Frame(parent)
        self.frame_sched_manual_grid.pack(fill="x", pady=(4, 2))
        grid_frame = ttk.Frame(self.frame_sched_manual_grid)
        grid_frame.pack(fill="x")
        slot_col_pad = 4
        slot_col_minsize = 28
        for col_idx in range(2, 7):
            grid_frame.grid_columnconfigure(col_idx, minsize=slot_col_minsize)

        # Header labels
        ttk.Label(grid_frame, text="Thứ", width=4, anchor="center",
                  font=("Segoe UI", 8, "bold")).grid(row=0, column=0, padx=1)
        ttk.Label(grid_frame, text="Buổi", width=6, anchor="center",
                  font=("Segoe UI", 8, "bold")).grid(row=0, column=1, padx=1)
        for t in range(1, 6):
            ttk.Label(grid_frame, text=f"T{t}", width=3, anchor="center",
                      font=("Segoe UI", 8, "bold")).grid(row=0, column=1+t, padx=slot_col_pad)

        # Data rows — Thứ 2 → CN (7 rows)
        BUOI_OPTIONS = ["---", "Sáng", "Chiều", "Cả hai"]
        for idx, thu in enumerate(SCHEDULE_DAYS):
            row_num = idx + 1

            # Label Thứ (dùng SCHEDULE_DAY_LABELS để hiển thị "CN" cho thu=8)
            day_label = SCHEDULE_DAY_LABELS.get(thu, str(thu))
            ttk.Label(grid_frame, text=day_label, width=4, anchor="center",
                      font=("Segoe UI", 9)).grid(row=row_num, column=0, padx=1, pady=1)

            # Combobox Buổi
            cmb_buoi = ttk.Combobox(
                grid_frame, textvariable=self._sched_buoi[thu],
                values=BUOI_OPTIONS, state="readonly", width=5
            )
            cmb_buoi.grid(row=row_num, column=1, padx=1, pady=1)
            # Khi đổi buổi → enable/disable checkboxes tương ứng
            cmb_buoi.bind("<<ComboboxSelected>>",
                          lambda e, t=thu: self._on_sched_buoi_changed(t))

            # 5 checkboxes Tiết — mặc định dùng buổi Sáng
            for tiet_idx in range(5):
                cb = tk.Checkbutton(
                    grid_frame,
                    variable=self._sched_grid[(thu, "S")][tiet_idx],
                    image=self._sched_checkbox_images["off"],
                    selectimage=self._sched_checkbox_images["on"],
                    indicatoron=False,
                    relief="flat",
                    offrelief="flat",
                    overrelief="flat",
                    borderwidth=0,
                    highlightthickness=0,
                    takefocus=0,
                    bg=self.root.cget("bg"),
                    activebackground=self.root.cget("bg"),
                    disabledforeground="#8a8a8a",
                    padx=0,
                    pady=0,
                )
                cb.grid(row=row_num, column=2+tiet_idx, padx=slot_col_pad, pady=1)
                self._sched_checkbuttons[(thu, tiet_idx)] = cb

        # Chú thích
        hint_row = ttk.Frame(self.frame_sched_manual_grid)
        hint_row.pack(fill="x", pady=(0, 2))
        ttk.Label(
            hint_row, text="💡 Buổi: Sáng/Chiều/Cả hai.",
            style="Hint.TLabel"
        ).pack(side="left")

        row_mode = ttk.Frame(parent)
        self.frame_sched_mode_row = row_mode
        row_mode.pack(fill="x", pady=(0, 4))
        ttk.Label(row_mode, text="Mode chạy:").pack(side="left")
        ttk.Radiobutton(
            row_mode,
            text="Theo TKB cũ",
            value=SCHEDULE_MODE_MANUAL,
            variable=self.var_sched_mode,
            command=self._on_sched_mode_changed,
        ).pack(side="left", padx=(6, 2))
        ttk.Radiobutton(
            row_mode,
            text="Theo KHDH gợi ý",
            value=SCHEDULE_MODE_KHDH,
            variable=self.var_sched_mode,
            command=self._on_sched_mode_changed,
        ).pack(side="left", padx=2)

        # Buttons chính đặt cao trong panel để luôn nhìn thấy ở cả mode KHDH
        # lẫn mode TKB cũ; dữ liệu nhập có thể cuộn bên dưới.
        row_btn = ttk.Frame(parent)
        row_btn.pack(fill="x", pady=(2, 4))
        for col_idx in range(3):
            row_btn.columnconfigure(col_idx, weight=1, uniform="sched_actions")

        self.btn_sched_run = ttk.Button(
            row_btn, text="Quét và nhập",
            style="QuickGreen.TButton",
            command=self._on_schedule_run, state="disabled"
        )
        self.btn_sched_run.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.btn_sched_stop = ttk.Button(
            row_btn, text="Dừng an toàn",
            command=self._on_schedule_stop, state="disabled",
            style="Danger.TButton",
        )
        self.btn_sched_stop.grid(row=0, column=1, sticky="ew", padx=4)

        self.btn_sched_resume = ttk.Button(
            row_btn, text="Tiếp tục",
            command=self._on_schedule_resume, state="disabled",
            style="Subtle.TButton",
        )
        self.btn_sched_resume.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        ttk.Label(
            parent,
            text="Esc = dừng an toàn sau slot hiện tại | Tiếp tục = chạy từ checkpoint gần nhất",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 2))

        progress_info_frame = ttk.Frame(parent)
        progress_info_frame.pack(fill="x", pady=(2, 4))

        ttk.Label(
            progress_info_frame,
            text="Tiến độ PPCT môn đang dạy trong lớp đã chọn",
            font=("Segoe UI", 9, "bold"),
            foreground=UI_PRIMARY,
        ).pack(anchor="w")
        ttk.Label(
            progress_info_frame,
            textvariable=self.var_sched_teacher_progress_status,
            style="Hint.TLabel",
            wraplength=355,
            justify="left",
        ).pack(anchor="w", pady=(0, 2))

        self.btn_sched_teacher_progress = ttk.Button(
            progress_info_frame,
            textvariable=self.var_sched_teacher_progress_button,
            command=self._on_sched_teacher_progress_button_click,
            state="disabled",
        )
        self.btn_sched_teacher_progress.pack(fill="x")
        ttk.Checkbutton(
            progress_info_frame,
            text="Fast mode (quét nhanh tiến độ PPCT)",
            variable=self.var_sched_teacher_progress_fast_mode,
            command=self._on_sched_teacher_progress_mode_changed,
        ).pack(anchor="w", pady=(2, 0))

        # ===== CDP Nhập liệu Section =====
        sep_cdp = ttk.Separator(parent, orient="horizontal")
        sep_cdp.pack(fill="x", pady=(4, 4))

        ttk.Label(
            parent, text="Dữ liệu nhập",
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")

        self.frame_sched_manual_data = ttk.Frame(parent)
        self.frame_sched_manual_data.pack(fill="x")

        # Row: Môn học combobox
        row_mh = ttk.Frame(self.frame_sched_manual_data)
        row_mh.pack(fill="x", pady=2)
        row_mh.columnconfigure(1, weight=1)
        ttk.Label(row_mh, text="Môn học:").grid(row=0, column=0, sticky="w")
        self.cmb_sched_mon_hoc = ttk.Combobox(
            row_mh, textvariable=self.var_sched_mon_hoc,
            state="readonly", width=20
        )
        self.cmb_sched_mon_hoc.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.cmb_sched_mon_hoc.bind(
            "<<ComboboxSelected>>", self._on_sched_mon_hoc_selected
        )

        # Row: Phân môn combobox + nút Quét
        row_pm = ttk.Frame(self.frame_sched_manual_data)
        row_pm.pack(fill="x", pady=2)
        row_pm.columnconfigure(1, weight=1)
        ttk.Label(row_pm, text="Phân môn:").grid(row=0, column=0, sticky="w")
        self.cmb_sched_phan_mon = ttk.Combobox(
            row_pm, textvariable=self.var_sched_phan_mon,
            state="readonly", width=20
        )
        self.cmb_sched_phan_mon.grid(row=0, column=1, sticky="ew", padx=(8, 6))
        self.cmb_sched_phan_mon.bind(
            "<<ComboboxSelected>>", self._on_sched_phan_mon_selected
        )
        self.btn_sched_scan_form = ttk.Button(
            row_pm, text="Quét form",
            command=self._on_sched_scan_form
        )
        self.btn_sched_scan_form.grid(row=0, column=2, sticky="ew")

        # Row: Tiết PPCT nhập tay
        row_ppct = ttk.Frame(self.frame_sched_manual_data)
        row_ppct.pack(fill="x", pady=2)
        ttk.Label(row_ppct, text="PPCT bắt đầu:").pack(side="left")
        self.spn_sched_ppct_start = ttk.Spinbox(
            row_ppct, from_=1, to=200, width=5,
            textvariable=self.var_sched_ppct_start
        )
        self.spn_sched_ppct_start.pack(side="left", padx=4)

        self.lbl_sched_ppct_runtime = ttk.Label(
            self.frame_sched_manual_data,
            textvariable=self.var_sched_ppct_runtime,
            style="Hint.TLabel",
        )
        self.lbl_sched_ppct_runtime.pack(anchor="w", pady=(0, 2))

        self.frame_sched_common_data = ttk.Frame(parent)
        self.frame_sched_common_data.pack(fill="x", pady=(2, 0))
        self.frame_sched_common_data.columnconfigure(1, weight=0)
        self.frame_sched_common_data.columnconfigure(3, weight=0)
        ttk.Label(self.frame_sched_common_data, text="HS nghỉ:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            self.frame_sched_common_data, from_=0, to=50, width=4,
            textvariable=self.var_sched_hs_nghi
        ).grid(row=0, column=1, sticky="w", padx=(4, 14))
        ttk.Label(self.frame_sched_common_data, text="Điểm tiết học:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(
            self.frame_sched_common_data, from_=0, to=10, width=4,
            textvariable=self.var_sched_diem
        ).grid(row=0, column=3, sticky="w", padx=(4, 0))

        # Row: Nhận xét GV (pipe separated for random)
        row_nx = ttk.Frame(parent)
        row_nx.pack(fill="x", pady=2)
        row_nx.columnconfigure(1, weight=1)
        ttk.Label(row_nx, text="Nhận xét:").grid(row=0, column=0, sticky="w")
        self.ent_sched_nhan_xet = ttk.Entry(
            row_nx, textvariable=self.var_sched_nhan_xet, width=28
        )
        self.ent_sched_nhan_xet.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(
            parent, text="💡 Nhiều nhận xét ngẫu nhiên: cách bởi dấu |",
            style="Hint.TLabel"
        ).pack(anchor="w")

        # Runtime status của Schedule vẫn giữ nội bộ để worker/update logic không gãy,
        # nhưng không render trên GUI vì panel CDP + Log đã đủ thông tin.
        self._sched_runtime_status_frame = ttk.Frame(parent)

        self.lbl_sched_progress = ttk.Label(
            self._sched_runtime_status_frame, text="", font=("Segoe UI", 9)
        )
        self.lbl_sched_progress.pack(fill="x", pady=(2, 0))

        self.sched_progressbar = ttk.Progressbar(
            self._sched_runtime_status_frame,
            mode="determinate",
            length=300,
            style="LiveGreen.Horizontal.TProgressbar",
        )
        self.sched_progressbar.pack(fill="x", pady=(2, 0))
        self.lbl_sched_live_progress = ttk.Label(
            self._sched_runtime_status_frame,
            textvariable=self.var_sched_live_progress,
            style="LiveGreen.TLabel",
        )
        self.lbl_sched_live_progress.pack(fill="x", pady=(2, 0))

    def _on_sched_buoi_changed(self, thu):
        """Callback khi người dùng đổi Buổi cho 1 Thứ.

        Cập nhật checkboxes hiển thị theo buổi đã chọn.
        Nếu '---' → disable tất cả checkboxes của thứ đó.
        Nếu 'Sáng'/'Chiều' → hiển thị checkboxes cho buổi tương ứng.
        Nếu 'Cả hai' → hiển thị checkboxes cho buổi Sáng (Chiều dùng cùng config).

        Args:
            thu: int — thứ (2-7)
        """
        buoi_text = self._sched_buoi[thu].get()

        for tiet_idx in range(5):
            cb = self._sched_checkbuttons.get((thu, tiet_idx))
            if cb is None:
                continue

            if buoi_text == "---":
                # Disable — không dạy thứ này
                cb.config(
                    state="disabled",
                    image=self._sched_checkbox_images["off_disabled"],
                    selectimage=self._sched_checkbox_images["on_disabled"],
                )
            else:
                cb.config(
                    state="normal",
                    image=self._sched_checkbox_images["off"],
                    selectimage=self._sched_checkbox_images["on"],
                )

                # Rebind checkbutton variable theo buổi
                if buoi_text == "Sáng":
                    cb.config(variable=self._sched_grid[(thu, "S")][tiet_idx])
                elif buoi_text == "Chiều":
                    cb.config(variable=self._sched_grid[(thu, "C")][tiet_idx])
                else:
                    # "Cả hai" — dùng biến Sáng (Chiều sẽ copy khi chạy)
                    cb.config(variable=self._sched_grid[(thu, "S")][tiet_idx])

    def _get_sched_mode(self):
        """Đọc mode schedule hiện tại trên UI."""
        mode = str(self.var_sched_mode.get() or "").strip().lower()
        return mode if mode in {SCHEDULE_MODE_MANUAL, SCHEDULE_MODE_KHDH} else SCHEDULE_MODE_MANUAL

    def _is_sched_khdh_mode(self):
        """True khi schedule đang chạy theo các gợi ý KHDH live."""
        return self._get_sched_mode() == SCHEDULE_MODE_KHDH

    def _get_sched_mode_invalid_reason(self, mode=None):
        """Trả về lý do block chạy/resume cho mode schedule tương ứng."""
        resolved_mode = str(mode or self._get_sched_mode() or "").strip().lower()
        if resolved_mode == SCHEDULE_MODE_KHDH:
            lop_list = self._get_sched_lop_list(SCHEDULE_MODE_KHDH)
            if not lop_list:
                return "Mode KHDH yêu cầu chọn Lớp trước khi chạy."
            return ""
        return self._get_sched_form_invalid_reason()

    def _apply_sched_mode_state(self):
        """Đổi trạng thái widget theo mode schedule hiện tại."""
        khdh_mode = self._is_sched_khdh_mode()
        manual_state = "disabled" if khdh_mode else "readonly"
        button_state = "disabled" if khdh_mode else "normal"
        spin_state = "disabled" if khdh_mode else "normal"

        for widget in (
            getattr(self, "cmb_sched_mon_hoc", None),
            getattr(self, "cmb_sched_phan_mon", None),
        ):
            if widget is not None:
                try:
                    widget.config(state=manual_state)
                except Exception:
                    pass

        for widget in (
            getattr(self, "btn_sched_scan_form", None),
            getattr(self, "spn_sched_ppct_start", None),
        ):
            if widget is not None:
                try:
                    widget.config(state=button_state if widget == getattr(self, "btn_sched_scan_form", None) else spin_state)
                except Exception:
                    pass

        multi_lop_entry = getattr(self, "ent_sched_lop_multi", None)
        if multi_lop_entry is not None:
            try:
                multi_lop_entry.config(state="normal" if khdh_mode else "disabled")
            except Exception:
                pass

        manual_grid = getattr(self, "frame_sched_manual_grid", None)
        mode_row = getattr(self, "frame_sched_mode_row", None)
        if manual_grid is not None:
            try:
                if khdh_mode:
                    manual_grid.pack_forget()
                elif not manual_grid.winfo_manager():
                    pack_kwargs = {"fill": "x", "pady": (4, 2)}
                    if mode_row is not None and mode_row.winfo_manager():
                        pack_kwargs["before"] = mode_row
                    manual_grid.pack(**pack_kwargs)
            except Exception:
                pass

        manual_data = getattr(self, "frame_sched_manual_data", None)
        common_data = getattr(self, "frame_sched_common_data", None)
        if manual_data is not None:
            try:
                if khdh_mode:
                    manual_data.pack_forget()
                elif not manual_data.winfo_manager():
                    pack_kwargs = {"fill": "x"}
                    if common_data is not None and common_data.winfo_manager():
                        pack_kwargs["before"] = common_data
                    manual_data.pack(**pack_kwargs)
            except Exception:
                pass

    def _on_sched_mode_changed(self):
        """Khi đổi mode schedule thì cập nhật guidance và validation tương ứng."""
        self._apply_sched_mode_state()
        if self._is_sched_khdh_mode():
            self._log(
                "Mode schedule: Theo KHDH gợi ý. Grid Thứ/Buổi/Tiết và Môn/Phân môn/PPCT nhập tay sẽ bị bỏ qua.",
                "info",
            )
        else:
            self._log(
                "Mode schedule: Theo TKB cũ. App sẽ dùng grid slot + Môn/Phân môn/PPCT nhập tay.",
                "info",
            )
        if (
            self._schedule_resume_params
            and str(
                self._schedule_resume_params.get("schedule_mode", SCHEDULE_MODE_MANUAL)
                or SCHEDULE_MODE_MANUAL
            ).strip().lower() != self._get_sched_mode()
        ):
            self._log(
                "Checkpoint schedule hiện có thuộc mode khác với mode đang chọn. "
                "Nếu bấm Tiếp tục, app sẽ tự quay về mode của checkpoint để tránh chạy lệch.",
                "warning",
            )
        self._set_schedule_button_states(
            running=self._schedule_running,
            can_resume=bool(self._schedule_resume_state),
        )

    @staticmethod
    def _sort_lop_options(options):
        """Sort tên lớp tự nhiên: 6A4 trước 6A10, giữ text gốc."""
        def _key(value):
            text = str(value or "").strip()
            parts = re.split(r"(\d+)", text.casefold())
            key = []
            for part in parts:
                if part.isdigit():
                    key.append((0, int(part)))
                else:
                    key.append((1, part))
            return key

        cleaned = []
        seen = set()
        for item in list(options or []):
            text = str(item or "").strip()
            if not text or text.startswith("--"):
                continue
            norm = text.casefold()
            if norm in seen:
                continue
            seen.add(norm)
            cleaned.append(text)
        return sorted(cleaned, key=_key)

    def _get_sched_lop_list(self, mode=None):
        """Đọc danh sách lớp schedule; KHDH cho phép nhiều lớp trong ô riêng."""
        resolved_mode = str(mode or self._get_sched_mode() or "").strip().lower()
        raw_items = []
        if resolved_mode == SCHEDULE_MODE_KHDH:
            multi_text = str(self.var_sched_lop_multi.get() or "").strip()
            if multi_text:
                raw_items.extend(re.split(r"[,;\\n]+", multi_text))
        if not raw_items:
            raw_items.append(self.var_sched_lop.get())

        result = []
        seen = set()
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            norm = text.casefold()
            if norm in seen:
                continue
            seen.add(norm)
            result.append(text)
        return result

    @staticmethod
    def _normalize_person_name(text):
        """Chuẩn hóa tên người để so khớp ổn định không phụ thuộc dấu/case."""
        value = str(text or "").replace("\n", " ")
        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return value

    @staticmethod
    def _schedule_buoi_sort_key(buoi_text):
        """Cho khóa sắp xếp buổi học để so row trong cùng tuần."""
        normalized = SchedulePanelMixin._normalize_person_name(buoi_text)
        if "sang" in normalized:
            return 0
        if "chieu" in normalized:
            return 1
        return 2

    def _schedule_occurrence_sort_key(self, occurrence):
        """Khóa so sánh ổn định cho một occurrence trong lịch."""
        week = int(occurrence.get("week", 0) or 0)
        raw_thu = str(occurrence.get("thu", "")).strip().upper()
        if raw_thu == "CN":
            thu_num = 8
        else:
            match = re.search(r"\d+", raw_thu)
            thu_num = int(match.group()) if match else 0
        buoi_num = self._schedule_buoi_sort_key(occurrence.get("buoi", ""))
        tiet_match = re.search(r"\d+", str(occurrence.get("tiet", "")).strip())
        tiet_num = int(tiet_match.group()) if tiet_match else 0
        return (week, thu_num, buoi_num, tiet_num)

"""Tab Khôi phục."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...engine.backup.models import BACKUP_FILE_EXT, BackupSchemaError, load_backup_json
from ..theme import CLR_BODY, CLR_HINT, CLR_NAVY, CLR_OK, CLR_PANEL_BG
from ..workers.restore import RESTORE_MODE_MERGE, RESTORE_MODE_OVERWRITE, RESTORE_MODE_SKIP


class RestoreTabMixin:
    """Tab Khôi phục."""

    # -----------------------------------------------------------
    # Tab 2: Khôi phục
    # -----------------------------------------------------------

    def _build_restore_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        ttk.Label(
            parent,
            text=(
                "Chọn tệp sao lưu, đánh dấu tuần cần lấy lại, rồi bấm khôi phục. "
                "Công cụ sẽ lấy đúng PPCT, tên bài, ghi chú và trạng thái trong tệp."
            ),
            wraplength=920, justify="left",
            style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        # File picker
        pf = ttk.LabelFrame(
            parent, text=" Tệp sao lưu ",
            padding=(10, 8), style="Wiz.TLabelframe",
        )
        pf.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        pf.columnconfigure(0, weight=1)
        ttk.Entry(
            pf, textvariable=self.var_restore_path,
            font=("Consolas", 9),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(
            pf, text="📂 Chọn tệp…",
            command=self._on_pick_restore_path,
            style="WizSubtle.TButton",
        ).grid(row=0, column=1)
        ttk.Button(
            pf, text="🔄 Tải lại",
            command=self._on_reload_restore_file,
            style="WizSubtle.TButton",
        ).grid(row=0, column=2, padx=(6, 0))

        # Conflict mode
        cf = ttk.LabelFrame(
            parent, text=" Cách khôi phục ",
            padding=(8, 5), style="Wiz.TLabelframe",
        )
        cf.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        cf.columnconfigure(0, weight=1)
        cf.columnconfigure(1, weight=1)

        mode_box = ttk.Frame(cf, style="Wiz.TFrame")
        mode_box.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        safety_box = ttk.Frame(cf, style="Wiz.TFrame")
        safety_box.grid(row=0, column=1, sticky="nsew")

        ttk.Label(
            mode_box,
            text="Luôn xóa tuần đã chọn trước khi ghi lại:",
            style="WizAccent.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))

        mode_rows = [
            (
                RESTORE_MODE_OVERWRITE,
                "🔁 Ghi lại toàn bộ từ backup",
            ),
            (
                RESTORE_MODE_MERGE,
                "➕ Ghi lại nội dung có trong backup",
            ),
            (
                RESTORE_MODE_SKIP,
                "⏭ Chỉ ghi tuần đã chọn",
            ),
        ]
        for r, (value, title) in enumerate(mode_rows, start=1):
            ttk.Radiobutton(
                mode_box, text=title,
                variable=self.var_restore_conflict,
                value=value,
            ).grid(row=r, column=0, sticky="w")
        ttk.Label(
            mode_box,
            text="Mục tiêu: tránh trùng PPCT/dữ liệu cuốn chiếu trên VnEdu.",
            style="WizHint.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=len(mode_rows) + 1, column=0, sticky="w", padx=(24, 0), pady=(1, 0))

        ttk.Label(
            safety_box, text="Giữ an toàn trước khi ghi:",
            style="WizAccent.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        ttk.Checkbutton(
            safety_box,
            text="📸 Tự tạo bản lưu dự phòng trước khi khôi phục (nên bật)",
            variable=self.var_auto_snapshot,
        ).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            safety_box,
            text="⚡ Chạy nhanh nhưng vẫn kiểm tra đủ ô trước khi lưu",
            variable=self.var_restore_fast_safe,
        ).grid(row=2, column=0, sticky="w")
        ttk.Label(
            safety_box,
            text="Nếu VnEdu chưa đủ ô, công cụ tự chậm lại.",
            style="WizHint.TLabel", wraplength=420, justify="left",
        ).grid(row=3, column=0, sticky="w", padx=(24, 0), pady=(1, 0))

        # Danh sách tuần dạng checkbox. Không dùng Treeview ở đây vì khi
        # dialog thấp, Treeview dễ bị co chỉ còn hàng tiêu đề.
        tree_frm = ttk.LabelFrame(
            parent, text=" Chọn tuần cần khôi phục ",
            padding=8, style="Wiz.TLabelframe",
        )
        tree_frm.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        tree_frm.rowconfigure(1, weight=1)
        tree_frm.columnconfigure(0, weight=1)

        bar = ttk.Frame(tree_frm, style="Wiz.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(
            bar, text="✓ Chọn tất cả", command=lambda: self._set_all_weeks(True),
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="✗ Bỏ chọn", command=lambda: self._set_all_weeks(False),
            style="WizSubtle.TButton",
        ).pack(side="left")
        ttk.Button(
            bar, text="🎯 Chọn tuần có dữ liệu",
            command=self._auto_check_diff_weeks,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(12, 0))

        list_wrap = ttk.Frame(tree_frm, style="Wiz.TFrame")
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.rowconfigure(0, weight=1)
        list_wrap.columnconfigure(0, weight=1)

        self.restore_week_canvas = tk.Canvas(
            list_wrap, height=150, background=CLR_PANEL_BG,
            borderwidth=0, highlightthickness=0,
        )
        self.restore_week_canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(
            list_wrap, orient="vertical", command=self.restore_week_canvas.yview,
        )
        sb.grid(row=0, column=1, sticky="ns")
        self.restore_week_canvas.configure(yscrollcommand=sb.set)

        self.restore_week_inner = ttk.Frame(
            self.restore_week_canvas, style="Wiz.TFrame",
        )
        self.restore_week_window = self.restore_week_canvas.create_window(
            (0, 0), window=self.restore_week_inner, anchor="nw",
        )

        def _sync_scroll_region(_event=None):
            self.restore_week_canvas.configure(
                scrollregion=self.restore_week_canvas.bbox("all"),
            )

        def _sync_inner_width(event):
            self.restore_week_canvas.itemconfigure(
                self.restore_week_window, width=event.width,
            )

        def _wheel(event):
            delta = -1 if event.delta > 0 else 1
            self.restore_week_canvas.yview_scroll(delta, "units")
            return "break"

        self.restore_week_inner.bind("<Configure>", _sync_scroll_region)
        self.restore_week_canvas.bind("<Configure>", _sync_inner_width)
        self.restore_week_canvas.bind("<MouseWheel>", _wheel)
        self.restore_week_inner.bind("<MouseWheel>", _wheel)
        self._restore_week_wheel = _wheel

        ttk.Label(
            self.restore_week_inner,
            text="Chưa chọn tệp sao lưu. Sau khi tải tệp, các tuần sẽ hiện ở đây.",
            style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=6, pady=8)

        # Sticky footer: luôn nằm ngoài vùng danh sách tuần để không bị 35+ chip đẩy mất.
        footer = ttk.Frame(parent, style="Wiz.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        footer.columnconfigure(0, weight=1)

        action = ttk.Frame(footer, style="Wiz.TFrame")
        action.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.btn_restore_run = ttk.Button(
            action, text="📥 Khôi phục các tuần đã chọn",
            command=self._on_restore_clicked,
            state="disabled",
            style="WizSuccess.TButton",
        )
        self.btn_restore_run.pack(side="left")
        self.btn_restore_stop = ttk.Button(
            action, text="■ Dừng",
            command=self._on_restore_stop,
            state="disabled",
            style="WizDanger.TButton",
        )
        self.btn_restore_stop.pack(side="left", padx=(8, 0))
        # Status + progress
        self._restore_progress_bar = ttk.Progressbar(
            footer, mode="determinate", variable=self.var_restore_progress,
            style="WizBlue.Horizontal.TProgressbar",
        )
        self._restore_progress_bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(
            footer, textvariable=self.var_restore_status,
            style="Wiz.TLabel", wraplength=920, justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        # v3.3: Real-time blocker status — số XHR autofill đã chặn
        ttk.Label(
            footer, textvariable=self.var_blocker_status,
            style="WizHint.TLabel", wraplength=920, justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(2, 0))

    def _on_pick_restore_path(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self,
            title="Chọn tệp sao lưu KHDH",
            filetypes=[("Tệp sao lưu KHBD", f"*{BACKUP_FILE_EXT}"),
                       ("Tệp JSON", "*.json"), ("Tất cả tệp", "*.*")],
        )
        if path:
            self.var_restore_path.set(path)
            self._on_reload_restore_file()

    def _on_reload_restore_file(self):
        path = self.var_restore_path.get().strip()
        if not path:
            return
        try:
            bf = load_backup_json(path)
        except FileNotFoundError:
            messagebox.showerror(
                "Không tìm thấy tệp",
                f"Không tìm thấy tệp:\n{path}",
                parent=self,
            )
            self._loaded_backup = None
            self._populate_restore_tree()
            return
        except BackupSchemaError as e:
            messagebox.showerror(
                "Tệp sao lưu không hợp lệ",
                str(e),
                parent=self,
            )
            self._loaded_backup = None
            self._populate_restore_tree()
            return
        except Exception as e:
            messagebox.showerror(
                "Lỗi đọc tệp",
                f"{type(e).__name__}: {e}",
                parent=self,
            )
            self._loaded_backup = None
            self._populate_restore_tree()
            return

        # Cảnh báo nếu tệp sao lưu khác năm học hiện tại
        warn_lines = []
        if self.wizard.ctx_info is not None:
            cur_nh = int(getattr(self.wizard.ctx_info, "nam_hoc", 0) or 0)
            cur_gv = int(getattr(self.wizard.ctx_info, "giao_vien_id", 0) or 0)
            if cur_nh and bf.metadata.nam_hoc and cur_nh != bf.metadata.nam_hoc:
                warn_lines.append(
                    f"⚠ Năm học khác: tệp = {bf.metadata.nam_hoc}, "
                    f"hiện tại = {cur_nh}"
                )
            if cur_gv and bf.metadata.giao_vien_id and cur_gv != bf.metadata.giao_vien_id:
                warn_lines.append(
                    f"⚠ Giáo viên khác: tệp = {bf.metadata.giao_vien_id} "
                    f"({bf.metadata.giao_vien_name}), "
                    f"hiện tại = {cur_gv}"
                )
        if warn_lines:
            ans = messagebox.askyesno(
                "Cảnh báo metadata",
                "\n".join(warn_lines)
                + "\n\nVẫn tiếp tục mở tệp này?",
                parent=self,
            )
            if not ans:
                self._loaded_backup = None
                self._populate_restore_tree()
                return

        self._loaded_backup = bf
        self._populate_restore_tree()
        # Mặc định chọn tuần có dữ liệu trong tệp
        self._auto_check_diff_weeks()
        # Update status
        meta = bf.metadata
        self.var_restore_status.set(
            f"✓ Đã đọc tệp: {len(bf.weeks)} tuần, "
            f"{bf.total_filled_slots} ô có dữ liệu."
            + (f" Ghi chú: \"{meta.note}\"" if meta.note else "")
            + f" Tạo lúc: {meta.created_at}"
        )
        self.btn_restore_run.configure(state="normal")

    def _populate_restore_tree(self):
        inner = getattr(self, "restore_week_inner", None)
        if inner is not None:
            for child in inner.winfo_children():
                child.destroy()
        self._week_check_vars.clear()
        self._week_row_widgets.clear()
        self._week_row_meta.clear()
        if self._loaded_backup is None:
            if inner is not None:
                ttk.Label(
                    inner,
                    text="Chưa chọn tệp sao lưu. Sau khi tải tệp, các tuần sẽ hiện ở đây.",
                    style="WizHint.TLabel",
                ).grid(row=0, column=0, sticky="w", padx=6, pady=8)
            return

        if inner is None:
            return

        chip_cols = 8
        for c in range(chip_cols):
            inner.columnconfigure(c, weight=1, uniform="restore_week_chip")

        for idx, wk in enumerate(self._loaded_backup.weeks):
            tuan = int(wk.tuan)
            var = tk.BooleanVar(value=False)
            self._week_check_vars[tuan] = var
            filled = wk.filled_count
            total = len(wk.slots)
            if total == 0:
                status = "(Tuần trống)"
                note = "Tệp sao lưu không có tiết nào ở tuần này."
                is_missing = True
            elif not wk.fetched_at:
                status = "(Đọc lỗi)"
                note = "Lúc sao lưu, tuần này đọc từ VnEdu bị lỗi nên không có dữ liệu."
                is_missing = True
            else:
                status = f"{filled} ô đã nhập / {total} ô có lớp"
                note = wk.fetched_at
                is_missing = False

            row_idx = idx // chip_cols
            col_idx = idx % chip_cols
            text = (
                f"Tuần {tuan} - {filled}/{total} ô"
                if not is_missing
                else f"Tuần {tuan} - trống"
            )
            cb = tk.Checkbutton(
                inner,
                text=text,
                variable=var,
                command=lambda t=tuan: self._refresh_tree_restore_row(t),
                indicatoron=False,
                anchor="center",
                justify="center",
                cursor="hand2",
                takefocus=True,
                font=("Segoe UI", 9, "bold"),
                width=16,
                padx=6,
                pady=3,
                bd=1,
                relief="solid",
                background="#ffffff",
                foreground=CLR_BODY,
                activebackground="#eaf3ff",
                activeforeground=CLR_NAVY,
                selectcolor="#dff3e4",
                disabledforeground=CLR_HINT,
            )
            cb.grid(row=row_idx, column=col_idx, sticky="ew", padx=5, pady=4)
            wheel_handler = getattr(self, "_restore_week_wheel", None)
            if wheel_handler is not None:
                cb.bind("<MouseWheel>", wheel_handler)
            if is_missing:
                cb.configure(
                    state="disabled",
                    background="#f0f2f5",
                    foreground=CLR_HINT,
                    cursor="arrow",
                    relief="groove",
                )

            self._week_row_meta[tuan] = {
                "status": status,
                "note": note,
                "is_missing": is_missing,
                "text": text,
            }
            self._week_row_widgets[tuan] = {
                "check": cb,
            }

        try:
            self.restore_week_canvas.configure(
                scrollregion=self.restore_week_canvas.bbox("all"),
            )
            self.restore_week_canvas.yview_moveto(0)
        except Exception:
            pass

    def _toggle_restore_week(self, tuan: int):
        var = self._week_check_vars.get(tuan)
        meta = self._week_row_meta.get(tuan) or {}
        if var is None or meta.get("is_missing"):
            return
        var.set(not var.get())
        self._refresh_tree_restore_row(tuan)

    def _on_tree_restore_click(self, event):
        # Giữ lại để tương thích nếu bản cũ còn bind sự kiện Treeview.
        return

    def _refresh_tree_restore_row(self, tuan: int):
        var = self._week_check_vars.get(tuan)
        if var is None:
            return
        widgets = self._week_row_widgets.get(tuan) or {}
        meta = self._week_row_meta.get(tuan) or {}
        cb = widgets.get("check")
        if cb is None:
            return
        is_missing = bool(meta.get("is_missing"))
        base_text = str(meta.get("text") or f"Tuần {tuan}")
        if is_missing:
            cb.configure(
                text=base_text,
                state="disabled",
                background="#f0f2f5",
                foreground=CLR_HINT,
                cursor="arrow",
                relief="groove",
            )
            return

        if var.get():
            cb.configure(
                text=f"✓ Tuần {tuan} - đã chọn",
                background="#dff3e4",
                foreground=CLR_OK,
                activebackground="#d4edda",
                relief="sunken",
            )
        else:
            cb.configure(
                text=base_text,
                background="#ffffff",
                foreground=CLR_BODY,
                activebackground="#eaf3ff",
                relief="solid",
            )

    def _set_all_weeks(self, checked: bool):
        for tuan, var in self._week_check_vars.items():
            wk = self._loaded_backup.week_by_num(tuan) if self._loaded_backup else None
            is_missing = wk is None or not wk.fetched_at or len(wk.slots) == 0
            if checked and is_missing:
                var.set(False)
            else:
                var.set(checked)
            self._refresh_tree_restore_row(tuan)

    def _auto_check_diff_weeks(self):
        """Mặc định chọn tuần CÓ dữ liệu trong tệp."""
        if self._loaded_backup is None:
            return
        for tuan, var in self._week_check_vars.items():
            wk = self._loaded_backup.week_by_num(tuan)
            should_check = (
                wk is not None
                and wk.fetched_at
                and len(wk.slots) > 0
            )
            var.set(should_check)
            self._refresh_tree_restore_row(tuan)

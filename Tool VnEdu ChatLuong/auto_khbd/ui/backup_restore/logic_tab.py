"""Tab rà soát PPCT (kiểm tra / đề xuất sửa / ngày nghỉ / so sánh)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ...engine.backup.logic_engine import (
    KHBD_ISSUE_ACTIVE_DUP,
    KHBD_ISSUE_NGHI_NO_MAKEUP,
    KHBD_ISSUE_OVER_CAP,
    KHBD_ISSUE_PPCT_GAP,
    KHBD_ISSUE_PPCT_ORDER,
    KHBD_ISSUE_TITLE_CONFLICT,
    KHBDLogicEngine,
    KHBDRepairAction,
    load_khbd_review_file,
)
from ...engine.backup.models import BACKUP_FILE_EXT, BackupFile, save_backup_json
from ..dialogs.khbd_holiday import KHBDHolidayDialog
from ..theme import CLR_BODY, CLR_ERR, CLR_OK, CLR_WARN


class LogicTabMixin:
    """Tab rà soát PPCT (kiểm tra / đề xuất sửa / ngày nghỉ / so sánh)."""

    # -----------------------------------------------------------
    # Tab rà soát PPCT — kiểm tra / sửa đề xuất / ngày nghỉ / so sánh
    # -----------------------------------------------------------

    def _build_khbd_logic_tab(self, parent: ttk.Frame):
        self.var_logic_path = tk.StringVar(value="")
        self.var_logic_status = tk.StringVar(
            value="Chọn tệp sao lưu hoặc Excel rà soát để kiểm tra PPCT.",
        )
        self._logic_backup: BackupFile | None = None
        self._logic_actions: list[KHBDRepairAction] = []
        self._logic_action_check_vars: dict[str, tk.BooleanVar] = {}
        self._logic_pending_backup: BackupFile | None = None

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        ttk.Label(
            parent,
            text=(
                "Kiểm tra trước khi nhập lên VnEdu: phát hiện PPCT trùng, thiếu, "
                "đi lùi hoặc tiết Nghỉ chưa có tiết dạy lại. Mọi thay đổi chỉ lưu "
                "ra tệp mới để bạn xem lại trước."
            ),
            wraplength=980, justify="left", style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        src = ttk.LabelFrame(
            parent, text=" Nguồn dữ liệu ", padding=10,
            style="Wiz.TLabelframe",
        )
        src.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        src.columnconfigure(1, weight=1)
        ttk.Label(src, text="Tệp:", style="Wiz.TLabel").grid(
            row=0, column=0, sticky="w",
        )
        ttk.Entry(src, textvariable=self.var_logic_path).grid(
            row=0, column=1, sticky="ew", padx=(6, 6),
        )
        ttk.Button(
            src, text="Chọn tệp...", command=self._on_logic_pick_file,
            style="WizSubtle.TButton",
        ).grid(row=0, column=2)

        bar = ttk.Frame(parent, style="Wiz.TFrame")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        for text, cmd, style in [
            ("Rà soát", self._on_logic_audit_clicked, "WizPrimary.TButton"),
            ("Gợi ý sửa PPCT", self._on_logic_repair_preview_clicked, "WizSubtle.TButton"),
            ("Ngày nghỉ / dạy bù", self._on_logic_holiday_clicked, "WizSubtle.TButton"),
            ("So sánh 2 tệp", self._on_logic_diff_clicked, "WizSubtle.TButton"),
        ]:
            ttk.Button(bar, text=text, command=cmd, style=style).pack(
                side="left", padx=(0, 6),
            )
        self.btn_logic_apply = ttk.Button(
            bar, text="Lưu tệp mới",
            command=self._on_logic_apply_clicked,
            state="disabled", style="WizPrimary.TButton",
        )
        self.btn_logic_apply.pack(side="left")

        tree_box = ttk.LabelFrame(
            parent, text=" Kết quả / Xem trước ", padding=8,
            style="Wiz.TLabelframe",
        )
        tree_box.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)
        cols = ("apply", "kind", "tuan", "row_key", "group", "before", "after", "note")
        self.tree_logic = ttk.Treeview(
            tree_box, columns=cols, show="headings", style="Wiz.Treeview",
        )
        for col, label, w, anchor, stretch in [
            ("apply", "Áp dụng", 62, "center", False),
            ("kind", "Vấn đề", 140, "w", False),
            ("tuan", "Tuần", 52, "center", False),
            ("row_key", "Ô", 70, "center", False),
            ("group", "Lớp / Môn / Phân môn", 260, "w", False),
            ("before", "Trước", 160, "w", False),
            ("after", "Sau", 160, "w", False),
            ("note", "Ghi chú", 280, "w", True),
        ]:
            self.tree_logic.heading(col, text=label)
            self.tree_logic.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_logic.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(
            tree_box, orient="vertical", command=self.tree_logic.yview,
        )
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_logic.configure(yscrollcommand=sb.set)
        self.tree_logic.bind("<Button-1>", self._on_logic_tree_click)
        self.tree_logic.tag_configure(
            "error", foreground=CLR_ERR, font=("Segoe UI", 9, "bold"),
        )
        self.tree_logic.tag_configure("warning", foreground=CLR_WARN)
        self.tree_logic.tag_configure(
            "checked", foreground=CLR_OK, font=("Segoe UI", 9, "bold"),
        )
        self.tree_logic.tag_configure("info", foreground=CLR_BODY)

        ttk.Label(
            parent, textvariable=self.var_logic_status,
            style="Wiz.TLabel", wraplength=980, justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(2, 0))

    def _logic_clear_tree(self):
        for iid in self.tree_logic.get_children():
            self.tree_logic.delete(iid)
        self._logic_action_check_vars.clear()
        self._logic_actions = []
        self._logic_pending_backup = None
        self.btn_logic_apply.configure(state="disabled", text="Lưu tệp mới")

    def _logic_load_current(self) -> BackupFile | None:
        path = self.var_logic_path.get().strip()
        if not path:
            messagebox.showinfo(
                "Chưa chọn tệp",
                "Hãy chọn tệp sao lưu hoặc Excel rà soát trước.",
                parent=self,
            )
            return None
        try:
            bf = load_khbd_review_file(path)
        except Exception as e:
            messagebox.showerror(
                "Không đọc được tệp",
                f"{type(e).__name__}: {e}",
                parent=self,
            )
            return None
        self._logic_backup = bf
        return bf

    @staticmethod
    def _logic_default_save_path(src_path: str, suffix: str) -> str:
        p = Path(src_path)
        name = p.name
        if name.endswith(BACKUP_FILE_EXT):
            stem = name[:-len(BACKUP_FILE_EXT)]
        else:
            stem = p.stem
        return str(p.with_name(f"{stem}{suffix}{BACKUP_FILE_EXT}"))

    def _on_logic_pick_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Chọn tệp sao lưu hoặc Excel rà soát",
            filetypes=[
                ("Tệp KHBD hoặc Excel", f"*{BACKUP_FILE_EXT} *.xlsx"),
                ("Tệp sao lưu KHBD", f"*{BACKUP_FILE_EXT}"),
                ("Excel", "*.xlsx"),
                ("Tất cả tệp", "*.*"),
            ],
            parent=self,
        )
        if not path:
            return
        self.var_logic_path.set(path)
        bf = self._logic_load_current()
        if bf is None:
            return
        self._logic_clear_tree()
        self.var_logic_status.set(
            f"Đã đọc {Path(path).name}: {len(bf.weeks)} tuần, "
            f"{bf.total_slots} ô/tiết. Bấm [Rà soát] để kiểm tra.",
        )

    def _on_logic_audit_clicked(self):
        bf = self._logic_load_current()
        if bf is None:
            return
        self._logic_clear_tree()
        result = KHBDLogicEngine.analyze_backup(bf)
        for idx, issue in enumerate(result.issues):
            before = f"PPCT {issue.ppct}" if issue.ppct else ""
            self.tree_logic.insert(
                "", "end", iid=f"issue_{idx}",
                values=(
                    "", self._logic_kind_label(issue.kind), issue.tuan, issue.row_key,
                    issue.label, before, "", issue.message,
                ),
                tags=(issue.severity,),
            )
        if result.issues:
            self.var_logic_status.set(
                f"Rà soát xong: {result.groups_checked} nhóm, "
                f"{result.error_count} lỗi chặn, "
                f"{result.warning_count} cảnh báo.",
            )
        else:
            self.var_logic_status.set(
                f"Rà soát xong: {result.groups_checked} nhóm, "
                "không phát hiện lỗi PPCT.",
            )

    def _on_logic_repair_preview_clicked(self):
        bf = self._logic_load_current()
        if bf is None:
            return
        self._logic_clear_tree()
        actions = KHBDLogicEngine.build_sequential_repairs(bf)
        self._logic_actions = actions
        for idx, action in enumerate(actions):
            iid = f"logic_action_{idx}"
            var = tk.BooleanVar(value=action.needs_change)
            self._logic_action_check_vars[iid] = var
            self.tree_logic.insert(
                "", "end", iid=iid,
                values=(
                    "☑" if var.get() else "☐",
                    self._logic_kind_label("repair_ppct"), action.tuan, action.row_key,
                    action.label,
                    f"{action.current_ppct} | {self._short_text(action.current_ten_bai, 40)}",
                    f"{action.proposed_ppct} | {self._short_text(action.proposed_ten_bai, 40)}",
                    action.note,
                ),
                tags=("checked" if var.get() else "info",),
            )
        if actions:
            self.btn_logic_apply.configure(state="normal", text="Lưu tệp mới")
            self.var_logic_status.set(
                f"Đã tạo {len(actions)} đề xuất sửa. Đánh dấu dòng cần áp dụng.",
            )
        else:
            self.var_logic_status.set("Không có đề xuất sửa PPCT. Dữ liệu đang đúng thứ tự.")

    def _on_logic_holiday_clicked(self):
        bf = self._logic_load_current()
        if bf is None:
            return
        dlg = KHBDHolidayDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        candidate = KHBDLogicEngine.clone_backup(bf)
        try:
            actions = KHBDLogicEngine.apply_holiday(candidate, dlg.result)
        except Exception as e:
            messagebox.showerror("Không tạo được ngày nghỉ", str(e), parent=self)
            return
        self._logic_clear_tree()
        self._logic_pending_backup = candidate
        diffs = KHBDLogicEngine.diff_backups(bf, candidate)
        for idx, diff in enumerate(diffs):
            self.tree_logic.insert(
                "", "end", iid=f"holiday_diff_{idx}",
                values=(
                    "", self._logic_kind_label("holiday_diff"), diff.tuan, diff.row_key,
                    diff.label, diff.left_value, diff.right_value, diff.field,
                ),
                tags=(diff.severity,),
            )
        self.btn_logic_apply.configure(state="normal", text="Lưu tệp mới")
        self.var_logic_status.set(
            f"Đã dựng ngày nghỉ/dạy bù: {len(actions)} hành động, "
            f"{len(diffs)} khác biệt. Xem lại rồi bấm [Lưu tệp mới].",
        )

    def _on_logic_diff_clicked(self):
        from tkinter import filedialog
        left_path = filedialog.askopenfilename(
            title="Chọn tệp gốc",
            filetypes=[
                ("Tệp KHBD hoặc Excel", f"*{BACKUP_FILE_EXT} *.xlsx"),
                ("Tất cả tệp", "*.*"),
            ],
            parent=self,
        )
        if not left_path:
            return
        right_path = filedialog.askopenfilename(
            title="Chọn tệp cần so sánh",
            filetypes=[
                ("Tệp KHBD hoặc Excel", f"*{BACKUP_FILE_EXT} *.xlsx"),
                ("Tất cả tệp", "*.*"),
            ],
            parent=self,
        )
        if not right_path:
            return
        try:
            left = load_khbd_review_file(left_path)
            right = load_khbd_review_file(right_path)
        except Exception as e:
            messagebox.showerror("Không đọc được tệp", str(e), parent=self)
            return
        self._logic_clear_tree()
        diffs = KHBDLogicEngine.diff_backups(left, right)
        for idx, diff in enumerate(diffs):
            self.tree_logic.insert(
                "", "end", iid=f"diff_{idx}",
                values=(
                    "", self._logic_kind_label("diff"), diff.tuan, diff.row_key,
                    diff.label, diff.left_value, diff.right_value, diff.field,
                ),
                tags=(diff.severity,),
            )
        self.var_logic_status.set(
            f"So sánh xong: {len(diffs)} khác biệt giữa "
            f"{Path(left_path).name} và {Path(right_path).name}.",
        )

    def _on_logic_tree_click(self, event):
        region = self.tree_logic.identify_region(event.x, event.y)
        if region != "cell" or self.tree_logic.identify_column(event.x) != "#1":
            return
        iid = self.tree_logic.identify_row(event.y)
        if not iid or iid not in self._logic_action_check_vars:
            return
        var = self._logic_action_check_vars[iid]
        var.set(not var.get())
        idx = int(iid.rsplit("_", 1)[1])
        if idx < len(self._logic_actions):
            self._logic_actions[idx].skip = not var.get()
        cur = list(self.tree_logic.item(iid, "values"))
        cur[0] = "☑" if var.get() else "☐"
        self.tree_logic.item(
            iid, values=cur,
            tags=("checked" if var.get() else "info",),
        )

    def _on_logic_apply_clicked(self):
        from tkinter import filedialog
        src_path = self.var_logic_path.get().strip()
        if self._logic_pending_backup is not None:
            out_default = self._logic_default_save_path(src_path, "-holiday-repaired")
            target = filedialog.asksaveasfilename(
                title="Lưu tệp đề xuất",
                initialfile=Path(out_default).name,
                initialdir=str(Path(out_default).parent),
                defaultextension=BACKUP_FILE_EXT,
                filetypes=[("Tệp sao lưu KHBD", f"*{BACKUP_FILE_EXT}")],
                parent=self,
            )
            if not target:
                return
            save_backup_json(self._logic_pending_backup, target)
            result = KHBDLogicEngine.analyze_backup(self._logic_pending_backup)
            self.var_logic_path.set(target)
            self._logic_backup = self._logic_pending_backup
            self._logic_pending_backup = None
            self.var_logic_status.set(
                f"Đã lưu {Path(target).name}. Rà soát sau lưu: "
                f"{result.error_count} lỗi, {result.warning_count} cảnh báo.",
            )
            return

        bf = self._logic_load_current()
        if bf is None:
            return
        selected = [
            a for a in self._logic_actions
            if a.needs_change and not a.skip
        ]
        if not selected:
            messagebox.showinfo(
                "Không có dòng áp dụng",
                "Hãy bấm [Gợi ý sửa PPCT] và đánh dấu ít nhất 1 dòng cần sửa.",
                parent=self,
            )
            return
        candidate = KHBDLogicEngine.clone_backup(bf)
        changed = KHBDLogicEngine.apply_actions(candidate, selected)
        out_default = self._logic_default_save_path(src_path, "-logic-repaired")
        target = filedialog.asksaveasfilename(
            title="Lưu tệp đã sửa",
            initialfile=Path(out_default).name,
            initialdir=str(Path(out_default).parent),
            defaultextension=BACKUP_FILE_EXT,
            filetypes=[("Tệp sao lưu KHBD", f"*{BACKUP_FILE_EXT}")],
            parent=self,
        )
        if not target:
            return
        save_backup_json(candidate, target)
        result = KHBDLogicEngine.analyze_backup(candidate)
        self.var_logic_path.set(target)
        self._logic_backup = candidate
        self.var_logic_status.set(
            f"Đã lưu {Path(target).name}: {changed} dòng đổi. "
            f"Rà soát sau lưu: {result.error_count} lỗi, "
            f"{result.warning_count} cảnh báo.",
        )

    @staticmethod
    def _logic_kind_label(kind: str) -> str:
        labels = {
            KHBD_ISSUE_ACTIVE_DUP: "Trùng PPCT",
            KHBD_ISSUE_PPCT_GAP: "Thiếu PPCT",
            KHBD_ISSUE_PPCT_ORDER: "Sai thứ tự",
            KHBD_ISSUE_OVER_CAP: "Vượt số tiết",
            KHBD_ISSUE_NGHI_NO_MAKEUP: "Nghỉ chưa dạy lại",
            KHBD_ISSUE_TITLE_CONFLICT: "Tên bài lệch",
            "repair_ppct": "Đề xuất sửa",
            "holiday_diff": "Nghỉ / dạy bù",
            "diff": "Khác biệt",
        }
        return labels.get(str(kind or ""), str(kind or ""))

    @staticmethod
    def _short_text(text: str, limit: int = 60) -> str:
        text = str(text or "").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)] + "…"

"""Hộp thoại báo cáo sức khoẻ KHDH."""

from __future__ import annotations

import csv
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ...engine.analyzer.reports import (
    HEALTH_KIND_GROUP_NO_DATA,
    HEALTH_KIND_PPCT_DUPLICATE,
    HEALTH_KIND_PPCT_GAP,
    HEALTH_KIND_SLOT_EXTRA,
    HEALTH_KIND_SLOT_MISSING,
    HealthIssue,
    HealthScanResult,
)
from ...engine.profile.excel_io import (
    _autosize_columns,
    _check_openpyxl,
    _write_cell,
    _write_header_row,
)
from ..theme import CLR_ERR, CLR_OK, CLR_PANEL_BG, CLR_WARN


class HealthReportDialog(tk.Toplevel):
    """Dialog hiển thị kết quả quét tình trạng KHDH trong scope user."""

    def __init__(self, parent, result: HealthScanResult):
        super().__init__(parent)
        self.title("Quét tình trạng KHDH")
        self.transient(parent)
        self.grab_set()
        self.resizable(True, True)
        self.geometry("1180x620")
        self.configure(bg=CLR_PANEL_BG)
        self.result = result
        self.var_show_ok = tk.BooleanVar(value=False)
        self._build_ui()
        self._populate()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_ui(self):
        outer = ttk.Frame(self, style="Wiz.TFrame", padding=10)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)

        hdr = ttk.Frame(outer, style="Wiz.TFrame")
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        hdr.columnconfigure(0, weight=1)
        ttk.Label(
            hdr,
            text=(
                f"📊 Scope: {len(self.result.allowed_groups)} nhóm  ·  "
                f"Web thấy: {len(self.result.actual_groups_seen)} nhóm / {self.result.total_slots_scanned} slot  ·  "
                f"Issues: {self.result.total_issue_count}  ·  "
                f"Thiếu PPCT: {self.result.total_missing_ppcts}"
            ),
            style="WizSection.TLabel",
        ).grid(row=0, column=0, sticky="w")

        ctl = ttk.Frame(outer, style="Wiz.TFrame")
        ctl.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(
            ctl,
            text="Hiện cả nhóm OK",
            variable=self.var_show_ok,
            command=self._populate,
        ).pack(side="left")
        ttk.Button(
            ctl, text="💾 Xuất CSV", style="WizSubtle.TButton",
            command=lambda: self._export("csv"),
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            ctl, text="💾 Xuất Excel", style="WizSubtle.TButton",
            command=lambda: self._export("xlsx"),
        ).pack(side="right")

        wrap = ttk.Frame(outer, style="Wiz.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        cols = ("stt", "lop", "mon", "pm", "kind", "week", "ppct", "detail")
        self.tree_health = ttk.Treeview(
            wrap, columns=cols, show="headings", style="Wiz.Treeview"
        )
        specs = [
            ("stt", "STT", 50, "center", False),
            ("lop", "Lớp", 70, "center", False),
            ("mon", "Môn", 160, "w", True),
            ("pm", "Phân môn", 210, "w", True),
            ("kind", "Loại", 110, "center", False),
            ("week", "Tuần", 90, "center", False),
            ("ppct", "PPCT", 110, "center", False),
            ("detail", "Chi tiết", 320, "w", True),
        ]
        for col, label, w, anchor, stretch in specs:
            self.tree_health.heading(col, text=label)
            self.tree_health.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_health.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree_health.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        self.tree_health.configure(yscrollcommand=ysb.set)

        self.tree_health.tag_configure("ok_row", foreground=CLR_OK)
        self.tree_health.tag_configure("warn_row", foreground=CLR_WARN, font=("Segoe UI", 9, "bold"))
        self.tree_health.tag_configure("error_row", foreground=CLR_ERR, font=("Segoe UI", 9, "bold"))

        ftr = ttk.Frame(outer, style="Wiz.TFrame")
        ftr.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(ftr, text="Đóng", style="WizPrimary.TButton", command=self.destroy).pack(side="right")

    def _row_kind_text(self, issue: HealthIssue) -> str:
        return {
            HEALTH_KIND_PPCT_GAP: "PPCT nhảy",
            HEALTH_KIND_PPCT_DUPLICATE: "PPCT trùng",
            HEALTH_KIND_SLOT_MISSING: "Thiếu slot",
            HEALTH_KIND_SLOT_EXTRA: "Thừa slot",
            HEALTH_KIND_GROUP_NO_DATA: "Không có data",
        }.get(issue.kind, issue.kind)

    def _row_ppct_text(self, issue: HealthIssue) -> str:
        if issue.kind == HEALTH_KIND_PPCT_GAP:
            return f"{issue.ppct_from}→{issue.ppct_to}"
        if issue.kind == HEALTH_KIND_PPCT_DUPLICATE:
            return str(issue.duplicate_ppct)
        return "—"

    def _row_detail_text(self, issue: HealthIssue) -> str:
        if issue.kind == HEALTH_KIND_PPCT_GAP:
            return f"Thiếu: {', '.join(str(x) for x in issue.missing_ppcts)}"
        if issue.kind == HEALTH_KIND_PPCT_DUPLICATE:
            return issue.message
        return issue.message

    def _populate(self):
        for iid in self.tree_health.get_children():
            self.tree_health.delete(iid)
        items = self.result.issues
        if not self.var_show_ok.get():
            items = [x for x in items if x.severity != "ok"]
        idx = 0
        for issue in items:
            idx += 1
            tag = "ok_row" if issue.severity == "ok" else ("warn_row" if issue.severity == "warn" else "error_row")
            self.tree_health.insert(
                "", "end",
                values=(
                    idx,
                    issue.lop_text,
                    issue.mon_text,
                    issue.phan_mon_text,
                    self._row_kind_text(issue),
                    issue.week_from if issue.week_from == issue.week_to else f"{issue.week_from}→{issue.week_to}",
                    self._row_ppct_text(issue),
                    self._row_detail_text(issue),
                ),
                tags=(tag,),
            )

    def _export(self, fmt: str):
        try:
            from tkinter import filedialog
            if fmt == "csv":
                path = filedialog.asksaveasfilename(
                    parent=self,
                    defaultextension=".csv",
                    filetypes=[("CSV", "*.csv")],
                    title="Xuất báo cáo CSV",
                )
                if not path:
                    return
                with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["STT", "Lớp", "Môn", "Phân môn", "Loại", "Tuần", "PPCT", "Chi tiết"])
                    for i, issue in enumerate(self.result.issues, 1):
                        w.writerow([
                            i, issue.lop_text, issue.mon_text, issue.phan_mon_text,
                            self._row_kind_text(issue),
                            issue.week_from if issue.week_from == issue.week_to else f"{issue.week_from}→{issue.week_to}",
                            self._row_ppct_text(issue),
                            self._row_detail_text(issue),
                        ])
            else:
                _check_openpyxl()
                from openpyxl import Workbook
                path = filedialog.asksaveasfilename(
                    parent=self,
                    defaultextension=".xlsx",
                    filetypes=[("Excel", "*.xlsx")],
                    title="Xuất báo cáo Excel",
                )
                if not path:
                    return
                wb = Workbook()
                ws = wb.active
                ws.title = "HealthReport"
                headers = ["STT", "Lớp", "Môn", "Phân môn", "Loại", "Tuần", "PPCT", "Chi tiết"]
                _write_header_row(ws, 1, headers)
                row = 2
                for i, issue in enumerate(self.result.issues, 1):
                    vals = [
                        i, issue.lop_text, issue.mon_text, issue.phan_mon_text,
                        self._row_kind_text(issue),
                        issue.week_from if issue.week_from == issue.week_to else f"{issue.week_from}→{issue.week_to}",
                        self._row_ppct_text(issue),
                        self._row_detail_text(issue),
                    ]
                    for col, val in enumerate(vals, 1):
                        _write_cell(ws, row, col, val)
                    row += 1
                _autosize_columns(ws, headers)
                wb.save(path)
            messagebox.showinfo("Xuất thành công", f"Đã xuất báo cáo ra file:\n{path}", parent=self)
        except Exception as e:
            messagebox.showerror("Lỗi xuất báo cáo", f"{type(e).__name__}: {e}", parent=self)

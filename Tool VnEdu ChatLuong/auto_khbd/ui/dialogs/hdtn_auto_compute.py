"""Hộp thoại tự tính PPCT HĐTN."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..theme import CLR_HINT, CLR_OK, CLR_PANEL_BG


# =====================================================================
# Dialog — ConfirmRunDialog (xác nhận trước khi chạy)
# =====================================================================

class HDTNAutoComputeDialog(tk.Toplevel):
    """Dialog preview kết quả Tự tính PPCT HĐTN Chủ đề.

    Treeview 5 cột:
      [✓] | Lớp | Phân môn | PPCT hiện tại | PPCT đề xuất

    User check group nào muốn apply, bấm OK → return list group_key đã tick.
    Cancel hoặc đóng → return None.
    """

    def __init__(self, parent, results: list[dict], tuan_from: int):
        super().__init__(parent)
        self.title("Tự tính PPCT HĐTN Chủ đề")
        self.transient(parent)
        self.grab_set()
        self.resizable(True, True)
        self.geometry("720x460")
        self.configure(bg=CLR_PANEL_BG)

        self._results = list(results)  # copy
        self._tuan_from = tuan_from
        self._checked: dict[str, tk.BooleanVar] = {}
        self.selected_groups: list[str] | None = None  # output

        self._build_ui()
        self._populate()
        self.bind("<Escape>", lambda e: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        # Center on parent
        self.update_idletasks()
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - 720) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - 460) // 2
            self.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass

    def _build_ui(self):
        # Header
        hdr = ttk.Frame(self, style="Wiz.TFrame", padding=(12, 10, 12, 6))
        hdr.pack(fill="x")
        ttk.Label(
            hdr,
            text=f"Tool tính PPCT bắt đầu cho group HĐTN Chủ đề từ tuần {self._tuan_from}",
            style="WizSection.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            hdr,
            text=("Giả định: GV trước đã dạy đầy đủ từ tuần 1 đến tuần "
                  f"{self._tuan_from - 1}. Nếu sai, bạn có thể bỏ chọn nhóm "
                  "nào không phù hợp rồi chỉnh tay sau."),
            style="WizHint.TLabel",
            wraplength=680, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        # Body — Treeview với checkbox column
        body = ttk.Frame(self, style="Wiz.TFrame", padding=(12, 4, 12, 4))
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        cols = ("apply", "lop", "phan_mon", "current", "suggested", "detail")
        self.tree = ttk.Treeview(
            body, columns=cols, show="headings",
            style="Wiz.Treeview", height=10,
        )
        col_specs = [
            ("apply",     "Áp dụng",        70,  "center", False),
            ("lop",       "Lớp",            70,  "center", False),
            ("phan_mon",  "Phân môn",       180, "w",      True),
            ("current",   "PPCT hiện tại", 110, "center", False),
            ("suggested", "PPCT đề xuất",  110, "center", False),
            ("detail",    "Tính toán",      150, "w",      True),
        ]
        for col, label, w, anchor, stretch in col_specs:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        # Click trên cột "Áp dụng" để toggle
        self.tree.bind("<Button-1>", self._on_tree_click)

        # Tag cho row được apply
        self.tree.tag_configure(
            "apply_yes", foreground=CLR_OK,
            font=("Segoe UI", 9, "bold"),
        )
        self.tree.tag_configure(
            "apply_no", foreground=CLR_HINT,
            font=("Segoe UI", 9),
        )

        # Toolbar: chọn tất / bỏ chọn tất
        bar = ttk.Frame(self, style="Wiz.TFrame", padding=(12, 4, 12, 4))
        bar.pack(fill="x")
        ttk.Button(
            bar, text="Chọn tất cả", style="WizSubtle.TButton",
            command=lambda: self._set_all(True),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Bỏ chọn tất", style="WizSubtle.TButton",
            command=lambda: self._set_all(False),
        ).pack(side="left")

        # Footer: OK / Cancel
        ftr = ttk.Frame(self, style="Wiz.TFrame", padding=(12, 6, 12, 12))
        ftr.pack(fill="x")
        ttk.Button(
            ftr, text="Hủy", style="WizSubtle.TButton",
            command=self._on_cancel,
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            ftr, text="Áp dụng PPCT", style="WizSuccess.TButton",
            command=self._on_apply,
        ).pack(side="right")

    def _populate(self):
        for r in self._results:
            gk = r["group_key"]
            # Pre-check nếu suggested != current
            checked = r["suggested_ppct"] != r["current_ppct"]
            var = tk.BooleanVar(value=checked)
            self._checked[gk] = var
            tick = "☑" if checked else "☐"
            detail = (
                f"({r['weeks_before_le']}+{r['weeks_before_chan']})t × "
                f"({r['tiet_le']}+{r['tiet_chan']})PPCT/t = {r['total_before']}"
            )
            tag = "apply_yes" if checked else "apply_no"
            self.tree.insert(
                "", "end", iid=gk,
                values=(tick, r["lop_text"], r["phan_mon_text"],
                       r["current_ppct"] or "—", r["suggested_ppct"], detail),
                tags=(tag,),
            )

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != "#1":  # cột Áp dụng
            return
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self._checked:
            return
        var = self._checked[iid]
        var.set(not var.get())
        self._refresh_row(iid)

    def _refresh_row(self, iid: str):
        var = self._checked.get(iid)
        if var is None:
            return
        checked = var.get()
        tick = "☑" if checked else "☐"
        # Cập nhật cell + tag
        cur_vals = list(self.tree.item(iid, "values"))
        cur_vals[0] = tick
        self.tree.item(iid, values=cur_vals,
                      tags=("apply_yes" if checked else "apply_no",))

    def _set_all(self, checked: bool):
        for gk, var in self._checked.items():
            var.set(checked)
            self._refresh_row(gk)

    def _on_apply(self):
        self.selected_groups = [
            gk for gk, var in self._checked.items() if var.get()
        ]
        self.destroy()

    def _on_cancel(self):
        self.selected_groups = None
        self.destroy()

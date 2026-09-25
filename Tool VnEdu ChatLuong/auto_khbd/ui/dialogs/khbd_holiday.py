"""Hộp thoại khai báo ngày nghỉ cho rà soát KHBD."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...engine.backup.logic_engine import KHBDHolidayRequest
from ..styles import apply_wizard_styles
from ..theme import CLR_PANEL_BG


class KHBDHolidayDialog(tk.Toplevel):
    """Nhập nhanh ngày Nghỉ + tuần Dạy bù cho KHBD Logic Engine."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Lịch nghỉ / Dạy bù")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent)
        self.resizable(False, False)
        apply_wizard_styles(ttk.Style(self))
        self.result: KHBDHolidayRequest | None = None

        self.var_tuan = tk.IntVar(value=32)
        self.var_thu = tk.IntVar(value=2)
        self.var_buoi = tk.StringVar(value="0 - Cả ngày")
        self.var_makeup = tk.IntVar(value=36)
        self.var_note = tk.StringVar(value="Ngày nghỉ")
        self.var_include_unmade = tk.BooleanVar(value=True)
        self.var_resequence = tk.BooleanVar(value=True)

        frm = ttk.Frame(self, padding=14, style="Wiz.TFrame")
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(
            frm,
            text="Khai báo ngày nghỉ, chọn tuần dạy bù; công cụ sẽ tính lại PPCT theo đúng thứ tự.",
            style="WizHint.TLabel",
            wraplength=520,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        fields = ttk.LabelFrame(
            frm, text=" Thông tin nghỉ / bù ", padding=10,
            style="Wiz.TLabelframe",
        )
        fields.grid(row=1, column=0, columnspan=2, sticky="ew")
        fields.columnconfigure(1, weight=1)

        ttk.Label(fields, text="Tuần nghỉ:", style="Wiz.TLabel").grid(
            row=0, column=0, sticky="w", pady=3,
        )
        ttk.Spinbox(
            fields, from_=1, to=52, textvariable=self.var_tuan,
            width=8, font=("Segoe UI", 10),
        ).grid(row=0, column=1, sticky="w", pady=3)

        ttk.Label(fields, text="Thứ:", style="Wiz.TLabel").grid(
            row=1, column=0, sticky="w", pady=3,
        )
        ttk.Spinbox(
            fields, from_=2, to=7, textvariable=self.var_thu,
            width=8, font=("Segoe UI", 10),
        ).grid(row=1, column=1, sticky="w", pady=3)

        ttk.Label(fields, text="Buổi:", style="Wiz.TLabel").grid(
            row=2, column=0, sticky="w", pady=3,
        )
        ttk.Combobox(
            fields, textvariable=self.var_buoi,
            values=("0 - Cả ngày", "1 - Sáng", "2 - Chiều"),
            state="readonly", width=16,
        ).grid(row=2, column=1, sticky="w", pady=3)

        ttk.Label(fields, text="Tuần bù:", style="Wiz.TLabel").grid(
            row=3, column=0, sticky="w", pady=3,
        )
        ttk.Spinbox(
            fields, from_=1, to=52, textvariable=self.var_makeup,
            width=8, font=("Segoe UI", 10),
        ).grid(row=3, column=1, sticky="w", pady=3)

        ttk.Label(fields, text="Ghi chú:", style="Wiz.TLabel").grid(
            row=4, column=0, sticky="w", pady=3,
        )
        ttk.Entry(fields, textvariable=self.var_note, width=44).grid(
            row=4, column=1, sticky="ew", pady=3,
        )

        ttk.Checkbutton(
            frm,
            text="Tự tìm tiết Nghỉ trong tuần đó nếu chưa có tiết dạy lại tương ứng",
            variable=self.var_include_unmade,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            frm,
            text="Tự kéo lại PPCT và tên bài theo chuỗi đang dạy sau khi tạo bù",
            variable=self.var_resequence,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btns = ttk.Frame(frm, style="Wiz.TFrame")
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(
            btns, text="Hủy", command=self.destroy,
            style="WizSubtle.TButton",
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            btns, text="Tạo đề xuất", command=self._on_ok,
            style="WizPrimary.TButton",
        ).pack(side="right")

        self.update_idletasks()
        w = 590
        h = self.winfo_reqheight() + 10
        x = self.winfo_toplevel().winfo_rootx() + 80
        y = self.winfo_toplevel().winfo_rooty() + 80
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.grab_set()

    def _on_ok(self):
        try:
            buoi_raw = str(self.var_buoi.get()).split("-", 1)[0].strip()
            self.result = KHBDHolidayRequest(
                holiday_tuan=int(self.var_tuan.get()),
                thu=int(self.var_thu.get()),
                buoi_idx=int(buoi_raw or 0),
                makeup_tuan=int(self.var_makeup.get()),
                note=self.var_note.get().strip(),
                include_unmade_nghi_in_week=bool(self.var_include_unmade.get()),
                auto_resequence=bool(self.var_resequence.get()),
            )
        except Exception as e:
            messagebox.showerror("Sai dữ liệu", str(e), parent=self)
            return
        if not (
            1 <= self.result.holiday_tuan <= 52
            and 1 <= self.result.makeup_tuan <= 52
        ):
            messagebox.showerror(
                "Sai tuần", "Tuần phải nằm trong khoảng 1-52.",
                parent=self,
            )
            return
        if not (2 <= self.result.thu <= 7):
            messagebox.showerror(
                "Sai thứ", "Thứ phải nằm trong khoảng 2-7.",
                parent=self,
            )
            return
        self.destroy()

"""Hộp thoại chọn lớp + môn + phân môn cho 1 tiết."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from ...engine.bootstrap import BootstrapData
from ...engine.profile.models import SlotEntry
from ..styles import apply_wizard_styles
from ..theme import CLR_PANEL_BG


class SlotPickerDialog(tk.Toplevel):
    """Dialog chọn (lớp + môn + phân môn) cho 1 tiết.

    Args:
        parent: Toplevel hoặc widget cha
        thu, buoi, tiet: vị trí tiết trong lưới
        current: SlotEntry hiện tại (nếu sửa) hoặc None (nếu thêm mới)
        bootstrap: BootstrapData để build option list
        on_save: callback nhận SlotEntry mới
        on_delete: callback xóa tiết hiện tại (chỉ khi current != None)
    """

    def __init__(
        self,
        parent,
        thu: int, buoi: int, tiet: int,
        current: SlotEntry | None,
        bootstrap: BootstrapData,
        on_save: Callable[[SlotEntry], None],
        on_delete: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.thu = thu
        self.buoi = buoi
        self.tiet = tiet
        self.current = current
        self.bootstrap = bootstrap
        self.on_save = on_save
        self.on_delete = on_delete

        # Tk vars
        self.var_lop = tk.StringVar()
        self.var_mon = tk.StringVar()
        self.var_pm = tk.StringVar()

        # Internal — current selected IDs (chứ không chỉ text)
        self._lop_options: list[dict] = []
        self._mon_options: list[dict] = []
        self._pm_options: list[dict] = []

        self._build_ui()
        self._init_values_from_current()

        # Modal
        self.transient(parent)
        self.grab_set()
        self.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._on_save())

        # Center over parent
        self.update_idletasks()
        if parent and hasattr(parent, "winfo_toplevel"):
            top = parent.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() // 2) - (self.winfo_width() // 2)
            y = top.winfo_rooty() + (top.winfo_height() // 2) - (self.winfo_height() // 2)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _build_ui(self):
        self.title("Chọn lớp + môn cho tiết này")
        self.configure(background=CLR_PANEL_BG)
        self.resizable(False, False)
        apply_wizard_styles(ttk.Style(self))

        body = ttk.Frame(self, padding=16, style="Wiz.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        # Title
        thu_text = "Chủ nhật" if self.thu == 8 else f"Thứ {self.thu}"
        buoi_text = "Sáng" if self.buoi == 1 else "Chiều"
        ttk.Label(
            body,
            text=f"Tiết: {thu_text} – {buoi_text} – Tiết {self.tiet}",
            style="WizAccent.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Lớp
        ttk.Label(body, text="Lớp:", style="Wiz.TLabel"
                ).grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        self.cmb_lop = ttk.Combobox(
            body, textvariable=self.var_lop, state="readonly",
            font=("Segoe UI", 10), width=30,
        )
        self.cmb_lop.grid(row=1, column=1, sticky="ew", pady=4)
        self.cmb_lop.bind("<<ComboboxSelected>>", lambda e: self._on_lop_changed())

        # Môn học
        ttk.Label(body, text="Môn học:", style="Wiz.TLabel"
                ).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        self.cmb_mon = ttk.Combobox(
            body, textvariable=self.var_mon, state="readonly",
            font=("Segoe UI", 10), width=30,
        )
        self.cmb_mon.grid(row=2, column=1, sticky="ew", pady=4)
        self.cmb_mon.bind("<<ComboboxSelected>>", lambda e: self._on_mon_changed())

        # Phân môn
        ttk.Label(body, text="Phân môn:", style="Wiz.TLabel"
                ).grid(row=3, column=0, sticky="e", padx=(0, 8), pady=4)
        self.cmb_pm = ttk.Combobox(
            body, textvariable=self.var_pm, state="readonly",
            font=("Segoe UI", 10), width=30,
        )
        self.cmb_pm.grid(row=3, column=1, sticky="ew", pady=4)

        # Hint
        ttk.Label(
            body,
            text="Phân môn sẽ tự chọn nếu lớp + môn chỉ có 1 lựa chọn.",
            style="WizHint.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 12))

        # Buttons
        btn_row = ttk.Frame(body, style="Wiz.TFrame")
        btn_row.grid(row=5, column=0, columnspan=2, sticky="ew")

        if self.current is not None and self.on_delete is not None:
            ttk.Button(
                btn_row, text="🗑 Xóa tiết",
                command=self._on_delete,
                style="WizDanger.TButton",
            ).pack(side="left")

        ttk.Button(
            btn_row, text="Hủy",
            command=self.destroy,
            style="WizSubtle.TButton",
        ).pack(side="right", padx=(6, 0))

        ttk.Button(
            btn_row, text="Lưu", command=self._on_save,
            style="WizPrimary.TButton",
        ).pack(side="right")

        # Init lop options
        self._lop_options = list(self.bootstrap.lop_options)
        self.cmb_lop["values"] = [o["text"] for o in self._lop_options]

    def _init_values_from_current(self):
        """Pre-fill dropdown nếu đang sửa 1 slot có sẵn."""
        if not self.current:
            return

        # Validate: slot có thể trỏ đến lop/mon/pm đã bị xóa khỏi web
        # → cảnh báo user thay vì im lặng để dropdown rỗng.
        stale_warnings = []
        if self.bootstrap and not self.bootstrap.has_lop(self.current.lop_id):
            stale_warnings.append(
                f"Lớp '{self.current.lop_text}' không còn trong danh sách web."
            )
        elif (self.bootstrap
              and not self.bootstrap.has_mon(self.current.lop_id, self.current.mon_id)):
            stale_warnings.append(
                f"Môn '{self.current.mon_text}' không còn ở lớp này trên web."
            )
        elif (self.bootstrap
              and not self.bootstrap.has_phan_mon(
                  self.current.lop_id, self.current.mon_id, self.current.phan_mon_id)):
            stale_warnings.append(
                f"Phân môn '{self.current.phan_mon_text}' không còn trên web."
            )
        if stale_warnings:
            messagebox.showwarning(
                "Dữ liệu cũ",
                "Tiết hiện tại có dữ liệu không khớp với web:\n\n"
                + "\n".join(f"  • {w}" for w in stale_warnings)
                + "\n\nHãy chọn lại lớp/môn/phân môn từ danh sách hiện hành.",
                parent=self,
            )

        # Match lop by id
        for o in self._lop_options:
            if o.get("id") == self.current.lop_id:
                self.var_lop.set(o["text"])
                self._on_lop_changed()  # cascade
                break
        # Match mon by id
        for o in self._mon_options:
            if o.get("id") == self.current.mon_id:
                self.var_mon.set(o["text"])
                self._on_mon_changed()  # cascade
                break
        # Match pm by id
        for o in self._pm_options:
            if o.get("id") == self.current.phan_mon_id:
                self.var_pm.set(o["text"])
                break

    def _selected_lop(self) -> dict | None:
        text = self.var_lop.get()
        for o in self._lop_options:
            if o["text"] == text:
                return o
        return None

    def _selected_mon(self) -> dict | None:
        text = self.var_mon.get()
        for o in self._mon_options:
            if o["text"] == text:
                return o
        return None

    def _selected_pm(self) -> dict | None:
        text = self.var_pm.get()
        for o in self._pm_options:
            if o["text"] == text:
                return o
        return None

    def _on_lop_changed(self):
        lop = self._selected_lop()
        if not lop:
            self._mon_options = []
            self._pm_options = []
            self.cmb_mon["values"] = []
            self.cmb_pm["values"] = []
            self.var_mon.set("")
            self.var_pm.set("")
            return
        self._mon_options = self.bootstrap.get_mon_options(lop["id"])
        self.cmb_mon["values"] = [o["text"] for o in self._mon_options]
        # Auto-select nếu chỉ 1 môn
        if len(self._mon_options) == 1:
            self.var_mon.set(self._mon_options[0]["text"])
            self._on_mon_changed()
        else:
            self.var_mon.set("")
            self._pm_options = []
            self.cmb_pm["values"] = []
            self.var_pm.set("")

    def _on_mon_changed(self):
        lop = self._selected_lop()
        mon = self._selected_mon()
        if not lop or not mon:
            self._pm_options = []
            self.cmb_pm["values"] = []
            self.var_pm.set("")
            return
        self._pm_options = self.bootstrap.get_phan_mon_options(lop["id"], mon["id"])
        self.cmb_pm["values"] = [o["text"] for o in self._pm_options]
        # Auto-select nếu chỉ 1 phân môn
        if len(self._pm_options) == 1:
            self.var_pm.set(self._pm_options[0]["text"])
        elif len(self._pm_options) == 0:
            # Không có phân môn — tạo 1 placeholder (giữ nguyên ID "0" sẽ fail
            # validation ở SlotEntry, nên thông báo cho user)
            self.var_pm.set("")
        else:
            self.var_pm.set("")

    def _on_save(self):
        lop = self._selected_lop()
        mon = self._selected_mon()
        pm = self._selected_pm()
        if not lop:
            messagebox.showwarning("Thiếu thông tin", "Hãy chọn Lớp.", parent=self)
            return
        if not mon:
            messagebox.showwarning("Thiếu thông tin", "Hãy chọn Môn học.", parent=self)
            return
        if not pm:
            messagebox.showwarning(
                "Thiếu thông tin",
                "Hãy chọn Phân môn.\n\nNếu danh sách phân môn rỗng, "
                "có thể lớp này chưa có phân môn nào trên web — "
                "hãy kiểm tra lại trên trang VnEdu.",
                parent=self,
            )
            return
        try:
            slot = SlotEntry(
                thu=self.thu, buoi=self.buoi, tiet=self.tiet,
                lop_id=lop["id"], lop_text=lop["text"],
                mon_id=mon["id"], mon_text=mon["text"],
                phan_mon_id=pm["id"], phan_mon_text=pm["text"],
            )
        except Exception as e:
            messagebox.showerror("Lỗi", f"Dữ liệu không hợp lệ: {e}", parent=self)
            return
        self.on_save(slot)
        self.destroy()

    def _on_delete(self):
        if self.on_delete is None:
            return
        if not messagebox.askyesno(
            "Xác nhận",
            f"Xóa tiết Thứ {self.thu} – "
            f"{'Sáng' if self.buoi == 1 else 'Chiều'} – Tiết {self.tiet}?",
            parent=self,
        ):
            return
        self.on_delete()
        self.destroy()

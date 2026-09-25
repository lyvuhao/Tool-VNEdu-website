"""Hộp thoại xoá nhiều tuần KHDH (2 lần xác nhận)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..styles import apply_wizard_styles
from ..theme import CLR_PANEL_BG


# =====================================================================
# Dialog — DeleteWeeksDialog (xóa nhiều tuần KHDH với 2 lần confirm)
# =====================================================================

# Chuỗi xác nhận user phải gõ TAY (không paste) để enable nút Xóa.
# Dùng ký tự tiếng Việt có dấu để filter input copy-paste vô tình.
DELETE_CONFIRM_TEXT = "XÓA"


class DeleteWeeksDialog(tk.Toplevel):
    """Hộp xác nhận xóa nhiều tuần KHDH của giáo viên hiện tại.

    SIẾT LOGIC AN TOÀN:
    1. Nút Xóa **chỉ enable** khi user gõ chính xác chuỗi "XÓA" (có dấu,
       in hoa) vào ô Entry confirm — chống click vô tình + chống paste.
    2. **2 lần confirm**:
       a) Dialog này (Toplevel modal) — user gõ "XÓA" + click button
       b) `messagebox.askyesno` thứ 2 với detail tóm tắt
    3. Title đỏ rõ ràng "🗑 XÓA TUẦN KHDH" — không có từ "cấp" / "tất cả"
       để tránh nhầm lẫn cognitive với menu "Xoá KHDH cấp THCS".
    4. Disclaimer rõ ràng "CHỈ xóa tuần đã chỉ định, KHÔNG động đến cấp THCS".
    5. Spinbox validate range 1..52, swap không tự động (force user nhập đúng).
    6. Đếm số tuần realtime khi đổi range.

    Returns:
        - `self.confirmed_range`: tuple (tuan_from, tuan_to) nếu user xác nhận
        - None nếu user hủy / đóng dialog
    """

    def __init__(self, parent, gv_name: str = "", nam_hoc: int = 0,
                 cap_hoc_text: str = "",
                 default_from: int = 1, default_to: int = 1):
        super().__init__(parent)
        self.title("🗑 XÓA TUẦN KHDH")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        apply_wizard_styles(ttk.Style(self))

        self.confirmed_range: tuple[int, int] | None = None
        self.var_tuan_from = tk.IntVar(value=max(1, min(52, int(default_from or 1))))
        self.var_tuan_to = tk.IntVar(value=max(1, min(52, int(default_to or 1))))
        self.var_count = tk.StringVar(value="")
        self.var_confirm_text = tk.StringVar(value="")
        # Khi confirm text HOẶC range thay đổi → cần re-evaluate button
        # state. Vì cả 2 đều ảnh hưởng (text="XÓA" + range hợp lệ).
        def _on_any_change(*_):
            self._refresh_count()
            self._on_confirm_text_changed()
        self.var_confirm_text.trace_add("write", _on_any_change)
        self.var_tuan_from.trace_add("write", _on_any_change)
        self.var_tuan_to.trace_add("write", _on_any_change)

        self.gv_name = gv_name or "(không xác định)"
        self.nam_hoc = int(nam_hoc or 0)
        self.cap_hoc_text = cap_hoc_text or ""

        self._build_ui()
        self._refresh_count()
        self._on_confirm_text_changed()

        # Center on parent
        self.update_idletasks()
        try:
            top = parent.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() // 2) - (self.winfo_width() // 2)
            y = top.winfo_rooty() + (top.winfo_height() // 2) - (self.winfo_height() // 2)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self.bind("<Escape>", lambda e: self.destroy())

    def _build_ui(self):
        body = ttk.Frame(self, padding=18, style="Wiz.TFrame")
        body.pack(fill="both", expand=True)

        # Title đỏ
        tk.Label(
            body, text="🗑 XÓA TUẦN KHDH",
            font=("Segoe UI", 14, "bold"),
            fg="#b00020", bg=CLR_PANEL_BG,
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            body,
            text="⚠ HÀNH ĐỘNG NÀY KHÔNG HOÀN TÁC ĐƯỢC!",
            font=("Segoe UI", 10, "bold"),
            fg="#b00020", bg=CLR_PANEL_BG,
        ).pack(anchor="w", pady=(0, 10))

        # Disclaimer rõ ràng
        warn_frame = tk.Frame(body, background="#fff7e6",
                             highlightbackground="#d9a900",
                             highlightthickness=1)
        warn_frame.pack(fill="x", pady=(0, 10))
        tk.Label(
            warn_frame,
            text=(
                "Công cụ sẽ CHỈ xóa các TUẦN bạn chỉ định bên dưới của riêng bạn.\n"
                "TUYỆT ĐỐI KHÔNG động đến:\n"
                "    • KHDH cấp THCS / THPT / Tiểu học\n"
                "    • KHDH của các giáo viên khác\n"
                "    • Các tuần ngoài khoảng đã chọn"
            ),
            font=("Segoe UI", 9),
            background="#fff7e6", fg="#5c4400",
            anchor="w", justify="left",
            padx=10, pady=8,
        ).pack(fill="x")

        # Thông tin GV/năm/cấp — hiển thị cho user verify
        info_frame = ttk.Frame(body, style="Wiz.TFrame")
        info_frame.pack(fill="x", pady=(0, 10))
        info_lines = [
            f"Giáo viên:  {self.gv_name}",
        ]
        if self.nam_hoc:
            info_lines.append(
                f"Năm học:    {self.nam_hoc}–{self.nam_hoc + 1}"
            )
        if self.cap_hoc_text:
            info_lines.append(f"Cấp:        {self.cap_hoc_text}")
        for line in info_lines:
            ttk.Label(
                info_frame, text=line, style="Wiz.TLabel",
                font=("Consolas", 10),
            ).pack(anchor="w")

        # Spinbox tuần
        range_frame = ttk.LabelFrame(
            body, text=" Khoảng tuần xóa ",
            padding=10, style="Wiz.TLabelframe",
        )
        range_frame.pack(fill="x", pady=(0, 10))
        r1 = ttk.Frame(range_frame, style="Wiz.TFrame")
        r1.pack(fill="x")
        ttk.Label(r1, text="Từ tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52,
            textvariable=self.var_tuan_from,
            width=5, font=("Segoe UI", 11),
        ).pack(side="left", padx=(6, 14))
        ttk.Label(r1, text="đến tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52,
            textvariable=self.var_tuan_to,
            width=5, font=("Segoe UI", 11),
        ).pack(side="left", padx=(6, 14))

        self._lbl_count = ttk.Label(
            range_frame, textvariable=self.var_count,
            style="WizAccent.TLabel",
            font=("Segoe UI", 10, "bold"),
        )
        self._lbl_count.pack(anchor="w", pady=(8, 0))

        # Confirm text input
        cfm_frame = ttk.Frame(body, style="Wiz.TFrame")
        cfm_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(
            cfm_frame,
            text=f"Để xác nhận, gõ chính xác chuỗi  \"{DELETE_CONFIRM_TEXT}\"  "
                 "(có dấu, in hoa):",
            style="Wiz.TLabel",
        ).pack(anchor="w")
        self._entry_confirm = ttk.Entry(
            cfm_frame, textvariable=self.var_confirm_text,
            font=("Consolas", 12), width=20,
        )
        self._entry_confirm.pack(anchor="w", pady=(4, 0))
        self._lbl_confirm_state = tk.Label(
            cfm_frame, text="", font=("Segoe UI", 9),
            background=CLR_PANEL_BG,
        )
        self._lbl_confirm_state.pack(anchor="w", pady=(2, 0))

        # Buttons
        btn_row = ttk.Frame(body, style="Wiz.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(
            btn_row, text="Hủy", command=self.destroy,
            style="WizSubtle.TButton", width=12,
        ).pack(side="left")
        self._btn_delete = ttk.Button(
            btn_row, text="🗑 Xóa tuần",
            command=self._on_delete_clicked,
            style="WizDanger.TButton", width=18,
            state="disabled",
        )
        self._btn_delete.pack(side="right")

        # Auto-focus vào entry confirm
        self._entry_confirm.focus_set()

    def _refresh_count(self):
        try:
            f = int(self.var_tuan_from.get())
            t = int(self.var_tuan_to.get())
        except (tk.TclError, ValueError):
            self.var_count.set("⚠ Tuần không hợp lệ")
            try:
                self._lbl_count.configure(foreground="#b00020")
            except Exception:
                pass
            return
        if not (1 <= f <= 52 and 1 <= t <= 52):
            self.var_count.set("⚠ Tuần phải trong khoảng 1..52")
            try:
                self._lbl_count.configure(foreground="#b00020")
            except Exception:
                pass
            return
        if f > t:
            self.var_count.set(
                f"⚠ Tuần từ ({f}) > tuần đến ({t}) — vui lòng đảo lại"
            )
            try:
                self._lbl_count.configure(foreground="#b00020")
            except Exception:
                pass
            return
        count = t - f + 1
        self.var_count.set(
            f"→ Số tuần sẽ xóa: {count} tuần (tuần {f} → tuần {t})"
        )
        try:
            self._lbl_count.configure(foreground="#1f7a1f")
        except Exception:
            pass

    def _on_confirm_text_changed(self):
        text = self.var_confirm_text.get()
        if text == DELETE_CONFIRM_TEXT:
            # Đã gõ đúng — kiểm thêm range hợp lệ
            range_ok = self._is_range_valid()
            if range_ok:
                self._btn_delete.configure(state="normal")
                self._lbl_confirm_state.configure(
                    text="✓ Đã xác nhận — có thể xóa",
                    fg="#1f7a1f",
                )
            else:
                self._btn_delete.configure(state="disabled")
                self._lbl_confirm_state.configure(
                    text="✓ Đã xác nhận, nhưng tuần chưa hợp lệ",
                    fg="#a07000",
                )
        elif text == "":
            self._btn_delete.configure(state="disabled")
            self._lbl_confirm_state.configure(text="", fg="#888")
        else:
            self._btn_delete.configure(state="disabled")
            self._lbl_confirm_state.configure(
                text=f"✗ Sai — gõ chính xác \"{DELETE_CONFIRM_TEXT}\"",
                fg="#b00020",
            )

    def _is_range_valid(self) -> bool:
        try:
            f = int(self.var_tuan_from.get())
            t = int(self.var_tuan_to.get())
        except (tk.TclError, ValueError):
            return False
        return 1 <= f <= t <= 52

    def _on_delete_clicked(self):
        # Defensive: re-check tất cả conditions
        if self.var_confirm_text.get() != DELETE_CONFIRM_TEXT:
            return
        if not self._is_range_valid():
            messagebox.showerror(
                "Tuần không hợp lệ",
                "Khoảng tuần phải nằm trong 1..52 và tuần từ ≤ tuần đến.",
                parent=self,
            )
            return
        f = int(self.var_tuan_from.get())
        t = int(self.var_tuan_to.get())
        count = t - f + 1

        # Confirm lần 2 — messagebox standard
        msg = (
            f"Bạn sắp xóa {count} tuần KHDH:\n\n"
            f"  • Giáo viên:  {self.gv_name}\n"
            + (f"  • Năm học:    {self.nam_hoc}–{self.nam_hoc + 1}\n"
               if self.nam_hoc else "")
            + (f"  • Cấp:        {self.cap_hoc_text}\n"
               if self.cap_hoc_text else "")
            + f"  • Khoảng:     Tuần {f} → Tuần {t}\n\n"
            f"Hành động này KHÔNG hoàn tác được.\n"
            f"Bạn có chắc chắn muốn tiếp tục?"
        )
        if not messagebox.askyesno(
            "Xác nhận xóa lần cuối",
            msg, parent=self,
            icon="warning",
        ):
            return

        self.confirmed_range = (f, t)
        self.destroy()

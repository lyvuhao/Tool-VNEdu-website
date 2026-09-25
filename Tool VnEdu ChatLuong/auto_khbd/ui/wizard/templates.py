"""Tách TKB lẻ/chẵn, phóng to TKB, import TKB từ web."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...engine.profile.models import SlotEntry, TKBTemplate
from ...log import logger
from ..dialogs.import_tkb import ImportTKBDialog
from ..full_tkb_editor.window import FullTKBEditorWindow
from ..theme import CLR_PANEL_BG
from ..workers.tkb_import import TKBPattern


class TemplatesMixin:
    """Tách TKB lẻ/chẵn, phóng to TKB, import TKB từ web."""

    # -----------------------------------------------------------
    # Tách lẻ/chẵn — toggle handler
    # -----------------------------------------------------------

    def _on_tach_changed(self):
        """User tick/untick checkbox 'Tôi dạy khác nhau giữa tuần lẻ/chẵn'."""
        new_state = bool(self.var_tach_le_chan.get())
        if new_state == self.profile.tach_le_chan:
            return  # No actual change

        # Block khi executor đang chạy — revert checkbox
        if self._is_executor_running():
            self._loading_profile = True
            try:
                self.var_tach_le_chan.set(self.profile.tach_le_chan)
            finally:
                self._loading_profile = False
            messagebox.showwarning(
                "Đang nhập KHDH",
                "Không thể thay đổi cấu trúc TKB khi đang nhập web.\n"
                "Hãy chờ hoàn tất hoặc bấm [Dừng].",
                parent=self,
            )
            return

        if new_state:
            # Bật: clone template
            self.profile.toggle_tach_le_chan(True)
            self.var_active_tab.set("le")
            self._build_le_chan_tabs()
            self._log("Đã tách lịch tuần lẻ và tuần chẵn (clone TKB hiện tại).", "info")
        else:
            # Tắt: hỏi giữ le hay chan
            choice = self._ask_keep_which()
            if choice is None:
                # User hủy → revert checkbox
                self.var_tach_le_chan.set(True)
                return
            self.profile.toggle_tach_le_chan(False, prefer=choice)
            self.var_active_tab.set("chinh")
            self._destroy_le_chan_tabs()
            self._log(f"Đã gộp về 1 mẫu (giữ {'lẻ' if choice == 'le' else 'chẵn'}).", "info")

        self._refresh_grid()
        self._refresh_ppct_table()
        self._refresh_tuan_hint()
        self._mark_dirty()

    def _ask_keep_which(self) -> str | None:
        """Dialog hỏi giữ template lẻ hay chẵn."""
        win = tk.Toplevel(self)
        win.title("Gộp lại 1 mẫu")
        win.configure(background=CLR_PANEL_BG)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        win.resizable(False, False)
        result = {"choice": None}

        body = ttk.Frame(win, padding=18, style="Wiz.TFrame")
        body.pack()
        ttk.Label(
            body, text="Bạn muốn giữ mẫu nào làm chính?",
            style="WizTitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            body,
            text="Mẫu kia sẽ bị xóa. Hành động này không hoàn tác được.",
            style="WizHint.TLabel", wraplength=320,
        ).pack(anchor="w", pady=(0, 12))

        btn_row = ttk.Frame(body, style="Wiz.TFrame")
        btn_row.pack()

        def pick(c):
            result["choice"] = c
            win.destroy()

        ttk.Button(
            btn_row, text="Giữ TKB lẻ", command=lambda: pick("le"),
            style="WizPrimary.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            btn_row, text="Giữ TKB chẵn", command=lambda: pick("chan"),
            style="WizPrimary.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            btn_row, text="Hủy", command=win.destroy,
            style="WizSubtle.TButton",
        ).pack(side="left")

        # Center
        win.update_idletasks()
        top = self.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() // 2) - (win.winfo_width() // 2)
        y = top.winfo_rooty() + (top.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.wait_window(win)
        return result["choice"]

    # -----------------------------------------------------------
    # Phóng to TKB — cửa sổ full screen + chế độ lẻ-chẵn cạnh nhau
    # -----------------------------------------------------------

    def _on_full_tkb_clicked(self):
        """Mở `FullTKBEditorWindow`. Nếu đã mở rồi → focus lại."""
        # Tránh mở 2 cửa sổ song song
        existing = getattr(self, "_full_tkb_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        # Cần có profile để hiển thị template
        if not self.profile:
            messagebox.showwarning(
                "Chưa có TKB",
                "Hãy mở hồ sơ hoặc soạn TKB trước khi phóng to.",
                parent=self,
            )
            return
        win = FullTKBEditorWindow(self)
        self._full_tkb_window = win

        def _on_full_destroy(_e=None):
            if _e is not None and _e.widget is not win:
                return
            self._full_tkb_window = None
        win.bind("<Destroy>", _on_full_destroy, add="+")

    # -----------------------------------------------------------
    # Import TKB từ web — quét + chọn pattern + áp vào template
    # -----------------------------------------------------------

    def _on_import_tkb_clicked(self):
        """Mở ImportTKBDialog → spawn TKBScanWorker → user chọn → apply."""
        if self._guard_cdp_exclusive("Nhập TKB từ web"):
            return
        # Cần đăng nhập trước — port phải có Chrome + tab VnEdu
        try:
            port = int(self.var_port.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning(
                "Cổng CDP không hợp lệ",
                "Hãy nhập cổng CDP hợp lệ trước khi quét TKB.",
                parent=self,
            )
            return

        # Suggest Save-As trước nếu profile dirty + có path → tránh mất data
        if self._is_dirty and self._profile_path:
            ans = messagebox.askyesnocancel(
                "Lưu trước khi nhập?",
                (
                    "Bạn đang có thay đổi chưa lưu trên TKB hiện tại.\n\n"
                    "• [Yes] Lưu hồ sơ rồi tiếp tục nhập TKB từ web.\n"
                    "• [No]  Tiếp tục, KHÔNG lưu (có thể mất thay đổi).\n"
                    "• [Cancel] Hủy thao tác."
                ),
                parent=self,
            )
            if ans is None:
                return
            if ans:
                self._on_save_profile()
                # Nếu vẫn dirty (user hủy save dialog) → abort
                if self._is_dirty:
                    return

        dlg = ImportTKBDialog(self, port=port)
        self._import_tkb_dialog = dlg
        try:
            self.wait_window(dlg)
        finally:
            self._import_tkb_dialog = None

        result = getattr(dlg, "imported", None)
        if not result:
            return  # User huỷ
        try:
            self._apply_imported_tkb(result)
        except Exception as e:
            logger.exception("apply_imported_tkb failed")
            messagebox.showerror(
                "Lỗi áp dụng",
                f"Không áp dụng được TKB đã nhập: {e}",
                parent=self,
            )

    def _apply_imported_tkb(self, result: dict):
        """Áp pattern đã chọn vào profile.template_chinh / template_le /
        template_chan. Confirm 1 lần nếu sẽ đè template có data hiện tại.

        SIẾT LOGIC:
        - Block nếu executor đang chạy (race với checkpoint).
        - Confirm khi đè template có ≥ 1 slot để user khỏi mất data.
        - KHÔNG đụng `ppct_starts` (giữ PPCT mà user đã set).
        - Sau apply: rebuild grid + ppct_table + tuan_hint + mark_dirty.
        - Log + status message rõ ràng.
        """
        if self._block_if_executor_running("áp TKB từ web"):
            return
        if not self.profile:
            return
        mode = result.get("mode")
        if mode == "single":
            p: TKBPattern = result["pattern"]
            # Đếm slot đang có để cảnh báo
            existing = sum(
                t.slot_count() for t in self.profile.all_templates()
            )
            if existing > 0:
                if not messagebox.askyesno(
                    "Đè TKB hiện có?",
                    (
                        f"TKB hiện tại có {existing} slot đã thiết kế.\n\n"
                        f"Sẽ ĐÈ toàn bộ TKB hiện tại bằng Mẫu "
                        f"{p.pattern_id} ({len(p.slots)} slot, "
                        f"{len(p.weeks)} tuần xuất hiện trên web).\n\n"
                        "Tiếp tục? (Hành động này KHÔNG hoàn tác được "
                        "sau khi đã lưu file)."
                    ),
                    parent=self,
                ):
                    return
            # Reset về 1 mẫu chính (không tách)
            self.profile.tach_le_chan = False
            self.profile.template_chinh = TKBTemplate(
                slots=[
                    SlotEntry(
                        thu=s.thu, buoi=s.buoi, tiet=s.tiet,
                        lop_id=s.lop_id, lop_text=s.lop_text,
                        mon_id=s.mon_id, mon_text=s.mon_text,
                        phan_mon_id=s.phan_mon_id,
                        phan_mon_text=s.phan_mon_text,
                    ) for s in p.slots
                ]
            )
            self.profile.template_le = None
            self.profile.template_chan = None
            self._loading_profile = True
            try:
                self.var_tach_le_chan.set(False)
                self.var_active_tab.set("chinh")
            finally:
                self._loading_profile = False
            self._destroy_le_chan_tabs()
            self._log(
                f"Đã nhập TKB từ web: Mẫu {p.pattern_id} "
                f"({len(p.slots)} slot).",
                "ok",
            )
            self.var_status.set(
                f"✓ Áp TKB Mẫu {p.pattern_id}: {len(p.slots)} slot"
            )
        elif mode == "le_chan":
            le_p: TKBPattern = result["pattern_le"]
            chan_p: TKBPattern = result["pattern_chan"]
            existing = sum(
                t.slot_count() for t in self.profile.all_templates()
            )
            if existing > 0:
                if not messagebox.askyesno(
                    "Đè TKB hiện có?",
                    (
                        f"TKB hiện tại có {existing} slot đã thiết kế.\n\n"
                        f"Sẽ ĐÈ toàn bộ TKB hiện tại bằng:\n"
                        f"  • Tuần lẻ: Mẫu {le_p.pattern_id} "
                        f"({len(le_p.slots)} slot)\n"
                        f"  • Tuần chẵn: Mẫu {chan_p.pattern_id} "
                        f"({len(chan_p.slots)} slot)\n\n"
                        "Tiếp tục? (KHÔNG hoàn tác được sau khi đã lưu)."
                    ),
                    parent=self,
                ):
                    return
            self.profile.tach_le_chan = True
            self.profile.template_le = TKBTemplate(slots=[
                SlotEntry(
                    thu=s.thu, buoi=s.buoi, tiet=s.tiet,
                    lop_id=s.lop_id, lop_text=s.lop_text,
                    mon_id=s.mon_id, mon_text=s.mon_text,
                    phan_mon_id=s.phan_mon_id,
                    phan_mon_text=s.phan_mon_text,
                ) for s in le_p.slots
            ])
            self.profile.template_chan = TKBTemplate(slots=[
                SlotEntry(
                    thu=s.thu, buoi=s.buoi, tiet=s.tiet,
                    lop_id=s.lop_id, lop_text=s.lop_text,
                    mon_id=s.mon_id, mon_text=s.mon_text,
                    phan_mon_id=s.phan_mon_id,
                    phan_mon_text=s.phan_mon_text,
                ) for s in chan_p.slots
            ])
            self.profile.template_chinh = None
            self._loading_profile = True
            try:
                self.var_tach_le_chan.set(True)
                self.var_active_tab.set("le")
            finally:
                self._loading_profile = False
            self._build_le_chan_tabs()
            self._log(
                f"Đã nhập TKB từ web (lẻ/chẵn): Mẫu {le_p.pattern_id} "
                f"({len(le_p.slots)} slot) cho lẻ, Mẫu "
                f"{chan_p.pattern_id} ({len(chan_p.slots)} slot) cho chẵn.",
                "ok",
            )
            self.var_status.set(
                f"✓ Áp TKB lẻ/chẵn: lẻ={len(le_p.slots)} ô, "
                f"chẵn={len(chan_p.slots)} ô"
            )
        else:
            return

        # Sau khi đổi cấu trúc → rebuild UI + sync derived state
        self._refresh_grid()
        self._refresh_ppct_table()
        self._refresh_tuan_hint()
        self._mark_dirty()

    def _build_le_chan_tabs(self):
        """Show 2 tab radio button to switch giữa TKB lẻ và TKB chẵn."""
        for w in self._tab_frame.winfo_children():
            w.destroy()
        ttk.Label(
            self._tab_frame, text="Đang sửa: ",
            style="Wiz.TLabel",
        ).pack(side="left", padx=(0, 4))
        ttk.Radiobutton(
            self._tab_frame, text="TKB tuần lẻ (1, 3, 5…)",
            variable=self.var_active_tab, value="le",
            command=self._on_tab_switched,
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            self._tab_frame, text="TKB tuần chẵn (2, 4, 6…)",
            variable=self.var_active_tab, value="chan",
            command=self._on_tab_switched,
        ).pack(side="left")

    def _destroy_le_chan_tabs(self):
        for w in self._tab_frame.winfo_children():
            w.destroy()

    def _on_tab_switched(self):
        # Clear selection — slot keys giống nhau giữa lẻ/chẵn nhưng data khác,
        # giữ selection sẽ hiểu nhầm sang slot khác data.
        self._selected_slot_key = None
        self._refresh_grid()
        self._refresh_ppct_table()

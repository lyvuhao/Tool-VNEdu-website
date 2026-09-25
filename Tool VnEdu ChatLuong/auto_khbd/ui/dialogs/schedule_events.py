"""Lưới và trình quản lý sự kiện Nghỉ / Dạy bù."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ...engine.analyzer.models import TKB_EVENT_DAY_BU, TKB_EVENT_LABELS, TKB_EVENT_NGHI
from ...engine.profile.excel_io import _BUOI_TO_TEXT, _THU_TO_TEXT
from ...engine.profile.models import SlotEntry, TKBScheduleEvent, TKBTemplate
from ...engine.profile.profile import profile_schedule_events, validate_profile_schedule_events
from ..styles import apply_wizard_styles
from ..theme import (
    CLR_GRID_HEADER_BG,
    CLR_GRID_HEADER_FG,
    CLR_PANEL_BG,
    DAYS,
    fit_geometry_to_work_area,
    get_tk_work_area,
    TIETS_BY_BUOI,
)

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..wizard.wizard import KHDHWizard


# =====================================================================
# Dialog — TKBHolidayRulesDialog (Nghỉ/Dạy bù trong setup TKB chính)
# =====================================================================

class TKBScheduleEventGridDialog(tk.Toplevel):
    """Chọn nhiều tiết Nghỉ/Dạy bù bằng lưới TKB theo từng tuần."""

    CELL_W = 118
    CELL_H = 44

    def __init__(
        self,
        parent,
        wizard: "KHDHWizard",
        source_slot: SlotEntry,
        default_kind: str,
    ):
        super().__init__(parent.winfo_toplevel())
        self.wizard = wizard
        self.profile = wizard.profile
        self.source_slot = source_slot
        self.result: list[TKBScheduleEvent] | None = None
        self._selected: set[tuple[int, int, int, int]] = set()
        self._cell_buttons: dict[tuple[int, int, int, int], tk.Button] = {}

        self.var_kind = tk.StringVar(value=default_kind)
        self.var_from = tk.IntVar(value=self._safe_int(wizard.var_tuan_from.get(), 1))
        self.var_to = tk.IntVar(value=self._safe_int(wizard.var_tuan_to.get(), 35))
        self.var_view_week = tk.IntVar(value=self._safe_int(self.var_from.get(), 1))
        self.var_view_buoi = tk.IntVar(value=self._safe_int(source_slot.buoi, 1))
        self.var_note = tk.StringVar(
            value="Nghỉ theo kế hoạch" if default_kind == TKB_EVENT_NGHI else "Dạy bù"
        )
        self.var_replace = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="")

        self.withdraw()
        self.title("Chọn tiết Nghỉ / Dạy bù")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.resizable(True, True)
        apply_wizard_styles(ttk.Style(self))
        self.minsize(980, 620)

        self._build_ui()
        self._rebuild_week_grid()
        self._center_on_parent()
        self.deiconify()
        self.lift()
        self.focus_force()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.var_kind.trace_add("write", lambda *_: self._on_kind_changed())

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (tk.TclError, TypeError, ValueError):
            return default

    @staticmethod
    def _pos_label(thu: int, buoi: int, tiet: int) -> str:
        thu_lbl = "CN" if thu == 8 else f"Thứ {thu}"
        buoi_lbl = "Sáng" if buoi == 1 else "Chiều"
        return f"{thu_lbl} {buoi_lbl} tiết {tiet}"

    @staticmethod
    def _short_text(slot: SlotEntry | None) -> str:
        if slot is None:
            return "Trống"
        pm = slot.phan_mon_text or slot.mon_text
        return f"{slot.lop_text}\n{pm[:20]}"

    def _build_ui(self):
        body = ttk.Frame(self, padding=(12, 10), style="Wiz.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)

        source = ttk.LabelFrame(
            body, text=" 1. Tiết đang thao tác ", padding=(10, 8), style="Wiz.TLabelframe",
        )
        source.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        source.columnconfigure(1, weight=1)
        source.columnconfigure(3, weight=0)
        ttk.Label(source, text="Môn/phân môn:", style="Wiz.TLabel").grid(
            row=0, column=0, sticky="w",
        )
        ttk.Label(
            source,
            text=(
                f"{self.source_slot.lop_text} / {self.source_slot.mon_text} / "
                f"{self.source_slot.phan_mon_text}"
            ),
            style="WizAccent.TLabel",
            wraplength=640,
            justify="left",
        ).grid(row=0, column=1, sticky="ew", padx=(8, 18))
        ttk.Label(source, text="Ô đang chọn:", style="Wiz.TLabel").grid(
            row=0, column=2, sticky="e",
        )
        ttk.Label(
            source,
            text=self._pos_label(
                self.source_slot.thu, self.source_slot.buoi, self.source_slot.tiet
            ),
            style="Wiz.TLabel",
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(
            source,
            text=(
                "Chọn loại, chọn tuần áp dụng, rồi bấm ô tiết trong bảng. "
                "Ô vàng là ô đã chọn."
            ),
            style="WizMicro.TLabel",
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        controls = ttk.LabelFrame(
            body, text=" 2. Chọn việc cần làm ", padding=(10, 8),
            style="Wiz.TLabelframe",
        )
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        controls.columnconfigure(12, weight=1)
        ttk.Radiobutton(
            controls,
            text="Đánh dấu NGHỈ - tiết này không tăng PPCT",
            variable=self.var_kind,
            value=TKB_EVENT_NGHI,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=(0, 16))
        ttk.Radiobutton(
            controls,
            text="Thêm DẠY BÙ - tiết này có tăng PPCT",
            variable=self.var_kind,
            value=TKB_EVENT_DAY_BU,
        ).grid(row=0, column=4, columnspan=4, sticky="w")

        ttk.Label(controls, text="Xem tuần:", style="WizAccent.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0),
        )
        ttk.Button(
            controls, text="◀ Tuần trước", command=lambda: self._change_view_week(-1),
            style="WizSubtle.TButton",
        ).grid(row=1, column=1, sticky="w", padx=(6, 4), pady=(8, 0))
        spin = ttk.Spinbox(
            controls, from_=1, to=52, width=5, textvariable=self.var_view_week,
            command=self._rebuild_week_grid,
        )
        spin.grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(8, 0))
        ttk.Button(
            controls, text="Tuần sau ▶", command=lambda: self._change_view_week(1),
            style="WizSubtle.TButton",
        ).grid(row=1, column=3, sticky="w", padx=(0, 16), pady=(8, 0))

        ttk.Label(controls, text="Dải tuần:", style="Wiz.TLabel").grid(
            row=1, column=4, sticky="e", pady=(8, 0),
        )
        ttk.Spinbox(controls, from_=1, to=52, width=5, textvariable=self.var_from).grid(
            row=1, column=5, sticky="w", padx=(4, 4), pady=(8, 0),
        )
        ttk.Label(controls, text="đến", style="Wiz.TLabel").grid(
            row=1, column=6, sticky="w", pady=(8, 0),
        )
        ttk.Spinbox(controls, from_=1, to=52, width=5, textvariable=self.var_to).grid(
            row=1, column=7, sticky="w", padx=(4, 12), pady=(8, 0),
        )
        ttk.Checkbutton(
            controls,
            text="Dạy bù được dùng ô đang có tiết thường",
            variable=self.var_replace,
        ).grid(row=1, column=8, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Label(controls, text="Ghi chú:", style="Wiz.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.var_note, width=34).grid(
            row=2, column=1, columnspan=4, sticky="ew", padx=(6, 10), pady=(8, 0),
        )
        ttk.Button(
            controls, text="Chọn đúng ô này trong tuần đang xem",
            command=self._select_source_position_current_week,
            style="WizPrimary.TButton",
        ).grid(row=2, column=5, columnspan=3, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Button(
            controls, text="Chọn đúng ô này cho cả dải tuần",
            command=self._select_source_position,
            style="WizSubtle.TButton",
        ).grid(row=2, column=8, columnspan=3, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Button(
            controls, text="Bỏ chọn hết",
            command=self._clear_selection,
            style="WizSubtle.TButton",
        ).grid(row=2, column=11, sticky="ew", pady=(8, 0))

        grid_box = ttk.LabelFrame(
            body, text=" 3. Bấm vào ô tiết cần áp dụng ", padding=8,
            style="Wiz.TLabelframe",
        )
        grid_box.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        grid_box.columnconfigure(0, weight=1)
        grid_box.rowconfigure(1, weight=1)
        grid_header = ttk.Frame(grid_box, style="Wiz.TFrame")
        grid_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        grid_header.columnconfigure(0, weight=1)
        ttk.Label(
            grid_header,
            textvariable=self.var_status,
            style="WizHint.TLabel",
            wraplength=540,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        buoi_filter = ttk.Frame(grid_header, style="Wiz.TFrame")
        buoi_filter.grid(row=0, column=1, sticky="e")
        ttk.Label(buoi_filter, text="Buổi xem:", style="WizMicro.TLabel").pack(side="left", padx=(0, 6))
        for text, value in (("Sáng", 1), ("Chiều", 2), ("Cả ngày", 0)):
            ttk.Radiobutton(
                buoi_filter,
                text=text,
                variable=self.var_view_buoi,
                value=value,
            ).pack(side="left", padx=(0, 6))

        grid_scroll = ttk.Frame(grid_box, style="Wiz.TFrame")
        grid_scroll.grid(row=1, column=0, sticky="nsew")
        grid_scroll.columnconfigure(0, weight=1)
        grid_scroll.rowconfigure(0, weight=1)
        self.grid_canvas = tk.Canvas(
            grid_scroll,
            background=CLR_PANEL_BG,
            highlightthickness=0,
            width=760,
            height=470,
        )
        self.grid_canvas.grid(row=0, column=0, sticky="nsew")
        grid_vsb = ttk.Scrollbar(
            grid_scroll,
            orient="vertical",
            command=self.grid_canvas.yview,
        )
        grid_vsb.grid(row=0, column=1, sticky="ns")
        self.grid_canvas.configure(yscrollcommand=grid_vsb.set)
        self.grid_inner = ttk.Frame(self.grid_canvas, style="Wiz.TFrame")
        self._grid_window = self.grid_canvas.create_window(
            (0, 0), window=self.grid_inner, anchor="nw",
        )
        self.grid_inner.bind(
            "<Configure>",
            lambda _e: self.grid_canvas.configure(
                scrollregion=self.grid_canvas.bbox("all")
            ),
        )
        self.grid_canvas.bind(
            "<Configure>",
            lambda e: self.grid_canvas.itemconfigure(
                self._grid_window, width=max(1, e.width - 4)
            ),
        )
        self.grid_canvas.bind("<MouseWheel>", self._on_grid_mousewheel)
        self.grid_inner.bind("<MouseWheel>", self._on_grid_mousewheel)

        summary = ttk.LabelFrame(
            body, text=" Danh sách đã chọn ", padding=8,
            style="Wiz.TLabelframe",
        )
        summary.grid(row=2, column=1, sticky="nsew")
        summary.columnconfigure(0, weight=1)
        summary.rowconfigure(1, weight=1)
        self.var_selected_summary = tk.StringVar(value="Chưa chọn tiết nào.")
        ttk.Label(
            summary,
            textvariable=self.var_selected_summary,
            style="WizAccent.TLabel",
            wraplength=280,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.tree_selected = ttk.Treeview(
            summary,
            columns=("tuan", "vitri"),
            show="headings",
            height=8,
            style="Wiz.Treeview",
        )
        self.tree_selected.heading("tuan", text="Tuần")
        self.tree_selected.heading("vitri", text="Vị trí")
        self.tree_selected.column("tuan", width=55, anchor="center", stretch=False)
        self.tree_selected.column("vitri", width=210, anchor="w", stretch=True)
        self.tree_selected.grid(row=1, column=0, sticky="nsew")
        sb_sel = ttk.Scrollbar(summary, orient="vertical", command=self.tree_selected.yview)
        sb_sel.grid(row=1, column=1, sticky="ns")
        self.tree_selected.configure(yscrollcommand=sb_sel.set)
        ttk.Button(
            summary,
            text="Xóa dòng đang chọn",
            command=self._remove_selected_rows,
            style="WizSubtle.TButton",
        ).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            summary,
            text=(
                "Màu: xanh = đúng môn, trắng = trống, cam = tiết khác, vàng = đã chọn. "
                "Nghỉ chỉ chọn ô xanh; Dạy bù có thể chọn ô trống."
            ),
            style="WizMicro.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        footer = ttk.Frame(body, style="Wiz.TFrame")
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(
            footer,
            text="Sau khi bấm Lưu, các tiết này sẽ được đưa vào hồ sơ TKB. Chạy Xem trước để kiểm PPCT trước khi nhập web.",
            style="WizHint.TLabel",
            wraplength=780,
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            footer, text="Lưu các tiết đã chọn", command=self._on_save,
            style="WizSuccess.TButton",
        ).pack(side="right")
        ttk.Button(
            footer, text="Hủy", command=self.destroy, style="WizSubtle.TButton",
        ).pack(side="right", padx=(6, 8))
        self.var_view_week.trace_add(
            "write",
            lambda *_: self.after_idle(self._rebuild_week_grid),
        )
        self.var_view_buoi.trace_add(
            "write",
            lambda *_: self.after_idle(self._rebuild_week_grid),
        )

    def _center_on_parent(self):
        self.update_idletasks()
        try:
            _work_x, _work_y, work_w, work_h = get_tk_work_area(self)
            self.maxsize(work_w, work_h)
            w, h, x, y = fit_geometry_to_work_area(
                self,
                preferred_w=1180,
                preferred_h=840,
                margin=12,
                min_w=980,
                min_h=620,
                anchor="top_center",
                chrome_h=36,
            )
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _visible_week(self) -> int:
        week = self._safe_int(self.var_view_week.get(), self._safe_int(self.var_from.get(), 1))
        week = max(1, min(52, week))
        if week != self._safe_int(self.var_view_week.get(), 0):
            self.var_view_week.set(week)
        return week

    def _change_view_week(self, delta: int):
        self.var_view_week.set(max(1, min(52, self._visible_week() + int(delta))))
        self._rebuild_week_grid()

    def _selection_label(self, pos: tuple[int, int, int, int]) -> str:
        tuan, thu, buoi, tiet = pos
        return f"Tuần {tuan} - {self._pos_label(thu, buoi, tiet)}"

    def _on_grid_mousewheel(self, event):
        try:
            delta = -1 if event.delta > 0 else 1
            self.grid_canvas.yview_scroll(delta, "units")
            return "break"
        except Exception:
            return None

    def _on_kind_changed(self):
        self._selected.clear()
        if self.var_kind.get() == TKB_EVENT_NGHI:
            self.var_note.set("Nghỉ theo kế hoạch")
        elif self.var_note.get().strip() in ("", "Nghỉ theo kế hoạch"):
            self.var_note.set("Dạy bù")
        self._rebuild_week_grid()
        self._refresh_selection_list()

    def _template_for_week(self, tuan: int) -> TKBTemplate | None:
        try:
            return self.profile.get_active_template(tuan)
        except Exception:
            return None

    def _target_slot(self, tuan: int, thu: int, buoi: int, tiet: int) -> SlotEntry | None:
        template = self._template_for_week(tuan)
        if template is None:
            return None
        return template.slot_at(thu, buoi, tiet)

    def _can_select(self, tuan: int, thu: int, buoi: int, tiet: int) -> bool:
        if self.var_kind.get() == TKB_EVENT_DAY_BU:
            return True
        return self.source_slot.group_key == (
            self._target_slot(tuan, thu, buoi, tiet).group_key
            if self._target_slot(tuan, thu, buoi, tiet) else ""
        )

    def _rebuild_week_grid(self):
        for child in self.grid_inner.winfo_children():
            child.destroy()
        self._cell_buttons.clear()
        tuan = self._visible_week()
        panel = ttk.LabelFrame(
            self.grid_inner,
            text=f" Tuần {tuan} - chọn ô trong bảng bên dưới ",
            padding=4,
            style="Wiz.TLabelframe",
        )
        panel.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.grid_inner.columnconfigure(0, weight=1)
        self.grid_inner.rowconfigure(0, weight=1)
        self._build_week_panel(panel, tuan)
        self._refresh_selection_list()
        self._refresh_status()
        try:
            self.grid_canvas.yview_moveto(0)
        except Exception:
            pass

    def _build_week_panel(self, parent, tuan: int):
        for col in range(9):
            parent.columnconfigure(col, weight=(1 if col >= 2 else 0))
        tk.Label(parent, text="Buổi", width=7, background=CLR_GRID_HEADER_BG).grid(
            row=0, column=0, sticky="nsew",
        )
        tk.Label(parent, text="Tiết", width=5, background=CLR_GRID_HEADER_BG).grid(
            row=0, column=1, sticky="nsew",
        )
        for col, (thu, label) in enumerate(DAYS, 2):
            tk.Label(
                parent, text=label, background=CLR_GRID_HEADER_BG,
                foreground=("#b00020" if thu == 8 else CLR_GRID_HEADER_FG),
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=col, sticky="nsew")

        view_buoi = self._safe_int(self.var_view_buoi.get(), self.source_slot.buoi)
        groups = [
            (buoi_label, buoi, tiets)
            for buoi_label, buoi, tiets in TIETS_BY_BUOI
            if view_buoi == 0 or buoi == view_buoi
        ]
        if not groups:
            groups = TIETS_BY_BUOI
        row = 1
        for buoi_label, buoi, tiets in groups:
            for pos, tiet in enumerate(tiets):
                if pos == 0:
                    tk.Label(
                        parent, text=buoi_label, background=("#ffefd5" if buoi == 1 else "#dceefb"),
                        font=("Segoe UI", 9, "bold"),
                    ).grid(row=row, column=0, rowspan=len(tiets), sticky="nsew")
                tk.Label(parent, text=str(tiet), background="#f4f7fb").grid(
                    row=row, column=1, sticky="nsew",
                )
                for col, (thu, _label) in enumerate(DAYS, 2):
                    self._make_event_cell(parent, tuan, thu, buoi, tiet, row, col)
                row += 1

    def _make_event_cell(self, parent, tuan: int, thu: int, buoi: int, tiet: int, row: int, col: int):
        pos = (tuan, thu, buoi, tiet)
        slot = self._target_slot(tuan, thu, buoi, tiet)
        can_select = self._can_select(tuan, thu, buoi, tiet)
        text = self._short_text(slot)
        if self.var_kind.get() == TKB_EVENT_NGHI and not can_select:
            text = "Khác\nmôn" if slot else ""
        btn = tk.Button(
            parent,
            text=text,
            width=14,
            height=2,
            wraplength=self.CELL_W - 8,
            justify="center",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 8),
            command=lambda p=pos: self._toggle_cell(p),
        )
        btn.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
        btn.bind("<MouseWheel>", self._on_grid_mousewheel)
        self._cell_buttons[pos] = btn
        if not can_select:
            btn.configure(state="disabled")
        self._restyle_event_cell(pos)

    def _restyle_event_cell(self, pos: tuple[int, int, int, int]):
        btn = self._cell_buttons.get(pos)
        if btn is None:
            return
        tuan, thu, buoi, tiet = pos
        slot = self._target_slot(tuan, thu, buoi, tiet)
        if pos in self._selected:
            bg = "#ffe08a"
            fg = "#1d2733"
        elif self.var_kind.get() == TKB_EVENT_NGHI and not self._can_select(tuan, thu, buoi, tiet):
            bg = "#edf0f4"
            fg = "#98a1ad"
        elif slot and slot.group_key == self.source_slot.group_key:
            bg = "#d9f2e6"
            fg = "#173d2a"
        elif slot:
            bg = "#ffe4d6"
            fg = "#6b2d16"
        else:
            bg = "#ffffff"
            fg = "#6b7280"
        btn.configure(background=bg, activebackground=bg, foreground=fg)

    def _toggle_cell(self, pos: tuple[int, int, int, int]):
        if pos in self._selected:
            self._selected.remove(pos)
        else:
            self._selected.add(pos)
        self._restyle_event_cell(pos)
        self._refresh_selection_list()
        self._refresh_status()

    def _select_source_position_current_week(self):
        pos = (
            self._visible_week(),
            self.source_slot.thu,
            self.source_slot.buoi,
            self.source_slot.tiet,
        )
        if self._safe_int(self.var_view_buoi.get(), 0) not in (0, self.source_slot.buoi):
            self.var_view_buoi.set(self.source_slot.buoi)
            self._rebuild_week_grid()
        if self._can_select(*pos):
            self._selected.add(pos)
            if pos in self._cell_buttons:
                self._restyle_event_cell(pos)
            self._refresh_selection_list()
            self._refresh_status()
        else:
            messagebox.showinfo(
                "Không chọn được ô này",
                "Ở tuần đang xem, ô cùng vị trí không đúng môn nguồn nên không thể đánh dấu Nghỉ.",
                parent=self,
            )

    def _select_source_position(self):
        try:
            tuan_from = int(self.var_from.get())
            tuan_to = int(self.var_to.get())
        except (tk.TclError, TypeError, ValueError):
            return
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        added = 0
        for tuan in range(max(1, tuan_from), min(52, tuan_to) + 1):
            pos = (tuan, self.source_slot.thu, self.source_slot.buoi, self.source_slot.tiet)
            if self._can_select(*pos):
                self._selected.add(pos)
                added += 1
        for pos in list(self._cell_buttons):
            self._restyle_event_cell(pos)
        self._refresh_selection_list()
        self._refresh_status()
        if added:
            self.var_status.set(
                f"Đã chọn cùng vị trí nguồn cho {added} tuần trong dải. "
                "Xem danh sách bên phải trước khi lưu."
            )

    def _clear_selection(self):
        self._selected.clear()
        for pos in list(self._cell_buttons):
            self._restyle_event_cell(pos)
        self._refresh_selection_list()
        self._refresh_status()

    def _refresh_status(self):
        kind = self.var_kind.get()
        if kind == TKB_EVENT_NGHI:
            msg = (
                f"Tuần {self._visible_week()} - NGHỈ: bấm ô xanh đúng môn nguồn. "
                f"Đã chọn {len(self._selected)} tiết."
            )
        else:
            msg = (
                f"Tuần {self._visible_week()} - DẠY BÙ: bấm ô trống hoặc ô cam nếu đã bật dùng ô đang có tiết. "
                f"Đã chọn {len(self._selected)} tiết."
            )
        self.var_status.set(msg)

    def _refresh_selection_list(self):
        tree = getattr(self, "tree_selected", None)
        if tree is None:
            return
        for iid in tree.get_children():
            tree.delete(iid)
        kind_label = TKB_EVENT_LABELS.get(self.var_kind.get(), "Sự kiện")
        for pos in sorted(self._selected):
            iid = "sel_" + "_".join(str(x) for x in pos)
            tree.insert(
                "", "end", iid=iid,
                values=(pos[0], self._pos_label(pos[1], pos[2], pos[3])),
            )
        summary = getattr(self, "var_selected_summary", None)
        if summary is not None:
            if self._selected:
                summary.set(f"{kind_label}: đã chọn {len(self._selected)} tiết")
            else:
                summary.set("Chưa chọn tiết nào.")

    def _remove_selected_rows(self):
        tree = getattr(self, "tree_selected", None)
        if tree is None:
            return
        for iid in tree.selection():
            raw = str(iid).replace("sel_", "")
            try:
                pos = tuple(int(x) for x in raw.split("_"))
            except (TypeError, ValueError):
                continue
            if len(pos) == 4 and pos in self._selected:
                self._selected.remove(pos)
                self._restyle_event_cell(pos)
        self._refresh_selection_list()
        self._refresh_status()

    def _on_save(self):
        if not self._selected:
            messagebox.showinfo("Chưa chọn tiết", "Hãy chọn ít nhất một ô trong bảng.", parent=self)
            return
        kind = self.var_kind.get()
        note = self.var_note.get().strip()
        existing = {
            e.position_tuple(): e
            for e in profile_schedule_events(self.profile)
            if e.enabled
        }
        replace = bool(self.var_replace.get())
        occupied_without_replace = [
            pos for pos in self._selected
            if kind == TKB_EVENT_DAY_BU
            and self._target_slot(*pos) is not None
            and not replace
        ]
        if occupied_without_replace:
            if not messagebox.askyesno(
                "Ô đang có tiết thường",
                (
                    f"Có {len(occupied_without_replace)} ô đang có tiết TKB thường.\n\n"
                    "Nếu tiếp tục, tool sẽ dùng các ô đó cho Dạy bù trong lần nhập "
                    "để tránh trùng dữ liệu trên VnEdu."
                ),
                parent=self,
            ):
                return
            replace = True
            self.var_replace.set(True)

        events: list[TKBScheduleEvent] = []
        for pos in sorted(self._selected):
            if pos in existing:
                old = existing[pos]
                messagebox.showerror(
                    "Bị trùng vị trí",
                    (
                        f"Tuần {pos[0]}, {self._pos_label(pos[1], pos[2], pos[3])} "
                        f"đã có {old.label}. Hãy bỏ chọn ô này trước khi lưu."
                    ),
                    parent=self,
                )
                return
            if kind == TKB_EVENT_NGHI and not self._can_select(*pos):
                messagebox.showerror(
                    "Ô không đúng môn",
                    "Tiết Nghỉ chỉ được chọn ở ô TKB có cùng lớp/môn/phân môn nguồn.",
                    parent=self,
                )
                return
            events.append(TKBScheduleEvent.from_slot(
                kind=kind,
                source_slot=self.source_slot,
                tuan=pos[0],
                thu=pos[1],
                buoi=pos[2],
                tiet=pos[3],
                note=note,
                enabled=True,
                replace_normal_slot=(replace if kind == TKB_EVENT_DAY_BU else False),
            ))
        self.result = events
        self.destroy()


class TKBScheduleEventsManagerDialog(tk.Toplevel):
    """Xem và dọn danh sách Nghỉ/Dạy bù đang lưu trong hồ sơ."""

    def __init__(self, parent: "KHDHWizard"):
        super().__init__(parent.winfo_toplevel())
        self.wizard = parent
        self.profile = parent.profile
        self.result: list[TKBScheduleEvent] | None = None
        self.events: list[TKBScheduleEvent] = [
            e.clone() for e in profile_schedule_events(self.profile)
        ]

        self.title("Quản lý Nghỉ / Dạy bù")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.resizable(True, True)
        apply_wizard_styles(ttk.Style(self))
        self.geometry("1080x620")
        self.minsize(900, 520)

        self.var_summary = tk.StringVar(value="")
        self._build_ui()
        self._refresh_tree()
        self._refresh_summary()
        self._center_on_parent()
        self.bind("<Escape>", lambda _e: self.destroy())

    @staticmethod
    def _thu_label(thu: int) -> str:
        return _THU_TO_TEXT.get(int(thu), str(thu))

    @staticmethod
    def _buoi_label(buoi: int) -> str:
        return _BUOI_TO_TEXT.get(int(buoi), str(buoi))

    def _build_ui(self):
        body = ttk.Frame(self, padding=14, style="Wiz.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(
            body,
            text=(
                "Danh sách dưới đây được tạo từ lưới TKB. "
                "Muốn thêm tiết mới: đóng cửa sổ này, chuột phải hoặc giữ Alt "
                "rồi bấm vào ô TKB đã có môn."
            ),
            style="WizHint.TLabel",
            wraplength=920,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        actions = ttk.Frame(body, style="Wiz.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(
            actions, text="Tắt/Bật dòng chọn",
            command=self._on_toggle_selected,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="Xóa dòng chọn",
            command=self._on_delete_selected,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="Xóa tất cả",
            command=self._on_delete_all,
            style="WizDanger.TButton",
        ).pack(side="left")
        ttk.Label(
            actions, textvariable=self.var_summary,
            style="WizAccent.TLabel",
        ).pack(side="right")

        table = ttk.Frame(body, style="Wiz.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree_events = ttk.Treeview(
            table,
            columns=("bat", "loai", "vitri", "nguon", "ghide", "ghichu"),
            show="headings",
            style="Wiz.Treeview",
            height=14,
        )
        for col, label, width, anchor in [
            ("bat", "Bật", 56, "center"),
            ("loai", "Loại", 88, "center"),
            ("vitri", "Tuần / thời điểm", 230, "w"),
            ("nguon", "Lớp / môn / phân môn", 300, "w"),
            ("ghide", "Dùng ô TKB", 92, "center"),
            ("ghichu", "Ghi chú", 260, "w"),
        ]:
            self.tree_events.heading(col, text=label)
            self.tree_events.column(col, width=width, anchor=anchor, stretch=True)
        self.tree_events.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree_events.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_events.configure(yscrollcommand=sb.set)
        self.tree_events.tag_configure("disabled", foreground="#888")
        self.tree_events.tag_configure("error", foreground="#b00020")

        footer = ttk.Frame(body, style="Wiz.TFrame")
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(
            footer, text="Đóng", command=self.destroy,
            style="WizSubtle.TButton",
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            footer, text="Lưu thay đổi", command=self._on_save,
            style="WizSuccess.TButton",
        ).pack(side="right")

    def _center_on_parent(self):
        self.update_idletasks()
        try:
            top = self.wizard.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() // 2) - (self.winfo_width() // 2)
            y = top.winfo_rooty() + (top.winfo_height() // 2) - (self.winfo_height() // 2)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _event_position_text(self, event: TKBScheduleEvent) -> str:
        return (
            f"Tuần {event.tuan}, {self._thu_label(event.thu)}, "
            f"{self._buoi_label(event.buoi)}, tiết {event.tiet}"
        )

    @staticmethod
    def _event_source_text(event: TKBScheduleEvent) -> str:
        return (
            f"{event.source_lop_text} / {event.source_mon_text} / "
            f"{event.source_phan_mon_text}"
        )

    def _refresh_summary(self):
        n_on = sum(1 for e in self.events if e.enabled)
        n_nghi = sum(1 for e in self.events if e.enabled and e.kind == TKB_EVENT_NGHI)
        n_bu = sum(1 for e in self.events if e.enabled and e.kind == TKB_EVENT_DAY_BU)
        self.var_summary.set(
            f"{n_on} dòng đang bật: {n_nghi} Nghỉ, {n_bu} Dạy bù"
        )

    def _refresh_tree(self):
        for child in self.tree_events.get_children():
            self.tree_events.delete(child)
        if not self.events:
            self.tree_events.insert(
                "", "end", iid="__empty__",
                values=("—", "Chưa có", "Chuột phải/Alt trên ô TKB để thêm", "", "", ""),
                tags=("disabled",),
            )
            return
        seen: set[tuple[int, int, int, int]] = set()
        for idx, event in enumerate(self.events):
            tags = []
            if not event.enabled:
                tags.append("disabled")
            pos = event.position_tuple()
            if pos in seen:
                tags.append("error")
            seen.add(pos)
            self.tree_events.insert(
                "", "end", iid=str(idx),
                values=(
                    "Có" if event.enabled else "Không",
                    event.label,
                    self._event_position_text(event),
                    self._event_source_text(event),
                    "Có" if event.replace_normal_slot else "Không",
                    event.note,
                ),
                tags=tuple(tags),
            )

    def _selected_indexes(self) -> list[int]:
        out: list[int] = []
        for iid in self.tree_events.selection():
            if iid == "__empty__":
                continue
            try:
                idx = int(iid)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(self.events):
                out.append(idx)
        return sorted(set(out))

    def _on_toggle_selected(self):
        for idx in self._selected_indexes():
            self.events[idx].enabled = not self.events[idx].enabled
        self._refresh_tree()
        self._refresh_summary()

    def _on_delete_selected(self):
        indexes = self._selected_indexes()
        if not indexes:
            return
        for idx in sorted(indexes, reverse=True):
            del self.events[idx]
        self._refresh_tree()
        self._refresh_summary()

    def _on_delete_all(self):
        if not self.events:
            return
        if not messagebox.askyesno(
            "Xóa tất cả?",
            "Bạn muốn xóa toàn bộ danh sách Nghỉ/Dạy bù trong hồ sơ này?",
            parent=self,
        ):
            return
        self.events.clear()
        self._refresh_tree()
        self._refresh_summary()

    def _on_save(self):
        old_events = getattr(self.profile, "schedule_events", [])
        self.profile.schedule_events = [e.clone() for e in self.events]
        try:
            issues = validate_profile_schedule_events(self.profile)
        finally:
            self.profile.schedule_events = old_events
        hard_errors = [msg for level, msg in issues if level == "error"]
        if hard_errors:
            messagebox.showerror("Còn lỗi", hard_errors[0], parent=self)
            return
        self.result = [e.clone() for e in self.events]
        self.destroy()

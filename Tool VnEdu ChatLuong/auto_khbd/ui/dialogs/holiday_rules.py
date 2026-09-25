"""Hộp thoại quy tắc Nghỉ / Dạy bù trong TKB chính."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ...engine.profile.excel_io import _BUOI_TO_TEXT, _THU_TO_TEXT
from ...engine.profile.models import SlotEntry, TKBHolidayRule
from ...engine.profile.profile import (
    _legacy_holiday_rules_to_events,
    validate_profile_holiday_rules,
)
from ..styles import apply_wizard_styles
from ..theme import CLR_PANEL_BG

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..wizard.wizard import KHDHWizard


class TKBHolidayRulesDialog(tk.Toplevel):
    """Quản lý quy tắc Nghỉ/Dạy bù cấp hồ sơ TKB."""

    def __init__(self, parent: "KHDHWizard"):
        super().__init__(parent.winfo_toplevel())
        self.wizard = parent
        self.profile = parent.profile
        self.result: list[TKBHolidayRule] | None = None
        self.rules: list[TKBHolidayRule] = [
            r.clone() for r in getattr(self.profile, "holiday_rules", [])
        ]

        self.title("📅 Nghỉ / dạy bù theo TKB")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.resizable(True, True)
        apply_wizard_styles(ttk.Style(self))

        default_from = self._safe_int(parent.var_tuan_from.get(), 1)
        default_to = self._safe_int(parent.var_tuan_to.get(), default_from)
        self.var_holiday_tuan = tk.IntVar(value=default_from)
        self.var_holiday_thu = tk.IntVar(value=2)
        self.var_holiday_buoi = tk.StringVar(value="0 - Cả ngày")
        self.var_holiday_tiet = tk.IntVar(value=1)
        self.var_has_makeup = tk.BooleanVar(value=True)
        self.var_makeup_tuan = tk.IntVar(value=max(default_from, default_to))
        self.var_makeup_thu = tk.IntVar(value=2)
        self.var_makeup_buoi = tk.StringVar(value="1 - Sáng")
        self.var_makeup_tiet = tk.IntVar(value=1)
        self.var_note = tk.StringVar(value="Nghỉ theo kế hoạch")
        self.var_rule_summary = tk.StringVar(value="")

        self._build_ui()
        self._refresh_tree()
        self._refresh_summary()
        self._center_on_parent()
        self.bind("<Escape>", lambda _e: self.destroy())

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (tk.TclError, TypeError, ValueError):
            return default

    @staticmethod
    def _combo_int(value: str) -> int:
        return int(str(value).split("-", 1)[0].strip())

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
        body.rowconfigure(3, weight=1)

        ttk.Label(
            body,
            text=(
                "Khai báo ngày nghỉ ngay tại bước soạn TKB. "
                "Tiết Nghỉ giữ nguyên PPCT nhưng không tăng số; "
                "tiết Dạy bù mới làm PPCT tăng tiếp."
            ),
            style="WizHint.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 10))

        fields = ttk.Frame(body, style="Wiz.TFrame")
        fields.grid(row=1, column=0, sticky="ew")
        fields.columnconfigure(0, weight=1)
        fields.columnconfigure(1, weight=1)
        self._build_holiday_fields(fields)
        self._build_makeup_fields(fields)

        actions = ttk.Frame(body, style="Wiz.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        ttk.Button(
            actions, text="Thêm 1 tiết", command=self._on_add_one,
            style="WizPrimary.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="Thêm cả ngày/buổi từ TKB",
            command=self._on_add_from_tkb,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="Xóa dòng chọn", command=self._on_delete_selected,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="Xóa tất cả", command=self._on_delete_all,
            style="WizSubtle.TButton",
        ).pack(side="left")
        ttk.Label(
            actions, textvariable=self.var_rule_summary,
            style="WizAccent.TLabel",
        ).pack(side="right")

        table = ttk.Frame(body, style="Wiz.TFrame")
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree_rules = ttk.Treeview(
            table,
            columns=("nghi", "bai", "bu", "ghichu"),
            show="headings",
            style="Wiz.Treeview",
            height=10,
        )
        for col, label, width, anchor in [
            ("nghi", "Tiết Nghỉ", 185, "w"),
            ("bai", "Lớp / phân môn", 250, "w"),
            ("bu", "Tiết Dạy bù", 210, "w"),
            ("ghichu", "Ghi chú", 260, "w"),
        ]:
            self.tree_rules.heading(col, text=label)
            self.tree_rules.column(col, width=width, anchor=anchor, stretch=True)
        self.tree_rules.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree_rules.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_rules.configure(yscrollcommand=sb.set)

        footer = ttk.Frame(body, style="Wiz.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(
            footer, text="Hủy", command=self.destroy,
            style="WizSubtle.TButton",
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            footer, text="Lưu quy tắc", command=self._on_save,
            style="WizSuccess.TButton",
        ).pack(side="right")
        self.geometry("980x610")

    def _build_holiday_fields(self, parent):
        frm = ttk.LabelFrame(
            parent, text=" Tiết nghỉ ", padding=10,
            style="Wiz.TLabelframe",
        )
        frm.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(frm, text="Tuần:", style="Wiz.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(frm, from_=1, to=52, width=6,
                    textvariable=self.var_holiday_tuan).grid(row=0, column=1, sticky="w", padx=(4, 12))
        ttk.Label(frm, text="Thứ:", style="Wiz.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(frm, from_=2, to=8, width=6,
                    textvariable=self.var_holiday_thu).grid(row=0, column=3, sticky="w", padx=(4, 0))
        ttk.Label(frm, text="Buổi:", style="Wiz.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            frm, textvariable=self.var_holiday_buoi,
            values=("0 - Cả ngày", "1 - Sáng", "2 - Chiều"),
            state="readonly", width=13,
        ).grid(row=1, column=1, sticky="w", padx=(4, 12), pady=(8, 0))
        ttk.Label(frm, text="Tiết:", style="Wiz.TLabel").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Spinbox(frm, from_=1, to=5, width=6,
                    textvariable=self.var_holiday_tiet).grid(row=1, column=3, sticky="w", padx=(4, 0), pady=(8, 0))
        ttk.Label(
            frm,
            text="Chọn 'Cả ngày' rồi bấm 'Thêm cả ngày/buổi từ TKB' để tự lấy mọi tiết trong ngày đó.",
            style="WizHint.TLabel",
            wraplength=390,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _build_makeup_fields(self, parent):
        frm = ttk.LabelFrame(
            parent, text=" Tiết dạy bù ", padding=10,
            style="Wiz.TLabelframe",
        )
        frm.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Checkbutton(
            frm, text="Có dạy bù cho tiết nghỉ này",
            variable=self.var_has_makeup,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frm, text="Tuần:", style="Wiz.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(frm, from_=1, to=52, width=6,
                    textvariable=self.var_makeup_tuan).grid(row=1, column=1, sticky="w", padx=(4, 12), pady=(8, 0))
        ttk.Label(frm, text="Thứ:", style="Wiz.TLabel").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Spinbox(frm, from_=2, to=8, width=6,
                    textvariable=self.var_makeup_thu).grid(row=1, column=3, sticky="w", padx=(4, 0), pady=(8, 0))
        ttk.Label(frm, text="Buổi:", style="Wiz.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            frm, textvariable=self.var_makeup_buoi,
            values=("1 - Sáng", "2 - Chiều"),
            state="readonly", width=13,
        ).grid(row=2, column=1, sticky="w", padx=(4, 12), pady=(8, 0))
        ttk.Label(frm, text="Tiết:", style="Wiz.TLabel").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Spinbox(frm, from_=1, to=5, width=6,
                    textvariable=self.var_makeup_tiet).grid(row=2, column=3, sticky="w", padx=(4, 0), pady=(8, 0))
        ttk.Label(frm, text="Ghi chú:", style="Wiz.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_note, width=42).grid(
            row=3, column=1, columnspan=3, sticky="ew", padx=(4, 0), pady=(8, 0)
        )
        ttk.Label(
            frm,
            text="Khi thêm cả ngày/buổi, chỉ cần chọn Tuần bù; tool sẽ dùng đúng Thứ/Buổi/Tiết tương ứng.",
            style="WizHint.TLabel",
            wraplength=420,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _center_on_parent(self):
        self.update_idletasks()
        try:
            top = self.wizard.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() // 2) - (self.winfo_width() // 2)
            y = top.winfo_rooty() + (top.winfo_height() // 2) - (self.winfo_height() // 2)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _refresh_summary(self):
        n_nghi = sum(1 for r in self.rules if r.enabled)
        n_bu = sum(1 for r in self.rules if r.enabled and r.has_makeup)
        self.var_rule_summary.set(f"{n_nghi} tiết Nghỉ • {n_bu} tiết Dạy bù")

    def _refresh_tree(self):
        for child in self.tree_rules.get_children():
            self.tree_rules.delete(child)
        if not self.rules:
            self.tree_rules.insert(
                "", "end", iid="__empty__",
                values=("Chưa khai báo", "", "", ""),
                tags=("empty",),
            )
            self.tree_rules.tag_configure("empty", foreground="#888")
            return
        for idx, rule in enumerate(self.rules):
            self.tree_rules.insert(
                "", "end", iid=str(idx),
                values=(
                    self._format_holiday(rule),
                    self._source_slot_label(rule),
                    self._format_makeup(rule),
                    rule.note,
                ),
            )

    def _format_holiday(self, rule: TKBHolidayRule) -> str:
        return (
            f"Tuần {rule.holiday_tuan}, {self._thu_label(rule.holiday_thu)}, "
            f"{self._buoi_label(rule.holiday_buoi)}, tiết {rule.holiday_tiet}"
        )

    def _format_makeup(self, rule: TKBHolidayRule) -> str:
        if not rule.has_makeup:
            return "Không khai báo dạy bù"
        return (
            f"Tuần {rule.makeup_tuan}, {self._thu_label(rule.makeup_thu)}, "
            f"{self._buoi_label(rule.makeup_buoi)}, tiết {rule.makeup_tiet}"
        )

    def _source_slot_label(self, rule: TKBHolidayRule) -> str:
        slot = self._source_slot_for_rule(rule)
        if slot is None:
            return "(không thấy ô TKB)"
        return f"{slot.lop_text} / {slot.phan_mon_text or slot.mon_text}"

    def _source_slot_for_rule(self, rule: TKBHolidayRule) -> SlotEntry | None:
        try:
            template = self.profile.get_active_template(rule.holiday_tuan)
            return template.slot_at(
                rule.holiday_thu, rule.holiday_buoi, rule.holiday_tiet
            )
        except Exception:
            return None

    def _make_rule_from_vars(self) -> TKBHolidayRule:
        holiday_buoi = self._combo_int(self.var_holiday_buoi.get())
        if holiday_buoi == 0:
            raise ValueError(
                "Khi thêm 1 tiết, hãy chọn Sáng hoặc Chiều. "
                "Nếu muốn lấy cả ngày, dùng nút 'Thêm cả ngày/buổi từ TKB'."
            )
        return self._make_rule(
            holiday_tuan=int(self.var_holiday_tuan.get()),
            holiday_thu=int(self.var_holiday_thu.get()),
            holiday_buoi=holiday_buoi,
            holiday_tiet=int(self.var_holiday_tiet.get()),
            use_makeup=bool(self.var_has_makeup.get()),
        )

    def _make_rule(
        self,
        *,
        holiday_tuan: int,
        holiday_thu: int,
        holiday_buoi: int,
        holiday_tiet: int,
        use_makeup: bool,
        makeup_same_position: bool = False,
    ) -> TKBHolidayRule:
        if use_makeup:
            if makeup_same_position:
                makeup_thu = holiday_thu
                makeup_buoi = holiday_buoi
                makeup_tiet = holiday_tiet
            else:
                makeup_thu = int(self.var_makeup_thu.get())
                makeup_buoi = self._combo_int(self.var_makeup_buoi.get())
                makeup_tiet = int(self.var_makeup_tiet.get())
            makeup_tuan = int(self.var_makeup_tuan.get())
        else:
            makeup_tuan = makeup_thu = makeup_buoi = makeup_tiet = 0
        return TKBHolidayRule(
            holiday_tuan=holiday_tuan,
            holiday_thu=holiday_thu,
            holiday_buoi=holiday_buoi,
            holiday_tiet=holiday_tiet,
            makeup_tuan=makeup_tuan,
            makeup_thu=makeup_thu,
            makeup_buoi=makeup_buoi,
            makeup_tiet=makeup_tiet,
            note=self.var_note.get().strip(),
            enabled=True,
        )

    def _validate_rule(
        self,
        rule: TKBHolidayRule,
        *,
        ask_conflict: bool,
        silent: bool = False,
    ) -> bool:
        if self._source_slot_for_rule(rule) is None:
            if not silent:
                messagebox.showerror(
                    "Không thấy tiết trong TKB",
                    "Tiết nghỉ vừa chọn không có trong mẫu TKB của tuần đó.\n"
                    "Hãy kiểm tra lại tuần lẻ/chẵn, thứ, buổi và tiết.",
                    parent=self,
                )
            return False

        for existing in self.rules:
            if existing.holiday_position_tuple() == rule.holiday_position_tuple():
                if not silent:
                    messagebox.showerror(
                        "Bị trùng tiết nghỉ",
                        "Tiết nghỉ này đã có trong danh sách.",
                        parent=self,
                    )
                return False
            if (
                rule.has_makeup and existing.has_makeup
                and existing.makeup_position_tuple() == rule.makeup_position_tuple()
            ):
                if not silent:
                    messagebox.showerror(
                        "Bị trùng vị trí dạy bù",
                        "Mỗi thứ/buổi/tiết chỉ nhập được một tiết. "
                        "Hãy chọn vị trí dạy bù khác.",
                        parent=self,
                    )
                return False

        if rule.has_makeup:
            dst_key = rule.makeup_position_tuple()
            if dst_key is not None and dst_key <= rule.holiday_position_tuple():
                if not silent:
                    messagebox.showerror(
                        "Thứ tự chưa đúng",
                        "Tiết dạy bù phải nằm sau tiết nghỉ để PPCT chạy đúng thứ tự.",
                        parent=self,
                    )
                return False
            if dst_key in [r.holiday_position_tuple() for r in self.rules]:
                if not silent:
                    messagebox.showerror(
                        "Vị trí bù đang là tiết nghỉ",
                        "Không thể đặt dạy bù vào đúng một ô đang khai báo Nghỉ.",
                        parent=self,
                    )
                return False
            try:
                target_template = self.profile.get_active_template(rule.makeup_tuan)
                target_slot = target_template.slot_at(
                    rule.makeup_thu, rule.makeup_buoi, rule.makeup_tiet
                )
            except Exception:
                target_slot = None
            if target_slot is not None and ask_conflict:
                if not messagebox.askyesno(
                    "Vị trí bù đang có tiết TKB",
                    (
                        "Vị trí dạy bù bạn chọn đang có một tiết trong TKB.\n\n"
                        "Khi chạy, tool sẽ nhập tiết Dạy bù tại vị trí đó "
                        "và không nhập tiết thường ở cùng ô để tránh trùng dữ liệu.\n\n"
                        "Bạn vẫn muốn dùng vị trí này?"
                    ),
                    parent=self,
                ):
                    return False
        return True

    def _on_add_one(self):
        try:
            rule = self._make_rule_from_vars()
        except Exception as e:
            messagebox.showerror("Sai dữ liệu", str(e), parent=self)
            return
        if not self._validate_rule(rule, ask_conflict=True):
            return
        self.rules.append(rule)
        self._refresh_tree()
        self._refresh_summary()

    def _on_add_from_tkb(self):
        try:
            tuan = int(self.var_holiday_tuan.get())
            thu = int(self.var_holiday_thu.get())
            buoi_filter = self._combo_int(self.var_holiday_buoi.get())
            template = self.profile.get_active_template(tuan)
        except Exception as e:
            messagebox.showerror("Sai dữ liệu", str(e), parent=self)
            return

        slots = [
            s for s in sorted(template.slots, key=lambda x: (x.thu, x.buoi, x.tiet))
            if s.thu == thu and (buoi_filter == 0 or s.buoi == buoi_filter)
        ]
        if not slots:
            messagebox.showinfo(
                "Không có tiết",
                "Không tìm thấy tiết nào trong TKB ở ngày/buổi đã chọn.",
                parent=self,
            )
            return

        use_makeup = bool(self.var_has_makeup.get())
        candidate_rules: list[TKBHolidayRule] = []
        try:
            for slot in slots:
                candidate_rules.append(self._make_rule(
                    holiday_tuan=tuan,
                    holiday_thu=slot.thu,
                    holiday_buoi=slot.buoi,
                    holiday_tiet=slot.tiet,
                    use_makeup=use_makeup,
                    makeup_same_position=True,
                ))
        except Exception as e:
            messagebox.showerror("Sai dữ liệu", str(e), parent=self)
            return

        if use_makeup:
            occupied = 0
            try:
                makeup_template = self.profile.get_active_template(
                    int(self.var_makeup_tuan.get())
                )
                for rule in candidate_rules:
                    if makeup_template.slot_at(
                        rule.makeup_thu, rule.makeup_buoi, rule.makeup_tiet
                    ) is not None:
                        occupied += 1
            except Exception:
                occupied = 0
            if occupied and not messagebox.askyesno(
                "Một số vị trí bù đang có tiết TKB",
                (
                    f"Có {occupied} vị trí dạy bù đang trùng với ô TKB thường.\n\n"
                    "Tool sẽ dùng các ô đó cho tiết Dạy bù để tránh trùng cùng vị trí. "
                    "Tiếp tục thêm?"
                ),
                parent=self,
            ):
                return

        added = 0
        skipped = 0
        for rule in candidate_rules:
            if self._validate_rule(rule, ask_conflict=False, silent=True):
                self.rules.append(rule)
                added += 1
            else:
                skipped += 1
        self._refresh_tree()
        self._refresh_summary()
        messagebox.showinfo(
            "Đã thêm",
            f"Đã thêm {added} tiết nghỉ/dạy bù."
            + (f"\nBỏ qua {skipped} tiết do trùng hoặc sai vị trí." if skipped else ""),
            parent=self,
        )

    def _on_delete_selected(self):
        selected = [iid for iid in self.tree_rules.selection() if iid != "__empty__"]
        if not selected:
            return
        for idx in sorted((int(iid) for iid in selected), reverse=True):
            if 0 <= idx < len(self.rules):
                del self.rules[idx]
        self._refresh_tree()
        self._refresh_summary()

    def _on_delete_all(self):
        if not self.rules:
            return
        if not messagebox.askyesno(
            "Xóa tất cả?",
            "Bạn muốn xóa toàn bộ quy tắc Nghỉ/Dạy bù đang khai báo?",
            parent=self,
        ):
            return
        self.rules.clear()
        self._refresh_tree()
        self._refresh_summary()

    def _on_save(self):
        old_rules = getattr(self.profile, "holiday_rules", [])
        old_events = getattr(self.profile, "schedule_events", [])
        self.profile.holiday_rules = [r.clone() for r in self.rules]
        try:
            self.profile.schedule_events = _legacy_holiday_rules_to_events(
                self.profile, self.profile.holiday_rules, skip_invalid=True
            )
            issues = validate_profile_holiday_rules(self.profile)
        finally:
            self.profile.holiday_rules = old_rules
            self.profile.schedule_events = old_events
        hard_errors = [msg for level, msg in issues if level == "error"]
        if hard_errors:
            messagebox.showerror("Còn lỗi", hard_errors[0], parent=self)
            return
        self.result = [r.clone() for r in self.rules]
        self.destroy()

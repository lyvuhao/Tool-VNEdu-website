"""Tra cứu ô và vẽ lại trạng thái ô."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..theme import (
    CLR_BORDER,
    CLR_SLOT_DRAG_TARGET_BG,
    CLR_SLOT_EMPTY,
    CLR_SLOT_FILLED,
    CLR_SLOT_FILLED_TEXT,
    CLR_SLOT_SELECTED_BG,
    CLR_SLOT_SELECTED_BORDER,
)

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.profile.models import SlotEntry, TKBTemplate


class RenderMixin:
    """Tra cứu ô và vẽ lại trạng thái ô."""

    # -----------------------------------------------------------
    # Lookups + helpers
    # -----------------------------------------------------------

    def _template_for_tag(self, tag: str) -> "TKBTemplate | None":
        prof = self.wizard.profile
        if prof is None:
            return None
        if tag == "le":
            return prof.template_le
        if tag == "chan":
            return prof.template_chan
        if tag == "chinh":
            return prof.template_chinh
        return prof.get_active_template(1)

    def _slot_at_panel(self, panel_idx: int, key: str) -> "SlotEntry | None":
        if not (0 <= panel_idx < len(self._panels)):
            return None
        tag = self._panels[panel_idx]["tag"]
        tpl = self._template_for_tag(tag)
        if tpl is None:
            return None
        try:
            t, b, ti = (int(x) for x in key.split("_"))
        except (ValueError, TypeError):
            return None
        return tpl.slot_at(t, b, ti)

    def _find_panel_button(self, widget) -> tuple[int, str] | None:
        """Reverse lookup widget → (panel_idx, key) via O(1) dict cache.

        Cache `_widget_to_cell` được build khi `_build_grid_in` chạy.
        Mỗi cell có 4 widget (cell Frame + 3 Labels) → 4 entries trong dict.
        Tổng ~560 entries cho 2 panels × 70 cells × 4 widgets — O(1) lookup
        thay vì O(n×m) linear scan trên mỗi mouse move event.
        """
        if widget is None:
            return None
        return self._widget_to_cell.get(id(widget))

    # -----------------------------------------------------------
    # Refresh / restyle
    # -----------------------------------------------------------

    def _restyle_button(self, panel_idx: int, key: str):
        """Apply style cho 1 cell — phù hợp filled / empty / selected /
        drag-target. Cell là dict 4 widget (cell, lbl_lop, lbl_mon, lbl_pm)
        cho phép màu chữ tách riêng cho lớp (đỏ đậm) và môn/pm.
        """
        if not (0 <= panel_idx < len(self._panels)):
            return
        panel = self._panels[panel_idx]
        cell_dict = panel["buttons"].get(key)
        if cell_dict is None:
            return
        cell = cell_dict["cell"]
        lbl_lop = cell_dict["lbl_lop"]
        lbl_mon = cell_dict["lbl_mon"]
        lbl_pm = cell_dict["lbl_pm"]

        slot = self._slot_at_panel(panel_idx, key)
        is_selected = (
            self._selected_panel_idx == panel_idx
            and self._selected_key == key
        )
        is_drag_target = False
        ds = self._drag_state
        if ds is not None:
            target = ds.get("last_target")
            source = (ds.get("source_panel_idx"), ds.get("source_key"))
            cur = (panel_idx, key)
            if target == cur and target != source:
                is_drag_target = True

        # Tính cell width hiện tại để wraplength đúng. Sizes lưu trong
        # panel để tránh winfo_width (chưa rendered có thể trả 1).
        col_w, row_h = panel["sizes"]
        wrap_w = max(col_w - 8, 40)

        if slot:
            mon_short = self._abbreviate_mon(slot.mon_text or "")
            pm_short = self._abbreviate_phan_mon(slot.phan_mon_text or "")
            # Ngắn hơn nếu vẫn dài quá so với cell width
            if len(mon_short) > 18:
                mon_short = mon_short[:16] + "…"
            if len(pm_short) > 20:
                pm_short = pm_short[:18] + "…"

            if is_drag_target:
                bg = CLR_SLOT_DRAG_TARGET_BG
            elif is_selected:
                bg = CLR_SLOT_SELECTED_BG
            else:
                bg = CLR_SLOT_FILLED

            cell.configure(
                background=bg,
                highlightthickness=(2 if is_selected else 1),
                highlightbackground=(
                    CLR_SLOT_SELECTED_BORDER if is_selected else CLR_BORDER
                ),
            )

            # Lớp — đỏ đậm để nổi bật giữa các ô có data
            lbl_lop.configure(
                text=slot.lop_text or "",
                background=bg,
                foreground="#c1121f",
                font=("Segoe UI", 10, "bold"),
                wraplength=wrap_w,
            )
            # Môn
            lbl_mon.configure(
                text=mon_short,
                background=bg,
                foreground=CLR_SLOT_FILLED_TEXT,
                font=("Segoe UI", 10),
                wraplength=wrap_w,
            )
            # Phân môn — vàng đậm, KHÔNG bold, font nhỏ hơn 1 cỡ so với môn
            # để tránh bị tràn/ẩn khi cell hẹp (dual mode).
            if pm_short:
                lbl_pm.configure(
                    text=pm_short,
                    background=bg,
                    foreground="#c1121f",
                    font=("Segoe UI", 7),
                    wraplength=wrap_w,
                )
            else:
                lbl_pm.configure(text="", background=bg)

            # Layout 3 label theo y-pos cố định trong cell
            self._layout_cell_labels(cell, lbl_lop, lbl_mon, lbl_pm,
                                     col_w, row_h, has_pm=bool(pm_short))
        else:
            # Empty cell — chỉ "+" mờ ở giữa
            if is_drag_target:
                bg = CLR_SLOT_DRAG_TARGET_BG
            elif is_selected:
                bg = CLR_SLOT_SELECTED_BG
            else:
                bg = CLR_SLOT_EMPTY

            cell.configure(
                background=bg,
                highlightthickness=(2 if is_selected else 1),
                highlightbackground=(
                    CLR_SLOT_SELECTED_BORDER if is_selected else CLR_BORDER
                ),
            )
            # Empty: hide lop + pm, dùng lbl_mon hiển thị "+" giữa cell
            lbl_lop.configure(text="", background=bg)
            lbl_pm.configure(text="", background=bg)
            lbl_mon.configure(
                text="+",
                background=bg,
                foreground="#aaa",
                font=("Segoe UI", 13),
            )
            self._layout_cell_labels(cell, lbl_lop, lbl_mon, lbl_pm,
                                     col_w, row_h, has_pm=False,
                                     empty=True)

    @staticmethod
    def _layout_cell_labels(
        cell, lbl_lop, lbl_mon, lbl_pm,
        col_w: int, row_h: int,
        *, has_pm: bool, empty: bool = False,
    ):
        """Place các label trong cell với spacing đẹp.

        - Empty: chỉ lbl_mon ("+") full center.
        - Có data, no pm: lbl_lop trên (1/3), lbl_mon dưới (2/3).
        - Có data + pm: 3 label chia đều theo tỉ lệ 30 / 30 / 30 với gap 1px.
        """
        # Hide all trước rồi place lại theo case
        for w in (lbl_lop, lbl_mon, lbl_pm):
            try:
                w.place_forget()
            except Exception:
                pass
        if empty:
            lbl_mon.place(
                relx=0.5, rely=0.5, anchor="center",
            )
            return
        if has_pm:
            # 3 dòng — chia đều
            third = max(row_h // 3, 14)
            lbl_lop.place(x=2, y=2, width=col_w - 4, height=third - 1)
            lbl_mon.place(x=2, y=third + 1,
                          width=col_w - 4, height=third - 1)
            lbl_pm.place(x=2, y=third * 2 + 1,
                         width=col_w - 4, height=row_h - third * 2 - 3)
        else:
            half = max(row_h // 2, 18)
            lbl_lop.place(x=2, y=2, width=col_w - 4, height=half - 2)
            lbl_mon.place(x=2, y=half,
                          width=col_w - 4, height=row_h - half - 2)

    @staticmethod
    def _abbreviate_mon(mon_text: str) -> str:
        """Rút gọn tên môn cho hiển thị compact.

        Hoạt động trải nghiệm... → HĐTN
        Hoạt động giáo dục theo... → HĐGD theo chủ đề
        Giữ nguyên các môn khác.
        """
        m = (mon_text or "").strip()
        if not m:
            return ""
        low = m.lower()
        if "hoạt động trải nghiệm" in low:
            return "HĐTN"
        if "hoạt động giáo dục" in low:
            return "HĐGD"
        return m

    @staticmethod
    def _abbreviate_phan_mon(pm_text: str) -> str:
        """Rút gọn phân môn HĐTN cho compact + match request user.

        - Hoạt động giáo dục theo chủ đề → Chủ đề
        - Sinh hoạt dưới cờ → Chào cờ
        - Sinh hoạt lớp → giữ nguyên (đã ngắn)
        - Tiết chủ nhiệm → giữ nguyên
        - TC ngoại ngữ / TC ... → giữ nguyên (đã có TC)
        """
        p = (pm_text or "").strip()
        if not p:
            return ""
        low = p.lower()
        # HĐTN sub-themes
        if "hoạt động giáo dục theo" in low or low.startswith("chủ đề"):
            return "Chủ đề"
        if "sinh hoạt dưới cờ" in low or low == "chào cờ":
            return "Chào cờ"
        return p

    def _refresh_all_panels(self):
        for pi in range(len(self._panels)):
            for key in list(self._panels[pi]["buttons"].keys()):
                self._restyle_button(pi, key)

    def _set_selection(self, panel_idx: int, key: str | None):
        prev_pi = self._selected_panel_idx
        prev_key = self._selected_key
        self._selected_panel_idx = panel_idx
        self._selected_key = key
        # Restore old
        if prev_pi >= 0 and prev_key is not None:
            self._restyle_button(prev_pi, prev_key)
        # Apply new
        if key is not None and panel_idx >= 0:
            self._restyle_button(panel_idx, key)

    def _set_status(self, msg: str):
        try:
            self.var_status.set(msg)
        except Exception:
            pass

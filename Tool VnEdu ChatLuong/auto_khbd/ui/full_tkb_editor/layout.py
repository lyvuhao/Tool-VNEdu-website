"""Dựng giao diện và lưới TKB."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..theme import (
    CLR_BORDER,
    CLR_GRID_HEADER_BG,
    CLR_GRID_HEADER_BORDER,
    CLR_GRID_HEADER_FG,
    CLR_PANEL_BG,
    CLR_SLOT_EMPTY,
    DAYS,
    TIETS_BY_BUOI,
)


class EditorLayoutMixin:
    """Dựng giao diện và lưới TKB."""

    # -----------------------------------------------------------
    # Build UI
    # -----------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Header — toolbar với mode picker
        hdr = ttk.Frame(self, padding=(12, 8), style="Wiz.TFrame")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(99, weight=1)

        ttk.Label(
            hdr, text="🔍 Phóng to TKB",
            font=("Segoe UI", 13, "bold"),
            style="WizAccent.TLabel",
        ).grid(row=0, column=0, sticky="w")

        mode_frm = ttk.Frame(hdr, style="Wiz.TFrame")
        mode_frm.grid(row=0, column=1, padx=(20, 0))

        is_tach = bool(self.wizard.profile
                       and self.wizard.profile.tach_le_chan)

        ttk.Radiobutton(
            mode_frm, text="Xem 1 mẫu",
            variable=self.var_mode, value=self.MODE_SINGLE,
            command=self._on_mode_changed,
        ).pack(side="left")
        rb_le_chan = ttk.Radiobutton(
            mode_frm, text="Xem cả lẻ + chẵn cạnh nhau",
            variable=self.var_mode, value=self.MODE_LE_CHAN,
            command=self._on_mode_changed,
        )
        rb_le_chan.pack(side="left", padx=(12, 0))
        if not is_tach:
            rb_le_chan.configure(state="disabled")

        # Single target picker — chỉ active khi mode SINGLE + profile tách.
        self._single_target_frm = ttk.Frame(hdr, style="Wiz.TFrame")
        self._single_target_frm.grid(row=0, column=2, padx=(20, 0))
        ttk.Label(
            self._single_target_frm, text="Hiển thị:", style="Wiz.TLabel",
        ).pack(side="left")
        ttk.Radiobutton(
            self._single_target_frm,
            text="TKB lẻ", variable=self.var_single_target,
            value="le", command=self._on_single_target_changed,
        ).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(
            self._single_target_frm,
            text="TKB chẵn", variable=self.var_single_target,
            value="chan", command=self._on_single_target_changed,
        ).pack(side="left", padx=(4, 0))
        if not is_tach:
            self._single_target_frm.grid_remove()

        # Hint
        ttk.Label(
            hdr,
            text=(
                "Click chọn · Double-click sửa · Kéo để copy · "
                "Chuột phải/Alt để Nghỉ-Dạy bù · Ctrl+C/V · Delete · Esc đóng"
            ),
            style="WizHint.TLabel",
        ).grid(row=1, column=0, columnspan=99, sticky="w", pady=(4, 0))

        # Body — chứa 1 hoặc 2 grid panels
        self._body = ttk.Frame(self, padding=(12, 0, 12, 6),
                                style="Wiz.TFrame")
        self._body.grid(row=1, column=0, sticky="nsew")
        self._body.rowconfigure(0, weight=1)

        # Status bar — feedback cho copy/paste/delete actions
        sb = ttk.Frame(self, padding=(12, 4, 12, 8), style="Wiz.TFrame")
        sb.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            sb, textvariable=self.var_status, style="WizHint.TLabel",
        ).pack(side="left")

        self._render_body()

    def _on_mode_changed(self):
        is_tach = bool(self.wizard.profile
                       and self.wizard.profile.tach_le_chan)
        if (self.var_mode.get() == self.MODE_SINGLE) and is_tach:
            try:
                self._single_target_frm.grid()
            except Exception:
                pass
        else:
            try:
                self._single_target_frm.grid_remove()
            except Exception:
                pass
        # Reset selection vì panel sẽ rebuild
        self._selected_key = None
        self._selected_panel_idx = -1
        self._render_body()

    def _on_single_target_changed(self):
        if self.var_mode.get() == self.MODE_SINGLE:
            self._selected_key = None
            self._selected_panel_idx = -1
            self._render_body()

    def _compute_cell_sizes(self, panel_count: int) -> tuple[int, int]:
        """Compute (COL_DAY_W, ROW_BODY_H) sao cho grid vừa khít screen.

        Nguyên tắc:
            - Total width (per panel) = COL_BUOI_W + COL_TIET_W + 7×COL_DAY_W.
            - Available width = (screen_w − chrome) / panel_count.
            - Available height = screen_h − header − status − chrome.
            - Trong height có 10 rows tiet + 1 separator + 1 header row +
              1 title bar.
        Áp dụng MIN/MAX clamp.
        """
        try:
            scr_w = self.winfo_screenwidth()
            scr_h = self.winfo_screenheight()
        except Exception:
            scr_w, scr_h = 1366, 768
        # Available — cập nhật theo size cửa sổ thực tế nếu đã rendered.
        cur_w = self.winfo_width() or scr_w
        cur_h = self.winfo_height() or scr_h
        # Nếu chưa render (cur_w=1) → dùng screen size.
        if cur_w <= 100:
            cur_w = scr_w
        if cur_h <= 100:
            cur_h = scr_h

        # Width per panel — tính chính xác overhead:
        # Body padding: 12*2 = 24
        # Scrollbar per panel: 18
        # Panel wrapper padx: 4*2 = 8 per panel
        # Khi dual: gap giữa 2 panel = 8
        per_panel_overhead = 18 + 8  # scrollbar + padx
        total_overhead = 24 + per_panel_overhead * panel_count
        if panel_count > 1:
            total_overhead += 8  # gap giữa panels
        available_w_per_panel = max(
            (cur_w - total_overhead) // panel_count, 400,
        )
        # COL_DAY_W = (avail - col_buoi - col_tiet) / 7
        col_day = (available_w_per_panel - self.COL_BUOI_W - self.COL_TIET_W) // len(DAYS)
        col_day = max(self.MIN_COL_DAY_W, min(self.MAX_COL_DAY_W, col_day))

        # Available height: (header 50) + (hint 24) + (title bar 32) +
        # (status 28) + (window padding 28) ≈ 162 chrome.
        # Inner needs: header_row 32 + 10*row_body + separator 16
        avail_h = max(cur_h - 162, 360)
        row_body = (avail_h - self.ROW_HEADER_H - self.SEPARATOR_H) // 10
        row_body = max(self.MIN_ROW_BODY_H, min(self.MAX_ROW_BODY_H, row_body))
        return col_day, row_body

    def _render_body(self):
        """(Re)build các panels theo mode hiện tại."""
        for w in self._body.winfo_children():
            w.destroy()
        self._panels.clear()
        # Reset widget lookup cache — rebuild khi panels rebuild.
        self._widget_to_cell: dict[int, tuple[int, str]] = {}
        # Reset drag state — visual cũng bị destroy theo widget.
        self._drag_state = None

        mode = self.var_mode.get()
        if mode == self.MODE_LE_CHAN:
            self._body.columnconfigure(0, weight=1, uniform="grids")
            self._body.columnconfigure(1, weight=1, uniform="grids")
            le_frm = self._make_panel_frame(
                self._body, "TKB tuần lẻ (1, 3, 5…)",
                accent="#fff8e1", col=0,
            )
            chan_frm = self._make_panel_frame(
                self._body, "TKB tuần chẵn (2, 4, 6…)",
                accent="#e3f2fd", col=1,
            )
            self._build_grid_in(le_frm, mode_tag="le")
            self._build_grid_in(chan_frm, mode_tag="chan")
        else:
            self._body.columnconfigure(0, weight=1, uniform="grids")
            try:
                self._body.columnconfigure(1, weight=0)
            except Exception:
                pass
            is_tach = bool(self.wizard.profile
                           and self.wizard.profile.tach_le_chan)
            if is_tach:
                target = self.var_single_target.get()
                title = ("TKB tuần lẻ (1, 3, 5…)" if target == "le"
                         else "TKB tuần chẵn (2, 4, 6…)")
                accent = "#fff8e1" if target == "le" else "#e3f2fd"
                tag = target
            else:
                title = "TKB chính (cùng cho mọi tuần)"
                accent = "#f0f4f8"
                tag = "chinh"
            single_frm = self._make_panel_frame(
                self._body, title, accent=accent, col=0,
            )
            self._build_grid_in(single_frm, mode_tag=tag)

        self._refresh_all_panels()

    def _make_panel_frame(
        self, parent, title: str, accent: str, col: int,
    ) -> ttk.Frame:
        wrapper = ttk.Frame(parent, style="Wiz.TFrame")
        wrapper.grid(row=0, column=col, sticky="nsew", padx=4)
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(1, weight=1)

        title_bar = tk.Label(
            wrapper, text=title,
            background=accent, foreground="#1a3a5c",
            font=("Segoe UI", 11, "bold"),
            padx=10, pady=6, anchor="w",
            borderwidth=1, relief="solid",
        )
        title_bar.grid(row=0, column=0, sticky="ew")

        body = ttk.Frame(wrapper, style="Wiz.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        return body

    def _build_grid_in(self, parent: ttk.Frame, mode_tag: str):
        """Build 1 grid scrollable bên trong parent.

        Args:
            mode_tag: "chinh" | "le" | "chan" — xác định template để edit.
        """
        is_dual = (self.var_mode.get() == self.MODE_LE_CHAN)
        panel_count = 2 if is_dual else 1
        col_day_w, row_body_h = self._compute_cell_sizes(panel_count)

        total_inner_w = (
            self.COL_BUOI_W + self.COL_TIET_W
            + col_day_w * len(DAYS)
        )
        total_inner_h = (
            self.ROW_HEADER_H
            + row_body_h * 10
            + self.SEPARATOR_H * (len(TIETS_BY_BUOI) - 1)
        )

        canvas = tk.Canvas(
            parent, background=CLR_PANEL_BG,
            highlightthickness=0, bd=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        v_sb = ttk.Scrollbar(
            parent, orient="vertical", command=canvas.yview,
        )
        v_sb.grid(row=0, column=1, sticky="ns")
        h_sb = ttk.Scrollbar(
            parent, orient="horizontal", command=canvas.xview,
        )
        h_sb.grid(row=1, column=0, sticky="ew")
        canvas.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)

        inner = tk.Frame(canvas, background=CLR_PANEL_BG)
        cw_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_cfg(_evt=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
        inner.bind("<Configure>", _on_inner_cfg)

        # Center inner khi canvas rộng hơn inner (single mode trên screen
        # lớn → grid không chiếm hết chiều ngang → khoảng trống bên phải).
        # Reposition canvas_window x để inner nằm giữa.
        def _on_canvas_cfg(evt):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
                # Nếu canvas rộng hơn inner → center
                cw = evt.width
                iw = inner.winfo_reqwidth() or total_inner_w
                if cw > iw:
                    x_offset = (cw - iw) // 2
                    canvas.coords(cw_id, x_offset, 0)
                else:
                    canvas.coords(cw_id, 0, 0)
            except Exception:
                pass
        canvas.bind("<Configure>", _on_canvas_cfg)

        def _wheel(evt):
            try:
                canvas.yview_scroll(int(-1 * (evt.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind("<Enter>",
                    lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>",
                    lambda e: canvas.unbind_all("<MouseWheel>"))

        # ---- Headers ----
        def _make_header(text, x, y, w, h, *, fg=None):
            tk.Label(
                inner, text=text,
                font=("Segoe UI", 10, "bold"),
                background=CLR_GRID_HEADER_BG,
                foreground=fg or CLR_GRID_HEADER_FG,
                borderwidth=1, relief="solid",
                highlightbackground=CLR_GRID_HEADER_BORDER,
            ).place(x=x, y=y, width=w, height=h)

        _make_header("Buổi", 0, 0, self.COL_BUOI_W, self.ROW_HEADER_H)
        _make_header("Tiết", self.COL_BUOI_W, 0,
                     self.COL_TIET_W, self.ROW_HEADER_H)
        for ci, (thu_num, thu_lbl) in enumerate(DAYS):
            x_col = self.COL_BUOI_W + self.COL_TIET_W + ci * col_day_w
            _make_header(
                thu_lbl, x_col, 0, col_day_w, self.ROW_HEADER_H,
                fg="#b00020" if thu_num == 8 else None,
            )

        slot_buttons: dict[str, tk.Button] = {}
        # panel_idx được set sau khi append; cell handler cần ID này nên
        # tôi capture qua closure bằng list reference.
        panel_idx = len(self._panels)

        # ---- Body rows + separator giữa Sáng/Chiều ----
        row_y = self.ROW_HEADER_H
        for buoi_pos, (buoi_label, buoi_idx, tiets) in enumerate(TIETS_BY_BUOI):
            if buoi_pos > 0:
                tk.Frame(
                    inner, background="#3a4a5e",
                    height=2, borderwidth=0,
                ).place(
                    x=0, y=row_y, width=total_inner_w, height=2,
                )
                tk.Frame(
                    inner, background=CLR_PANEL_BG, borderwidth=0,
                ).place(
                    x=0, y=row_y + 2,
                    width=total_inner_w,
                    height=self.SEPARATOR_H - 4,
                )
                tk.Frame(
                    inner, background="#3a4a5e",
                    height=2, borderwidth=0,
                ).place(
                    x=0, y=row_y + self.SEPARATOR_H - 2,
                    width=total_inner_w, height=2,
                )
                row_y += self.SEPARATOR_H

            tiet_count = len(tiets)
            buoi_y = row_y
            buoi_h = row_body_h * tiet_count
            if buoi_idx == 1:
                buoi_bg = "#ffd9a8"
                buoi_fg = "#7a4a18"
                buoi_icon = "☀"
                tiet_bg = "#ffefd5"
            else:
                buoi_bg = "#bcdaf2"
                buoi_fg = "#1c4e7a"
                buoi_icon = "🌙"
                tiet_bg = "#dceefb"
            tk.Label(
                inner, text=f"{buoi_icon}\n{buoi_label}",
                font=("Segoe UI", 11, "bold"),
                background=buoi_bg, foreground=buoi_fg,
                borderwidth=1, relief="solid",
                highlightbackground=CLR_GRID_HEADER_BORDER,
                justify="center",
            ).place(x=0, y=buoi_y,
                    width=self.COL_BUOI_W, height=buoi_h)

            for ti_pos, tiet in enumerate(tiets):
                cell_y = buoi_y + ti_pos * row_body_h
                tk.Label(
                    inner, text=str(tiet),
                    font=("Segoe UI", 11, "bold"),
                    background=tiet_bg, foreground=buoi_fg,
                    borderwidth=1, relief="solid",
                    highlightbackground=CLR_GRID_HEADER_BORDER,
                ).place(
                    x=self.COL_BUOI_W, y=cell_y,
                    width=self.COL_TIET_W, height=row_body_h,
                )
                for ci, (thu_num, _lbl) in enumerate(DAYS):
                    cell_x = (
                        self.COL_BUOI_W + self.COL_TIET_W
                        + ci * col_day_w
                    )
                    # Cell wrapper Frame — chứa 3 Label (lop / mon / pm)
                    # cho phép màu chữ khác nhau (lop đỏ đậm). Frame nhận
                    # events; child Label cũng được bind cùng handler để
                    # click trên text vẫn trigger select/drag.
                    cell = tk.Frame(
                        inner,
                        background=CLR_SLOT_EMPTY,
                        borderwidth=1, relief="solid",
                        highlightbackground=CLR_BORDER,
                        highlightthickness=0,
                        cursor="hand2",
                    )
                    cell.place(
                        x=cell_x, y=cell_y,
                        width=col_day_w, height=row_body_h,
                    )
                    # Disable propagate để Label children không ép resize
                    cell.pack_propagate(False)
                    cell.grid_propagate(False)
                    # Placeholder labels — sẽ update text/màu trong
                    # _restyle_button. Tạo sẵn để giữ ref ổn định, không
                    # phải destroy/create mỗi lần refresh.
                    lbl_lop = tk.Label(
                        cell, text="",
                        background=CLR_SLOT_EMPTY,
                        anchor="center", justify="center",
                    )
                    lbl_mon = tk.Label(
                        cell, text="",
                        background=CLR_SLOT_EMPTY,
                        anchor="center", justify="center",
                    )
                    lbl_pm = tk.Label(
                        cell, text="",
                        background=CLR_SLOT_EMPTY,
                        anchor="center", justify="center",
                    )
                    # Bind events trên cell + 3 child label (để click vào
                    # text vẫn trigger handler của cell, không bị nuốt).
                    targets = (cell, lbl_lop, lbl_mon, lbl_pm)
                    key = f"{thu_num}_{buoi_idx}_{tiet}"
                    self._bind_cell_events(
                        targets, panel_idx, mode_tag,
                        thu_num, buoi_idx, tiet,
                    )
                    # Lưu cell + child label refs vào structure giàu hơn
                    # so với slot_buttons cũ. _restyle_button đọc tuple
                    # này để update đúng từng label.
                    slot_buttons[key] = {
                        "cell": cell,
                        "lbl_lop": lbl_lop,
                        "lbl_mon": lbl_mon,
                        "lbl_pm": lbl_pm,
                    }

            row_y += buoi_h

        inner.configure(width=total_inner_w, height=total_inner_h)
        self._panels.append({
            "canvas": canvas,
            "inner": inner,
            "buttons": slot_buttons,
            "tag": mode_tag,
            "sizes": (col_day_w, row_body_h),
        })
        # Populate widget→cell lookup cache cho O(1) drag lookup.
        pi = len(self._panels) - 1
        for k, cell_dict in slot_buttons.items():
            for w in (cell_dict["cell"], cell_dict["lbl_lop"],
                      cell_dict["lbl_mon"], cell_dict["lbl_pm"]):
                self._widget_to_cell[id(w)] = (pi, k)

    def _bind_cell_events(
        self, widgets: tuple,
        panel_idx: int, tag: str,
        thu: int, buoi: int, tiet: int,
    ):
        """Bind tất cả mouse events lên cell Frame + child Labels.

        Click vào label child KHÔNG tự fire event của parent Frame trong Tk
        (event propagation default), vì vậy phải bind cùng handler trên
        cả 4 widget.
        """
        for w in widgets:
            w.bind(
                "<Button-1>",
                lambda e, t=thu, b=buoi, ti=tiet, pi=panel_idx, g=tag:
                self._on_cell_press(e, pi, g, t, b, ti),
            )
            w.bind(
                "<B1-Motion>",
                lambda e: self._on_cell_drag_motion(e),
            )
            w.bind(
                "<ButtonRelease-1>",
                lambda e: self._on_cell_release(e),
            )
            w.bind(
                "<Double-Button-1>",
                lambda _e, t=thu, b=buoi, ti=tiet, pi=panel_idx, g=tag:
                self._on_cell_double_clicked(pi, g, t, b, ti),
            )
            w.bind(
                "<Button-3>",
                lambda e, t=thu, b=buoi, ti=tiet, pi=panel_idx, g=tag:
                self._on_cell_right_click(e, pi, g, t, b, ti),
            )
            w.bind(
                "<Alt-Button-1>",
                lambda e, t=thu, b=buoi, ti=tiet, pi=panel_idx, g=tag:
                self._on_cell_alt_click(e, pi, g, t, b, ti),
            )

"""Hộp thoại quét TKB toàn năm và chọn mẫu."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ..theme import (
    CLR_BORDER,
    CLR_GRID_HEADER_BG,
    CLR_GRID_HEADER_BORDER,
    CLR_GRID_HEADER_FG,
    CLR_PANEL_BG,
    CLR_SLOT_FILLED,
    CLR_SLOT_FILLED_BORDER,
    CLR_SLOT_FILLED_TEXT,
    DAYS,
    TIETS_BY_BUOI,
)
from ..workers.tkb_import import ImportTKBPatterns, ImportTKBReport, TKBPattern
from ..workers.tkb_scan import TKBScanWorker

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.profile.models import SlotEntry


# =====================================================================
# Dialog — ImportTKBDialog (quét TKB toàn năm + chọn pattern)
# =====================================================================

class ImportTKBDialog(tk.Toplevel):
    """Quét TKB toàn năm từ VnEdu + cho user chọn pattern áp vào TKB tool.

    Workflow chuẩn:
        1. Mở dialog → spawn `TKBScanWorker` ngầm.
        2. Progress bar realtime, có nút Stop.
        3. Khi xong → render danh sách patterns + lựa chọn áp dụng.
        4. User chọn:
           - Áp 1 mẫu cho tất cả tuần → set `template_chinh`.
           - Tách lẻ/chẵn → set `template_le` + `template_chan`.
        5. Dialog trả result qua `self.imported` (None nếu Hủy).

    Halt-on-error: bất kỳ lỗi nghiêm trọng nào → hiển thị message + cho
    user thử lại / đóng dialog. KHÔNG tự apply gì.
    """

    def __init__(self, parent: tk.Misc, port: int):
        super().__init__(parent)
        self.title("🔍 Nhập TKB từ VnEdu")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent.winfo_toplevel() if parent else None)
        self.grab_set()
        self.resizable(True, True)

        # Auto-fit screen — tránh dialog bị tràn ra ngoài màn hình của user.
        # Theo screenshot user thấy, dialog gốc 920×720 + popup 700×460 đè
        # lên nhau khiến UI thừa không gian. Dialog chính dùng 90% màn hình
        # với cap hợp lý để không quá khổ trên màn hình lớn.
        scr_w = self.winfo_screenwidth()
        scr_h = self.winfo_screenheight()
        target_w = min(int(scr_w * 0.85), 1080)
        target_h = min(int(scr_h * 0.85), 760)
        x = max(0, (scr_w - target_w) // 2)
        y = max(0, (scr_h - target_h) // 2)
        self.geometry(f"{target_w}x{target_h}+{x}+{y}")
        self.minsize(min(820, scr_w - 40), min(560, scr_h - 80))

        self.port = port
        self.imported: dict | None = None  # set khi user bấm "Áp dụng"
        self._report: ImportTKBReport | None = None
        self._patterns: ImportTKBPatterns | None = None

        # Worker state
        self._worker: TKBScanWorker | None = None
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._poll_after_id: str | None = None

        # UI vars
        self.var_status = tk.StringVar(value="Đang chuẩn bị quét…")
        self.var_progress = tk.IntVar(value=0)
        self.var_apply_mode = tk.StringVar(value="single")
        # ↑ "single" | "le_chan"
        self.var_pick_single = tk.StringVar(value="")  # pattern_id
        self.var_pick_le = tk.StringVar(value="")
        self.var_pick_chan = tk.StringVar(value="")

        self._build_ui()
        # Bind Esc để Stop / Cancel
        self.bind("<Escape>", self._on_esc)
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

        # Auto-start scan
        self.after(150, self._start_scan)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header
        hdr = ttk.Frame(self, padding=12, style="Wiz.TFrame")
        hdr.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            hdr, text="🔍 Nhập TKB từ VnEdu",
            font=("Segoe UI", 13, "bold"),
            style="WizAccent.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            hdr,
            text=(
                "Công cụ sẽ CHỈ ĐỌC các tuần KHDH từ web, "
                "phát hiện các MẪU TKB khác nhau, rồi cho bạn chọn "
                "mẫu nào để áp vào TKB hiện tại trong tool. "
                "KHÔNG ghi gì lên server."
            ),
            wraplength=860,
            style="WizHint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # Progress
        prog_frm = ttk.Frame(self, padding=(12, 0, 12, 8), style="Wiz.TFrame")
        prog_frm.grid(row=1, column=0, sticky="ew")
        prog_frm.columnconfigure(0, weight=1)
        ttk.Label(
            prog_frm, textvariable=self.var_status, style="Wiz.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.progress_bar = ttk.Progressbar(
            prog_frm, mode="determinate",
            variable=self.var_progress, maximum=1,
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.btn_stop = ttk.Button(
            prog_frm, text="■ Dừng quét", command=self._on_stop,
            style="WizDanger.TButton",
        )
        self.btn_stop.grid(row=1, column=1, padx=(8, 0))

        # Body — sẽ build khi worker xong
        self._body = ttk.Frame(self, padding=12, style="Wiz.TFrame")
        self._body.grid(row=2, column=0, sticky="nsew")
        self._body.columnconfigure(0, weight=1)
        self._body.rowconfigure(0, weight=1)

        # Footer
        ftr = ttk.Frame(self, padding=12, style="Wiz.TFrame")
        ftr.grid(row=3, column=0, sticky="ew")
        ftr.columnconfigure(0, weight=1)
        self.btn_apply = ttk.Button(
            ftr, text="✓ Áp dụng vào TKB",
            command=self._on_apply,
            state="disabled",
            style="WizPrimary.TButton",
        )
        self.btn_apply.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            ftr, text="✗ Hủy", command=self._on_cancel,
            style="WizSubtle.TButton",
        ).grid(row=0, column=2)

    def _start_scan(self):
        self._stop_event.clear()
        self._worker = TKBScanWorker(
            port=self.port,
            event_queue=self._queue,
            stop_event=self._stop_event,
        )
        self._worker.start()
        self._poll_queue()

    def _poll_queue(self):
        try:
            while True:
                ev = self._queue.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        if self._worker and self._worker.is_alive():
            try:
                self._poll_after_id = self.after(120, self._poll_queue)
            except tk.TclError:
                self._poll_after_id = None
        else:
            self._poll_after_id = None
            self.btn_stop.configure(state="disabled")

    def _handle_event(self, ev: tuple):
        kind = ev[0]
        if kind == "status":
            self.var_status.set(str(ev[1]))
        elif kind == "progress":
            done, total = int(ev[1]), int(ev[2])
            self.progress_bar.configure(maximum=max(total, 1))
            self.var_progress.set(done)
            self.var_status.set(f"Đang quét: {done}/{total} tuần")
        elif kind == "done":
            report: ImportTKBReport = ev[1]
            self._report = report
            self._patterns = report.patterns
            self.var_progress.set(self.progress_bar["maximum"])
            note = ""
            if report.stopped:
                note = " (đã dừng)"
            elif report.patterns.fetch_errors:
                note = (
                    f" — {len(report.patterns.fetch_errors)} tuần lỗi"
                )
            self.var_status.set(
                f"✓ Quét xong {len(report.patterns.weeks_with_data)} "
                f"tuần có data, "
                f"{len(report.patterns.patterns)} mẫu khác nhau "
                f"({report.duration_ms / 1000:.1f}s){note}"
            )
            self._render_results()
        elif kind == "error":
            self.var_status.set("❌ Lỗi quét")
            self.btn_stop.configure(state="disabled")
            messagebox.showerror(
                "Lỗi quét TKB", str(ev[1]).splitlines()[0],
                parent=self,
            )
            # Cho user xem chi tiết trong status, có thể đóng dialog
            for w in self._body.winfo_children():
                w.destroy()
            ttk.Label(
                self._body,
                text=str(ev[1]),
                wraplength=860, justify="left",
                style="WizHint.TLabel",
            ).pack(anchor="w", padx=8, pady=8)

    def _render_results(self):
        for w in self._body.winfo_children():
            w.destroy()
        patterns = (self._patterns.patterns if self._patterns else [])
        if not patterns:
            ttk.Label(
                self._body,
                text=(
                    "Không phát hiện mẫu TKB nào trên web.\n"
                    "Có thể tất cả các tuần đều trống, hoặc TKB chưa "
                    "được phân công cho giáo viên này."
                ),
                wraplength=860, justify="left",
                style="WizHint.TLabel",
            ).pack(anchor="w", padx=8, pady=8)
            return

        # Section 1: list patterns
        sec1 = ttk.LabelFrame(
            self._body, text=" Các mẫu TKB phát hiện được ",
            padding=8, style="Wiz.TLabelframe",
        )
        sec1.pack(fill="x", pady=(0, 8))

        for p in patterns:
            row = ttk.Frame(sec1, style="Wiz.TFrame")
            row.pack(fill="x", pady=2)
            label = (
                f"📋 Mẫu {p.pattern_id} — {len(p.weeks)} tuần "
                f"({_format_weeks_compact(p.weeks)}) — "
                f"{p.slot_count} ô / {p.group_count} nhóm "
                f"(lẻ: {p.le_count} / chẵn: {p.chan_count})"
            )
            ttk.Label(
                row, text=label, style="Wiz.TLabel",
                font=("Segoe UI", 10, "bold"),
            ).pack(side="left")
            ttk.Button(
                row, text="👁 Xem chi tiết",
                command=lambda pp=p: self._show_pattern_detail(pp),
                style="WizSubtle.TButton",
            ).pack(side="right")

        # Section 2: apply mode
        sec2 = ttk.LabelFrame(
            self._body, text=" Cách áp dụng ",
            padding=10, style="Wiz.TLabelframe",
        )
        sec2.pack(fill="x", pady=(0, 8))

        # Build option list theo pattern_id
        pids = [p.pattern_id for p in patterns]
        # Default chọn pattern dominant cho single
        if pids:
            self.var_pick_single.set(pids[0])
        # Default đề xuất le_chan nếu phát hiện được
        auto_split = (
            self._patterns is not None
            and self._patterns.has_le_chan_split
        )
        if auto_split:
            self.var_apply_mode.set("le_chan")
            le_p = self._patterns.pattern_for_le
            chan_p = self._patterns.pattern_for_chan
            if le_p:
                self.var_pick_le.set(le_p.pattern_id)
            if chan_p:
                self.var_pick_chan.set(chan_p.pattern_id)
        else:
            self.var_apply_mode.set("single")

        opt_single_frm = ttk.Frame(sec2, style="Wiz.TFrame")
        opt_single_frm.pack(fill="x", anchor="w", pady=2)
        ttk.Radiobutton(
            opt_single_frm,
            text="Áp 1 mẫu cho tất cả tuần (KHÔNG tách lẻ/chẵn) — chọn mẫu:",
            variable=self.var_apply_mode, value="single",
            command=self._refresh_apply_state,
        ).pack(side="left")
        self._cmb_single = ttk.Combobox(
            opt_single_frm, textvariable=self.var_pick_single,
            values=pids, state="readonly", width=6,
        )
        self._cmb_single.pack(side="left", padx=(6, 0))

        opt_lc_frm = ttk.Frame(sec2, style="Wiz.TFrame")
        opt_lc_frm.pack(fill="x", anchor="w", pady=4)
        rb_lc = ttk.Radiobutton(
            opt_lc_frm,
            text="Tách lẻ/chẵn — mẫu cho tuần lẻ:",
            variable=self.var_apply_mode, value="le_chan",
            command=self._refresh_apply_state,
        )
        rb_lc.pack(side="left")
        self._cmb_le = ttk.Combobox(
            opt_lc_frm, textvariable=self.var_pick_le,
            values=pids, state="readonly", width=6,
        )
        self._cmb_le.pack(side="left", padx=(6, 0))
        ttk.Label(
            opt_lc_frm, text="  mẫu cho tuần chẵn:", style="Wiz.TLabel",
        ).pack(side="left")
        self._cmb_chan = ttk.Combobox(
            opt_lc_frm, textvariable=self.var_pick_chan,
            values=pids, state="readonly", width=6,
        )
        self._cmb_chan.pack(side="left", padx=(6, 0))

        # Hint dưới radio le_chan
        if auto_split:
            ttk.Label(
                sec2,
                text=(
                    "✓ Công cụ tự phát hiện rõ 2 mẫu chia đúng lẻ/chẵn — "
                    "đã chọn sẵn ánh xạ phù hợp, bạn có thể đổi nếu cần."
                ),
                style="WizAccent.TLabel",
                wraplength=860, justify="left",
            ).pack(anchor="w", padx=20, pady=(2, 0))
        else:
            ttk.Label(
                sec2,
                text=(
                    "Tool KHÔNG đề xuất tách lẻ/chẵn vì các mẫu phát "
                    "hiện được không phân tách rõ ràng theo lẻ/chẵn. "
                    "Nếu vẫn muốn tách, hãy chọn thủ công."
                ),
                style="WizHint.TLabel",
                wraplength=860, justify="left",
            ).pack(anchor="w", padx=20, pady=(2, 0))

        # Section 3: weeks_empty / fetch_errors
        if (self._patterns and (self._patterns.weeks_empty
                                or self._patterns.fetch_errors)):
            sec3 = ttk.LabelFrame(
                self._body, text=" Ghi chú ",
                padding=8, style="Wiz.TLabelframe",
            )
            sec3.pack(fill="x", pady=(0, 8))
            if self._patterns.weeks_empty:
                ttk.Label(
                    sec3,
                    text=(
                        f"• {len(self._patterns.weeks_empty)} tuần "
                        f"không có TKB ({_format_weeks_compact(self._patterns.weeks_empty)})"
                        " — bị bỏ qua khi gom mẫu."
                    ),
                    style="WizHint.TLabel",
                    wraplength=860, justify="left",
                ).pack(anchor="w")
            if self._patterns.fetch_errors:
                err_weeks = [w for w, _ in self._patterns.fetch_errors]
                ttk.Label(
                    sec3,
                    text=(
                        f"• {len(err_weeks)} tuần fetch lỗi "
                        f"({_format_weeks_compact(err_weeks)})"
                        " — vẫn áp dụng được mẫu nhưng có thể thiếu data."
                    ),
                    style="WizHint.TLabel",
                    wraplength=860, justify="left",
                ).pack(anchor="w")

        self._refresh_apply_state()
        self.btn_apply.configure(state="normal")

    def _refresh_apply_state(self):
        # Enable/disable comboboxes theo radio chọn
        try:
            mode = self.var_apply_mode.get()
            if mode == "single":
                self._cmb_single.configure(state="readonly")
                self._cmb_le.configure(state="disabled")
                self._cmb_chan.configure(state="disabled")
            else:
                self._cmb_single.configure(state="disabled")
                self._cmb_le.configure(state="readonly")
                self._cmb_chan.configure(state="readonly")
        except Exception:
            pass

    def _show_pattern_detail(self, p: TKBPattern):
        """Mở popup hiển thị TKB pattern dưới dạng GRID giống bảng TKB chính.

        Layout: 7 cột (Thứ 2..7 + CN) × 10 hàng (Sáng tiết 1-5 + Chiều
        tiết 1-5). Mỗi ô:
        - Có data → hiển thị Lớp + Môn rút gọn + tooltip phân môn.
        - Trống → ô xám nhạt với "—".

        Auto-fit theo screen size: giới hạn tối đa 95% width/height màn hình,
        scroll dọc nếu cần.
        """
        win = tk.Toplevel(self)
        win.title(f"Chi tiết Mẫu {p.pattern_id}")
        win.configure(background=CLR_PANEL_BG)
        win.transient(self)
        win.grab_set()

        # Layout sizes — đủ chứa "Lớp + Môn ngắn", và 7 cột không bị crush
        ROW_HEADER_H = 30
        ROW_BODY_H = 56
        # Separator dày giữa Sáng và Chiều — giúp mắt nhanh phân biệt 2 buổi
        # thay vì hàng tiếp tiếp tục liền mạch (user feedback).
        SEPARATOR_H = 18
        COL_BUOI_W = 60
        COL_TIET_W = 40
        COL_DAY_W = 132   # 7 ngày × 132 = 924 — vừa nhiều màn hình 1366+
        TOTAL_INNER_W = COL_BUOI_W + COL_TIET_W + COL_DAY_W * len(DAYS)
        # Tổng chiều cao = header + 2 × (5 tiết) + 1 separator giữa
        TOTAL_INNER_H = (
            ROW_HEADER_H
            + ROW_BODY_H * 10
            + SEPARATOR_H * (len(TIETS_BY_BUOI) - 1)
        )

        # Fit screen
        scr_w = win.winfo_screenwidth()
        scr_h = win.winfo_screenheight()
        max_w = int(scr_w * 0.95)
        max_h = int(scr_h * 0.90)
        # Padding cho header label + scrollbar + footer
        win_w = min(TOTAL_INNER_W + 40, max_w)
        win_h = min(TOTAL_INNER_H + 110, max_h)
        # Center
        x = max(0, (scr_w - win_w) // 2)
        y = max(0, (scr_h - win_h) // 2)
        win.geometry(f"{win_w}x{win_h}+{x}+{y}")
        win.minsize(min(900, max_w), min(420, max_h))

        # Header
        ttk.Label(
            win,
            text=(
                f"Mẫu {p.pattern_id} — {p.slot_count} ô · "
                f"{p.group_count} nhóm · "
                f"{len(p.weeks)} tuần ({_format_weeks_compact(p.weeks)})"
            ),
            font=("Segoe UI", 12, "bold"), padding=(12, 10),
            style="WizAccent.TLabel",
        ).pack(anchor="w")

        # Outer scroll wrapper
        scroll_wrap = ttk.Frame(win, style="Wiz.TFrame")
        scroll_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        scroll_wrap.columnconfigure(0, weight=1)
        scroll_wrap.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            scroll_wrap, background=CLR_PANEL_BG,
            highlightthickness=0, bd=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        v_sb = ttk.Scrollbar(
            scroll_wrap, orient="vertical", command=canvas.yview,
        )
        v_sb.grid(row=0, column=1, sticky="ns")
        h_sb = ttk.Scrollbar(
            scroll_wrap, orient="horizontal", command=canvas.xview,
        )
        h_sb.grid(row=1, column=0, sticky="ew")
        canvas.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)

        # Inner grid frame
        inner = tk.Frame(canvas, background=CLR_PANEL_BG)
        canvas.create_window(
            (0, 0), window=inner, anchor="nw",
        )

        def _on_inner_configure(_evt=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
        inner.bind("<Configure>", _on_inner_configure)

        # Mouse wheel scroll trên Windows
        def _on_wheel(evt):
            try:
                canvas.yview_scroll(int(-1 * (evt.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind("<Enter>",
                    lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>",
                    lambda e: canvas.unbind_all("<MouseWheel>"))

        # ---- Build header row: Buổi · Tiết · Thứ 2 .. CN ----
        def _make_header(text, x, y, w, h, *, fg=None, bg=None):
            lbl = tk.Label(
                inner, text=text,
                font=("Segoe UI", 10, "bold"),
                background=bg or CLR_GRID_HEADER_BG,
                foreground=fg or CLR_GRID_HEADER_FG,
                borderwidth=1, relief="solid",
                highlightbackground=CLR_GRID_HEADER_BORDER,
            )
            lbl.place(x=x, y=y, width=w, height=h)

        _make_header("Buổi", 0, 0, COL_BUOI_W, ROW_HEADER_H)
        _make_header("Tiết", COL_BUOI_W, 0, COL_TIET_W, ROW_HEADER_H)
        for ci, (thu_num, thu_lbl) in enumerate(DAYS):
            x_col = COL_BUOI_W + COL_TIET_W + ci * COL_DAY_W
            # Đánh dấu CN (8) bằng màu hint
            is_cn = (thu_num == 8)
            _make_header(
                thu_lbl, x_col, 0, COL_DAY_W, ROW_HEADER_H,
                fg="#b00020" if is_cn else None,
            )

        # ---- Build body rows: Sáng/Chiều × Tiết 1..5 × Day cells ----
        # Build slot lookup map for O(1) access
        slot_by_pos: dict[tuple[int, int, int], "SlotEntry" | None] = {}
        for s in p.slots:
            slot_by_pos[(s.thu, s.buoi, s.tiet)] = s

        row_y = ROW_HEADER_H
        for buoi_pos, (buoi_label, buoi_idx, tiets) in enumerate(TIETS_BY_BUOI):
            # Separator stripe giữa Sáng và Chiều — kéo dài full chiều ngang.
            # Buoi đầu tiên KHÔNG có separator phía trên (đã có header).
            if buoi_pos > 0:
                # Strip màu đậm phân chia 2 buổi rõ ràng.
                tk.Frame(
                    inner,
                    background="#3a4a5e",
                    height=2, borderwidth=0,
                ).place(
                    x=0, y=row_y, width=TOTAL_INNER_W, height=2,
                )
                # Khoảng trắng nền panel để mắt thấy "vùng nghỉ" giữa
                # 2 buổi — không có viền cell, không có chữ.
                tk.Frame(
                    inner,
                    background=CLR_PANEL_BG,
                    borderwidth=0,
                ).place(
                    x=0, y=row_y + 2,
                    width=TOTAL_INNER_W,
                    height=SEPARATOR_H - 4,
                )
                tk.Frame(
                    inner,
                    background="#3a4a5e",
                    height=2, borderwidth=0,
                ).place(
                    x=0, y=row_y + SEPARATOR_H - 2,
                    width=TOTAL_INNER_W, height=2,
                )
                row_y += SEPARATOR_H

            tiet_count = len(tiets)
            # Buổi spans tiet_count rows
            buoi_y = row_y
            buoi_h = ROW_BODY_H * tiet_count
            # Buổi sáng/chiều có nền + icon riêng để dễ nhận diện
            if buoi_idx == 1:
                buoi_bg = "#ffd9a8"           # cam đậm hơn (trước: #fff5e6)
                buoi_fg = "#7a4a18"
                buoi_icon = "☀"
            else:
                buoi_bg = "#bcdaf2"           # xanh đậm hơn (trước: #e6f4ff)
                buoi_fg = "#1c4e7a"
                buoi_icon = "🌙"
            tk.Label(
                inner, text=f"{buoi_icon}\n{buoi_label}",
                font=("Segoe UI", 11, "bold"),
                background=buoi_bg, foreground=buoi_fg,
                borderwidth=1, relief="solid",
                highlightbackground=CLR_GRID_HEADER_BORDER,
                justify="center",
            ).place(x=0, y=buoi_y, width=COL_BUOI_W, height=buoi_h)

            # Mỗi tiết
            for ti_pos, tiet in enumerate(tiets):
                cell_y = buoi_y + ti_pos * ROW_BODY_H
                # Tiết label — tô đậm khi cùng nền với buổi để khớp tone
                tiet_bg = (
                    "#ffefd5" if buoi_idx == 1 else "#dceefb"
                )
                tk.Label(
                    inner, text=str(tiet),
                    font=("Segoe UI", 11, "bold"),
                    background=tiet_bg,
                    foreground=buoi_fg,
                    borderwidth=1, relief="solid",
                    highlightbackground=CLR_GRID_HEADER_BORDER,
                ).place(
                    x=COL_BUOI_W, y=cell_y,
                    width=COL_TIET_W, height=ROW_BODY_H,
                )
                # 7 day cells
                for ci, (thu_num, _thu_lbl) in enumerate(DAYS):
                    cell_x = (
                        COL_BUOI_W + COL_TIET_W + ci * COL_DAY_W
                    )
                    s = slot_by_pos.get((thu_num, buoi_idx, tiet))
                    if s is not None:
                        self._render_filled_cell(
                            inner, s, cell_x, cell_y,
                            COL_DAY_W, ROW_BODY_H,
                        )
                    else:
                        # Empty cell — light grey, "—" mờ
                        # Nhuộm tone nhẹ theo buổi để empty vẫn cảm thấy
                        # thuộc đúng nhóm buổi sáng/chiều.
                        empty_bg = (
                            "#fafafa" if buoi_idx == 1 else "#f5f8fb"
                        )
                        tk.Label(
                            inner, text="—",
                            font=("Segoe UI", 11),
                            background=empty_bg, foreground="#bbb",
                            borderwidth=1, relief="solid",
                            highlightbackground=CLR_BORDER,
                        ).place(
                            x=cell_x, y=cell_y,
                            width=COL_DAY_W, height=ROW_BODY_H,
                        )

            row_y += buoi_h

        # Configure inner total size to enable scrolling
        inner.configure(
            width=TOTAL_INNER_W,
            height=TOTAL_INNER_H,
        )

        # Footer — chỉ nút Đóng
        ftr = ttk.Frame(win, padding=(10, 4, 10, 10), style="Wiz.TFrame")
        ftr.pack(fill="x")
        ttk.Button(
            ftr, text="Đóng", command=win.destroy,
            style="WizSubtle.TButton",
        ).pack(side="right")
        # Esc đóng popup
        win.bind("<Escape>", lambda _e: win.destroy())

    @staticmethod
    def _render_filled_cell(
        parent: tk.Widget, slot: "SlotEntry",
        x: int, y: int, w: int, h: int,
    ):
        """Render 1 cell có data trong grid pattern detail.

        - Hiển thị 2 dòng: Lớp (đậm) + Môn rút gọn.
        - Background = `CLR_SLOT_FILLED` để khớp với grid TKB chính.
        - Tooltip bằng dòng phụ chữ nhỏ (phân môn) ngay bên dưới — vì
          Tk không có native tooltip, embed thẳng vào text giúp UX rõ
          hơn so với hover.
        """
        mon_short = slot.mon_text or ""
        if len(mon_short) > 18:
            mon_short = mon_short[:16] + "…"
        pm_short = slot.phan_mon_text or ""
        if len(pm_short) > 22:
            pm_short = pm_short[:20] + "…"

        # Frame background
        cell = tk.Frame(
            parent,
            background=CLR_SLOT_FILLED,
            borderwidth=1, relief="solid",
            highlightbackground=CLR_SLOT_FILLED_BORDER,
            highlightthickness=1,
        )
        cell.place(x=x, y=y, width=w, height=h)

        # Lớp (đậm)
        tk.Label(
            cell, text=slot.lop_text or "",
            font=("Segoe UI", 10, "bold"),
            background=CLR_SLOT_FILLED,
            foreground=CLR_SLOT_FILLED_TEXT,
        ).place(x=4, y=2, width=w - 8, height=18)

        # Môn (regular)
        tk.Label(
            cell, text=mon_short,
            font=("Segoe UI", 9),
            background=CLR_SLOT_FILLED,
            foreground=CLR_SLOT_FILLED_TEXT,
            anchor="w",
        ).place(x=4, y=20, width=w - 8, height=16)

        # Phân môn (chữ nhỏ, dim)
        if pm_short:
            tk.Label(
                cell, text=pm_short,
                font=("Segoe UI", 8),
                background=CLR_SLOT_FILLED,
                foreground="#5a7390",
                anchor="w",
            ).place(x=4, y=36, width=w - 8, height=14)

    def _on_apply(self):
        """Validate user choice + set self.imported + close."""
        if not self._patterns or not self._patterns.patterns:
            messagebox.showwarning(
                "Chưa có mẫu", "Chưa có mẫu TKB nào để áp dụng.",
                parent=self,
            )
            return
        mode = self.var_apply_mode.get()
        if mode == "single":
            pid = (self.var_pick_single.get() or "").strip()
            if not pid:
                messagebox.showwarning(
                    "Chưa chọn", "Hãy chọn mẫu để áp dụng.", parent=self,
                )
                return
            p = self._find_pattern(pid)
            if p is None:
                messagebox.showerror(
                    "Lỗi", f"Không tìm thấy mẫu {pid}.", parent=self,
                )
                return
            self.imported = {"mode": "single", "pattern": p}
        else:
            lid = (self.var_pick_le.get() or "").strip()
            cid = (self.var_pick_chan.get() or "").strip()
            if not lid or not cid:
                messagebox.showwarning(
                    "Chưa đủ", "Hãy chọn mẫu cho cả tuần lẻ và tuần chẵn.",
                    parent=self,
                )
                return
            if lid == cid:
                if not messagebox.askyesno(
                    "Mẫu trùng nhau",
                    (
                        "Bạn chọn cùng 1 mẫu cho cả tuần lẻ và tuần chẵn — "
                        "tương đương với 'Áp 1 mẫu cho tất cả'.\n\n"
                        "Tiếp tục theo cách 'Tách lẻ/chẵn' (cấu trúc "
                        "tách nhưng dữ liệu giống nhau) chứ?"
                    ),
                    parent=self,
                ):
                    return
            le_p = self._find_pattern(lid)
            chan_p = self._find_pattern(cid)
            if le_p is None or chan_p is None:
                messagebox.showerror(
                    "Lỗi", "Không tìm thấy 1 trong các mẫu đã chọn.",
                    parent=self,
                )
                return
            self.imported = {
                "mode": "le_chan",
                "pattern_le": le_p,
                "pattern_chan": chan_p,
            }
        # Cleanup + close
        self._cleanup_and_close()

    def _find_pattern(self, pid: str) -> "TKBPattern | None":
        if not self._patterns:
            return None
        for p in self._patterns.patterns:
            if p.pattern_id == pid:
                return p
        return None

    def _on_cancel(self):
        self.imported = None
        self._cleanup_and_close()

    def _on_close_request(self):
        # X button → giống Cancel
        self._on_cancel()

    def _on_esc(self, _event=None):
        # Nếu đang quét → Stop. Nếu đã xong → Cancel.
        if self._worker and self._worker.is_alive():
            self._on_stop()
        else:
            self._on_cancel()

    def _on_stop(self):
        self._stop_event.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Đang dừng…")

    def _cleanup_and_close(self):
        # Cancel after callback
        if self._poll_after_id:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        # Signal stop + give thread short time to die
        try:
            self._stop_event.set()
        except Exception:
            pass
        try:
            if self._worker and self._worker.is_alive():
                self._worker.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def _format_week_runs(
    weeks: list[int],
    *,
    empty_text: str = "",
    dash: str = "-",
) -> str:
    """Gom danh sách tuần thành dải liên tiếp. [1,2,3,5,6,8] → '1-3, 5-6, 8'.

    Helper DUY NHẤT cho toàn bộ format tuần (#6 refactor: trước đây có 3
    bản sao cùng thuật toán ở ImportTKBDialog / BackupRestoreDialog /
    KHDHWizard, chỉ khác `empty_text` và ký tự gạch). Tham số hóa 2 điểm
    khác biệt đó để 1 nguồn sự thật.

    Args:
        weeks: danh sách số tuần (có thể trùng, không sort sẵn).
        empty_text: chuỗi trả về khi rỗng (vd "", "(không có)", "(rỗng)").
        dash: ký tự nối dải ("-" hoặc en-dash "–").
    """
    if not weeks:
        return empty_text
    sw = sorted(set(int(w) for w in weeks))
    runs: list[tuple[int, int]] = []
    start = prev = sw[0]
    for w in sw[1:]:
        if w == prev + 1:
            prev = w
        else:
            runs.append((start, prev))
            start = prev = w
    runs.append((start, prev))
    return ", ".join(
        str(a) if a == b else f"{a}{dash}{b}" for a, b in runs
    )


def _format_weeks_compact(weeks: list[int]) -> str:
    """Format compact: [1,2,3,5,6,8] → '1-3, 5-6, 8'. Rỗng → ''."""
    return _format_week_runs(weeks, empty_text="", dash="-")

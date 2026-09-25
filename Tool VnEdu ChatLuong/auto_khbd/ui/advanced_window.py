"""Cửa sổ Nâng cao."""

from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ..engine.analyzer.models import PATTERN_BOTH, PATTERN_CHAN_ONLY, PATTERN_LE_ONLY, PATTERN_NONE
from ..engine.analyzer.pattern_analyzer import PatternAnalyzer
from ..engine.analyzer.reports import YearScanReport
from ..engine.catalog import LessonCatalog
from .dialogs.import_tkb import _format_week_runs
from .styles import apply_wizard_styles
from .theme import CLR_PANEL_BG
from .workers.scan import ScanWorker

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from .wizard.wizard import KHDHWizard


# =====================================================================
# Window — AdvancedWindow (Tab Nâng cao - popup riêng)
# =====================================================================

class AdvancedWindow(tk.Toplevel):
    """Cửa sổ Nâng cao — quét toàn năm, xem patterns + sổ tên bài.

    Mở bằng nút "🔧 Nâng cao" trong KHDHWizard. Là cửa sổ riêng để không
    làm rối main view.
    """

    def __init__(self, parent, port: int, wizard: "KHDHWizard | None" = None):
        super().__init__(parent)
        self.parent = parent
        self.port = port
        self._wizard = wizard  # reference để check CDP guard
        self.title("🔧 Nâng cao — Phân tích KHDH")
        self.geometry("1200x780")
        self.configure(background=CLR_PANEL_BG)
        apply_wizard_styles(ttk.Style(self))

        self.report: YearScanReport | None = None
        self.catalog: LessonCatalog | None = None
        self.weeks_data: dict = {}
        self._breakdown: dict[str, dict] = {}    # subject_breakdown cache
        self._warnings: list[dict] = []          # health warnings cache

        self._scan_worker: ScanWorker | None = None
        self._scan_queue: queue.Queue = queue.Queue()
        self._scan_stop_event = threading.Event()

        self.var_tuan_from = tk.IntVar(value=1)
        self.var_tuan_to = tk.IntVar(value=40)
        self.var_status = tk.StringVar(value="Bấm [Quét toàn năm] để bắt đầu phân tích.")
        self.var_progress = tk.IntVar(value=0)
        self.var_progress_text = tk.StringVar(value="")
        # Search/filter vars cho từng tab
        self.var_search_progress = tk.StringVar(value="")
        self.var_search_catalog = tk.StringVar(value="")
        self.var_search_warnings = tk.StringVar(value="")
        # Debounce timer IDs — clear timer cũ khi user gõ tiếp để tránh
        # rebuild treeview liên tục (lag với catalog 500+ entries).
        self._search_after_progress: str | None = None
        self._search_after_catalog: str | None = None
        self._search_after_warnings: str | None = None
        self._SEARCH_DEBOUNCE_MS = 200
        self.var_search_progress.trace_add(
            "write",
            lambda *_: self._debounce_filter("progress"),
        )
        self.var_search_catalog.trace_add(
            "write",
            lambda *_: self._debounce_filter("catalog"),
        )
        self.var_search_warnings.trace_add(
            "write",
            lambda *_: self._debounce_filter("warnings"),
        )

        self._build_ui()

        self.transient(parent)
        self.bind("<Escape>", lambda e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        body = ttk.Frame(self, padding=10, style="Wiz.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        # Header + scan controls
        ttk.Label(
            body, text="🔧 Phân tích chi tiết KHDH (cho người dùng nâng cao)",
            style="WizTitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        ctrl = ttk.LabelFrame(
            body, text=" Quét dữ liệu năm học ",
            padding=10, style="Wiz.TLabelframe",
        )
        ctrl.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctrl.columnconfigure(99, weight=1)

        r = ttk.Frame(ctrl, style="Wiz.TFrame")
        r.grid(row=0, column=0, sticky="ew")
        ttk.Label(r, text="Từ tuần:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(r, from_=1, to=52, textvariable=self.var_tuan_from,
                   width=5, font=("Segoe UI", 10)).pack(side="left", padx=(4, 8))
        ttk.Label(r, text="đến:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(r, from_=1, to=52, textvariable=self.var_tuan_to,
                   width=5, font=("Segoe UI", 10)).pack(side="left", padx=(4, 14))
        self.btn_scan = ttk.Button(
            r, text="▶ Quét toàn năm", command=self._on_scan_clicked,
            style="WizPrimary.TButton",
        )
        self.btn_scan.pack(side="left", padx=(0, 4))
        self.btn_scan_stop = ttk.Button(
            r, text="■ Dừng", command=self._on_scan_stop,
            state="disabled", style="WizDanger.TButton",
        )
        self.btn_scan_stop.pack(side="left")

        # Progress
        prog_row = ttk.Frame(ctrl, style="Wiz.TFrame")
        prog_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        prog_row.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(
            prog_row, mode="determinate", variable=self.var_progress,
            style="WizBlue.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            prog_row, textvariable=self.var_progress_text,
            style="WizAccent.TLabel",
        ).grid(row=0, column=1, padx=(10, 0))
        ttk.Label(
            ctrl, textvariable=self.var_status, style="Wiz.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        # Notebook 3 tab
        nb = ttk.Notebook(body)
        nb.grid(row=2, column=0, sticky="nsew")

        tab_pat = ttk.Frame(nb, padding=6, style="Wiz.TFrame")
        nb.add(tab_pat, text="  📊  TKB Patterns + Tiến độ  ")
        self._build_patterns_tab(tab_pat)

        tab_cat = ttk.Frame(nb, padding=6, style="Wiz.TFrame")
        nb.add(tab_cat, text="  📚  Sổ tên bài  ")
        self._build_catalog_tab(tab_cat)

        tab_health = ttk.Frame(nb, padding=6, style="Wiz.TFrame")
        nb.add(tab_health, text="  🩺  Sức khỏe dữ liệu  ")
        self._build_health_tab(tab_health)

    def _build_patterns_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        paned = ttk.PanedWindow(parent, orient="vertical")
        paned.grid(row=0, column=0, sticky="nsew")

        # Top — Pattern list
        top = ttk.LabelFrame(paned, text=" Các pattern TKB phát hiện ",
                            padding=6, style="Wiz.TLabelframe")
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        self.tree_pat = ttk.Treeview(
            top, columns=("weeks", "slots", "weeks_count"),
            show="headings", style="Wiz.Treeview",
        )
        self.tree_pat.heading("weeks", text="Áp dụng cho tuần")
        self.tree_pat.heading("slots", text="Số slot")
        self.tree_pat.heading("weeks_count", text="Số tuần")
        self.tree_pat.column("weeks", width=300, anchor="w")
        self.tree_pat.column("slots", width=80, anchor="center")
        self.tree_pat.column("weeks_count", width=80, anchor="center")
        self.tree_pat.grid(row=0, column=0, sticky="nsew")
        sb1 = ttk.Scrollbar(top, orient="vertical", command=self.tree_pat.yview)
        sb1.grid(row=0, column=1, sticky="ns")
        self.tree_pat.configure(yscrollcommand=sb1.set)
        paned.add(top, weight=1)

        # Bottom — Progress per (lop, mon, phan_mon) + search/export
        bot = ttk.LabelFrame(
            paned, text=" Tiến độ PPCT theo (Lớp × Phân môn) ",
            padding=6, style="Wiz.TLabelframe",
        )
        bot.columnconfigure(0, weight=1)
        bot.rowconfigure(1, weight=1)

        # Search + export row
        bot_top = ttk.Frame(bot, style="Wiz.TFrame")
        bot_top.grid(row=0, column=0, sticky="ew", pady=(0, 4), columnspan=2)
        ttk.Label(bot_top, text="🔎 Lọc:", style="Wiz.TLabel").pack(side="left")
        ttk.Entry(
            bot_top, textvariable=self.var_search_progress, width=30,
        ).pack(side="left", padx=(4, 8))
        ttk.Button(
            bot_top, text="📥 Xuất CSV",
            command=lambda: self._export_csv("progress"),
            style="WizSubtle.TButton",
        ).pack(side="right")

        self.tree_prog = ttk.Treeview(
            bot, columns=(
                "lop", "mon", "phan_mon", "last_ppct",
                "last_tuan", "next", "history", "le_chan", "gaps",
            ),
            show="headings", style="Wiz.Treeview",
        )
        for col, label, w, anchor in [
            ("lop", "Lớp", 60, "center"),
            ("mon", "Môn học", 180, "w"),
            ("phan_mon", "Phân môn", 180, "w"),
            ("last_ppct", "PPCT cuối", 80, "center"),
            ("last_tuan", "Tuần cuối", 70, "center"),
            ("next", "PPCT tiếp", 80, "center"),
            ("history", "Số bài", 60, "center"),
            ("le_chan", "Lẻ/Chẵn", 100, "center"),
            ("gaps", "Thiếu PPCT", 110, "w"),
        ]:
            self.tree_prog.heading(col, text=label)
            self.tree_prog.column(col, width=w, anchor=anchor)
        self.tree_prog.grid(row=1, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(bot, orient="vertical", command=self.tree_prog.yview)
        sb2.grid(row=1, column=1, sticky="ns")
        self.tree_prog.configure(yscrollcommand=sb2.set)
        # Tag color cho row có gaps / pattern lẻ-chỉ / chẵn-chỉ
        # (Tag names có prefix `pat_` để phân biệt với pattern enum constants
        # ở module-level — tránh nhầm lẫn dù value trùng.)
        self.tree_prog.tag_configure("has_gap", foreground="#b00020")
        self.tree_prog.tag_configure("pat_le_only", foreground="#a07000")
        self.tree_prog.tag_configure("pat_chan_only", foreground="#0070a0")
        paned.add(bot, weight=2)

    def _build_catalog_tab(self, parent):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text="Tên bài tự động tổng hợp từ các tuần đã có dữ liệu trên web. "
                 "Dùng làm gợi ý tên bài khi bạn nhập KHDH cho các tuần mới.",
            style="WizHint.TLabel", wraplength=900, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        # Search + export
        cat_top = ttk.Frame(parent, style="Wiz.TFrame")
        cat_top.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(cat_top, text="🔎 Lọc:", style="Wiz.TLabel").pack(side="left")
        ttk.Entry(
            cat_top, textvariable=self.var_search_catalog, width=30,
        ).pack(side="left", padx=(4, 8))
        ttk.Button(
            cat_top, text="📥 Xuất CSV",
            command=lambda: self._export_csv("catalog"),
            style="WizSubtle.TButton",
        ).pack(side="right")

        wrap = ttk.Frame(parent, style="Wiz.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.tree_cat = ttk.Treeview(
            wrap, columns=("lop", "mon", "phan_mon", "ppct", "ten_bai", "weeks"),
            show="headings", style="Wiz.Treeview",
        )
        for col, label, w, anchor, stretch in [
            ("lop", "Lớp", 70, "center", False),
            ("mon", "Môn", 150, "w", False),
            ("phan_mon", "Phân môn", 160, "w", False),
            ("ppct", "PPCT", 60, "center", False),
            ("ten_bai", "Tên bài dạy", 380, "w", True),
            ("weeks", "Tuần đã thấy", 130, "w", False),
        ]:
            self.tree_cat.heading(col, text=label)
            self.tree_cat.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_cat.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree_cat.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_cat.configure(yscrollcommand=sb.set)

    def _build_health_tab(self, parent):
        """Tab thứ 3 — hiển thị các cảnh báo/khuyến cáo về sức khỏe data."""
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text=(
                "Tool quét data web và phát hiện các vấn đề tiềm ẩn: PPCT "
                "trùng, PPCT thiếu (gap), tuần giữa năm bị trống, nhóm môn "
                "chỉ dạy ở tuần lẻ/chẵn, nhóm có data nhưng không trong "
                "profile, v.v."
            ),
            style="WizHint.TLabel", wraplength=900, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        h_top = ttk.Frame(parent, style="Wiz.TFrame")
        h_top.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(h_top, text="🔎 Lọc:", style="Wiz.TLabel").pack(side="left")
        ttk.Entry(
            h_top, textvariable=self.var_search_warnings, width=30,
        ).pack(side="left", padx=(4, 8))
        ttk.Button(
            h_top, text="📥 Xuất CSV",
            command=lambda: self._export_csv("warnings"),
            style="WizSubtle.TButton",
        ).pack(side="right")

        wrap = ttk.Frame(parent, style="Wiz.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.tree_health = ttk.Treeview(
            wrap, columns=("severity", "type", "title", "message"),
            show="headings", style="Wiz.Treeview",
        )
        for col, label, w, anchor, stretch in [
            ("severity", "Mức độ", 80, "center", False),
            ("type", "Loại", 130, "w", False),
            ("title", "Tóm tắt", 380, "w", False),
            ("message", "Chi tiết", 460, "w", True),
        ]:
            self.tree_health.heading(col, text=label)
            self.tree_health.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_health.grid(row=0, column=0, sticky="nsew")
        sb_h = ttk.Scrollbar(wrap, orient="vertical", command=self.tree_health.yview)
        sb_h.grid(row=0, column=1, sticky="ns")
        self.tree_health.configure(yscrollcommand=sb_h.set)
        # Color tags theo severity
        self.tree_health.tag_configure("error", foreground="#b00020")
        self.tree_health.tag_configure("warning", foreground="#a07000")
        self.tree_health.tag_configure("info", foreground="#0070a0")

    # ----------------------------------------------------------
    # Scan flow
    # ----------------------------------------------------------

    def _on_scan_clicked(self):
        if self._scan_worker and self._scan_worker.is_alive():
            return
        # Guard: check wizard's CDP workers (tránh race với connect/detect/exec)
        if self._wizard and self._wizard._is_any_cdp_worker_busy():
            messagebox.showinfo(
                "Đang bận",
                "Cửa sổ chính đang chạy tác vụ trên Chrome.\n"
                "Hãy chờ hoàn tất rồi quét lại.",
                parent=self,
            )
            return
        try:
            tf = int(self.var_tuan_from.get())
            tt = int(self.var_tuan_to.get())
            if not (1 <= tf <= tt <= 52):
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror("Lỗi", "Tuần từ/đến không hợp lệ.", parent=self)
            return

        self._scan_stop_event.clear()
        self._scan_queue = queue.Queue()
        self._scan_worker = ScanWorker(
            port=self.port, tuan_from=tf, tuan_to=tt,
            event_queue=self._scan_queue, stop_event=self._scan_stop_event,
        )
        self._scan_worker.start()
        self.btn_scan.configure(state="disabled")
        self.btn_scan_stop.configure(state="normal")
        self.progress_bar.configure(maximum=tt - tf + 1)
        self.var_progress.set(0)
        self.var_progress_text.set(f"0 / {tt - tf + 1}")
        self._poll_scan()

    def _on_scan_stop(self):
        if self._scan_worker and self._scan_worker.is_alive():
            self._scan_stop_event.set()
            self.btn_scan_stop.configure(state="disabled")

    def _poll_scan(self):
        try:
            while True:
                ev = self._scan_queue.get_nowait()
                try:
                    kind = ev[0]
                    if kind == "status":
                        self.var_status.set(ev[1])
                    elif kind == "progress":
                        # Format mới: (kind, done, total, last_week, label)
                        cur, total, week, info = ev[1], ev[2], ev[3], ev[4]
                        self.var_progress.set(cur)
                        self.var_progress_text.set(f"{cur} / {total}")
                        self.var_status.set(f"Đã quét {cur}/{total} ({info})")
                    elif kind == "done":
                        self.report, self.catalog, self.weeks_data = ev[1], ev[2], ev[3]
                        self._populate_results()
                    elif kind == "error":
                        messagebox.showerror("Lỗi quét", ev[1], parent=self)
                        self.var_status.set("Lỗi.")
                except Exception as e:
                    # Error boundary: không crash poll loop
                    try:
                        self.var_status.set(f"⚠ Lỗi xử lý event: {e}")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        if self._scan_worker and self._scan_worker.is_alive():
            self.after(120, self._poll_scan)
        else:
            self.btn_scan.configure(state="normal")
            self.btn_scan_stop.configure(state="disabled")

    def _populate_results(self):
        if not self.report:
            return

        # 1. Tính breakdown lẻ/chẵn + warnings 1 lần, cache lại
        # Reuse breakdown cho compute_health_warnings để tránh O(n²) recompute.
        self._breakdown = PatternAnalyzer.compute_subject_breakdown(self.weeks_data)
        # Profile của wizard (nếu có) — để so sánh missing/orphan
        profile = getattr(self._wizard, "profile", None) if self._wizard else None
        self._warnings = PatternAnalyzer.compute_health_warnings(
            self.weeks_data, self.report,
            profile=profile,
            breakdown=self._breakdown,
        )

        # 2. Patterns — top
        for c in self.tree_pat.get_children():
            self.tree_pat.delete(c)
        for p in self.report.patterns:
            weeks_text = self._format_weeks(p.weeks)
            self.tree_pat.insert(
                "", "end",
                values=(f"P{p.pattern_id}: Tuần {weeks_text}",
                       len(p.slots), len(p.weeks)),
            )

        # 3. Tiến độ — bottom (filter applied lần đầu)
        self._refilter_progress()

        # 4. Catalog
        self._refilter_catalog()

        # 5. Health warnings
        self._refilter_warnings()

        n_pat = len(self.report.patterns)
        n_prog = len(self.report.subject_progress)
        n_cat = len(self.catalog) if self.catalog else 0
        # Đếm warnings theo severity
        sev_count = {"error": 0, "warning": 0, "info": 0}
        for w in self._warnings:
            sev_count[w.get("severity", "info")] = sev_count.get(
                w.get("severity", "info"), 0) + 1
        warn_text = ""
        if sev_count["error"]:
            warn_text += f", {sev_count['error']} lỗi"
        if sev_count["warning"]:
            warn_text += f", {sev_count['warning']} cảnh báo"
        if sev_count["info"]:
            warn_text += f", {sev_count['info']} thông tin"
        self.var_status.set(
            f"✓ Hoàn tất: {n_pat} pattern, {n_prog} nhóm, "
            f"{n_cat} tên bài{warn_text}"
            + (" — XEM TAB 🩺 NGAY!" if sev_count["error"] else "")
        )

    @staticmethod
    def _format_weeks(weeks: list[int]) -> str:
        return _format_week_runs(weeks, empty_text="(empty)", dash="–")

    # ----------------------------------------------------------
    # Filter / Export
    # ----------------------------------------------------------

    def _debounce_filter(self, kind: str):
        """Schedule rebuild treeview sau `_SEARCH_DEBOUNCE_MS` ms.

        Mỗi lần user gõ thêm ký tự → cancel timer cũ, tạo timer mới.
        Tránh rebuild liên tục với catalog/progress lớn.

        Args:
            kind: "progress" | "catalog" | "warnings"
        """
        attr_name = f"_search_after_{kind}"
        cur_id = getattr(self, attr_name, None)
        if cur_id:
            try:
                self.after_cancel(cur_id)
            except Exception:
                pass
        method = {
            "progress": self._refilter_progress,
            "catalog": self._refilter_catalog,
            "warnings": self._refilter_warnings,
        }.get(kind)
        if not method:
            return
        new_id = self.after(self._SEARCH_DEBOUNCE_MS, method)
        setattr(self, attr_name, new_id)

    def _refilter_progress(self):
        """Render lại tree_prog với filter từ var_search_progress."""
        if not hasattr(self, "tree_prog") or not self.report:
            return
        for c in self.tree_prog.get_children():
            self.tree_prog.delete(c)
        q = self.var_search_progress.get().strip().lower()
        items = sorted(
            self.report.subject_progress.values(),
            key=lambda x: (x.lop_text or x.lop_id, x.mon_text or x.mon_id),
        )
        for p in items:
            row_text = (
                f"{p.lop_text or p.lop_id} {p.mon_text or p.mon_id} "
                f"{p.phan_mon_text or p.phan_mon_id}"
            ).lower()
            if q and q not in row_text:
                continue
            key = f"{p.lop_id}|{p.mon_id}|{p.phan_mon_id}"
            bd = self._breakdown.get(key, {})
            pattern = bd.get("pattern", PATTERN_NONE)
            le_chan_text = {
                PATTERN_BOTH: "Cả 2",
                PATTERN_LE_ONLY: "Chỉ LẺ",
                PATTERN_CHAN_ONLY: "Chỉ CHẴN",
                PATTERN_NONE: "—",
            }.get(pattern, "—")
            gaps = bd.get("gaps", []) or []
            if gaps:
                gaps_text = ", ".join(str(x) for x in gaps[:6])
                if len(gaps) > 6:
                    gaps_text += f" (+{len(gaps) - 6})"
            else:
                gaps_text = "—"
            tags = []
            if gaps:
                tags.append("has_gap")
            if pattern == PATTERN_LE_ONLY:
                tags.append("pat_le_only")
            elif pattern == PATTERN_CHAN_ONLY:
                tags.append("pat_chan_only")
            self.tree_prog.insert(
                "", "end",
                values=(
                    p.lop_text or p.lop_id,
                    p.mon_text or p.mon_id,
                    p.phan_mon_text or p.phan_mon_id,
                    p.last_ppct,
                    f"Tuần {p.last_tuan}" if p.last_tuan else "—",
                    p.next_ppct,
                    len(p.ppct_history),
                    le_chan_text,
                    gaps_text,
                ),
                tags=tuple(tags),
            )

    def _refilter_catalog(self):
        """Render lại tree_cat với filter từ var_search_catalog."""
        if not hasattr(self, "tree_cat") or not self.catalog:
            return
        for c in self.tree_cat.get_children():
            self.tree_cat.delete(c)
        q = self.var_search_catalog.get().strip().lower()
        for e in sorted(
            self.catalog.all_entries(),
            key=lambda x: (x.lop_id, x.mon_id, x.phan_mon_id, x.ppct),
        ):
            row_text = (
                f"{e.lop_text or e.lop_id} {e.mon_text or e.mon_id} "
                f"{e.phan_mon_text or e.phan_mon_id} {e.ten_bai or ''}"
            ).lower()
            if q and q not in row_text:
                continue
            weeks_str = self._format_weeks(
                [w for w in (e.seen_in_weeks or []) if w]
            )
            self.tree_cat.insert(
                "", "end",
                values=(
                    e.lop_text or e.lop_id,
                    e.mon_text or e.mon_id,
                    e.phan_mon_text or e.phan_mon_id,
                    e.ppct, e.ten_bai,
                    weeks_str if weeks_str != "(empty)" else "—",
                ),
            )

    def _refilter_warnings(self):
        """Render lại tree_health với filter từ var_search_warnings."""
        if not hasattr(self, "tree_health"):
            return
        for c in self.tree_health.get_children():
            self.tree_health.delete(c)
        q = self.var_search_warnings.get().strip().lower()
        sev_label = {"error": "❌ Lỗi", "warning": "⚠ Cảnh báo", "info": "ℹ Thông tin"}
        for w in self._warnings:
            row_text = (
                f"{w.get('title', '')} {w.get('message', '')} "
                f"{w.get('type', '')}"
            ).lower()
            if q and q not in row_text:
                continue
            sev = w.get("severity", "info")
            self.tree_health.insert(
                "", "end",
                values=(
                    sev_label.get(sev, sev),
                    w.get("type", ""),
                    w.get("title", ""),
                    w.get("message", ""),
                ),
                tags=(sev,),
            )

    def _export_csv(self, kind: str):
        """Xuất 1 trong 3 bảng ra file CSV (UTF-8 BOM cho Excel mở đúng).

        Args:
            kind: "progress" | "catalog" | "warnings"
        """
        from tkinter import filedialog
        if not self.report:
            messagebox.showinfo(
                "Chưa có dữ liệu",
                "Hãy bấm [Quét toàn năm] trước khi xuất CSV.",
                parent=self,
            )
            return

        suggested_names = {
            "progress": "tien_do_ppct.csv",
            "catalog": "so_ten_bai.csv",
            "warnings": "canh_bao_du_lieu.csv",
        }
        path = filedialog.asksaveasfilename(
            parent=self,
            title=f"Xuất {kind} ra CSV",
            defaultextension=".csv",
            initialfile=suggested_names.get(kind, "export.csv"),
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            # UTF-8 BOM để Excel mở không bị lỗi tiếng Việt
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if kind == "progress":
                    writer.writerow([
                        "Lớp", "Mã lớp", "Môn", "Mã môn",
                        "Phân môn", "Mã phân môn",
                        "PPCT cuối", "Tuần cuối", "PPCT tiếp",
                        "Số bài", "Lẻ/Chẵn", "Thiếu PPCT",
                    ])
                    for p in sorted(
                        self.report.subject_progress.values(),
                        key=lambda x: (x.lop_text or x.lop_id, x.mon_text or x.mon_id),
                    ):
                        key = f"{p.lop_id}|{p.mon_id}|{p.phan_mon_id}"
                        bd = self._breakdown.get(key, {})
                        pattern = bd.get("pattern", PATTERN_NONE)
                        le_chan_text = {
                            PATTERN_BOTH: "Cả 2",
                            PATTERN_LE_ONLY: "Chỉ LẺ",
                            PATTERN_CHAN_ONLY: "Chỉ CHẴN",
                            PATTERN_NONE: "—",
                        }.get(pattern, "—")
                        gaps = bd.get("gaps", []) or []
                        gaps_text = ", ".join(str(x) for x in gaps) if gaps else ""
                        writer.writerow([
                            p.lop_text or p.lop_id, p.lop_id,
                            p.mon_text or p.mon_id, p.mon_id,
                            p.phan_mon_text or p.phan_mon_id, p.phan_mon_id,
                            p.last_ppct,
                            p.last_tuan or "",
                            p.next_ppct,
                            len(p.ppct_history),
                            le_chan_text,
                            gaps_text,
                        ])
                elif kind == "catalog":
                    writer.writerow([
                        "Lớp", "Mã lớp", "Môn", "Mã môn",
                        "Phân môn", "Mã phân môn",
                        "PPCT", "Tên bài dạy", "Tuần đã thấy",
                    ])
                    if self.catalog:
                        for e in sorted(
                            self.catalog.all_entries(),
                            key=lambda x: (x.lop_id, x.mon_id, x.phan_mon_id, x.ppct),
                        ):
                            weeks_str = self._format_weeks(
                                [w for w in (e.seen_in_weeks or []) if w]
                            )
                            writer.writerow([
                                e.lop_text or e.lop_id, e.lop_id,
                                e.mon_text or e.mon_id, e.mon_id,
                                e.phan_mon_text or e.phan_mon_id, e.phan_mon_id,
                                e.ppct, e.ten_bai,
                                weeks_str if weeks_str != "(empty)" else "",
                            ])
                elif kind == "warnings":
                    writer.writerow([
                        "Mức độ", "Loại", "Tóm tắt", "Chi tiết",
                    ])
                    for w in self._warnings:
                        writer.writerow([
                            w.get("severity", ""),
                            w.get("type", ""),
                            w.get("title", ""),
                            w.get("message", ""),
                        ])
                else:
                    return
            messagebox.showinfo(
                "Đã xuất CSV",
                f"Đã ghi: {path}\n\nMở bằng Excel để xem (đã set UTF-8 BOM).",
                parent=self,
            )
        except Exception as e:
            messagebox.showerror(
                "Lỗi xuất CSV",
                f"Không ghi được file:\n{type(e).__name__}: {e}",
                parent=self,
            )

    def _on_close(self):
        # Cancel pending debounce timers — tránh leak callbacks vào widget
        # đã destroyed (gây Tkinter error sau close).
        for attr in ("_search_after_progress", "_search_after_catalog",
                     "_search_after_warnings"):
            cur_id = getattr(self, attr, None)
            if cur_id:
                try:
                    self.after_cancel(cur_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._scan_worker and self._scan_worker.is_alive():
            self._scan_stop_event.set()
            # Đợi tối đa 2s rồi đóng
            self._scan_worker.join(timeout=2)
        self.destroy()

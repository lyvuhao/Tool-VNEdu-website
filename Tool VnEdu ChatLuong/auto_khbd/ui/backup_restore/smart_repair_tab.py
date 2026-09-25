"""Tab Sửa thông minh (Smart Repair)."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ...engine.backup.smart_repair import SmartRepairReport
from ..theme import CLR_OK, CLR_WARN, DEFAULT_CDP_PORT
from ..workers.smart_repair import SmartRepairWorker

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.executor.models import ExecutorReport


class SmartRepairTabMixin:
    """Tab Sửa thông minh (Smart Repair)."""

    # -----------------------------------------------------------
    # Tab 4: Sửa thông minh (Smart Repair)
    # -----------------------------------------------------------

    def _build_smart_repair_tab(self, parent: ttk.Frame):
        # Init state khi build (vars)
        self.var_sr_anchor = tk.IntVar(value=1)
        self.var_sr_to = tk.IntVar(value=36)
        self.var_sr_status = tk.StringVar(
            value="Chọn tuần MỐC (tuần cuối cùng có PPCT đúng) → bấm "
                  "[Phân tích đề xuất] để công cụ tính PPCT đúng cuốn chiếu."
        )
        self.var_sr_progress = tk.IntVar(value=0)
        self._sr_report: SmartRepairReport | None = None
        self._sr_action_check_vars: dict[str, tk.BooleanVar] = {}
        self._sr_worker: SmartRepairWorker | None = None
        self._sr_queue: queue.Queue = queue.Queue()
        self._sr_stop = threading.Event()

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        ttk.Label(
            parent,
            text=(
                "Sửa PPCT lệch sau khi bạn nhập sai 1 tuần:\n"
                "  1. Chọn tuần MỐC — tuần cuối cùng có PPCT ĐÚNG\n"
                "  2. Bấm [Phân tích] → công cụ quét tất cả tuần > mốc, "
                "tính PPCT đúng cuốn chiếu (tự nhận diện chu kỳ lẻ/chẵn)\n"
                "  3. Duyệt bảng đề xuất, đánh dấu ô muốn sửa\n"
                "  4. Bấm [Áp dụng] → công cụ sao lưu nhanh + ghi đè web tự động"
            ),
            wraplength=920, justify="left",
            style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        # Anchor selection
        anc_frm = ttk.LabelFrame(
            parent, text=" Tuần Mốc (tuần cuối cùng có PPCT đúng) ",
            padding=10, style="Wiz.TLabelframe",
        )
        anc_frm.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        r1 = ttk.Frame(anc_frm, style="Wiz.TFrame")
        r1.pack(fill="x")
        ttk.Label(r1, text="Tuần mốc:", style="Wiz.TLabel").pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52, textvariable=self.var_sr_anchor,
            width=5, font=("Segoe UI", 10),
        ).pack(side="left", padx=(4, 14))
        ttk.Label(
            r1, text="đến tuần cuối:", style="Wiz.TLabel",
        ).pack(side="left")
        ttk.Spinbox(
            r1, from_=1, to=52, textvariable=self.var_sr_to,
            width=5, font=("Segoe UI", 10),
        ).pack(side="left", padx=(4, 14))
        ttk.Label(
            r1,
            text="(Chu kỳ lẻ/chẵn được học từ toàn bộ dữ liệu tuần ≤ mốc)",
            style="WizHint.TLabel",
        ).pack(side="left")

        action1 = ttk.Frame(parent, style="Wiz.TFrame")
        action1.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.btn_sr_analyze = ttk.Button(
            action1, text="🔍 Phân tích đề xuất",
            command=self._on_sr_analyze_clicked,
            style="WizPrimary.TButton",
        )
        self.btn_sr_analyze.pack(side="left")
        self.btn_sr_stop = ttk.Button(
            action1, text="■ Dừng",
            command=self._on_sr_stop_clicked,
            state="disabled",
            style="WizDanger.TButton",
        )
        self.btn_sr_stop.pack(side="left", padx=(8, 0))

        # Preview tree
        tree_frm = ttk.LabelFrame(
            parent, text=" Đề xuất sửa ",
            padding=8, style="Wiz.TLabelframe",
        )
        tree_frm.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        tree_frm.rowconfigure(1, weight=1)
        tree_frm.columnconfigure(0, weight=1)

        bar = ttk.Frame(tree_frm, style="Wiz.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(
            bar, text="✓ Chọn tất cả ô cần sửa",
            command=lambda: self._sr_set_all_actions(True),
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="✗ Bỏ chọn",
            command=lambda: self._sr_set_all_actions(False),
            style="WizSubtle.TButton",
        ).pack(side="left")

        cols = ("apply", "tuan", "lop_pm", "ppct_now", "ppct_new", "tb_now", "notes")
        self.tree_sr = ttk.Treeview(
            tree_frm, columns=cols, show="headings",
            style="Wiz.Treeview",
        )
        for col, label, w, anchor, stretch in [
            ("apply",   "Áp dụng",  60,  "center", False),
            ("tuan",    "Tuần",     50,  "center", False),
            ("lop_pm",  "Lớp / Phân môn", 220, "w", False),
            ("ppct_now","PPCT cũ",  70,  "center", False),
            ("ppct_new","PPCT mới", 80,  "center", False),
            ("tb_now",  "Tên bài cũ", 280, "w",  True),
            ("notes",   "Ghi chú",  150, "w",  False),
        ]:
            self.tree_sr.heading(col, text=label)
            self.tree_sr.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_sr.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(
            tree_frm, orient="vertical", command=self.tree_sr.yview,
        )
        sb.grid(row=1, column=1, sticky="ns")
        self.tree_sr.configure(yscrollcommand=sb.set)
        self.tree_sr.bind("<Button-1>", self._on_sr_tree_click)
        self.tree_sr.tag_configure(
            "checked", foreground=CLR_OK, font=("Segoe UI", 9, "bold"),
        )
        self.tree_sr.tag_configure(
            "warn", foreground=CLR_WARN,
        )

        # Apply action
        action2 = ttk.Frame(parent, style="Wiz.TFrame")
        action2.grid(row=4, column=0, sticky="ew", pady=(4, 6))
        self.btn_sr_apply = ttk.Button(
            action2, text="🗑 Xóa dải + Nhập lại (sao lưu nhanh sẵn)",
            command=self._on_sr_apply_clicked,
            state="disabled",
            style="WizDanger.TButton",
        )
        self.btn_sr_apply.pack(side="left")
        ttk.Label(
            action2,
            text=(
                "(Web KHDH yêu cầu xóa toàn dải tuần > mốc rồi nhập lại "
                "— công cụ tự sao lưu trước khi xóa)"
            ),
            style="WizHint.TLabel",
        ).pack(side="left", padx=(8, 0))

        self._sr_progress_bar = ttk.Progressbar(
            parent, mode="determinate", variable=self.var_sr_progress,
            style="WizBlue.Horizontal.TProgressbar",
        )
        self._sr_progress_bar.grid(row=5, column=0, sticky="ew")
        ttk.Label(
            parent, textvariable=self.var_sr_status,
            style="Wiz.TLabel", wraplength=920, justify="left",
        ).grid(row=6, column=0, sticky="w", pady=(4, 0))

    # -----------------------------------------------------------
    # Smart Repair handlers — analyze
    # -----------------------------------------------------------

    def _on_sr_analyze_clicked(self):
        if self._sr_worker is not None and self._sr_worker.is_alive():
            return
        if self.wizard._guard_cdp_exclusive("Sửa thông minh"):
            return
        try:
            anchor = int(self.var_sr_anchor.get())
            tt = int(self.var_sr_to.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Sai", "Tuần không hợp lệ.", parent=self)
            return
        if not (1 <= anchor < tt <= 52):
            messagebox.showerror(
                "Sai khoảng",
                f"Cần 1 ≤ anchor < to ≤ 52. Nhận: anchor={anchor}, to={tt}",
                parent=self,
            )
            return

        self._sr_stop = threading.Event()
        self._sr_queue = queue.Queue()
        try:
            port = int(self.wizard.var_port.get())
        except (tk.TclError, ValueError):
            port = DEFAULT_CDP_PORT
        self._sr_worker = SmartRepairWorker(
            port=port,
            mode="analyze",
            anchor_tuan=anchor,
            tuan_from=1,            # Quét toàn bộ để học pattern
            tuan_to=tt,
            excel_path=self.wizard._profile_path,
            event_queue=self._sr_queue,
            stop_event=self._sr_stop,
        )
        self._sr_worker.start()
        self.btn_sr_analyze.configure(state="disabled")
        self.btn_sr_stop.configure(state="normal")
        self.btn_sr_apply.configure(state="disabled")
        self._sr_progress_bar.configure(maximum=tt)
        self.var_sr_progress.set(0)
        self.var_sr_status.set(f"🔍 Đang quét tuần 1–{tt}…")
        self.after(150, self._poll_sr_queue)

    def _on_sr_stop_clicked(self):
        if self._sr_worker and self._sr_worker.is_alive():
            self._sr_stop.set()
            self.btn_sr_stop.configure(state="disabled")
            self.var_sr_status.set("⏸ Đang dừng…")

    def _poll_sr_queue(self):
        try:
            while True:
                ev = self._sr_queue.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.var_sr_status.set(str(ev[1]))
                elif kind == "progress":
                    _, done, total, label = ev
                    self.var_sr_progress.set(int(done))
                    self.var_sr_status.set(f"Quét {done}/{total} ({label})")
                elif kind == "done_analyze":
                    report = ev[1]
                    self._sr_report = report
                    self._populate_sr_tree(report)
                    n_change = sum(1 for a in report.actions if a.needs_change)
                    n_total = len(report.actions)
                    n_groups = len(report.groups)
                    self.var_sr_status.set(
                        f"✓ Phân tích xong: {n_groups} nhóm, "
                        f"{n_total} ô PPCT cuốn chiếu, "
                        f"{n_change} ô cần sửa."
                        + (f" {len(report.warnings)} cảnh báo." if report.warnings else "")
                    )
                    if n_change > 0:
                        self.btn_sr_apply.configure(state="normal")
                elif kind == "event":
                    xev = ev[1]
                    t = getattr(xev, "event_type", "")
                    if t == "week_done":
                        self.var_sr_status.set(f"Tuần {xev.tuan}: {xev.message}")
                    elif t in ("error", "halt"):
                        self.var_sr_status.set(
                            f"❌ Tuần {xev.tuan}: {xev.message[:200]}"
                        )
                elif kind == "done_apply":
                    self._show_sr_apply_report(ev[1])
                elif kind == "error":
                    self.var_sr_status.set("❌ Lỗi")
                    messagebox.showerror(
                        "Lỗi sửa thông minh",
                        str(ev[1]).splitlines()[0],
                        parent=self,
                    )
        except queue.Empty:
            pass
        if self._sr_worker and self._sr_worker.is_alive():
            self.after(150, self._poll_sr_queue)
        else:
            self.btn_sr_analyze.configure(state="normal")
            self.btn_sr_stop.configure(state="disabled")

    def _populate_sr_tree(self, report: "SmartRepairReport"):
        for iid in self.tree_sr.get_children():
            self.tree_sr.delete(iid)
        self._sr_action_check_vars.clear()
        for i, a in enumerate(report.actions):
            iid = f"act_{i}"
            var = tk.BooleanVar(value=not a.skip)
            self._sr_action_check_vars[iid] = var
            tick = "☑" if var.get() else "☐"
            tag = "checked" if (var.get() and a.needs_change) else (
                "warn" if a.notes else ""
            )
            lop_pm = f"{a.lop_text} / {a.phan_mon_text}"
            tb = a.current_ten_bai
            if len(tb) > 40:
                tb = tb[:38] + "…"
            self.tree_sr.insert(
                "", "end", iid=iid,
                values=(
                    tick, a.tuan, lop_pm,
                    a.current_ppct,
                    a.proposed_ppct if a.needs_change else "(giữ)",
                    tb, a.notes,
                ),
                tags=(tag,) if tag else (),
            )

    def _on_sr_tree_click(self, event):
        region = self.tree_sr.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree_sr.identify_column(event.x)
        if col != "#1":
            return
        iid = self.tree_sr.identify_row(event.y)
        if not iid or iid not in self._sr_action_check_vars:
            return
        var = self._sr_action_check_vars[iid]
        var.set(not var.get())
        self._refresh_sr_tree_row(iid)

    def _refresh_sr_tree_row(self, iid: str):
        var = self._sr_action_check_vars.get(iid)
        if var is None:
            return
        cur = list(self.tree_sr.item(iid, "values"))
        cur[0] = "☑" if var.get() else "☐"
        # Reload from action source
        idx = int(iid.split("_")[1])
        if self._sr_report is None or idx >= len(self._sr_report.actions):
            return
        a = self._sr_report.actions[idx]
        a.skip = not var.get()
        tag = "checked" if (var.get() and a.needs_change) else (
            "warn" if a.notes else ""
        )
        self.tree_sr.item(iid, values=cur, tags=(tag,) if tag else ())

    def _sr_set_all_actions(self, checked: bool):
        if self._sr_report is None:
            return
        for iid, var in self._sr_action_check_vars.items():
            idx = int(iid.split("_")[1])
            a = self._sr_report.actions[idx]
            # Chỉ check các ô needs_change, ô đã đúng giữ uncheck
            target = checked and a.needs_change
            var.set(target)
            self._refresh_sr_tree_row(iid)

    def _on_sr_apply_clicked(self):
        if self._sr_worker is not None and self._sr_worker.is_alive():
            return
        if self._sr_report is None:
            return
        if self.wizard._guard_cdp_exclusive("Áp dụng sửa thông minh"):
            return
        actions = [a for a in self._sr_report.actions
                   if not a.skip and a.needs_change]
        if not actions:
            messagebox.showinfo(
                "Không có gì để áp dụng",
                "Hãy đánh dấu ít nhất 1 ô có PPCT khác đề xuất.",
                parent=self,
            )
            return
        # Tính range delete cho thông báo confirm
        max_action_tuan = max(a.tuan for a in actions)
        delete_range = list(
            range(self._sr_report.anchor_tuan + 1, max_action_tuan + 1),
        )
        # Confirm 1: giải thích cơ chế delete-rồi-reinsert
        if not messagebox.askyesno(
            "⚠ Xác nhận sửa thông minh (XÓA + NHẬP LẠI)",
            (
                f"Web KHDH KHÔNG cho sửa 1 ô đơn lẻ — phải XÓA toàn dải "
                f"tuần > mốc rồi NHẬP LẠI từ đầu.\n\n"
                f"Công cụ sẽ:\n"
                f"  1. Tạo bản sao lưu nhanh (auto)\n"
                f"  2. Đọc dữ liệu hiện tại của T{delete_range[0]}–T{delete_range[-1]}\n"
                f"  3. XÓA {len(delete_range)} tuần "
                f"(T{delete_range[-1]} → T{delete_range[0]})\n"
                f"  4. Lấy tên bài đúng PPCT mới qua API\n"
                f"  5. NHẬP LẠI toàn bộ ô (kể cả ô PPCT đúng — vì đã bị xóa)\n"
                f"  6. Bật fallback dấu cách BẮT BUỘC cho ô không có CSDL tên bài\n\n"
                f"Dừng-khi-lỗi: 1 ô lỗi → DỪNG, dùng bản sao lưu nhanh để khôi phục.\n\n"
                f"Tiếp tục?"
            ),
            icon="warning",
            parent=self,
        ):
            return
        # Confirm 2: cảnh báo cuối cùng — thao tác phá data tạm thời
        if not messagebox.askyesno(
            "⚠ XÁC NHẬN LẦN CUỐI",
            (
                f"Sẽ XÓA {len(delete_range)} tuần "
                f"(T{delete_range[0]}–T{delete_range[-1]}) trên web.\n\n"
                f"Trong vài giây sau khi xóa nhưng TRƯỚC khi nhập lại xong, "
                f"trang KHDH của các tuần này sẽ TRỐNG.\n\n"
                f"Đảm bảo bạn:\n"
                f"  • KHÔNG đóng Chrome / reload tab VnEdu\n"
                f"  • KHÔNG đụng vào module KHDH cho đến khi tool báo xong\n"
                f"  • Đã thấy bản sao lưu nhanh trong tab 📸 (sẽ tạo ngay khi bấm)\n\n"
                f"Tiếp tục?"
            ),
            icon="warning",
            parent=self,
        ):
            return

        self._sr_stop = threading.Event()
        self._sr_queue = queue.Queue()
        try:
            port = int(self.wizard.var_port.get())
        except (tk.TclError, ValueError):
            port = DEFAULT_CDP_PORT
        self._sr_worker = SmartRepairWorker(
            port=port,
            mode="apply",
            anchor_tuan=self._sr_report.anchor_tuan,
            tuan_from=self._sr_report.tuan_from,
            tuan_to=self._sr_report.tuan_to,
            excel_path=self.wizard._profile_path,
            event_queue=self._sr_queue,
            stop_event=self._sr_stop,
            actions_to_apply=actions,
            auto_snapshot=True,
        )
        self._sr_worker.start()
        self.btn_sr_analyze.configure(state="disabled")
        self.btn_sr_apply.configure(state="disabled")
        self.btn_sr_stop.configure(state="normal")
        # Group by week để biết bao nhiêu tuần sẽ chạy
        weeks_count = len({a.tuan for a in actions})
        self._sr_progress_bar.configure(maximum=weeks_count)
        self.var_sr_progress.set(0)
        self.var_sr_status.set(
            f"🚀 Đang áp dụng {len(actions)} ô trên {weeks_count} tuần…"
        )
        self.after(150, self._poll_sr_queue)

    def _show_sr_apply_report(self, exec_report: "ExecutorReport"):
        ok_weeks = sorted(exec_report.successful_weeks)
        fail_weeks = sorted({
            wr.tuan for wr in exec_report.week_results
            if not wr.save_ok and not wr.skipped
        })
        skip_weeks = sorted({
            wr.tuan for wr in exec_report.week_results if wr.skipped
        })
        ok = len(ok_weeks)
        err = len(fail_weeks)

        ok_list = self._format_weeks_compact(ok_weeks)
        fail_list = self._format_weeks_compact(fail_weeks)
        skip_list = self._format_weeks_compact(skip_weeks)

        lines = [
            "📊 KẾT QUẢ SỬA THÔNG MINH",
            "",
            f"✓ Thành công: {ok} tuần",
        ]
        if ok_weeks:
            lines.append(f"   → Tuần: {ok_list}")
        lines.append("")
        lines.append(f"✗ Thất bại: {err} tuần")
        if fail_weeks:
            lines.append(f"   → Tuần: {fail_list}")
            # Detail per failing week
            fail_details = [
                wr for wr in exec_report.week_results
                if not wr.save_ok and not wr.skipped
            ]
            if fail_details:
                lines.append("")
                lines.append("Chi tiết tuần lỗi:")
                for wr in fail_details[:8]:
                    msg = (wr.save_msg or "(không có thông báo)")[:140]
                    lines.append(f"   • Tuần {wr.tuan}: {msg}")
                if len(fail_details) > 8:
                    lines.append(f"   • … và {len(fail_details) - 8} tuần khác")
        lines.append("")
        if skip_weeks:
            lines.append(f"⏭ Bỏ qua: {len(skip_weeks)} tuần")
            lines.append(f"   → Tuần: {skip_list}")
            lines.append("")
        lines.append(
            f"⏱ Thời gian: {exec_report.total_duration_ms / 1000:.1f}s"
        )

        if err == 0:
            lines.append("")
            lines.append(
                "💡 Bản lưu nhanh trước khi sửa được lưu trong tab "
                "📸 Bản lưu nhanh — có thể khôi phục nếu cần hoàn tác."
            )
        else:
            lines.append("")
            lines.append("CÁCH XỬ LÝ:")
            lines.append(
                "  1. Mở VnEdu kiểm tra trực tiếp các tuần thất bại "
                "(thường có ô vàng cảnh báo)."
            )
            lines.append(
                "  2. Nếu mất tiết: tab 📸 Bản lưu nhanh → khôi phục "
                "snapshot trước khi sửa."
            )
            lines.append(
                "  3. Sau khi sửa các ô vàng trên web, chạy lại Sửa thông "
                "minh từ tuần thất bại."
            )

        summary = "\n".join(lines)
        self.var_sr_status.set(
            f"{'✓' if err == 0 else '⚠'} Hoàn tất: "
            f"{ok} OK · {err} lỗi · {len(skip_weeks)} bỏ qua"
        )
        if err == 0:
            messagebox.showinfo("Sửa thông minh xong", summary, parent=self)
        else:
            messagebox.showwarning(
                "Sửa thông minh — có lỗi", summary, parent=self,
            )
        try:
            self._refresh_snapshots_list()
        except Exception:
            pass

"""Sửa PPCT, tự tính HĐTN, health scan, dò PPCT."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ..dialogs.hdtn_auto_compute import HDTNAutoComputeDialog
from ..dialogs.health_report import HealthReportDialog
from ..theme import CLR_PANEL_BG
from ..workers.detect_ppct import DetectPPCTWorker
from ..workers.tkb_scan import HealthScanWorker


class PPCTMixin:
    """Sửa PPCT, tự tính HĐTN, health scan, dò PPCT."""

    # -----------------------------------------------------------
    # PPCT edit
    # -----------------------------------------------------------

    def _on_ppct_dbl_click(self, event):
        """Double-click cột PPCT → mở popup chỉnh số."""
        if self._block_if_executor_running("sửa PPCT"):
            return
        region = self.tree_ppct.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree_ppct.identify_column(event.x)
        if col != "#4":  # cột thứ 4 = PPCT
            return
        iid = self.tree_ppct.identify_row(event.y)
        if not iid:
            return
        # Tìm entry
        entry = next(
            (e for e in self.profile.ppct_starts if e.group_key == iid), None
        )
        if not entry:
            return

        new_val = self._ask_int_value(
            title="Sửa PPCT bắt đầu",
            prompt=f"PPCT bắt đầu cho:\n{entry.lop_text} – "
                   f"{entry.mon_text} – {entry.phan_mon_text}",
            current=entry.ppct_start, minimum=1,
        )
        if new_val is None:
            return
        entry.ppct_start = new_val
        self._refresh_ppct_table()
        self._mark_dirty()
        self._log(
            f"Đặt PPCT bắt đầu cho {entry.lop_text}/{entry.phan_mon_text} = {new_val}",
            "info",
        )

    def _on_auto_hdtn_clicked(self):
        """v2: Tự tính PPCT bắt đầu cho group HĐTN-Chủ-đề dựa vào tuan_from.

        Workflow:
          1. Validate profile + tuan_from
          2. Compute suggested PPCT cho mỗi group HĐTN-chủ-đề
          3. Show preview dialog cho user xem + check group cần apply
          4. User OK → patch profile.ppct_starts → refresh + mark dirty
        """
        if self._block_if_executor_running("tính PPCT HĐTN"):
            return
        if not self.profile or not self.profile.ppct_starts:
            messagebox.showinfo(
                "Chưa có nhóm",
                "Bạn cần soạn lịch dạy có môn HĐTN-Chủ đề trước.",
                parent=self,
            )
            return
        # Lấy tuan_from từ UI hiện tại (có thể user chưa save)
        try:
            tuan_from = int(self.var_tuan_from.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Lỗi", "Tuần 'Từ tuần' không hợp lệ.", parent=self,
            )
            return
        if not (1 <= tuan_from <= 52):
            messagebox.showerror(
                "Lỗi", f"'Từ tuần' = {tuan_from} ngoài khoảng 1–52.",
                parent=self,
            )
            return
        # FIX-B: Sync ppct_starts với template hiện tại TRƯỚC KHI compute.
        # Lý do: nếu user vừa thêm tiết HĐTN ở grid mà chưa save profile, group
        # chỉ tồn tại trong template, chưa có entry trong ppct_starts → apply
        # rỗng dù compute đúng. Sync sẽ tạo entry mới với ppct_start=1.
        try:
            self.profile.sync_ppct_starts_with_templates()
        except Exception as e:
            messagebox.showerror(
                "Lỗi đồng bộ PPCT",
                f"Không sync được ppct_starts: {type(e).__name__}: {e}",
                parent=self,
            )
            return
        # Compute suggestions
        try:
            results = self.profile.auto_compute_hdtn_chu_de_starts(
                tuan_from=tuan_from,
            )
        except Exception as e:
            messagebox.showerror(
                "Lỗi tính PPCT",
                f"Không tính được PPCT HĐTN: {type(e).__name__}: {e}",
                parent=self,
            )
            return
        if not results:
            messagebox.showinfo(
                "Không tìm thấy nhóm HĐTN",
                "Trong lịch dạy hiện tại, không có nhóm nào thuộc môn "
                "'Hoạt động trải nghiệm' với phân môn 'theo chủ đề'. "
                "Hãy thêm tiết HĐTN vào lưới TKB trước.",
                parent=self,
            )
            return
        # Show dialog
        dlg = HDTNAutoComputeDialog(self, results, tuan_from)
        self.wait_window(dlg)
        if not dlg.selected_groups:
            return
        # Apply: patch ppct_starts
        applied = 0
        result_map = {r["group_key"]: r for r in results}
        for entry in self.profile.ppct_starts:
            if entry.group_key in dlg.selected_groups:
                r = result_map.get(entry.group_key)
                if r and r["suggested_ppct"] != entry.ppct_start:
                    entry.ppct_start = r["suggested_ppct"]
                    applied += 1
        if applied > 0:
            self._refresh_ppct_table()
            self._mark_dirty()
            self._log(
                f"🎯 Tự tính PPCT HĐTN: đã cập nhật {applied} nhóm "
                f"(tuần bắt đầu = {tuan_from}).",
                "ok",
            )
        else:
            self._log(
                "🎯 Tự tính PPCT HĐTN: không có thay đổi nào được áp dụng.",
                "info",
            )

    def _on_health_scan_clicked(self):
        """Quét tình trạng KHDH trong scope group user có quyền nhập."""
        if self._guard_cdp_exclusive("Quét tình trạng KHDH"):
            return
        if not self.profile:
            messagebox.showinfo("Chưa có profile", "Bạn cần mở profile trước.", parent=self)
            return
        try:
            self.profile.sync_ppct_starts_with_templates()
        except Exception as e:
            messagebox.showerror("Lỗi sync profile", str(e), parent=self)
            return
        tuan_to = max(1, int(self.profile.tuan_to or 35))

        self.var_status.set("Đang quét tình trạng KHDH…")
        self.var_detect_progress.set(0)
        self.progress_detect.configure(maximum=tuan_to)
        self.var_detect_progress_text.set(f"0 / {tuan_to} tuần")
        try:
            self.btn_health_scan.configure(state="disabled", text="⏳ Đang quét…")
            self.btn_detect_ppct.configure(state="disabled")
            self.btn_auto_hdtn.configure(state="disabled")
        except Exception:
            pass
        try:
            self._detect_progress_row.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        except Exception:
            pass

        self._health_stop = threading.Event()
        self._health_queue = queue.Queue()
        try:
            port = int(self.var_port.get())
        except (tk.TclError, ValueError):
            port = 9224
        self._health_worker = HealthScanWorker(
            port=port,
            profile=self.profile,
            tuan_to=tuan_to,
            event_queue=self._health_queue,
            stop_event=self._health_stop,
        )
        self._health_worker.start()
        self._safe_after(150, self._poll_health_queue)

    def _poll_health_queue(self):
        drained_done = False
        try:
            while True:
                ev = self._health_queue.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.var_status.set(ev[1])
                elif kind == "progress":
                    _, done, total, batch_weeks, label = ev
                    self.var_status.set(f"Đã quét {done}/{total} tuần (vừa xong {label})")
                    self.var_detect_progress.set(done)
                    self.var_detect_progress_text.set(f"{done} / {total} tuần — {label}")
                elif kind == "done":
                    _, result = ev
                    drained_done = True
                    self._finalize_health_ui()
                    dlg = HealthReportDialog(self, result)
                    self.wait_window(dlg)
                elif kind == "error":
                    drained_done = True
                    self._finalize_health_ui()
                    self.var_status.set("❌ Lỗi quét tình trạng")
                    self._log(ev[1], "err")
                    messagebox.showerror("Lỗi quét tình trạng", ev[1].splitlines()[0], parent=self)
        except queue.Empty:
            pass
        if drained_done:
            return
        if (self._health_worker and self._health_worker.is_alive()) or not self._health_queue.empty():
            self._safe_after(150, self._poll_health_queue)
        else:
            self._finalize_health_ui()

    def _finalize_health_ui(self):
        try:
            self.btn_health_scan.configure(state="normal", text="Kiểm tra lỗi")
            self.btn_detect_ppct.configure(state="normal", text="Lấy PPCT từ web")
            self.btn_auto_hdtn.configure(state="normal", text="Tính PPCT HĐTN")
        except Exception:
            pass
        try:
            self._detect_progress_row.grid_forget()
        except Exception:
            pass

    def _on_detect_ppct_clicked(self):
        """Quét tuần 1 đến tuần_to để tìm PPCT lớn nhất theo nhóm,
        rồi đề xuất ppct_start = max + 1.

        Đây là cách duy nhất tránh lỗi 'tiết PPCT này có trạng thái không
        phù hợp với trạng thái trước đó' từ server VnEdu — vì server giữ
        toàn bộ history PPCT của giáo viên xuyên năm học.
        """
        # Validate: phải kết nối Chrome trước
        if not self.bootstrap_data:
            messagebox.showinfo(
                "Chưa đăng nhập VnEdu",
                "Bạn cần bấm [Đăng nhập VnEdu] trước khi tool quét PPCT từ web.",
                parent=self,
            )
            return

        # Đang chạy worker CDP khác?
        if self._guard_cdp_exclusive("Phát hiện PPCT"):
            return

        if not self.profile or not self.profile.ppct_starts:
            messagebox.showinfo(
                "Chưa có nhóm",
                "Bạn cần soạn ít nhất 1 tiết trong lịch dạy "
                "trước khi phát hiện PPCT.",
                parent=self,
            )
            return

        # Confirm
        tuan_to = self.profile.tuan_to or 35
        if not messagebox.askyesno(
            "Phát hiện PPCT từ web",
            f"Công cụ sẽ quét song song từ tuần 1 đến tuần {tuan_to} trên web "
            f"để tìm số PPCT lớn nhất bạn đã dùng.\n\n"
            f"Sau đó tool đề xuất PPCT bắt đầu = (max đã dùng) + 1 cho "
            f"từng nhóm Lớp × Phân môn.\n\n"
            f"Việc này tránh lỗi trùng PPCT khi nhập KHDH, đồng thời báo "
            f"cho bạn biết:\n"
            f"  • Môn nào CHỈ dạy ở tuần lẻ hoặc CHỈ tuần chẵn\n"
            f"  • Nhóm có data trên web nhưng KHÔNG có trong profile\n\n"
            f"Quét sẽ mất ~{max(3, tuan_to // 5)} giây (song song 6 tuần/lần). "
            f"Tiếp tục?",
            parent=self,
        ):
            return

        # Disable UI để tránh user spam
        self._log(f"━━ Bắt đầu quét PPCT từ tuần 1 đến {tuan_to} ━━", "info")
        self.var_status.set("Đang quét web…")

        # Hiện progress bar + reset
        try:
            self.btn_detect_ppct.configure(
                state="disabled", text="⏳ Đang quét…",
            )
        except Exception:
            pass
        self.var_detect_progress.set(0)
        self.progress_detect.configure(maximum=tuan_to)
        self.var_detect_progress_text.set(f"0 / {tuan_to} tuần")
        try:
            self._detect_progress_row.grid(row=2, column=0,
                                          sticky="ew", pady=(4, 0))
        except Exception:
            pass

        self._detect_stop = threading.Event()
        self._detect_queue = queue.Queue()
        try:
            port = int(self.var_port.get())
        except (tk.TclError, ValueError):
            port = 9224
        self._detect_worker = DetectPPCTWorker(
            port=port,
            tuan_from=1, tuan_to=tuan_to,
            event_queue=self._detect_queue,
            stop_event=self._detect_stop,
        )
        self._detect_worker.start()
        self._safe_after(150, self._poll_detect_queue)

    def _poll_detect_queue(self):
        """Drain queue của DetectPPCTWorker."""
        drained_done = False
        try:
            while True:
                ev = self._detect_queue.get_nowait()
                try:
                    kind = ev[0]
                    if kind == "status":
                        self.var_status.set(ev[1])
                    elif kind == "progress":
                        # Format mới: (kind, done, total, batch_weeks, label)
                        _, done, total, batch_weeks, label = ev
                        self.var_status.set(
                            f"Đã quét {done}/{total} tuần (vừa xong {label})"
                        )
                        try:
                            self.var_detect_progress.set(done)
                            self.var_detect_progress_text.set(
                                f"{done} / {total} tuần — {label}"
                            )
                        except Exception:
                            pass
                    elif kind == "done":
                        # Format mới: (kind, report_dict)
                        _, report = ev
                        drained_done = True
                        self._finalize_detect_ui()
                        self._apply_detected_ppct(report)
                    elif kind == "error":
                        self._log(ev[1], "err")
                        self.var_status.set("❌ Lỗi quét")
                        drained_done = True
                        self._finalize_detect_ui()
                        messagebox.showerror("Lỗi quét", ev[1].splitlines()[0],
                                           parent=self)
                except Exception as e:
                    # Error boundary: log nhưng KHÔNG crash poll loop
                    try:
                        self._log(
                            f"⚠ Lỗi xử lý detect event: {type(e).__name__}: {e}",
                            "err",
                        )
                    except Exception:
                        pass
        except queue.Empty:
            pass
        if drained_done:
            return
        if (self._detect_worker and self._detect_worker.is_alive()) \
                or not self._detect_queue.empty():
            self._safe_after(150, self._poll_detect_queue)
        else:
            # Worker đã chết và queue rỗng nhưng chưa nhận done/error
            # → có thể worker exit bất thường, finalize UI để user click lại được
            self._finalize_detect_ui()

    def _finalize_detect_ui(self):
        """Restore UI sau khi quét xong: ẩn progress, enable nút lại."""
        try:
            self.btn_detect_ppct.configure(
                state="normal", text="Lấy PPCT từ web",
            )
            self.btn_health_scan.configure(
                state="normal", text="Kiểm tra lỗi",
            )
            self.btn_auto_hdtn.configure(
                state="normal", text="Tính PPCT HĐTN",
            )
        except Exception:
            pass
        try:
            self._detect_progress_row.grid_forget()
        except Exception:
            pass

    def _apply_detected_ppct(self, report: dict):
        """Áp dụng kết quả quét: đặt ppct_start = max + 1 cho từng nhóm.

        Siết logic theo yêu cầu user:
        - Hiển thị max LẺ và max CHẴN riêng để user thấy rõ nếu môn phân
          biệt rõ ràng giữa 2 phía.
        - Group chỉ xuất hiện ở 1 phía → ghi chú "(chỉ tuần lẻ)" hoặc
          "(chỉ tuần chẵn)" trong dialog.
        - Cảnh báo group có trên web nhưng KHÔNG có trong profile (user có
          thể đã xóa khỏi profile nhưng web còn data cũ).
        - PPCT là tuyến tính theo môn (server đếm cộng dồn) → ppct_start
          gợi ý vẫn là max_overall + 1 cho mọi case (an toàn nhất).
        """
        max_overall = dict(report.get("max_overall") or {})
        max_le = dict(report.get("max_le") or {})
        max_chan = dict(report.get("max_chan") or {})
        group_info = dict(report.get("group_info") or {})
        group_appearances = dict(report.get("group_appearances") or {})
        weeks_scanned = int(report.get("weeks_scanned") or 0)
        elapsed_ms = int(report.get("elapsed_ms") or 0)

        if not max_overall:
            messagebox.showinfo(
                "Không tìm thấy PPCT",
                f"Đã quét {weeks_scanned} tuần ({elapsed_ms / 1000:.1f}s) "
                "nhưng web chưa có data PPCT nào.\n"
                "Bạn có thể giữ PPCT bắt đầu mặc định (= 1) hoặc tự nhập.",
                parent=self,
            )
            self.var_status.set(f"Quét xong {weeks_scanned} tuần: web rỗng")
            return

        # Tách entries thành: cập nhật / không đổi / không có data web
        # đồng thời track group nào trên web không có trong profile (orphan).
        profile_keys = {e.group_key for e in self.profile.ppct_starts}
        web_keys = set(max_overall.keys())
        orphan_keys = web_keys - profile_keys

        updated: list[tuple] = []   # (entry, old, new, max_le, max_chan, max_o)
        unchanged: list = []        # entry already correct
        no_web_data: list = []      # entry không có data web

        for entry in self.profile.ppct_starts:
            key = entry.group_key
            mo = max_overall.get(key)
            if mo is None or mo <= 0:
                no_web_data.append(entry)
                continue
            old = entry.ppct_start
            new = mo + 1
            ml = max_le.get(key, 0)
            mc = max_chan.get(key, 0)
            if new != old:
                updated.append((entry, old, new, ml, mc, mo))
            else:
                unchanged.append((entry, ml, mc, mo))

        if not updated and not orphan_keys:
            # Mọi thứ đã đúng
            messagebox.showinfo(
                "PPCT đã đúng",
                f"Đã quét {weeks_scanned} tuần ({elapsed_ms / 1000:.1f}s).\n"
                "Tất cả các nhóm đã có PPCT bắt đầu hợp lý "
                "(không bị trùng với data trên web).",
                parent=self,
            )
            self.var_status.set(f"Quét xong {weeks_scanned} tuần: PPCT OK")
            return

        # Build dialog message
        lines: list[str] = [
            f"Đã quét {weeks_scanned} tuần trong {elapsed_ms / 1000:.1f}s.",
            "",
        ]

        if updated:
            lines.append(f"━━ {len(updated)} nhóm cần cập nhật PPCT bắt đầu ━━")
            for entry, old, new, ml, mc, mo in updated[:20]:
                # Ghi chú phân biệt lẻ/chẵn nếu khác nhau
                note = ""
                if ml > 0 and mc == 0:
                    note = "  (chỉ tuần lẻ)"
                elif mc > 0 and ml == 0:
                    note = "  (chỉ tuần chẵn)"
                elif ml > 0 and mc > 0 and abs(ml - mc) >= 2:
                    note = f"  (lẻ:{ml} / chẵn:{mc})"
                lines.append(
                    f"  • {entry.lop_text} / {entry.phan_mon_text}: "
                    f"{old} → {new} (web đã dùng tới {mo}){note}"
                )
            if len(updated) > 20:
                lines.append(f"  • … và {len(updated) - 20} nhóm khác")
            lines.append("")

        if orphan_keys:
            lines.append(
                f"⚠ {len(orphan_keys)} nhóm có data trên web nhưng KHÔNG "
                "trong profile của bạn:"
            )
            for k in list(orphan_keys)[:10]:
                info = group_info.get(k, {})
                ap = group_appearances.get(k, {})
                le_w = ap.get("le_weeks", [])
                chan_w = ap.get("chan_weeks", [])
                pattern_note = ""
                if le_w and not chan_w:
                    pattern_note = " (chỉ tuần lẻ)"
                elif chan_w and not le_w:
                    pattern_note = " (chỉ tuần chẵn)"
                lines.append(
                    f"  • {info.get('lop_text', '?')} / "
                    f"{info.get('mon_text', '?')} / "
                    f"{info.get('phan_mon_text', '?')} "
                    f"(max PPCT: {max_overall.get(k, 0)}){pattern_note}"
                )
            if len(orphan_keys) > 10:
                lines.append(f"  • … và {len(orphan_keys) - 10} nhóm khác")
            lines.append(
                "  → Có thể bạn đã bỏ các môn này khỏi profile. Kiểm tra "
                "lại nếu cần thêm vào."
            )
            lines.append("")

        if no_web_data:
            lines.append(
                f"ℹ {len(no_web_data)} nhóm trong profile chưa có data trên "
                "web (sẽ giữ PPCT bắt đầu cũ)."
            )
            lines.append("")

        if updated:
            lines.append("Áp dụng các thay đổi này?")
        else:
            lines.append("Không có thay đổi nào để áp dụng.")

        msg = "\n".join(lines)

        if not updated:
            # Chỉ hiện thông tin orphan
            messagebox.showwarning("Phát hiện PPCT", msg, parent=self)
            self.var_status.set(
                f"Quét xong {weeks_scanned} tuần: PPCT OK, có {len(orphan_keys)} nhóm web orphan"
            )
            return

        if messagebox.askyesno("Xác nhận áp dụng", msg, parent=self):
            for entry, _old, new, _ml, _mc, _mo in updated:
                entry.ppct_start = new
            self._refresh_ppct_table()
            self._mark_dirty()
            self._log(
                f"Đã cập nhật {len(updated)} PPCT bắt đầu theo web "
                f"(quét {weeks_scanned} tuần, {elapsed_ms / 1000:.1f}s)",
                "ok",
            )
            self.var_status.set(f"Đã cập nhật {len(updated)} PPCT")
        else:
            self._log("Đã hủy áp dụng PPCT đã phát hiện", "warn")
            self.var_status.set("Đã hủy")

    def _ask_int_value(self, title: str, prompt: str, current: int,
                      minimum: int = 1) -> int | None:
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(background=CLR_PANEL_BG)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        win.resizable(False, False)
        result = {"value": None}

        body = ttk.Frame(win, padding=18, style="Wiz.TFrame")
        body.pack()
        ttk.Label(body, text=prompt, style="Wiz.TLabel",
                wraplength=320, justify="left").pack(anchor="w", pady=(0, 8))

        var = tk.IntVar(value=current)
        sp = ttk.Spinbox(body, from_=minimum, to=999, textvariable=var,
                       font=("Segoe UI", 11), width=8)
        sp.pack(anchor="w", pady=(0, 10))
        sp.focus_set()
        sp.selection_range(0, "end")

        def confirm():
            try:
                v = int(var.get())
                if v < minimum:
                    raise ValueError
            except (tk.TclError, ValueError):
                messagebox.showwarning(
                    "Sai", f"PPCT phải là số nguyên ≥ {minimum}.",
                    parent=win,
                )
                return
            result["value"] = v
            win.destroy()

        btn_row = ttk.Frame(body, style="Wiz.TFrame")
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Hủy", command=win.destroy,
                  style="WizSubtle.TButton").pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="Lưu", command=confirm,
                  style="WizPrimary.TButton").pack(side="right")
        win.bind("<Return>", lambda e: confirm())
        win.bind("<Escape>", lambda e: win.destroy())

        # Center
        win.update_idletasks()
        top = self.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() // 2) - (win.winfo_width() // 2)
        y = top.winfo_rooty() + (top.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.wait_window(win)
        return result["value"]

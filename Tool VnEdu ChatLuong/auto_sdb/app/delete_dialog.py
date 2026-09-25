"""Xoá dữ liệu sổ đầu bài theo khoảng tuần."""

import copy
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ..cdp.bridge import ChromeBridge


class DeleteDialogMixin:
    """Xoá dữ liệu sổ đầu bài theo khoảng tuần."""

    # =================================================================
    # XÓA DỮ LIỆU SỔ ĐẦU BÀI (theo khoảng tuần) — API-first an toàn
    # =================================================================

    def _destroy_delete_dialog(self):
        """Đóng dialog xóa và clear references."""
        if self._delete_dialog and self._delete_dialog.winfo_exists():
            try:
                self._delete_dialog.destroy()
            except Exception:
                pass
        self._delete_dialog = None
        self._delete_status_label = None
        self._delete_result_text = None
        self._delete_run_button = None
        self._delete_stop_button = None
        self._delete_scan_button = None
        self._delete_class_combo = None
        self._delete_scanned_entries = []
        self._delete_scan_signature = None
        self._delete_scanning = False

    def _clear_delete_queue(self):
        """Xóa message tồn trong queue trước phiên xóa mới."""
        try:
            while not self._delete_queue.empty():
                self._delete_queue.get_nowait()
        except Exception:
            pass

    def _set_delete_status(self, text, color="#555"):
        """Cập nhật dòng trạng thái trong dialog xóa."""
        if self._delete_status_label and self._delete_status_label.winfo_exists():
            self._delete_status_label.config(text=text, foreground=color)

    def _delete_log(self, message, tag="muted"):
        """Ghi 1 dòng vào vùng kết quả của dialog xóa."""
        widget = self._delete_result_text
        if widget is None or not widget.winfo_exists():
            return
        widget.config(state="normal")
        widget.insert("end", message + "\n", tag)
        widget.see("end")
        widget.config(state="disabled")

    def _delete_busy_reason(self):
        """Trả về lý do KHÔNG được phép xóa/quét lúc này (concurrency guard)."""
        if self._schedule_running:
            return "Schedule đang chạy. Hãy dừng hoặc chờ xong rồi mới thao tác xóa."
        if self._class_stats_running or (
            self._class_stats_thread and self._class_stats_thread.is_alive()
        ):
            return "Đang có phiên thống kê lớp dùng CDP. Hãy chờ xong rồi mới thao tác xóa."
        if self._auto_login_running:
            return "Auto-login đang chạy. Hãy chờ xong rồi mới thao tác xóa."
        if self._quick_prepare_running:
            return "Đang quét full dữ liệu. Hãy chờ xong rồi mới thao tác xóa."
        return ""

    def _compute_delete_scan_signature(self, lop_text, tuan_from, tuan_to, only_mine):
        """Chữ ký ngữ cảnh scan để phát hiện thay đổi trước khi xóa."""
        return (
            str(lop_text or "").strip().lower(),
            int(tuan_from),
            int(tuan_to),
            bool(only_mine),
        )

    def _current_delete_signature(self):
        """Chữ ký theo các lựa chọn hiện tại trên dialog xóa."""
        try:
            tuan_from = int(self.var_delete_tuan_from.get())
            tuan_to = int(self.var_delete_tuan_to.get())
        except (tk.TclError, ValueError):
            return None
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        return self._compute_delete_scan_signature(
            self.var_delete_lop.get().strip(),
            tuan_from,
            tuan_to,
            bool(self.var_delete_only_mine.get()),
        )

    def _invalidate_delete_scan(self, reason=""):
        """Hủy kết quả scan hiện có (buộc quét lại trước khi xóa)."""
        self._delete_scanned_entries = []
        self._delete_scan_signature = None
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="disabled")
        if reason and self._delete_status_label and self._delete_status_label.winfo_exists():
            self._set_delete_status(reason, "#a86400")

    def _on_open_delete_dialog(self):
        """Mở dialog Xóa dữ liệu sổ đầu bài."""
        if not self._cdp_connected:
            self._log("Chưa kết nối CDP!", "warning")
            messagebox.showwarning("Cảnh báo", "Hãy kết nối CDP trước khi xóa dữ liệu.")
            return
        busy = self._delete_busy_reason()
        if busy:
            messagebox.showwarning("Cảnh báo", busy)
            return
        self._ensure_delete_dialog()
        self._load_delete_lop_options()

    def _ensure_delete_dialog(self):
        """Tạo hoặc focus dialog xóa dữ liệu."""
        if self._delete_dialog and self._delete_dialog.winfo_exists():
            self._delete_dialog.deiconify()
            self._delete_dialog.lift()
            self._delete_dialog.focus_force()
            return self._delete_dialog

        dlg = tk.Toplevel(self.root)
        dlg.title("Xóa dữ liệu sổ đầu bài")
        dlg.transient(self.root)
        dlg.geometry("900x560")
        dlg.minsize(720, 440)
        dlg.protocol("WM_DELETE_WINDOW", self._on_delete_dialog_close)
        self._delete_dialog = dlg

        header = ttk.Frame(dlg, padding=10)
        header.pack(fill="x")
        tk.Label(
            header,
            text="⚠ XÓA DỮ LIỆU SỔ ĐẦU BÀI — KHÔNG THỂ HOÀN TÁC",
            font=("Segoe UI", 11, "bold"),
            fg="#b00020",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Chọn Lớp và khoảng Tuần, bấm 'Quét tiết đã ghi' để xem preview, "
                "rồi gõ xác nhận để xóa. Mặc định chỉ xóa tiết do CHÍNH BẠN ký tên."
            ),
            foreground="#666",
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        row_top = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_top.pack(fill="x")
        ttk.Label(row_top, text="Lớp:").pack(side="left")
        self._delete_class_combo = ttk.Combobox(
            row_top, textvariable=self.var_delete_lop, state="readonly", width=12
        )
        self._delete_class_combo.pack(side="left", padx=4)
        self._delete_class_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._invalidate_delete_scan(
                "Đã đổi Lớp — hãy bấm Quét lại trước khi xóa."
            ),
        )
        ttk.Label(row_top, text="  Tuần từ:").pack(side="left")
        ttk.Spinbox(
            row_top, from_=1, to=52, width=5, textvariable=self.var_delete_tuan_from,
            command=lambda: self._invalidate_delete_scan(
                "Đã đổi khoảng Tuần — hãy bấm Quét lại trước khi xóa."
            ),
        ).pack(side="left", padx=4)
        ttk.Label(row_top, text="→").pack(side="left", padx=2)
        ttk.Spinbox(
            row_top, from_=1, to=52, width=5, textvariable=self.var_delete_tuan_to,
            command=lambda: self._invalidate_delete_scan(
                "Đã đổi khoảng Tuần — hãy bấm Quét lại trước khi xóa."
            ),
        ).pack(side="left", padx=4)
        ttk.Button(
            row_top, text="↻ Tải lại DS lớp", command=self._load_delete_lop_options
        ).pack(side="right")

        row_opt = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_opt.pack(fill="x")
        ttk.Checkbutton(
            row_opt,
            text="Chỉ xóa tiết do CHÍNH TÔI ký tên (an toàn — không đụng dữ liệu giáo viên khác)",
            variable=self.var_delete_only_mine,
            command=lambda: self._invalidate_delete_scan(
                "Đã đổi bộ lọc giáo viên — hãy bấm Quét lại trước khi xóa."
            ),
        ).pack(side="left")

        row_scan = ttk.Frame(dlg, padding=(10, 0, 10, 6))
        row_scan.pack(fill="x")
        self._delete_scan_button = ttk.Button(
            row_scan, text="🔍 Quét tiết đã ghi", command=self._on_delete_scan
        )
        self._delete_scan_button.pack(side="left")

        self._delete_status_label = ttk.Label(
            dlg, text="Chưa quét.", foreground="#555", padding=(10, 0, 10, 6)
        )
        self._delete_status_label.pack(fill="x")

        # Pack các hàng thao tác ở ĐÁY trước (side="bottom") để chúng luôn được
        # giữ chỗ, không bao giờ bị vùng preview (expand) đẩy khuất khỏi dialog.
        row_btn = ttk.Frame(dlg, padding=(10, 4, 10, 10))
        row_btn.pack(side="bottom", fill="x")
        self._delete_run_button = ttk.Button(
            row_btn, text="🗑 XÓA CÁC TIẾT ĐÃ QUÉT",
            command=self._on_delete_run, state="disabled"
        )
        self._delete_run_button.pack(side="left")
        self._delete_stop_button = ttk.Button(
            row_btn, text="⏹ Dừng", command=self._on_delete_stop, state="disabled"
        )
        self._delete_stop_button.pack(side="left", padx=6)
        ttk.Button(row_btn, text="Đóng", command=self._on_delete_dialog_close).pack(side="right")

        row_confirm = ttk.Frame(dlg, padding=(10, 0, 10, 0))
        row_confirm.pack(side="bottom", fill="x")
        ttk.Label(
            row_confirm,
            text="Gõ XOA để xác nhận:",
            foreground="#b00020",
        ).pack(side="left")
        ttk.Entry(
            row_confirm, textvariable=self.var_delete_confirm, width=12
        ).pack(side="left", padx=6)

        # Vùng preview chiếm phần giữa còn lại; Text giới hạn chiều cao để không
        # đẩy các nút phía dưới ra ngoài dialog.
        result_frame = ttk.LabelFrame(dlg, text="Preview các tiết sẽ xóa", padding=6)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._delete_result_text = tk.Text(
            result_frame, height=8, wrap="word", font=("Consolas", 10),
            bg="#ffffff", fg="#222222", insertbackground="#000000",
        )
        result_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self._delete_result_text.yview
        )
        self._delete_result_text.configure(yscrollcommand=result_scroll.set)
        self._delete_result_text.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self._delete_result_text.tag_configure("title", font=("Segoe UI", 10, "bold"))
        self._delete_result_text.tag_configure("ok", foreground="#1f7a1f")
        self._delete_result_text.tag_configure("warn", foreground="#a86400")
        self._delete_result_text.tag_configure("error", foreground="#b00020")
        self._delete_result_text.tag_configure("muted", foreground="#666666")
        self._delete_result_text.insert(
            "end", "Bấm 'Quét tiết đã ghi' để xem danh sách tiết có dữ liệu.\n", "muted"
        )
        self._delete_result_text.config(state="disabled")
        return dlg

    def _on_delete_dialog_close(self):
        """Đóng dialog xóa (chặn khi đang chạy)."""
        if self._delete_running:
            messagebox.showwarning(
                "Đang xóa",
                "Tiến trình xóa đang chạy. Hãy bấm Dừng và chờ kết thúc trước khi đóng.",
            )
            return
        self._destroy_delete_dialog()

    def _set_delete_running(self, running):
        """Bật/tắt trạng thái chạy cho cụm xóa."""
        self._delete_running = running
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="disabled" if running else "normal")
        if self._delete_stop_button and self._delete_stop_button.winfo_exists():
            self._delete_stop_button.config(state="normal" if running else "disabled")
        if self._delete_scan_button and self._delete_scan_button.winfo_exists():
            self._delete_scan_button.config(state="disabled" if running else "normal")

    def _load_delete_lop_options(self):
        """Tải danh sách lớp cho dialog xóa (tái dùng combobox lớp của schedule nếu có)."""
        existing = list(self._delete_class_combo["values"]) if (
            self._delete_class_combo and self._delete_class_combo.winfo_exists()
        ) else []
        sched_values = list(self.cmb_sched_lop["values"]) if hasattr(self, "cmb_sched_lop") else []
        if sched_values:
            self._apply_delete_lop_options(sched_values)
        elif existing:
            pass
        else:
            self._set_delete_status("⏳ Đang tải danh sách lớp...", "#a86400")

        tuan_nums = self._week_numbers_from_vars(
            self.var_delete_tuan_from,
            self.var_delete_tuan_to,
            require_multi_week=True,
        )
        scan_scope = (
            f"Tuần {tuan_nums[0]}→{tuan_nums[-1]}" if tuan_nums else "toàn bộ tuần có trên web"
        )

        def _work():
            try:
                bridge = ChromeBridge(port=self._cdp_port)
                ok, msg = bridge.connect()
                if not ok:
                    bridge.disconnect()
                    self._post_ui(lambda: self._set_delete_status(f"Kết nối CDP lỗi: {msg}", "#b00020"))
                    return
                ok2, lop_data = bridge.discover_lop_options_for_weeks(tuan_nums=tuan_nums)
                bridge.disconnect()
                if ok2:
                    options = self._sort_lop_options(lop_data.get("options", []))
                    self._post_ui(lambda: self._apply_delete_lop_options(options, scan_scope=scan_scope))
                else:
                    self._post_ui(lambda: self._set_delete_status(f"Lỗi tải DS lớp: {lop_data}", "#b00020"))
            except Exception as e:
                self._post_ui(lambda e=e: self._set_delete_status(f"Exception tải DS lớp: {e}", "#b00020"))

        threading.Thread(target=_work, daemon=True).start()

    def _apply_delete_lop_options(self, options, scan_scope=""):
        """Populate combobox lớp trong dialog xóa."""
        if not self._delete_class_combo or not self._delete_class_combo.winfo_exists():
            return
        prev = self.var_delete_lop.get().strip()
        self._delete_class_combo["values"] = options
        if prev and prev in options:
            self._delete_class_combo.set(prev)
        elif self.var_sched_lop.get().strip() in options:
            self._delete_class_combo.set(self.var_sched_lop.get().strip())
        elif options:
            self._delete_class_combo.set(options[0])
        suffix = f" ({scan_scope})" if scan_scope else ""
        self._set_delete_status(
            f"Đã tải {len(options)} lớp{suffix}. Chọn lớp + tuần rồi bấm Quét.",
            "#1f7a1f",
        )

    def _on_delete_scan(self):
        """Quét các tiết đã có dữ liệu trong khoảng tuần để preview trước khi xóa."""
        if self._delete_running or self._delete_scanning:
            return
        if not self._cdp_connected:
            self._set_delete_status("Chưa kết nối CDP!", "#b00020")
            return
        busy = self._delete_busy_reason()
        if busy:
            self._set_delete_status(busy, "#b00020")
            return
        lop_text = self.var_delete_lop.get().strip()
        if not lop_text:
            messagebox.showwarning("Cảnh báo", "Chưa chọn Lớp cần xóa.")
            return
        try:
            tuan_from = int(self.var_delete_tuan_from.get())
            tuan_to = int(self.var_delete_tuan_to.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Cảnh báo", "Khoảng tuần không hợp lệ.")
            return
        if tuan_from < 1 or tuan_to < 1 or tuan_from > 52 or tuan_to > 52:
            messagebox.showwarning("Cảnh báo", "Tuần phải nằm trong khoảng 1–52.")
            return
        if tuan_from > tuan_to:
            tuan_from, tuan_to = tuan_to, tuan_from
        self.var_delete_tuan_from.set(tuan_from)
        self.var_delete_tuan_to.set(tuan_to)

        only_mine = bool(self.var_delete_only_mine.get())
        self._delete_scanned_entries = []
        self._delete_scan_signature = None
        self._clear_delete_queue()
        self._delete_scanning = True
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="disabled")
        if self._delete_scan_button and self._delete_scan_button.winfo_exists():
            self._delete_scan_button.config(state="disabled")
        self._set_delete_status(
            f"⏳ Đang quét lớp {lop_text} | Tuần {tuan_from}→{tuan_to}...", "#a86400"
        )

        params = {
            "port": self._cdp_port,
            "lop": lop_text,
            "tuan_from": tuan_from,
            "tuan_to": tuan_to,
            "only_mine": only_mine,
            "signature": self._compute_delete_scan_signature(
                lop_text, tuan_from, tuan_to, only_mine
            ),
        }
        self._delete_thread = threading.Thread(
            target=self._delete_scan_worker, args=(params,), daemon=True
        )
        self._delete_thread.start()
        self.root.after(120, self._poll_delete_queue)

    def _delete_scan_worker(self, params):
        """Worker quét tiết đã ghi để preview (read-only)."""
        q = self._delete_queue
        bridge = None
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("scan_error", f"Kết nối CDP thất bại: {msg}"))
                return

            teacher_name = ""
            if params.get("only_mine"):
                ok_user, name_or_err = bridge.get_current_user_full_name()
                if ok_user:
                    teacher_name = str(name_or_err)
                else:
                    q.put(("scan_warn",
                           f"Không đọc được tên giáo viên hiện tại ({name_or_err}). "
                           "Bộ lọc 'chỉ xóa tiết của tôi' sẽ không áp dụng được."))
            teacher_key = self._normalize_person_name(teacher_name) if teacher_name else ""

            all_entries = []
            week_errors = []
            for tuan_num in range(int(params["tuan_from"]), int(params["tuan_to"]) + 1):
                q.put(("scan_status", f"⏳ Quét lớp {params['lop']} — Tuần {tuan_num}..."))
                ok_fetch, payload = bridge.fetch_deletable_entries(
                    params["lop"], tuan_num, timeout_s=12.0
                )
                if not ok_fetch:
                    week_errors.append({"week": tuan_num, "message": str(payload)})
                    continue
                server_week = payload.get("week")
                if server_week is not None and int(server_week) != int(tuan_num):
                    week_errors.append({
                        "week": tuan_num,
                        "message": f"Service trả về tuần {server_week}, không khớp tuần {tuan_num}",
                    })
                    continue
                for entry in payload.get("entries", []):
                    item = dict(entry)
                    item["week"] = tuan_num
                    item["week_text"] = f"Tuần {tuan_num}"
                    is_mine = bool(
                        teacher_key and
                        self._normalize_person_name(item.get("ky_ten", "")) == teacher_key
                    )
                    item["is_mine"] = is_mine
                    all_entries.append(item)

            q.put(("scan_done", {
                "entries": all_entries,
                "teacher_name": teacher_name,
                "only_mine": bool(params.get("only_mine")),
                "lop": params["lop"],
                "tuan_from": int(params["tuan_from"]),
                "tuan_to": int(params["tuan_to"]),
                "week_errors": week_errors,
                "signature": params.get("signature"),
            }))
        except Exception as e:
            q.put(("scan_error", f"Worker quét xóa lỗi: {type(e).__name__}: {str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _render_delete_preview(self, payload):
        """Hiển thị preview các tiết sẽ xóa và bật nút xóa nếu hợp lệ."""
        widget = self._delete_result_text
        if widget is None or not widget.winfo_exists():
            return

        only_mine = bool(payload.get("only_mine"))
        teacher_name = str(payload.get("teacher_name", "")).strip()
        all_entries = list(payload.get("entries", []))
        week_errors = list(payload.get("week_errors", []))

        if only_mine and teacher_name:
            target = [e for e in all_entries if e.get("is_mine")]
            others = [e for e in all_entries if not e.get("is_mine")]
        elif only_mine and not teacher_name:
            # An toàn: bật lọc "chỉ của tôi" nhưng KHÔNG đọc được tên GV →
            # không xác định được tiết nào của mình → KHÔNG xóa gì cả.
            target = []
            others = list(all_entries)
        else:
            target = list(all_entries)
            others = []

        self._delete_scanned_entries = target
        self._delete_scan_signature = payload.get("signature")

        widget.config(state="normal")
        widget.delete("1.0", "end")

        widget.insert("end", f"Lớp {payload.get('lop','?')} | "
                             f"Tuần {payload.get('tuan_from','?')}→{payload.get('tuan_to','?')}\n", "title")
        if only_mine:
            widget.insert("end", f"Bộ lọc: chỉ tiết của '{teacher_name or '(không rõ)'}'\n", "muted")
        else:
            widget.insert("end", "Bộ lọc: XÓA TẤT CẢ giáo viên (cẩn thận!)\n", "warn")
        widget.insert("end", f"Số tiết SẼ XÓA: {len(target)}", "title")
        if others:
            widget.insert("end", f"  |  Bỏ qua (GV khác): {len(others)}", "muted")
        widget.insert("end", "\n\n")

        if not target:
            widget.insert("end", "Không có tiết nào khớp điều kiện để xóa.\n", "warn")
        else:
            for e in target:
                widget.insert(
                    "end",
                    f"[XÓA] {e.get('week_text','')} | Thứ {e.get('thu','?')} "
                    f"{e.get('buoi','?')} Tiết {e.get('tiet','?')} | PPCT {e.get('ppct','--')} | "
                    f"{e.get('mon_hoc','')} | {e.get('ky_ten','')} | id={e.get('chitiet_id','')}\n",
                    "error",
                )

        if others:
            widget.insert("end", "\nCác tiết của giáo viên khác (KHÔNG xóa):\n", "title")
            for e in others[:50]:
                widget.insert(
                    "end",
                    f"  - {e.get('week_text','')} | Thứ {e.get('thu','?')} {e.get('buoi','?')} "
                    f"Tiết {e.get('tiet','?')} | {e.get('mon_hoc','')} | {e.get('ky_ten','')}\n",
                    "muted",
                )
            if len(others) > 50:
                widget.insert("end", f"  ... còn {len(others) - 50} tiết khác\n", "muted")

        if week_errors:
            widget.insert("end", "\nTuần đọc lỗi:\n", "title")
            for item in week_errors[:10]:
                widget.insert("end", f"  - Tuần {item['week']}: {item['message']}\n", "warn")

        widget.config(state="disabled")

        self._delete_scanning = False
        if self._delete_run_button and self._delete_run_button.winfo_exists():
            self._delete_run_button.config(state="normal" if target else "disabled")
        if self._delete_scan_button and self._delete_scan_button.winfo_exists():
            self._delete_scan_button.config(state="normal")

        if target:
            self._set_delete_status(
                f"Sẵn sàng xóa {len(target)} tiết. Gõ 'XOA' rồi bấm nút xóa.", "#b00020"
            )
        elif only_mine and not teacher_name:
            self._set_delete_status(
                "Không đọc được tên giáo viên → không thể xác định tiết của bạn. "
                "Hãy đăng nhập lại hoặc bỏ chọn lọc (rủi ro) rồi Quét lại.",
                "#b00020",
            )
        else:
            self._set_delete_status("Không có tiết nào để xóa.", "#1f7a1f")

    def _on_delete_run(self):
        """Thực thi xóa các tiết đã quét (sau khi xác nhận)."""
        if self._delete_running or self._delete_scanning:
            return
        if not self._cdp_connected:
            self._set_delete_status("Chưa kết nối CDP!", "#b00020")
            return
        busy = self._delete_busy_reason()
        if busy:
            self._set_delete_status(busy, "#b00020")
            messagebox.showwarning("Cảnh báo", busy)
            return
        target = list(self._delete_scanned_entries or [])
        if not target:
            messagebox.showinfo("Thông báo", "Chưa có tiết nào để xóa. Hãy Quét trước.")
            return

        # Chống xóa lệch: ngữ cảnh hiện tại phải khớp đúng lần Quét gần nhất.
        current_sig = self._current_delete_signature()
        if self._delete_scan_signature is None or current_sig != self._delete_scan_signature:
            self._invalidate_delete_scan(
                "Lựa chọn đã thay đổi sau lần Quét. Hãy bấm Quét lại trước khi xóa."
            )
            messagebox.showwarning(
                "Cần quét lại",
                "Lớp / khoảng Tuần / bộ lọc đã thay đổi so với lần Quét gần nhất.\n"
                "Hãy bấm 'Quét tiết đã ghi' lại để xác nhận danh sách trước khi xóa.",
            )
            return

        confirm_text = self._normalize_person_name(self.var_delete_confirm.get())
        if confirm_text != "xoa":
            messagebox.showwarning(
                "Xác nhận chưa đúng",
                "Hãy gõ chính xác chữ XOA vào ô xác nhận trước khi xóa.",
            )
            return

        # Lọc lại các entry có chitiet_id hợp lệ ngay tại đây để con số xác nhận
        # khớp đúng số sẽ thực sự gửi lệnh xóa.
        valid_target = [
            e for e in target if str(e.get("chitiet_id", "")).strip()
        ]
        if not valid_target:
            messagebox.showinfo("Thông báo", "Không có tiết nào có mã hợp lệ để xóa.")
            return

        if not messagebox.askyesno(
            "XÁC NHẬN XÓA",
            f"Xóa vĩnh viễn {len(valid_target)} tiết đã ghi?\n"
            f"Lớp {self.var_delete_lop.get().strip()} | "
            f"Tuần {self.var_delete_tuan_from.get()}→{self.var_delete_tuan_to.get()}\n"
            "Thao tác này KHÔNG THỂ HOÀN TÁC.",
            icon="warning",
        ):
            return

        self.var_delete_confirm.set("")
        self._clear_delete_queue()
        self._delete_stop_event.clear()
        self._set_delete_running(True)
        self._set_delete_status(f"⏳ Đang xóa {len(valid_target)} tiết...", "#a86400")
        self._delete_log(f"\n=== BẮT ĐẦU XÓA {len(valid_target)} tiết ===", "title")

        params = {
            "port": self._cdp_port,
            # Signature đã đảm bảo lớp hiện tại khớp đúng lớp lúc Quét (case-insensitive),
            # nên dùng giá trị hiển thị đúng hoa/thường cho verify refetch.
            "lop": self.var_delete_lop.get().strip(),
            "entries": copy.deepcopy(valid_target),
        }
        self._delete_thread = threading.Thread(
            target=self._delete_run_worker, args=(params,), daemon=True
        )
        self._delete_thread.start()
        self.root.after(120, self._poll_delete_queue)

    def _on_delete_stop(self):
        """Yêu cầu dừng tiến trình xóa sau tiết hiện tại."""
        if self._delete_running and not self._delete_stop_event.is_set():
            self._delete_stop_event.set()
            self._set_delete_status("⏸ Đang dừng sau tiết hiện tại...", "#a86400")
            if self._delete_stop_button and self._delete_stop_button.winfo_exists():
                self._delete_stop_button.config(state="disabled")

    def _delete_run_worker(self, params):
        """Worker xóa từng tiết qua API + verify refetch (đã trống)."""
        q = self._delete_queue
        bridge = None
        deleted = 0
        failed = 0
        skipped = 0
        stopped = False
        lop_text = params["lop"]
        try:
            bridge = ChromeBridge(port=params["port"])
            ok, msg = bridge.connect()
            if not ok:
                q.put(("run_error", f"Kết nối CDP thất bại: {msg}"))
                return

            # Gom entries theo tuần để verify theo tuần (1 fetch/tuần sau khi xóa)
            entries = list(params.get("entries", []))
            weeks_touched = set()
            deleted_ok_ids = set()  # CHỈ các id đã xóa thành công (để verify đúng)

            for idx, entry in enumerate(entries):
                if self._delete_stop_event.is_set():
                    stopped = True
                    break

                chitiet_id = str(entry.get("chitiet_id", "")).strip()
                label = (
                    f"{entry.get('week_text','')} | Thứ {entry.get('thu','?')} "
                    f"{entry.get('buoi','?')} Tiết {entry.get('tiet','?')} | "
                    f"{entry.get('mon_hoc','')} (id={chitiet_id})"
                )
                if not chitiet_id:
                    skipped += 1
                    q.put(("run_log", f"[BỎ QUA] {label}: thiếu chitiet_id", "warn"))
                    continue

                q.put(("run_status", f"⏳ Đang xóa {idx + 1}/{len(entries)}: {label}"))
                ok_del, msg_del = bridge.delete_entry_by_id(chitiet_id, timeout_s=12.0)
                if ok_del:
                    deleted += 1
                    deleted_ok_ids.add(chitiet_id)
                    weeks_touched.add(int(entry.get("week", 0) or 0))
                    q.put(("run_log", f"[OK] {label}", "ok"))
                else:
                    failed += 1
                    q.put(("run_log", f"[LỖI] {label}: {msg_del}", "error"))

            # Verify: refetch các tuần đã đụng tới, chỉ kiểm tra các id ĐÃ xóa OK.
            # Nếu id còn xuất hiện -> xóa chưa ăn (rollback/đồng bộ trễ).
            remaining = 0
            verify_errors = []
            for week_num in sorted(w for w in weeks_touched if w > 0):
                if not deleted_ok_ids:
                    break
                ok_fetch, payload = bridge.fetch_deletable_entries(
                    lop_text, week_num, timeout_s=12.0
                )
                if not ok_fetch:
                    verify_errors.append({"week": week_num, "message": str(payload)})
                    continue
                # Chống alias: service phải trả đúng tuần mới tin kết quả verify.
                server_week = payload.get("week")
                if server_week is not None and int(server_week) != int(week_num):
                    verify_errors.append({
                        "week": week_num,
                        "message": f"Service trả về tuần {server_week} khi verify, bỏ qua",
                    })
                    continue
                still = {
                    str(e.get("chitiet_id", "")).strip()
                    for e in payload.get("entries", [])
                }
                left = deleted_ok_ids & still
                if left:
                    remaining += len(left)
                    q.put(("run_log",
                           f"[CẢNH BÁO] Tuần {week_num} còn {len(left)} tiết chưa xóa được: "
                           + ", ".join(sorted(left)), "warn"))

            q.put(("run_done", {
                "deleted": deleted,
                "failed": failed,
                "skipped": skipped,
                "stopped": stopped,
                "remaining": remaining,
                "verify_errors": verify_errors,
            }))
        except Exception as e:
            q.put(("run_error", f"Worker xóa lỗi: {type(e).__name__}: {str(e)[:140]}"))
        finally:
            if bridge:
                try:
                    bridge.disconnect()
                except Exception:
                    pass

    def _poll_delete_queue(self):
        """Poll queue cho dialog xóa."""
        try:
            while not self._delete_queue.empty():
                msg = self._delete_queue.get_nowait()
                mtype = msg[0]

                if mtype == "scan_status":
                    self._set_delete_status(msg[1], "#a86400")
                elif mtype == "scan_warn":
                    self._delete_log(msg[1], "warn")
                elif mtype == "scan_error":
                    self._set_delete_status(msg[1], "#b00020")
                    self._delete_log(msg[1], "error")
                    self._delete_scanning = False
                    if self._delete_scan_button and self._delete_scan_button.winfo_exists():
                        self._delete_scan_button.config(state="normal")
                    return
                elif mtype == "scan_done":
                    self._render_delete_preview(msg[1])
                    return
                elif mtype == "run_status":
                    self._set_delete_status(msg[1], "#a86400")
                elif mtype == "run_log":
                    self._delete_log(msg[1], msg[2] if len(msg) > 2 else "muted")
                elif mtype == "run_error":
                    self._set_delete_status(msg[1], "#b00020")
                    self._delete_log(msg[1], "error")
                    self._set_delete_running(False)
                    return
                elif mtype == "run_done":
                    self._delete_finished(msg[1])
                    return
        except Exception as e:
            self._set_delete_status(f"Lỗi poll xóa: {e}", "#b00020")
            self._delete_scanning = False
            self._set_delete_running(False)
            return

        if (self._delete_running or (self._delete_thread and self._delete_thread.is_alive())) \
                and self._root_exists():
            self.root.after(150, self._poll_delete_queue)

    def _delete_finished(self, summary):
        """Xử lý kết thúc tiến trình xóa."""
        deleted = int(summary.get("deleted", 0) or 0)
        failed = int(summary.get("failed", 0) or 0)
        skipped = int(summary.get("skipped", 0) or 0)
        stopped = bool(summary.get("stopped"))
        remaining = int(summary.get("remaining", 0) or 0)

        self._set_delete_running(False)
        self._delete_scanned_entries = []
        # Danh sách vừa quét đã cũ (các id vừa xóa không còn tồn tại) → buộc quét lại.
        self._delete_scan_signature = None

        prefix = "⏸ Đã dừng" if stopped else "🏁 Hoàn tất"
        color = "#b00020" if (failed or remaining) else "#1f7a1f"
        status = (
            f"{prefix}: đã xóa {deleted}, lỗi {failed}, bỏ qua {skipped}"
            + (f", còn sót {remaining}" if remaining else "")
        )
        self._set_delete_status(status, color)
        self._delete_log(f"=== {status} ===", "title")
        self._log(
            f"🗑 Xóa sổ đầu bài: đã xóa {deleted}, lỗi {failed}, bỏ qua {skipped}"
            + (f", còn sót {remaining}" if remaining else ""),
            "success" if (not failed and not remaining) else "warning",
        )

        if deleted > 0:
            self._invalidate_class_stats_cache()
            self._on_sched_progress_context_changed()
        # Tự refresh preview để phản ánh trạng thái mới — chỉ khi dialog còn mở,
        # không bị dừng, và không có thao tác nền khác đang chạy.
        if (
            not stopped
            and deleted > 0
            and self._delete_dialog
            and self._delete_dialog.winfo_exists()
            and not self._delete_busy_reason()
        ):
            self.root.after(400, self._on_delete_scan)

"""Điền tên bài thiếu và cập nhật tên bài fallback."""

from __future__ import annotations

import queue
from tkinter import messagebox

from ...engine.fallback_log import FallbackLog
from ...log import logger
from ..workers.fill_titles import _trim_report_details, FillMissingHDTNTitlesWorker
from ..workers.refresh_fallback import RefreshFallbackWorker


class RefreshFallbackMixin:
    """Điền tên bài thiếu và cập nhật tên bài fallback."""

    # -----------------------------------------------------------
    # Refresh Fallback Worker
    # -----------------------------------------------------------

    def _refresh_fallback_button_state(self):
        """Cập nhật trạng thái nút Cập nhật fallback dựa trên log + worker.

        - Disable nếu chưa có hồ sơ Excel hoặc đang có worker khác chạy.
        - Đếm số entry trong log để hiển thị `(N ô đang chờ)` trên text nút.
        - Disable nếu count == 0.
        """
        try:
            busy = self._is_any_cdp_worker_busy() or (
                self._refresh_fb_worker is not None
                and self._refresh_fb_worker.is_alive()
            )
            if not self._profile_path:
                try:
                    count = FallbackLog(None).load().count()
                except Exception:
                    count = 0
                self.var_fallback_pending_count.set(int(count))
                if count <= 0:
                    self.var_fallback_button_text.set(
                        "🔄 Cập nhật Tên bài đã fallback (log chung trống)"
                    )
                else:
                    self.var_fallback_button_text.set(
                        f"🔄 Cập nhật Tên bài đã fallback ({count} ô log chung)"
                    )
                if hasattr(self, "btn_refresh_fallback"):
                    self.btn_refresh_fallback.configure(
                        state="disabled" if busy else "normal"
                    )
                if hasattr(self, "btn_fill_missing_titles"):
                    self.btn_fill_missing_titles.configure(
                        state="disabled" if busy else "normal"
                    )
                return

            count = 0
            try:
                count = FallbackLog(self._profile_path).load().count()
            except Exception:
                count = 0
            self.var_fallback_pending_count.set(int(count))
            if count <= 0:
                self.var_fallback_button_text.set(
                    "🔄 Cập nhật Tên bài đã fallback (không có ô nào)"
                )
            else:
                self.var_fallback_button_text.set(
                    f"🔄 Cập nhật Tên bài đã fallback ({count} ô đang chờ)"
                )

            if not hasattr(self, "btn_refresh_fallback"):
                return

            if not busy:
                self.btn_refresh_fallback.configure(state="normal")
            else:
                self.btn_refresh_fallback.configure(state="disabled")
            if hasattr(self, "btn_fill_missing_titles"):
                self.btn_fill_missing_titles.configure(
                    state="disabled" if busy else "normal"
                )
        except Exception as e:
            logger.warning(f"_refresh_fallback_button_state: {e}")

    def _on_fill_missing_titles_clicked(self):
        """Quét dải tuần hiện tại và chỉ điền tên bài HĐTN lớp 6/8 còn thiếu."""
        if self._guard_cdp_exclusive("Điền tên bài HĐTN thiếu"):
            return
        try:
            tuan_from = max(1, min(52, int(self.var_tuan_from.get())))
            tuan_to = max(tuan_from, min(52, int(self.var_tuan_to.get())))
        except Exception:
            messagebox.showwarning(
                "Dải tuần chưa hợp lệ",
                "Hãy kiểm tra lại mục 'Từ tuần' và 'đến tuần'.",
                parent=self,
            )
            return

        ans = messagebox.askyesno(
            "Điền tên bài HĐTN thiếu",
            (
                f"Tool sẽ quét tuần {tuan_from}-{tuan_to} trên web và chỉ điền "
                "các ô HĐTN lớp 6/8 đang thiếu Tên bài dạy.\n\n"
                "• Không nhập lại lớp, môn, phân môn, PPCT hoặc trạng thái.\n"
                "• Ô đã có Tên bài dạy sẽ được giữ nguyên.\n"
                "• Nếu web đang ở tuần khác ngay trước khi lưu, tool sẽ hủy "
                "để tránh ghi nhầm.\n\n"
                "Tiếp tục?"
            ),
            parent=self,
        )
        if not ans:
            return

        self._fill_titles_stop_event.clear()
        self._fill_titles_queue = queue.Queue()
        self._fill_titles_processed_count = 0
        self._fill_titles_worker = FillMissingHDTNTitlesWorker(
            port=int(self.var_port.get()),
            tuan_from=tuan_from,
            tuan_to=tuan_to,
            event_queue=self._fill_titles_queue,
            stop_event=self._fill_titles_stop_event,
        )
        self._fill_titles_worker.start()

        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_refresh_fallback.configure(state="disabled")
        self.btn_fill_missing_titles.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        total_weeks = tuan_to - tuan_from + 1
        self.progress_bar.configure(maximum=max(total_weeks, 1))
        self.var_progress.set(0)
        self.var_progress_text.set(f"0 / {total_weeks}")
        self.var_status.set("Đang điền tên bài HĐTN thiếu…")
        self._log(
            f"━━ ĐIỀN TÊN BÀI HĐTN THIẾU: tuần {tuan_from}-{tuan_to} ━━",
            "info",
        )
        self._poll_fill_titles_queue()

    def _poll_fill_titles_queue(self):
        try:
            while True:
                ev = self._fill_titles_queue.get_nowait()
                try:
                    self._handle_fill_titles_event(ev)
                except Exception as e:
                    self._log(
                        f"⚠ Lỗi xử lý sự kiện điền tên bài: {type(e).__name__}: {e}",
                        "err",
                    )
        except queue.Empty:
            pass

        if self._fill_titles_worker and self._fill_titles_worker.is_alive():
            self._poll_after_id = self._safe_after(120, self._poll_fill_titles_queue)
        else:
            self._poll_after_id = None
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self._refresh_fallback_button_state()
            self._refresh_fallback_button_state()

    def _handle_fill_titles_event(self, ev: tuple):
        kind = ev[0]
        if kind == "status":
            self.var_status.set(ev[1])
        elif kind == "event":
            xev = ev[1]
            t = xev.event_type
            if t == "plan_start":
                self._log(f"━━ {xev.message} ━━", "info")
            elif t == "week_start":
                self.var_status.set(f"Tuần {xev.tuan}: đang quét tên bài…")
                self._log(f"Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "week_done":
                self._fill_titles_processed_count += 1
                self.var_progress.set(self._fill_titles_processed_count)
                self.var_progress_text.set(
                    f"{self._fill_titles_processed_count} / {self.progress_bar['maximum']}"
                )
                self._log(f"Tuần {xev.tuan}: ✓ {xev.message}", "ok")
            elif t == "save":
                self.var_status.set(f"Tuần {xev.tuan}: đang lưu tên bài…")
                self._log(f"  Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "info":
                self._log(xev.message, "info")
            elif t == "warning":
                self._log(f"⚠ Tuần {xev.tuan}: {xev.message}", "warn")
            elif t == "error":
                self._log(f"❌ Tuần {xev.tuan}: {xev.message}", "err")
            elif t == "stop":
                self._log("Đã dừng theo yêu cầu", "warn")
            elif t == "plan_done":
                self._log(f"━━ {xev.message} ━━", "ok")
        elif kind == "done":
            report = ev[1]
            updated = int(getattr(report, "updated_count", 0) or 0)
            no_word = int(getattr(report, "no_word_title_count", 0) or 0)
            ctx_chg = int(getattr(report, "context_changed_count", 0) or 0)
            err = int(getattr(report, "error_count", 0) or 0)
            updated_details = list(getattr(report, "updated_details", []) or [])
            no_word_details = list(getattr(report, "no_word_details", []) or [])
            skipped_details = list(getattr(report, "skipped_details", []) or [])
            self.var_status.set(
                f"✓ Điền tên bài xong: {updated} cập nhật, "
                f"{no_word} chưa có trong Word, {ctx_chg} bỏ qua, {err} lỗi"
            )
            detail_blocks = []
            if updated_details:
                detail_blocks.append(
                    "Đã điền:\n" + _trim_report_details(updated_details)
                )
            if no_word_details:
                detail_blocks.append(
                    "Chưa tìm thấy trong Word:\n" + _trim_report_details(no_word_details)
                )
            if skipped_details:
                detail_blocks.append(
                    "Đã bỏ qua:\n" + _trim_report_details(skipped_details)
                )
            detail_text = ("\n\n" + "\n\n".join(detail_blocks)) if detail_blocks else ""
            if err:
                messagebox.showwarning(
                    "Điền tên bài xong (có lỗi)",
                    (
                        f"• {updated} ô đã được điền và lưu.\n"
                        f"• {no_word} ô HĐTN lớp 6/8 chưa tìm thấy tên trong Word.\n"
                        f"• {ctx_chg} ô bị bỏ qua vì dữ liệu trên màn hình đã đổi.\n"
                        f"• {err} lỗi.\n\n"
                        "Xem Nhật ký để biết tuần/ô cụ thể."
                        + detail_text
                    ),
                    parent=self,
                )
            else:
                messagebox.showinfo(
                    "Điền tên bài xong",
                    (
                        f"• {updated} ô đã được điền và lưu.\n"
                        f"• {no_word} ô HĐTN lớp 6/8 chưa tìm thấy tên trong Word.\n"
                        f"• {ctx_chg} ô bị bỏ qua vì dữ liệu trên màn hình đã đổi."
                        + detail_text
                    ),
                    parent=self,
                )
        elif kind == "error":
            self.var_status.set("❌ Lỗi điền tên bài HĐTN")
            self._log(ev[1], "err")
            messagebox.showerror("Lỗi", ev[1].splitlines()[0], parent=self)

    def _on_refresh_fallback_clicked(self):
        if self._guard_cdp_exclusive("Cập nhật Tên bài đã fallback"):
            return
        log = FallbackLog(self._profile_path).load()
        count = log.count()
        if count <= 0:
            messagebox.showinfo(
                "Không có ô fallback",
                "Log fallback không có ô nào đang chờ cập nhật.",
                parent=self,
            )
            return

        source_text = (
            "log chung vì hồ sơ Excel chưa được lưu"
            if self._profile_path is None else
            "log đi kèm hồ sơ Excel đang mở"
        )
        ans = messagebox.askyesno(
            "Cập nhật Tên bài đã fallback",
            (
                f"Sẽ kiểm tra {count} ô đã được tool chèn dấu cách thay tên bài "
                f"và cố gắng fetch lại Tên bài thật từ web.\n"
                f"Nguồn: {source_text}.\n\n"
                "• Ô bạn đã sửa thủ công → bỏ khỏi log (an toàn).\n"
                "• Ô có Tên bài mới trên web → cập nhật + lưu tuần.\n"
                "• Ô vẫn chưa có CSDL → giữ trong log để chạy lại sau.\n\n"
                "Công cụ sẽ DỪNG NGAY nếu gặp lỗi lưu, không tự chạy tiếp.\n"
                "Tiếp tục?"
            ),
            parent=self,
        )
        if not ans:
            return

        # Reset queue + state
        self._refresh_fb_stop_event.clear()
        self._refresh_fb_queue = queue.Queue()
        self._refresh_fb_processed_count = 0

        # Spawn worker
        self._refresh_fb_worker = RefreshFallbackWorker(
            port=int(self.var_port.get()),
            excel_path=self._profile_path,
            event_queue=self._refresh_fb_queue,
            stop_event=self._refresh_fb_stop_event,
        )
        self._refresh_fb_worker.start()

        # Lock UI
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_refresh_fallback.configure(state="disabled")
        self.btn_fill_missing_titles.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        weeks_count = len(log.by_week())
        self.progress_bar.configure(maximum=max(weeks_count, 1))
        self.var_progress.set(0)
        self.var_progress_text.set(f"0 / {weeks_count}")
        self.var_status.set("Đang cập nhật fallback…")
        self._log(
            f"━━ CẬP NHẬT FALLBACK: {count} ô trên {weeks_count} tuần ━━",
            "info",
        )
        self._poll_refresh_fb_queue()

    def _poll_refresh_fb_queue(self):
        try:
            while True:
                ev = self._refresh_fb_queue.get_nowait()
                try:
                    self._handle_refresh_fb_event(ev)
                except Exception as e:
                    try:
                        self._log(
                            f"⚠ Lỗi xử lý refresh-fb event: "
                            f"{type(e).__name__}: {e}",
                            "err",
                        )
                    except Exception:
                        pass
        except queue.Empty:
            pass

        if self._refresh_fb_worker and self._refresh_fb_worker.is_alive():
            self._poll_after_id = self._safe_after(
                120, self._poll_refresh_fb_queue,
            )
        else:
            self._poll_after_id = None
            # Restore UI
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self._refresh_fallback_button_state()

    def _handle_refresh_fb_event(self, ev: tuple):
        kind = ev[0]
        if kind == "status":
            self.var_status.set(ev[1])
        elif kind == "event":
            xev = ev[1]
            t = xev.event_type
            if t == "plan_start":
                self._log(f"━━ {xev.message} ━━", "info")
            elif t == "week_start":
                self.var_status.set(f"Tuần {xev.tuan}: kiểm tra…")
                self._log(f"Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "week_done":
                self._refresh_fb_processed_count += 1
                self.var_progress.set(self._refresh_fb_processed_count)
                self.var_progress_text.set(
                    f"{self._refresh_fb_processed_count} / "
                    f"{self.progress_bar['maximum']}"
                )
                self._log(f"Tuần {xev.tuan}: ✓ {xev.message}", "ok")
            elif t == "save":
                self.var_status.set(f"Tuần {xev.tuan}: đang lưu…")
                self._log(f"  Tuần {xev.tuan}: {xev.message}", "info")
            elif t == "info":
                self._log(xev.message, "info")
            elif t == "warning":
                self._log(f"⚠ Tuần {xev.tuan}: {xev.message}", "warn")
            elif t == "error":
                self._log(f"❌ Tuần {xev.tuan}: {xev.message}", "err")
            elif t == "stop":
                self._log("Đã dừng theo yêu cầu", "warn")
            elif t == "plan_done":
                self._log(f"━━ {xev.message} ━━", "ok")
        elif kind == "done":
            report = ev[1]
            updated = int(getattr(report, "updated_count", 0) or 0)
            user_mod = int(getattr(report, "user_modified_count", 0) or 0)
            still = int(getattr(report, "still_no_data_count", 0) or 0)
            ctx_chg = int(getattr(report, "context_changed_count", 0) or 0)
            err = int(getattr(report, "error_count", 0) or 0)
            self.var_status.set(
                f"✓ Cập nhật fallback xong: "
                f"{updated} cập nhật, {user_mod} đã sửa thủ công, "
                f"{ctx_chg} đổi context, {still} chờ CSDL, {err} lỗi"
            )
            ctx_line = (
                f"\n• {ctx_chg} ô đã đổi lớp/môn/phân môn/PPCT → bỏ khỏi log."
                if ctx_chg else ""
            )
            if err > 0:
                messagebox.showwarning(
                    "Cập nhật xong (có lỗi)",
                    (
                        f"• {updated} ô có Tên bài mới đã được lưu.\n"
                        f"• {user_mod} ô đã được bạn sửa thủ công → bỏ khỏi log."
                        + ctx_line + "\n"
                        f"• {still} ô vẫn chưa có CSDL trên web → giữ trong log.\n"
                        f"• {err} ô gặp lỗi.\n\n"
                        "Xem khung Nhật ký để biết chi tiết."
                    ),
                    parent=self,
                )
            else:
                messagebox.showinfo(
                    "Cập nhật xong",
                    (
                        f"• {updated} ô có Tên bài mới đã được lưu.\n"
                        f"• {user_mod} ô đã được bạn sửa thủ công → bỏ khỏi log."
                        + ctx_line + "\n"
                        f"• {still} ô vẫn chưa có CSDL trên web → giữ trong log "
                        "để chạy lại sau khi quản trị viên cập nhật."
                    ),
                    parent=self,
                )
        elif kind == "error":
            self.var_status.set("❌ Lỗi cập nhật fallback")
            self._log(ev[1], "err")
            messagebox.showerror("Lỗi", ev[1].splitlines()[0], parent=self)

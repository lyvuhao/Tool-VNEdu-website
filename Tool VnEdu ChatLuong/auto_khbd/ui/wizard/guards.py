"""Chặn chạy song song các worker CDP; after/destroy an toàn."""

from __future__ import annotations

from tkinter import messagebox


class WorkerGuardMixin:
    """Chặn chạy song song các worker CDP; after/destroy an toàn."""

    # -----------------------------------------------------------
    # CDP Worker Guard
    # -----------------------------------------------------------

    def _is_any_cdp_worker_busy(self) -> bool:
        """True nếu bất kỳ worker nào đang dùng Chrome CDP."""
        workers = [
            self._bootstrap_worker,
            self._detect_worker,
            self._health_worker,
            self._exec_worker,
            self._refresh_fb_worker,
            self._fill_titles_worker,
            self._delete_worker,
        ]
        if any(w is not None and w.is_alive() for w in workers):
            return True
        # ImportTKBDialog tự host worker bên trong — check qua dialog ref.
        # Nếu dialog tồn tại và worker bên trong còn sống → busy.
        try:
            d = self._import_tkb_dialog
            if (d is not None and d.winfo_exists()
                    and getattr(d, "_worker", None) is not None
                    and d._worker.is_alive()):
                return True
        except Exception:
            pass
        # BackupRestoreDialog có 3 worker (backup/restore/smart-repair).
        # Worker nào còn alive → busy.
        try:
            d = self._backup_restore_dialog
            if d is not None and d.winfo_exists():
                for attr in ("_backup_worker", "_restore_worker", "_sr_worker"):
                    w = getattr(d, attr, None)
                    if w is not None and w.is_alive():
                        return True
        except Exception:
            pass
        return False

    def _is_executor_running(self) -> bool:
        """True nếu ExecutorWorker đang chạy (đang nhập web)."""
        return bool(self._exec_worker and self._exec_worker.is_alive())

    def _block_if_executor_running(self, action_name: str) -> bool:
        """Block + warn nếu executor đang chạy. True = blocked."""
        if not self._is_executor_running():
            return False
        messagebox.showwarning(
            "Đang nhập KHDH",
            f"Không thể {action_name} khi tool đang nhập KHDH lên web.\n\n"
            f"Hãy chờ hoàn tất hoặc bấm [Dừng] trước.",
            parent=self,
        )
        return True

    def _block_if_any_cdp_worker_busy(self, action_name: str) -> bool:
        """Block + warn nếu BẤT KỲ worker CDP nào đang chạy. True = blocked.

        Khác với `_block_if_executor_running` — chỉ check exec_worker,
        method này check tất cả workers để tránh user mở profile khác
        trong lúc tool đang dùng `_profile_path` cho refresh fallback /
        delete weeks. Nếu cho phép swap, worker đang chạy sẽ tiếp tục
        mutate log của profile cũ → mất đồng bộ + confusing UI.
        """
        if not self._is_any_cdp_worker_busy():
            return False
        running = []
        if self._bootstrap_worker and self._bootstrap_worker.is_alive():
            running.append("Đăng nhập VnEdu")
        if self._detect_worker and self._detect_worker.is_alive():
            running.append("Phát hiện PPCT")
        if self._exec_worker and self._exec_worker.is_alive():
            running.append("Nhập KHDH")
        if self._refresh_fb_worker and self._refresh_fb_worker.is_alive():
            running.append("Cập nhật Tên bài fallback")
        if self._fill_titles_worker and self._fill_titles_worker.is_alive():
            running.append("Điền Tên bài HĐTN thiếu")
        if self._delete_worker and self._delete_worker.is_alive():
            running.append("Xóa tuần KHDH")
        try:
            d = self._import_tkb_dialog
            if (d is not None and d.winfo_exists()
                    and getattr(d, "_worker", None) is not None
                    and d._worker.is_alive()):
                running.append("Quét TKB từ web")
        except Exception:
            pass
        running_text = ", ".join(running) or "một tác vụ"
        messagebox.showwarning(
            "Đang chạy",
            (
                f"Không thể {action_name} khi đang chạy: {running_text}.\n\n"
                "Hãy chờ tác vụ hiện tại hoàn tất hoặc bấm [Dừng] trước."
            ),
            parent=self,
        )
        return True

    def _guard_cdp_exclusive(self, action_name: str) -> bool:
        """Check và block nếu đã có worker CDP đang chạy.

        Returns:
            True nếu BLOCKED (caller nên return ngay).
            False nếu OK (caller tiếp tục spawn worker).
        """
        if not self._is_any_cdp_worker_busy():
            return False
        # Xác định worker nào đang chạy để thông báo rõ
        running = []
        if self._bootstrap_worker and self._bootstrap_worker.is_alive():
            running.append("Đăng nhập VnEdu")
        if self._detect_worker and self._detect_worker.is_alive():
            running.append("Phát hiện PPCT")
        if self._exec_worker and self._exec_worker.is_alive():
            running.append("Nhập KHDH")
        if self._refresh_fb_worker and self._refresh_fb_worker.is_alive():
            running.append("Cập nhật Tên bài fallback")
        if self._fill_titles_worker and self._fill_titles_worker.is_alive():
            running.append("Điền Tên bài HĐTN thiếu")
        if self._delete_worker and self._delete_worker.is_alive():
            running.append("Xóa tuần KHDH")
        running_text = ", ".join(running) or "một tác vụ"
        messagebox.showinfo(
            "Đang bận",
            f"Không thể [{action_name}] vì đang chạy: {running_text}.\n\n"
            f"Hãy chờ tác vụ hiện tại hoàn tất hoặc bấm [Dừng].",
            parent=self,
        )
        return True

    # -----------------------------------------------------------
    # Lifecycle — safe after + destroy cleanup
    # -----------------------------------------------------------

    def _safe_after(self, ms: int, func) -> str | None:
        """Schedule callback an toàn — track ID để cancel khi destroy."""
        if not self.winfo_exists():
            return None
        after_id = self.after(ms, func)
        self._pending_after_ids.append(after_id)
        return after_id

    def _on_destroy(self, event):
        """Cleanup khi widget bị destroy — cancel after + signal stop + join workers."""
        # Chỉ xử lý khi chính widget này bị destroy (không phải child)
        if event.widget is not self:
            return
        # v2: Đóng floating progress overlay nếu còn
        try:
            if self._progress_overlay is not None:
                self._progress_overlay.destroy()
                self._progress_overlay = None
        except Exception:
            pass
        try:
            if self._help_dialog is not None and self._help_dialog.winfo_exists():
                self._help_dialog.destroy()
                self._help_dialog = None
        except Exception:
            pass
        # Unbind các keyboard binding ở toplevel — tránh stale handler gọi
        # vào instance đã chết. Mỗi `unbind(seq, fid)` chỉ remove specific
        # handler, không cần xóa các binding của module/widget khác.
        try:
            toplevel = self.winfo_toplevel()
            for seq, fid in self._kb_bound_funcids:
                try:
                    toplevel.unbind(seq, fid)
                except Exception:
                    pass
            self._kb_bound_funcids.clear()
        except Exception:
            pass
        # Cancel tất cả pending after callbacks
        for aid in self._pending_after_ids:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._pending_after_ids.clear()
        # Signal stop cho tất cả workers
        try:
            self._exec_stop_event.set()
        except Exception:
            pass
        try:
            self._detect_stop.set()
        except Exception:
            pass
        try:
            self._refresh_fb_stop_event.set()
        except Exception:
            pass
        try:
            self._fill_titles_stop_event.set()
        except Exception:
            pass
        try:
            self._delete_stop_event.set()
        except Exception:
            pass
        # Join workers với timeout ngắn — cho Playwright cơ hội cleanup
        # CDP websocket trước khi process exit (tránh leak connection).
        # Timeout ngắn (1.5s mỗi worker) để không block UI shutdown quá lâu.
        for w in (
            self._bootstrap_worker,
            self._exec_worker,
            self._detect_worker,
            self._refresh_fb_worker,
            self._fill_titles_worker,
            self._delete_worker,
        ):
            try:
                if w is not None and w.is_alive():
                    w.join(timeout=1.5)
            except Exception:
                pass

    @staticmethod
    def _is_child_of(widget, parent) -> bool:
        """Check xem widget có phải là child (hoặc chính nó) của parent không."""
        try:
            w = widget
            while w is not None:
                if w is parent:
                    return True
                w = w.master
        except Exception:
            pass
        return False

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        """Parse int an toàn từ checkpoint dict — chống corrupt/manual edit.

        Trả về default nếu value là None, string non-numeric, list, dict.
        """
        if value is None:
            return default
        if isinstance(value, bool):
            # bool là subclass của int — reject để tránh nhầm True/False = 1/0
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value != value:  # NaN check
                return default
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except (ValueError, AttributeError):
                return default
        return default

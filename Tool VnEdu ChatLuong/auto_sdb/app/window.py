"""Kích thước/vị trí cửa sổ và đóng ứng dụng."""

import ctypes
import time

from ..config import (
    CLOSE_GRACE_PERIOD_MS,
    COMPACT_HEIGHT,
    COMPACT_WIDTH,
    EXPANDED_HEIGHT,
    EXPANDED_TOTAL_WIDTH,
)


class WindowMixin:
    """Kích thước/vị trí cửa sổ và đóng ứng dụng."""

    # -----------------------------------------------------------------
    # WINDOW MANAGEMENT
    # -----------------------------------------------------------------

    @staticmethod
    def _coerce_window_geometry(current_rect, screen_rect, target_rect, margin=10, min_visible=120):
        """Chuẩn hóa geometry để cửa sổ không bị tụt khỏi màn hình hoặc co bất thường."""
        screen_x, screen_y, screen_w, screen_h = [int(v) for v in screen_rect]
        target_w, target_h, target_x, target_y = [int(v) for v in target_rect]

        max_w = max(220, screen_w - (margin * 2))
        max_h = max(220, screen_h - (margin * 2))
        target_w = max(220, min(target_w, max_w))
        target_h = max(220, min(target_h, max_h))
        target_x = min(max(target_x, screen_x + margin), screen_x + screen_w - target_w - margin)
        target_y = min(max(target_y, screen_y + margin), screen_y + screen_h - target_h - margin)

        if not current_rect:
            return target_w, target_h, target_x, target_y, True

        cur_x, cur_y, cur_w, cur_h = [int(v) for v in current_rect]
        compact_mode = target_h <= (COMPACT_HEIGHT + 20)
        min_width = target_w if not compact_mode else min(max(320, COMPACT_WIDTH), target_w)
        min_height = (
            target_h if compact_mode
            else min(target_h, max(520, int(target_h * 0.80)))
        )
        width_mismatch = abs(cur_w - target_w) > 8
        invalid_size = cur_w < min_width or cur_h < min_height or width_mismatch
        offscreen_h = (cur_x + min_visible) < screen_x or cur_x > (screen_x + screen_w - min_visible)
        offscreen_v = (cur_y + 40) < screen_y or cur_y > (screen_y + screen_h - min_visible)
        if invalid_size or offscreen_h or offscreen_v:
            return target_w, target_h, target_x, target_y, True

        clamped_x = min(max(cur_x, screen_x + margin), screen_x + screen_w - cur_w - margin)
        clamped_y = min(max(cur_y, screen_y + margin), screen_y + screen_h - cur_h - margin)
        changed = clamped_x != cur_x or clamped_y != cur_y
        return cur_w, cur_h, clamped_x, clamped_y, changed

    def _get_virtual_screen_bounds(self):
        """Lấy work-area bounds theo hệ tọa độ Tk, tránh bị taskbar che.

        Tk trên Windows thường chạy theo logical pixels (ví dụ màn 1920x1080
        có thể báo `winfo_screenheight() == 720`). Win32 trả work area theo
        physical pixels, nên phải quy đổi về cùng hệ tọa độ trước khi gọi
        `geometry()`.
        """
        try:
            user32 = ctypes.windll.user32
            phys_x = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
            phys_y = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
            phys_w = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
            phys_h = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.c_ulong),
                ]

            hwnd = int(self.root.winfo_id())
            monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = info.rcWork
            else:
                rect = RECT()
                if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    raise RuntimeError("SPI_GETWORKAREA failed")

            tk_w = max(int(self.root.winfo_screenwidth()), 1)
            tk_h = max(int(self.root.winfo_screenheight()), 1)
            scale_x = tk_w / max(phys_w, 1)
            scale_y = tk_h / max(phys_h, 1)
            x = int(round((int(rect.left) - phys_x) * scale_x))
            y = int(round((int(rect.top) - phys_y) * scale_y))
            w = int(round((int(rect.right) - int(rect.left)) * scale_x))
            h = int(round((int(rect.bottom) - int(rect.top)) * scale_y))
            if w > 0 and h > 0:
                return (x, y, w, h)
        except Exception:
            pass
        return (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _build_target_window_geometry(self, compact=None):
        """Tính geometry đích chuẩn cho mode compact/expanded hiện tại."""
        compact_mode = self.is_compact if compact is None else bool(compact)
        screen_x, screen_y, screen_w, screen_h = self._get_virtual_screen_bounds()
        target_w = COMPACT_WIDTH if compact_mode else EXPANDED_TOTAL_WIDTH
        target_w = min(target_w, max(320, screen_w - 50))
        target_h = (
            COMPACT_HEIGHT
            if compact_mode
            else min(EXPANDED_HEIGHT, max(screen_h - 96, 560))
        )
        target_h = min(target_h, max(220, screen_h - 72))
        target_x = screen_x + max(10, screen_w - target_w - 30)
        target_y = screen_y + 5
        return (screen_x, screen_y, screen_w, screen_h), (target_w, target_h, target_x, target_y)

    def _apply_window_geometry(self, compact=None, force=False):
        """Áp geometry hợp lệ và khóa width theo mode hiện tại để tránh cửa sổ bị co lệch."""
        try:
            self.root.deiconify()
        except Exception:
            pass
        self.root.update_idletasks()

        screen_rect, target_rect = self._build_target_window_geometry(compact=compact)
        try:
            current_rect = (
                int(self.root.winfo_x()),
                int(self.root.winfo_y()),
                int(self.root.winfo_width()),
                int(self.root.winfo_height()),
            )
        except Exception:
            current_rect = None

        compact_mode = self.is_compact if compact is None else bool(compact)
        if force:
            win_w, win_h, x, y = target_rect
            changed = True
        else:
            win_w, win_h, x, y, changed = self._coerce_window_geometry(
                current_rect=current_rect,
                screen_rect=screen_rect,
                target_rect=target_rect,
            )
        if force or changed:
            try:
                self.root.minsize(1, 1)
                self.root.maxsize(max(int(screen_rect[2]), win_w), max(int(screen_rect[3]), win_h))
            except Exception:
                pass
            self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")
            self.root.update_idletasks()

        min_h = (
            COMPACT_HEIGHT
            if compact_mode
            else min(target_rect[1], max(520, int(target_rect[1] * 0.80)))
        )
        min_w = COMPACT_WIDTH if compact_mode else min(EXPANDED_TOTAL_WIDTH, max(860, win_w))
        self.root.minsize(min_w, min_h)
        try:
            max_w = win_w if compact_mode else max(int(screen_rect[2] - 20), min_w)
            self.root.maxsize(max_w, max(int(screen_rect[3] - 60), min_h))
        except Exception:
            pass

    def _ensure_window_visible(self, force=False):
        """Tự cứu geometry nếu cửa sổ bị co cực nhỏ hoặc trôi khỏi vùng nhìn thấy."""
        if self._closing or not self._root_exists():
            return
        self._apply_window_geometry(compact=self.is_compact, force=force)

    def _position_window(self):
        """Đặt cửa sổ gọn gàng phía bên phải màn hình.

        Chiều rộng = EXPANDED_TOTAL_WIDTH (vừa đủ 2-panel).
        Chiều cao = screen_height - 80px (trừ taskbar + padding).
        Vị trí: sát mép phải.
        """
        self._apply_window_geometry(compact=self.is_compact, force=True)

    def _has_active_background_work(self):
        """Kiểm tra còn worker nền nào đang chạy để đóng app an toàn hơn."""
        thread_flags = [
            bool(self._schedule_thread and self._schedule_thread.is_alive()),
            bool(self._class_stats_thread and self._class_stats_thread.is_alive()),
            bool(self._auto_login_thread and self._auto_login_thread.is_alive()),
            bool(self._delete_thread and self._delete_thread.is_alive()),
        ]
        return any(thread_flags) or any([
            self._schedule_running,
            self._class_stats_running,
            self._auto_login_running,
            self._quick_prepare_running,
            self._delete_running,
            not self._schedule_queue.empty(),
            not self._class_stats_queue.empty(),
            not self._delete_queue.empty(),
        ])

    def _finalize_close_when_idle(self):
        """Chờ worker flush checkpoint rồi mới hủy root."""
        if not self._root_exists():
            return
        elapsed_ms = (time.time() - float(self._close_started_at or time.time())) * 1000.0
        if self._has_active_background_work() and elapsed_ms < CLOSE_GRACE_PERIOD_MS:
            self.root.after(150, self._finalize_close_when_idle)
            return

        self._cancel_sched_teacher_progress_jobs()
        self._destroy_class_stats_dialog()
        self._destroy_delete_dialog()
        self._save_config()
        if self._ui_task_pump_after_id:
            try:
                self.root.after_cancel(self._ui_task_pump_after_id)
            except Exception:
                pass
            self._ui_task_pump_after_id = None
        self.root.destroy()

    def _on_close(self):
        """Xử lý đóng app: dừng schedule + save config."""
        if self._closing:
            return
        self._closing = True
        self._close_started_at = time.time()
        self._cancel_sched_teacher_progress_jobs()
        self._sched_teacher_progress_request_id += 1
        if self._schedule_running:
            self._request_schedule_stop("đóng ứng dụng")
        if self._delete_running:
            self._delete_stop_event.set()
        self.root.after(150, self._finalize_close_when_idle)

    def run(self):
        """Chạy main loop."""
        self._log("App khởi động — sẵn sàng", "success")
        self.root.mainloop()

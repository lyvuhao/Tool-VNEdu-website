"""Thông báo toast và đóng ứng dụng."""

from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import messagebox

from ..config import UI_FONT_FAMILY
from ..log import _get_logger


class LifecycleMixin:
    """Thông báo toast và đóng ứng dụng."""

    def _log_error(self, title: str, error: Exception) -> None:
        detail = str(error).strip() or type(error).__name__
        self.status_var.set(detail)
        _get_logger().error("%s: %s", title, error, exc_info=True)
        messagebox.showerror(title, detail, parent=self.root)

    def _toast(self, message: str, duration_ms: int = 2500) -> None:
        """Non-blocking status toast that auto-dismisses."""

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#1e293b")
        label = tk.Label(
            toast, text=message, bg="#1e293b", fg="#f8fafc",
            font=(UI_FONT_FAMILY, 10), padx=18, pady=10,
        )
        label.pack()
        toast.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width() - toast.winfo_width() - 16
        y = self.root.winfo_rooty() + self.root.winfo_height() - toast.winfo_height() - 16
        toast.geometry(f"+{x}+{y}")
        # Fade-in effect
        toast.attributes("-alpha", 0.0)
        def _fade_in(step: int = 0) -> None:
            alpha = min(1.0, step * 0.15)
            try:
                toast.attributes("-alpha", alpha)
                if alpha < 1.0:
                    toast.after(30, lambda: _fade_in(step + 1))
            except tk.TclError:
                pass
        _fade_in()
        toast.after(duration_ms, lambda: self._dismiss_toast(toast))

    def _dismiss_toast(self, toast: tk.Toplevel) -> None:
        """Fade-out and destroy a toast notification."""
        def _fade_out(step: int = 7) -> None:
            alpha = max(0.0, step * 0.15)
            try:
                toast.attributes("-alpha", alpha)
                if alpha > 0.0:
                    toast.after(30, lambda: _fade_out(step - 1))
                else:
                    toast.destroy()
            except tk.TclError:
                pass
        _fade_out()

    def _on_close(self) -> None:
        running = [k for k, p in self.tool_processes.items() if p.poll() is None]
        if running:
            if not messagebox.askyesno(
                "Thoát dashboard",
                f"Có {len(running)} tool đang chạy. Bạn có muốn thoát?",
                parent=self.root,
            ):
                return
        if self._tool_process_poll_after_id is not None:
            try:
                self.root.after_cancel(self._tool_process_poll_after_id)
            except tk.TclError:
                pass
            self._tool_process_poll_after_id = None
        if self._health_after_id is not None:
            try:
                self.root.after_cancel(self._health_after_id)
            except tk.TclError:
                pass
            self._health_after_id = None
        _get_logger().info("Dashboard closed.")
        self._cleanup_all_children()
        self.root.destroy()

    def _cleanup_all_children(self) -> None:
        """Best-effort cleanup of all child tool processes."""

        for process in self.processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            except Exception:  # noqa: BLE001 - cleanup must not crash.
                pass

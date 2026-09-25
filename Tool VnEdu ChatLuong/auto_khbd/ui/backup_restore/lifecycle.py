"""Đóng hộp thoại."""

from __future__ import annotations


class DialogLifecycleMixin:
    """Đóng hộp thoại."""

    # -----------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------

    def _on_esc(self, _event=None):
        if self._backup_worker and self._backup_worker.is_alive():
            self._on_backup_stop()
            return
        if self._restore_worker and self._restore_worker.is_alive():
            self._restore_stop.set()
            return
        self._on_close()

    def _on_close(self):
        if self._backup_worker and self._backup_worker.is_alive():
            self._backup_stop.set()
        if self._restore_worker and self._restore_worker.is_alive():
            self._restore_stop.set()
        if self._poll_after_id:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
        self.destroy()

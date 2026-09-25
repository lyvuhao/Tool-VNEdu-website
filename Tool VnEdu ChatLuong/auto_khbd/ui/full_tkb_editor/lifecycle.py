"""Đóng cửa sổ và dọn dẹp."""

from __future__ import annotations


class EditorLifecycleMixin:
    """Đóng cửa sổ và dọn dẹp."""

    # -----------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------

    def _on_escape(self, _event=None):
        # Nếu đang drag → chỉ cancel drag.
        if self._drag_state is not None and self._drag_state.get("started"):
            target = self._drag_state.get("last_target")
            self._drag_state = None
            if target is not None:
                self._restyle_button(*target)
            return
        # Else đóng cửa sổ
        self.destroy()

    def _on_destroy(self, event):
        # Chỉ xử lý khi chính window destroy (không phải child)
        if event.widget is not self:
            return
        # Unbind keyboard shortcuts ở Toplevel — tránh stale handler.
        for seq, fid in self._kb_bound_funcids:
            try:
                self.unbind(seq, fid)
            except Exception:
                pass
        self._kb_bound_funcids.clear()
        # Drop refs để GC kịp dọn
        self._panels.clear()
        self._drag_state = None
        self._context_menu = None

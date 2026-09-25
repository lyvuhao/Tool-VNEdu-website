"""Cuộn khung và thu gọn / mở rộng cửa sổ."""


class ScrollMixin:
    """Cuộn khung và thu gọn / mở rộng cửa sổ."""

    # -----------------------------------------------------------------
    # SCROLL HELPERS
    # -----------------------------------------------------------------

    def _on_frame_configure(self, event):
        """Cập nhật scroll region khi nội dung main_frame thay đổi kích thước."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Đảm bảo main_frame luôn rộng bằng canvas (tránh khoảng trống)."""
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """Cuộn nội dung panel trái bằng mouse wheel."""
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_right_mousewheel(self, event):
        """Cuộn nội dung panel phải (Schedule) bằng mouse wheel."""
        self._right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_mousewheel_recursive(self, widget, handler):
        """Bind mousewheel handler cho widget và tất cả widget con (đệ quy).

        Đảm bảo scroll hoạt động khi chuột hover lên bất kỳ widget con nào
        trong panel, không chỉ trên canvas/frame gốc.
        """
        widget.bind("<MouseWheel>", handler)
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child, handler)

    # -----------------------------------------------------------------
    # COMPACT / EXPAND
    # -----------------------------------------------------------------

    def _toggle_compact(self):
        """Toggle thu gọn / mở rộng."""
        self.is_compact = not self.is_compact

        if self.is_compact:
            # Thu gọn: chuyển sang mini dashboard thay vì để canvas trống.
            self._left_panel.grid_forget()
            self._right_panel.grid_forget()
            self._compact_shell.grid(row=0, column=0, columnspan=2, sticky="nsew")

            self.btn_compact.config(text="Mở đầy đủ")
            self._apply_window_geometry(compact=True, force=True)
            self._log("Thu gọn UI", "info")
        else:
            # Mở rộng: hiện lại theo thứ tự hiện hành
            self._compact_shell.grid_forget()
            self._left_panel.grid(row=0, column=0, sticky="nsew")
            self.frame_buttons.pack_forget()

            self.frame_cdp.pack(fill="x", pady=(0, 4))
            self.frame_buttons.pack(anchor="e", pady=(0, 4))
            self.frame_class_stats.pack(fill="x", pady=(0, 6))
            self.frame_log.pack(fill="x", expand=False, pady=(0, 0))
            self._right_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
            self._canvas.yview_moveto(0)

            self.btn_compact.config(text="Thu gọn")
            self._apply_window_geometry(compact=False, force=True)
            self._log("Mở rộng UI", "info")

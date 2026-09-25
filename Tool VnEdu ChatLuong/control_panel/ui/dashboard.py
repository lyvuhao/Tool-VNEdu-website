"""Lọc, nhóm và vẽ thẻ tool trên dashboard."""

from __future__ import annotations

import tkinter as tk
import unicodedata
from tkinter import ttk

from ..config import SURFACE_COLOR, UI_FONT_FAMILY
from ..tool_registry import TOOL_FILES


class DashboardMixin:
    """Lọc, nhóm và vẽ thẻ tool trên dashboard."""

    def _dashboard_tool_items(self) -> list[tuple[str, dict[str, str], bool]]:
        """Return built-in and user-added tools in dashboard display order."""

        items: list[tuple[str, dict[str, str], bool]] = [
            (tool_name, metadata, False) for tool_name, metadata in TOOL_FILES.items()
        ]
        for tool_id, metadata in self.custom_tools.items():
            items.append((tool_id, metadata, True))
        return items

    def _normalized_search_text(self, text: str) -> str:
        """Normalize Vietnamese text for loose dashboard filtering."""

        text = text.replace("đ", "d").replace("Đ", "D")
        decomposed = unicodedata.normalize("NFKD", text)
        ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        return ascii_text.casefold()

    def _tool_matches_filter(self, tool_name: str, metadata: dict[str, str], *, custom: bool) -> bool:
        """Return whether one tool should be visible for the current search filter."""

        query = self._normalized_search_text(self.tool_filter_var.get().strip())
        if not query:
            return True
        haystack = " ".join(
            [
                metadata.get("title", ""),
                metadata.get("description", ""),
                metadata.get("script", ""),
                "tool them moi" if custom else "tool mac dinh",
                tool_name,
            ]
        )
        return query in self._normalized_search_text(haystack)

    def _dashboard_tool_groups(self) -> list[tuple[str, list[tuple[str, dict[str, str], bool]]]]:
        """Return grouped and filtered dashboard tools."""

        default_tools: list[tuple[str, dict[str, str], bool]] = []
        custom_tools: list[tuple[str, dict[str, str], bool]] = []
        for tool_name, metadata in TOOL_FILES.items():
            if self._tool_matches_filter(tool_name, metadata, custom=False):
                default_tools.append((tool_name, metadata, False))
        for tool_id, metadata in self.custom_tools.items():
            if self._tool_matches_filter(tool_id, metadata, custom=True):
                custom_tools.append((tool_id, metadata, True))
        return [("TOOL MẶC ĐỊNH", default_tools), ("TOOL THÊM MỚI", custom_tools)]

    def _card_state_fingerprint(self) -> str:
        """Build a lightweight fingerprint of card-relevant state to skip no-op rebuilds."""

        parts: list[str] = []
        for tool_name in TOOL_FILES:
            rk = self._tool_runtime_key(tool_name, custom=False)
            running = "R" if rk in self._running_tool_keys_snapshot else "_"
            parts.append(f"d:{tool_name}:{running}")
        for tool_id in self.custom_tools:
            rk = self._tool_runtime_key(tool_id, custom=True)
            running = "R" if rk in self._running_tool_keys_snapshot else "_"
            parts.append(f"c:{tool_id}:{running}")
        parts.append(f"filter:{self.tool_filter_var.get()}")
        parts.append(f"mode:{self.advanced_mode_var.get()}")
        return "|".join(parts)

    def _render_dashboard_cards(self, cards_frame: ttk.Frame) -> None:
        """Render grouped dashboard cards, skipping rebuild when state is unchanged."""

        fingerprint = self._card_state_fingerprint()
        if fingerprint == self._prev_card_fingerprint:
            return
        self._prev_card_fingerprint = fingerprint

        for child in cards_frame.winfo_children():
            child.destroy()
        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)

        row = 0
        visible_count = 0
        for group_title, items in self._dashboard_tool_groups():
            if not items:
                continue
            for index, (tool_name, metadata, is_custom) in enumerate(items):
                self._tool_card(cards_frame, tool_name, row + index // 2, index % 2, metadata=metadata, custom=is_custom)
                visible_count += 1
            row += (len(items) + 1) // 2

        if visible_count == 0:
            empty_panel, empty_frame = self._outlined_panel(cards_frame, padding=(18, 16, 18, 16), height=82)
            empty_panel.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            ttk.Label(
                empty_frame,
                text="Không có tool phù hợp.",
                style="PanelMuted.TLabel",
                font=(UI_FONT_FAMILY, 10, "bold"),
            ).pack(anchor="w")
        self.tool_count_var.set(f"{visible_count} tool")

    def _scrollable_tool_area(self, parent: ttk.Frame) -> tuple[ttk.Frame, ttk.Frame]:
        """Create a fixed-height, mouse-wheel-scrollable area for tool cards."""

        container = ttk.Frame(parent, style="App.TFrame")
        container.columnconfigure(0, weight=1)
        area_height = 242 if self.advanced_mode_var.get() else 218
        canvas = tk.Canvas(container, bg=SURFACE_COLOR, height=area_height, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)

        inner = ttk.Frame(canvas, style="App.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        _scroll_debounce_id: list[str | None] = [None]

        def _do_update_scroll() -> None:
            _scroll_debounce_id[0] = None
            bbox = canvas.bbox("all")
            canvas.configure(scrollregion=bbox)
            content_height = 0 if bbox is None else bbox[3] - bbox[1]
            if content_height > canvas.winfo_height() + 4:
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
            else:
                scrollbar.grid_remove()

        def update_scroll_region(_event: object | None = None) -> None:
            if _scroll_debounce_id[0] is not None:
                try:
                    canvas.after_cancel(_scroll_debounce_id[0])
                except tk.TclError:
                    pass
            _scroll_debounce_id[0] = canvas.after(16, _do_update_scroll)

        def resize_inner(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)
            update_scroll_region()

        def on_mouse_wheel(event: tk.Event) -> None:
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        def _scoped_mouse_wheel(event: tk.Event) -> None:
            if self._active_scroll_canvas is canvas:
                delta = -1 if event.delta > 0 else 1
                canvas.yview_scroll(delta, "units")

        def bind_mouse_wheel(_event: object) -> None:
            self._active_scroll_canvas = canvas
            canvas.bind_all("<MouseWheel>", _scoped_mouse_wheel)

        def unbind_mouse_wheel(_event: object) -> None:
            if self._active_scroll_canvas is canvas:
                self._active_scroll_canvas = None

        inner.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", resize_inner)
        canvas.bind("<Enter>", bind_mouse_wheel)
        canvas.bind("<Leave>", unbind_mouse_wheel)
        inner.bind("<Enter>", bind_mouse_wheel)
        inner.bind("<Leave>", unbind_mouse_wheel)
        return container, inner

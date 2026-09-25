"""Widget dựng sẵn: panel, nút, chip, hộp thoại thông báo/nhập."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..config import (
    BORDER_COLOR,
    BUTTON_BORDER_COLOR,
    BUTTON_HOVER_COLOR,
    MUTED_TEXT_COLOR,
    PANEL_COLOR,
    SURFACE_COLOR,
    TEXT_COLOR,
    UI_FONT_FAMILY,
    UI_FONT_SEMIBOLD,
)


class WidgetsMixin:
    """Widget dựng sẵn: panel, nút, chip, hộp thoại thông báo/nhập."""

    def _safe_after(self, ms: int, callback: Callable[..., None]) -> str | None:
        """Schedule callback on Tk main thread, suppress TclError if root destroyed."""

        try:
            if self.root.winfo_exists():
                return self.root.after(ms, callback)
        except tk.TclError:
            pass
        return None

    def _clear_root(self) -> None:
        """Remove the current screen before switching login/dashboard views."""

        for child in self.root.winfo_children():
            child.destroy()

    def _outlined_panel(
        self,
        parent: tk.Misc,
        *,
        padding: tuple[int, int, int, int] = (16, 14, 16, 14),
        height: int | None = None,
    ) -> tuple[tk.Frame, ttk.Frame]:
        """Create a bordered white panel and return its outer and inner frames."""

        outer = tk.Frame(
            parent,
            bg=PANEL_COLOR,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        if height is not None:
            outer.configure(height=height)
            outer.grid_propagate(False)
            outer.pack_propagate(False)
        inner = ttk.Frame(outer, style="Panel.TFrame", padding=padding)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _flat_button(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        width: int | None = None,
        primary: bool = False,
        compact: bool = False,
        disabled: bool = False,
    ) -> tk.Frame:
        """Create a bordered lightweight button that matches the older dashboard look."""

        bg = "#f1f5f9" if disabled else ("#ffffff" if not primary else "#eef4ff")
        fg = "#64748b" if disabled else ("#0f172a" if not primary else "#1d4ed8")
        hover_bg = bg if disabled else (BUTTON_HOVER_COLOR if not primary else "#e0ecff")
        display_text = text.upper()
        button = tk.Frame(
            parent,
            bg=bg,
            highlightthickness=1,
            highlightbackground=BUTTON_BORDER_COLOR,
            highlightcolor=BUTTON_BORDER_COLOR,
            cursor="arrow" if disabled else "hand2",
        )
        label = tk.Label(
            button,
            text=display_text,
            width=width or 0,
            bg=bg,
            fg=fg,
            font=(UI_FONT_FAMILY, 10, "bold" if primary else "normal"),
            padx=7 if compact else 14,
            pady=3 if compact else 7,
            cursor="arrow" if disabled else "hand2",
        )
        label.pack(fill="both", expand=True)

        def set_bg(color: str) -> None:
            button.configure(bg=color)
            label.configure(bg=color)

        def invoke(_event: object | None = None) -> None:
            if disabled:
                return
            command()

        for widget in (button, label):
            widget.bind("<Button-1>", invoke)
            widget.bind("<Enter>", lambda _event: set_bg(hover_bg))
            widget.bind("<Leave>", lambda _event: set_bg(bg))
        return button

    def _chip(self, parent: tk.Misc, text: str, column: int) -> None:
        """Render one compact session chip in the dashboard header."""

        label = tk.Label(
            parent,
            text=text,
            bg=PANEL_COLOR,
            fg=MUTED_TEXT_COLOR,
            font=(UI_FONT_FAMILY, 9),
            padx=10,
            pady=4,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        label.grid(row=0, column=column, sticky="w", padx=(0, 6), pady=(7, 0))

    def _styled_info(
        self,
        title: str,
        message: str,
        *,
        parent: tk.Misc | None = None,
        accent: str = "#16a34a",
    ) -> None:
        """Show a styled info dialog matching the dashboard visual language."""

        anchor = parent or self.root
        dlg = tk.Toplevel(anchor)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=SURFACE_COLOR)
        dlg.transient(anchor)
        dlg.grab_set()

        # --- accent bar ---
        tk.Frame(dlg, bg=accent, height=4).pack(fill="x")

        shell = ttk.Frame(dlg, style="App.TFrame", padding=(26, 20, 26, 20))
        shell.pack(fill="both", expand=True)

        # --- icon + title row ---
        header = ttk.Frame(shell, style="App.TFrame")
        header.pack(fill="x")
        icon_canvas = tk.Canvas(header, width=28, height=28, bg=SURFACE_COLOR, highlightthickness=0, bd=0)
        icon_canvas.pack(side="left", padx=(0, 12))
        icon_canvas.create_oval(2, 2, 26, 26, fill=accent, outline=accent)
        icon_canvas.create_text(14, 14, text="✓", fill="#ffffff", font=(UI_FONT_FAMILY, 14, "bold"))
        tk.Label(
            header,
            text=title,
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            font=(UI_FONT_SEMIBOLD, 14, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # --- message body ---
        tk.Frame(shell, bg=BORDER_COLOR, height=1).pack(fill="x", pady=(14, 14))
        tk.Label(
            shell,
            text=message,
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            font=(UI_FONT_FAMILY, 11),
            justify="left",
            anchor="nw",
            wraplength=380,
        ).pack(fill="x")

        # --- OK button ---
        btn_row = ttk.Frame(shell, style="App.TFrame")
        btn_row.pack(fill="x", pady=(18, 0))
        self._flat_button(btn_row, text="OK", command=dlg.destroy, primary=True, width=10).pack(side="right")

        dlg.update_idletasks()
        x = anchor.winfo_rootx() + max((anchor.winfo_width() - dlg.winfo_width()) // 2, 0)
        y = anchor.winfo_rooty() + max((anchor.winfo_height() - dlg.winfo_height()) // 3, 0)
        dlg.geometry(f"+{x}+{y}")
        dlg.bind("<Return>", lambda _e: dlg.destroy())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.wait_window()

    def _styled_ask_string(
        self,
        title: str,
        prompt: str,
        *,
        parent: tk.Misc | None = None,
        show: str = "",
    ) -> str | None:
        """Show a styled text-input dialog matching the dashboard visual language."""

        anchor = parent or self.root
        dlg = tk.Toplevel(anchor)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=SURFACE_COLOR)
        dlg.transient(anchor)
        dlg.grab_set()

        result: dict[str, str | None] = {"value": None}

        # --- accent bar ---
        tk.Frame(dlg, bg="#2563eb", height=4).pack(fill="x")

        shell = ttk.Frame(dlg, style="App.TFrame", padding=(26, 20, 26, 20))
        shell.pack(fill="both", expand=True)

        # --- title ---
        tk.Label(
            shell,
            text=title,
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            font=(UI_FONT_SEMIBOLD, 14, "bold"),
            anchor="w",
        ).pack(fill="x")

        # --- prompt ---
        tk.Label(
            shell,
            text=prompt,
            bg=SURFACE_COLOR,
            fg=MUTED_TEXT_COLOR,
            font=(UI_FONT_FAMILY, 10),
            anchor="w",
            wraplength=340,
        ).pack(fill="x", pady=(8, 10))

        # --- input ---
        entry_var = tk.StringVar()
        entry = ttk.Entry(shell, textvariable=entry_var, width=40, show=show)
        entry.pack(fill="x")
        entry.focus_set()

        def on_ok(_event: object | None = None) -> None:
            result["value"] = entry_var.get()
            dlg.destroy()

        def on_cancel(_event: object | None = None) -> None:
            dlg.destroy()

        # --- buttons ---
        btn_row = ttk.Frame(shell, style="App.TFrame")
        btn_row.pack(fill="x", pady=(18, 0))
        self._flat_button(btn_row, text="Hủy", command=on_cancel, width=8).pack(side="right", padx=(8, 0))
        self._flat_button(btn_row, text="OK", command=on_ok, primary=True, width=8).pack(side="right")

        entry.bind("<Return>", on_ok)
        dlg.bind("<Escape>", on_cancel)

        dlg.update_idletasks()
        x = anchor.winfo_rootx() + max((anchor.winfo_width() - dlg.winfo_width()) // 2, 0)
        y = anchor.winfo_rooty() + max((anchor.winfo_height() - dlg.winfo_height()) // 3, 0)
        dlg.geometry(f"+{x}+{y}")
        dlg.wait_window()
        return result["value"]

    def _login_title_canvas(self, parent: tk.Misc) -> tk.Canvas:
        """Create the centered login title with a subtle text outline."""

        canvas = tk.Canvas(parent, width=410, height=38, bg=SURFACE_COLOR, highlightthickness=0, bd=0)
        title = "ĐĂNG NHẬP VNEDU"
        font = (UI_FONT_SEMIBOLD, 18, "bold")
        center_x = 205
        center_y = 21
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            canvas.create_text(center_x + dx, center_y + dy, text=title, fill="#c8d8ee", font=font, anchor="center")
        canvas.create_text(center_x, center_y, text=title, fill="#0b1f3a", font=font, anchor="center")
        return canvas

    def _lock_icon(self, parent: tk.Misc) -> tk.Canvas:
        """Create a small vector lock icon for the login subtitle."""

        canvas = tk.Canvas(parent, width=18, height=18, bg=SURFACE_COLOR, highlightthickness=0, bd=0)
        canvas.create_arc(4, 2, 14, 13, start=0, extent=180, outline="#2563eb", width=2, style="arc")
        canvas.create_rectangle(3, 8, 15, 16, outline="#2563eb", fill="#e8f1ff", width=1)
        canvas.create_oval(8, 11, 10, 13, outline="#1d4ed8", fill="#1d4ed8")
        return canvas

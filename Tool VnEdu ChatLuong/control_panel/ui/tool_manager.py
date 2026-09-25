"""Quản lý và xoá tool tuỳ chỉnh."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..config import CUSTOM_TOOLS_DIR_NAME, EMBEDDED_STATUS_COLORS, SURFACE_COLOR, UI_FONT_FAMILY
from ..custom_tools import (
    custom_tool_external_path,
    load_custom_tools,
    normalize_custom_script_path,
    save_custom_tools,
)
from ..storage import embedded_tool_dir
from ..tool_registry import TOOL_FILES


class ToolManagerMixin:
    """Quản lý và xoá tool tuỳ chỉnh."""

    def _open_tool_manager_dialog(self) -> None:
        """Open a focused management surface for all built-in and custom tools."""

        self.custom_tools = load_custom_tools()
        dialog = tk.Toplevel(self.root)
        dialog.title("Quản lý tool")
        dialog.resizable(False, False)
        dialog.configure(background=SURFACE_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        manager_filter_var = tk.StringVar(value=self.tool_filter_var.get().strip())
        manager_count_var = tk.StringVar()

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(22, 18, 22, 18))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)

        header = ttk.Frame(shell, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Quản lý tool",
            style="DialogTitle.TLabel",
            font=(UI_FONT_FAMILY, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self._flat_button(
            header,
            text="Thêm tool",
            command=lambda: (dialog.destroy(), self._open_add_tool_dialog()),
            width=8,
        ).grid(row=0, column=1, sticky="e")
        ttk.Label(
            shell,
            text="Theo dõi file ngoài, bản nhúng và chỉnh tool thêm mới tại một nơi.",
            style="Muted.TLabel",
            wraplength=560,
        ).grid(row=1, column=0, sticky="w", pady=(5, 12))

        filter_row = ttk.Frame(shell, style="App.TFrame")
        filter_row.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="TÌM TOOL", style="Muted.TLabel", font=(UI_FONT_FAMILY, 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        manager_entry = ttk.Entry(filter_row, textvariable=manager_filter_var, width=28)
        manager_entry.grid(row=0, column=1, sticky="w")
        ttk.Label(filter_row, textvariable=manager_count_var, style="Footer.TLabel").grid(row=0, column=2, sticky="e")

        list_container = ttk.Frame(shell, style="App.TFrame")
        list_container.grid(row=3, column=0, sticky="ew")
        list_container.columnconfigure(0, weight=1)
        canvas = tk.Canvas(list_container, bg=SURFACE_COLOR, height=245, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="ew")
        inner = ttk.Frame(canvas, style="App.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def update_scroll_region(_event: object | None = None) -> None:
            bbox = canvas.bbox("all")
            canvas.configure(scrollregion=bbox)
            content_height = 0 if bbox is None else bbox[3] - bbox[1]
            if content_height > canvas.winfo_height() + 4:
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
            else:
                scrollbar.grid_remove()

        def resize_inner(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)
            update_scroll_region()

        canvas.bind("<Configure>", resize_inner)
        inner.bind("<Configure>", update_scroll_region)

        def item_matches(tool_id: str, metadata: dict[str, str], *, custom: bool) -> bool:
            query = self._normalized_search_text(manager_filter_var.get().strip())
            if not query:
                return True
            haystack = " ".join(
                [
                    metadata.get("title", ""),
                    metadata.get("description", ""),
                    metadata.get("script", ""),
                    tool_id,
                    "tool them moi" if custom else "tool mac dinh",
                ]
            )
            return query in self._normalized_search_text(haystack)

        def open_replace(tool_id: str) -> None:
            dialog.destroy()
            self._open_replace_tool_dialog(tool_id)

        def delete_custom(tool_id: str) -> None:
            metadata = self.custom_tools.get(tool_id)
            if metadata is None:
                messagebox.showwarning("Không tìm thấy card", "Card tool này không còn tồn tại.", parent=dialog)
                return
            title = metadata.get("title", tool_id)
            confirmed = messagebox.askyesno(
                "Xóa card tool",
                (
                    f"Xóa card \"{title}\" khỏi dashboard?\n\n"
                    "App chỉ xóa bản copy và bản nhúng do dashboard quản lý. File gốc bạn đã chọn sẽ không bị xóa."
                ),
                parent=dialog,
            )
            if not confirmed:
                return
            try:
                removed_title, cleanup_errors = self._remove_custom_tool(tool_id)
            except Exception as error:  # noqa: BLE001
                self._log_error(f"Không xóa được {title}", error)
                return
            self.custom_tools = load_custom_tools()
            self.status_var.set(f"Đã xóa card tool: {removed_title}.")
            dialog.destroy()
            self._build_dashboard_screen()
            if cleanup_errors:
                messagebox.showwarning(
                    "Đã xóa card",
                    "Card đã được xóa, nhưng có file phụ chưa dọn được:\n\n" + "\n".join(cleanup_errors),
                    parent=self.root,
                )

        def render_rows(_event: object | None = None) -> None:
            for child in inner.winfo_children():
                child.destroy()
            inner.columnconfigure(0, weight=1)
            visible = 0
            row = 0
            groups = [
                ("TOOL MẶC ĐỊNH", [(tool_id, metadata, False) for tool_id, metadata in TOOL_FILES.items()]),
                ("TOOL THÊM MỚI", [(tool_id, metadata, True) for tool_id, metadata in self.custom_tools.items()]),
            ]
            for group_title, raw_items in groups:
                items = [
                    (tool_id, metadata, custom)
                    for tool_id, metadata, custom in raw_items
                    if item_matches(tool_id, metadata, custom=custom)
                ]
                if not items:
                    continue
                ttk.Label(
                    inner,
                    text=group_title,
                    style="Muted.TLabel",
                    font=(UI_FONT_FAMILY, 9, "bold"),
                ).grid(row=row, column=0, sticky="w", pady=(4 if row else 0, 4))
                row += 1
                for tool_id, metadata, custom in items:
                    row_panel, row_frame = self._outlined_panel(inner, padding=(14, 10, 14, 10), height=104)
                    row_panel.grid(row=row, column=0, sticky="ew", pady=(0, 8))
                    row_frame.columnconfigure(0, weight=1)
                    ttk.Label(
                        row_frame,
                        text=metadata["title"].upper(),
                        style="CardTitle.TLabel",
                        font=(UI_FONT_FAMILY, 11, "bold"),
                    ).grid(row=0, column=0, sticky="w")
                    script_label = Path(metadata["script"]).name
                    type_text = "Thêm mới" if custom else "Mặc định"
                    ttk.Label(
                        row_frame,
                        text=f"{type_text} | {script_label}",
                        style="PanelMuted.TLabel",
                    ).grid(row=1, column=0, sticky="w", pady=(4, 0))
                    status_row = ttk.Frame(row_frame, style="Panel.TFrame")
                    status_row.grid(row=2, column=0, sticky="w", pady=(7, 0))
                    self._status_pill(status_row, self._tool_status_kind(tool_id, custom=custom)).grid(row=0, column=0)
                    self._status_pill(
                        status_row,
                        self._embedded_status_kind(tool_id, custom=custom),
                        palette=EMBEDDED_STATUS_COLORS,
                    ).grid(row=0, column=1, padx=(6, 0))
                    actions = ttk.Frame(row_frame, style="Panel.TFrame")
                    actions.grid(row=0, column=1, rowspan=3, sticky="e")
                    self._flat_button(
                        actions,
                        text="Mở",
                        command=lambda item_id=tool_id, item_custom=custom: self._launch_tool(item_id, custom=item_custom),
                        width=6,
                        primary=True,
                    ).grid(row=0, column=0, padx=(0, 6))
                    self._flat_button(
                        actions,
                        text="Sửa" if custom else "Đổi file",
                        command=lambda item_id=tool_id: open_replace(item_id),
                        width=7,
                    ).grid(row=0, column=1)
                    self._flat_button(
                        actions,
                        text="Trạng thái",
                        command=lambda item_id=tool_id, item_custom=custom: self._show_tool_status(
                            item_id,
                            custom=item_custom,
                            parent=dialog,
                        ),
                        width=8,
                    ).grid(row=1, column=0, padx=(0, 6), pady=(7, 0))
                    if custom:
                        self._flat_button(
                            actions,
                            text="Xóa",
                            command=lambda item_id=tool_id: delete_custom(item_id),
                            width=5,
                        ).grid(row=1, column=1, sticky="ew", pady=(7, 0))
                    visible += 1
                    row += 1
            if visible == 0:
                empty_panel, empty_frame = self._outlined_panel(inner, padding=(14, 14, 14, 14), height=70)
                empty_panel.grid(row=0, column=0, sticky="ew")
                ttk.Label(empty_frame, text="Không có tool phù hợp.", style="PanelMuted.TLabel").pack(anchor="w")
            manager_count_var.set(f"{visible} tool")

        manager_entry.bind("<KeyRelease>", render_rows)
        manager_entry.bind("<<Paste>>", lambda _event: self.root.after(1, render_rows))
        render_rows()

        footer = ttk.Frame(shell, style="App.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        self._flat_button(footer, text="Kiểm tra", command=self._self_check, width=8).grid(row=0, column=1, padx=(0, 8))
        self._flat_button(footer, text="Đóng", command=dialog.destroy, width=8).grid(row=0, column=2)

        dialog.update_idletasks()
        dialog.geometry("640x470")
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 4, 0)
        dialog.geometry(f"+{x}+{y}")

    def _remove_custom_tool(self, tool_id: str) -> tuple[str, list[str]]:
        """Remove a user-added card and its dashboard-managed copies."""

        tools = load_custom_tools()
        metadata = tools.pop(tool_id, None)
        if metadata is None:
            raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_id}")

        save_custom_tools(tools)
        title = metadata.get("title", tool_id)
        cleanup_errors: list[str] = []
        candidate_paths: list[Path] = []
        try:
            candidate_paths.append(custom_tool_external_path(metadata))
        except Exception as error:  # noqa: BLE001 - the card is already removed; report cleanup only.
            cleanup_errors.append(f"Không xác định được file ngoài: {error}")
        try:
            relative_script = Path(normalize_custom_script_path(metadata["script"]))
            candidate_paths.append(embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / relative_script.name)
        except Exception as error:  # noqa: BLE001
            cleanup_errors.append(f"Không xác định được file nhúng: {error}")

        for path in dict.fromkeys(candidate_paths):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                cleanup_errors.append(f"{path}: {error}")
        return title, cleanup_errors

    def _delete_custom_tool_card(self, tool_id: str) -> None:
        """Ask for confirmation and delete one user-added dashboard card."""

        self.custom_tools = load_custom_tools()
        metadata = self.custom_tools.get(tool_id)
        if metadata is None:
            messagebox.showwarning("Không tìm thấy card", "Card tool này không còn tồn tại.", parent=self.root)
            self._build_dashboard_screen()
            return

        title = metadata.get("title", tool_id)
        confirmed = messagebox.askyesno(
            "Xóa card tool",
            (
                f"Xóa card \"{title}\" khỏi dashboard?\n\n"
                "App chỉ xóa bản copy và bản nhúng do dashboard quản lý. "
                "File gốc bạn đã chọn sẽ không bị xóa."
            ),
            parent=self.root,
        )
        if not confirmed:
            return

        try:
            removed_title, cleanup_errors = self._remove_custom_tool(tool_id)
        except Exception as error:  # noqa: BLE001 - user-facing deletion path.
            self._log_error(f"Không xóa được {title}", error)
            return

        self.custom_tools = load_custom_tools()
        self.status_var.set(f"Đã xóa card tool: {removed_title}.")
        self._build_dashboard_screen()
        if cleanup_errors:
            messagebox.showwarning(
                "Đã xóa card",
                "Card đã được xóa, nhưng có file phụ chưa dọn được:\n\n" + "\n".join(cleanup_errors),
                parent=self.root,
            )

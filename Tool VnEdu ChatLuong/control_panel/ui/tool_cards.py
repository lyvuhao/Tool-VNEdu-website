"""Thẻ tool, chạy tool và menu thẻ."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..config import (
    BUTTON_HOVER_COLOR,
    CARD_BORDER_COLOR,
    CUSTOM_TOOLS_DIR_NAME,
    EMBEDDED_STATUS_COLORS,
    PANEL_COLOR,
    RUNNING_STATUS_COLORS,
    TEXT_COLOR,
    TOOL_ACCENTS,
    TOOL_STATUS_COLORS,
    UI_FONT_FAMILY,
)
from ..custom_tools import (
    custom_tool_external_path,
    load_default_tool_overrides,
    normalize_custom_script_path,
    tool_workspace_dir,
)
from ..embedded import external_tool_script_path
from ..log import _get_logger
from ..storage import embedded_tool_dir, save_app_config
from ..tool_configs import sync_tool_configs
from ..tool_registry import TOOL_FILES
from ..validation import parse_debug_port, validate_target_url


class ToolCardsMixin:
    """Thẻ tool, chạy tool và menu thẻ."""

    def _tool_card(
        self,
        parent: ttk.Frame,
        tool_name: str,
        row: int,
        column: int,
        *,
        metadata: dict[str, str] | None = None,
        custom: bool = False,
    ) -> None:
        metadata = metadata if metadata is not None else TOOL_FILES[tool_name]
        accent_color = metadata.get("accent") if custom else TOOL_ACCENTS.get(tool_name, "#2563eb")
        if not accent_color:
            accent_color = "#2563eb"
        advanced = self.advanced_mode_var.get()
        is_running = self._is_tool_running(tool_name, custom=custom)
        card = tk.Frame(
            parent,
            bg=PANEL_COLOR,
            height=108 if advanced else 96,
            highlightthickness=2,
            highlightbackground=CARD_BORDER_COLOR,
            highlightcolor=CARD_BORDER_COLOR,
        )
        card.grid_propagate(False)
        card.pack_propagate(False)
        card.grid(
            row=row,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 6, 6 if column == 0 else 0),
            pady=5,
        )
        tk.Frame(card, bg=accent_color, width=5).pack(side="left", fill="y")
        frame = ttk.Frame(card, style="Panel.TFrame", padding=(16, 10, 14, 10))
        frame.pack(side="left", fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)
        ttk.Label(
            frame,
            text=metadata["title"].upper(),
            style="CardTitle.TLabel",
            font=(UI_FONT_FAMILY, 12, "bold"),
        ).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        description = ttk.Label(frame, text=metadata["description"], wraplength=250, style="PanelMuted.TLabel")
        description.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(5, 0))
        # Xuống dòng theo bề rộng thật của cột chữ (trước đây cố định 320px nên chữ bị nút "Mở" che mất).
        frame.bind(
            "<Configure>",
            lambda event, label=description: label.configure(
                wraplength=max(160, event.width - 150)
            ),
            add="+",
        )
        row_span = 3 if advanced else 2
        if advanced:
            status_badges = ttk.Frame(frame, style="Panel.TFrame")
            status_badges.grid(row=2, column=0, sticky="w", pady=(8, 0))
            badge_column = 0
            if is_running:
                self._status_pill(status_badges, "running", palette=RUNNING_STATUS_COLORS).grid(
                    row=0, column=badge_column, sticky="w"
                )
                badge_column += 1
            self._status_pill(status_badges, self._tool_status_kind(tool_name, custom=custom)).grid(
                row=0, column=badge_column, sticky="w", padx=(6 if badge_column else 0, 0)
            )
            badge_column += 1
            self._status_pill(
                status_badges,
                self._embedded_status_kind(tool_name, custom=custom),
                palette=EMBEDDED_STATUS_COLORS,
            ).grid(
                row=0, column=badge_column, sticky="w", padx=(6, 0)
            )
        action_frame = ttk.Frame(frame, style="Panel.TFrame")
        action_frame.grid(row=0, column=1, rowspan=row_span, sticky="e")
        self._flat_button(
            action_frame,
            text="Đang mở" if is_running else "Mở",
            command=lambda: self._launch_tool(tool_name, custom=custom),
            width=8 if is_running else 7,
            primary=not is_running,
            disabled=is_running,
        ).grid(row=0, column=0, sticky="e")

    def _launch_tool(self, tool_name: str, *, custom: bool = False) -> None:
        logger = _get_logger()
        try:
            if custom:
                metadata = self.custom_tools.get(tool_name)
                if metadata is None:
                    raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_name}")
                title = metadata["title"]
                command_flag = "--custom-tool"
            else:
                title = TOOL_FILES[tool_name]["title"]
                command_flag = "--tool"

            runtime_key = self._tool_runtime_key(tool_name, custom=custom)
            if self._is_tool_running(tool_name, custom=custom):
                self.status_var.set(f"{title} đang mở; không mở thêm phiên thứ hai.")
                self._running_tool_keys_snapshot = self._collect_running_tool_keys()
                self._refresh_tool_cards()
                return

            # Loading indicator after duplicate check (avoid unnecessary UI flush)
            self.status_var.set(f"Đang khởi chạy {title}...")
            self.root.update_idletasks()

            self._refresh_session_caption()
            try:
                target_url = validate_target_url(self.url_var.get())
                debug_port = parse_debug_port(self.port_var.get())
                sync_tool_configs(
                    username=self.username_var.get().strip(),
                    target_url=target_url,
                    debug_port=debug_port,
                )
            except ValueError:
                pass
            command = [self._launcher_executable(), str(self._launcher_script_or_arg()), command_flag, tool_name]
            if getattr(sys, "frozen", False):
                command = [sys.executable, command_flag, tool_name]
            process = subprocess.Popen(
                command,
                cwd=str(tool_workspace_dir()),
                env={**os.environ, "VNEDU_CONTROL_PANEL": "1"},
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            self.processes.append(process)
            self.tool_processes[runtime_key] = process
            self._running_tool_keys_snapshot = self._collect_running_tool_keys()
            self._refresh_tool_cards()
            self._start_tool_process_poll()
        except Exception as error:  # noqa: BLE001
            fallback_title = (
                self.custom_tools.get(tool_name, {}).get("title", tool_name)
                if custom
                else TOOL_FILES[tool_name]["title"]
            )
            self._log_error(f"Lỗi mở {fallback_title}", error)
            return
        self.status_var.set(f"Đã mở {title}.")
        logger.info("Launched tool: %s (custom=%s, pid=%s)", tool_name, custom, process.pid)
        self.bus.emit("tool:started", tool_name=tool_name, custom=custom)

    def _open_tool_card_menu(self, anchor: tk.Widget, tool_name: str, *, custom: bool = False) -> None:
        """Show compact per-card actions without cluttering the card surface."""

        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=PANEL_COLOR,
            fg=TEXT_COLOR,
            activebackground=BUTTON_HOVER_COLOR,
            activeforeground=TEXT_COLOR,
            font=(UI_FONT_FAMILY, 10),
        )
        menu.add_command(label="Xem trạng thái", command=lambda: self._show_tool_status(tool_name, custom=custom))
        menu.add_command(label="Đổi thông tin/file" if custom else "Đổi file", command=lambda: self._open_replace_tool_dialog(tool_name))
        if custom:
            menu.add_separator()
            menu.add_command(label="Xóa card", command=lambda: self._delete_custom_tool_card(tool_name))
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4)
        finally:
            menu.grab_release()

    def _show_tool_status(self, tool_name: str, *, custom: bool = False, parent: tk.Misc | None = None) -> None:
        """Show where a tool is loaded from and whether its fallback exists."""

        try:
            if custom:
                metadata = self.custom_tools.get(tool_name)
                if metadata is None:
                    raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_name}")
                title = metadata["title"]
                external_path = custom_tool_external_path(metadata)
                relative_script = Path(normalize_custom_script_path(metadata["script"]))
                embedded_path = embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / relative_script.name
            else:
                metadata = TOOL_FILES[tool_name]
                title = metadata["title"]
                external_path = external_tool_script_path(tool_name)
                embedded_path = embedded_tool_dir() / metadata["script"]
            run_text, _run_bg, _run_fg = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name, custom=custom)]
            embedded_text, _embedded_bg, _embedded_fg = EMBEDDED_STATUS_COLORS[
                self._embedded_status_kind(tool_name, custom=custom)
            ]
            open_text = "ĐANG MỞ" if self._is_tool_running(tool_name, custom=custom) else "CHƯA MỞ"
            source_info = ""
            if not custom:
                override = load_default_tool_overrides().get(tool_name) or {}
                override_source = override.get("source_name", "").strip()
                override_time = override.get("updated_at", "").strip()
                if override_source:
                    source_info = f"\nFile nguồn gốc: {override_source}"
                    if override_time:
                        source_info += f" (cập nhật: {override_time})"
            message = (
                f"Tool: {title}\n"
                f"Trạng thái mở: {open_text}\n"
                f"Nguồn đang dùng: {run_text}\n"
                f"Bản nhúng: {embedded_text}"
                f"{source_info}\n\n"
                f"File ngoài:\n{external_path}\n\n"
                f"File nhúng dự phòng:\n{embedded_path}"
            )
            messagebox.showinfo("Trạng thái tool", message, parent=parent or self.root)
        except Exception as error:  # noqa: BLE001
            self._log_error("Không đọc được trạng thái tool", error)

    def _toggle_dashboard_mode(self) -> None:
        """Switch between simple and advanced dashboard controls."""

        if self.advanced_mode_var.get():
            self.advanced_mode_var.set(False)
            mode = "basic"
            self.status_var.set("Đã bật chế độ cơ bản.")
        else:
            self.advanced_mode_var.set(True)
            mode = "advanced"
            self.status_var.set("Đã bật chế độ nâng cao.")
        self.app_config["dashboard_mode"] = mode
        try:
            save_app_config(self.app_config)
        except OSError as error:
            self._log_error("Không lưu được chế độ giao diện", error)
            return
        self._build_dashboard_screen()

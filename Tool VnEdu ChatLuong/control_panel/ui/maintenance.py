"""Mở lại VNEDU, thư mục, sao lưu/khôi phục cấu hình, báo cáo."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from ..config import (
    APP_TITLE,
    APP_VERSION,
    BUTTON_HOVER_COLOR,
    CUSTOM_TOOLS_DIR_NAME,
    DEFAULT_DEBUG_PORT,
    DEFAULT_VNEDU_URL,
    EMBEDDED_STATUS_COLORS,
    PANEL_COLOR,
    TEXT_COLOR,
    TOOL_STATUS_COLORS,
    UI_FONT_FAMILY,
)
from ..custom_tools import (
    custom_tool_external_path,
    load_custom_tools,
    resolve_custom_tool_script,
    sanitize_custom_tools,
    save_custom_tools,
    tool_workspace_dir,
)
from ..embedded import external_tool_script_path, resolve_tool_script
from ..login import login_to_vnedu
from ..paths import TOOL_DIR
from ..storage import app_data_dir, embedded_tool_dir
from ..tool_registry import TOOL_FILES


class MaintenanceMixin:
    """Mở lại VNEDU, thư mục, sao lưu/khôi phục cấu hình, báo cáo."""

    def _launcher_executable(self) -> str:
        return sys.executable

    def _launcher_script_or_arg(self) -> Path:
        return TOOL_DIR / "vnedu_control_panel2.py"

    def _reopen_vnedu(self) -> None:
        password = self.password_var.get()
        if not password:
            password = self._styled_ask_string(
                "Mật khẩu VNEDU",
                "Nhập mật khẩu VNEDU để mở lại phiên đăng nhập:",
                show="*",
            )
            if not password:
                self.status_var.set("Hủy mở lại vnEdu: chưa nhập mật khẩu.")
                return
        self.status_var.set("Đang mở lại trang vnEdu...")
        threading.Thread(target=self._reopen_worker, args=(password,), daemon=True, name="VNEDUReopen").start()

    def _reopen_worker(self, password: str) -> None:
        try:
            target_url = str(self.session.get("target_url") or DEFAULT_VNEDU_URL)
            debug_port = int(self.session.get("debug_port") or DEFAULT_DEBUG_PORT)
            username = str(self.session.get("username") or self.username_var.get().strip())
            message = login_to_vnedu(username, password, target_url, debug_port)
        except Exception as error:  # noqa: BLE001
            self.root.after(0, lambda error=error: self._log_error("Lỗi mở lại vnEdu", error))
            return
        self.root.after(0, lambda: self.status_var.set(message or "Đã mở lại trang vnEdu."))

    def _open_tool_folder(self) -> None:
        try:
            os.startfile(str(tool_workspace_dir()))  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001
            self._log_error("Không mở được thư mục tool", error)

    def _open_tool_logs_folder(self) -> None:
        """Open the folder where each tool writes its rotating log file (logs/<tool>.log)."""

        try:
            target = tool_workspace_dir() / "logs"
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001
            self._log_error("Không mở được thư mục log", error)

    def _open_app_data_folder(self) -> None:
        """Open the folder that stores embedded tools, backups, and logs."""

        try:
            target = app_data_dir()
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001
            self._log_error("Không mở được thư mục dữ liệu app", error)

    def _backup_tool_config(self) -> None:
        """Export custom-tool configuration, including embedded payloads, to JSON."""

        self.custom_tools = load_custom_tools()
        default_name = f"vnedu_tool_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Sao lưu cấu hình tool",
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON backup", "*.json"), ("All files", "*.*")],
        )
        if not target:
            return
        backup = {
            "backup_type": "vnedu_control_panel_tools",
            "version": 1,
            "app_version": APP_VERSION,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "custom_tools": {"version": 1, "tools": self.custom_tools},
        }
        try:
            Path(target).write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self._log_error("Không sao lưu được cấu hình tool", error)
            return
        self.status_var.set(f"Đã sao lưu cấu hình tool: {Path(target).name}")
        messagebox.showinfo("Sao lưu cấu hình tool", "Đã sao lưu xong cấu hình tool.", parent=self.root)

    def _restore_tool_config(self) -> None:
        """Restore custom-tool configuration from a dashboard backup JSON file."""

        source = filedialog.askopenfilename(
            parent=self.root,
            title="Khôi phục cấu hình tool",
            filetypes=[("JSON backup", "*.json"), ("All files", "*.*")],
        )
        if not source:
            return
        try:
            raw = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self._log_error("File sao lưu không hợp lệ", error)
            return
        if not isinstance(raw, dict):
            messagebox.showerror("File sao lưu không hợp lệ", "File sao lưu phải là dữ liệu JSON dạng object.", parent=self.root)
            return

        raw_tools: object
        custom_tools_payload = raw.get("custom_tools")
        if isinstance(custom_tools_payload, dict):
            raw_tools = custom_tools_payload.get("tools", {})
        else:
            raw_tools = raw.get("tools", {})
        restored_tools = sanitize_custom_tools(raw_tools)
        if raw_tools and not restored_tools:
            messagebox.showerror(
                "File sao lưu không hợp lệ",
                "Không tìm thấy card tool hợp lệ trong file sao lưu.",
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "Khôi phục cấu hình tool",
            (
                f"Khôi phục {len(restored_tools)} card tool từ file sao lưu?\n\n"
                "Cấu hình tool thêm mới hiện tại sẽ được thay thế."
            ),
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            save_custom_tools(restored_tools)
        except OSError as error:
            self._log_error("Không khôi phục được cấu hình tool", error)
            return
        self.custom_tools = load_custom_tools()
        self.status_var.set(f"Đã khôi phục {len(self.custom_tools)} card tool.")
        self._build_dashboard_screen()
        messagebox.showinfo("Khôi phục cấu hình tool", "Đã khôi phục xong cấu hình tool.", parent=self.root)

    def _tool_report_text(self) -> str:
        """Build a plain-text diagnostic report for built-in and custom tools."""

        lines = [
            f"{APP_TITLE} v{APP_VERSION}",
            f"Báo cáo tool: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Thư mục app: {app_data_dir()}",
            f"Thư mục chạy tool: {tool_workspace_dir()}",
            f"Thư mục nhúng dự phòng: {embedded_tool_dir()}",
            "",
            "TOOL MẶC ĐỊNH",
        ]
        for tool_name, metadata in TOOL_FILES.items():
            external_path = external_tool_script_path(tool_name)
            embedded_path = embedded_tool_dir() / metadata["script"]
            run_text = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name)][0]
            embedded_text = EMBEDDED_STATUS_COLORS[self._embedded_status_kind(tool_name)][0]
            lines.extend(
                [
                    f"- {metadata['title']} [{tool_name}]",
                    f"  File chuẩn: {metadata['script']}",
                    f"  Trạng thái chạy: {run_text}",
                    f"  Bản nhúng: {embedded_text}",
                    f"  File ngoài: {external_path} | tồn tại: {'có' if external_path.exists() else 'không'}",
                    f"  File nhúng runtime: {embedded_path} | tồn tại: {'có' if embedded_path.exists() else 'không'}",
                ]
            )

        self.custom_tools = load_custom_tools()
        lines.extend(["", f"TOOL THÊM MỚI ({len(self.custom_tools)})"])
        if not self.custom_tools:
            lines.append("- Chưa có tool thêm mới.")
        for tool_id, metadata in self.custom_tools.items():
            try:
                external_path = custom_tool_external_path(metadata)
                ext_exists = external_path.exists()
            except Exception as error:  # noqa: BLE001
                external_path = f"<lỗi đường dẫn: {error}>"
                ext_exists = False
            embedded_path = embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / Path(metadata.get("script", "")).name
            run_text = TOOL_STATUS_COLORS[self._tool_status_kind(tool_id, custom=True)][0]
            embedded_text = EMBEDDED_STATUS_COLORS[self._embedded_status_kind(tool_id, custom=True)][0]
            try:
                emb_exists = embedded_path.exists()
            except OSError:
                emb_exists = False
            lines.extend(
                [
                    f"- {metadata.get('title', tool_id)} [{tool_id}]",
                    f"  File: {metadata.get('script', '')}",
                    f"  Trạng thái chạy: {run_text}",
                    f"  Bản nhúng: {embedded_text}",
                    f"  File ngoài: {external_path} | tồn tại: {'có' if ext_exists else 'không'}",
                    f"  File nhúng runtime: {embedded_path} | tồn tại: {'có' if emb_exists else 'không'}",
                ]
            )
        return "\n".join(lines) + "\n"

    def _export_tool_report(self) -> None:
        """Save a readable tool-status report for troubleshooting."""

        default_name = f"vnedu_tool_report_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Xuất báo cáo tool",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text report", "*.txt"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(self._tool_report_text(), encoding="utf-8")
        except OSError as error:
            self._log_error("Không xuất được báo cáo tool", error)
            return
        self.status_var.set(f"Đã xuất báo cáo tool: {Path(target).name}")
        messagebox.showinfo("Xuất báo cáo tool", "Đã xuất xong báo cáo tool.", parent=self.root)

    def _open_tools_menu(self, anchor: tk.Widget) -> None:
        """Show secondary maintenance actions without taking dashboard space."""

        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=PANEL_COLOR,
            fg=TEXT_COLOR,
            activebackground=BUTTON_HOVER_COLOR,
            activeforeground=TEXT_COLOR,
            font=(UI_FONT_FAMILY, 10),
        )
        menu.add_command(label="Quản lý tool", command=self._open_tool_manager_dialog)
        menu.add_command(label="Đổi file nhanh", command=self._open_replace_tool_dialog)
        menu.add_separator()
        menu.add_command(label="Kiểm tra lại tool", command=self._self_check)
        menu.add_command(label="Xuất báo cáo tool", command=self._export_tool_report)
        menu.add_separator()
        menu.add_command(label="Sao lưu cấu hình tool", command=self._backup_tool_config)
        menu.add_command(label="Khôi phục cấu hình tool", command=self._restore_tool_config)
        menu.add_separator()
        menu.add_command(label="Mở thư mục tool", command=self._open_tool_folder)
        menu.add_command(label="Mở thư mục log của tool", command=self._open_tool_logs_folder)
        menu.add_command(label="Mở thư mục dữ liệu app", command=self._open_app_data_folder)
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4)
        finally:
            menu.grab_release()

    def _self_check(self) -> None:
        errors: list[str] = []
        for tool_name in TOOL_FILES:
            try:
                resolve_tool_script(tool_name)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{tool_name}: {error}")
        self.custom_tools = load_custom_tools()
        for tool_id, metadata in self.custom_tools.items():
            try:
                resolve_custom_tool_script(tool_id)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{metadata.get('title', tool_id)}: {error}")
        if errors:
            messagebox.showerror("Kiểm tra tool", "\n".join(errors), parent=self.root)
            return
        custom_count = len(self.custom_tools)
        if custom_count:
            message = f"Đã tìm thấy đủ 4 tool mặc định và {custom_count} tool thêm mới."
        else:
            message = "Đã tìm thấy đủ 4 tool mặc định."
        messagebox.showinfo("Kiểm tra tool", message, parent=self.root)

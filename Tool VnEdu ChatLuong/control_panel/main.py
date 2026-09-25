"""Điểm khởi chạy control panel (CLI + dashboard)."""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
import traceback
from tkinter import messagebox

from .config import APP_TITLE
from .custom_tools import tool_workspace_dir
from .process import (
    focus_existing_control_panel_window,
    run_custom_tool_process,
    run_tool_process,
    SingleInstanceGuard,
)
from .selftest import run_self_test
from .storage import app_data_dir
from .tool_registry import TOOL_FILES
from .ui.app import ControlPanelApp
from .ui.style import configure_style


def launch_dashboard(skip_login: bool = False) -> int:
    """Launch the dashboard immediately; login is handled inside the window."""

    single_instance = SingleInstanceGuard()
    if not single_instance.acquire():
        focus_existing_control_panel_window()
        return 0
    tool_workspace_dir()
    try:
        root = tk.Tk()
        configure_style(root)
        ControlPanelApp(root, skip_login=skip_login)
        root.mainloop()
        return 0
    finally:
        single_instance.release()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build command-line parser."""

    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--tool", choices=sorted(TOOL_FILES), help="Chạy một tool legacy trong tiến trình con.")
    parser.add_argument("--custom-tool", help="Chạy một tool tùy chỉnh trong tiến trình con.")
    parser.add_argument("--self-test", action="store_true", help="Kiểm tra nhanh dashboard và file tool.")
    parser.add_argument("--skip-login", action="store_true", help="Mở dashboard để xem giao diện, không đăng nhập trước.")
    return parser


def configure_cli_streams() -> None:
    """Make command-line help/errors safe with Vietnamese text on Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main() -> int:
    """Program entry point."""

    configure_cli_streams()
    parser = build_arg_parser()
    args, extra_args = parser.parse_known_args()
    try:
        if args.self_test:
            return run_self_test()
        if args.custom_tool:
            return run_custom_tool_process(args.custom_tool, extra_args)
        if args.tool:
            return run_tool_process(args.tool, extra_args)
        return launch_dashboard(skip_login=args.skip_login)
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001 - crash report for desktop app startup.
        crash_path = app_data_dir() / "vnedu_control_panel_crash.txt"
        crash_path.parent.mkdir(parents=True, exist_ok=True)
        crash_path.write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{traceback.format_exc()}",
            encoding="utf-8",
        )
        try:
            messagebox.showerror("Lỗi VNEDU Control Panel", f"{error}\n\nCrash report: {crash_path}")
        except Exception:
            print(f"Lỗi: {error}", file=sys.stderr)
            print(f"Crash report: {crash_path}", file=sys.stderr)
        return 1

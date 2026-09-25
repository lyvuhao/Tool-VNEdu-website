"""Điểm khởi chạy của tool Ghi nhận xét."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from vnedu_common.logging_setup import install_tk_exception_logging, setup_tool_logging

from .paths import TOOL_DIR
from .ui.app import AutoNhanXetV2App


def main() -> None:
    """Program entry point."""
    setup_tool_logging("nhanxet", TOOL_DIR)
    root = tk.Tk()
    install_tk_exception_logging(root)
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    AutoNhanXetV2App(root)
    root.mainloop()

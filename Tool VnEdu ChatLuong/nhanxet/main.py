"""Điểm khởi chạy của tool Ghi nhận xét."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .ui.app import AutoNhanXetV2App


def main() -> None:
    """Program entry point."""
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    AutoNhanXetV2App(root)
    root.mainloop()

"""Điểm khởi chạy của tool Nhập điểm."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from . import config as app_config
from .models import LogTag
from .selftest import _run_self_tests
from .ui.app import VnEduStandaloneApp
from .ui.window import apply_app_styles


def main() -> None:
    """Entry point chính cho ứng dụng nhập điểm VNEDU.

    IMP-C4: Hỗ trợ CLI arguments (--debug, --config-file).
    IMP-C5: Bọc mainloop trong try-except để ghi crash report.
    """
    import argparse
    parser = argparse.ArgumentParser(description="VNEDU Score Entry Tool")
    parser.add_argument("--debug", action="store_true", help="Bật chế độ debug (hiện log chi tiết)")
    parser.add_argument("--config-file", type=str, default=None, help="Đường dẫn file config thay thế")
    parser.add_argument("--self-test", action="store_true", help="Chạy kiểm tra nhanh logic lõi rồi thoát")
    args = parser.parse_args()

    if args.self_test:
        _run_self_tests()
        return

    # IMP-C4: Ghi đè CONFIG_FILE nếu user chỉ định
    if args.config_file:

        app_config.CONFIG_FILE = Path(args.config_file)

    root = tk.Tk()
    style = ttk.Style(root)
    apply_app_styles(style)
    app = VnEduStandaloneApp(root)
    # IMP-C4: Bật debug mode nếu có flag
    if args.debug:
        app._log("🐛 Debug mode enabled via --debug flag.", tag=LogTag.INFO)
    # IMP-C5: Bọc mainloop trong try-except để ghi crash report
    try:
        root.mainloop()
    except Exception:
        crash_report_path = Path.home() / "Desktop" / "nhapdiem_crash_report.txt"
        try:
            import traceback
            with crash_report_path.open("w", encoding="utf-8") as crash_f:
                crash_f.write(f"CRASH REPORT — {datetime.now().isoformat()}\n")
                crash_f.write("=" * 60 + "\n")
                crash_f.write(traceback.format_exc())
            print(f"[FATAL] Crash report saved to: {crash_report_path}")
        except Exception:
            pass
        raise

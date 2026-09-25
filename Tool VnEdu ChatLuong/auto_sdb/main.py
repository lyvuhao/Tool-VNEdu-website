"""Điểm khởi chạy của tool Sổ đầu bài."""

from vnedu_common.logging_setup import install_tk_exception_logging, setup_tool_logging

from .app.app import AutoDaNangApp
from .paths import TOOL_DIR


def main():
    setup_tool_logging("auto_sdb", TOOL_DIR)
    app = AutoDaNangApp()
    install_tk_exception_logging(app.root)
    app.run()

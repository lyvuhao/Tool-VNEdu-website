"""Điểm khởi chạy của tool Sổ đầu bài."""

from .app.app import AutoDaNangApp


def main():
    app = AutoDaNangApp()
    app.run()

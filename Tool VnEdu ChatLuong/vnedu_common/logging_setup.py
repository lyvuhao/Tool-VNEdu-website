"""Ghi log ra file xoay vòng và bắt lỗi chưa xử lý (thread, callback Tkinter).

Mỗi tool gọi `setup_tool_logging("<tên tool>", TOOL_DIR)` một lần khi khởi động:

- Log được ghi vào `<thư mục tool>/logs/<tên tool>.log` (tối đa 2 MB x 5 file, UTF-8).
- Dòng log trên giao diện được ghi thêm vào file qua `log_ui_message(...)`.
- Lỗi chưa bắt (luồng chính, thread nền, callback Tkinter) được ghi kèm traceback đầy đủ.

Đặt biến môi trường `VNEDU_DISABLE_FILE_LOG=1` để tắt ghi file.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import tempfile
import threading
from pathlib import Path

LOG_DIR_NAME = "logs"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
DISABLE_ENV = "VNEDU_DISABLE_FILE_LOG"

_UI_LEVELS = {
    "log_warning": logging.WARNING,
    "log_error": logging.ERROR,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "err": logging.ERROR,
    "error": logging.ERROR,
    "danger": logging.ERROR,
}

_state_lock = threading.Lock()
_log_path: Path | None = None
_hooks_installed = False


def _open_log_handler(tool_name: str, tool_dir: Path) -> tuple[logging.Handler, Path] | None:
    """Mở file log trong thư mục tool; không ghi được thì dùng thư mục tạm."""

    for base in (Path(tool_dir) / LOG_DIR_NAME, Path(tempfile.gettempdir()) / "vnedu_logs"):
        try:
            base.mkdir(parents=True, exist_ok=True)
            path = base / f"{tool_name}.log"
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            # Mở thử để phát hiện sớm thư mục không ghi được.
            with open(path, "a", encoding="utf-8"):
                pass
            return handler, path
        except OSError:
            continue
    return None


def setup_tool_logging(tool_name: str, tool_dir: Path) -> Path | None:
    """Bật ghi log ra file cho một tool. An toàn khi gọi nhiều lần. Trả về đường dẫn file log."""

    global _log_path
    with _state_lock:
        if _log_path is not None:
            return _log_path
        if os.environ.get(DISABLE_ENV, "").strip() in ("1", "true", "yes"):
            return None
        opened = _open_log_handler(tool_name, tool_dir)
        if opened is None:
            return None
        handler, path = opened
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.setLevel(logging.INFO)

        root = logging.getLogger()
        had_handlers = bool(root.handlers)
        root.addHandler(handler)
        if not had_handlers and sys.stderr is not None:
            # Giữ hành vi cũ: cảnh báo/lỗi vẫn in ra console như logging mặc định (lastResort).
            console = logging.StreamHandler(sys.stderr)
            console.setLevel(logging.WARNING)
            console.setFormatter(logging.Formatter("%(message)s"))
            # Lỗi callback/thread vẫn được Tkinter/threading tự in ra -> không in lặp lần hai.
            console.addFilter(lambda record: record.name not in ("crash", "tk"))
            root.addHandler(console)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        # Thư viện HTTP ghi INFO rất nhiều -> chỉ giữ cảnh báo trở lên.
        for noisy in ("urllib3", "asyncio", "PIL"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        _log_path = path
        _install_exception_hooks()
    logging.getLogger(tool_name).info("===== Khởi động %s (Python %s) — log: %s =====", tool_name, sys.version.split()[0], path)
    return path


def current_log_path() -> Path | None:
    """Đường dẫn file log đang dùng (None nếu chưa bật)."""

    return _log_path


def _install_exception_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    crash_logger = logging.getLogger("crash")

    previous_excepthook = sys.excepthook

    def excepthook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            crash_logger.critical("Lỗi chưa xử lý", exc_info=(exc_type, exc_value, exc_tb))
        previous_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

    previous_thread_hook = threading.excepthook

    def thread_excepthook(args):
        if args.exc_type is not SystemExit:
            thread_name = args.thread.name if args.thread is not None else "?"
            crash_logger.critical(
                "Lỗi chưa xử lý trong thread %s",
                thread_name,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        previous_thread_hook(args)

    threading.excepthook = thread_excepthook


def install_tk_exception_logging(root) -> None:
    """Ghi vào log mọi lỗi xảy ra trong callback Tkinter (nút bấm, after(), sự kiện...).

    Mặc định Tkinter chỉ in lỗi callback ra stderr — khi chạy bằng pythonw/.exe sẽ mất hẳn.
    """

    previous = root.report_callback_exception
    tk_logger = logging.getLogger("tk")

    def report_callback_exception(exc_type, exc_value, exc_tb):
        tk_logger.error("Lỗi trong callback giao diện", exc_info=(exc_type, exc_value, exc_tb))
        previous(exc_type, exc_value, exc_tb)

    root.report_callback_exception = report_callback_exception


def log_ui_message(logger_name: str, message: object, level: str = "info") -> None:
    """Ghi thêm một dòng log hiển thị trên giao diện vào file log (không bao giờ ném lỗi)."""

    if _log_path is None:
        return  # chưa bật ghi file (vd. khi import để test) -> giữ nguyên hành vi cũ
    try:
        logging.getLogger(logger_name).log(_UI_LEVELS.get(str(getattr(level, "value", level)).lower(), logging.INFO), "%s", message)
    except Exception:  # noqa: BLE001 - ghi log không được làm hỏng giao diện
        pass

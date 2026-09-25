"""Logger ghi file cho control panel."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .storage import app_data_dir


_logger_instance: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Lazy-init centralized logger with rotation."""

    global _logger_instance  # noqa: PLW0603
    if _logger_instance is not None:
        return _logger_instance

    try:
        log_dir = app_data_dir() / "logs"
    except NameError:
        log_dir = Path.home() / ".vnedu" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            str(log_dir / "control_panel.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    _logger_instance = logging.getLogger("vnedu.panel")
    _logger_instance.setLevel(logging.DEBUG)
    _logger_instance.addHandler(handler)
    return _logger_instance

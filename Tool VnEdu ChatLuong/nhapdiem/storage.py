"""Đọc/ghi file JSON an toàn (atomic write, backup file lỗi)."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path


def _timestamp_for_backup() -> str:
    """Builds a filesystem-safe timestamp for local recovery files."""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _json_temp_file(path: Path) -> Path:
    """Returns a unique temp path next to the final JSON file."""
    return path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.{int(time.time() * 1000)}.tmp"
    )


def _backup_unreadable_json_file(path: Path) -> Path | None:
    """Renames an unreadable JSON file so the app can regenerate a clean one."""
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.stem}.corrupt-{_timestamp_for_backup()}{path.suffix}")
    try:
        path.replace(backup_path)
    except OSError:
        return None
    return backup_path


def _load_json_object_file(path: Path) -> tuple[dict[str, object], Path | None, Exception | None]:
    """Loads a JSON object and backs up corrupt content instead of reusing it."""
    if not path.exists():
        return {}, None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as error:  # noqa: BLE001 - caller logs a user-facing message
        return {}, _backup_unreadable_json_file(path), error
    if not isinstance(payload, dict):
        error = ValueError(f"{path.name} phải chứa JSON object ở cấp gốc.")
        return {}, _backup_unreadable_json_file(path), error
    return payload, None, None


def _write_json_atomic_file(path: Path, payload: object) -> None:
    """Writes JSON through a flushed temp file and same-directory replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = _json_temp_file(path)
    try:
        with tmp_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_file.replace(path)
    finally:
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass

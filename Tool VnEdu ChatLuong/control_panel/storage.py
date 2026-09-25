"""Thư mục dữ liệu ứng dụng và file cấu hình của control panel."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import (
    APP_CONFIG_NAME,
    CUSTOM_TOOLS_CONFIG_NAME,
    DEFAULT_TOOL_OVERRIDES_CONFIG_NAME,
    VALID_DASHBOARD_MODES,
)
from .paths import TOOL_DIR


def app_data_dir() -> Path:
    """Return the writable app directory used for bundled tool scripts/configs."""

    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "VNEDU-Control-Panel"


def bundled_base_dir() -> Path:
    """Return the source directory for bundled data files."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return TOOL_DIR


def embedded_tool_dir() -> Path:
    """Return the writable folder used when external tool files are missing."""

    target_dir = app_data_dir() / "embedded_tools"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def custom_tools_config_path() -> Path:
    """Return the JSON file that stores user-added tool metadata and payloads."""

    return app_data_dir() / CUSTOM_TOOLS_CONFIG_NAME


def default_tool_overrides_path() -> Path:
    """Return the JSON file that stores replaceable fallback payloads for bundled tools."""

    return app_data_dir() / DEFAULT_TOOL_OVERRIDES_CONFIG_NAME


def app_config_path() -> Path:
    """Return the JSON file that stores small dashboard preferences."""

    return app_data_dir() / APP_CONFIG_NAME


def load_app_config() -> dict[str, str]:
    """Load dashboard preferences with conservative defaults."""

    path = app_config_path()
    if not path.exists():
        return {"dashboard_mode": "basic"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"dashboard_mode": "basic"}
    if not isinstance(raw, dict):
        return {"dashboard_mode": "basic"}
    mode = str(raw.get("dashboard_mode") or "basic").strip().lower()
    if mode not in VALID_DASHBOARD_MODES:
        mode = "basic"
    return {"dashboard_mode": mode}


def save_app_config(config: dict[str, str]) -> None:
    """Persist dashboard preferences without touching tool configuration."""

    mode = str(config.get("dashboard_mode") or "basic").strip().lower()
    if mode not in VALID_DASHBOARD_MODES:
        mode = "basic"
    path = app_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"version": 1, "dashboard_mode": mode}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)

"""Đồng bộ file cấu hình dùng chung cho các tool (không lưu mật khẩu)."""

from __future__ import annotations

import json
from pathlib import Path

from .custom_tools import tool_workspace_dir
from .storage import embedded_tool_dir


def write_json_if_changed(path: Path, payload: dict[str, object]) -> None:
    """Write JSON only when content differs to avoid needless config churn."""

    existing: dict[str, object] = {}
    if path.exists():
        try:
            existing_raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing_raw, dict):
                existing = existing_raw
        except (OSError, json.JSONDecodeError):
            existing = {}

    merged = {**existing, **payload}
    if merged == existing:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def sync_tool_configs(username: str, target_url: str, debug_port: int) -> None:
    """Seed shared tool config files without persisting the password."""

    shared_payload = {
        "debug_port": str(debug_port),
        "target_url": target_url,
        "cdp_url": target_url,
        "username": username,
        "show_password": False,
    }
    for workspace in (tool_workspace_dir(), embedded_tool_dir()):
        write_json_if_changed(workspace / "vnedu_standalone_config.json", shared_payload)
        write_json_if_changed(
            workspace / "nhanxet_v2_config.json",
            {
                "debug_port": str(debug_port),
                "target_url": target_url,
                "username": username,
                "show_password": False,
            },
        )
        write_json_if_changed(
            workspace / "auto_danang_config.json",
            {
                "vnedu_username": username,
                "vnedu_password": "",
                "debug_port": str(debug_port),
            },
        )

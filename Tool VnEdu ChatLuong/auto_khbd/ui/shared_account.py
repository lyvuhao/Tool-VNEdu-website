"""Tài khoản VnEdu dùng chung với dashboard (VNEDU Control Panel).

Dashboard ghi `khdh_config.json` cạnh launcher trước khi mở KHDH: tài khoản, cổng Chrome debug và
URL VnEdu — **không bao giờ có mật khẩu**. KHDH đọc file này để điền sẵn tài khoản; nếu Chrome đã
đăng nhập (từ dashboard) thì bấm "Đăng nhập VnEdu" không cần mật khẩu. Khi đăng nhập thành công,
KHDH cũng ghi lại tài khoản/cổng/URL để lần sau dùng lại.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..paths import TOOL_DIR

ACCOUNT_CONFIG_NAME = "khdh_config.json"


def account_config_path() -> Path:
    return TOOL_DIR / ACCOUNT_CONFIG_NAME


def load_account_config() -> dict[str, object]:
    """Đọc tài khoản/cổng/URL đã lưu. File thiếu hoặc lỗi -> {} (không bao giờ ném lỗi)."""

    try:
        data = json.loads(account_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, object] = {}
    username = str(data.get("username", "")).strip()
    if username:
        result["username"] = username
    try:
        port = int(str(data.get("debug_port", "")).strip())
        if 1 <= port <= 65535:
            result["debug_port"] = port
    except ValueError:
        pass
    url = str(data.get("target_url", "")).strip()
    if url.lower().startswith(("http://", "https://")):
        result["target_url"] = url
    return result


def save_account_config(username: str, debug_port: int, target_url: str) -> None:
    """Ghi tài khoản/cổng/URL (không có mật khẩu). Lỗi ghi file bị bỏ qua."""

    path = account_config_path()
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, ValueError):
        existing = {}
    payload = {**existing, "username": username.strip(), "debug_port": str(debug_port), "target_url": target_url}
    payload.pop("password", None)
    if payload == existing:
        return
    try:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        pass

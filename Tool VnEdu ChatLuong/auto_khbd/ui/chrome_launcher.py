"""Tự mở Chrome với cổng debug (CDP)."""

from __future__ import annotations

import sys
from pathlib import Path


# =====================================================================
# Chrome auto-launch helpers
# =====================================================================

# URL của VnEdu — mở khi auto-launch Chrome (user sẽ tự đăng nhập tại đây)
VNEDU_LOGIN_URL = "https://vnedu.vn/"


def find_chrome_executable() -> str | None:
    """Tìm chrome.exe trên Windows (registry-free, chỉ check known paths).

    Returns:
        Absolute path tới chrome.exe, hoặc None nếu không tìm thấy.
    """
    import os
    candidates = [
        # Windows standard locations
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        # Edge fallback (cùng CDP protocol)
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def launch_chrome_with_debug(port: int, url: str = VNEDU_LOGIN_URL,
                             chrome_path: str | None = None) -> bool:
    """Spawn Chrome ở chế độ debug + mở URL VnEdu.

    Dùng user data directory riêng (kbhd_pro_chrome) để tránh conflict
    với Chrome instance đã chạy của user. Returns True nếu spawn thành công.
    """
    import os
    import subprocess
    if chrome_path is None:
        chrome_path = find_chrome_executable()
    if not chrome_path:
        return False
    # User data dir riêng — tránh "DevToolsActivePort doesn't exist" khi
    # Chrome đã chạy với profile mặc định.
    user_data_dir = os.path.expandvars(
        r"%LocalAppData%\KHBD_Pro\ChromeDebugProfile"
    )
    try:
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    try:
        # CREATE_NEW_PROCESS_GROUP để Chrome không bị kill khi tool đóng.
        # DETACHED_PROCESS để tách hoàn toàn khỏi parent stdin/stdout.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x00000008 | 0x00000200  # DETACHED + NEW_PROCESS_GROUP
        subprocess.Popen(
            args,
            creationflags=creationflags,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def is_cdp_port_open(port: int, timeout: float = 0.5) -> bool:
    """Probe CDP HTTP endpoint xem Chrome đã sẵn sàng chưa."""
    import socket
    try:
        with socket.create_connection(("localhost", port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False

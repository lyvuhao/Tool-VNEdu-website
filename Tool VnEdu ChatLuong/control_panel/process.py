"""Chạy tool trong tiến trình con và khoá chống mở trùng (mutex Windows)."""

from __future__ import annotations

import os
import re
import runpy
import sys
from pathlib import Path

from .config import (
    ALLOW_MULTIPLE_ENV,
    APP_TITLE,
    APP_VERSION,
    SINGLE_INSTANCE_MUTEX_NAME,
    TOOL_PROCESS_MUTEX_PREFIX,
)
from .custom_tools import load_custom_tools, resolve_custom_tool_script
from .embedded import resolve_tool_script
from .tool_registry import TOOL_FILES


def tool_runtime_key(tool_name: str, *, custom: bool = False) -> str:
    """Return the stable runtime key used to prevent duplicate tool sessions."""

    scope = "custom" if custom else "default"
    return f"{scope}:{tool_name}"


def tool_process_mutex_name(runtime_key: str) -> str:
    """Return a Windows-safe mutex name for one running tool."""

    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", runtime_key).strip("_")
    return f"{TOOL_PROCESS_MUTEX_PREFIX}{safe_key or 'tool'}"


_kernel32_cache: tuple[object, object] | None = None


def _setup_kernel32_mutex():
    """Setup kernel32 WinDLL once, cache for reuse across all mutex calls."""

    global _kernel32_cache  # noqa: PLW0603
    if _kernel32_cache is not None:
        return _kernel32_cache

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32_cache = (kernel32, ctypes)
    return _kernel32_cache


class ToolProcessGuard:
    """Hold a named Windows mutex while one dashboard tool is running."""

    def __init__(self, runtime_key: str) -> None:
        self.runtime_key = runtime_key
        self._handle: int | None = None
        self._kernel32: object | None = None

    def acquire(self) -> bool:
        """Return True when this process owns the per-tool runtime lock."""

        if sys.platform != "win32":
            return True

        kernel32, ctypes = _setup_kernel32_mutex()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, tool_process_mutex_name(self.runtime_key))
        if not handle:
            raise OSError(ctypes.get_last_error(), "Không tạo được khóa tool.")
        already_running = ctypes.get_last_error() == 183
        if already_running:
            kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = handle
        return True

    def release(self) -> None:
        """Release the per-tool runtime lock."""

        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        finally:
            self._handle = None
            self._kernel32 = None


def is_tool_process_mutex_active(runtime_key: str) -> bool:
    """Return whether another wrapped process currently owns one tool lock."""

    if sys.platform != "win32":
        return False

    kernel32, _ctypes = _setup_kernel32_mutex()
    synchronize = 0x00100000
    handle = kernel32.OpenMutexW(synchronize, False, tool_process_mutex_name(runtime_key))
    if not handle:
        return False
    try:
        return True
    finally:
        kernel32.CloseHandle(handle)


def run_tool_process(tool_name: str, extra_args: list[str] | None = None) -> int:
    """Run a bundled legacy tool as if it was started directly."""

    guard = ToolProcessGuard(tool_runtime_key(tool_name, custom=False))
    if not guard.acquire():
        print(f"{TOOL_FILES[tool_name]['title']} đang mở. Không mở thêm phiên thứ hai.")
        return 0

    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    try:
        script_path = resolve_tool_script(tool_name)
        os.chdir(script_path.parent)
        sys.argv = [str(script_path), *(extra_args or [])]
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        guard.release()
    return 0


def run_custom_tool_process(tool_id: str, extra_args: list[str] | None = None) -> int:
    """Run a user-added tool as if it was started directly."""

    guard = ToolProcessGuard(tool_runtime_key(tool_id, custom=True))
    if not guard.acquire():
        title = load_custom_tools().get(tool_id, {}).get("title", tool_id)
        print(f"{title} đang mở. Không mở thêm phiên thứ hai.")
        return 0

    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    try:
        script_path = resolve_custom_tool_script(tool_id)
        os.chdir(script_path.parent)
        sys.argv = [str(script_path), *(extra_args or [])]
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        guard.release()
    return 0


class SingleInstanceGuard:
    """Hold a process-wide lock so only one login/dashboard window can run."""

    def __init__(self) -> None:
        self._handle: int | None = None
        self._kernel32: object | None = None

    def acquire(self) -> bool:
        """Return True when this process owns the dashboard lock."""

        if os.environ.get(ALLOW_MULTIPLE_ENV) == "1":
            return True
        if sys.platform != "win32":
            return True

        kernel32, ctypes = _setup_kernel32_mutex()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Không tạo được khóa phiên VNEDU Control Panel.")
        already_running = ctypes.get_last_error() == 183
        if already_running:
            kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = handle
        return True

    def release(self) -> None:
        """Release the dashboard lock when the GUI exits."""

        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        finally:
            self._handle = None
            self._kernel32 = None


def focus_existing_control_panel_window() -> bool:
    """Bring the already-open control panel window to the foreground on Windows."""

    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL

        hwnd = user32.FindWindowW(None, f"{APP_TITLE} v{APP_VERSION}")
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001 - duplicate launch must exit even if focusing fails.
        return False

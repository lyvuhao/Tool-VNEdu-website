"""Theo dõi tiến trình tool đang chạy."""

from __future__ import annotations

import tkinter as tk

from ..config import TOOL_PROCESS_POLL_MS
from ..custom_tools import custom_tool_external_path
from ..embedded import external_tool_script_path
from ..process import is_tool_process_mutex_active, tool_runtime_key
from ..tool_registry import TOOL_FILES


class ToolProcessesMixin:
    """Theo dõi tiến trình tool đang chạy."""

    def _tool_runtime_key(self, tool_name: str, *, custom: bool = False) -> str:
        """Return the runtime key used by dashboard cards and child locks."""

        return tool_runtime_key(tool_name, custom=custom)

    def _cleanup_finished_tool_processes(self) -> bool:
        """Drop finished child processes from the in-memory dashboard registry."""

        changed = False
        for runtime_key, process in list(self.tool_processes.items()):
            if process.poll() is None:
                continue
            self.tool_processes.pop(runtime_key, None)
            changed = True
        self.processes = [process for process in self.processes if process.poll() is None]
        return changed

    def _is_tool_running(self, tool_name: str, *, custom: bool = False) -> bool:
        """Return True when this tool is already open through the dashboard wrapper."""

        runtime_key = self._tool_runtime_key(tool_name, custom=custom)
        process = self.tool_processes.get(runtime_key)
        if process is not None:
            if process.poll() is None:
                return True
            self.tool_processes.pop(runtime_key, None)
        return is_tool_process_mutex_active(runtime_key)

    def _collect_running_tool_keys(self) -> set[str]:
        """Return the set of dashboard tools currently known as running."""

        self._cleanup_finished_tool_processes()
        running_keys: set[str] = set()
        for tool_name in TOOL_FILES:
            runtime_key = self._tool_runtime_key(tool_name, custom=False)
            if self._is_tool_running(tool_name, custom=False):
                running_keys.add(runtime_key)
        for tool_id in self.custom_tools:
            runtime_key = self._tool_runtime_key(tool_id, custom=True)
            if self._is_tool_running(tool_id, custom=True):
                running_keys.add(runtime_key)
        return running_keys

    def _refresh_tool_cards(self) -> None:
        """Refresh only the card list after a tool starts or exits."""

        frame = self._dashboard_cards_frame
        if frame is None:
            return
        try:
            if frame.winfo_exists():
                self._render_dashboard_cards(frame)
        except tk.TclError:
            self._dashboard_cards_frame = None

    def _start_tool_process_poll(self) -> None:
        """Start a lightweight poll that keeps card running badges accurate."""

        if self._tool_process_poll_after_id is not None:
            return
        self._tool_process_poll_after_id = self._safe_after(TOOL_PROCESS_POLL_MS, self._poll_tool_processes)

    def _poll_tool_processes(self) -> None:
        """Refresh running indicators when a child tool exits or file changes."""

        self._tool_process_poll_after_id = None
        running_keys = self._collect_running_tool_keys()
        running_changed = running_keys != self._running_tool_keys_snapshot
        file_changed = self._detect_tool_file_changes()
        if running_changed or file_changed:
            self._running_tool_keys_snapshot = running_keys
            self._refresh_tool_cards()
        self._tool_process_poll_after_id = self._safe_after(TOOL_PROCESS_POLL_MS, self._poll_tool_processes)

    def _detect_tool_file_changes(self) -> bool:
        """Cheap stat()-based check for external tool file modifications."""

        changed = False
        for tool_name in TOOL_FILES:
            try:
                path = external_tool_script_path(tool_name)
                mtime = path.stat().st_mtime_ns if path.exists() else 0
            except OSError:
                mtime = 0
            key = f"default:{tool_name}"
            if self._file_mtimes.get(key) != mtime:
                self._file_mtimes[key] = mtime
                changed = True
        for tool_id, metadata in self.custom_tools.items():
            try:
                path = custom_tool_external_path(metadata)
                mtime = path.stat().st_mtime_ns if path.exists() else 0
            except (OSError, ValueError):
                mtime = 0
            key = f"custom:{tool_id}"
            if self._file_mtimes.get(key) != mtime:
                self._file_mtimes[key] = mtime
                changed = True
        return changed

"""Kiểm tra sức khoẻ tool và CDP."""

from __future__ import annotations

import copy
import socket
import threading
import time
import tkinter as tk
from urllib.request import urlopen

from ..config import (
    DEFAULT_DEBUG_PORT,
    HEALTH_CHECK_CACHE_SECONDS,
    HEALTH_CHECK_DELAY_MS,
    TOOL_STATUS_COLORS,
    UI_FONT_FAMILY,
)
from ..custom_tools import (
    custom_tool_external_path,
    decode_custom_tool_source,
    default_tool_payload,
)
from ..embedded import (
    compile_tool_packages,
    decode_embedded_tool_source,
    external_tool_script_path,
    missing_tool_packages,
)
from ..embedded_payloads import EMBEDDED_TOOL_PAYLOADS
from ..tool_registry import TOOL_FILES
from ..validation import parse_debug_port


class HealthMixin:
    """Kiểm tra sức khoẻ tool và CDP."""

    def _tool_status_kind(self, tool_name: str, *, custom: bool = False) -> str:
        """Return whether a tool is using the external file, embedded fallback, or is missing."""

        try:
            if custom:
                metadata = self.custom_tools.get(tool_name)
                if metadata is None:
                    return "missing"
                if custom_tool_external_path(metadata).exists():
                    return "external"
                return "embedded" if metadata.get("payload") else "missing"
            if external_tool_script_path(tool_name).exists() and not missing_tool_packages(tool_name):
                return "external"
        except Exception:  # noqa: BLE001 - status display must never break the dashboard.
            return "missing"
        return "embedded" if tool_name in EMBEDDED_TOOL_PAYLOADS else "missing"

    def _embedded_status_kind(self, tool_name: str, *, custom: bool = False) -> str:
        """Return whether the embedded fallback payload exists for this tool."""

        if custom:
            metadata = self.custom_tools.get(tool_name)
            return "ok" if metadata and metadata.get("payload") else "missing"
        return "ok" if default_tool_payload(tool_name) else "missing"

    def _check_one_tool_health(
        self,
        tool_name: str,
        metadata: dict[str, str],
        *,
        custom: bool = False,
        deep: bool = False,
    ) -> tuple[bool, list[str]]:
        """Validate external and embedded source for one tool without showing dialogs."""

        errors: list[str] = []
        title = metadata.get("title", tool_name)
        external_ok = False
        try:
            external_path = custom_tool_external_path(metadata) if custom else external_tool_script_path(tool_name)
            if external_path.exists():
                if deep:
                    compile(external_path.read_bytes(), str(external_path), "exec")
                if not custom:
                    missing = missing_tool_packages(tool_name)
                    if missing:
                        raise FileNotFoundError(f"thiếu thư mục package {', '.join(missing)}")
                    if deep:
                        compile_tool_packages(tool_name)
                external_ok = True
        except Exception as error:  # noqa: BLE001 - background health check must keep going.
            errors.append(f"{title}: file ngoài lỗi ({error})")

        embedded_ok = False
        try:
            if custom:
                payload = metadata.get("payload", "").strip()
                if deep:
                    embedded_source = decode_custom_tool_source(tool_name, metadata)
                    compile(embedded_source, f"<embedded {title}>", "exec")
                    embedded_ok = True
                else:
                    embedded_ok = bool(payload)
            else:
                payload = EMBEDDED_TOOL_PAYLOADS.get(tool_name, "").strip()
                if deep:
                    embedded_source = decode_embedded_tool_source(tool_name)
                    compile(embedded_source, f"<embedded {title}>", "exec")
                    embedded_ok = True
                else:
                    embedded_ok = bool(payload)
        except Exception as error:  # noqa: BLE001 - corrupted fallback should be visible in footer status.
            errors.append(f"{title}: bản nhúng lỗi ({error})")
        if not external_ok and not embedded_ok:
            errors.append(f"{title}: không có nguồn chạy hợp lệ")
        return external_ok or embedded_ok, errors

    def _check_cdp_health(self, debug_port: int) -> tuple[bool, str]:
        """Return whether Chrome DevTools Protocol is reachable on the configured port."""

        # Fast socket pre-check (50ms) to avoid slow urlopen timeout when Chrome is not running.
        try:
            sock = socket.create_connection(("127.0.0.1", debug_port), timeout=0.05)
            sock.close()
        except (OSError, ConnectionRefusedError):
            return False, "Chrome chưa mở CDP."
        try:
            with urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=0.5) as response:
                if response.status != 200:
                    return False, f"CDP trả mã {response.status}"
                response.read(256)
        except Exception as error:  # noqa: BLE001 - health status is informational, not fatal.
            return False, str(error).strip() or type(error).__name__
        return True, "OK"

    def _collect_dashboard_health(
        self,
        custom_tools: dict[str, dict[str, str]],
        debug_port: int,
        *,
        deep: bool = False,
    ) -> dict[str, object]:
        """Collect dashboard health data in a worker thread."""

        ready_count = 0
        total_count = len(TOOL_FILES) + len(custom_tools)
        errors: list[str] = []
        for tool_name, metadata in TOOL_FILES.items():
            ready, tool_errors = self._check_one_tool_health(tool_name, metadata, custom=False, deep=deep)
            ready_count += 1 if ready else 0
            errors.extend(tool_errors)
        for tool_id, metadata in custom_tools.items():
            ready, tool_errors = self._check_one_tool_health(tool_id, metadata, custom=True, deep=deep)
            ready_count += 1 if ready else 0
            errors.extend(tool_errors)
        cdp_ok, cdp_detail = self._check_cdp_health(debug_port)
        return {
            "ready_count": ready_count,
            "total_count": total_count,
            "errors": errors,
            "cdp_ok": cdp_ok,
            "cdp_detail": cdp_detail,
        }

    def _format_smart_status(self, health: dict[str, object] | None = None) -> str:
        """Build the compact dashboard footer status text."""

        username = self.username_var.get().strip()
        vnedu_text = "ĐÃ ĐĂNG NHẬP" if username else "CHƯA ĐĂNG NHẬP"
        if not health:
            return f"VNEDU: {vnedu_text} | CDP: ĐANG KIỂM TRA | TOOL: ĐANG KIỂM TRA"
        cdp_text = "OK" if health.get("cdp_ok") else "LỖI"
        ready_count = int(health.get("ready_count") or 0)
        total_count = int(health.get("total_count") or 0)
        return f"VNEDU: {vnedu_text} | CDP: {cdp_text} | TOOL: {ready_count}/{total_count} SẴN SÀNG"

    def _tool_health_cache_key(self, custom_tools: dict[str, dict[str, str]], debug_port: int) -> str:
        """Build a cheap cache key for automatic dashboard health checks."""

        parts = [f"cdp={debug_port}"]
        for tool_name, metadata in TOOL_FILES.items():
            try:
                external_path = external_tool_script_path(tool_name)
                stat = external_path.stat() if external_path.exists() else None
                external_sig = f"{stat.st_mtime_ns}:{stat.st_size}" if stat else "missing"
            except OSError:
                external_sig = "error"
            parts.append(
                f"default:{tool_name}:{external_sig}:payload={len(EMBEDDED_TOOL_PAYLOADS.get(tool_name, ''))}"
            )
        for tool_id, metadata in sorted(custom_tools.items()):
            try:
                external_path = custom_tool_external_path(metadata)
                stat = external_path.stat() if external_path.exists() else None
                external_sig = f"{stat.st_mtime_ns}:{stat.st_size}" if stat else "missing"
            except (OSError, ValueError):
                external_sig = "error"
            parts.append(f"custom:{tool_id}:{external_sig}:payload={len(metadata.get('payload', ''))}")
        return "|".join(parts)

    def _use_cached_health_if_fresh(self, cache_key: str) -> bool:
        """Reuse recent health results so mode switches do not restart heavy checks."""

        if not self.health_result or self._health_cache_key != cache_key:
            return False
        if time.monotonic() - self._health_cache_at > HEALTH_CHECK_CACHE_SECONDS:
            return False
        self.smart_status_var.set(self._format_smart_status(self.health_result))
        return True

    def _schedule_dashboard_health_check(self, *, force: bool = False) -> None:
        """Start a silent background check after the dashboard is rendered."""

        try:
            debug_port = parse_debug_port(self.port_var.get())
        except ValueError:
            debug_port = DEFAULT_DEBUG_PORT
        custom_tools = copy.deepcopy(self.custom_tools)
        cache_key = self._tool_health_cache_key(custom_tools, debug_port)
        if not force and self._use_cached_health_if_fresh(cache_key):
            return
        if self._health_after_id is not None:
            try:
                self.root.after_cancel(self._health_after_id)
            except tk.TclError:
                pass
            self._health_after_id = None
        if self._health_in_progress and not force:
            if self.health_result:
                self.smart_status_var.set(self._format_smart_status(self.health_result))
            else:
                self.smart_status_var.set(self._format_smart_status(None))
            return

        self._health_check_token += 1
        token = self._health_check_token
        self.smart_status_var.set(self._format_smart_status(None))

        def worker() -> None:
            health = self._collect_dashboard_health(custom_tools, debug_port, deep=False)
            self._safe_after(0, lambda: self._apply_dashboard_health_result(token, health, cache_key))

        def start_worker() -> None:
            self._health_after_id = None
            self._health_in_progress = True
            threading.Thread(target=worker, daemon=True, name="VNEDUDashboardHealthCheck").start()

        self._health_after_id = self._safe_after(HEALTH_CHECK_DELAY_MS, start_worker)

    def _apply_dashboard_health_result(self, token: int, health: dict[str, object], cache_key: str) -> None:
        """Apply the latest silent health-check result to the footer."""

        self._health_in_progress = False
        if token != self._health_check_token:
            return
        self.health_result = health
        self._health_cache_key = cache_key
        self._health_cache_at = time.monotonic()
        self.smart_status_var.set(self._format_smart_status(health))
        if health.get("errors"):
            self.status_var.set("Có lỗi nhẹ trong tool. Mở CÔNG CỤ > KIỂM TRA LẠI TOOL để xem chi tiết.")

    def _status_pill(
        self,
        parent: tk.Misc,
        kind: str,
        *,
        palette: dict[str, tuple[str, str, str]] | None = None,
    ) -> tk.Label:
        """Create a small status badge for a tool card."""

        colors = palette or TOOL_STATUS_COLORS
        text, bg, fg = colors.get(kind, colors["missing"])
        return tk.Label(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=(UI_FONT_FAMILY, 8, "bold"),
            padx=8,
            pady=2,
        )

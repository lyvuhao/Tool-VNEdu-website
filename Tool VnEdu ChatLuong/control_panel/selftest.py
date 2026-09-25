"""Kiểm tra nhanh đóng gói dashboard và file tool (`--self-test`)."""

from __future__ import annotations

import sys

from .custom_tools import (
    custom_tool_external_path,
    decode_custom_tool_source,
    load_custom_tools,
    resolve_custom_tool_script,
)
from .embedded import decode_embedded_tool_source, external_tool_script_path, resolve_tool_script
from .embedded_payloads import EMBEDDED_TOOL_PAYLOADS
from .process import is_tool_process_mutex_active, ToolProcessGuard
from .tool_registry import TOOL_FILES


def run_self_test() -> int:
    """Run lightweight checks for dashboard packaging and tool presence."""

    failures: list[str] = []
    expected_tools = set(TOOL_FILES)
    embedded_tools = set(EMBEDDED_TOOL_PAYLOADS)
    if embedded_tools != expected_tools:
        missing = sorted(expected_tools - embedded_tools)
        extra = sorted(embedded_tools - expected_tools)
        if missing:
            failures.append(f"Thiếu payload nhúng cho: {', '.join(missing)}")
        if extra:
            failures.append(f"Payload nhúng thừa: {', '.join(extra)}")

    guard: ToolProcessGuard | None = None
    try:
        guard = ToolProcessGuard("self_test:tool_process_guard")
        if not guard.acquire():
            failures.append("Khóa chống mở trùng tool đang bị chiếm bất thường.")
        elif sys.platform == "win32" and not is_tool_process_mutex_active("self_test:tool_process_guard"):
            failures.append("Không đọc được trạng thái khóa chống mở trùng tool.")
    except Exception as error:  # noqa: BLE001
        failures.append(f"Khóa chống mở trùng tool: {error}")
    finally:
        if guard is not None:
            guard.release()

    for tool_name, metadata in TOOL_FILES.items():
        try:
            external_path = external_tool_script_path(tool_name)
            if external_path.exists():
                compile(external_path.read_text(encoding="utf-8-sig"), str(external_path), "exec")

            embedded_script_path = resolve_tool_script(tool_name, allow_external=False)
            if not embedded_script_path.exists():
                raise FileNotFoundError(f"Không bung được file nhúng: {embedded_script_path}")
            compile(embedded_script_path.read_text(encoding="utf-8-sig"), str(embedded_script_path), "exec")
            compile(
                decode_embedded_tool_source(tool_name),
                f"<embedded {metadata['script']}>",
                "exec",
            )
        except Exception as error:  # noqa: BLE001
            failures.append(f"{metadata['script']}: {error}")

    for tool_id, metadata in load_custom_tools().items():
        try:
            external_path = custom_tool_external_path(metadata)
            if external_path.exists():
                compile(external_path.read_text(encoding="utf-8-sig"), str(external_path), "exec")

            embedded_script_path = resolve_custom_tool_script(tool_id, allow_external=False)
            if not embedded_script_path.exists():
                raise FileNotFoundError(f"Không bung được file nhúng: {embedded_script_path}")
            compile(embedded_script_path.read_text(encoding="utf-8-sig"), str(embedded_script_path), "exec")
            compile(
                decode_custom_tool_source(tool_id, metadata),
                f"<embedded custom {metadata['script']}>",
                "exec",
            )
        except Exception as error:  # noqa: BLE001
            failures.append(f"{metadata.get('title', tool_id)}: {error}")
    if failures:
        print("SELF-TEST FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("SELF-TEST OK")
    return 0

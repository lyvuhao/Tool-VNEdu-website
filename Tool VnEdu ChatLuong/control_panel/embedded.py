"""Giải nén / cập nhật mã nguồn tool nhúng và xác định script cần chạy."""

from __future__ import annotations

import base64
import gzip
import hashlib
import sys
from pathlib import Path

from .custom_tools import decode_payload_bytes, default_tool_payload, tool_workspace_dir
from .embedded_payloads import PAYLOAD_SOURCE_FILE
from .embedded_payloads import EMBEDDED_TOOL_PAYLOADS, PAYLOAD_END_MARKER, PAYLOAD_START
from .storage import embedded_tool_dir
from .tool_registry import TOOL_FILES


def decode_embedded_tool_source(tool_name: str) -> str:
    """Decode one embedded legacy tool source payload."""

    return decode_embedded_tool_bytes(tool_name).decode("utf-8-sig")


def decode_embedded_tool_bytes(tool_name: str) -> bytes:
    """Decode one embedded legacy tool source payload as exact bytes."""

    payload = default_tool_payload(tool_name)
    if not payload:
        raise FileNotFoundError(f"Không có mã nhúng dự phòng cho tool: {tool_name}")
    try:
        return decode_payload_bytes(payload)
    except Exception as error:  # noqa: BLE001 - payload corruption is a startup/runtime issue.
        raise RuntimeError(f"Mã nhúng của tool {tool_name} bị lỗi: {error}") from error


def extract_embedded_tool_script(tool_name: str, script_name: str) -> Path:
    """Write an embedded tool script to app data and return its path."""

    target = embedded_tool_dir() / script_name
    source = decode_embedded_tool_bytes(tool_name)
    existing = b""
    if target.exists():
        try:
            existing = target.read_bytes()
        except OSError:
            existing = b""
    if existing != source:
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        tmp_path.write_bytes(source)
        tmp_path.replace(target)
    return target


def encode_embedded_tool_source(source: bytes) -> str:
    """Return a compact base64+gzip payload for one tool source file."""

    return base64.b64encode(gzip.compress(source, compresslevel=9)).decode("ascii")


def sha256_hex(data: bytes) -> str:
    """Return a stable SHA-256 hex digest for one byte payload."""

    return hashlib.sha256(data).hexdigest()


def source_bytes_for_embedding(tool_name: str) -> bytes:
    """Read current source for a tool, falling back to the existing embedded snapshot."""

    metadata = TOOL_FILES.get(tool_name)
    if metadata is None:
        raise ValueError(f"Tool không hợp lệ: {tool_name}")
    source_path = tool_workspace_dir() / metadata["script"]
    if source_path.exists():
        return source_path.read_bytes()
    return decode_embedded_tool_bytes(tool_name)


def build_embedded_payload_block() -> tuple[str, dict[str, str]]:
    """Build the Python source block for the current embedded tool snapshots."""

    payloads: dict[str, str] = {}
    lines = [PAYLOAD_START]
    for tool_name in TOOL_FILES:
        payload = encode_embedded_tool_source(source_bytes_for_embedding(tool_name))
        payloads[tool_name] = payload
        lines.append(f"    {tool_name!r}: (")
        for index in range(0, len(payload), 96):
            lines.append(f"        {payload[index:index + 96]!r}")
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines), payloads


def refresh_embedded_payloads_in_control_panel() -> None:
    """Refresh embedded fallback snapshots in this control-panel source file."""

    if getattr(sys, "frozen", False):
        raise RuntimeError("Bản .exe không thể tự cập nhật mã nhúng. Hãy chạy file .py để đổi file tool.")

    control_panel_path = PAYLOAD_SOURCE_FILE
    source = control_panel_path.read_text(encoding="utf-8")
    start = source.index(PAYLOAD_START)
    end = source.index(PAYLOAD_END_MARKER, start) + len("\n}")
    payload_block, payloads = build_embedded_payload_block()
    tmp_path = control_panel_path.with_suffix(control_panel_path.suffix + ".tmp")
    tmp_path.write_text(source[:start] + payload_block + source[end:], encoding="utf-8")
    tmp_path.replace(control_panel_path)
    EMBEDDED_TOOL_PAYLOADS.clear()
    EMBEDDED_TOOL_PAYLOADS.update(payloads)


def external_tool_script_path(tool_name: str) -> Path:
    """Return the expected external script path for one logical tool."""

    metadata = TOOL_FILES.get(tool_name)
    if metadata is None:
        raise ValueError(f"Tool không hợp lệ: {tool_name}")
    return tool_workspace_dir() / metadata["script"]


def resolve_tool_script(tool_name: str, allow_external: bool = True) -> Path:
    """Return one validated tool script path."""

    metadata = TOOL_FILES.get(tool_name)
    if metadata is None:
        raise ValueError(f"Tool không hợp lệ: {tool_name}")
    script_path = external_tool_script_path(tool_name)
    if allow_external and script_path.exists():
        return script_path
    return extract_embedded_tool_script(tool_name, metadata["script"])


def embedded_tool_text(tool_name: str) -> str:
    """Return the normalized embedded source text for one default tool."""

    return decode_embedded_tool_source(tool_name)

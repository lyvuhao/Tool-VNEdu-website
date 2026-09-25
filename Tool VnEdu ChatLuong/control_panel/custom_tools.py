"""Thư mục làm việc của tool, tool tuỳ chỉnh và ghi đè tool mặc định."""

from __future__ import annotations

import base64
import gzip
import json
import re
import shutil
import sys
import time
import unicodedata
import uuid
from pathlib import Path

from .config import ACCENT_CHOICES, CUSTOM_TOOL_ID_PREFIX, CUSTOM_TOOLS_DIR_NAME
from .embedded_payloads import EMBEDDED_TOOL_PAYLOADS
from .paths import TOOL_DIR
from .storage import (
    app_data_dir,
    bundled_base_dir,
    custom_tools_config_path,
    default_tool_overrides_path,
    embedded_tool_dir,
)
from .tool_registry import TOOL_FILES


def tool_workspace_dir() -> Path:
    """Return the writable folder where tool scripts run and store sidecar files."""

    if getattr(sys, "frozen", False):
        target_dir = app_data_dir() / "tools"
        target_dir.mkdir(parents=True, exist_ok=True)
        source_dir = bundled_base_dir()
        overrides = load_default_tool_overrides()
        for tool_name, metadata in TOOL_FILES.items():
            source = source_dir / metadata["script"]
            target = target_dir / metadata["script"]
            override = overrides.get(tool_name)
            if override and override.get("payload"):
                try:
                    override_bytes = decode_payload_bytes(override["payload"])
                    if not target.exists() or target.read_bytes() != override_bytes:
                        target.write_bytes(override_bytes)
                    continue
                except Exception:
                    pass
            if not source.exists():
                continue
            # Only seed defaults on first run. Once a user has replaced a tool,
            # the packaged app must not silently overwrite it on later launches.
            if not target.exists():
                shutil.copy2(source, target)
        return target_dir
    return TOOL_DIR


def custom_tool_storage_dir() -> Path:
    """Return the writable folder where user-added external tool copies are stored."""

    target_dir = tool_workspace_dir() / CUSTOM_TOOLS_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def slugify_tool_text(text: str) -> str:
    """Convert a display title into a stable ASCII slug for file/id names."""

    normalized = unicodedata.normalize("NFKD", text.strip()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return slug or "tool"


def custom_tool_id_for_title(title: str) -> str:
    """Build a collision-resistant custom tool id."""

    return f"{CUSTOM_TOOL_ID_PREFIX}{slugify_tool_text(title)}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def normalize_custom_script_path(script: str) -> str:
    """Return a safe custom tool script path relative to the tool workspace."""

    normalized = script.replace("\\", "/").strip()
    candidate = Path(normalized)
    parts = candidate.parts
    if (
        candidate.is_absolute()
        or len(parts) != 2
        or parts[0] != CUSTOM_TOOLS_DIR_NAME
        or parts[1] in {"", ".", ".."}
        or Path(parts[1]).name != parts[1]
        or not parts[1].lower().endswith(".py")
    ):
        raise ValueError("Đường dẫn tool tùy chỉnh không hợp lệ.")
    return f"{CUSTOM_TOOLS_DIR_NAME}/{parts[1]}"


def sanitize_custom_tools(raw_tools: object) -> dict[str, dict[str, str]]:
    """Normalize custom-tool config data and drop malformed entries."""

    tools: dict[str, dict[str, str]] = {}
    if not isinstance(raw_tools, dict):
        return tools
    for tool_id, metadata in raw_tools.items():
        if not isinstance(tool_id, str) or not isinstance(metadata, dict):
            continue
        title = str(metadata.get("title") or "").strip()
        try:
            script = normalize_custom_script_path(str(metadata.get("script") or ""))
        except ValueError:
            continue
        if not tool_id.startswith(CUSTOM_TOOL_ID_PREFIX) or not title:
            continue
        tools[tool_id] = {
            "title": title,
            "description": str(metadata.get("description") or "").strip(),
            "script": script,
            "accent": str(metadata.get("accent") or ACCENT_CHOICES["Xanh ngọc"]).strip(),
            "payload": str(metadata.get("payload") or "").strip(),
            "created_at": str(metadata.get("created_at") or ""),
            "updated_at": str(metadata.get("updated_at") or ""),
        }
    return tools


def load_custom_tools() -> dict[str, dict[str, str]]:
    """Load user-added tools from app config, ignoring malformed entries."""

    path = custom_tools_config_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    raw_tools = raw.get("tools", {})
    return sanitize_custom_tools(raw_tools)


def save_custom_tools(tools: dict[str, dict[str, str]]) -> None:
    """Persist user-added tool metadata and embedded payloads atomically."""

    path = custom_tools_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tools": tools}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def sanitize_default_tool_overrides(raw_overrides: object) -> dict[str, dict[str, str]]:
    """Normalize persisted fallback payloads for default tools."""

    overrides: dict[str, dict[str, str]] = {}
    if not isinstance(raw_overrides, dict):
        return overrides
    for tool_name, metadata in raw_overrides.items():
        if tool_name not in TOOL_FILES or not isinstance(metadata, dict):
            continue
        payload = str(metadata.get("payload") or "").strip()
        if not payload:
            continue
        overrides[tool_name] = {
            "payload": payload,
            "source_hash": str(metadata.get("source_hash") or "").strip(),
            "source_name": str(metadata.get("source_name") or "").strip(),
            "updated_at": str(metadata.get("updated_at") or ""),
        }
    return overrides


def load_default_tool_overrides() -> dict[str, dict[str, str]]:
    """Load default-tool fallback payload overrides from app data."""

    path = default_tool_overrides_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return sanitize_default_tool_overrides(raw.get("tools", {}))


def save_default_tool_overrides(overrides: dict[str, dict[str, str]]) -> None:
    """Persist default-tool fallback payload overrides atomically."""

    path = default_tool_overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tools": sanitize_default_tool_overrides(overrides)}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def default_tool_payload(tool_name: str) -> str:
    """Return the active fallback payload for one default tool."""

    if getattr(sys, "frozen", False):
        override = load_default_tool_overrides().get(tool_name)
        if override and override.get("payload"):
            return override["payload"].strip()
    return EMBEDDED_TOOL_PAYLOADS.get(tool_name, "").strip()


def custom_tool_external_path(metadata: dict[str, str]) -> Path:
    """Return the external script path for one user-added tool."""

    return tool_workspace_dir() / normalize_custom_script_path(metadata["script"])


def decode_payload_bytes(payload: str) -> bytes:
    """Decode one base64+gzip source payload as exact bytes."""

    compressed = base64.b64decode(payload.encode("ascii"))
    return gzip.decompress(compressed)


def decode_custom_tool_bytes(tool_id: str, metadata: dict[str, str]) -> bytes:
    """Decode one custom tool fallback payload as exact bytes."""

    payload = metadata.get("payload", "").strip()
    if not payload:
        raise FileNotFoundError(f"Tool tùy chỉnh chưa có bản nhúng: {metadata.get('title') or tool_id}")
    try:
        return decode_payload_bytes(payload)
    except Exception as error:  # noqa: BLE001 - user-added payload corruption needs a clear message.
        raise RuntimeError(f"Bản nhúng của tool tùy chỉnh {tool_id} bị lỗi: {error}") from error


def decode_custom_tool_source(tool_id: str, metadata: dict[str, str]) -> str:
    """Decode one custom tool fallback payload."""

    return decode_custom_tool_bytes(tool_id, metadata).decode("utf-8-sig")


def extract_custom_tool_script(tool_id: str, metadata: dict[str, str]) -> Path:
    """Write a custom tool payload to the embedded runtime folder and return it."""

    relative_script = Path(normalize_custom_script_path(metadata["script"]))
    target = embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / relative_script.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source = decode_custom_tool_bytes(tool_id, metadata)
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


def resolve_custom_tool_script(tool_id: str, allow_external: bool = True) -> Path:
    """Return the script path for a user-added tool, falling back to its payload."""

    metadata = load_custom_tools().get(tool_id)
    if metadata is None:
        raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_id}")
    script_path = custom_tool_external_path(metadata)
    if allow_external and script_path.exists():
        return script_path
    return extract_custom_tool_script(tool_id, metadata)

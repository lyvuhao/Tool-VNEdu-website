"""Đọc/ghi hồ sơ tự động dạng JSON đi kèm file Excel."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .profile import KHDHProfile


# =====================================================================
# Task 2.4 — Auto JSON I/O
# =====================================================================

def compute_excel_sha1(path) -> str:
    """SHA1 hex của file Excel — dùng để detect 'hồ sơ thay đổi từ checkpoint'."""
    p = Path(path)
    if not p.exists():
        return ""
    h = hashlib.sha1()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def auto_json_path_for(excel_path) -> Path:
    """Đường dẫn file `.auto.json` đi kèm 1 file Excel."""
    p = Path(excel_path)
    return p.with_suffix(".auto.json")


def save_profile_auto_json(profile: KHDHProfile, excel_path,
                            checkpoint: dict | None = None) -> None:
    """Ghi file `.auto.json` đi kèm Excel.

    Format:
        {
            "version": 1,
            "profile_excel_sha1": "<hex>",
            "ids": {
                "lop": {"6A4": "5000926787", ...},
                "mon": {"Ngoại ngữ": "8", ...},
                "phan_mon": {"TC Ngoại ngữ": "396461796", ...}
            },
            "checkpoint": {...} | null,
            "saved_at": "ISO timestamp"
        }

    Args:
        profile: hồ sơ
        excel_path: đường dẫn file Excel — dùng để compute sha1 + đặt tên file json
        checkpoint: dict checkpoint (last_completed_tuan, ...) hoặc None để xóa
    """
    p_json = auto_json_path_for(excel_path)
    p_json.parent.mkdir(parents=True, exist_ok=True)

    # Build ids dict
    ids = {"lop": {}, "mon": {}, "phan_mon": {}}
    for tpl in profile.all_templates():
        for s in tpl.slots:
            if s.lop_text and s.lop_id:
                ids["lop"][s.lop_text] = s.lop_id
            if s.mon_text and s.mon_id:
                ids["mon"][s.mon_text] = s.mon_id
            if s.phan_mon_text and s.phan_mon_id:
                ids["phan_mon"][s.phan_mon_text] = s.phan_mon_id

    data = {
        "version": 1,
        "profile_excel_sha1": compute_excel_sha1(excel_path),
        "ids": ids,
        "checkpoint": checkpoint,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

    # Atomic write
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=p_json.stem + ".", suffix=".tmp", dir=str(p_json.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, p_json)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_profile_auto_json(excel_path) -> dict:
    """Đọc file `.auto.json` đi kèm Excel.

    Returns:
        dict đã parse, hoặc dict rỗng nếu file không tồn tại / corrupt.
        Version > 1 (future schema): trả về dict (forward-compat) — caller
        dùng `.get()` defensively. Version unknown/missing: trả về {}.
    """
    p_json = auto_json_path_for(excel_path)
    if not p_json.exists():
        return {}
    try:
        with p_json.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        version = data.get("version")
        if not isinstance(version, int) or version < 1:
            # Không có version hoặc invalid — treat as corrupt
            return {}
        # version >= 1 → trả về (forward-compat cho v2, v3...)
        # Caller phải dùng .get() defensively cho mọi field.
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def auto_json_is_fresh_for(excel_path) -> bool:
    """True khi sha1 trong .auto.json khớp với sha1 hiện tại của Excel.

    Dùng để check 'profile có thay đổi từ checkpoint cuối không' (Property 6).
    """
    data = load_profile_auto_json(excel_path)
    if not data:
        return False
    return data.get("profile_excel_sha1") == compute_excel_sha1(excel_path)

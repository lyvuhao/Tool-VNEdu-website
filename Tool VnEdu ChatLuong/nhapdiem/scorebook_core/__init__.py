"""Lõi Sổ điểm cho tool Nhập điểm.

Phần dùng chung được lấy trực tiếp từ package `nhanxet`; package này chỉ chứa phần
khác biệt (ghi điểm, xác minh lưu, chọn context) — thay cho bản sao `nhanxet_pro`
nhúng dạng chuỗi + exec() trước đây.
"""

from __future__ import annotations

from nhanxet.access import (
    apply_access_entries_to_context,
    class_options_from_access_entries,
    merge_score_options,
    resolve_accessible_selection,
    subject_options_from_access_entries,
)
from nhanxet.models import (
    ScorebookAccessEntry,
    ScorebookContext,
    ScoreColumnSchema,
    ScoreOption,
)
from nhanxet.progress import (
    build_progress_caption,
    clamp_progress_value,
    create_subprogress_reporter,
    emit_progress,
    password_entry_show_value,
)
from .automation.client import (
    VnEduScoreAutomation,
)

__all__ = [
    "apply_access_entries_to_context",
    "build_progress_caption",
    "clamp_progress_value",
    "class_options_from_access_entries",
    "create_subprogress_reporter",
    "emit_progress",
    "merge_score_options",
    "password_entry_show_value",
    "resolve_accessible_selection",
    "ScorebookAccessEntry",
    "ScorebookContext",
    "ScoreColumnSchema",
    "ScoreOption",
    "subject_options_from_access_entries",
    "VnEduScoreAutomation",
]

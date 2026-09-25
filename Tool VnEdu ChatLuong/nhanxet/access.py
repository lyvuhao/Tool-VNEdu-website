"""Xử lý danh sách quyền truy cập Sổ điểm (khối/lớp/môn được phép)."""

from __future__ import annotations

from typing import List, Tuple

from .models import ScorebookAccessEntry, ScorebookContext, ScoreOption


def merge_score_options(*option_groups: List[ScoreOption]) -> List[ScoreOption]:
    """Merges option groups by id while preserving first-seen order."""
    merged: List[ScoreOption] = []
    seen_ids = set()
    for group in option_groups:
        for option in group:
            option_id = str(option.option_id).strip()
            option_text = str(option.option_text).strip() or option_id
            if not option_id or option_id in seen_ids:
                continue
            seen_ids.add(option_id)
            merged.append(ScoreOption(option_id=option_id, option_text=option_text))
    return merged


def ensure_selected_score_option(
    options: List[ScoreOption],
    selected_id: str,
    selected_text: str,
) -> List[ScoreOption]:
    """Keeps the selected option visible even when the live ExtJS store is incomplete."""
    normalized_id = selected_id.strip()
    normalized_text = selected_text.strip() or normalized_id
    if not normalized_id:
        return merge_score_options(options)
    return merge_score_options(
        options,
        [ScoreOption(option_id=normalized_id, option_text=normalized_text)],
    )


def class_options_from_access_entries(entries: List[ScorebookAccessEntry]) -> List[ScoreOption]:
    """Builds class options from discovered access entries."""
    return merge_score_options(
        [
            ScoreOption(
                option_id=entry.class_id.strip(),
                option_text=entry.class_text.strip() or entry.class_id.strip(),
            )
            for entry in entries
            if entry.class_id.strip()
        ]
    )


def subject_options_from_access_entries(
    entries: List[ScorebookAccessEntry],
    class_id: str = "",
) -> List[ScoreOption]:
    """Builds subject options from discovered access entries."""
    normalized_class_id = class_id.strip()
    matching_entries = [
        entry for entry in entries if not normalized_class_id or entry.class_id.strip() == normalized_class_id
    ]
    if not matching_entries and normalized_class_id:
        matching_entries = list(entries)
    return merge_score_options(
        [
            ScoreOption(
                option_id=entry.subject_id.strip(),
                option_text=entry.subject_text.strip() or entry.subject_id.strip(),
            )
            for entry in matching_entries
            if entry.subject_id.strip()
        ]
    )


def resolve_accessible_selection(
    entries: List[ScorebookAccessEntry],
    preferred_class_id: str = "",
    preferred_subject_id: str = "",
) -> Tuple[str, str, List[str]]:
    """Chooses a valid class-subject pair from the discovered permission matrix."""
    notes: List[str] = []
    if not entries:
        return "", "", notes

    preferred_class_id = preferred_class_id.strip()
    preferred_subject_id = preferred_subject_id.strip()

    exact_entry = next(
        (
            entry
            for entry in entries
            if entry.class_id == preferred_class_id and entry.subject_id == preferred_subject_id
        ),
        None,
    )
    if exact_entry is not None:
        return exact_entry.class_id, exact_entry.subject_id, notes

    if preferred_class_id:
        class_entry = next((entry for entry in entries if entry.class_id == preferred_class_id), None)
        if class_entry is not None:
            if preferred_subject_id:
                notes.append("Môn đã chọn không có quyền ở lớp này, app chuyển sang môn hợp lệ đầu tiên.")
            return class_entry.class_id, class_entry.subject_id, notes

    if preferred_subject_id:
        subject_entry = next((entry for entry in entries if entry.subject_id == preferred_subject_id), None)
        if subject_entry is not None:
            if preferred_class_id:
                notes.append("Lớp đã chọn không có quyền với môn này, app chuyển sang lớp hợp lệ đầu tiên.")
            return subject_entry.class_id, subject_entry.subject_id, notes

    fallback_entry = entries[0]
    if preferred_class_id or preferred_subject_id:
        notes.append("Tổ hợp lớp/môn đã chọn không có quyền, app chuyển sang tổ hợp hợp lệ đầu tiên.")
    return fallback_entry.class_id, fallback_entry.subject_id, notes


def apply_access_entries_to_context(
    context: ScorebookContext,
    entries: List[ScorebookAccessEntry],
    grade_id: str,
    term_id: str,
) -> ScorebookContext:
    """Attaches discovered permission entries to the returned scorebook context."""
    if not entries:
        context.accessible_entries = []
        context.accessible_grade_id = ""
        context.accessible_term_id = ""
        return context
    context.class_options = merge_score_options(
        context.class_options,
        class_options_from_access_entries(entries),
    )
    context.subject_options = merge_score_options(
        context.subject_options,
        subject_options_from_access_entries(entries),
    )
    context.accessible_entries = list(entries)
    context.accessible_grade_id = grade_id.strip()
    context.accessible_term_id = term_id.strip()
    return context

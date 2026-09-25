"""VNEDU Auto Nhận Xét V2.

Skeleton mới cho tool nhận xét theo hướng:
- Attach live Chrome qua Chrome DevTools Protocol.
- Tự mở VNEDU và đăng nhập nếu cần.
- Đọc trực tiếp combobox `Khối/Lớp/Môn/Học kỳ` từ cửa sổ `Sổ điểm`.
- Chuẩn bị kiến trúc cho bước quét schema cột và ghi nhận xét chính xác hơn.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, Iterator, List, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright


_SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = _SCRIPT_DIR / "nhanxet_v2_config.json"
DEFAULT_RULE_EXPORT_DIR = _SCRIPT_DIR / "nhanxet_mac_dinh"
APP_TITLE = "AUTO Ghi Nhận Xét Học Sinh - Developed by Vu Hao"
WINDOW_SIZE = "1060x760"
NUMERIC_COMMENT_SCORE_RE = re.compile(r"^\s*(?:10(?:[.,]0+)?|[0-9](?:[.,]\d+)?)\s*$")
PASSIVE_SCAN_SKIPPED = object()
ProgressCallback = Callable[[float, str], None]


@dataclass
class ScoreOption:
    """One ExtJS combobox option in the VNEDU scorebook screen."""

    option_id: str
    option_text: str


@dataclass
class ScoreColumnSchema:
    """Placeholder for a parsed scorebook leaf column."""

    column_key: str
    header_path: Tuple[str, ...]
    leaf_index: int
    display_name: str = ""
    editable: bool = False
    role_hint: str = ""
    input_kind: str = ""
    sample_value: str = ""
    block_index: str = ""
    child_index: str = ""
    data_column: str = ""
    input_name: str = ""


@dataclass
class CommentRule:
    """One rule mapping score/value conditions to a comment template."""

    condition: str
    template: str


@dataclass
class CommentWriteRow:
    """One student row prepared for the internal write queue."""

    row_index: int
    student_code: str
    student_name: str
    source_column_key: str
    source_column_name: str
    source_value: str
    comment_input_name: str
    current_comment: str
    proposed_comment: str
    status: str
    reason: str = ""


@dataclass
class CommentWriteResult:
    """Write-back result summary after applying the internal write queue."""

    attempted: int
    updated: int
    verified: int
    skipped: int
    save_clicked: bool
    save_verified: bool = False
    save_verification_mode: str = ""
    save_verification_detail: str = ""
    verified_input_names: List[str] = field(default_factory=list)
    failed_input_names: List[str] = field(default_factory=list)
    failed_rows: List[str] = field(default_factory=list)


@dataclass
class ScorebookAccessEntry:
    """One class-subject permission entry discovered from the live scorebook screen."""

    grade_id: str
    grade_text: str
    class_id: str
    class_text: str
    subject_id: str
    subject_text: str
    term_id: str
    term_text: str
    teacher_text: str = ""
    permission_text: str = ""
    comment_input_count: int = 0
    enabled_comment_input_count: int = 0


@dataclass
class ScorebookDetectedColumns:
    """Auto-detected source/target columns derived from the parsed live schema."""

    preferred_score_column_key: str = ""
    preferred_score_reason: str = ""
    preferred_comment_column_key: str = ""
    score_candidate_keys: List[str] = field(default_factory=list)
    average_candidate_keys: List[str] = field(default_factory=list)
    comment_candidate_keys: List[str] = field(default_factory=list)


@dataclass
class ScorebookContext:
    """Current scorebook shell state read from the live browser."""

    grade_options: List[ScoreOption]
    selected_grade_id: str
    class_options: List[ScoreOption]
    selected_class_id: str
    subject_options: List[ScoreOption]
    selected_subject_id: str
    term_options: List[ScoreOption]
    selected_term_id: str
    window_id: str = ""
    window_title: str = ""
    teacher_text: str = ""
    permission_text: str = ""
    comment_input_count: int = 0
    enabled_comment_input_count: int = 0
    column_schemas: List[ScoreColumnSchema] = field(default_factory=list)
    detected_columns: ScorebookDetectedColumns = field(default_factory=ScorebookDetectedColumns)
    accessible_entries: List[ScorebookAccessEntry] = field(default_factory=list)
    accessible_grade_id: str = ""
    accessible_term_id: str = ""


def clamp_progress_value(value: float) -> float:
    """Clamps one progress value into the GUI-safe 0..100 range."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, numeric_value))


def emit_progress(
    progress_callback: ProgressCallback | None,
    value: float,
    message: str = "",
) -> None:
    """Sends one progress update when a reporter is available."""
    if progress_callback is None:
        return
    progress_callback(clamp_progress_value(value), str(message or "").strip())


def create_subprogress_reporter(
    progress_callback: ProgressCallback | None,
    start_value: float,
    end_value: float,
) -> ProgressCallback | None:
    """Maps one nested 0..100 reporter into a parent progress span."""
    if progress_callback is None:
        return None

    clamped_start = clamp_progress_value(start_value)
    clamped_end = clamp_progress_value(end_value)
    span = clamped_end - clamped_start

    def report(value: float, message: str = "") -> None:
        nested_value = clamp_progress_value(value)
        progress_callback(clamped_start + (span * (nested_value / 100.0)), message)

    return report


def build_progress_caption(progress_value: float, message: str, max_message_length: int = 48) -> str:
    """Builds a compact progress caption suitable for the canvas-based progress bar."""
    normalized_value = int(round(clamp_progress_value(progress_value)))
    normalized_message = str(message or "").strip() or "Sẵn sàng"
    if len(normalized_message) > max_message_length:
        normalized_message = normalized_message[: max_message_length - 3].rstrip() + "..."
    return f"{normalized_value:>3}% | {normalized_message}"


def password_entry_show_value(show_password: bool) -> str:
    """Returns the Tk `show` value for the password entry."""
    return "" if show_password else "*"


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


def parse_condition(condition_str: str):
    """Parses a score condition into a checker callable."""
    s = condition_str.strip()
    if not s:
        return None

    matched = re.match(r"^<=(\d+\.?\d*)$", s)
    if matched:
        value = float(matched.group(1))
        return lambda score, v=value: score <= v

    matched = re.match(r"^<(\d+\.?\d*)$", s)
    if matched:
        value = float(matched.group(1))
        return lambda score, v=value: score < v

    matched = re.match(r"^>=(\d+\.?\d*)$", s)
    if matched:
        value = float(matched.group(1))
        return lambda score, v=value: score >= v

    matched = re.match(r"^>(\d+\.?\d*)$", s)
    if matched:
        value = float(matched.group(1))
        return lambda score, v=value: score > v

    matched = re.match(r"^=(\d+\.?\d*)$", s)
    if matched:
        value = float(matched.group(1))
        return lambda score, v=value: score == v

    matched = re.match(r"^(\d+\.?\d*)\s*-\s*(\d+\.?\d*)$", s)
    if matched:
        low = float(matched.group(1))
        high = float(matched.group(2))
        return lambda score, lo=low, hi=high: lo <= score <= hi

    matched = re.match(r"^(\d+\.?\d*)$", s)
    if matched:
        value = float(matched.group(1))
        return lambda score, v=value: score == v

    normalized = s.upper()
    if normalized in ("Đ", "ĐẠT", "DAT", "D"):
        return lambda score: isinstance(score, str) and score.strip().upper() in (
            "Đ",
            "ĐẠT",
            "DAT",
            "D",
        )

    if normalized in ("CĐ", "CD", "CHƯA ĐẠT", "CHUA DAT"):
        return lambda score: isinstance(score, str) and score.strip().upper() in (
            "CĐ",
            "CD",
            "CHƯA ĐẠT",
            "CHUA DAT",
        )

    return None


def looks_like_numeric_comment_score(value: str) -> bool:
    """Returns whether an existing comment cell actually contains a numeric score-like value."""
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return bool(NUMERIC_COMMENT_SCORE_RE.fullmatch(normalized))


def describe_condition(condition_str: str) -> str:
    """Returns a short Vietnamese description for one rule condition."""
    condition = condition_str.strip()
    if re.match(r"^<=", condition):
        return f"Điểm ≤ {condition[2:]}"
    if re.match(r"^<", condition):
        return f"Điểm < {condition[1:]}"
    if re.match(r"^>=", condition):
        return f"Điểm ≥ {condition[2:]}"
    if re.match(r"^>", condition):
        return f"Điểm > {condition[1:]}"
    if re.match(r"^=", condition):
        return f"Điểm = {condition[1:]}"
    if "-" in condition and not condition.startswith("-"):
        parts = condition.split("-")
        if len(parts) == 2:
            return f"Điểm từ {parts[0].strip()} đến {parts[1].strip()}"
    if re.match(r"^\d+\.?\d*$", condition):
        return f"Điểm = {condition}"
    normalized = condition.upper()
    if normalized in ("Đ", "ĐẠT", "DAT", "D"):
        return "Xếp loại Đạt"
    if normalized in ("CĐ", "CD", "CHƯA ĐẠT", "CHUA DAT"):
        return "Xếp loại Chưa đạt"
    return "Không hợp lệ"


def compile_comment_rules(rules: List[CommentRule]) -> List[Tuple[Callable[[object], bool], str, str]]:
    """Compiles UI rules into callable matchers once per analyze/apply run."""
    compiled: List[Tuple[Callable[[object], bool], str, str]] = []
    for rule in rules:
        condition = rule.condition.strip()
        template = rule.template.strip()
        if not condition or not template:
            continue
        checker = parse_condition(condition)
        if checker is None:
            continue
        compiled.append((checker, template, condition))
    return compiled


def match_comment_for_value(
    raw_value: str,
    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],
) -> Tuple[str | None, str]:
    """Returns the first matching comment template and the matched condition."""
    value = raw_value.strip()
    if not value:
        return None, ""

    for checker, template, condition in compiled_rules:
        try:
            if checker(value):
                return template, condition
        except TypeError:
            continue

    try:
        numeric_value = float(value.replace(",", "."))
    except ValueError:
        return None, ""

    for checker, template, condition in compiled_rules:
        try:
            if checker(numeric_value):
                return template, condition
        except TypeError:
            continue

    return None, ""


def plan_comment_row_write(
    source_value: str,
    current_comment: str,
    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],
    allow_overwrite_existing_comment: bool = False,
) -> Tuple[str, str, str]:
    """Returns the proposed comment, queue status, and explanation for one live score row."""
    normalized_source_value = str(source_value).strip()
    normalized_current_comment = str(current_comment).strip()
    current_comment_is_numeric_score = looks_like_numeric_comment_score(normalized_current_comment)
    proposed_comment = ""

    if not normalized_source_value:
        return proposed_comment, "skip_no_score", "Chưa có điểm nguồn."

    if (
        normalized_current_comment
        and not current_comment_is_numeric_score
        and not allow_overwrite_existing_comment
    ):
        return (
            proposed_comment,
            "skip_existing_comment",
            "Ô nhận xét hiện đã có dữ liệu, app giữ nguyên để tránh ghi đè.",
        )

    matched_comment, matched_condition = match_comment_for_value(normalized_source_value, compiled_rules)
    if matched_comment is None:
        return (
            proposed_comment,
            "skip_unmatched",
            f"Không khớp rule nào cho giá trị `{normalized_source_value}`.",
        )

    proposed_comment = matched_comment
    if proposed_comment.strip() == normalized_current_comment:
        return proposed_comment, "skip_same", "Nhận xét hiện tại đã giống kết quả dự kiến."
    return proposed_comment, "ready", f"Khớp rule `{matched_condition}`."


def build_comment_write_row_from_live_data(
    live_row: Dict[str, object],
    source_column_key: str,
    source_column_name: str,
    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],
    allow_overwrite_existing_comment: bool = False,
) -> CommentWriteRow:
    """Builds one queue row from one live DOM row snapshot and the compiled rule set."""
    source_value = str(live_row.get("sourceValue", "")).strip()
    current_comment = str(live_row.get("currentComment", "")).strip()
    proposed_comment, status, reason = plan_comment_row_write(
        source_value,
        current_comment,
        compiled_rules,
        allow_overwrite_existing_comment=allow_overwrite_existing_comment,
    )
    return CommentWriteRow(
        row_index=int(live_row.get("rowIndex", 0) or 0),
        student_code=str(live_row.get("studentCode", "")).strip(),
        student_name=str(live_row.get("studentName", "")).strip(),
        source_column_key=source_column_key,
        source_column_name=source_column_name,
        source_value=source_value,
        comment_input_name=str(live_row.get("commentInputName", "")).strip(),
        current_comment=current_comment,
        proposed_comment=proposed_comment,
        status=status,
        reason=reason,
    )


def build_comment_write_rows_from_live_data(
    live_rows: List[Dict[str, object]],
    source_column_key: str,
    source_column_name: str,
    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],
    allow_overwrite_existing_comment: bool = False,
) -> List[CommentWriteRow]:
    """Builds the internal comment queue rows from live DOM rows."""
    return [
        build_comment_write_row_from_live_data(
            live_row,
            source_column_key=source_column_key,
            source_column_name=source_column_name,
            compiled_rules=compiled_rules,
            allow_overwrite_existing_comment=allow_overwrite_existing_comment,
        )
        for live_row in live_rows
    ]


def ready_comment_write_rows(write_rows: List[CommentWriteRow]) -> List[CommentWriteRow]:
    """Returns only rows that are ready for DOM write-back."""
    return [
        row
        for row in write_rows
        if row.status == "ready" and row.comment_input_name.strip() and row.proposed_comment.strip()
    ]


def build_comment_write_payload(rows_to_apply: List[CommentWriteRow]) -> List[Dict[str, str]]:
    """Builds the DOM payload used to write comments into live inputs."""
    return [
        {"inputName": row.comment_input_name, "text": row.proposed_comment}
        for row in rows_to_apply
    ]


def select_relevant_server_save_requests(
    save_requests: List[Dict[str, object]],
    request_markers: List[str] | None = None,
) -> List[Dict[str, object]]:
    """Keeps the most likely mutation requests triggered by one save action."""
    normalized_markers = [marker.strip().lower() for marker in list(request_markers or []) if marker.strip()]
    if normalized_markers:
        marker_matched_requests = []
        for request in save_requests:
            request_blob = " ".join(
                [
                    str(request.get("url", "")).strip(),
                    str(request.get("bodyPreview", "")).strip(),
                    str(request.get("responsePreview", "")).strip(),
                ]
            ).lower()
            if any(marker in request_blob for marker in normalized_markers):
                marker_matched_requests.append(request)
        if marker_matched_requests:
            return marker_matched_requests

    mutation_requests = [
        request
        for request in save_requests
        if str(request.get("method", "")).strip().upper() not in {"", "GET", "HEAD", "OPTIONS"}
        or bool(str(request.get("bodyPreview", "")).strip())
    ]
    return mutation_requests or list(save_requests)


def _json_save_response_state(response_preview: str) -> Tuple[bool | None, str]:
    """Extracts a success/failure hint from one JSON-like save response preview."""
    preview = response_preview.strip()
    if not preview or preview[:1] not in "[{":
        return None, ""
    try:
        payload = json.loads(preview)
    except (TypeError, ValueError):
        return None, ""

    if isinstance(payload, dict):
        if bool(payload.get("error")):
            return False, str(payload.get("error"))
        if "success" in payload:
            return bool(payload.get("success")), str(payload.get("message", "")).strip()
        if "status" in payload:
            status_value = str(payload.get("status", "")).strip().lower()
            if status_value in {"1", "true", "ok", "success"}:
                return True, str(payload.get("message", "")).strip()
            if status_value in {"0", "false", "error", "failed", "failure"}:
                return False, str(payload.get("message", "")).strip()
        if "message" in payload:
            message = str(payload.get("message", "")).strip()
            lowered_message = message.lower()
            if any(token in lowered_message for token in ("thành công", "thanh cong", "success", "ok")):
                return True, message
            if any(token in lowered_message for token in ("thất bại", "that bai", "không thành công", "khong thanh cong", "lỗi", "loi", "error", "exception")):
                return False, message
    return None, ""


def evaluate_server_save_verification(
    auto_save_requested: bool,
    save_clicked: bool,
    save_requests: List[Dict[str, object]],
    request_markers: List[str] | None = None,
) -> Tuple[bool, str, str]:
    """Evaluates whether one auto-save run was verified by server-side request/response signals."""
    if not auto_save_requested:
        return False, "not_requested", "Không bật tự bấm Lưu."
    if not save_clicked:
        return False, "button_missing", "Không tìm thấy nút Lưu để bấm tự động."

    relevant_requests = select_relevant_server_save_requests(save_requests, request_markers=request_markers)
    if not relevant_requests:
        return False, "no_request", "Đã bấm Lưu nhưng không bắt được request lưu từ trình duyệt."

    unfinished_requests = [request for request in relevant_requests if not bool(request.get("finished"))]
    if unfinished_requests:
        return False, "timeout", "Đã bấm Lưu nhưng request lưu chưa hoàn tất trong thời gian chờ."

    failing_status_requests = []
    for request in relevant_requests:
        try:
            status_code = int(request.get("status", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code not in range(200, 300) and status_code != 304:
            failing_status_requests.append((status_code, str(request.get("url", "")).strip()))
    if failing_status_requests:
        first_status, first_url = failing_status_requests[0]
        return False, "http_error", f"Request lưu trả về HTTP {first_status}: {first_url}"

    for request in relevant_requests:
        preview = str(request.get("responsePreview", "")).strip()
        response_state, response_message = _json_save_response_state(preview)
        if response_state is False:
            return False, "server_rejected", response_message or "Server trả về phản hồi lỗi khi lưu."

        lowered_preview = preview.lower()
        if any(
            token in lowered_preview
            for token in (
                "thất bại",
                "that bai",
                "không thành công",
                "khong thanh cong",
                "không có quyền",
                "khong co quyen",
                "permission denied",
                "exception",
            )
        ):
            return False, "server_rejected", preview[:180] or "Server trả về phản hồi lỗi khi lưu."

    return True, "server_response", f"Đã xác minh lưu server-side qua {len(relevant_requests)} request."


def summarize_comment_write_result(
    write_rows: List[CommentWriteRow],
    rows_to_apply: List[CommentWriteRow],
    result_payload: Dict[str, object],
    verification_map: Dict[str, object],
) -> CommentWriteResult:
    """Builds the final write-back summary from raw DOM execution and verification data."""
    verified = 0
    verified_input_names: List[str] = []
    failed_input_names: List[str] = list(result_payload.get("failed", []))
    failed_rows: List[str] = list(failed_input_names)
    for row in rows_to_apply:
        actual_value = str(verification_map.get(row.comment_input_name, "")).strip()
        if actual_value == row.proposed_comment.strip():
            verified += 1
            verified_input_names.append(row.comment_input_name)
        elif row.student_name:
            failed_input_names.append(row.comment_input_name)
            failed_rows.append(f"{row.student_name} ({row.student_code})")
        else:
            failed_input_names.append(row.comment_input_name)
            failed_rows.append(row.comment_input_name)

    return CommentWriteResult(
        attempted=len(rows_to_apply),
        updated=len(list(result_payload.get("updated", []))),
        verified=verified,
        skipped=len(write_rows) - len(rows_to_apply),
        save_clicked=bool(result_payload.get("saveClicked")),
        save_verified=bool(result_payload.get("saveVerified")),
        save_verification_mode=str(result_payload.get("saveVerificationMode", "")).strip(),
        save_verification_detail=str(result_payload.get("saveVerificationDetail", "")).strip(),
        verified_input_names=list(dict.fromkeys(verified_input_names)),
        failed_input_names=list(dict.fromkeys(failed_input_names)),
        failed_rows=list(dict.fromkeys(failed_rows)),
    )


class VnEduScoreAutomation:
    """Playwright CDP automation for the VNEDU scorebook screen."""

    def __init__(self, debug_port: int, target_url: str) -> None:
        self.debug_port = int(debug_port)
        self.target_url = target_url.strip()
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
        self._cdp_profile_dir = Path(local_app_data) / "VNEDU-NHANXET-V2-CDP"

    def _cdp_http_endpoint(self) -> str:
        """Returns the local HTTP endpoint exposed by Chromium DevTools."""
        return f"http://127.0.0.1:{self.debug_port}"

    def _is_cdp_ready(self) -> bool:
        """Checks whether a valid Chromium CDP endpoint is reachable."""
        try:
            with urlopen(f"{self._cdp_http_endpoint()}/json/version", timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return False

        browser_name = str(payload.get("Browser", "")).lower()
        return bool(payload.get("webSocketDebuggerUrl")) and (
            "chrome" in browser_name or "chromium" in browser_name or "edg" in browser_name
        )

    def _find_chromium_executable(self) -> str:
        """Tries common Windows locations for Chrome or Edge."""
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]

        for command in ("chrome", "msedge", "chromium"):
            located = shutil.which(command)
            if located:
                candidates.append(Path(located))

        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return str(candidate)
        return ""

    def _start_debug_browser(self) -> None:
        """Starts a dedicated Chromium session with remote debugging enabled."""
        executable = self._find_chromium_executable()
        if not executable:
            raise RuntimeError("Không tìm thấy Chrome/Edge trên máy để mở phiên debug.")

        self._cdp_profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            executable,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self._cdp_profile_dir}",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self.target_url:
            args.append(self.target_url)

        try:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as error:
            raise RuntimeError(f"Không thể khởi động browser debug: {error}") from error

    def _ensure_cdp_server(self) -> None:
        """Ensures the CDP endpoint exists before any automation action."""
        if self._is_cdp_ready():
            return

        self._start_debug_browser()
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._is_cdp_ready():
                return
            time.sleep(0.5)
        raise RuntimeError(
            f"Không thể mở cổng debug {self.debug_port}. Browser debug chưa sẵn sàng."
        )

    def _is_internal_browser_url(self, raw_url: str) -> bool:
        """Returns whether one CDP page URL points to a browser-internal surface."""
        url = raw_url.strip().lower()
        return (
            not url
            or url.startswith("chrome://")
            or url.startswith("chrome-extension://")
            or url.startswith("devtools://")
            or url.startswith("edge://")
            or url.startswith("about:")
        )

    def _target_host(self) -> str:
        """Returns the configured VNEDU host name when the target URL is valid."""
        try:
            return (urlparse(self.target_url).hostname or "").strip().lower()
        except ValueError:
            return ""

    def _page_priority(self, page: Page) -> int:
        """Scores CDP pages so the automation prefers stable VNEDU tabs over transient browser popups."""
        if page.is_closed():
            return -1

        page_url = (page.url or "").strip()
        if self._is_internal_browser_url(page_url):
            return 0

        parsed_page = urlparse(page_url)
        if parsed_page.scheme not in {"http", "https"}:
            return 1

        target_url = self.target_url.strip().lower()
        page_url_lower = page_url.lower()
        target_host = self._target_host()
        page_host = (parsed_page.hostname or "").strip().lower()

        if target_url and target_url in page_url_lower:
            return 110
        if target_host and page_host == target_host:
            return 100
        if page_host == "user.vnedu.vn" and parsed_page.path.startswith("/sso"):
            return 95
        if "vnedu" in page_host:
            return 85
        return 50

    def _pick_target_page(self, contexts) -> Page | None:
        """Selects the most suitable page from the live CDP session."""
        scored_pages: List[Tuple[int, int, Page]] = []
        ordinal = 0
        for context in contexts:
            for page in context.pages:
                try:
                    score = self._page_priority(page)
                except PlaywrightError:
                    continue
                if score >= 0:
                    scored_pages.append((score, ordinal, page))
                ordinal += 1
        if not scored_pages:
            return None
        scored_pages.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, _best_ordinal, best_page = scored_pages[0]
        if best_score >= 85:
            return best_page
        return None

    @contextmanager
    def _open_page(self) -> Iterator[Page]:
        """Connects to the live Chromium instance and yields one reusable working page."""
        self._ensure_cdp_server()
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(self._cdp_http_endpoint())
            except PlaywrightError as error:
                raise RuntimeError(
                    f"Kết nối CDP thất bại tại {self._cdp_http_endpoint()}."
                ) from error

            try:
                contexts = browser.contexts
                if not contexts:
                    raise RuntimeError("Không tìm thấy browser context trong phiên CDP.")

                target_page = self._pick_target_page(contexts)
                if target_page is None:
                    target_page = contexts[0].new_page()
                yield target_page
            finally:
                browser.close()

    def _goto_target_page(self, page: Page) -> None:
        """Navigates to the configured VNEDU URL when the current page differs."""
        if self.target_url and self.target_url not in (page.url or ""):
            page.goto(self.target_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(300)

    def _close_notice_popup(self, page: Page) -> None:
        """Dismisses VNEDU modal notices that block further clicks."""
        try:
            ok_button = page.locator("button:has-text('OK')")
            if ok_button.count() > 0 and ok_button.first.is_visible():
                ok_button.first.click(timeout=800)
                page.wait_for_timeout(200)
        except PlaywrightError:
            return

    def _find_username_input(self, page: Page):
        """Returns a likely username input on the current login form."""
        selectors = [
            "input[name*='user' i]",
            "input[id*='user' i]",
            "input[name*='login' i]",
            "input[id*='login' i]",
            "input[name*='account' i]",
            "input[id*='account' i]",
            "input[type='email']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator.first

        fallback = page.locator("input[type='text']")
        if fallback.count() > 0:
            return fallback.first
        raise RuntimeError("Không tìm thấy ô nhập tài khoản trên form đăng nhập.")

    def _login_if_needed_on_page(
        self,
        page: Page,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        """Logs in when a password field is visible."""
        emit_progress(progress_callback, 5.0, "Đang truy cập trang VNEDU...")
        self._goto_target_page(page)
        self._close_notice_popup(page)

        password_input = page.locator("input[type='password']")
        if password_input.count() == 0:
            emit_progress(progress_callback, 100.0, "Không cần đăng nhập lại, phiên đã sẵn sàng.")
            if username.strip() or password:
                return "Không phát hiện form đăng nhập; có thể phiên đã đăng nhập sẵn."
            return ""

        if not username.strip() or not password:
            raise RuntimeError(
                "Phiên hiện tại đang ở màn hình đăng nhập. Hãy nhập tài khoản và mật khẩu VNEDU."
            )

        username_input = self._find_username_input(page)
        emit_progress(progress_callback, 20.0, "Đang điền tài khoản và mật khẩu VNEDU...")
        username_input.fill(username.strip())
        password_input.first.fill(password)

        captcha_input = page.locator(
            "input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='captcha' i]"
        )
        if captcha_input.count() > 0 and captcha_input.first.is_visible():
            captcha_value = captcha_input.first.input_value().strip()
            if not captcha_value:
                try:
                    captcha_input.first.focus()
                except PlaywrightError:
                    pass
                raise RuntimeError(
                    "Trang đăng nhập VNEDU đang yêu cầu mã captcha. "
                    "App đã điền sẵn tài khoản và mật khẩu trên tab hiện tại; "
                    "hãy nhập captcha rồi bấm Đăng nhập thủ công, sau đó nhấn lại 'Đăng nhập + đọc Sổ điểm'."
                )

        emit_progress(progress_callback, 40.0, "Đang gửi yêu cầu đăng nhập...")
        clicked = page.evaluate(
            """() => {
            const candidates = Array.from(document.querySelectorAll('button, input[type="submit"]'));
            const target = candidates.find(el => {
                const text = (el.innerText || el.value || '').trim().toLowerCase();
                return text.includes('đăng nhập') || text.includes('dang nhap') || text.includes('login');
            });
            if (!target) return false;
            target.click();
            return true;
        }"""
        )
        if not clicked:
            password_input.first.press("Enter")

        deadline = time.time() + 20
        while time.time() < deadline:
            page.wait_for_timeout(250)
            self._close_notice_popup(page)
            elapsed_ratio = min((time.time() - (deadline - 20)) / 20.0, 1.0)
            emit_progress(
                progress_callback,
                40.0 + (elapsed_ratio * 55.0),
                "Đang chờ VNEDU xác thực đăng nhập...",
            )
            if page.locator("input[type='password']").count() == 0:
                emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")
                return "Đã gửi đăng nhập và xác thực thành công."

        raise RuntimeError("Đăng nhập chưa thành công. Vui lòng kiểm tra tài khoản hoặc xác thực bổ sung.")

    def _has_scorebook_controls(self, page: Page) -> bool:
        """Checks whether the active VNEDU window looks like the scorebook screen."""
        return bool(
            page.evaluate(
                """() => {
                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) return false;

                const labels = Array.from(root.querySelectorAll('label.x-form-item-label'))
                    .map(label => (label.innerText || '').toLowerCase());
                const required = ['khối', 'lớp', 'môn', 'học kỳ'];
                return required.every(token => labels.some(label => label.includes(token)));
            }"""
            )
        )

    def _find_scorebook_shortcut(self, page: Page, timeout_sec: float = 10.0) -> Dict[str, object]:
        """Finds the VNEDU desktop shortcut metadata for `Sổ điểm` with retry and debug details."""
        deadline = time.time() + max(timeout_sec, 1.0)
        last_snapshot: Dict[str, object] = {}
        while time.time() < deadline:
            self._close_notice_popup(page)
            last_snapshot = page.evaluate(
                """() => {
                const normalizeText = (value) => (value || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/đ/g, 'd')
                    .replace(/\\s+/g, ' ')
                    .trim();

                const serialize = (el) => ({
                    id: el.id || '',
                    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '),
                    className: el.className || '',
                });

                const desktopCandidates = Array.from(
                    document.querySelectorAll('.ux-desktop-shortcut, .x-view-item, [id$="-shortcut"]')
                );
                const visibleCandidates = desktopCandidates.filter(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
                const target = document.querySelector('#Sổ điểm-shortcut')
                    || document.querySelector('#So diem-shortcut')
                    || visibleCandidates.find(el => {
                        const haystacks = [
                            normalizeText(el.id),
                            normalizeText(el.innerText || el.textContent || ''),
                            normalizeText(el.getAttribute('title') || ''),
                        ];
                        return haystacks.some(text =>
                            text.includes('so diem') || text.includes('sodiem')
                        );
                    });
                if (!target) {
                    return {
                        found: false,
                        shortcutCount: visibleCandidates.length,
                        sampleShortcuts: visibleCandidates.slice(0, 12).map(serialize),
                    };
                }

                if (typeof target.scrollIntoView === 'function') {
                    target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                }
                const rect = target.getBoundingClientRect();
                return {
                    found: true,
                    shortcutCount: visibleCandidates.length,
                    sampleShortcuts: visibleCandidates.slice(0, 12).map(serialize),
                    id: target.id || '',
                    text: (target.innerText || target.textContent || '').trim(),
                    className: target.className || '',
                    centerX: rect.x + (rect.width / 2),
                    centerY: rect.y + (rect.height / 2),
                    width: rect.width,
                    height: rect.height,
                };
            }"""
            )
            if last_snapshot.get("found"):
                return last_snapshot
            page.wait_for_timeout(300)

        sample_labels = []
        for item in list(last_snapshot.get("sampleShortcuts", []))[:6]:
            if isinstance(item, dict):
                label = str(item.get("text", "")).strip() or str(item.get("id", "")).strip()
                if label:
                    sample_labels.append(label)
        observed = f"shortcut thấy được: {int(last_snapshot.get('shortcutCount', 0) or 0)}"
        if sample_labels:
            observed += f" | mẫu: {', '.join(sample_labels)}"
        raise RuntimeError(
            "Không tìm thấy shortcut Sổ điểm trên desktop VNEDU. "
            f"{observed}"
        )

    def _open_scorebook_from_desktop(
        self,
        page: Page,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Opens the scorebook window by double-clicking the live desktop shortcut."""
        emit_progress(progress_callback, 10.0, "Đang tìm shortcut Sổ điểm...")
        shortcut = self._find_scorebook_shortcut(page)
        clicked = False
        shortcut_id = str(shortcut.get("id", "")).strip()
        if shortcut_id:
            clicked = bool(
                page.evaluate(
                    """(shortcutId) => {
                    const target = document.getElementById(shortcutId);
                    if (!target) return false;
                    if (typeof target.scrollIntoView === 'function') {
                        target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                    }
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
                    return true;
                }""",
                    shortcut_id,
                )
            )
        if not clicked:
            page.mouse.dblclick(float(shortcut["centerX"]), float(shortcut["centerY"]))
        for _ in range(30):
            page.wait_for_timeout(400)
            self._close_notice_popup(page)
            elapsed_ratio = (_ + 1) / 30.0
            emit_progress(
                progress_callback,
                25.0 + (elapsed_ratio * 70.0),
                "Đang chờ cửa sổ Sổ điểm mở ra...",
            )
            if self._has_scorebook_controls(page):
                emit_progress(progress_callback, 100.0, "Đã mở cửa sổ Sổ điểm.")
                return
        raise RuntimeError("Đã bấm shortcut 'Sổ điểm' nhưng không mở được cửa sổ Sổ điểm.")

    def _ensure_scorebook_screen(
        self,
        page: Page,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Ensures the live VNEDU session ends up on the scorebook window."""
        emit_progress(progress_callback, 5.0, "Đang kiểm tra cửa sổ Sổ điểm...")
        self._goto_target_page(page)
        self._close_notice_popup(page)
        if self._has_scorebook_controls(page):
            emit_progress(progress_callback, 100.0, "Cửa sổ Sổ điểm đã sẵn sàng.")
            return
        emit_progress(progress_callback, 20.0, "Đang mở cửa sổ Sổ điểm...")
        self._open_scorebook_from_desktop(
            page,
            progress_callback=create_subprogress_reporter(progress_callback, 20.0, 100.0),
        )

    def _read_scorebook_window_shell(self, page: Page) -> Dict[str, object]:
        """Reads the active scorebook window identity, combo ids, and window-level badges."""
        return dict(
            page.evaluate(
                """() => {
                if (typeof Ext === 'undefined') {
                    return { ok: false, reason: 'ExtJS không tồn tại trên trang.' };
                }

                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) {
                    return { ok: false, reason: 'Không tìm thấy cửa sổ Sổ điểm đang hoạt động.' };
                }

                const labels = Array.from(root.querySelectorAll('label.x-form-item-label'));
                const findComboIdByLabel = (labelPart) => {
                    const label = labels.find(item => (item.innerText || '').toLowerCase().includes(labelPart));
                    const field = label ? label.closest('.x-field') : null;
                    return field ? (field.id || '') : '';
                };

                const teacherCell = root.querySelector('#gvbm');
                const permissionCell = Array.from(root.querySelectorAll('td'))
                    .find(td => /quyền hạn/i.test((td.innerText || '').trim()));
                const commentInputs = Array.from(root.querySelectorAll('input.input_nhan_xet'));

                return {
                    ok: true,
                    windowId: root.id || '',
                    windowTitle: ((root.querySelector('.x-window-header-text')?.innerText) || '').trim(),
                    gradeComboId: findComboIdByLabel('khối'),
                    classComboId: findComboIdByLabel('lớp'),
                    subjectComboId: findComboIdByLabel('môn'),
                    termComboId: findComboIdByLabel('học kỳ'),
                    teacherText: ((teacherCell?.innerText) || '').trim(),
                    permissionText: ((permissionCell?.innerText) || '').trim(),
                    commentInputCount: commentInputs.length,
                    enabledCommentInputCount: commentInputs.filter(input => !input.disabled && !input.readOnly).length,
                };
            }"""
            )
        )

    def _read_scorebook_combo_snapshot(self, page: Page, combo_id: str) -> Dict[str, object]:
        """Reads one scorebook combobox current value together with its current ExtJS store payload."""
        normalized_combo_id = combo_id.strip()
        if not normalized_combo_id:
            return {"id": "", "text": "", "options": []}

        return dict(
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') {
                    return { id: '', text: '', options: [] };
                }

                const readField = (record, fieldName) => {
                    if (!record) return '';
                    try {
                        if (record.get) {
                            const value = record.get(fieldName);
                            if (value !== undefined && value !== null) return value;
                        }
                    } catch (error) {}
                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {
                        return record.data[fieldName];
                    }
                    return '';
                };

                const collectStoreRecords = (source, bucket, visitedSources) => {
                    if (!source) return;
                    if (typeof source === 'object' || typeof source === 'function') {
                        if (visitedSources.has(source)) return;
                        visitedSources.add(source);
                    }
                    if (Array.isArray(source)) {
                        bucket.push(...source);
                        return;
                    }
                    if (typeof source.getRange === 'function') {
                        try {
                            const range = source.getRange();
                            if (Array.isArray(range)) {
                                bucket.push(...range);
                            }
                        } catch (error) {}
                    }
                    if (Array.isArray(source.items)) {
                        bucket.push(...source.items);
                    }
                    if (source.data) {
                        collectStoreRecords(source.data, bucket, visitedSources);
                    }
                    if (typeof source.getSource === 'function') {
                        try {
                            collectStoreRecords(source.getSource(), bucket, visitedSources);
                        } catch (error) {}
                    } else if (source.source) {
                        collectStoreRecords(source.source, bucket, visitedSources);
                    }
                };

                const readStoreRecords = (store) => {
                    if (!store) return [];
                    const bucket = [];
                    const visitedSources = new WeakSet();
                    collectStoreRecords(store.snapshot, bucket, visitedSources);
                    collectStoreRecords(store.data, bucket, visitedSources);
                    if (typeof store.getData === 'function') {
                        try {
                            collectStoreRecords(store.getData(), bucket, visitedSources);
                        } catch (error) {}
                    }
                    collectStoreRecords(store, bucket, visitedSources);

                    const deduped = [];
                    const seen = new Set();
                    for (const record of bucket) {
                        const recordId = String(readField(record, 'id')).trim();
                        const recordText = String(readField(record, 'ten') || readField(record, 'value')).trim();
                        const key = `${recordId}||${recordText}`;
                        if (!recordId && !recordText) continue;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        deduped.push(record);
                    }
                    return deduped;
                };

                const cmp = Ext.getCmp(comboId);
                if (!cmp) {
                    return { id: '', text: '', options: [] };
                }
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                const range = store ? readStoreRecords(store) : [];
                const currentValue = cmp.getValue ? cmp.getValue() : '';
                const currentText = cmp.getRawValue ? cmp.getRawValue() : '';
                const matchedRecord = range.find(record => {
                    const recordId = String(readField(record, 'id'));
                    const recordText = String(readField(record, 'ten') || readField(record, 'value'));
                    const normalizedValue = currentValue === undefined || currentValue === null ? '' : String(currentValue);
                    const normalizedText = currentText === undefined || currentText === null ? '' : String(currentText);
                    return recordId === normalizedValue || recordText === normalizedValue || recordText === normalizedText;
                });
                const normalizedId = matchedRecord
                    ? String(readField(matchedRecord, 'id'))
                    : (currentValue === undefined || currentValue === null ? '' : String(currentValue));
                const normalizedText = currentText === undefined || currentText === null ? '' : String(currentText);

                return {
                    id: normalizedId,
                    text: normalizedText || (matchedRecord ? String(readField(matchedRecord, 'ten') || readField(matchedRecord, 'value')) : ''),
                    options: range.map(record => ({
                        id: String(readField(record, 'id')),
                        ten: String(readField(record, 'ten') || readField(record, 'value')),
                    })),
                };
            }""",
                normalized_combo_id,
            )
        )

    def _read_scorebook_hidden_values(self, page: Page, window_id: str = "") -> Dict[str, str]:
        """Reads hidden scorebook form inputs from the active or specified scorebook window."""
        return dict(
            page.evaluate(
                """(windowId) => {
                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = (windowId && document.getElementById(windowId)) || active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) {
                    return {};
                }

                const form = root.querySelector('form');
                const hiddenValues = {};
                if (!form) {
                    return hiddenValues;
                }
                const hiddenInputs = Array.from(form.querySelectorAll('input[type="hidden"]'));
                for (const input of hiddenInputs) {
                    const key = (input.name || input.id || '').trim();
                    if (!key) continue;
                    hiddenValues[key] = String(input.value || '');
                }
                return hiddenValues;
            }""",
                window_id.strip(),
            )
        )

    def _read_scorebook_table_snapshot(self, page: Page, window_id: str = "") -> Dict[str, object]:
        """Reads the visible score table structure from the active or specified scorebook window."""
        return dict(
            page.evaluate(
                """(windowId) => {
                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = (windowId && document.getElementById(windowId)) || active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) {
                    return {
                        headerRows: [],
                        bodyRows: [],
                        rowCount: 0,
                        tableClass: '',
                    };
                }

                const scoreTable = root.querySelector('table.table.tablefix');
                if (!scoreTable) {
                    return {
                        headerRows: [],
                        bodyRows: [],
                        rowCount: 0,
                        tableClass: '',
                    };
                }

                const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const readInput = (input) => {
                    if (!input) return null;
                    return {
                        tagName: input.tagName || '',
                        type: input.type || '',
                        id: input.id || '',
                        name: input.name || '',
                        className: input.className || '',
                        value: input.value || '',
                        b: input.getAttribute('b') || '',
                        c: input.getAttribute('c') || '',
                        bc: input.getAttribute('bc') || '',
                    };
                };

                const headerRows = Array.from(scoreTable.querySelectorAll('thead tr')).map((tr, rowIndex) => ({
                    rowIndex,
                    cells: Array.from(tr.querySelectorAll('td, th')).map((cell, cellIndex) => ({
                        cellIndex,
                        text: normalizeText(cell.innerText || cell.textContent || ''),
                        className: cell.className || '',
                        colspan: parseInt(cell.getAttribute('colspan') || '1', 10) || 1,
                        rowspan: parseInt(cell.getAttribute('rowspan') || '1', 10) || 1,
                        cn: cell.getAttribute('cn') || '',
                        cl: cell.getAttribute('cl') || '',
                        b: cell.getAttribute('b') || '',
                        c: cell.getAttribute('c') || '',
                    })),
                }));

                const bodyRows = Array.from(scoreTable.querySelectorAll('tbody tr')).slice(0, 5).map((tr, rowIndex) => ({
                    rowIndex,
                    rowId: tr.id || '',
                    cells: Array.from(tr.children).map((cell, cellIndex) => ({
                        cellIndex,
                        tagName: cell.tagName || '',
                        text: normalizeText(cell.innerText || cell.textContent || ''),
                        className: cell.className || '',
                        dataLoai: cell.getAttribute('data-loai') || '',
                        dataCot: cell.getAttribute('data-cot') || '',
                        dataHang: cell.getAttribute('data-hang') || '',
                        inputInfo: readInput(cell.querySelector('input, textarea, select')),
                    })),
                }));

                return {
                    headerRows,
                    bodyRows,
                    rowCount: scoreTable.querySelectorAll('tbody tr').length,
                    tableClass: scoreTable.className || '',
                };
            }""",
                window_id.strip(),
            )
        )

    def _scorebook_snapshot(self, page: Page) -> Dict[str, object]:
        """Reads scorebook combobox ids, current values, and ExtJS store items."""
        shell_snapshot = self._read_scorebook_window_shell(page)
        if not shell_snapshot.get("ok"):
            raise RuntimeError(str(shell_snapshot.get("reason", "Không đọc được cấu trúc Sổ điểm.")))

        window_id = str(shell_snapshot.get("windowId", "")).strip()
        hidden_values = self._read_scorebook_hidden_values(page, window_id=window_id)
        score_table_info = self._read_scorebook_table_snapshot(page, window_id=window_id)

        grade_combo_id = str(shell_snapshot.get("gradeComboId", "")).strip()
        class_combo_id = str(shell_snapshot.get("classComboId", "")).strip()
        subject_combo_id = str(shell_snapshot.get("subjectComboId", "")).strip()
        term_combo_id = str(shell_snapshot.get("termComboId", "")).strip()

        grade_snapshot = self._read_scorebook_combo_snapshot(page, grade_combo_id)
        class_snapshot = self._read_scorebook_combo_snapshot(page, class_combo_id)
        subject_snapshot = self._read_scorebook_combo_snapshot(page, subject_combo_id)
        term_snapshot = self._read_scorebook_combo_snapshot(page, term_combo_id)

        return {
            "ok": True,
            "windowId": window_id,
            "windowTitle": str(shell_snapshot.get("windowTitle", "")).strip(),
            "gradeComboId": grade_combo_id,
            "classComboId": class_combo_id,
            "subjectComboId": subject_combo_id,
            "termComboId": term_combo_id,
            "currentGradeId": str(grade_snapshot.get("id", "")).strip(),
            "currentGradeText": str(grade_snapshot.get("text", "")).strip(),
            "currentClassId": str(class_snapshot.get("id", "")).strip(),
            "currentClassText": str(class_snapshot.get("text", "")).strip(),
            "currentSubjectId": str(subject_snapshot.get("id", "")).strip(),
            "currentSubjectText": str(subject_snapshot.get("text", "")).strip(),
            "currentTermId": str(term_snapshot.get("id", "")).strip(),
            "currentTermText": str(term_snapshot.get("text", "")).strip(),
            "teacherText": str(shell_snapshot.get("teacherText", "")).strip(),
            "permissionText": str(shell_snapshot.get("permissionText", "")).strip(),
            "commentInputCount": int(shell_snapshot.get("commentInputCount", 0) or 0),
            "enabledCommentInputCount": int(shell_snapshot.get("enabledCommentInputCount", 0) or 0),
            "hiddenSchoolYear": str(hidden_values.get("iNamHoc", "")).strip(),
            "hiddenGradeId": str(hidden_values.get("iKhoi", "")).strip(),
            "hiddenClassId": str(hidden_values.get("iLopId", "")).strip(),
            "hiddenSubjectId": str(hidden_values.get("iMonHocId", "")).strip(),
            "hiddenTermId": str(hidden_values.get("iHocKy", "")).strip(),
            "scoreTableClass": str(score_table_info.get("tableClass", "")).strip(),
            "scoreTableRowCount": int(score_table_info.get("rowCount", 0) or 0),
            "scoreTableHeaderRows": list(score_table_info.get("headerRows", [])),
            "scoreTableBodyRows": list(score_table_info.get("bodyRows", [])),
            "gradeOptions": list(grade_snapshot.get("options", [])),
            "classOptions": list(class_snapshot.get("options", [])),
            "subjectOptions": list(subject_snapshot.get("options", [])),
            "termOptions": list(term_snapshot.get("options", [])),
        }

    def _read_combo_store_items(self, page: Page, combo_id: str) -> List[Dict[str, object]]:
        """Reads the current ExtJS store items for one scorebook combobox."""
        normalized_combo_id = combo_id.strip()
        if not normalized_combo_id:
            return []
        try:
            raw_items = page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return [];
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return [];
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                if (!store) return [];

                const readField = (record, fieldName) => {
                    if (!record) return '';
                    try {
                        if (record.get) {
                            const value = record.get(fieldName);
                            if (value !== undefined && value !== null) return value;
                        }
                    } catch (error) {}
                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {
                        return record.data[fieldName];
                    }
                    return '';
                };

                const collectStoreRecords = (source, bucket, visitedSources) => {
                    if (!source || typeof source !== 'object') {
                        return;
                    }
                    if (visitedSources.has(source)) {
                        return;
                    }
                    visitedSources.add(source);
                    if (typeof source.getRange === 'function') {
                        try {
                            const range = source.getRange();
                            if (Array.isArray(range)) {
                                bucket.push(...range);
                            }
                        } catch (error) {}
                    }
                    if (Array.isArray(source.items)) {
                        bucket.push(...source.items);
                    }
                    if (source.data) {
                        collectStoreRecords(source.data, bucket, visitedSources);
                    }
                    if (typeof source.getSource === 'function') {
                        try {
                            collectStoreRecords(source.getSource(), bucket, visitedSources);
                        } catch (error) {}
                    } else if (source.source) {
                        collectStoreRecords(source.source, bucket, visitedSources);
                    }
                };

                const bucket = [];
                const visitedSources = new WeakSet();
                collectStoreRecords(store.snapshot, bucket, visitedSources);
                collectStoreRecords(store.data, bucket, visitedSources);
                if (typeof store.getData === 'function') {
                    try {
                        collectStoreRecords(store.getData(), bucket, visitedSources);
                    } catch (error) {}
                }
                collectStoreRecords(store, bucket, visitedSources);

                const deduped = [];
                const seen = new Set();
                for (const record of bucket) {
                    const recordId = String(readField(record, 'id')).trim();
                    const recordText = String(readField(record, 'ten') || readField(record, 'value')).trim();
                    const key = `${recordId}||${recordText}`;
                    if (!recordId && !recordText) continue;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    deduped.push({
                        id: recordId,
                        ten: recordText,
                    });
                }
                return deduped;
            }""",
                normalized_combo_id,
            )
        except PlaywrightError:
            return []
        return list(raw_items or [])

    def _load_live_combo_options(self, page: Page, combo_id: str) -> List[Dict[str, object]]:
        """Expands one combo so dependent ExtJS stores can populate before the app reads them."""
        normalized_combo_id = combo_id.strip()
        if not normalized_combo_id:
            return []

        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try {
                    if (cmp.onTriggerClick) {
                        cmp.onTriggerClick();
                    } else if (cmp.expand) {
                        cmp.expand();
                    }
                    return true;
                } catch (error) {
                    return false;
                }
            }""",
                normalized_combo_id,
            )
        except PlaywrightError:
            return self._read_combo_store_items(page, normalized_combo_id)

        best_items = self._read_combo_store_items(page, normalized_combo_id)
        last_signature: Tuple[Tuple[str, str], ...] = tuple()
        stable_reads = 0
        deadline = time.time() + 2.5
        while time.time() < deadline:
            page.wait_for_timeout(180)
            current_items = self._read_combo_store_items(page, normalized_combo_id)
            if len(current_items) > len(best_items):
                best_items = current_items
            current_signature = tuple(
                (
                    str(item.get("id", "")).strip(),
                    str(item.get("ten", "")).strip(),
                )
                for item in current_items
            )
            if current_signature and current_signature == last_signature:
                stable_reads += 1
                if stable_reads >= 1:
                    if current_items:
                        best_items = current_items
                    break
            else:
                stable_reads = 0
            last_signature = current_signature

        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try {
                    if (cmp.collapse) cmp.collapse();
                    return true;
                } catch (error) {
                    return false;
                }
            }""",
                normalized_combo_id,
            )
        except PlaywrightError:
            pass

        return best_items

    def _hydrate_scorebook_snapshot_options(
        self,
        page: Page,
        snapshot: Dict[str, object],
        *,
        include_grade: bool = True,
        include_class: bool = True,
        include_subject: bool = True,
        include_term: bool = True,
    ) -> Dict[str, object]:
        """Refreshes combo option payloads so lazily loaded stores are not mistaken for single-value lists."""
        updated_snapshot = dict(snapshot)
        combo_specs = []
        if include_grade:
            combo_specs.append(("gradeComboId", "gradeOptions", "currentGradeId", "currentGradeText", "hiddenGradeId"))
        if include_class:
            combo_specs.append(("classComboId", "classOptions", "currentClassId", "currentClassText", "hiddenClassId"))
        if include_subject:
            combo_specs.append(("subjectComboId", "subjectOptions", "currentSubjectId", "currentSubjectText", "hiddenSubjectId"))
        if include_term:
            combo_specs.append(("termComboId", "termOptions", "currentTermId", "currentTermText", "hiddenTermId"))

        for combo_id_key, options_key, current_key, current_text_key, hidden_key in combo_specs:
            combo_id = str(updated_snapshot.get(combo_id_key, "")).strip()
            if not combo_id:
                continue
            existing_options = self._build_options(list(updated_snapshot.get(options_key, [])))
            live_options = self._build_options(self._load_live_combo_options(page, combo_id))
            merged_options = ensure_selected_score_option(
                merge_score_options(existing_options, live_options),
                self._effective_snapshot_selected_id(updated_snapshot, current_key, hidden_key),
                str(updated_snapshot.get(current_text_key, "")).strip(),
            )
            updated_snapshot[options_key] = [
                {
                    "id": option.option_id,
                    "ten": option.option_text,
                }
                for option in merged_options
            ]

        return updated_snapshot

    def _read_combo_target_state(self, page: Page, combo_id: str, value_id: str) -> Dict[str, object]:
        """Reads the selectors and target text needed to select one ExtJS combobox option."""
        return dict(
            page.evaluate(
                """({ comboId, valueId }) => {
                if (typeof Ext === 'undefined') return { ok: false, reason: 'ExtJS không tồn tại trên trang.' };
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return { ok: false, reason: `Không tìm thấy combobox ${comboId}.` };
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                if (!store) return { ok: false, reason: `Combobox ${comboId} không có store.` };

                const readField = (record, fieldName) => {
                    if (!record) return '';
                    try {
                        if (record.get) {
                            const value = record.get(fieldName);
                            if (value !== undefined && value !== null) return value;
                        }
                    } catch (error) {}
                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {
                        return record.data[fieldName];
                    }
                    return '';
                };

                const collectStoreRecords = (source, bucket, visitedSources) => {
                    if (!source) return;
                    if (typeof source === 'object' || typeof source === 'function') {
                        if (visitedSources.has(source)) return;
                        visitedSources.add(source);
                    }
                    if (Array.isArray(source)) {
                        bucket.push(...source);
                        return;
                    }
                    if (typeof source.getRange === 'function') {
                        try {
                            const range = source.getRange();
                            if (Array.isArray(range)) {
                                bucket.push(...range);
                            }
                        } catch (error) {}
                    }
                    if (Array.isArray(source.items)) {
                        bucket.push(...source.items);
                    }
                    if (source.data) {
                        collectStoreRecords(source.data, bucket, visitedSources);
                    }
                    if (typeof source.getSource === 'function') {
                        try {
                            collectStoreRecords(source.getSource(), bucket, visitedSources);
                        } catch (error) {}
                    } else if (source.source) {
                        collectStoreRecords(source.source, bucket, visitedSources);
                    }
                };

                const readStoreRecords = (currentStore) => {
                    if (!currentStore) return [];
                    const bucket = [];
                    const visitedSources = new WeakSet();
                    collectStoreRecords(currentStore.snapshot, bucket, visitedSources);
                    collectStoreRecords(currentStore.data, bucket, visitedSources);
                    if (typeof currentStore.getData === 'function') {
                        try {
                            collectStoreRecords(currentStore.getData(), bucket, visitedSources);
                        } catch (error) {}
                    }
                    collectStoreRecords(currentStore, bucket, visitedSources);

                    const deduped = [];
                    const seen = new Set();
                    for (const record of bucket) {
                        const recordId = String(readField(record, 'id')).trim();
                        const recordText = String(readField(record, 'ten') || readField(record, 'value')).trim();
                        const key = `${recordId}||${recordText}`;
                        if (!recordId && !recordText) continue;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        deduped.push(record);
                    }
                    return deduped;
                };

                const range = readStoreRecords(store);
                const target = range.find(record => String(readField(record, 'id')) === valueId);
                if (!target) {
                    return { ok: false, reason: `Không tìm thấy option id=${valueId} trong combobox ${comboId}.` };
                }

                const field = document.getElementById(comboId);
                const trigger = field?.querySelector('.x-form-arrow-trigger, .x-form-trigger');
                const input = field?.querySelector('input.x-form-field, input[role="textbox"]');
                const targetText = String(readField(target, 'ten') || readField(target, 'value') || valueId).trim();
                const currentValue = cmp.getValue ? cmp.getValue() : '';
                const currentText = cmp.getRawValue ? cmp.getRawValue() : '';

                return {
                    ok: true,
                    comboId,
                    valueId,
                    targetText,
                    currentValue: currentValue === undefined || currentValue === null ? '' : String(currentValue),
                    currentText: currentText === undefined || currentText === null ? '' : String(currentText),
                    fieldSelector: field?.id ? `#${CSS.escape(field.id)}` : '',
                    triggerSelector: trigger?.id ? `#${CSS.escape(trigger.id)}` : '',
                    inputSelector: input?.id ? `#${CSS.escape(input.id)}` : '',
                };
            }""",
                {"comboId": combo_id, "valueId": value_id},
            )
        )

    def _select_combo_via_extjs_fallback(self, page: Page, combo_id: str, value_id: str) -> bool:
        """Falls back to direct ExtJS mutation when the real picker path is unavailable."""
        return bool(
            page.evaluate(
                """({ comboId, valueId }) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                if (!store) return false;

                const readField = (record, fieldName) => {
                    if (!record) return '';
                    try {
                        if (record.get) {
                            const value = record.get(fieldName);
                            if (value !== undefined && value !== null) return value;
                        }
                    } catch (error) {}
                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {
                        return record.data[fieldName];
                    }
                    return '';
                };

                const collectStoreRecords = (source, bucket, visitedSources) => {
                    if (!source) return;
                    if (typeof source === 'object' || typeof source === 'function') {
                        if (visitedSources.has(source)) return;
                        visitedSources.add(source);
                    }
                    if (Array.isArray(source)) {
                        bucket.push(...source);
                        return;
                    }
                    if (typeof source.getRange === 'function') {
                        try {
                            const range = source.getRange();
                            if (Array.isArray(range)) {
                                bucket.push(...range);
                            }
                        } catch (error) {}
                    }
                    if (Array.isArray(source.items)) {
                        bucket.push(...source.items);
                    }
                    if (source.data) {
                        collectStoreRecords(source.data, bucket, visitedSources);
                    }
                    if (typeof source.getSource === 'function') {
                        try {
                            collectStoreRecords(source.getSource(), bucket, visitedSources);
                        } catch (error) {}
                    } else if (source.source) {
                        collectStoreRecords(source.source, bucket, visitedSources);
                    }
                };

                const readStoreRecords = (currentStore) => {
                    if (!currentStore) return [];
                    const bucket = [];
                    const visitedSources = new WeakSet();
                    collectStoreRecords(currentStore.snapshot, bucket, visitedSources);
                    collectStoreRecords(currentStore.data, bucket, visitedSources);
                    if (typeof currentStore.getData === 'function') {
                        try {
                            collectStoreRecords(currentStore.getData(), bucket, visitedSources);
                        } catch (error) {}
                    }
                    collectStoreRecords(currentStore, bucket, visitedSources);

                    const deduped = [];
                    const seen = new Set();
                    for (const record of bucket) {
                        const recordId = String(readField(record, 'id')).trim();
                        const recordText = String(readField(record, 'ten') || readField(record, 'value')).trim();
                        const key = `${recordId}||${recordText}`;
                        if (!recordId && !recordText) continue;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        deduped.push(record);
                    }
                    return deduped;
                };

                const target = readStoreRecords(store).find(record => String(readField(record, 'id')) === valueId);
                if (!target) return false;

                const targetText = String(readField(target, 'ten') || readField(target, 'value') || valueId).trim();
                try {
                    if (typeof store.clearFilter === 'function') {
                        store.clearFilter();
                    }
                } catch (error) {}
                try {
                    if (cmp.select) {
                        cmp.select(target, true);
                    }
                } catch (error) {}
                try {
                    if (cmp.setValue) {
                        cmp.setValue(valueId);
                    }
                } catch (error) {}
                try {
                    if (cmp.setRawValue) {
                        cmp.setRawValue(targetText);
                    }
                } catch (error) {}

                const field = document.getElementById(comboId);
                const input = (cmp.inputEl && cmp.inputEl.dom)
                    ? cmp.inputEl.dom
                    : field?.querySelector('input.x-form-field, input[role="textbox"]');

                if (input) {
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }

                try {
                    if (cmp.collapse) {
                        cmp.collapse();
                    }
                } catch (error) {}

                const currentValue = cmp.getValue ? cmp.getValue() : '';
                return String(currentValue === undefined || currentValue === null ? '' : currentValue) === valueId;
            }""",
                {"comboId": combo_id, "valueId": value_id},
            )
        )

    def _select_combo_via_picker_click(self, page: Page, combo_state: Dict[str, object]) -> bool:
        """Uses the visible ExtJS picker to select the target option when the trigger is available."""
        target_text = str(combo_state.get("targetText", "")).strip()
        if not target_text:
            return False

        item_pattern = re.compile(rf"^\s*{re.escape(target_text)}\s*$")
        trigger_selector = str(combo_state.get("triggerSelector", "")).strip()
        input_selector = str(combo_state.get("inputSelector", "")).strip()
        field_selector = str(combo_state.get("fieldSelector", "")).strip()

        trigger_locator = None
        if trigger_selector:
            trigger_locator = page.locator(trigger_selector)
        elif input_selector:
            trigger_locator = page.locator(input_selector)
        elif field_selector:
            trigger_locator = page.locator(field_selector)
        else:
            return False

        try:
            trigger_locator.click(timeout=2500)
        except PlaywrightError:
            try:
                trigger_locator.click(timeout=2500, force=True)
            except PlaywrightError:
                return False

        picker_items = page.locator(".x-boundlist.x-layer:visible .x-boundlist-item")
        try:
            picker_items.first.wait_for(state="visible", timeout=2500)
        except PlaywrightError:
            return False

        target_item = picker_items.filter(has_text=item_pattern).first
        try:
            target_item.wait_for(state="visible", timeout=2500)
            target_item.click(timeout=2500)
        except PlaywrightError:
            try:
                target_item.click(timeout=2500, force=True)
            except PlaywrightError:
                return False

        page.wait_for_timeout(150)
        return True

    def _set_combo_value(self, page: Page, combo_id: str, value_id: str) -> bool:
        """Selects one ExtJS combobox value via a real picker click instead of mutating combo internals."""
        combo_id = combo_id.strip()
        value_id = value_id.strip()
        if not combo_id or not value_id:
            return False

        combo_state = self._read_combo_target_state(page, combo_id, value_id)
        if not combo_state.get("ok"):
            return False

        if self._select_combo_via_picker_click(page, combo_state):
            return True
        return self._select_combo_via_extjs_fallback(page, combo_id, value_id)

    def _build_scorebook_context(self, snapshot: Dict[str, object]) -> ScorebookContext:
        """Builds a typed scorebook context from one raw ExtJS snapshot."""
        column_schemas = self._extract_scorebook_schema_from_snapshot(snapshot)
        selected_grade_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentGradeId",
            hidden_key="hiddenGradeId",
        )
        selected_class_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentClassId",
            hidden_key="hiddenClassId",
        )
        selected_subject_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentSubjectId",
            hidden_key="hiddenSubjectId",
        )
        selected_term_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentTermId",
            hidden_key="hiddenTermId",
        )
        return ScorebookContext(
            grade_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("gradeOptions", []))),
                selected_grade_id,
                str(snapshot.get("currentGradeText", "")).strip(),
            ),
            selected_grade_id=selected_grade_id,
            class_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("classOptions", []))),
                selected_class_id,
                str(snapshot.get("currentClassText", "")).strip(),
            ),
            selected_class_id=selected_class_id,
            subject_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("subjectOptions", []))),
                selected_subject_id,
                str(snapshot.get("currentSubjectText", "")).strip(),
            ),
            selected_subject_id=selected_subject_id,
            term_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("termOptions", []))),
                selected_term_id,
                str(snapshot.get("currentTermText", "")).strip(),
            ),
            selected_term_id=selected_term_id,
            window_id=str(snapshot.get("windowId", "")).strip(),
            window_title=str(snapshot.get("windowTitle", "")).strip(),
            teacher_text=str(snapshot.get("teacherText", "")).strip(),
            permission_text=str(snapshot.get("permissionText", "")).strip(),
            comment_input_count=int(snapshot.get("commentInputCount", 0) or 0),
            enabled_comment_input_count=int(snapshot.get("enabledCommentInputCount", 0) or 0),
            column_schemas=column_schemas,
            detected_columns=self._detect_scorebook_columns(column_schemas),
        )

    def _effective_snapshot_selected_id(
        self,
        snapshot: Dict[str, object],
        current_key: str,
        hidden_key: str,
    ) -> str:
        """Returns the most reliable selected id from one raw scorebook snapshot."""
        hidden_value = str(snapshot.get(hidden_key, "")).strip()
        if hidden_value:
            return hidden_value
        return str(snapshot.get(current_key, "")).strip()

    def _requested_scorebook_context_differs(
        self,
        snapshot: Dict[str, object],
        *,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
    ) -> bool:
        """Returns whether one requested scorebook context needs live combo synchronization."""
        requested_specs = (
            (grade_id, "currentGradeId", "hiddenGradeId"),
            (class_id, "currentClassId", "hiddenClassId"),
            (subject_id, "currentSubjectId", "hiddenSubjectId"),
            (term_id, "currentTermId", "hiddenTermId"),
        )
        for requested_id, current_key, hidden_key in requested_specs:
            normalized_requested_id = requested_id.strip()
            if not normalized_requested_id:
                continue
            if normalized_requested_id != self._effective_snapshot_selected_id(snapshot, current_key, hidden_key):
                return True
        return False

    def _build_options(self, items: List[Dict[str, object]]) -> List[ScoreOption]:
        """Converts raw ExtJS store items into typed options."""
        options: List[ScoreOption] = []
        seen_ids = set()
        for item in items:
            option_id = str(item.get("id", "")).strip()
            option_text = str(item.get("ten", "")).strip() or option_id
            if not option_id or option_id in seen_ids:
                continue
            seen_ids.add(option_id)
            options.append(ScoreOption(option_id=option_id, option_text=option_text))
        return options

    def _build_access_entries(self, items: List[Dict[str, object]]) -> List[ScorebookAccessEntry]:
        """Converts raw permission scan items into typed access entries."""
        entries: List[ScorebookAccessEntry] = []
        seen_keys = set()
        for item in items:
            class_id = str(item.get("classId", "")).strip()
            subject_id = str(item.get("subjectId", "")).strip()
            if not class_id or not subject_id:
                continue
            key = (class_id, subject_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append(
                ScorebookAccessEntry(
                    grade_id=str(item.get("gradeId", "")).strip(),
                    grade_text=str(item.get("gradeText", "")).strip(),
                    class_id=class_id,
                    class_text=str(item.get("classText", "")).strip(),
                    subject_id=subject_id,
                    subject_text=str(item.get("subjectText", "")).strip(),
                    term_id=str(item.get("termId", "")).strip(),
                    term_text=str(item.get("termText", "")).strip(),
                    teacher_text=str(item.get("teacherText", "")).strip(),
                    permission_text=str(item.get("permissionText", "")).strip(),
                    comment_input_count=int(item.get("commentInputCount", 0) or 0),
                    enabled_comment_input_count=int(item.get("enabledCommentInputCount", 0) or 0),
                )
            )
        return entries

    def _finalize_schema_identity(self, schemas: List[ScoreColumnSchema]) -> List[ScoreColumnSchema]:
        """Ensures repeated schema names and keys remain unique and stable."""
        name_counts: Dict[str, int] = {}
        name_totals: Dict[str, int] = {}
        key_counts: Dict[str, int] = {}
        key_totals: Dict[str, int] = {}
        for schema in schemas:
            base_name = schema.display_name.strip() or f"Cột {schema.leaf_index}"
            name_totals[base_name] = name_totals.get(base_name, 0) + 1
            base_key = schema.column_key.strip() or f"col_{schema.leaf_index}"
            key_totals[base_key] = key_totals.get(base_key, 0) + 1

        updated: List[ScoreColumnSchema] = []
        for schema in schemas:
            base_name = schema.display_name.strip() or f"Cột {schema.leaf_index}"
            name_counts[base_name] = name_counts.get(base_name, 0) + 1
            if name_totals.get(base_name, 0) > 1:
                schema.display_name = f"{base_name} ({name_counts[base_name]})"
            else:
                schema.display_name = base_name

            base_key = schema.column_key.strip() or f"col_{schema.leaf_index}"
            key_counts[base_key] = key_counts.get(base_key, 0) + 1
            if key_totals.get(base_key, 0) > 1:
                schema.column_key = f"{base_key}_{key_counts[base_key]}"
            else:
                schema.column_key = base_key
            updated.append(schema)
        return updated

    def _scorebook_leaf_count(self, body_rows: List[Dict[str, object]]) -> int:
        """Returns the maximum number of visible leaf cells across sampled score rows."""
        return max((len(list(row.get("cells", []))) for row in body_rows), default=0)

    def _build_scorebook_header_grids(
        self,
        header_rows: List[Dict[str, object]],
        leaf_count: int,
    ) -> Tuple[List[List[str]], List[List[Dict[str, object] | None]]]:
        """Expands scorebook header rowspans/colspans into one leaf-aligned grid."""
        header_grid: List[List[str]] = [["" for _ in range(leaf_count)] for _ in range(len(header_rows))]
        header_meta_grid: List[List[Dict[str, object] | None]] = [
            [None for _ in range(leaf_count)] for _ in range(len(header_rows))
        ]

        for row_index, row in enumerate(header_rows):
            col_pos = 0
            for cell in list(row.get("cells", [])):
                while col_pos < leaf_count and header_meta_grid[row_index][col_pos] is not None:
                    col_pos += 1
                colspan = max(1, int(cell.get("colspan", 1) or 1))
                rowspan = max(1, int(cell.get("rowspan", 1) or 1))
                label = (
                    str(cell.get("text", "")).strip()
                    or str(cell.get("cn", "")).strip()
                    or str(cell.get("cl", "")).strip()
                )
                for row_offset in range(rowspan):
                    target_row = row_index + row_offset
                    if target_row >= len(header_rows):
                        break
                    for col_offset in range(colspan):
                        target_col = col_pos + col_offset
                        if target_col >= leaf_count:
                            break
                        header_grid[target_row][target_col] = label
                        header_meta_grid[target_row][target_col] = dict(cell)
                col_pos += colspan

        return header_grid, header_meta_grid

    def _sample_scorebook_leaf_cells(
        self,
        body_rows: List[Dict[str, object]],
        leaf_index: int,
    ) -> List[Dict[str, object]]:
        """Returns the sampled body cells for one leaf column."""
        return [
            row["cells"][leaf_index]
            for row in body_rows
            if leaf_index < len(list(row.get("cells", [])))
        ]

    def _deduped_scorebook_header_path(
        self,
        header_grid: List[List[str]],
        leaf_index: int,
    ) -> Tuple[str, ...]:
        """Builds the de-duplicated header label path for one leaf column."""
        header_path = tuple(
            label
            for label in (
                header_grid[row_index][leaf_index].strip()
                for row_index in range(len(header_grid))
            )
            if label
        )
        deduped_header_path: List[str] = []
        for label in header_path:
            if not deduped_header_path or deduped_header_path[-1] != label:
                deduped_header_path.append(label)
        return tuple(deduped_header_path)

    def _deepest_scorebook_header_meta(
        self,
        header_meta_grid: List[List[Dict[str, object] | None]],
        leaf_index: int,
    ) -> Dict[str, object]:
        """Returns the deepest non-empty header metadata cell for one leaf column."""
        return next(
            (
                header_meta_grid[row_index][leaf_index]
                for row_index in range(len(header_meta_grid) - 1, -1, -1)
                if header_meta_grid[row_index][leaf_index] is not None
            ),
            None,
        ) or {}

    def _infer_scorebook_column_identity(
        self,
        *,
        input_class: str,
        td_class: str,
        normalized_header: str,
        data_column: str,
        cn_value: str,
        block_index: str,
        child_index: str,
        header_label: str,
        leaf_index: int,
    ) -> Tuple[str, str, str]:
        """Infers the semantic role, input kind, and stable key for one scorebook column."""
        role_hint = "static"
        input_kind = ""
        column_key = f"col_{leaf_index}"

        if "input_nhan_xet" in input_class:
            return "comment", "comment", "comment"
        if "input_diem_tbm" in input_class or "tbhk_tt" in td_class.lower():
            return "average", "score", "average_term"
        if "input_diem" in input_class or "txtdiem" in input_class.lower():
            score_suffix = data_column or cn_value or f"{block_index}_{child_index}"
            return "score", "score", f"score_{score_suffix}".strip("_")
        if any(
            token in normalized_header
            for token in (
                "đtbmhk",
                "tbhk",
                "tbhk 1",
                "tbhk 2",
                "tb cả năm",
                "tb ca nam",
                "trung bình học kỳ",
                "trung binh hoc ky",
                "trung bình cả năm",
                "trung binh ca nam",
            )
        ):
            static_average_key = data_column or cn_value or header_label or f"average_{leaf_index}"
            return (
                "average",
                "score",
                f"average_{re.sub(r'[^a-z0-9]+', '_', static_average_key.lower()).strip('_') or leaf_index}",
            )
        if any(
            token in normalized_header
            for token in (
                "điểm thi lại",
                "diem thi lai",
                "thi lại",
                "thi lai",
            )
        ):
            static_score_key = data_column or cn_value or header_label or f"score_{leaf_index}"
            return (
                "score",
                "score",
                f"score_{re.sub(r'[^a-z0-9]+', '_', static_score_key.lower()).strip('_') or leaf_index}",
            )
        if "mã hs" in normalized_header:
            return "student_code", input_kind, "student_code"
        if "họ và tên" in normalized_header:
            return "student_name", input_kind, "student_name"
        if "ngày sinh" in normalized_header:
            return "birth_date", input_kind, "birth_date"
        if "liên lạc" in normalized_header:
            return "contact", input_kind, "contact"
        if "stt" in normalized_header:
            return "ordinal", input_kind, "ordinal"
        return role_hint, input_kind, column_key

    def _scorebook_column_display_name(
        self,
        role_hint: str,
        data_column: str,
        header_label: str,
        cn_value: str,
    ) -> str:
        """Resolves the UI display name for one inferred scorebook schema column."""
        if role_hint == "score" and cn_value and data_column:
            return f"{cn_value} / {data_column}"
        if role_hint == "score" and cn_value:
            return cn_value
        return data_column or header_label

    def _build_scorebook_schema_for_leaf(
        self,
        body_rows: List[Dict[str, object]],
        header_grid: List[List[str]],
        header_meta_grid: List[List[Dict[str, object] | None]],
        leaf_index: int,
    ) -> ScoreColumnSchema:
        """Builds one typed schema object for a single leaf column."""
        sample_cells = self._sample_scorebook_leaf_cells(body_rows, leaf_index)
        first_nonempty_cell = next(
            (
                cell
                for cell in sample_cells
                if str(cell.get("text", "")).strip() or cell.get("inputInfo")
            ),
            sample_cells[0] if sample_cells else {},
        )
        input_info = next((cell.get("inputInfo") for cell in sample_cells if cell.get("inputInfo")), None) or {}
        header_path = self._deduped_scorebook_header_path(header_grid, leaf_index)
        deepest_meta = self._deepest_scorebook_header_meta(header_meta_grid, leaf_index)

        td_class = str(first_nonempty_cell.get("className", "")).strip()
        input_class = str(input_info.get("className", "")).strip()
        header_label = header_path[-1] if header_path else f"Cột {leaf_index}"
        sample_value = str(input_info.get("value", "")).strip() or str(first_nonempty_cell.get("text", "")).strip()
        block_index = str(input_info.get("b", "")).strip() or str(deepest_meta.get("b", "")).strip()
        child_index = str(input_info.get("c", "")).strip() or str(deepest_meta.get("c", "")).strip()
        data_column = str(first_nonempty_cell.get("dataCot", "")).strip() or str(deepest_meta.get("cl", "")).strip()
        cn_value = str(deepest_meta.get("cn", "")).strip()
        normalized_header = " ".join(header_path).lower()
        editable = any(bool(cell.get("inputInfo")) for cell in sample_cells)
        role_hint, input_kind, column_key = self._infer_scorebook_column_identity(
            input_class=input_class,
            td_class=td_class,
            normalized_header=normalized_header,
            data_column=data_column,
            cn_value=cn_value,
            block_index=block_index,
            child_index=child_index,
            header_label=header_label,
            leaf_index=leaf_index,
        )
        display_name = self._scorebook_column_display_name(
            role_hint,
            data_column=data_column,
            header_label=header_label,
            cn_value=cn_value,
        )

        return ScoreColumnSchema(
            column_key=column_key,
            header_path=header_path,
            leaf_index=leaf_index,
            display_name=display_name,
            editable=editable,
            role_hint=role_hint,
            input_kind=input_kind,
            sample_value=sample_value,
            block_index=block_index,
            child_index=child_index,
            data_column=data_column,
            input_name=str(input_info.get("name", "")).strip(),
        )

    def _extract_scorebook_schema_from_snapshot(self, snapshot: Dict[str, object]) -> List[ScoreColumnSchema]:
        """Builds leaf-column schema objects from one raw table snapshot."""
        header_rows = list(snapshot.get("scoreTableHeaderRows", []))
        body_rows = list(snapshot.get("scoreTableBodyRows", []))
        if not header_rows or not body_rows:
            return []

        leaf_count = self._scorebook_leaf_count(body_rows)
        if leaf_count <= 0:
            return []

        header_grid, header_meta_grid = self._build_scorebook_header_grids(header_rows, leaf_count)
        schemas = [
            self._build_scorebook_schema_for_leaf(
                body_rows,
                header_grid,
                header_meta_grid,
                leaf_index,
            )
            for leaf_index in range(leaf_count)
        ]

        return self._finalize_schema_identity(schemas)

    def _find_schema_by_key(
        self, schemas: List[ScoreColumnSchema], column_key: str
    ) -> ScoreColumnSchema | None:
        """Finds one parsed schema by its stable key."""
        column_key = column_key.strip()
        if not column_key:
            return None
        return next((schema for schema in schemas if schema.column_key == column_key), None)

    def _detect_scorebook_columns(self, schemas: List[ScoreColumnSchema]) -> ScorebookDetectedColumns:
        """Chooses comment/score columns from the parsed schema using deterministic heuristics."""
        editable_comment_columns = [
            schema for schema in schemas if schema.editable and schema.role_hint == "comment"
        ]
        average_columns = [
            schema for schema in schemas if schema.role_hint == "average"
        ]
        score_columns = [
            schema for schema in schemas if schema.role_hint == "score"
        ]
        editable_average_columns = [schema for schema in average_columns if schema.editable]
        editable_score_columns = [schema for schema in score_columns if schema.editable]
        all_score_candidates = sorted(
            [*average_columns, *score_columns],
            key=lambda schema: schema.leaf_index,
        )

        preferred_score_column_key = ""
        preferred_score_reason = ""

        average_with_data = [schema for schema in average_columns if schema.sample_value.strip()]
        score_with_data = [schema for schema in score_columns if schema.sample_value.strip()]
        if average_with_data:
            preferred_schema = max(average_with_data, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Ưu tiên cột trung bình có dữ liệu."
        elif score_with_data:
            preferred_schema = max(score_with_data, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Ưu tiên cột điểm ngoài cùng bên phải hiện đã có dữ liệu."
        elif average_columns:
            preferred_schema = max(average_columns, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Fallback sang cột trung bình ngoài cùng bên phải."
        elif score_columns:
            preferred_schema = max(score_columns, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Fallback sang cột điểm ngoài cùng bên phải."

        preferred_comment_column_key = ""
        if editable_comment_columns:
            preferred_comment_column_key = min(
                editable_comment_columns,
                key=lambda schema: schema.leaf_index,
            ).column_key

        return ScorebookDetectedColumns(
            preferred_score_column_key=preferred_score_column_key,
            preferred_score_reason=preferred_score_reason,
            preferred_comment_column_key=preferred_comment_column_key,
            score_candidate_keys=[schema.column_key for schema in all_score_candidates],
            average_candidate_keys=[schema.column_key for schema in average_columns],
            comment_candidate_keys=[schema.column_key for schema in editable_comment_columns],
        )

    def _extract_live_write_rows(
        self,
        page: Page,
        context: ScorebookContext,
        source_column_key: str,
        comment_column_key: str,
    ) -> List[Dict[str, object]]:
        """Extracts all visible student rows for internal analysis/write from the active scorebook table."""
        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)
        comment_schema = self._find_schema_by_key(context.column_schemas, comment_column_key)
        if source_schema is None:
            raise RuntimeError(f"Không tìm thấy schema cho cột điểm `{source_column_key}`.")
        if comment_schema is None:
            raise RuntimeError(f"Không tìm thấy schema cho cột nhận xét `{comment_column_key}`.")

        student_name_indices = [
            schema.leaf_index
            for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index)
            if schema.role_hint == "student_name"
        ]
        student_code_schema = next(
            (schema for schema in context.column_schemas if schema.role_hint == "student_code"),
            None,
        )
        student_code_index = student_code_schema.leaf_index if student_code_schema is not None else 1

        return list(
            page.evaluate(
                """({ sourceIndex, commentIndex, studentNameIndices, studentCodeIndex }) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                if (!root) return [];
                const table = root.querySelector('table.table.tablefix');
                if (!table) return [];

                const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const cellValue = (cell) => {
                    if (!cell) return '';
                    const input = cell.querySelector('input, textarea, select');
                    if (input) return normalizeText(input.value || '');
                    return normalizeText(cell.innerText || cell.textContent || '');
                };

                return Array.from(table.querySelectorAll('tbody tr')).map((tr, rowIndex) => {
                    const cells = Array.from(tr.children);
                    const scoreCell = cells[sourceIndex];
                    const commentCell = cells[commentIndex];
                    const commentInput = commentCell ? commentCell.querySelector('input, textarea, select') : null;
                    const nameParts = studentNameIndices
                        .map(index => normalizeText(cells[index]?.innerText || cells[index]?.textContent || ''))
                        .filter(Boolean);
                    let studentName = nameParts.join(' ');
                    if (!studentName) {
                        const fallback = cells
                            .slice(0, 6)
                            .map(cell => normalizeText(cell.innerText || cell.textContent || ''))
                            .find(text => text && !/^\\d+$/.test(text) && !/\\d{2}\\/\\d{2}\\/\\d{4}/.test(text));
                        studentName = fallback || '';
                    }
                    return {
                        rowIndex: rowIndex + 1,
                        rowId: tr.id || '',
                        studentCode: normalizeText(cells[studentCodeIndex]?.innerText || cells[studentCodeIndex]?.textContent || ''),
                        studentName,
                        sourceValue: cellValue(scoreCell),
                        currentComment: cellValue(commentCell),
                        commentInputName: commentInput ? (commentInput.name || commentInput.id || '') : '',
                    };
                });
            }""",
                {
                    "sourceIndex": source_schema.leaf_index,
                    "commentIndex": comment_schema.leaf_index,
                    "studentNameIndices": student_name_indices,
                    "studentCodeIndex": student_code_index,
                },
            )
        )

    def build_comment_write_queue(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        source_column_key: str,
        comment_column_key: str,
        rules: List[CommentRule],
        username: str = "",
        password: str = "",
        allow_overwrite_existing_comment: bool = False,
    ) -> Tuple[ScorebookContext, List[CommentWriteRow], str, str]:
        """Builds the internal write queue that would be written by the current rule set."""
        compiled_rules = compile_comment_rules(rules)
        if not compiled_rules:
            raise RuntimeError("Chưa có rule hợp lệ để phân tích dữ liệu ghi nhận xét.")

        with self._open_page() as page:
            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
            )
            context, write_rows = self._build_comment_write_queue_on_page(
                page,
                snapshot,
                source_column_key=source_column_key,
                comment_column_key=comment_column_key,
                compiled_rules=compiled_rules,
                allow_overwrite_existing_comment=allow_overwrite_existing_comment,
            )
            return context, write_rows, login_message, selection_message

    def _open_selected_scorebook_snapshot_on_page(
        self,
        page: Page,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[Dict[str, object], str, str]:
        """Logs in if needed, opens the scorebook screen, and applies one target context on the current page."""
        emit_progress(progress_callback, 5.0, "Đang chuẩn bị phiên VNEDU để đọc dữ liệu...")
        login_message = self._login_if_needed_on_page(
            page,
            username=username,
            password=password,
            progress_callback=create_subprogress_reporter(progress_callback, 5.0, 28.0),
        )
        self._ensure_scorebook_screen(
            page,
            progress_callback=create_subprogress_reporter(progress_callback, 28.0, 42.0),
        )
        snapshot = self._wait_for_scorebook_snapshot(
            page,
            timeout_sec=8.0,
            progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),
            progress_message="Đang đọc dữ liệu khung Sổ điểm...",
        )
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
            progress_callback=create_subprogress_reporter(progress_callback, 58.0, 70.0),
            progress_message="Đang đọc quyền và thông tin giáo viên...",
        )
        snapshot, selection_message = self._select_scorebook_context_on_page(
            page,
            snapshot,
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
            progress_callback=create_subprogress_reporter(progress_callback, 70.0, 100.0),
        )
        return snapshot, login_message, selection_message

    def _build_comment_write_queue_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        source_column_key: str,
        comment_column_key: str,
        compiled_rules: List[Tuple[Callable[[object], bool], str, str]],
        allow_overwrite_existing_comment: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, List[CommentWriteRow]]:
        """Builds one comment write queue from the already-selected live scorebook page."""
        emit_progress(progress_callback, 10.0, "Đang phân tích cấu trúc Sổ điểm...")
        context = self._build_scorebook_context(snapshot)
        emit_progress(progress_callback, 35.0, "Đang đọc dữ liệu từng học sinh từ live Chrome...")
        live_rows = self._extract_live_write_rows(
            page,
            context,
            source_column_key=source_column_key,
            comment_column_key=comment_column_key,
        )
        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)
        write_rows = build_comment_write_rows_from_live_data(
            live_rows,
            source_column_key=source_column_key,
            source_column_name=source_schema.display_name if source_schema is not None else source_column_key,
            compiled_rules=compiled_rules,
            allow_overwrite_existing_comment=allow_overwrite_existing_comment,
        )
        emit_progress(progress_callback, 100.0, "Đã phân tích xong hàng chờ ghi nhận xét.")
        return context, write_rows

    def _apply_comment_payload_to_active_scorebook(
        self,
        page: Page,
        payload: List[Dict[str, str]],
        auto_save: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, object]:
        """Writes one prepared payload into the active scorebook window in batches so the GUI can reflect live progress."""
        if not payload:
            return {"updated": [], "failed": [], "saveClicked": False}

        batch_size = 1 if len(payload) <= 12 else (3 if len(payload) <= 30 else 5)
        updated_input_names: List[str] = []
        failed_input_names: List[str] = []
        save_clicked = False

        emit_progress(progress_callback, 5.0, "Đang ghi dữ liệu lên các ô nhận xét...")
        for batch_start in range(0, len(payload), batch_size):
            batch_rows = payload[batch_start : batch_start + batch_size]
            batch_result = dict(
                page.evaluate(
                    """({ rows, finalizeMode }) => {
                    const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                    const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                    const root = active || wins[wins.length - 1];
                    if (!root) {
                        return { updated: [], failed: rows.map(item => item.inputName), saveClicked: false };
                    }

                    const setValue = (input, value) => {
                        const proto = input.tagName === 'TEXTAREA'
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                        const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (nativeSetter && nativeSetter.set) {
                            nativeSetter.set.call(input, value);
                        } else {
                            input.value = value;
                        }
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    };

                    const updated = [];
                    const failed = [];
                    for (const row of rows) {
                        const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;
                        const input = root.querySelector(selector);
                        if (!input) {
                            failed.push(row.inputName);
                            continue;
                        }
                        try {
                            input.focus();
                            setValue(input, row.text);
                            updated.push(row.inputName);
                        } catch (error) {
                            failed.push(row.inputName);
                        }
                    }

                    let saveClicked = false;
                    if (finalizeMode === 'save') {
                        const saveButton = Array.from(root.querySelectorAll('button, span, a, div'))
                            .find(el => /^lưu$/i.test((el.innerText || '').trim()));
                        if (saveButton) {
                            saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                            saveClicked = true;
                        }
                    } else if (finalizeMode === 'blur') {
                        const firstReadonlyCell = root.querySelector('table.table.tablefix tbody tr td');
                        if (firstReadonlyCell) {
                            firstReadonlyCell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                        }
                    }
                    return { updated, failed, saveClicked };
                }""",
                    {"rows": batch_rows, "finalizeMode": "none"},
                )
            )
            updated_input_names.extend(str(item).strip() for item in list(batch_result.get("updated", [])) if str(item).strip())
            failed_input_names.extend(str(item).strip() for item in list(batch_result.get("failed", [])) if str(item).strip())
            processed_rows = min(batch_start + len(batch_rows), len(payload))
            emit_progress(
                progress_callback,
                10.0 + ((processed_rows / max(len(payload), 1)) * 78.0),
                f"Đang ghi nhận xét {processed_rows}/{len(payload)} học sinh...",
            )

        emit_progress(
            progress_callback,
            92.0,
            "Đang bấm Lưu dữ liệu..." if auto_save else "Đang chốt dữ liệu trên ô nhận xét...",
        )
        finalize_result = dict(
            page.evaluate(
                """({ finalizeMode }) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                if (!root) {
                    return { updated: [], failed: [], saveClicked: false };
                }

                let saveClicked = false;
                if (finalizeMode === 'save') {
                    const saveButton = Array.from(root.querySelectorAll('button, span, a, div'))
                        .find(el => /^lưu$/i.test((el.innerText || '').trim()));
                    if (saveButton) {
                        saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                        saveClicked = true;
                    }
                } else if (finalizeMode === 'blur') {
                    const firstReadonlyCell = root.querySelector('table.table.tablefix tbody tr td');
                    if (firstReadonlyCell) {
                        firstReadonlyCell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    }
                }
                return { updated: [], failed: [], saveClicked };
            }""",
                {"finalizeMode": ("save" if auto_save else "blur")},
            )
        )
        save_clicked = bool(finalize_result.get("saveClicked"))
        emit_progress(progress_callback, 100.0, "Đã điền xong dữ liệu vào các ô nhận xét.")
        return {
            "updated": list(dict.fromkeys(updated_input_names)),
            "failed": list(dict.fromkeys(failed_input_names)),
            "saveClicked": save_clicked,
        }

    def _arm_scorebook_save_monitor(self, page: Page) -> int:
        """Installs or resets one browser-side monitor that records save requests after the next click."""
        return int(
            page.evaluate(
                """() => {
                const previewText = (value) => {
                    if (value == null) return '';
                    try {
                        return String(value).slice(0, 400);
                    } catch (error) {
                        return '';
                    }
                };

                if (!window.__codexScorebookSaveMonitor) {
                    const monitor = {
                        nextRunId: 1,
                        requests: [],
                    };

                    const originalOpen = XMLHttpRequest.prototype.open;
                    const originalSend = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                        this.__codexSaveMethod = method;
                        this.__codexSaveUrl = url;
                        return originalOpen.call(this, method, url, ...rest);
                    };
                    XMLHttpRequest.prototype.send = function(body) {
                        const runId = monitor.activeRunId || 0;
                        const request = {
                            runId,
                            transport: 'xhr',
                            method: previewText(this.__codexSaveMethod || 'GET').toUpperCase(),
                            url: previewText(this.__codexSaveUrl || ''),
                            bodyPreview: previewText(body),
                            startedAt: Date.now(),
                            finished: false,
                            status: 0,
                            responsePreview: '',
                        };
                        monitor.requests.push(request);
                        const finish = () => {
                            if (request.finished) return;
                            request.finished = true;
                            request.status = Number(this.status || 0);
                            request.finishedAt = Date.now();
                            try {
                                request.responsePreview = previewText(this.responseText || '');
                            } catch (error) {
                                request.responsePreview = '';
                            }
                        };
                        this.addEventListener('loadend', finish);
                        this.addEventListener('error', finish);
                        this.addEventListener('abort', finish);
                        return originalSend.call(this, body);
                    };

                    if (typeof window.fetch === 'function') {
                        const originalFetch = window.fetch.bind(window);
                        window.fetch = function(input, init) {
                            const runId = monitor.activeRunId || 0;
                            const request = {
                                runId,
                                transport: 'fetch',
                                method: previewText((init && init.method) || 'GET').toUpperCase(),
                                url: previewText(typeof input === 'string' ? input : ((input && input.url) || '')),
                                bodyPreview: previewText((init && init.body) || ''),
                                startedAt: Date.now(),
                                finished: false,
                                status: 0,
                                responsePreview: '',
                            };
                            monitor.requests.push(request);
                            return originalFetch(input, init).then(
                                async (response) => {
                                    request.status = Number(response.status || 0);
                                    request.finished = true;
                                    request.finishedAt = Date.now();
                                    try {
                                        const clone = response.clone();
                                        request.responsePreview = previewText(await clone.text());
                                    } catch (error) {
                                        request.responsePreview = '';
                                    }
                                    return response;
                                },
                                (error) => {
                                    request.finished = true;
                                    request.finishedAt = Date.now();
                                    request.responsePreview = previewText(error && error.message);
                                    throw error;
                                }
                            );
                        };
                    }

                    window.__codexScorebookSaveMonitor = monitor;
                }

                const monitor = window.__codexScorebookSaveMonitor;
                const runId = Number(monitor.nextRunId || 1);
                monitor.nextRunId = runId + 1;
                monitor.activeRunId = runId;
                monitor.requests = monitor.requests.filter(item => Number(item.runId || 0) !== runId);
                return runId;
            }"""
            )
        )

    def _read_scorebook_save_monitor_requests(self, page: Page, run_id: int) -> List[Dict[str, object]]:
        """Returns browser-observed save requests for one armed save run id."""
        return list(
            page.evaluate(
                """(runId) => {
                const monitor = window.__codexScorebookSaveMonitor;
                if (!monitor) return [];
                return (monitor.requests || [])
                    .filter(item => Number(item.runId || 0) === Number(runId || 0))
                    .map(item => ({
                        transport: String(item.transport || ''),
                        method: String(item.method || ''),
                        url: String(item.url || ''),
                        bodyPreview: String(item.bodyPreview || ''),
                        finished: Boolean(item.finished),
                        status: Number(item.status || 0),
                        responsePreview: String(item.responsePreview || ''),
                    }));
            }""",
                run_id,
            )
        )

    def _verify_scorebook_save_on_server(
        self,
        page: Page,
        save_monitor_run_id: int,
        auto_save: bool,
        save_clicked: bool,
        payload_input_names: List[str],
        timeout_sec: float = 8.0,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, object]:
        """Waits for server-side save requests and classifies the save outcome."""
        if not auto_save or not save_clicked:
            emit_progress(progress_callback, 100.0, "Không cần chờ xác minh lưu tự động.")
            save_verified, verification_mode, verification_detail = evaluate_server_save_verification(
                auto_save_requested=auto_save,
                save_clicked=save_clicked,
                save_requests=[],
                request_markers=payload_input_names,
            )
            return {
                "saveVerified": save_verified,
                "saveVerificationMode": verification_mode,
                "saveVerificationDetail": verification_detail,
            }

        deadline = time.time() + timeout_sec
        latest_requests: List[Dict[str, object]] = []
        while time.time() < deadline:
            latest_requests = self._read_scorebook_save_monitor_requests(page, save_monitor_run_id)
            relevant_requests = select_relevant_server_save_requests(
                latest_requests,
                request_markers=payload_input_names,
            )
            if relevant_requests and all(bool(request.get("finished")) for request in relevant_requests):
                emit_progress(progress_callback, 100.0, "Đã nhận phản hồi lưu dữ liệu từ server.")
                break
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(
                progress_callback,
                max(10.0, elapsed_ratio * 95.0),
                "Đang chờ VNEDU phản hồi thao tác Lưu...",
            )
            page.wait_for_timeout(250)

        save_verified, verification_mode, verification_detail = evaluate_server_save_verification(
            auto_save_requested=auto_save,
            save_clicked=save_clicked,
            save_requests=latest_requests,
            request_markers=payload_input_names,
        )
        emit_progress(progress_callback, 100.0, "Đã hoàn tất xác minh lưu dữ liệu.")
        return {
            "saveVerified": save_verified,
            "saveVerificationMode": verification_mode,
            "saveVerificationDetail": verification_detail,
        }

    def _read_comment_payload_values(
        self,
        page: Page,
        payload: List[Dict[str, str]],
    ) -> Dict[str, object]:
        """Reads the current DOM values for one prepared write payload from the active scorebook window."""
        return dict(
            page.evaluate(
                """(rows) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                const values = {};
                if (!root) return values;
                for (const row of rows) {
                    const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;
                    const input = root.querySelector(selector);
                    values[row.inputName] = input ? String(input.value || '') : '';
                }
                return values;
            }""",
                payload,
            )
        )

    def apply_comment_write_rows(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        write_rows: List[CommentWriteRow],
        username: str = "",
        password: str = "",
        auto_save: bool = True,
    ) -> Tuple[ScorebookContext, CommentWriteResult, str, str]:
        """Writes analyzed rows back into the live scorebook and verifies the updated values."""
        rows_to_apply = ready_comment_write_rows(write_rows)
        if not rows_to_apply:
            raise RuntimeError("Không có dòng nào trong hàng chờ ghi sẵn sàng để nhập nhận xét.")

        with self._open_page() as page:
            _snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
            )
            context, result = self._apply_comment_write_rows_on_page(
                page,
                write_rows,
                auto_save=auto_save,
            )
            return context, result, login_message, selection_message

    def _apply_comment_write_rows_on_page(
        self,
        page: Page,
        write_rows: List[CommentWriteRow],
        auto_save: bool = True,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, CommentWriteResult]:
        """Writes one analyzed comment queue back into the already-selected live scorebook page."""
        rows_to_apply = ready_comment_write_rows(write_rows)
        if not rows_to_apply:
            raise RuntimeError("Không có dòng nào trong hàng chờ ghi sẵn sàng để nhập nhận xét.")

        emit_progress(progress_callback, 8.0, "Đang chuẩn bị dữ liệu để ghi lên cột nhận xét...")
        payload = build_comment_write_payload(rows_to_apply)
        emit_progress(progress_callback, 16.0, "Đang gắn bộ theo dõi thao tác Lưu...")
        save_monitor_run_id = self._arm_scorebook_save_monitor(page)
        result_payload = self._apply_comment_payload_to_active_scorebook(
            page,
            payload,
            auto_save=auto_save,
            progress_callback=create_subprogress_reporter(progress_callback, 16.0, 68.0),
        )
        result_payload.update(
            self._verify_scorebook_save_on_server(
                page,
                save_monitor_run_id=save_monitor_run_id,
                auto_save=auto_save,
                save_clicked=bool(result_payload.get("saveClicked")),
                payload_input_names=[str(item.get("inputName", "")).strip() for item in payload],
                progress_callback=create_subprogress_reporter(progress_callback, 68.0, 84.0),
            )
        )
        emit_progress(progress_callback, 88.0, "Đang xác minh lại dữ liệu vừa ghi trên giao diện...")
        page.wait_for_timeout(800)
        verification_map = self._read_comment_payload_values(
            page,
            payload,
        )
        emit_progress(progress_callback, 94.0, "Đang tải lại ngữ cảnh Sổ điểm sau khi ghi...")
        context = self._build_scorebook_context(self._scorebook_snapshot(page))
        result = summarize_comment_write_result(
            write_rows,
            rows_to_apply,
            result_payload=result_payload,
            verification_map=verification_map,
        )
        emit_progress(progress_callback, 100.0, "Đã hoàn tất thao tác ghi nhận xét.")
        return context, result

    def analyze_and_apply_comment_rows(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        source_column_key: str,
        comment_column_key: str,
        rules: List[CommentRule],
        username: str = "",
        password: str = "",
        auto_save: bool = True,
        allow_overwrite_existing_comment: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[
        ScorebookContext,
        List[CommentWriteRow],
        str,
        str,
        ScorebookContext,
        CommentWriteResult | None,
        str,
        str,
    ]:
        """Runs analyze and apply on one live page session to avoid reopening the same scorebook twice."""
        emit_progress(progress_callback, 3.0, "Đang kiểm tra rule và chuẩn bị ghi nhận xét...")
        compiled_rules = compile_comment_rules(rules)
        if not compiled_rules:
            raise RuntimeError("Chưa có rule hợp lệ để phân tích dữ liệu ghi nhận xét.")

        emit_progress(progress_callback, 8.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 8.0, 42.0),
            )
            queue_context, write_rows = self._build_comment_write_queue_on_page(
                page,
                snapshot,
                source_column_key=source_column_key,
                comment_column_key=comment_column_key,
                compiled_rules=compiled_rules,
                allow_overwrite_existing_comment=allow_overwrite_existing_comment,
                progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),
            )
            if not ready_comment_write_rows(write_rows):
                emit_progress(progress_callback, 100.0, "Không có dòng nào sẵn sàng để ghi nhận xét.")
                return (
                    queue_context,
                    write_rows,
                    login_message,
                    selection_message,
                    queue_context,
                    None,
                    "",
                    "",
                )
            apply_context, apply_result = self._apply_comment_write_rows_on_page(
                page,
                write_rows,
                auto_save=auto_save,
                progress_callback=create_subprogress_reporter(progress_callback, 58.0, 100.0),
            )
            emit_progress(progress_callback, 100.0, "Đã hoàn tất quá trình ghi nhận xét.")
            return (
                queue_context,
                write_rows,
                login_message,
                selection_message,
                apply_context,
                apply_result,
                "",
                "",
            )

    def _option_text_by_id(self, options: List[ScoreOption], option_id: str) -> str:
        """Finds the human-readable text for one score option id."""
        option_id = option_id.strip()
        for option in options:
            if option.option_id == option_id:
                return option.option_text
        return option_id

    def _subject_options_by_class_for_current_grade(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str,
        term_id: str,
        class_options: List[ScoreOption],
    ) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
        """Enumerates subject options per class and restores the original live scorebook context afterwards."""
        original_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        original_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        original_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        original_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        current_snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=False,
            include_class=True,
            include_subject=True,
            include_term=False,
        )
        class_subject_specs: List[Dict[str, object]] = []

        for class_option in class_options:
            class_id = class_option.option_id.strip()
            if not class_id:
                continue
            current_class_id = self._effective_snapshot_selected_id(current_snapshot, "currentClassId", "hiddenClassId")
            if current_class_id != class_id:
                class_combo_id = str(current_snapshot.get("classComboId", "")).strip()
                if not class_combo_id or not self._set_combo_value(page, class_combo_id, class_id):
                    raise RuntimeError(f"Không thể chọn lớp id={class_id} trong lúc dò quyền lớp/môn.")
                page.wait_for_timeout(150)
                current_snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=grade_id or None,
                    expected_class_id=class_id,
                    expected_term_id=term_id or None,
                    timeout_sec=6.0,
                )
                actual_class_id = self._effective_snapshot_selected_id(current_snapshot, "currentClassId", "hiddenClassId")
                if actual_class_id != class_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={class_id} để dò quyền.")
            current_snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                current_snapshot,
                include_grade=False,
                include_class=False,
                include_subject=True,
                include_term=False,
            )
            subject_options = self._build_options(list(current_snapshot.get("subjectOptions", [])))
            if not subject_options:
                continue
            class_subject_specs.append(
                {
                    "classId": class_option.option_id,
                    "classText": class_option.option_text,
                    "subjectOptions": [
                        {
                            "option_id": option.option_id,
                            "option_text": option.option_text,
                        }
                        for option in subject_options
                    ],
                }
            )

        if self._requested_scorebook_context_differs(
            current_snapshot,
            grade_id=original_grade_id,
            class_id=original_class_id,
            subject_id=original_subject_id,
            term_id=original_term_id,
        ):
            current_snapshot, _ = self._select_scorebook_context_on_page(
                page,
                current_snapshot,
                grade_id=original_grade_id,
                class_id=original_class_id,
                subject_id=original_subject_id,
                term_id=original_term_id,
            )

        return class_subject_specs, current_snapshot

    def _can_comment_scorebook(self, permission_text: str, enabled_comment_input_count: int) -> bool:
        """Returns whether the current scorebook payload allows comment editing."""
        normalized_permission = permission_text.strip().lower()
        if enabled_comment_input_count <= 0:
            return False
        return "không có quyền" not in normalized_permission and "khong co quyen" not in normalized_permission

    def _best_effort_scorebook_snapshot(self, page: Page) -> Tuple[Dict[str, object] | None, Exception | None]:
        """Reads one scorebook snapshot while treating transient live-tab errors as retryable."""
        try:
            return self._scorebook_snapshot(page), None
        except (RuntimeError, PlaywrightError) as error:
            return None, error

    def _finish_scorebook_snapshot_wait(
        self,
        last_snapshot: Dict[str, object],
        last_error: Exception | None,
        timeout_sec: float,
        purpose: str,
    ) -> Dict[str, object]:
        """Returns the last good snapshot, or raises the last read error if none succeeded."""
        if last_snapshot:
            return last_snapshot
        if last_error is not None:
            raise RuntimeError(
                f"Hết thời gian chờ {purpose} trong {timeout_sec:.1f}s. Lỗi cuối: {last_error}"
            ) from last_error
        raise RuntimeError(f"Hết thời gian chờ {purpose} trong {timeout_sec:.1f}s nhưng chưa đọc được snapshot nào.")

    def _discover_accessible_entries_for_current_grade(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str,
        term_id: str,
    ) -> List[ScorebookAccessEntry]:
        """Scans the current grade-term matrix and keeps only class-subject pairs with comment rights."""
        grade_id = grade_id.strip()
        term_id = term_id.strip()
        snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=False,
            include_class=True,
            include_subject=True,
            include_term=False,
        )
        class_options = self._build_options(list(snapshot.get("classOptions", [])))
        if not grade_id or not term_id or not class_options:
            return []

        grade_text = self._option_text_by_id(self._build_options(list(snapshot.get("gradeOptions", []))), grade_id)
        term_text = self._option_text_by_id(self._build_options(list(snapshot.get("termOptions", []))), term_id)
        school_year = str(snapshot.get("hiddenSchoolYear", "")).strip() or str(snapshot.get("currentSchoolYear", "")).strip()
        window_id = str(snapshot.get("windowId", "")).strip()
        if not school_year or not window_id:
            return []
        class_subject_specs, _restored_snapshot = self._subject_options_by_class_for_current_grade(
            page,
            snapshot,
            grade_id=grade_id,
            term_id=term_id,
            class_options=class_options,
        )
        if not class_subject_specs:
            return []

        raw_entries = page.evaluate(
            """async ({ schoolYear, windowId, gradeId, gradeText, termId, termText, classSubjectSpecs }) => {
            const endpoint = '/v5/?load=edu.so_diem.nhap';
            const base = {
                app_nam_hoc: schoolYear,
                nam_hoc: schoolYear,
                iKhoi: gradeId,
                iHocKyId: termId,
                winid: windowId,
            };

            const parseRoleText = (doc) => {
                const roleCell = Array.from(doc.querySelectorAll('td')).find(td =>
                    /quyền hạn/i.test((td.textContent || '').trim())
                );
                return (roleCell?.textContent || '').trim();
            };

            const parseTeacherText = (doc) => ((doc.querySelector('#gvbm')?.textContent) || '').trim();
            const tasks = [];
            for (const classSpec of classSubjectSpecs) {
                for (const subjectOption of classSpec.subjectOptions || []) {
                    tasks.push({
                        classOption: {
                            option_id: classSpec.classId,
                            option_text: classSpec.classText,
                        },
                        subjectOption,
                    });
                }
            }

            const results = [];
            const concurrency = 6;
            for (let index = 0; index < tasks.length; index += concurrency) {
                const batch = tasks.slice(index, index + concurrency);
                const batchResults = await Promise.all(batch.map(async (task) => {
                    const params = new URLSearchParams({
                        ...base,
                        iLopId: task.classOption.option_id,
                        iMonHocId: task.subjectOption.option_id,
                    });

                    try {
                        const response = await fetch(endpoint, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                            },
                            body: params.toString(),
                        });
                        const html = await response.text();
                        const doc = new DOMParser().parseFromString(html, 'text/html');
                        const commentInputs = Array.from(doc.querySelectorAll('input.input_nhan_xet'));
                        const enabledCommentInputCount = commentInputs.filter(input => !input.disabled && !input.readOnly).length;
                        return {
                            gradeId,
                            gradeText,
                            classId: task.classOption.option_id,
                            classText: task.classOption.option_text,
                            subjectId: task.subjectOption.option_id,
                            subjectText: task.subjectOption.option_text,
                            termId,
                            termText,
                            teacherText: parseTeacherText(doc),
                            permissionText: parseRoleText(doc),
                            commentInputCount: commentInputs.length,
                            enabledCommentInputCount,
                        };
                    } catch (error) {
                        return {
                            gradeId,
                            gradeText,
                            classId: task.classOption.option_id,
                            classText: task.classOption.option_text,
                            subjectId: task.subjectOption.option_id,
                            subjectText: task.subjectOption.option_text,
                            termId,
                            termText,
                            teacherText: '',
                            permissionText: `Lỗi dò quyền: ${String(error)}`,
                            commentInputCount: 0,
                            enabledCommentInputCount: 0,
                        };
                    }
                }));
                results.push(...batchResults);
            }
            return results;
        }""",
            {
                "schoolYear": school_year,
                "windowId": window_id,
                "gradeId": grade_id,
                "gradeText": grade_text,
                "termId": term_id,
                "termText": term_text,
                "classSubjectSpecs": class_subject_specs,
            },
        )
        entries = self._build_access_entries(list(raw_entries or []))
        return [
            entry
            for entry in entries
            if self._can_comment_scorebook(entry.permission_text, entry.enabled_comment_input_count)
        ]

    def _wait_for_scorebook_snapshot(
        self,
        page: Page,
        expected_grade_id: str | None = None,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm đồng bộ dữ liệu...",
    ) -> Dict[str, object]:
        """Waits until scorebook combobox state reaches the expected ids."""
        deadline = time.time() + timeout_sec
        last_snapshot: Dict[str, object] = {}
        last_error: Exception | None = None
        while time.time() < deadline:
            snapshot, snapshot_error = self._best_effort_scorebook_snapshot(page)
            if snapshot is None:
                last_error = snapshot_error
                page.wait_for_timeout(250)
                continue
            last_snapshot = snapshot
            last_error = None

            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")

            grade_ready = expected_grade_id is None or actual_grade_id == expected_grade_id
            class_ready = expected_class_id is None or actual_class_id == expected_class_id
            subject_ready = expected_subject_id is None or actual_subject_id == expected_subject_id
            term_ready = expected_term_id is None or actual_term_id == expected_term_id

            class_store_ready = True
            if str(snapshot.get("classComboId", "")).strip():
                class_store_ready = len(list(snapshot.get("classOptions", []))) > 0

            subject_store_ready = True
            if str(snapshot.get("subjectComboId", "")).strip():
                subject_store_ready = len(list(snapshot.get("subjectOptions", []))) > 0

            term_store_ready = True
            if str(snapshot.get("termComboId", "")).strip():
                term_store_ready = len(list(snapshot.get("termOptions", []))) > 0

            if (
                grade_ready
                and class_ready
                and subject_ready
                and term_ready
                and class_store_ready
                and subject_store_ready
                and term_store_ready
            ):
                emit_progress(progress_callback, 100.0, progress_message)
                return snapshot
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(progress_callback, max(5.0, elapsed_ratio * 95.0), progress_message)
            page.wait_for_timeout(250)
        return self._finish_scorebook_snapshot_wait(
            last_snapshot,
            last_error,
            timeout_sec,
            "Sổ điểm đồng bộ Khối/Lớp/Môn/Học kỳ",
        )

    def _wait_for_scorebook_permission_snapshot(
        self,
        page: Page,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",
    ) -> Dict[str, object]:
        """Waits until the scorebook body catches up and exposes teacher/permission badges."""
        deadline = time.time() + timeout_sec
        last_snapshot: Dict[str, object] = {}
        last_error: Exception | None = None
        while time.time() < deadline:
            snapshot, snapshot_error = self._best_effort_scorebook_snapshot(page)
            if snapshot is None:
                last_error = snapshot_error
                page.wait_for_timeout(250)
                continue
            last_snapshot = snapshot
            last_error = None
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            class_ready = expected_class_id is None or actual_class_id == expected_class_id
            subject_ready = expected_subject_id is None or actual_subject_id == expected_subject_id
            term_ready = expected_term_id is None or actual_term_id == expected_term_id
            content_ready = bool(
                str(snapshot.get("permissionText", "")).strip()
                or str(snapshot.get("teacherText", "")).strip()
                or int(snapshot.get("commentInputCount", 0) or 0) > 0
            )
            if class_ready and subject_ready and term_ready and content_ready:
                emit_progress(progress_callback, 100.0, progress_message)
                return snapshot
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(progress_callback, max(5.0, elapsed_ratio * 95.0), progress_message)
            page.wait_for_timeout(250)
        return self._finish_scorebook_snapshot_wait(
            last_snapshot,
            last_error,
            timeout_sec,
            "Sổ điểm cập nhật quyền/giáo viên",
        )

    def _resolve_option_id(
        self,
        options: List[ScoreOption],
        current_id: str,
        preferred_id: str,
        label: str,
        notes: List[str],
    ) -> str:
        """Resolves a preferred combo id while tolerating stale GUI selections."""
        valid_ids = {item.option_id for item in options}
        preferred_id = preferred_id.strip()
        current_id = current_id.strip()

        if preferred_id and preferred_id in valid_ids:
            return preferred_id

        if preferred_id and preferred_id not in valid_ids:
            fallback_id = current_id if current_id in valid_ids else (options[0].option_id if options else "")
            if fallback_id:
                notes.append(f"{label} đã đổi dữ liệu trên web, app dùng lựa chọn hiện có gần nhất.")
                return fallback_id

        if current_id and current_id in valid_ids:
            return current_id

        return options[0].option_id if options else ""

    def _resolve_accessible_selection(
        self,
        entries: List[ScorebookAccessEntry],
        preferred_class_id: str = "",
        preferred_subject_id: str = "",
    ) -> Tuple[str, str, List[str]]:
        """Chooses a valid class-subject pair from the discovered permission matrix."""
        return resolve_accessible_selection(
            entries,
            preferred_class_id=preferred_class_id,
            preferred_subject_id=preferred_subject_id,
        )

    def _apply_access_entries_to_context(
        self,
        context: ScorebookContext,
        entries: List[ScorebookAccessEntry],
        grade_id: str,
        term_id: str,
    ) -> ScorebookContext:
        """Attaches discovered permission entries to the returned scorebook context."""
        return apply_access_entries_to_context(
            context,
            entries,
            grade_id=grade_id,
            term_id=term_id,
        )

    def _select_scorebook_grade_term_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str = "",
        term_id: str = "",
    ) -> Tuple[Dict[str, object], str]:
        """Applies grade and term first, without assuming the old class remains valid."""
        notes: List[str] = []
        target_grade_id = grade_id.strip()
        target_term_id = term_id.strip()
        context_requires_sync = self._requested_scorebook_context_differs(
            snapshot,
            grade_id=target_grade_id,
            term_id=target_term_id,
        )

        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        if target_grade_id:
            grade_options = self._build_options(list(snapshot.get("gradeOptions", [])))
            target_grade_id = self._resolve_option_id(
                grade_options,
                current_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"),
                preferred_id=target_grade_id,
                label="Khối",
                notes=notes,
            )
            grade_combo_id = str(snapshot.get("gradeComboId", "")).strip()
            current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            if grade_combo_id and target_grade_id and current_grade_id != target_grade_id:
                if not self._set_combo_value(page, grade_combo_id, target_grade_id):
                    raise RuntimeError(f"Không thể chọn khối id={target_grade_id} trên Sổ điểm.")
                page.wait_for_timeout(150)
                snapshot = self._wait_for_scorebook_snapshot(page, expected_grade_id=target_grade_id, timeout_sec=6.0)
                actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
                if actual_grade_id != target_grade_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")
                snapshot = self._hydrate_scorebook_snapshot_options(
                    page,
                    snapshot,
                    include_grade=False,
                    include_class=True,
                    include_subject=True,
                    include_term=True,
                )

        if target_term_id:
            term_options = self._build_options(list(snapshot.get("termOptions", [])))
            target_term_id = self._resolve_option_id(
                term_options,
                current_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId"),
                preferred_id=target_term_id,
                label="Học kỳ",
                notes=notes,
            )
            term_combo_id = str(snapshot.get("termComboId", "")).strip()
            current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            if term_combo_id and target_term_id and current_term_id != target_term_id:
                if not self._set_combo_value(page, term_combo_id, target_term_id):
                    raise RuntimeError(f"Không thể chọn học kỳ id={target_term_id} trên Sổ điểm.")
                page.wait_for_timeout(150)
                snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                    expected_term_id=target_term_id,
                    timeout_sec=6.0,
                )
                actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
                if actual_term_id != target_term_id:
                    snapshot = self._wait_for_scorebook_permission_snapshot(
                        page,
                        expected_term_id=target_term_id,
                        timeout_sec=8.0,
                    )
                    actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
                if actual_term_id != target_term_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn học kỳ id={target_term_id}.")
                snapshot = self._hydrate_scorebook_snapshot_options(
                    page,
                    snapshot,
                    include_grade=False,
                    include_class=True,
                    include_subject=True,
                    include_term=True,
                )

        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        return snapshot, " ".join(part for part in notes if part).strip()

    def _select_scorebook_context_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[Dict[str, object], str]:
        """Applies scorebook combo selections directly on the live VNEDU window."""
        notes: List[str] = []
        emit_progress(progress_callback, 5.0, "Đang so sánh ngữ cảnh Khối/Lớp/Môn/Học kỳ...")
        context_requires_sync = self._requested_scorebook_context_differs(
            snapshot,
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
        )
        if context_requires_sync:
            emit_progress(progress_callback, 10.0, "Đang nạp danh sách lựa chọn hiện tại từ Sổ điểm...")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        grade_options = self._build_options(list(snapshot.get("gradeOptions", [])))
        target_grade_id = self._resolve_option_id(
            grade_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"),
            preferred_id=grade_id,
            label="Khối",
            notes=notes,
        )
        grade_combo_id = str(snapshot.get("gradeComboId", "")).strip()
        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        if grade_combo_id and target_grade_id and current_grade_id != target_grade_id:
            emit_progress(progress_callback, 18.0, "Đang đổi Khối trên Sổ điểm...")
            if not self._set_combo_value(page, grade_combo_id, target_grade_id):
                raise RuntimeError(f"Không thể chọn khối id={target_grade_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=target_grade_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 18.0, 30.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Khối...",
            )
            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            if actual_grade_id != target_grade_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=False,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        class_options = self._build_options(list(snapshot.get("classOptions", [])))
        target_class_id = self._resolve_option_id(
            class_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),
            preferred_id=class_id,
            label="Lớp",
            notes=notes,
        )
        class_combo_id = str(snapshot.get("classComboId", "")).strip()
        current_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        if class_combo_id and target_class_id and current_class_id != target_class_id:
            emit_progress(progress_callback, 34.0, "Đang đổi Lớp trên Sổ điểm...")
            if not self._set_combo_value(page, class_combo_id, target_class_id):
                raise RuntimeError(f"Không thể chọn lớp id={target_class_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=target_class_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 34.0, 48.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Lớp...",
            )
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            if actual_class_id != target_class_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={target_class_id}.")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=False,
                include_class=True,
                include_subject=True,
                include_term=False,
            )

        subject_options = self._build_options(list(snapshot.get("subjectOptions", [])))
        target_subject_id = self._resolve_option_id(
            subject_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),
            preferred_id=subject_id,
            label="Môn",
            notes=notes,
        )
        subject_combo_id = str(snapshot.get("subjectComboId", "")).strip()
        current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        if subject_combo_id and target_subject_id and current_subject_id != target_subject_id:
            emit_progress(progress_callback, 52.0, "Đang đổi Môn trên Sổ điểm...")
            if not self._set_combo_value(page, subject_combo_id, target_subject_id):
                raise RuntimeError(f"Không thể chọn môn id={target_subject_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=target_subject_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 52.0, 66.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Môn...",
            )
            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
            if actual_subject_id != target_subject_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn môn id={target_subject_id}.")

        term_options = self._build_options(list(snapshot.get("termOptions", [])))
        target_term_id = self._resolve_option_id(
            term_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId"),
            preferred_id=term_id,
            label="Học kỳ",
            notes=notes,
        )
        term_combo_id = str(snapshot.get("termComboId", "")).strip()
        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        if term_combo_id and target_term_id and current_term_id != target_term_id:
            emit_progress(progress_callback, 70.0, "Đang đổi Học kỳ trên Sổ điểm...")
            if not self._set_combo_value(page, term_combo_id, target_term_id):
                raise RuntimeError(f"Không thể chọn học kỳ id={target_term_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=target_term_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 70.0, 84.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Học kỳ...",
            )
            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            if actual_term_id != target_term_id:
                snapshot = self._wait_for_scorebook_permission_snapshot(
                    page,
                    expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                    expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                    expected_term_id=target_term_id,
                    timeout_sec=8.0,
                    progress_callback=create_subprogress_reporter(progress_callback, 84.0, 90.0),
                    progress_message="Đang chờ quyền và giáo viên cập nhật sau khi đổi Học kỳ...",
                )
                actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            if actual_term_id != target_term_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn học kỳ id={target_term_id}.")

        emit_progress(progress_callback, 92.0, "Đang xác minh ngữ cảnh cuối cùng trên Sổ điểm...")
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
            progress_callback=create_subprogress_reporter(progress_callback, 92.0, 100.0),
            progress_message="Đang xác minh quyền và giáo viên cho ngữ cảnh mới...",
        )
        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )
        emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")
        return snapshot, " ".join(notes).strip()

    def load_scorebook_context(
        self,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, str]:
        """Loads the visible scorebook shell from the live browser."""
        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(
                page,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 30.0),
            )
            self._ensure_scorebook_screen(
                page,
                progress_callback=create_subprogress_reporter(progress_callback, 30.0, 45.0),
            )
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                timeout_sec=8.0,
                progress_callback=create_subprogress_reporter(progress_callback, 45.0, 65.0),
                progress_message="Đang đọc dữ liệu khung Sổ điểm...",
            )
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 65.0, 80.0),
                progress_message="Đang đọc quyền hạn và giáo viên...",
            )
            emit_progress(progress_callback, 82.0, "Đang nạp danh sách Khối/Lớp/Môn/Học kỳ...")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )
            emit_progress(progress_callback, 94.0, "Đang dựng ngữ cảnh Sổ điểm trong GUI...")
            context = self._build_scorebook_context(snapshot)
            emit_progress(progress_callback, 100.0, "Đã đọc xong dữ liệu Sổ điểm.")
            return context, login_message

    def load_accessible_scorebook_context(
        self,
        username: str = "",
        password: str = "",
    ) -> Tuple[ScorebookContext, str, str]:
        """Loads the current scorebook shell, then filters it to only class-subject pairs with permission."""
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(page, username=username, password=password)
            self._ensure_scorebook_screen(page)
            snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
            )
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )
            grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            entries = self._discover_accessible_entries_for_current_grade(page, snapshot, grade_id=grade_id, term_id=term_id)
            selection_message_parts: List[str] = []
            if entries:
                class_id, subject_id, fallback_notes = self._resolve_accessible_selection(
                    entries,
                    preferred_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),
                    preferred_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),
                )
                if class_id and subject_id:
                    snapshot, select_notes = self._select_scorebook_context_on_page(
                        page,
                        snapshot,
                        grade_id=grade_id,
                        class_id=class_id,
                        subject_id=subject_id,
                        term_id=term_id,
                    )
                    if select_notes:
                        selection_message_parts.append(select_notes)
                selection_message_parts.extend(fallback_notes)
            context = self._build_scorebook_context(snapshot)
            self._apply_access_entries_to_context(context, entries, grade_id=grade_id, term_id=term_id)
            if entries:
                selection_message_parts.append(
                    f"Đã dò quyền lớp/môn cho {self._option_text_by_id(context.grade_options, grade_id)}: "
                    f"{len(entries)} tổ hợp có thể nhập nhận xét."
                )
            else:
                selection_message_parts.append("Không tìm thấy tổ hợp lớp/môn nào có quyền nhập nhận xét trong khối hiện tại.")
            return context, login_message, " ".join(part for part in selection_message_parts if part).strip()

    def select_scorebook_context(
        self,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, str, str]:
        """Applies the requested scorebook context on the live browser and returns the refreshed shell."""
        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(
                page,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 20.0),
            )
            self._ensure_scorebook_screen(
                page,
                progress_callback=create_subprogress_reporter(progress_callback, 20.0, 32.0),
            )
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                timeout_sec=8.0,
                progress_callback=create_subprogress_reporter(progress_callback, 32.0, 45.0),
                progress_message="Đang đọc trạng thái hiện tại của Sổ điểm...",
            )
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 45.0, 55.0),
                progress_message="Đang đọc quyền trước khi đổi combobox...",
            )
            snapshot, selection_message = self._select_scorebook_context_on_page(
                page,
                snapshot,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                progress_callback=create_subprogress_reporter(progress_callback, 55.0, 100.0),
            )
            emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")
            return self._build_scorebook_context(snapshot), login_message, selection_message

    def select_accessible_scorebook_context(
        self,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        username: str = "",
        password: str = "",
    ) -> Tuple[ScorebookContext, str, str]:
        """Applies grade-term, scans permission matrix, then lands on one valid class-subject pair only."""
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(page, username=username, password=password)
            self._ensure_scorebook_screen(page)
            snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
            )
            initial_context = self._build_scorebook_context(snapshot)
            target_grade_id = grade_id.strip() or initial_context.selected_grade_id
            target_term_id = term_id.strip() or initial_context.selected_term_id
            snapshot, selection_message = self._select_scorebook_grade_term_on_page(
                page,
                snapshot,
                grade_id=target_grade_id,
                term_id=target_term_id,
            )
            current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or target_grade_id
            current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or target_term_id
            entries = self._discover_accessible_entries_for_current_grade(
                page,
                snapshot,
                grade_id=current_grade_id,
                term_id=current_term_id,
            )
            effective_class_id, effective_subject_id, fallback_notes = self._resolve_accessible_selection(
                entries,
                preferred_class_id=class_id,
                preferred_subject_id=subject_id,
            )
            if effective_class_id and effective_subject_id:
                snapshot, post_select_message = self._select_scorebook_context_on_page(
                    page,
                    snapshot,
                    grade_id=current_grade_id,
                    class_id=effective_class_id,
                    subject_id=effective_subject_id,
                    term_id=current_term_id,
                )
                if post_select_message:
                    selection_message = f"{selection_message} {post_select_message}".strip()
            context = self._build_scorebook_context(snapshot)
            self._apply_access_entries_to_context(
                context,
                entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
            )
            final_message_parts = [selection_message, *fallback_notes]
            if entries:
                final_message_parts.append(
                    f"Đã dò quyền lớp/môn cho {self._option_text_by_id(context.grade_options, current_grade_id)}: "
                    f"{len(entries)} tổ hợp có thể nhập nhận xét."
                )
            else:
                final_message_parts.append("Không tìm thấy tổ hợp lớp/môn nào có quyền nhập nhận xét trong khối hiện tại.")
            return context, login_message, " ".join(part for part in final_message_parts if part).strip()

    def discover_accessible_entries_for_current_context(
        self,
        username: str = "",
        password: str = "",
        expected_grade_id: str = "",
        expected_term_id: str = "",
    ) -> Tuple[ScorebookContext, List[ScorebookAccessEntry], str]:
        """Scans permission entries for the current live scorebook grade-term without changing combo selections."""
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(page, username=username, password=password)
            self._ensure_scorebook_screen(page)
            snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
            )
            context = self._build_scorebook_context(snapshot)
            grade_id = context.selected_grade_id
            term_id = context.selected_term_id
            if expected_grade_id.strip() and grade_id != expected_grade_id.strip():
                raise RuntimeError("Ngữ cảnh live Chrome đã đổi khối trước khi quét quyền nền hoàn tất.")
            if expected_term_id.strip() and term_id != expected_term_id.strip():
                raise RuntimeError("Ngữ cảnh live Chrome đã đổi học kỳ trước khi quét quyền nền hoàn tất.")
            entries = self._discover_accessible_entries_for_current_grade(
                page,
                snapshot,
                grade_id=grade_id,
                term_id=term_id,
            )
            self._apply_access_entries_to_context(context, entries, grade_id=grade_id, term_id=term_id)
            return context, entries, login_message

    def open_target_page(self, progress_callback: ProgressCallback | None = None) -> str:
        """Opens the VNEDU target page in the connected browser session."""
        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            emit_progress(progress_callback, 35.0, "Đang mở trang VNEDU...")
            self._goto_target_page(page)
            emit_progress(progress_callback, 100.0, "Đã mở trang VNEDU.")
            return page.url


class AutoNhanXetV2App:
    """Tkinter skeleton for the Playwright/CDP-based VNEDU comment tool."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(980, 680)

        self.port_var = tk.StringVar(value="9224")
        self.url_var = tk.StringVar(value="https://vemzezsoasgdsoctrang.vnedu.vn/v5/")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Chưa kết nối")

        self.grade_var = tk.StringVar()
        self.class_var = tk.StringVar()
        self.subject_var = tk.StringVar()
        self.term_var = tk.StringVar()
        self.detected_score_var = tk.StringVar(value="(chưa dò)")
        self.detected_comment_var = tk.StringVar(value="(chưa dò)")
        self.detected_candidates_var = tk.StringVar(value="")
        self.detected_reason_var = tk.StringVar(value="")
        self.score_source_var = tk.StringVar(value="(chưa dò)")
        self.num_forms_var = tk.StringVar(value="7")
        self.auto_save_var = tk.BooleanVar(value=True)
        self.allow_comment_overwrite_var = tk.BooleanVar(value=False)

        self.grade_label_to_id: Dict[str, str] = {}
        self.class_label_to_id: Dict[str, str] = {}
        self.subject_label_to_id: Dict[str, str] = {}
        self.term_label_to_id: Dict[str, str] = {}
        self.score_source_label_to_key: Dict[str, str] = {}
        self.current_context: ScorebookContext | None = None
        self.score_forms: List[Dict[str, tk.StringVar]] = []
        self.access_entry_cache: Dict[Tuple[str, str, str, str], List[ScorebookAccessEntry]] = {}
        self._access_scan_inflight: set[Tuple[str, str, str, str]] = set()
        self._access_cache_identity: Tuple[str, str] | None = None
        self._matrix_access_scan_enabled = False
        self.preferred_score_source_key = ""
        self._suspend_context_events = False
        self._suspend_config_autosave = True
        self._config_autosave_after_id: str | None = None
        self._rule_busy_widgets: List[tk.Misc] = []
        self._log_history: List[str] = []
        self._progress_value = 0.0
        self._progress_message = "Sẵn sàng"

        self._busy = False
        self._foreground_task_token = 0
        self._context_apply_after_id: str | None = None
        self._context_apply_delay_ms = 350
        self._poll_after_id: str | None = None
        self._automation_lock = threading.Lock()
        self._passive_scan_delay_ms = 900
        self._result_queue: Queue[
            Tuple[
                object | None,
                Exception | None,
                Callable[[object], None],
                Callable[[Exception], None] | None,
            ]
        ] = Queue()
        self._passive_result_queue: Queue[
            Tuple[
                object | None,
                Exception | None,
                Callable[[object], None],
                Callable[[Exception], None] | None,
            ]
        ] = Queue()
        self._progress_queue: Queue[Tuple[int, float, str]] = Queue()
        self._busy_widgets: List[Tuple[tk.Misc, str]] = []

        self._build_ui()
        self._load_config()
        self._sync_access_cache_identity()
        self._bind_config_autosave_traces()
        self._suspend_config_autosave = False
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self._poll_after_id = self.root.after(50, self._poll_background_results)

    def _register_busy_widget(self, widget: tk.Misc, normal_state: str) -> tk.Misc:
        """Tracks one widget so the busy-state guard can disable and restore it later."""
        self._busy_widgets.append((widget, normal_state))
        return widget

    def _on_root_destroy(self, event: tk.Event | None = None) -> None:
        """Cancels pending Tk callbacks when the root window is being destroyed."""
        if event is not None and event.widget is not self.root:
            return
        for attr_name in ("_poll_after_id", "_context_apply_after_id", "_config_autosave_after_id"):
            after_id = getattr(self, attr_name, None)
            if not after_id:
                continue
            setattr(self, attr_name, None)
            try:
                self.root.after_cancel(after_id)
            except (tk.TclError, RuntimeError):
                continue

    def _primary_action_button_options(self) -> Dict[str, object]:
        """Returns the shared visual styling for the main live-action buttons."""
        return {
            "bg": "#f0c93d",
            "fg": "#000000",
            "activebackground": "#ddb62f",
            "activeforeground": "#000000",
            "disabledforeground": "#a6924a",
            "font": ("Segoe UI", 10, "bold"),
            "relief": tk.SOLID,
            "borderwidth": 1,
            "padx": 14,
            "pady": 2,
            "highlightthickness": 0,
        }

    def _apply_comments_button_options(self) -> Dict[str, object]:
        """Returns the visual styling for the apply-comments action button."""
        return {
            "bg": "#58b957",
            "fg": "#000000",
            "activebackground": "#499f49",
            "activeforeground": "#000000",
            "disabledforeground": "#6f9c6f",
            "font": ("Segoe UI", 10, "bold"),
            "relief": tk.SOLID,
            "borderwidth": 1,
            "padx": 14,
            "pady": 2,
            "highlightthickness": 0,
        }

    def _build_session_frame(self, parent: tk.Misc) -> None:
        """Builds the VNEDU session/config controls at the top of the window."""
        session_frame = ttk.LabelFrame(parent, text="1. Phiên VNEDU")
        session_frame.pack(fill=tk.X)

        ttk.Label(session_frame, text="CDP Port:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.port_entry = ttk.Entry(session_frame, textvariable=self.port_var, width=10)
        self.port_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self._register_busy_widget(self.port_entry, "normal")

        ttk.Label(session_frame, text="URL:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.url_entry = ttk.Entry(session_frame, textvariable=self.url_var, width=58)
        self.url_entry.grid(row=0, column=3, padx=6, pady=6, sticky="we")
        self._register_busy_widget(self.url_entry, "normal")

        ttk.Label(session_frame, text="Tài khoản:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.username_entry = ttk.Entry(session_frame, textvariable=self.username_var, width=26)
        self.username_entry.grid(row=1, column=1, padx=6, pady=6, sticky="w")
        self._register_busy_widget(self.username_entry, "normal")

        ttk.Label(session_frame, text="Mật khẩu:").grid(row=1, column=2, padx=6, pady=6, sticky="w")
        password_row = ttk.Frame(session_frame)
        password_row.grid(row=1, column=3, padx=6, pady=6, sticky="w")
        self.password_entry = ttk.Entry(
            password_row,
            textvariable=self.password_var,
            width=26,
            show=password_entry_show_value(bool(self.show_password_var.get())),
        )
        self.password_entry.pack(side=tk.LEFT)
        self._register_busy_widget(self.password_entry, "normal")
        self.show_password_check = ttk.Checkbutton(
            password_row,
            text="Hiện mật khẩu",
            variable=self.show_password_var,
            command=self._apply_password_visibility,
        )
        self.show_password_check.pack(side=tk.LEFT, padx=(8, 0))
        self._register_busy_widget(self.show_password_check, "normal")

        button_row = ttk.Frame(session_frame)
        button_row.grid(row=2, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="we")

        self.open_button = ttk.Button(button_row, text="Mở VNEDU", command=self.on_open_web)
        self.open_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.open_button, "normal")

        self.save_session_button = ttk.Button(
            button_row,
            text="Lưu cấu hình",
            command=self.on_save_config,
        )
        self.save_session_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.save_session_button, "normal")

        self.load_shell_button = tk.Button(
            button_row,
            text="ĐĂNG NHẬP & LOAD DATA",
            command=self.on_load_scorebook_shell,
            **self._primary_action_button_options(),
        )
        self.load_shell_button.pack(side=tk.LEFT)
        self._register_busy_widget(self.load_shell_button, "normal")

        self.progress_canvas = tk.Canvas(
            button_row,
            height=24,
            background="#edf5ed",
            highlightthickness=1,
            highlightbackground="#b5cbb5",
            relief=tk.FLAT,
        )
        self.progress_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(18, 0))
        self._progress_fill_id = self.progress_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")
        self._progress_text_id = self.progress_canvas.create_text(
            8,
            12,
            anchor="w",
            fill="#1f4729",
            font=("Segoe UI", 9, "bold"),
            text=build_progress_caption(self._progress_value, self._progress_message),
        )
        self.progress_canvas.bind("<Configure>", self._on_progress_canvas_configure)
        self._render_progress_bar()

        ttk.Label(session_frame, textvariable=self.status_var, foreground="#2f5d50").grid(
            row=3, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="w"
        )
        session_frame.columnconfigure(3, weight=1)

    def _build_context_frame(self, parent: tk.Misc) -> None:
        """Builds the scorebook context selectors and apply button."""
        context_frame = ttk.LabelFrame(parent, text="2. Ngữ cảnh Sổ điểm (beta)")
        context_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        ttk.Label(context_frame, text="Khối:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.grade_combo = ttk.Combobox(context_frame, textvariable=self.grade_var, state="readonly", width=20)
        self.grade_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(context_frame, text="Lớp:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.class_combo = ttk.Combobox(context_frame, textvariable=self.class_var, state="readonly", width=20)
        self.class_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")

        ttk.Label(context_frame, text="Môn:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.subject_combo = ttk.Combobox(context_frame, textvariable=self.subject_var, state="readonly", width=20)
        self.subject_combo.grid(row=1, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(context_frame, text="Học kỳ:").grid(row=1, column=2, padx=6, pady=6, sticky="w")
        self.term_combo = ttk.Combobox(context_frame, textvariable=self.term_var, state="readonly", width=20)
        self.term_combo.grid(row=1, column=3, padx=6, pady=6, sticky="we")

        context_button_row = ttk.Frame(context_frame)
        context_button_row.grid(row=2, column=0, columnspan=4, padx=6, pady=(0, 6), sticky="w")

        self.apply_context_button = ttk.Button(
            context_button_row,
            text="Áp ngữ cảnh lên web",
            command=self.on_apply_selected_context,
        )
        self.apply_context_button.pack(side=tk.LEFT)

        self._register_busy_widget(self.grade_combo, "readonly")
        self._register_busy_widget(self.class_combo, "readonly")
        self._register_busy_widget(self.subject_combo, "readonly")
        self._register_busy_widget(self.term_combo, "readonly")
        self._register_busy_widget(self.apply_context_button, "normal")

        self.grade_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        self.class_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        self.subject_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        self.term_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)
        context_frame.columnconfigure(1, weight=1)
        context_frame.columnconfigure(3, weight=1)

    def _build_detected_columns_frame(self, parent: tk.Misc) -> None:
        """Builds the read-only panel showing detected score/comment columns."""
        detected_frame = ttk.LabelFrame(parent, text="3. Cột tự nhận diện")
        detected_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        ttk.Label(detected_frame, text="Cột điểm dùng để xét:").grid(row=0, column=0, padx=6, pady=4, sticky="nw")
        self.score_source_combo = ttk.Combobox(
            detected_frame,
            textvariable=self.score_source_var,
            state="readonly",
            width=28,
        )
        self.score_source_combo.grid(row=0, column=1, padx=6, pady=4, sticky="we")
        self.score_source_combo.bind("<<ComboboxSelected>>", self.on_score_source_selection_changed)
        self._register_busy_widget(self.score_source_combo, "readonly")
        ttk.Label(detected_frame, text="Cột điểm đề xuất:").grid(row=1, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_score_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(
            row=1, column=1, padx=6, pady=4, sticky="w"
        )
        ttk.Label(detected_frame, text="Cột nhận xét:").grid(row=2, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_comment_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(
            row=2, column=1, padx=6, pady=4, sticky="w"
        )
        ttk.Label(detected_frame, text="Ứng viên điểm:").grid(row=3, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_candidates_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(row=3, column=1, padx=6, pady=4, sticky="w")
        ttk.Label(detected_frame, text="Lý do chọn:").grid(row=4, column=0, padx=6, pady=4, sticky="nw")
        ttk.Label(
            detected_frame,
            textvariable=self.detected_reason_var,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(row=4, column=1, padx=6, pady=4, sticky="w")
        detected_frame.columnconfigure(1, weight=1)

    def _build_rules_frame(self, parent: tk.Misc) -> None:
        """Builds the rule editor, apply controls, and scrollable rule form area."""
        rules_frame = ttk.LabelFrame(parent, text="4. Rule nhận xét")
        rules_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        rule_ctrl_row = ttk.Frame(rules_frame)
        rule_ctrl_row.pack(fill=tk.X, padx=6, pady=(6, 4))
        ttk.Label(rule_ctrl_row, text="Số rule:").pack(side=tk.LEFT)
        self.num_forms_entry = ttk.Entry(rule_ctrl_row, textvariable=self.num_forms_var, width=6)
        self.num_forms_entry.pack(side=tk.LEFT, padx=(6, 8))
        self._register_busy_widget(self.num_forms_entry, "normal")

        self.build_rules_button = ttk.Button(
            rule_ctrl_row,
            text="Tạo form rule",
            command=self.on_build_rule_forms,
        )
        self.build_rules_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.build_rules_button, "normal")

        self.export_default_rules_button = ttk.Button(
            rule_ctrl_row,
            text="Nhận xét mặc định",
            command=self.on_export_default_rules,
        )
        self.export_default_rules_button.pack(side=tk.LEFT, padx=(0, 6))
        self._register_busy_widget(self.export_default_rules_button, "normal")

        self.apply_comments_button = tk.Button(
            rule_ctrl_row,
            text="GHI NHẬN XÉT",
            command=self.on_apply_comments,
            **self._apply_comments_button_options(),
        )
        self.apply_comments_button.pack(side=tk.RIGHT)
        self._register_busy_widget(self.apply_comments_button, "normal")

        self.auto_save_check = ttk.Checkbutton(
            rule_ctrl_row,
            text="Tự bấm Lưu",
            variable=self.auto_save_var,
        )
        self.auto_save_check.pack(side=tk.LEFT, padx=(12, 0))
        self._register_busy_widget(self.auto_save_check, "normal")

        self.allow_comment_overwrite_check = ttk.Checkbutton(
            rule_ctrl_row,
            text="Cho phép ghi đè nhận xét chữ đã có",
            variable=self.allow_comment_overwrite_var,
        )
        self.allow_comment_overwrite_check.pack(side=tk.LEFT, padx=(12, 0))
        self._register_busy_widget(self.allow_comment_overwrite_check, "normal")

        rule_hint = ttk.Label(
            rules_frame,
            text=(
                "Rule đầu tiên khớp sẽ được dùng. "
                "App dùng cột điểm bạn chọn ở trên để ghi trực tiếp lên web."
            ),
            justify=tk.LEFT,
        )
        rule_hint.pack(fill=tk.X, padx=6, pady=(0, 4))

        self.rule_canvas = tk.Canvas(rules_frame, height=180, highlightthickness=0)
        self.rule_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=(0, 6))
        self.rule_scrollbar = ttk.Scrollbar(rules_frame, orient=tk.VERTICAL, command=self.rule_canvas.yview)
        self.rule_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=(0, 6))
        self.rule_canvas.configure(yscrollcommand=self.rule_scrollbar.set)
        self.rule_form_container = ttk.Frame(self.rule_canvas)
        self.rule_canvas_window = self.rule_canvas.create_window(
            (0, 0),
            window=self.rule_form_container,
            anchor="nw",
        )
        self.rule_form_container.bind(
            "<Configure>",
            lambda _event: self.rule_canvas.configure(scrollregion=self.rule_canvas.bbox("all")),
        )
        self.rule_canvas.bind(
            "<Configure>",
            lambda event: self.rule_canvas.itemconfigure(self.rule_canvas_window, width=event.width),
        )

    def _build_ui(self) -> None:
        """Builds the initial skeleton GUI."""
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)
        self._build_session_frame(main)

        top_panels_row = ttk.Frame(main)
        top_panels_row.pack(fill=tk.X, pady=(10, 0))
        self._build_context_frame(top_panels_row)
        self._build_detected_columns_frame(top_panels_row)
        self._build_rules_frame(main)

    def _build_automation(self) -> VnEduScoreAutomation:
        """Builds the CDP automation object from current UI state."""
        self._sync_access_cache_identity()
        try:
            port = int(self.port_var.get().strip())
        except ValueError as error:
            raise ValueError("CDP Port phải là số nguyên hợp lệ.") from error

        url = self.url_var.get().strip()
        if not url:
            raise ValueError("URL VNEDU không được để trống.")
        return VnEduScoreAutomation(debug_port=port, target_url=url)

    def _log(self, message: str) -> None:
        """Stores one timestamped diagnostic line without rendering a GUI log panel."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self._log_history.append(line)
        if len(self._log_history) > 300:
            self._log_history = self._log_history[-300:]
        print(line)

    def _apply_password_visibility(self) -> None:
        """Toggles whether the password entry reveals the current VNEDU password."""
        show_value = password_entry_show_value(bool(self.show_password_var.get()))
        try:
            self.password_entry.configure(show=show_value)
        except (AttributeError, tk.TclError):
            return

    def _on_progress_canvas_configure(self, _event: tk.Event | None = None) -> None:
        """Re-renders the progress canvas whenever its available width changes."""
        self._render_progress_bar()

    def _render_progress_bar(self) -> None:
        """Draws the custom green progress bar together with the latest caption."""
        canvas = getattr(self, "progress_canvas", None)
        if canvas is None:
            return
        try:
            canvas_width = max(int(canvas.winfo_width()), 1)
            canvas_height = max(int(canvas.winfo_height()), 1)
        except tk.TclError:
            return

        fill_width = int((canvas_width - 2) * (clamp_progress_value(self._progress_value) / 100.0))
        canvas.coords(
            self._progress_fill_id,
            1,
            1,
            1 + max(fill_width, 0),
            max(canvas_height - 1, 1),
        )
        canvas.itemconfigure(
            self._progress_fill_id,
            fill=("#2fa34a" if self._progress_value > 0 else "#dfe9df"),
        )
        canvas.coords(self._progress_text_id, 8, canvas_height / 2)
        canvas.itemconfigure(
            self._progress_text_id,
            text=build_progress_caption(self._progress_value, self._progress_message),
        )

    def _set_status_text(self, status_text: str, sync_progress_caption: bool = True) -> None:
        """Updates the status line and optionally keeps the progress caption in sync with it."""
        normalized_text = str(status_text or "").strip()
        if not normalized_text:
            return
        self.status_var.set(normalized_text)
        if sync_progress_caption:
            self._progress_message = normalized_text
            self._render_progress_bar()

    def _set_progress(self, value: float, status_text: str = "") -> None:
        """Updates the visible progress bar and optionally mirrors the text into the status line."""
        self._progress_value = clamp_progress_value(value)
        if status_text:
            self._set_status_text(status_text, sync_progress_caption=True)
        self._render_progress_bar()

    def _make_progress_reporter(self, task_token: int) -> ProgressCallback:
        """Creates one thread-safe reporter that forwards worker progress back to the Tk thread."""

        def report(value: float, message: str = "") -> None:
            self._progress_queue.put((task_token, clamp_progress_value(value), str(message or "").strip()))

        return report

    def _set_busy(self, is_busy: bool, status_text: str = "") -> None:
        """Freezes interactive controls while a background automation task is running."""
        self._busy = bool(is_busy)
        for widget, normal_state in self._busy_widgets:
            try:
                widget.configure(state=("disabled" if is_busy else normal_state))
            except tk.TclError:
                continue

        try:
            self.root.configure(cursor="watch" if is_busy else "")
        except tk.TclError:
            pass

        if status_text:
            self._set_progress(self._progress_value if not is_busy else 0.0, status_text)

    def _cancel_pending_context_apply(self) -> None:
        """Cancels one queued auto-apply request for the scorebook context selectors."""
        after_id = getattr(self, "_context_apply_after_id", None)
        if not after_id:
            return
        self._context_apply_after_id = None
        try:
            self.root.after_cancel(after_id)
        except (tk.TclError, AttributeError):
            return

    def _selected_context_matches_current_context(self) -> bool:
        """Checks whether the GUI comboboxes still point to the current live scorebook context."""
        if self.current_context is None:
            return True
        selected_grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id)
        selected_class_id = self._selected_option_id(self.class_var, self.class_label_to_id)
        selected_subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id)
        selected_term_id = self._selected_option_id(self.term_var, self.term_label_to_id)
        return (
            selected_grade_id == self.current_context.selected_grade_id
            and selected_class_id == self.current_context.selected_class_id
            and selected_subject_id == self.current_context.selected_subject_id
            and selected_term_id == self.current_context.selected_term_id
        )

    def _schedule_context_apply(self) -> None:
        """Debounces UI selection changes so one burst of combobox edits triggers one live apply."""
        self._cancel_pending_context_apply()
        try:
            self._context_apply_after_id = self.root.after(
                self._context_apply_delay_ms,
                self._run_debounced_context_apply,
            )
        except (tk.TclError, AttributeError):
            self._context_apply_after_id = None
            self.on_apply_selected_context()

    def _run_debounced_context_apply(self) -> None:
        """Runs one delayed context apply if the UI selection is still different from live context."""
        self._context_apply_after_id = None
        if self._suspend_context_events or self._busy or self.current_context is None:
            return
        if self._selected_context_matches_current_context():
            return
        self.on_apply_selected_context()

    def _run_background_task(
        self,
        busy_text: str,
        worker: Callable[[ProgressCallback], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Runs one long browser automation task off the Tk main thread."""
        if self._busy:
            self._log("Đang có tác vụ khác chạy, bỏ qua lệnh mới.")
            return

        self._cancel_pending_context_apply()
        self._foreground_task_token += 1
        self._set_busy(True, busy_text)
        task_token = self._foreground_task_token
        progress_reporter = self._make_progress_reporter(task_token)
        progress_reporter(2.0, busy_text)

        def background_worker() -> None:
            result: object | None = None
            captured_error: Exception | None = None
            try:
                with self._automation_lock:
                    result = worker(progress_reporter)
            except Exception as error:  # noqa: BLE001 - UI thread will handle the result
                captured_error = error
            self._result_queue.put((result, captured_error, on_success, on_error))

        threading.Thread(target=background_worker, daemon=True).start()

    def _run_passive_background_task(
        self,
        worker: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Runs a non-blocking background task that does not freeze the main UI."""

        def background_worker() -> None:
            result: object | None = None
            captured_error: Exception | None = None
            try:
                result = worker()
            except Exception as error:  # noqa: BLE001 - main thread callback will handle the result
                captured_error = error
            self._passive_result_queue.put((result, captured_error, on_success, on_error))

        threading.Thread(target=background_worker, daemon=True).start()

    def _access_scan_block_reason(
        self,
        scan_key: Tuple[str, str, str, str],
        expected_task_token: int,
    ) -> str:
        """Explains why one passive access scan should no longer touch the live UI/browser state."""
        if expected_task_token != self._foreground_task_token:
            return "đã có tác vụ chính mới"
        if self._busy:
            return "đang có tác vụ chính chạy"
        if self.current_context is None:
            return "không còn ngữ cảnh hiện tại"
        current_scan_key = self._access_cache_key(
            self.current_context.selected_grade_id,
            self.current_context.selected_term_id,
        )
        if current_scan_key != scan_key:
            return "ngữ cảnh hiện tại đã đổi"
        return ""

    def _poll_background_results(self) -> None:
        """Processes background worker results back on the Tk main thread."""
        try:
            while True:
                task_token, progress_value, progress_message = self._progress_queue.get_nowait()
                if task_token == self._foreground_task_token and self._busy:
                    self._set_progress(progress_value, progress_message)
        except Empty:
            pass

        try:
            while True:
                result, error, on_success, on_error = self._passive_result_queue.get_nowait()
                try:
                    if error is not None:
                        if on_error is not None:
                            on_error(error)
                    else:
                        on_success(result)
                except Exception as callback_error:  # noqa: BLE001 - passive callback guard
                    self._log(f"Lỗi callback nền: {callback_error}")
        except Empty:
            pass

        try:
            while True:
                result, error, on_success, on_error = self._result_queue.get_nowait()
                self._set_busy(False)
                try:
                    if error is not None:
                        if on_error is not None:
                            on_error(error)
                        self._set_progress(0.0, self.status_var.get() or "Tác vụ thất bại")
                    else:
                        on_success(result)
                        self._set_progress(100.0, self.status_var.get() or "Đã hoàn tất")
                except Exception as callback_error:  # noqa: BLE001 - final guard for Tk callbacks
                    messagebox.showerror("Lỗi nội bộ", str(callback_error))
                    self._log(f"Lỗi callback UI: {callback_error}")
        except Empty:
            pass

        try:
            self._poll_after_id = self.root.after(50, self._poll_background_results)
        except tk.TclError:
            self._poll_after_id = None
            return

    def _render_option_combo(
        self,
        combo: ttk.Combobox,
        variable: tk.StringVar,
        label_map: Dict[str, str],
        options: List[ScoreOption],
        selected_id: str,
        empty_label: str,
    ) -> None:
        """Renders one read-only combobox from typed options."""
        label_map.clear()
        labels: List[str] = []
        for option in options:
            label = option.option_text.strip() or option.option_id
            if label in label_map:
                label = f"{label} ({option.option_id})"
            label_map[label] = option.option_id
            labels.append(label)

        combo["values"] = labels
        target_label = next((label for label, option_id in label_map.items() if option_id == selected_id), "")
        variable.set(target_label or (labels[0] if labels else empty_label))

    def _selected_option_id(self, variable: tk.StringVar, label_map: Dict[str, str]) -> str:
        """Maps the current GUI combobox label back to its underlying option id."""
        return label_map.get(variable.get().strip(), "").strip()

    def _schema_by_key(
        self, schemas: List[ScoreColumnSchema], column_key: str
    ) -> ScoreColumnSchema | None:
        """Finds one parsed schema by key inside the UI layer."""
        column_key = column_key.strip()
        if not column_key:
            return None
        return next((schema for schema in schemas if schema.column_key == column_key), None)

    def _score_source_candidate_schemas(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> List[ScoreColumnSchema]:
        """Returns score-like columns that can be used as the source for comment rules."""
        candidate_keys = list(detected.score_candidate_keys)
        candidate_schemas: List[ScoreColumnSchema] = []
        seen_keys = set()

        for column_key in candidate_keys:
            schema = self._schema_by_key(schemas, column_key)
            if schema is None or schema.column_key in seen_keys:
                continue
            seen_keys.add(schema.column_key)
            candidate_schemas.append(schema)

        fallback_schemas = [
            schema
            for schema in sorted(schemas, key=lambda item: item.leaf_index)
            if schema.role_hint in {"score", "average"}
        ]
        for schema in fallback_schemas:
            if schema.column_key in seen_keys:
                continue
            seen_keys.add(schema.column_key)
            candidate_schemas.append(schema)
        return candidate_schemas

    def _schema_source_label(self, schema: ScoreColumnSchema) -> str:
        """Builds one stable combobox label for a selectable score source column."""
        header_text = " / ".join(
            part.strip()
            for part in schema.header_path
            if part.strip()
        )
        display_name = schema.display_name.strip() or schema.column_key
        if header_text and display_name not in header_text:
            return f"{header_text} [{display_name}]"
        return header_text or display_name

    def _render_score_source_combo(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> None:
        """Populates the score-source combobox from the live parsed schema."""
        selectable_schemas = self._score_source_candidate_schemas(schemas, detected)
        previous_selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)
        self.score_source_label_to_key.clear()
        labels: List[str] = []
        for schema in selectable_schemas:
            label = self._schema_source_label(schema)
            if label in self.score_source_label_to_key:
                label = f"{label} ({schema.column_key})"
            self.score_source_label_to_key[label] = schema.column_key
            labels.append(label)

        self.score_source_combo["values"] = labels
        selected_key = ""
        if self.score_source_label_to_key:
            if previous_selected_key in self.score_source_label_to_key.values():
                selected_key = previous_selected_key
            elif self.preferred_score_source_key.strip() in self.score_source_label_to_key.values():
                selected_key = self.preferred_score_source_key.strip()
            elif detected.preferred_score_column_key.strip() in self.score_source_label_to_key.values():
                selected_key = detected.preferred_score_column_key.strip()
            else:
                selected_key = next(iter(self.score_source_label_to_key.values()), "")

        target_label = next(
            (label for label, column_key in self.score_source_label_to_key.items() if column_key == selected_key),
            "",
        )
        self.score_source_var.set(target_label or "(chưa dò)")
        self.preferred_score_source_key = selected_key

    def _bind_config_autosave_traces(self) -> None:
        """Binds lightweight autosave traces for config-backed UI fields."""
        self.num_forms_var.trace_add("write", self._schedule_config_autosave)
        self.auto_save_var.trace_add("write", self._schedule_config_autosave)
        self.allow_comment_overwrite_var.trace_add("write", self._schedule_config_autosave)
        self.show_password_var.trace_add("write", self._schedule_config_autosave)

    def _schedule_config_autosave(self, *_args: object) -> None:
        """Debounces config writes so rule edits persist without spamming disk writes."""
        if self._suspend_config_autosave:
            return
        if self._config_autosave_after_id is not None:
            self.root.after_cancel(self._config_autosave_after_id)
        self._config_autosave_after_id = self.root.after(700, self._flush_config_autosave)

    def _flush_config_autosave(self) -> None:
        """Writes the current config snapshot to disk for autosave-triggered changes."""
        self._config_autosave_after_id = None
        if self._suspend_config_autosave:
            return
        try:
            self._save_config()
        except Exception as error:  # noqa: BLE001 - autosave should not break the UI
            self._log(f"Lỗi auto-save cấu hình: {error}")

    def _render_detected_columns(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> None:
        """Shows the auto-detected score/comment columns in the GUI."""
        score_schema = self._schema_by_key(schemas, detected.preferred_score_column_key)
        comment_schema = self._schema_by_key(schemas, detected.preferred_comment_column_key)

        self.detected_score_var.set(
            self._schema_source_label(score_schema) if score_schema is not None else "(chưa xác định)"
        )
        self.detected_comment_var.set(
            self._schema_source_label(comment_schema) if comment_schema is not None else "(chưa xác định)"
        )

        candidate_labels = []
        for column_key in detected.score_candidate_keys:
            schema = self._schema_by_key(schemas, column_key)
            if schema is None:
                continue
            candidate_labels.append(self._schema_source_label(schema))
        self.detected_candidates_var.set(", ".join(candidate_labels) if candidate_labels else "(không có)")
        self.detected_reason_var.set(detected.preferred_score_reason or "")

    def _selected_score_source_key(self, context: ScorebookContext) -> str:
        """Returns the user-selected source score column key with safe fallbacks."""
        selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)
        if selected_key and self._schema_by_key(context.column_schemas, selected_key) is not None:
            return selected_key

        detected_key = context.detected_columns.preferred_score_column_key.strip()
        if detected_key and self._schema_by_key(context.column_schemas, detected_key) is not None:
            return detected_key

        candidate_schemas = self._score_source_candidate_schemas(
            context.column_schemas,
            context.detected_columns,
        )
        if candidate_schemas:
            return candidate_schemas[0].column_key
        return ""

    def _log_detected_columns(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> None:
        """Writes the auto-detected score/comment columns to the log panel."""
        score_schema = self._schema_by_key(schemas, detected.preferred_score_column_key)
        comment_schema = self._schema_by_key(schemas, detected.preferred_comment_column_key)
        if score_schema is not None:
            self._log(f"Cột điểm đề xuất: {score_schema.display_name}")
        else:
            self._log("Chưa auto-detect được cột điểm đề xuất.")
        if comment_schema is not None:
            self._log(f"Cột nhận xét đích: {comment_schema.display_name}")
        else:
            self._log("Chưa auto-detect được cột nhận xét.")
        if detected.preferred_score_reason:
            self._log(detected.preferred_score_reason)

    def _seed_default_rules(self, count: int) -> List[CommentRule]:
        """Returns a default rule template set for quick first use."""
        defaults = [
            CommentRule(">=8", "Hoàn thành tốt yêu cầu cần đạt của bộ môn, chủ động, tự giác trong học tập và rèn luyện."),
            CommentRule("6.5-7.9", "Hoàn thành khá tốt nội dung kiến thức đã học, vận dụng được vào bài thực hành, chăm chỉ trong học tập."),
            CommentRule("6-6.4", "Tiếp thu được các kiến thức cơ bản của môn học, có ý thức tự giác, tương đối chủ động trong học tập."),
            CommentRule("5-5.9", "Hoàn thành được các yêu cầu của bộ môn, chủ động hơn trong học tập, tăng cường rèn luyện kỹ năng giải bài tập."),
            CommentRule("<5", "Chưa hoàn thành các yêu cầu cần đạt của bộ môn, còn thụ động, tăng cường luyện tập kỹ năng thực hành."),
            CommentRule("Đ", "Hoàn thành tốt yêu cầu cần đạt của bộ môn, chủ động, tự giác trong học tập và rèn luyện."),
            CommentRule("CĐ", "Chưa hoàn thành tốt nội dung kiến thức môn học."),
        ]
        return defaults[:count]

    def _sanitize_rule_filename_part(self, value: str) -> str:
        """Converts one rule label into a Windows-safe filename fragment."""
        sanitized = value.strip()
        replacements = (
            (">=", "ge_"),
            ("<=", "le_"),
            (">", "gt_"),
            ("<", "lt_"),
            ("=", "eq_"),
        )
        for old, new in replacements:
            sanitized = sanitized.replace(old, new)
        sanitized = re.sub(r'[<>:"/\\\\|?*]+', "_", sanitized)
        sanitized = re.sub(r"\s+", "_", sanitized)
        sanitized = sanitized.strip("._")
        return sanitized or "rule"

    def _write_default_rule_files(self, target_dir: Path | None = None) -> List[Path]:
        """Writes the built-in default rule set into seven plain-text files."""
        export_dir = target_dir or DEFAULT_RULE_EXPORT_DIR
        export_dir.mkdir(parents=True, exist_ok=True)

        rules = self._seed_default_rules(7)
        created_files: List[Path] = []
        for index, rule in enumerate(rules, start=1):
            filename = f"{index:02d}_{self._sanitize_rule_filename_part(rule.condition)}.txt"
            file_path = export_dir / filename
            content = (
                f"STT: {index}\n"
                f"Điều kiện: {rule.condition}\n"
                "Mẫu nhận xét:\n"
                f"{rule.template}\n"
            )
            file_path.write_text(content, encoding="utf-8")
            created_files.append(file_path)
        return created_files

    def _build_rule_forms(self, count: int, initial_rules: List[CommentRule] | None = None) -> None:
        """Rebuilds the rule form list inside the scrollable rule panel."""
        if self._rule_busy_widgets:
            self._busy_widgets = [
                item for item in self._busy_widgets if item[0] not in self._rule_busy_widgets
            ]
            self._rule_busy_widgets = []
        for widget in self.rule_form_container.winfo_children():
            widget.destroy()
        self.score_forms = []

        header = ttk.Frame(self.rule_form_container)
        header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(header, text="STT", width=6).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(header, text="Điều kiện", width=18).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(header, text="Mẫu nhận xét", anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        seeded_rules = list(initial_rules or self._seed_default_rules(count))
        for index in range(count):
            row_frame = ttk.Frame(self.rule_form_container)
            row_frame.pack(fill=tk.X, pady=2)
            ttk.Label(row_frame, text=f"{index + 1}", width=6).pack(side=tk.LEFT, padx=(0, 4))

            condition_var = tk.StringVar()
            template_var = tk.StringVar()
            condition_entry = ttk.Entry(row_frame, textvariable=condition_var, width=18)
            template_entry = ttk.Entry(row_frame, textvariable=template_var)
            condition_entry.pack(side=tk.LEFT, padx=(0, 4))
            template_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self.score_forms.append({"condition": condition_var, "template": template_var})
            self._busy_widgets.append((condition_entry, "normal"))
            self._busy_widgets.append((template_entry, "normal"))
            self._rule_busy_widgets.extend([condition_entry, template_entry])
            condition_var.trace_add("write", self._schedule_config_autosave)
            template_var.trace_add("write", self._schedule_config_autosave)

            if index < len(seeded_rules):
                condition_var.set(seeded_rules[index].condition)
                template_var.set(seeded_rules[index].template)

    def on_build_rule_forms(self) -> None:
        """Rebuilds the rule forms from the requested form count."""
        existing_rules = self._collect_comment_rules()
        try:
            count = int(self.num_forms_var.get().strip())
        except ValueError:
            messagebox.showwarning("Thiếu rule", "Số rule phải là số nguyên hợp lệ.")
            self._log("Bỏ qua tạo form rule vì số lượng rule không hợp lệ.")
            return
        if count < 1 or count > 20:
            messagebox.showwarning("Thiếu rule", "Số rule phải nằm trong khoảng 1-20.")
            self._log("Bỏ qua tạo form rule vì số lượng nằm ngoài khoảng 1-20.")
            return
        self._build_rule_forms(count, initial_rules=existing_rules)
        self._schedule_config_autosave()
        self._log(f"Đã tạo {count} form rule.")

    def on_export_default_rules(self) -> None:
        """Restores the built-in default rule set and clears any saved custom config."""
        default_count = 7
        default_rules = self._seed_default_rules(default_count)
        try:
            if self._config_autosave_after_id is not None:
                self.root.after_cancel(self._config_autosave_after_id)
            self._config_autosave_after_id = None
            self._suspend_config_autosave = True
            self.num_forms_var.set(str(default_count))
            self._build_rule_forms(default_count, initial_rules=default_rules)
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
        except Exception as error:  # noqa: BLE001 - user-facing reset action
            messagebox.showerror("Lỗi nhận xét mặc định", str(error))
            self._log(f"Lỗi khôi phục nhận xét mặc định: {error}")
            return
        finally:
            self._suspend_config_autosave = False

        self._log("Đã khôi phục 7 rule nhận xét mặc định trong GUI.")
        self._log("Đã xóa file cấu hình tùy biến; lần mở sau app sẽ dùng lại rule mặc định cho đến khi bạn chỉnh sửa.")
        messagebox.showinfo(
            "Nhận xét mặc định",
            (
                "Đã khôi phục 7 rule nhận xét mặc định.\n"
                "Config tùy biến cũ đã được xóa. Khi bạn sửa rule sau đó, app sẽ tự tạo lại config."
            ),
        )

    def _collect_comment_rules(self) -> List[CommentRule]:
        """Collects the current rule form values from the GUI."""
        return [
            CommentRule(
                condition=form["condition"].get().strip(),
                template=form["template"].get().strip(),
            )
            for form in self.score_forms
        ]

    def _warn_rule_overlap(self, rules: List[CommentRule]) -> None:
        """Warns when numeric test points match more than one rule."""
        compiled_rules = compile_comment_rules(rules)
        if not compiled_rules:
            return
        overlaps: List[str] = []
        for probe in [0, 1, 2, 3, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10]:
            matched_conditions = []
            for checker, _template, condition in compiled_rules:
                try:
                    if checker(probe):
                        matched_conditions.append(condition)
                except TypeError:
                    continue
            if len(matched_conditions) > 1:
                overlaps.append(f"{probe}: {', '.join(matched_conditions)}")
        if overlaps:
            messagebox.showwarning(
                "Cảnh báo rule chồng lấn",
                "Một số điểm khớp nhiều rule. App sẽ lấy rule đầu tiên khớp từ trên xuống.\n\n"
                + "\n".join(overlaps[:6]),
            )

    def _validate_comment_rules(self) -> List[CommentRule]:
        """Validates the current rule forms and returns typed rules."""
        rules = self._collect_comment_rules()
        if not rules:
            raise RuntimeError("Chưa có form rule nào.")

        for index, rule in enumerate(rules, start=1):
            if not rule.condition.strip():
                raise RuntimeError(f"Rule {index}: chưa nhập điều kiện.")
            if not rule.template.strip():
                raise RuntimeError(f"Rule {index}: chưa nhập mẫu nhận xét.")
            if parse_condition(rule.condition) is None:
                raise RuntimeError(
                    f"Rule {index}: điều kiện `{rule.condition}` không hợp lệ ({describe_condition(rule.condition)})."
                )

        self._warn_rule_overlap(rules)
        return rules

    def _write_status_label(self, status: str) -> str:
        """Maps internal write-queue status codes to short Vietnamese labels."""
        return {
            "ready": "Sẵn sàng",
            "skip_no_score": "Chưa có điểm",
            "skip_comment_score": "Đã có điểm nhận xét",
            "skip_existing_comment": "Đã có nhận xét chữ",
            "skip_unmatched": "Không khớp rule",
            "skip_same": "Đã giống",
        }.get(status, status)

    def _write_status_counts(self, rows: List[CommentWriteRow]) -> Dict[str, int]:
        """Aggregates analyzed write rows by internal status code."""
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def _warn_students_with_numeric_comment_scores(
        self,
        rows: List[CommentWriteRow],
        allow_overwrite_existing_comment: bool,
    ) -> None:
        """Shows a warning when some comment cells already contain numeric score values."""
        if allow_overwrite_existing_comment:
            return
        blocked_rows = [row for row in rows if row.status == "skip_comment_score"]
        if not blocked_rows:
            return

        blocked_lines = [
            f"{row.student_name or '(không rõ tên)'}"
            f"{f' ({row.student_code})' if row.student_code else ''}: {row.current_comment}"
            for row in blocked_rows[:12]
        ]
        more_count = len(blocked_rows) - len(blocked_lines)
        message = "Tên học sinh đã có điểm nhận xét:\n" + "\n".join(blocked_lines)
        if more_count > 0:
            message += f"\n... và thêm {more_count} học sinh khác."
        messagebox.showwarning("Đã có điểm nhận xét", message)

    def _context_has_comment_permission(self, context: ScorebookContext) -> bool:
        """Returns whether the current scorebook context allows comment editing."""
        normalized_permission = context.permission_text.strip().lower()
        if "không có quyền" in normalized_permission or "khong co quyen" in normalized_permission:
            return False
        return context.enabled_comment_input_count > 0

    def _schedule_access_scan_for_context(
        self,
        context: ScorebookContext,
        reason: str,
    ) -> None:
        """Starts a passive permission scan for the current grade-term so filtered lists can update later."""
        if not getattr(self, "_matrix_access_scan_enabled", True):
            return
        self._sync_access_cache_identity()
        scan_key = self._access_cache_key(context.selected_grade_id, context.selected_term_id)
        scan_grade_id = context.selected_grade_id.strip()
        scan_term_id = context.selected_term_id.strip()
        if not scan_grade_id or not scan_term_id:
            return
        if scan_key in self.access_entry_cache:
            return
        if scan_key in self._access_scan_inflight:
            return

        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001 - background warmup should stay non-blocking
            self._log(f"Bỏ qua dò quyền nền: {error}")
            return

        username = self.username_var.get().strip()
        password = self.password_var.get()
        expected_task_token = self._foreground_task_token
        self._access_scan_inflight.add(scan_key)
        self._log(
            f"Đang dò quyền lớp/môn nền cho khối {context.selected_grade_id}, học kỳ {context.selected_term_id}"
            f" ({reason})..."
        )

        def worker() -> object:
            if self._access_scan_block_reason(scan_key, expected_task_token):
                return PASSIVE_SCAN_SKIPPED
            if not self._automation_lock.acquire(blocking=False):
                return PASSIVE_SCAN_SKIPPED
            try:
                if self._access_scan_block_reason(scan_key, expected_task_token):
                    return PASSIVE_SCAN_SKIPPED
                return automation.discover_accessible_entries_for_current_context(
                    username=username,
                    password=password,
                    expected_grade_id=scan_grade_id,
                    expected_term_id=scan_term_id,
                )
            finally:
                self._automation_lock.release()

        def on_success(result: object) -> None:
            self._access_scan_inflight.discard(scan_key)
            skip_reason = self._access_scan_block_reason(scan_key, expected_task_token)
            if result is PASSIVE_SCAN_SKIPPED:
                if skip_reason:
                    self._log(
                        f"Bỏ qua dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."
                    )
                return
            if skip_reason:
                self._log(
                    f"Bỏ qua cập nhật kết quả dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."
                )
                return
            scanned_context, entries, login_message = result if isinstance(result, tuple) else (None, None, "")
            if not isinstance(scanned_context, ScorebookContext) or not isinstance(entries, list):
                raise RuntimeError("Không nhận được dữ liệu quét quyền nền hợp lệ.")
            if login_message:
                self._log(login_message)
            if not entries:
                self._log(
                    "Dò quyền nền chưa trả về ma trận lớp/môn đầy đủ; "
                    "app giữ danh sách live hiện tại và không cache kết quả rỗng."
                )
            else:
                self.access_entry_cache[scan_key] = list(entries)
                self._log(
                    f"Đã cập nhật nền quyền lớp/môn cho khối {scan_grade_id}, học kỳ {scan_term_id}: "
                    f"{len(entries)} tổ hợp hợp lệ."
                )
            if self.current_context is None:
                return
            if self._access_scan_block_reason(scan_key, expected_task_token):
                return

            scanned_context.selected_grade_id = self.current_context.selected_grade_id
            scanned_context.selected_class_id = self.current_context.selected_class_id
            scanned_context.selected_subject_id = self.current_context.selected_subject_id
            scanned_context.selected_term_id = self.current_context.selected_term_id
            if entries:
                scanned_context = apply_access_entries_to_context(
                    scanned_context,
                    entries,
                    grade_id=scan_grade_id,
                    term_id=scan_term_id,
                )
            self._apply_scorebook_context(scanned_context)

        def on_error(error: Exception) -> None:
            self._access_scan_inflight.discard(scan_key)
            self._log(f"Lỗi dò quyền nền: {error}")

        def launch_passive_scan() -> None:
            if scan_key not in self._access_scan_inflight:
                return
            skip_reason = self._access_scan_block_reason(scan_key, expected_task_token)
            if skip_reason:
                self._access_scan_inflight.discard(scan_key)
                self._log(
                    f"Bỏ qua khởi động dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."
                )
                return
            self._run_passive_background_task(worker=worker, on_success=on_success, on_error=on_error)

        try:
            self.root.after(self._passive_scan_delay_ms, launch_passive_scan)
        except tk.TclError:
            self._access_scan_inflight.discard(scan_key)
            return

    def _selected_scorebook_context_ids(self) -> Tuple[str, str, str, str]:
        """Reads the currently selected scorebook ids from the GUI with safe fallbacks."""
        if self.current_context is None:
            raise RuntimeError("Chưa có ScorebookContext để xác định ngữ cảnh hiện tại.")

        grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id) or self.current_context.selected_grade_id
        class_id = self._selected_option_id(self.class_var, self.class_label_to_id) or self.current_context.selected_class_id
        subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id) or self.current_context.selected_subject_id
        term_id = self._selected_option_id(self.term_var, self.term_label_to_id) or self.current_context.selected_term_id

        if not grade_id or not class_id or not subject_id or not term_id:
            raise RuntimeError("Ngữ cảnh Khối/Lớp/Môn/Học kỳ chưa đầy đủ.")
        return grade_id, class_id, subject_id, term_id

    def _resolve_selected_columns(
        self,
        context: ScorebookContext,
    ) -> Tuple[ScoreColumnSchema, ScoreColumnSchema]:
        """Returns the selected source score column and detected target comment column."""
        source_column_key = self._selected_score_source_key(context)
        detected = context.detected_columns
        source_schema = self._schema_by_key(context.column_schemas, source_column_key)
        comment_schema = self._schema_by_key(context.column_schemas, detected.preferred_comment_column_key)
        if source_schema is None:
            raise RuntimeError("Chưa chọn được cột điểm nguồn hợp lệ trên bảng điểm hiện tại.")
        if comment_schema is None:
            raise RuntimeError("Chưa tự nhận diện được cột nhận xét đích trên bảng điểm hiện tại.")
        return source_schema, comment_schema

    def _background_credentials(self) -> Tuple[str, str]:
        """Returns the current username/password pair for background automation calls."""
        return self.username_var.get().strip(), self.password_var.get()

    def _collect_comment_apply_request(self) -> Dict[str, object]:
        """Collects the UI state required for one apply-comments run."""
        if self.current_context is None:
            raise RuntimeError("Hãy đọc Sổ điểm trước khi ghi nhận xét.")

        automation = self._build_automation()
        rules = self._validate_comment_rules()
        grade_id, class_id, subject_id, term_id = self._selected_scorebook_context_ids()
        source_schema, comment_schema = self._resolve_selected_columns(self.current_context)
        username, password = self._background_credentials()
        return {
            "automation": automation,
            "rules": rules,
            "grade_id": grade_id,
            "class_id": class_id,
            "subject_id": subject_id,
            "term_id": term_id,
            "source_column_key": source_schema.column_key,
            "comment_column_key": comment_schema.column_key,
            "username": username,
            "password": password,
            "auto_save": bool(self.auto_save_var.get()),
            "allow_overwrite_existing_comment": bool(self.allow_comment_overwrite_var.get()),
        }

    def _run_comment_apply(self, request: Dict[str, object]) -> object:
        """Executes one full analyze-and-apply cycle for the current UI rule set."""
        automation = request["automation"]
        return automation.analyze_and_apply_comment_rows(
            grade_id=request["grade_id"],
            class_id=request["class_id"],
            subject_id=request["subject_id"],
            term_id=request["term_id"],
            source_column_key=request["source_column_key"],
            comment_column_key=request["comment_column_key"],
            rules=request["rules"],
            username=request["username"],
            password=request["password"],
            auto_save=bool(request["auto_save"]),
            allow_overwrite_existing_comment=bool(request["allow_overwrite_existing_comment"]),
            progress_callback=request.get("progress_callback"),
        )

    def _handle_comment_apply_success(
        self,
        result: object,
        auto_save: bool,
        allow_overwrite_existing_comment: bool,
    ) -> None:
        """Handles the UI/logging side after one apply-comments run completes."""
        (
            queue_context,
            write_rows,
            queue_login_message,
            queue_selection_message,
            apply_context,
            apply_result,
            apply_login_message,
            apply_selection_message,
        ) = result if isinstance(result, tuple) else (None, None, "", "", None, None, "", "")
        if (
            not isinstance(queue_context, ScorebookContext)
            or not isinstance(write_rows, list)
            or not isinstance(apply_context, ScorebookContext)
            or (apply_result is not None and not isinstance(apply_result, CommentWriteResult))
        ):
            raise RuntimeError("Không nhận được kết quả ghi nhận xét hợp lệ từ live Chrome.")
        if queue_login_message:
            self._log(queue_login_message)
        if queue_selection_message:
            self._log(queue_selection_message)
        counts = self._write_status_counts(write_rows)
        self._warn_students_with_numeric_comment_scores(
            write_rows,
            allow_overwrite_existing_comment=allow_overwrite_existing_comment,
        )
        self._log(
            "Đã phân tích dữ liệu trước khi ghi: "
            f"{len(write_rows)} dòng, "
            f"{counts.get('ready', 0)} sẵn sàng, "
            f"{counts.get('skip_no_score', 0)} chưa có điểm, "
            f"{counts.get('skip_existing_comment', 0)} đã có nhận xét chữ, "
            f"{counts.get('skip_unmatched', 0)} không khớp rule, "
            f"{counts.get('skip_same', 0)} đã giống."
        )
        if apply_result is None:
            self._apply_scorebook_context(apply_context)
            no_ready_lines = [
                "Không có học sinh nào sẵn sàng để ghi nhận xét.",
            ]
            if counts.get("skip_existing_comment", 0) and not allow_overwrite_existing_comment:
                no_ready_lines.append(
                    "Các ô đã có nhận xét chữ đang được giữ nguyên. "
                    "Muốn thay thế có chủ đích, hãy bật 'Cho phép ghi đè nhận xét chữ đã có'."
                )
            no_ready_lines.append(
                "Hãy kiểm tra lại rule, cột điểm nguồn, hoặc các ô nhận xét chữ đã có."
            )
            messagebox.showwarning(
                "Không có dòng để ghi",
                "\n".join(no_ready_lines),
            )
            self._log("Không có dòng nào sẵn sàng để ghi sau khi phân tích dữ liệu.")
            self._set_status_text("Không có dòng để ghi")
            return
        if apply_login_message:
            self._log(apply_login_message)
        if apply_selection_message:
            self._log(apply_selection_message)
        self._apply_scorebook_context(apply_context)
        if not apply_result.save_clicked and auto_save:
            self._log("Không tìm thấy nút Lưu để bấm tự động; dữ liệu mới chỉ được xác minh trong ô nhập hiện tại.")
        if auto_save and apply_result.save_clicked:
            if apply_result.save_verified:
                self._log(apply_result.save_verification_detail or "Đã xác minh Lưu thành công ở mức server-side.")
            else:
                self._log(
                    "Cảnh báo xác minh Lưu: "
                    + (apply_result.save_verification_detail or "Đã bấm Lưu nhưng chưa xác minh được phản hồi server.")
                )
        if not auto_save:
            self._log("Đã ghi vào ô nhận xét nhưng chưa bấm Lưu tự động; cần kiểm tra và lưu thủ công nếu VNEDU yêu cầu.")

        failed_suffix = ""
        if apply_result.failed_rows:
            failed_suffix = f" Lỗi/không xác minh được: {', '.join(apply_result.failed_rows[:5])}"
            if len(apply_result.failed_rows) > 5:
                failed_suffix += f" ... (+{len(apply_result.failed_rows) - 5})"
        self._log(
            "Kết quả ghi nhận xét: "
            f"đã thử {apply_result.attempted}, "
            f"điền vào {apply_result.updated}, "
            f"xác minh {apply_result.verified}, "
            f"bỏ qua {apply_result.skipped}."
            f"{failed_suffix}"
        )
        messagebox.showinfo(
            "Đã ghi nhận xét",
            (
                f"Đã thử ghi {apply_result.attempted} dòng.\n"
                f"Xác minh thành công: {apply_result.verified}\n"
                f"Bỏ qua: {apply_result.skipped}\n"
                f"Tự bấm Lưu: {'Có' if apply_result.save_clicked else 'Không'}\n"
                f"Xác minh Lưu server-side: {'Có' if apply_result.save_verified else 'Không'}"
            ),
        )

    def _handle_comment_apply_error(self, error: Exception) -> None:
        """Handles one apply-comments failure on the UI thread."""
        messagebox.showerror("Lỗi ghi nhận xét", str(error))
        self._log(f"Lỗi ghi nhận xét: {error}")
        self._set_status_text("Ghi nhận xét thất bại")

    def on_apply_comments(self) -> None:
        """Builds the write queue internally and writes comments back into the live scorebook."""
        if self.current_context is None:
            messagebox.showwarning("Chưa có dữ liệu", "Hãy đọc Sổ điểm trước khi ghi nhận xét.")
            self._log("Bỏ qua ghi nhận xét vì chưa có ScorebookContext.")
            return

        try:
            request = self._collect_comment_apply_request()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi ghi nhận xét", str(error))
            self._log(f"Lỗi ghi nhận xét: {error}")
            return

        self._run_background_task(
            "Đang phân tích dữ liệu và ghi nhận xét lên live Chrome...",
            worker=lambda progress: self._run_comment_apply(
                {
                    **request,
                    "progress_callback": progress,
                }
            ),
            on_success=lambda result: self._handle_comment_apply_success(
                result,
                auto_save=bool(request["auto_save"]),
                allow_overwrite_existing_comment=bool(request["allow_overwrite_existing_comment"]),
            ),
            on_error=self._handle_comment_apply_error,
        )

    def _normalize_access_cache_url(self, target_url: str) -> str:
        """Normalizes the VNEDU target URL so equivalent session URLs share one cache namespace."""
        normalized_url = target_url.strip()
        if not normalized_url:
            return ""
        parsed = urlparse(normalized_url)
        if parsed.scheme or parsed.netloc:
            normalized_path = parsed.path.rstrip("/")
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{normalized_path}"
        return normalized_url.rstrip("/").lower()

    def _current_access_cache_identity(self) -> Tuple[str, str]:
        """Returns the current in-memory cache identity derived from URL and username fields."""
        current_url = self._normalize_access_cache_url(self.url_var.get())
        current_username = self.username_var.get().strip()
        return current_url, current_username

    def _sync_access_cache_identity(self) -> Tuple[str, str]:
        """Clears stale permission cache state when the active VNEDU URL or username changes."""
        current_identity = self._current_access_cache_identity()
        previous_identity = self._access_cache_identity
        if previous_identity is None:
            self._access_cache_identity = current_identity
            return current_identity
        if previous_identity != current_identity:
            self.access_entry_cache.clear()
            self._access_scan_inflight.clear()
            self._access_cache_identity = current_identity
        return current_identity

    def _access_cache_key(self, grade_id: str, term_id: str) -> Tuple[str, str, str, str]:
        """Builds the cache key for one scanned grade-term permission matrix within one VNEDU session identity."""
        current_url, current_username = self._access_cache_identity or ("", "")
        return current_url, current_username, grade_id.strip(), term_id.strip()

    def _cache_context_access_entries(self, context: ScorebookContext) -> None:
        """Stores discovered permission entries so grade-term scans can be reused later."""
        if not getattr(self, "_matrix_access_scan_enabled", True):
            return
        self._sync_access_cache_identity()
        if not context.accessible_entries:
            return
        cache_grade_id = context.accessible_grade_id or context.selected_grade_id
        cache_term_id = context.accessible_term_id or context.selected_term_id
        if not cache_grade_id or not cache_term_id:
            return
        cache_key = self._access_cache_key(
            cache_grade_id,
            cache_term_id,
        )
        self.access_entry_cache[cache_key] = list(context.accessible_entries)

    def _with_cached_access_entries(self, context: ScorebookContext) -> ScorebookContext:
        """Reattaches cached permission entries to a plain scorebook context when possible."""
        if not getattr(self, "_matrix_access_scan_enabled", True):
            return context
        self._sync_access_cache_identity()
        if context.accessible_entries:
            return context
        cache_key = self._access_cache_key(context.selected_grade_id, context.selected_term_id)
        if cache_key in self.access_entry_cache:
            cached_entries = self.access_entry_cache.get(cache_key, [])
            if cached_entries:
                context.class_options = merge_score_options(
                    context.class_options,
                    class_options_from_access_entries(cached_entries),
                )
                context.subject_options = merge_score_options(
                    context.subject_options,
                    subject_options_from_access_entries(cached_entries),
                )
                context.accessible_entries = list(cached_entries)
                context.accessible_grade_id = context.selected_grade_id
                context.accessible_term_id = context.selected_term_id
        return context

    def _build_minimal_access_entries_from_context(self, context: ScorebookContext) -> List[ScorebookAccessEntry]:
        """Builds a one-item permission fallback from the current live context when it is clearly writable."""
        if not self._context_has_comment_permission(context):
            return []

        grade_id = context.selected_grade_id.strip()
        class_id = context.selected_class_id.strip()
        subject_id = context.selected_subject_id.strip()
        term_id = context.selected_term_id.strip()
        if not grade_id or not class_id or not subject_id or not term_id:
            return []

        grade_text = next(
            (option.option_text for option in context.grade_options if option.option_id == grade_id),
            grade_id,
        )
        class_text = next(
            (option.option_text for option in context.class_options if option.option_id == class_id),
            class_id,
        )
        subject_text = next(
            (option.option_text for option in context.subject_options if option.option_id == subject_id),
            subject_id,
        )
        term_text = next(
            (option.option_text for option in context.term_options if option.option_id == term_id),
            term_id,
        )

        return [
            ScorebookAccessEntry(
                grade_id=grade_id,
                grade_text=grade_text,
                class_id=class_id,
                class_text=class_text,
                subject_id=subject_id,
                subject_text=subject_text,
                term_id=term_id,
                term_text=term_text,
                teacher_text=context.teacher_text,
                permission_text=context.permission_text,
                comment_input_count=context.comment_input_count,
                enabled_comment_input_count=context.enabled_comment_input_count,
            )
        ]

    def _accessible_class_options(self, context: ScorebookContext) -> List[ScoreOption]:
        """Returns only classes that appear in the discovered permission matrix."""
        if not context.accessible_entries and not (context.accessible_grade_id or context.accessible_term_id):
            return list(context.class_options)
        if not context.accessible_entries:
            return []

        allowed_ids = {
            entry.class_id.strip()
            for entry in context.accessible_entries
            if entry.class_id.strip()
        }
        if not allowed_ids:
            return []

        return merge_score_options(
            [option for option in context.class_options if option.option_id.strip() in allowed_ids],
            class_options_from_access_entries(context.accessible_entries),
        )

    def _accessible_subject_options(self, context: ScorebookContext, class_id: str) -> List[ScoreOption]:
        """Returns only subjects that are permitted for the selected class."""
        if not context.accessible_entries and not (context.accessible_grade_id or context.accessible_term_id):
            return list(context.subject_options)
        if not context.accessible_entries:
            return []

        normalized_class_id = class_id.strip()
        candidate_entries = [
            entry
            for entry in context.accessible_entries
            if not normalized_class_id or entry.class_id.strip() == normalized_class_id
        ]
        if not candidate_entries:
            candidate_entries = list(context.accessible_entries)

        allowed_ids = {
            entry.subject_id.strip()
            for entry in candidate_entries
            if entry.subject_id.strip()
        }
        if not allowed_ids:
            return []

        return merge_score_options(
            [option for option in context.subject_options if option.option_id.strip() in allowed_ids],
            subject_options_from_access_entries(candidate_entries, class_id=normalized_class_id),
        )

    def _normalize_context_for_ui_scope(self, context: ScorebookContext) -> ScorebookContext:
        """Keeps the UI bound to the exact live context instead of any broader permission matrix."""
        if getattr(self, "_matrix_access_scan_enabled", True):
            return context
        context.accessible_entries = []
        context.accessible_grade_id = ""
        context.accessible_term_id = ""
        return context

    def _apply_scorebook_context(self, context: ScorebookContext) -> None:
        """Hydrates the GUI shell from the latest scorebook snapshot."""
        context = self._normalize_context_for_ui_scope(context)
        context = self._with_cached_access_entries(context)
        self._cache_context_access_entries(context)
        permission_matrix_ready = bool(
            context.accessible_entries
            or context.accessible_grade_id
            or context.accessible_term_id
        )
        accessible_class_options = self._accessible_class_options(context)
        selected_class_ids = {option.option_id for option in accessible_class_options}
        if accessible_class_options and context.selected_class_id not in selected_class_ids:
            context.selected_class_id = accessible_class_options[0].option_id

        accessible_subject_options = self._accessible_subject_options(context, context.selected_class_id)
        selected_subject_ids = {option.option_id for option in accessible_subject_options}
        if accessible_subject_options and context.selected_subject_id not in selected_subject_ids:
            context.selected_subject_id = accessible_subject_options[0].option_id

        self.current_context = context
        self._render_detected_columns(context.column_schemas, context.detected_columns)
        self._suspend_context_events = True
        try:
            self._render_score_source_combo(context.column_schemas, context.detected_columns)
            self._render_option_combo(
                self.grade_combo,
                self.grade_var,
                self.grade_label_to_id,
                context.grade_options,
                context.selected_grade_id,
                "(chưa đọc)",
            )
            self._render_option_combo(
                self.class_combo,
                self.class_var,
                self.class_label_to_id,
                accessible_class_options,
                context.selected_class_id,
                "(chưa đọc)",
            )
            self._render_option_combo(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                accessible_subject_options,
                context.selected_subject_id,
                "(chưa đọc)",
            )
            self._render_option_combo(
                self.term_combo,
                self.term_var,
                self.term_label_to_id,
                context.term_options,
                context.selected_term_id,
                "(chưa đọc)",
            )
        finally:
            self._suspend_context_events = False
        class_wording = "lớp có quyền" if permission_matrix_ready else "lớp hiện có"
        subject_wording = "môn có quyền" if permission_matrix_ready else "môn hiện có"
        window_title = context.window_title or "Sổ điểm"
        self._set_status_text(
            f"Đã đọc ngữ cảnh {window_title}: "
            f"{len(context.grade_options)} khối, "
            f"{len(accessible_class_options)} {class_wording}, "
            f"{len(accessible_subject_options)} {subject_wording}, "
            f"{len(context.term_options)} học kỳ, "
            f"{len(context.column_schemas)} cột."
        )

    def _save_config(self) -> None:
        """Persists session fields together with the current rule preferences."""
        payload = {
            "debug_port": self.port_var.get().strip(),
            "target_url": self.url_var.get().strip(),
            "username": self.username_var.get().strip(),
            "num_forms": self.num_forms_var.get().strip(),
            "auto_save": bool(self.auto_save_var.get()),
            "allow_comment_overwrite": bool(self.allow_comment_overwrite_var.get()),
            "show_password": bool(self.show_password_var.get()),
            "preferred_score_source_key": self._selected_option_id(
                self.score_source_var,
                self.score_source_label_to_key,
            ) or self.preferred_score_source_key,
            "rules": [
                {
                    "condition": form["condition"].get().strip(),
                    "template": form["template"].get().strip(),
                }
                for form in self.score_forms
                if form["condition"].get().strip() or form["template"].get().strip()
            ],
        }
        with CONFIG_FILE.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _load_config(self) -> None:
        """Loads session fields and restores the last saved rule form layout."""
        default_count = 7
        default_rules = self._seed_default_rules(default_count)
        if not CONFIG_FILE.exists():
            self.num_forms_var.set(str(default_count))
            self._build_rule_forms(default_count, initial_rules=default_rules)
            return
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as error:  # noqa: BLE001 - config corruption should not stop app startup
            self._log(f"Không tải được cấu hình V2: {error}")
            self.num_forms_var.set(str(default_count))
            self._build_rule_forms(default_count, initial_rules=default_rules)
            return

        self.port_var.set(str(payload.get("debug_port", self.port_var.get())))
        self.url_var.set(str(payload.get("target_url", self.url_var.get())))
        self.username_var.set(str(payload.get("username", self.username_var.get())))
        self.auto_save_var.set(bool(payload.get("auto_save", self.auto_save_var.get())))
        self.allow_comment_overwrite_var.set(
            bool(payload.get("allow_comment_overwrite", self.allow_comment_overwrite_var.get()))
        )
        self.show_password_var.set(bool(payload.get("show_password", self.show_password_var.get())))
        self._apply_password_visibility()
        self.preferred_score_source_key = str(payload.get("preferred_score_source_key", "")).strip()

        try:
            rule_count = int(str(payload.get("num_forms", self.num_forms_var.get())).strip())
        except ValueError:
            rule_count = len(payload.get("rules", payload.get("forms", []))) or default_count
        rule_count = min(max(rule_count, 1), 20)
        self.num_forms_var.set(str(rule_count))

        raw_rules = payload.get("rules", payload.get("forms", []))
        loaded_rules: List[CommentRule] = []
        if isinstance(raw_rules, list):
            for item in raw_rules:
                if not isinstance(item, dict):
                    continue
                loaded_rules.append(
                    CommentRule(
                        condition=str(item.get("condition", "")).strip(),
                        template=str(item.get("template", "")).strip(),
                    )
                )
        self._build_rule_forms(rule_count, initial_rules=(loaded_rules or default_rules[:rule_count]))

    def on_save_config(self) -> None:
        """Saves the current CDP/session shell fields to disk."""
        try:
            self._save_config()
        except Exception as error:  # noqa: BLE001 - user-facing save command
            messagebox.showerror("Lỗi lưu", str(error))
            self._log(f"Lỗi lưu cấu hình: {error}")
            return
        messagebox.showinfo("Đã lưu", f"Đã lưu cấu hình vào {CONFIG_FILE}")
        self._log(f"Đã lưu cấu hình vào {CONFIG_FILE}")

    def on_open_web(self) -> None:
        """Opens the VNEDU target URL in the connected browser session."""
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi mở web", str(error))
            self._log(f"Lỗi mở web: {error}")
            return

        self._run_background_task(
            "Đang mở VNEDU qua live Chrome...",
            worker=lambda progress: automation.open_target_page(progress_callback=progress),
            on_success=lambda result: (
                self._set_status_text(f"Đã mở trang: {result}"),
                self._log(f"Đã mở VNEDU: {result}"),
            ),
            on_error=lambda error: (
                messagebox.showerror("Lỗi mở web", str(error)),
                self._log(f"Lỗi mở web: {error}"),
                self._set_status_text("Lỗi mở VNEDU"),
            ),
        )

    def _handle_load_scorebook_shell_success(self, result: object) -> None:
        """Applies one freshly loaded live scorebook context onto the UI."""
        context, login_message = result if isinstance(result, tuple) else (None, "")
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ.")
        if login_message:
            self._log(login_message)
        self._apply_scorebook_context(context)
        if context.permission_text:
            self._log(context.permission_text)
        if context.teacher_text:
            self._log(context.teacher_text)
        self._log_detected_columns(context.column_schemas, context.detected_columns)
        self._log("Đã đọc nhanh khung Khối/Lớp/Môn/Học kỳ từ live Chrome.")

    def _handle_load_scorebook_shell_error(self, error: Exception) -> None:
        """Handles one scorebook-shell load failure on the UI thread."""
        messagebox.showerror("Lỗi đọc Sổ điểm", str(error))
        self._log(f"Lỗi đọc Sổ điểm: {error}")
        self._set_status_text("Chưa đọc được Sổ điểm")

    def on_load_scorebook_shell(self) -> None:
        """Logs in if needed, then reads the scorebook shell from the live browser."""
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi đọc Sổ điểm", str(error))
            self._log(f"Lỗi đọc Sổ điểm: {error}")
            return

        username, password = self._background_credentials()

        self._run_background_task(
            "Đang đăng nhập và đọc nhanh Sổ điểm...",
            worker=lambda progress: automation.load_scorebook_context(
                username=username,
                password=password,
                progress_callback=progress,
            ),
            on_success=self._handle_load_scorebook_shell_success,
            on_error=self._handle_load_scorebook_shell_error,
        )

    def on_context_selection_changed(self, _event: tk.Event | None = None) -> None:
        """Auto-applies context changes when the user picks a different combobox value."""
        if self._suspend_context_events or self._busy or self.current_context is None:
            return

        if self._selected_context_matches_current_context():
            self._cancel_pending_context_apply()
            return

        self._schedule_context_apply()

    def on_score_source_selection_changed(self, _event: tk.Event | None = None) -> None:
        """Keeps the chosen score source column for the next direct write run."""
        if self._suspend_context_events or self.current_context is None:
            return
        selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)
        self.preferred_score_source_key = selected_key
        self._schedule_config_autosave()
        if not selected_key:
            return
        selected_schema = self._schema_by_key(self.current_context.column_schemas, selected_key)
        if selected_schema is not None:
            self._log(f"Đã chọn cột điểm dùng để xét: {selected_schema.display_name}")

    def _collect_selected_context_apply_request(self) -> Dict[str, object]:
        """Collects the current UI selection and cache state for one live context apply run."""
        if self.current_context is None:
            raise RuntimeError("Hãy đọc Sổ điểm trước khi áp ngữ cảnh.")

        automation = self._build_automation()
        requested_grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id)
        requested_class_id = self._selected_option_id(self.class_var, self.class_label_to_id)
        requested_subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id)
        requested_term_id = self._selected_option_id(self.term_var, self.term_label_to_id)
        username, password = self._background_credentials()
        requested_scan_key = self._access_cache_key(
            requested_grade_id or self.current_context.selected_grade_id,
            requested_term_id or self.current_context.selected_term_id,
        )
        requested_scan_grade_id = requested_grade_id or self.current_context.selected_grade_id
        requested_scan_term_id = requested_term_id or self.current_context.selected_term_id
        matrix_access_scan_enabled = getattr(self, "_matrix_access_scan_enabled", True)
        has_cached_matrix = matrix_access_scan_enabled and requested_scan_key in self.access_entry_cache
        cached_entries = (
            list(self.access_entry_cache.get(requested_scan_key, []))
            if matrix_access_scan_enabled and has_cached_matrix
            else []
        )

        fallback_notes: List[str] = []
        effective_class_id = requested_class_id
        effective_subject_id = requested_subject_id
        if cached_entries:
            effective_class_id, effective_subject_id, fallback_notes = resolve_accessible_selection(
                cached_entries,
                preferred_class_id=requested_class_id,
                preferred_subject_id=requested_subject_id,
            )

        return {
            "automation": automation,
            "requested_grade_id": requested_grade_id,
            "requested_class_id": requested_class_id,
            "requested_subject_id": requested_subject_id,
            "requested_term_id": requested_term_id,
            "effective_class_id": effective_class_id,
            "effective_subject_id": effective_subject_id,
            "requested_scan_key": requested_scan_key,
            "requested_scan_grade_id": requested_scan_grade_id,
            "requested_scan_term_id": requested_scan_term_id,
            "has_cached_matrix": has_cached_matrix,
            "cached_entries": cached_entries,
            "fallback_notes": fallback_notes,
            "previous_context": self.current_context,
            "username": username,
            "password": password,
        }

    def _run_selected_context_apply(self, request: Dict[str, object]) -> object:
        """Executes one live scorebook context apply request from the UI layer."""
        automation = request["automation"]
        return automation.select_scorebook_context(
            grade_id=request["requested_grade_id"],
            class_id=request["effective_class_id"],
            subject_id=request["effective_subject_id"],
            term_id=request["requested_term_id"],
            username=request["username"],
            password=request["password"],
            progress_callback=request.get("progress_callback"),
        )

    def _handle_selected_context_apply_success(
        self,
        result: object,
        request: Dict[str, object],
    ) -> None:
        """Handles one successful live context apply back on the UI thread."""
        context, login_message, selection_message = result if isinstance(result, tuple) else (None, "", "")
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi áp ngữ cảnh.")
        if getattr(self, "_matrix_access_scan_enabled", True) and request["has_cached_matrix"] and not (
            context.accessible_entries
            or context.accessible_grade_id
            or context.accessible_term_id
        ):
            context.accessible_entries = list(request["cached_entries"])
            context.accessible_grade_id = request["requested_scan_grade_id"]
            context.accessible_term_id = request["requested_scan_term_id"]
        if login_message:
            self._log(login_message)
        if selection_message:
            self._log(selection_message)
        for note in request["fallback_notes"]:
            self._log(note)
        self._apply_scorebook_context(context)
        if context.permission_text:
            self._log(context.permission_text)
        if context.teacher_text:
            self._log(context.teacher_text)
        self._log_detected_columns(context.column_schemas, context.detected_columns)
        self._log("Đã áp ngữ cảnh Khối/Lớp/Môn/Học kỳ lên live Chrome.")

    def _handle_selected_context_apply_error(
        self,
        error: Exception,
        previous_context: ScorebookContext | None,
    ) -> None:
        """Handles one apply-context failure and restores the previous UI context when possible."""
        if previous_context is not None:
            self._apply_scorebook_context(previous_context)
        messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
        self._log(f"Lỗi áp ngữ cảnh: {error}")
        self._set_status_text("Áp ngữ cảnh thất bại")

    def on_apply_selected_context(self) -> None:
        """Applies the current GUI combo selections back to the live VNEDU scorebook."""
        self._cancel_pending_context_apply()
        if self.current_context is None:
            messagebox.showwarning("Chưa có dữ liệu", "Hãy đọc Sổ điểm trước khi áp ngữ cảnh.")
            self._log("Bỏ qua áp ngữ cảnh vì chưa có ScorebookContext.")
            return

        try:
            request = self._collect_selected_context_apply_request()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
            self._log(f"Lỗi áp ngữ cảnh: {error}")
            return

        self._run_background_task(
            "Đang áp ngữ cảnh Sổ điểm lên live Chrome...",
            worker=lambda progress: self._run_selected_context_apply(
                {
                    **request,
                    "progress_callback": progress,
                }
            ),
            on_success=lambda result: self._handle_selected_context_apply_success(result, request),
            on_error=lambda error: self._handle_selected_context_apply_error(
                error,
                request["previous_context"],
            ),
        )


def main() -> None:
    """Program entry point."""
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    AutoNhanXetV2App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

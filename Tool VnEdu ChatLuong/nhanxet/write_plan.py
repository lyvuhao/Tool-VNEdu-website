"""Lập kế hoạch ghi nhận xét và đánh giá kết quả lưu trên server."""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Tuple

from .models import CommentWriteResult, CommentWriteRow
from .rules import looks_like_numeric_comment_score, match_comment_for_value


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

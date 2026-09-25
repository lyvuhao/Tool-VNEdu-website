"""Xác minh request lưu Sổ điểm trên server (bản hỗ trợ cả điểm lẫn nhận xét)."""

from __future__ import annotations

from typing import Dict, List, Tuple
from urllib.parse import quote_plus

from nhanxet.write_plan import _json_save_response_state, select_relevant_server_save_requests

from .score_write import normalize_score_text


def _request_blob_contains_expected_score_pair(
    request_blob: str,
    input_name: str,
    value: str,
) -> bool:
    """Returns whether one request blob contains strong evidence for a specific score write pair."""
    normalized_blob = request_blob.strip().lower()
    normalized_input = input_name.strip().lower()
    normalized_value = normalize_score_text(value).lower()
    if not normalized_blob or not normalized_input or not normalized_value:
        return False

    exact_markers = (
        f"{normalized_input}={normalized_value}",
        f"{quote_plus(normalized_input)}={quote_plus(normalized_value)}",
        f'"{normalized_input}":"{normalized_value}"',
        f"'{normalized_input}':'{normalized_value}'",
    )
    if any(marker in normalized_blob for marker in exact_markers):
        return True

    input_tokens = tuple(dict.fromkeys((normalized_input, quote_plus(normalized_input))))
    value_tokens = tuple(dict.fromkeys((normalized_value, quote_plus(normalized_value))))
    for input_token in input_tokens:
        if not input_token:
            continue
        token_index = normalized_blob.find(input_token)
        if token_index < 0:
            continue
        window_start = max(0, token_index - 48)
        window_end = min(len(normalized_blob), token_index + len(input_token) + 128)
        nearby_window = normalized_blob[window_start:window_end]
        if any(value_token and value_token in nearby_window for value_token in value_tokens):
            return True
    return False


def evaluate_server_save_verification(
    auto_save_requested: bool,
    save_clicked: bool,
    save_requests: List[Dict[str, object]],
    request_markers: List[str] | None = None,
    expected_score_pairs: List[Tuple[str, str]] | None = None,
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

    normalized_pairs = [
        (input_name.strip(), normalize_score_text(value))
        for input_name, value in list(expected_score_pairs or [])
        if input_name.strip() and normalize_score_text(value)
    ]
    if normalized_pairs:
        for request in relevant_requests:
            request_blob = " ".join(
                [
                    str(request.get("url", "")).strip(),
                    str(request.get("bodyPreview", "")).strip(),
                    str(request.get("responsePreview", "")).strip(),
                ]
            )
            if any(
                _request_blob_contains_expected_score_pair(request_blob, input_name, value)
                for input_name, value in normalized_pairs
            ):
                return True, "server_payload", f"Đã xác minh lưu server-side qua {len(relevant_requests)} request có payload điểm khớp."
        return (
            False,
            "payload_not_observed",
            "Đã bấm Lưu và request thành công nhưng chưa thấy cặp ô điểm/giá trị mong đợi trong payload gửi lên server.",
        )

    return True, "server_response", f"Đã xác minh lưu server-side qua {len(relevant_requests)} request."

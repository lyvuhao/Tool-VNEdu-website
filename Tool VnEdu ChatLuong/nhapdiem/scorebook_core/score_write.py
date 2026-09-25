"""Chuẩn hoá và lập payload ghi điểm."""

from __future__ import annotations

from typing import Dict, List, Tuple

from nhanxet.access import ensure_selected_score_option, merge_score_options
from nhanxet.models import ScoreOption

from .models import ScoreWriteEntry, ScoreWriteResult


def resolve_hydrated_score_options(
    existing_options: List[ScoreOption],
    live_options: List[ScoreOption],
    *,
    selected_id: str,
    selected_text: str,
    merge_existing: bool = True,
    preserve_selected_if_missing: bool = True,
) -> List[ScoreOption]:
    """Builds one refreshed option list while controlling whether stale pre-refresh items may survive."""
    resolved_options = (
        merge_score_options(existing_options, live_options)
        if merge_existing
        else merge_score_options(live_options)
    )
    if preserve_selected_if_missing:
        return ensure_selected_score_option(resolved_options, selected_id, selected_text)
    return resolved_options


def normalize_score_text(value: object) -> str:
    """Normalizes one score-like value for DOM write/verification."""
    normalized = str(value or "").strip().replace(',', '.')
    if not normalized:
        return ""
    try:
        numeric_value = float(normalized)
    except (TypeError, ValueError):
        return normalized
    if numeric_value.is_integer():
        return str(int(numeric_value))
    return f"{numeric_value:.2f}".rstrip('0').rstrip('.')


def coerce_score_write_entries(entries: List[ScoreWriteEntry | Dict[str, object]]) -> List[ScoreWriteEntry]:
    """Coerces raw payload rows into typed score write entries."""
    coerced_entries: List[ScoreWriteEntry] = []
    for item in list(entries or []):
        if isinstance(item, ScoreWriteEntry):
            coerced_entries.append(item)
            continue
        if not isinstance(item, dict):
            raise TypeError(f"Score entry không hợp lệ: {type(item)!r}")
        coerced_entries.append(
            ScoreWriteEntry(
                row_index=int(item.get('row_index', item.get('rowIndex', 0)) or 0),
                row_id=str(item.get('row_id', item.get('rowId', ''))).strip(),
                student_code=str(item.get('student_code', item.get('studentCode', ''))).strip(),
                student_name=str(item.get('student_name', item.get('studentName', ''))).strip(),
                target_column_key=str(item.get('target_column_key', item.get('targetColumnKey', ''))).strip(),
                target_column_name=str(item.get('target_column_name', item.get('targetColumnName', ''))).strip(),
                current_score=str(item.get('current_score', item.get('currentScore', ''))).strip(),
                target_input_name=str(item.get('target_input_name', item.get('targetInputName', ''))).strip(),
                proposed_score=normalize_score_text(item.get('proposed_score', item.get('proposedScore', ''))),
                status=str(item.get('status', 'ready') or 'ready').strip() or 'ready',
                reason=str(item.get('reason', '')).strip(),
            )
        )
    return coerced_entries


def ready_score_write_entries(entries: List[ScoreWriteEntry | Dict[str, object]]) -> List[ScoreWriteEntry]:
    """Returns only score rows that are ready for DOM write-back."""
    ready_entries: List[ScoreWriteEntry] = []
    for entry in coerce_score_write_entries(entries):
        proposed_score = normalize_score_text(entry.proposed_score)
        if not entry.target_input_name.strip() or not proposed_score:
            continue
        entry.proposed_score = proposed_score
        ready_entries.append(entry)
    return ready_entries


def build_score_write_payload(entries: List[ScoreWriteEntry | Dict[str, object]]) -> List[Dict[str, str]]:
    """Builds the DOM payload used to write scores into live inputs."""
    return [
        {"inputName": entry.target_input_name, "text": normalize_score_text(entry.proposed_score)}
        for entry in ready_score_write_entries(entries)
    ]


def build_score_write_request_pairs(payload: List[Dict[str, str]]) -> List[Tuple[str, str]]:
    """Builds conservative `(inputName, value)` pairs expected to surface in one save payload."""
    pairs: List[Tuple[str, str]] = []
    seen_pairs = set()
    for item in payload:
        input_name = str(item.get("inputName", "")).strip()
        value = normalize_score_text(str(item.get("text", "")).strip())
        if not input_name or not value:
            continue
        key = (input_name, value)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        pairs.append(key)
    return pairs


def summarize_score_write_result(
    entries: List[ScoreWriteEntry | Dict[str, object]],
    rows_to_apply: List[ScoreWriteEntry],
    result_payload: Dict[str, object],
    verification_map: Dict[str, object],
) -> ScoreWriteResult:
    """Builds the final score write-back summary from raw DOM execution data."""
    verified = 0
    verified_input_names: List[str] = []
    failed_input_names: List[str] = list(result_payload.get('failed', []))
    failed_rows: List[str] = list(failed_input_names)
    for entry in rows_to_apply:
        actual_value = normalize_score_text(verification_map.get(entry.target_input_name, ''))
        expected_value = normalize_score_text(entry.proposed_score)
        if actual_value == expected_value:
            verified += 1
            verified_input_names.append(entry.target_input_name)
        elif entry.student_name:
            failed_input_names.append(entry.target_input_name)
            failed_rows.append(f"{entry.student_name} ({entry.student_code})")
        else:
            failed_input_names.append(entry.target_input_name)
            failed_rows.append(entry.target_input_name)

    save_clicked = bool(result_payload.get("saveClicked"))
    save_verified = bool(result_payload.get("saveVerified"))
    if save_clicked and not save_verified:
        verified = 0
        verified_input_names = []
        for entry in rows_to_apply:
            failed_input_names.append(entry.target_input_name)
            if entry.student_name:
                failed_rows.append(f"{entry.student_name} ({entry.student_code})")
            else:
                failed_rows.append(entry.target_input_name)

    return ScoreWriteResult(
        attempted=len(rows_to_apply),
        updated=len(list(result_payload.get('updated', []))),
        verified=verified,
        skipped=len(coerce_score_write_entries(entries)) - len(rows_to_apply),
        save_clicked=save_clicked,
        save_verified=save_verified,
        save_verification_mode=str(result_payload.get('saveVerificationMode', '')).strip(),
        save_verification_detail=str(result_payload.get('saveVerificationDetail', '')).strip(),
        verified_input_names=list(dict.fromkeys(verified_input_names)),
        failed_input_names=list(dict.fromkeys(failed_input_names)),
        failed_rows=list(dict.fromkeys(failed_rows)),
    )

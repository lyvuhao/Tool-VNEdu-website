"""Parse, làm tròn và lập payload điểm."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .voice.constants import MANUAL_SCORE_PATTERN


def parse_manual_score_text(score_text: object) -> float | None:
    """Parses a manually typed VNEDU score without accepting surrounding words."""
    if score_text is None:
        return None
    normalized_text = str(score_text).strip()
    if not normalized_text or not MANUAL_SCORE_PATTERN.fullmatch(normalized_text):
        return None
    try:
        score_value = float(normalized_text.replace(",", "."))
    except ValueError:
        return None
    return score_value if 0 <= score_value <= 10 else None


def _format_score_value(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _clamp_score_value(value: float, minimum: float = 0.0, maximum: float = 10.0) -> float:
    """Keeps one score inside the valid VNEDU range."""
    return max(minimum, min(maximum, float(value)))


def _round_score_to_one_decimal(value: object) -> str:
    """Rounds one score to a single decimal place using half-up semantics."""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    normalized = text.replace(",", ".")
    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation:
        return text
    if decimal_value.as_tuple().exponent >= -1:
        return _format_score_value(normalized)
    rounded_value = decimal_value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return _format_score_value(str(rounded_value))


def _build_conflict_resolution_scores(existing_value: float, incoming_value: float) -> dict[str, float]:
    """Builds the derived score options shown in the conflict dialog."""
    return {
        "average": (existing_value + incoming_value) / 2,
        "plus_one": _clamp_score_value(existing_value + 1.0),
        "accumulate": _clamp_score_value(existing_value + incoming_value),
    }


def _build_apply_confirmation_summary(
    payload: list[dict[str, str]],
    rows_by_key: dict[str, object],
    preview_limit: int = 10,
) -> str:
    """Builds the confirmation summary using the exact scores that will be submitted."""
    summary_lines: list[str] = []
    for item in payload[:preview_limit]:
        row = rows_by_key.get(item.get("row_key", ""))
        row_name = getattr(row, "student_name", "").strip() if row is not None else ""
        name = row_name or item.get("student_name") or item.get("row_key", "?")
        summary_lines.append(f"  • {name}: {item.get('proposed_score', '?')}")
    if len(payload) > preview_limit:
        summary_lines.append(f"  ... và {len(payload) - preview_limit} học sinh nữa")
    return "\n".join(summary_lines)


def _build_score_apply_payload(
    rows: list[object],
    *,
    score_field: str,
) -> list[dict[str, str]]:
    """Builds the score apply payload from one chosen score source on each row."""
    payload: list[dict[str, str]] = []
    for row in rows:
        score_value = parse_manual_score_text(getattr(row, score_field, ""))
        proposed_score = _format_score_value(score_value)
        target_input_name = str(getattr(row, "target_input_name", "") or "").strip()
        if not proposed_score or not target_input_name:
            continue
        payload.append(
            {
                "row_key": str(getattr(row, "row_key", "") or ""),
                "row_index": str(getattr(row, "row_index", "") or ""),
                "row_id": str(getattr(row, "row_id", "") or ""),
                "student_code": str(getattr(row, "student_code", "") or ""),
                "student_name": str(getattr(row, "student_name", "") or ""),
                "current_score": str(getattr(row, "current_score", "") or ""),
                "target_input_name": target_input_name,
                "proposed_score": proposed_score,
            }
        )
    return payload

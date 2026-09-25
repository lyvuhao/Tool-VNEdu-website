"""Phân tích điều kiện rule nhận xét và chọn nhận xét theo điểm."""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Tuple

from .config import NUMERIC_COMMENT_SCORE_RE
from .models import CommentRule


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


# Nhiều câu nhận xét cho cùng một rule: ngăn cách bằng dấu này trong ô "Mẫu nhận xét".
COMMENT_VARIANT_SEPARATOR = "|"


def split_comment_variants(template: str) -> List[str]:
    """Tách ô "Mẫu nhận xét" thành các câu; ô không có dấu `|` -> đúng một câu như trước."""
    variants = [part.strip() for part in str(template).split(COMMENT_VARIANT_SEPARATOR)]
    variants = [part for part in variants if part]
    return variants or [str(template).strip()]


class CommentVariantPicker:
    """Chia đều các câu nhận xét của cùng một rule cho học sinh trong một lần phân tích.

    - Học sinh đã có nhận xét trùng một câu của rule -> giữ nguyên (chạy lại không bị xáo trộn).
    - Học sinh mới -> nhận câu đang được dùng ít nhất (hoà thì lấy câu đứng trước), nên các
      bạn liền nhau cùng mức điểm sẽ nhận câu khác nhau. Kết quả chỉ phụ thuộc dữ liệu vào,
      nên bước xem trước và bước ghi luôn ra cùng một kết quả.
    """

    def __init__(self) -> None:
        self._usage: Dict[Tuple[str, str], int] = {}

    def register_existing(self, condition: str, variants: List[str], current_comment: str) -> None:
        """Đếm trước nhận xét đang có trên Sổ điểm nếu nó là một câu của rule."""
        current = str(current_comment).strip()
        if current in variants:
            key = (condition, current)
            self._usage[key] = self._usage.get(key, 0) + 1

    def pick(self, condition: str, variants: List[str]) -> str:
        """Chọn câu dùng ít nhất cho một học sinh mới."""
        if len(variants) == 1:
            return variants[0]
        counts = [self._usage.get((condition, variant), 0) for variant in variants]
        chosen = variants[counts.index(min(counts))]
        self._usage[(condition, chosen)] = self._usage.get((condition, chosen), 0) + 1
        return chosen

"""Tách tên và điểm từ câu nói."""

from __future__ import annotations

import re
from typing import Sequence

from .constants import (
    VOICE_COMMAND_SPACE_PATTERN,
    VOICE_NAME_PREFIX_TOKENS,
    VOICE_NAME_TRAILING_TOKENS,
    VOICE_SCORE_DECIMAL_MARKERS,
    VOICE_SCORE_DECIMAL_PATTERN,
    VOICE_SCORE_DECIMAL_REPLACEMENTS,
    VOICE_SCORE_EXTRACT_PATTERN,
    VOICE_SCORE_HALF_MARKERS,
    VOICE_SCORE_NUMBER_REPLACEMENTS,
    VOICE_SCORE_ONLY_CUE_TOKENS,
    VOICE_SCORE_VALUE_TOKEN_RE,
    VOICE_SCORE_WORD_PATTERN,
)
from .phonetics import _normalize_diacritic_text, _replace_tokens


def voice_parse_score_text(score_text: str | None) -> float | None:
    if score_text is None:
        return None
    normalized_text = str(score_text).lower().strip()
    if not normalized_text:
        return None
    normalized_text = VOICE_SCORE_WORD_PATTERN.sub(" ", normalized_text)
    normalized_text = _replace_tokens(normalized_text, VOICE_SCORE_DECIMAL_REPLACEMENTS)
    normalized_text = _replace_tokens(normalized_text, VOICE_SCORE_NUMBER_REPLACEMENTS)
    normalized_text = VOICE_SCORE_DECIMAL_PATTERN.sub(r"\1.\2", normalized_text)
    normalized_text = VOICE_COMMAND_SPACE_PATTERN.sub(" ", normalized_text).strip()
    match = VOICE_SCORE_EXTRACT_PATTERN.search(normalized_text)
    if not match:
        return None
    try:
        score_value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return score_value if 0 <= score_value <= 10 else None


def _is_voice_score_value_token(token: str) -> bool:
    """Returns whether one token can be part of a spoken score value."""
    return bool(VOICE_SCORE_VALUE_TOKEN_RE.fullmatch(str(token or "").strip()))


def _is_voice_decimal_marker(token: str) -> bool:
    """Returns whether one token joins a decimal spoken score phrase."""
    return str(token or "").strip().lower() in VOICE_SCORE_DECIMAL_MARKERS


def _is_voice_half_marker(token: str) -> bool:
    """Returns whether one token means half a point in a spoken score phrase."""
    return str(token or "").strip().lower() in VOICE_SCORE_HALF_MARKERS


def _voice_score_tail_start(tokens: Sequence[str]) -> int | None:
    """Finds the start index of a score phrase at the end of a token list."""
    if not tokens:
        return None
    last_index = len(tokens) - 1
    last_token = tokens[last_index].lower()
    if _is_voice_half_marker(last_token) and last_index >= 1 and _is_voice_score_value_token(tokens[last_index - 1]):
        return last_index - 1
    if not _is_voice_score_value_token(last_token):
        return None
    if (
        last_index >= 2
        and _is_voice_decimal_marker(tokens[last_index - 1])
        and _is_voice_score_value_token(tokens[last_index - 2])
    ):
        return last_index - 2
    return last_index


def _voice_score_head_end(tokens: Sequence[str]) -> int | None:
    """Finds the exclusive end index of a score phrase at the beginning of a token list."""
    if not tokens or not _is_voice_score_value_token(tokens[0]):
        return None
    if len(tokens) >= 2 and _is_voice_half_marker(tokens[1]):
        return 2
    if len(tokens) >= 3 and _is_voice_decimal_marker(tokens[1]) and _is_voice_score_value_token(tokens[2]):
        return 3
    return 1


def _clean_voice_name_candidate(value: str, *, strip_score_words: bool = True) -> str:
    """Removes command filler around the student-name segment without touching inner name tokens."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    if strip_score_words:
        cleaned = VOICE_SCORE_WORD_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"^\s*(?:học\s+sinh|hoc\s+sinh)\s+", " ", cleaned, flags=re.IGNORECASE)
    tokens = [token for token in VOICE_COMMAND_SPACE_PATTERN.sub(" ", cleaned).split() if token]
    while tokens and tokens[0].lower() in VOICE_NAME_PREFIX_TOKENS:
        tokens.pop(0)
    trailing_tokens = set(VOICE_NAME_TRAILING_TOKENS)
    if not strip_score_words:
        trailing_tokens.discard("điểm")
        trailing_tokens.discard("diem")
    while tokens and tokens[-1].lower() in trailing_tokens:
        tokens.pop()
    return " ".join(tokens).strip()


def _voice_score_only_value(cleaned_text: str) -> float | None:
    """Parses a score-only command, rejecting mixed name+score transcripts."""
    tokens = [token.lower() for token in str(cleaned_text or "").split() if token]
    if not tokens:
        return None
    content_tokens = [
        token
        for token in tokens
        if token not in VOICE_SCORE_ONLY_CUE_TOKENS
    ]
    if not content_tokens:
        return None
    for token in content_tokens:
        if (
            not _is_voice_score_value_token(token)
            and not _is_voice_decimal_marker(token)
            and not _is_voice_half_marker(token)
        ):
            return None
    return voice_parse_score_text(" ".join(content_tokens))


# KHMER #B — Voice picker token map (số thứ tự ngắn cho cặp tied list).
_VOICE_PICKER_TOKEN_TO_INDEX: dict[str, int] = {
    # 1
    "mot": 0, "1": 0, "thunhat": 0, "thu1": 0, "first": 0,
    # 2
    "hai": 1, "2": 1, "thuhai": 1, "thu2": 1, "second": 1,
    # 3
    "ba": 2, "3": 2, "thuba": 2, "thu3": 2, "third": 2,
    # 4
    "bon": 3, "tu": 3, "4": 3, "thubon": 3, "thu4": 3, "fourth": 3,
    # 5
    "nam": 4, "5": 4, "thunam": 4, "thu5": 4, "fifth": 4,
}


def _voice_pick_tied_index(cleaned_text: str, candidate_count: int) -> tuple[int | None, str]:
    """Tries to extract a 1-based picker index from the start of the transcript.

    Trả về `(index, leftover_text)`:
        - `index`: 0-based vị trí trong tied list, hoặc None nếu không phải lệnh picker.
        - `leftover_text`: phần còn lại của transcript (vd: "8" trong "một 8") để
          caller tiếp tục parse score bình thường.

    Chỉ chấp nhận khi tied list active (caller tự kiểm tra) VÀ token đầu là số
    thứ tự (một/hai/ba/bốn/năm hoặc 1/2/3/4/5). Tránh khớp với "một" trong câu
    "một con vịt" — nếu transcript có nhiều token KHÔNG phải số/score sau token
    đầu, vẫn từ chối để pick.
    """
    if candidate_count <= 0:
        return None, ""
    text = str(cleaned_text or "").strip()
    if not text:
        return None, ""
    tokens = text.split()
    if not tokens:
        return None, ""
    first_token = _normalize_diacritic_text(tokens[0]).replace(" ", "")
    consumed = 1
    # L2 FIX: hỗ trợ "thứ nhất/thứ hai/..." — STT tách thành 2 token ("thứ" + số).
    # Ghép "thu" với token kế để khớp khóa thunhat/thuhai/... trong map.
    if first_token == "thu" and len(tokens) >= 2:
        second_token = _normalize_diacritic_text(tokens[1]).replace(" ", "")
        merged = "thu" + second_token
        if merged in _VOICE_PICKER_TOKEN_TO_INDEX:
            first_token = merged
            consumed = 2
    if first_token not in _VOICE_PICKER_TOKEN_TO_INDEX:
        return None, ""
    index = _VOICE_PICKER_TOKEN_TO_INDEX[first_token]
    if index >= candidate_count:
        return None, ""
    leftover_tokens = tokens[consumed:]
    # Nếu có leftover và toàn bộ là số/score-words → trả nguyên để parse score.
    # Nếu lẫn từ khác (vd "một anh tám") → vẫn cho leftover đi qua, parser score
    # sẽ tự lọc thêm.
    leftover = " ".join(leftover_tokens).strip()
    return index, leftover


def _voice_score_value_from_segment(segment_text: str) -> float | None:
    """Finds a spoken score inside a roster-relative transcript segment."""
    tokens = [token.lower() for token in str(segment_text or "").split() if token]
    if not tokens:
        return None
    strict_score = _voice_score_only_value(" ".join(tokens))
    if strict_score is not None:
        return strict_score
    if not any(token in VOICE_SCORE_ONLY_CUE_TOKENS for token in tokens):
        return None
    for start in range(len(tokens)):
        score_end = _voice_score_head_end(tokens[start:])
        if score_end is None:
            continue
        score_value = voice_parse_score_text(" ".join(tokens[start : start + score_end]))
        if score_value is not None:
            return score_value
    return None


def _find_token_span(haystack_tokens: Sequence[str], needle_tokens: Sequence[str]) -> tuple[int, int] | None:
    """Finds one exact contiguous token span inside a normalized transcript."""
    if not haystack_tokens or not needle_tokens or len(needle_tokens) > len(haystack_tokens):
        return None
    needle_length = len(needle_tokens)
    for start in range(0, len(haystack_tokens) - needle_length + 1):
        if tuple(haystack_tokens[start : start + needle_length]) == tuple(needle_tokens):
            return start, start + needle_length
    return None

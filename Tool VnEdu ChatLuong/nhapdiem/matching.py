"""Hàm đo độ giống chuỗi dùng để khớp tên học sinh."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Sequence

from .compat import fuzzywuzzy_fuzz, rapidfuzz_fuzz


def _similarity_ratio(left: str, right: str) -> int:
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return 0
    if rapidfuzz_fuzz is not None:
        return int(round(rapidfuzz_fuzz.ratio(left, right)))
    if fuzzywuzzy_fuzz is not None:
        return int(fuzzywuzzy_fuzz.ratio(left, right))
    return int(round(SequenceMatcher(None, left, right).ratio() * 100))


def _partial_similarity_ratio(left: str, right: str) -> int:
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return 0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if rapidfuzz_fuzz is not None:
        return int(round(rapidfuzz_fuzz.partial_ratio(shorter, longer)))
    if fuzzywuzzy_fuzz is not None:
        return int(fuzzywuzzy_fuzz.partial_ratio(shorter, longer))
    if shorter in longer:
        return 100
    if len(shorter) == len(longer):
        return _similarity_ratio(shorter, longer)
    best_score = 0
    window_count = max(1, len(longer) - len(shorter) + 1)
    for start in range(window_count):
        window = longer[start : start + len(shorter)]
        best_score = max(best_score, _similarity_ratio(shorter, window))
        if best_score >= 100:
            break
    return best_score


def _compact_similarity_ratio(left: str, right: str) -> int:
    return _similarity_ratio(left.replace(" ", ""), right.replace(" ", ""))


def _token_sort_ratio(left: str, right: str) -> int:
    return _similarity_ratio(" ".join(sorted(left.split())), " ".join(sorted(right.split())))


def _token_set_ratio(left: str, right: str) -> int:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0
    common = left_tokens & right_tokens
    left_only = left_tokens - common
    right_only = right_tokens - common
    merged_left = " ".join(sorted([*common, *left_only]))
    merged_right = " ".join(sorted([*common, *right_only]))
    return max(_similarity_ratio(merged_left, merged_right), _token_sort_ratio(left, right))


def _ordered_token_subsequence_score(query_tokens: Sequence[str], candidate_tokens: Sequence[str]) -> int:
    if len(query_tokens) < 2 or len(query_tokens) > len(candidate_tokens):
        return 0
    matched_positions: list[int] = []
    search_start = 0
    for token in query_tokens:
        try:
            matched_index = candidate_tokens.index(token, search_start)
        except ValueError:
            return 0
        matched_positions.append(matched_index)
        search_start = matched_index + 1
    span = matched_positions[-1] - matched_positions[0] + 1
    skipped_tokens = max(0, span - len(query_tokens))
    trailing_gap = max(0, len(candidate_tokens) - len(query_tokens) - skipped_tokens)
    base_score = 90 if len(query_tokens) >= 3 else 84
    if skipped_tokens == 0:
        base_score += 3
    elif skipped_tokens == 1 and len(query_tokens) >= 3:
        base_score += 1
    base_score += min(3, len(query_tokens))
    penalty = min(4, skipped_tokens * 2) + min(2, trailing_gap)
    return max(0, min(96, base_score - penalty))


def _normalized_name_variants(normalized_name: str) -> tuple[str, ...]:
    tokens = tuple(token for token in normalized_name.split() if token)
    if not tokens:
        return ()
    variants: list[str] = []
    seen: set[str] = set()

    def add(candidate_tokens: Sequence[str]) -> None:
        candidate = " ".join(candidate_tokens).strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)

    add(tokens)
    if len(tokens) >= 3:
        add(tokens[-3:])
    if len(tokens) >= 2:
        add(tokens[-2:])
    return tuple(variants)


def _entry_value(entry: object, key: str, default: object = "") -> object:
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)

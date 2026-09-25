"""Khớp tên học sinh từ giọng nói và áp điểm."""

from __future__ import annotations

import time
import tkinter as tk

from ..config import VOICE_MATCH_CACHE_MAX_SIZE, VOICE_PENDING_CONFIDENCE_DECAY_RATE
from ..matching import (
    _compact_similarity_ratio,
    _normalized_name_variants,
    _ordered_token_subsequence_score,
    _partial_similarity_ratio,
    _similarity_ratio,
)
from ..models import LogTag, RowStatus, ScoreStudentRow, VoiceMatchResult
from ..scores import _build_conflict_resolution_scores, _format_score_value
from ..voice.constants import _VOICE_UNDO_PATTERN
from ..voice.parsing import (
    _clean_voice_name_candidate,
    _find_token_span,
    _voice_pick_tied_index,
    _voice_score_head_end,
    _voice_score_only_value,
    _voice_score_tail_start,
    _voice_score_value_from_segment,
    voice_parse_score_text,
)
from ..voice.phonetics import (
    _normalize_diacritic_text,
    _voice_phonetic_text,
    _voice_phonetic_text_strict,
)


class VoiceMatchingMixin:
    """Khớp tên học sinh từ giọng nói và áp điểm."""

    def _roster_aware_name_score_pairs(self, cleaned: str) -> list[tuple[str, float]]:
        """Uses the scanned roster to find a real student name inside one transcript."""
        transcript_tokens = [token for token in str(cleaned or "").split() if token]
        if not transcript_tokens:
            return []
        normalized_tokens = [
            _normalize_diacritic_text(token)
            for token in transcript_tokens
        ]
        normalized_tokens = [token for token in normalized_tokens if token]
        if not normalized_tokens:
            return []

        variants: list[tuple[str, tuple[str, ...]]] = []
        with self._score_data_lock:
            for row in self._score_rows_by_key.values():
                row_tokens = tuple(token for token in row.normalized_name.split() if token)
                if row.student_name and row_tokens:
                    variants.append((row.student_name, row_tokens))
                for alias in self._student_aliases.get(row.student_name, []):
                    alias_tokens = tuple(token for token in _normalize_diacritic_text(alias).split() if token)
                    if alias_tokens:
                        variants.append((row.student_name, alias_tokens))

        pairs: list[tuple[str, float]] = []
        seen: set[tuple[str, float]] = set()
        for student_name, name_tokens in sorted(variants, key=lambda item: len(item[1]), reverse=True):
            span = _find_token_span(normalized_tokens, name_tokens)
            if span is None:
                continue
            start, end = span
            score_value = _voice_score_value_from_segment(" ".join(transcript_tokens[end:]))
            if score_value is None:
                score_value = _voice_score_value_from_segment(" ".join(transcript_tokens[:start]))
            if score_value is None:
                continue
            key = (_normalize_diacritic_text(student_name), score_value)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((student_name, score_value))
        return pairs

    def _candidate_name_score_pairs(self, text: str) -> list[tuple[str, float]]:
        cleaned = self._preprocess_voice_text(text)
        pairs: list[tuple[str, float]] = self._roster_aware_name_score_pairs(cleaned)
        tokens = [token for token in cleaned.split() if token]
        has_score_word = any(token.lower() == "diem" for token in tokens)

        def add_pair(name_text: str, score_text: str) -> None:
            name_candidate = _clean_voice_name_candidate(name_text)
            if len(_normalize_diacritic_text(name_candidate)) < 2:
                return
            score_value = voice_parse_score_text(score_text)
            if score_value is None:
                return
            pairs.append((name_candidate, score_value))

        for index, token in enumerate(tokens):
            if token.lower() != "diem":
                continue
            left_tokens = tokens[:index]
            right_tokens = tokens[index + 1 :]

            right_score_end = _voice_score_head_end(right_tokens)
            if left_tokens and right_score_end:
                add_pair(" ".join(left_tokens), " ".join(right_tokens[:right_score_end]))
            if right_score_end and right_score_end < len(right_tokens):
                add_pair(" ".join(right_tokens[right_score_end:]), " ".join(right_tokens[:right_score_end]))

            left_score_start = _voice_score_tail_start(left_tokens)
            if left_score_start is not None and not right_score_end:
                if left_score_start > 0:
                    add_pair(" ".join(left_tokens[:left_score_start]), " ".join(left_tokens[left_score_start:]))
                if right_tokens:
                    add_pair(" ".join(right_tokens), " ".join(left_tokens[left_score_start:]))

        if not has_score_word:
            tail_score_start = _voice_score_tail_start(tokens)
            if tail_score_start is not None and tail_score_start > 0:
                add_pair(" ".join(tokens[:tail_score_start]), " ".join(tokens[tail_score_start:]))

            head_score_end = _voice_score_head_end(tokens)
            if head_score_end is not None and head_score_end < len(tokens):
                add_pair(" ".join(tokens[head_score_end:]), " ".join(tokens[:head_score_end]))

        unique_pairs: list[tuple[str, float]] = []
        seen: set[tuple[str, float]] = set()
        for name_candidate, score_value in pairs:
            key = (_normalize_diacritic_text(name_candidate), score_value)
            if key not in seen:
                unique_pairs.append((name_candidate, score_value))
                seen.add(key)
        return unique_pairs

    def _resolve_voice_command(
        self,
        transcript: str,
        *,
        roster_revision: int | None = None,
        dry_run: bool = False,
    ) -> tuple[VoiceMatchResult | None, str]:
        """Resolves a transcript into a VoiceMatchResult or status message.

        BUG #10 FIX: `dry_run=True` được dùng bởi `_best_voice_match_from_transcripts`
        khi probe nhiều alternative từ Google STT để không vô tình overwrite
        pending/tied state khi xếp hạng. Chỉ caller cuối cùng (`_process_ptt_audio_task`)
        mới được phép commit state thay đổi.
        """
        with self._score_data_lock:
            has_rows = bool(self._score_rows_by_key)
        if not has_rows:
            return None, "Chưa có danh sách học sinh."
        cleaned = self._preprocess_voice_text(transcript)
        if _VOICE_UNDO_PATTERN.search(cleaned):
            return None, "UNDO"
        effective_roster_revision = self._voice_roster_revision(roster_revision)
        self._valid_voice_pending_snapshot(roster_revision=effective_roster_revision)

        # KHMER #B: Voice picker — nếu tied list đang active, kiểm tra lệnh "một/hai/ba ..."
        # TRƯỚC khi parse name+score thông thường (vì "một" có thể đụng các candidate khác).
        tied_keys, _tied_at, _tied_rev = self._valid_voice_tied_snapshot(
            roster_revision=effective_roster_revision
        )
        if tied_keys:
            picker_index, leftover = _voice_pick_tied_index(cleaned, len(tied_keys))
            if picker_index is not None:
                picked_key = tied_keys[picker_index]
                with self._score_data_lock:
                    picked_row = self._score_rows_by_key.get(picked_key)
                if picked_row is not None:
                    # Parse score từ phần còn lại (vd "một 8" → leftover = "8").
                    leftover_score: float | None = None
                    leftover_clean = (leftover or "").strip()
                    if leftover_clean:
                        leftover_score = _voice_score_only_value(leftover_clean)
                        if leftover_score is None:
                            leftover_score = voice_parse_score_text(leftover_clean)
                    # BUG #3 FIX: Có leftover NHƯNG không parse được score → có thể
                    # transcript là noise vô tình bắt đầu bằng số thứ tự (vd
                    # "một con vịt" sau khi STT nhả lỗi). KHÔNG pick để tránh
                    # gán nhầm; giữ tied list nguyên để user thử lại.
                    if leftover_clean and leftover_score is None:
                        # Bỏ qua picker, đi tiếp các nhánh fallback name+score.
                        # Tied list sẽ vẫn được surface nếu mọi nhánh fail.
                        pass
                    else:
                        if not dry_run:
                            self._clear_voice_tied_candidates()
                        if leftover_score is not None:
                            # Có cả picker + score → apply ngay.
                            return (
                                VoiceMatchResult(
                                    row_key=picked_row.row_key,
                                    student_name=picked_row.student_name,
                                    score_text=_format_score_value(leftover_score),
                                    score_value=leftover_score,
                                    match_score=88,
                                    transcript=transcript.strip(),
                                ),
                                "ok",
                            )
                        # Chỉ picker (leftover rỗng) → set pending row, đợi lệnh "8" sau.
                        if not dry_run:
                            self._set_voice_pending(picked_row.row_key, roster_revision=effective_roster_revision)
                        return None, f"NAME_ONLY:{picked_row.student_name}"

        for name_candidate, score_value in self._candidate_name_score_pairs(transcript):
            row, match_score = self._match_student(name_candidate, dry_run=dry_run)
            if row is None:
                continue
            if not dry_run:
                self._clear_voice_pending()
                self._clear_voice_tied_candidates()
            return (
                VoiceMatchResult(
                    row_key=row.row_key,
                    student_name=row.student_name,
                    score_text=_format_score_value(score_value),
                    score_value=score_value,
                    match_score=match_score,
                    transcript=transcript.strip(),
                ),
                "ok",
            )
        score_only = _voice_score_only_value(cleaned)
        pending_key, pending_at, pending_revision = self._valid_voice_pending_snapshot(
            roster_revision=effective_roster_revision
        )
        if score_only is not None and pending_key:
            with self._score_data_lock:
                pending_row = self._score_rows_by_key.get(pending_key)
            if pending_row is not None:
                # IMP-C8: Confidence decay — càng chờ lâu, confidence càng giảm
                elapsed = time.perf_counter() - pending_at
                base_confidence = 90
                decayed_confidence = max(60, int(base_confidence - elapsed * VOICE_PENDING_CONFIDENCE_DECAY_RATE))
                if not dry_run:
                    self._clear_voice_pending_if_current(pending_key, pending_at, pending_revision)
                return (
                    VoiceMatchResult(
                        row_key=pending_row.row_key,
                        student_name=pending_row.student_name,
                        score_text=_format_score_value(score_only),
                        score_value=score_only,
                        match_score=decayed_confidence,
                        transcript=transcript.strip(),
                    ),
                    "ok",
                )
            if not dry_run:
                self._clear_voice_pending_if_current(pending_key, pending_at, pending_revision)
        name_only = _clean_voice_name_candidate(cleaned)
        if name_only and len(name_only) >= 2:
            row, match_score = self._match_student(name_only, dry_run=dry_run)
            if row is not None:
                if not dry_run:
                    self._set_voice_pending(row.row_key, roster_revision=effective_roster_revision)
                return None, f"NAME_ONLY:{row.student_name}"
        preserved_name_only = _clean_voice_name_candidate(cleaned, strip_score_words=False)
        if (
            preserved_name_only
            and preserved_name_only != name_only
            and len(preserved_name_only) >= 2
        ):
            row, match_score = self._match_student(preserved_name_only, dry_run=dry_run)
            if row is not None:
                if not dry_run:
                    self._set_voice_pending(row.row_key, roster_revision=effective_roster_revision)
                return None, f"NAME_ONLY:{row.student_name}"
        # KHMER #B: Sau khi mọi nhánh fail, kiểm tra xem `_match_student` có
        # vừa set tied list mới hay không (do query gây tied). Nếu có, trả
        # signal TIED để UI hiện status picker.
        # Trong dry_run, _match_student không set tied → snapshot vẫn cho list cũ.
        new_tied_keys, _at, _rev = self._valid_voice_tied_snapshot(
            roster_revision=effective_roster_revision
        )
        if new_tied_keys:
            with self._score_data_lock:
                tied_names = [
                    self._score_rows_by_key[k].student_name
                    for k in new_tied_keys
                    if k in self._score_rows_by_key
                ]
            # BUG #5 FIX: Đảm bảo luôn return đúng tuple kể cả khi tied_names rỗng.
            tied_label = "/".join(tied_names) if tied_names else "?"
            return None, f"TIED:{tied_label}"
        return None, "Không phân tích được mẫu 'Tên + điểm'."

    def _candidate_row_keys_for_query(self, normalized_query: str) -> list[str]:
        tokens = [token for token in normalized_query.split() if token]
        if not tokens:
            return []
        candidate_keys: set[str] = set()
        if len(tokens) >= 2:
            candidate_keys.update(self._student_last_two_index.get(" ".join(tokens[-2:]), []))
        candidate_keys.update(self._student_last_name_index.get(tokens[-1], []))
        for token in tokens:
            if len(token) >= 2:
                candidate_keys.update(self._student_token_index.get(token, set()))
        phonetic_query = _voice_phonetic_text(normalized_query)
        phonetic_tokens = [token for token in phonetic_query.split() if token]
        if phonetic_tokens:
            if len(phonetic_tokens) >= 2:
                candidate_keys.update(self._student_phonetic_last_two_index.get(" ".join(phonetic_tokens[-2:]), []))
            candidate_keys.update(self._student_phonetic_last_name_index.get(phonetic_tokens[-1], []))
            for token in phonetic_tokens:
                if len(token) >= 2:
                    candidate_keys.update(self._student_phonetic_token_index.get(token, set()))
        return list(candidate_keys)

    def _token_set_ratio_for_tokens(
        self,
        query_tokens: frozenset[str],
        query_sorted: str,
        candidate_tokens: frozenset[str],
        candidate_sorted: str,
    ) -> int:
        if not query_tokens or not candidate_tokens:
            return 0
        common = query_tokens & candidate_tokens
        if not common:
            return _similarity_ratio(query_sorted, candidate_sorted)
        left_only = query_tokens - common
        right_only = candidate_tokens - common
        merged_left = " ".join(sorted([*common, *left_only]))
        merged_right = " ".join(sorted([*common, *right_only]))
        return max(
            _similarity_ratio(merged_left, merged_right),
            _similarity_ratio(query_sorted, candidate_sorted),
        )

    def _token_set_ratio_for_row(
        self,
        query_tokens: frozenset[str],
        query_sorted: str,
        row: ScoreStudentRow,
    ) -> int:
        return self._token_set_ratio_for_tokens(
            query_tokens,
            query_sorted,
            row.normalized_token_set,
            row.normalized_sorted_name,
        )

    def _best_fuzzy_score_for_row(
        self,
        normalized_query: str,
        query_tokens: tuple[str, ...],
        query_sorted: str,
        query_token_set: frozenset[str],
        row: ScoreStudentRow,
    ) -> int:
        best_score = 0
        allow_partial_variant_match = len(query_tokens) >= 2
        for variant in _normalized_name_variants(row.normalized_name):
            variant_tokens = tuple(token for token in variant.split() if token)
            variant_token_set = frozenset(variant_tokens)
            variant_sorted = " ".join(sorted(variant_tokens))
            score_candidates = [
                _similarity_ratio(normalized_query, variant),
                self._token_set_ratio_for_tokens(query_token_set, query_sorted, variant_token_set, variant_sorted),
            ]
            if allow_partial_variant_match:
                score_candidates.extend(
                    [
                        _partial_similarity_ratio(normalized_query, variant),
                        _compact_similarity_ratio(normalized_query, variant),
                    ]
                )
            score = max(
                *score_candidates,
            )
            ordered_score = _ordered_token_subsequence_score(query_tokens, variant_tokens)
            if ordered_score:
                score = max(score, ordered_score)
            best_score = max(best_score, score)
        return best_score

    def _best_phonetic_score_for_row(
        self,
        phonetic_query: str,
        phonetic_tokens: tuple[str, ...],
        phonetic_sorted: str,
        phonetic_token_set: frozenset[str],
        row: ScoreStudentRow,
    ) -> int:
        if not phonetic_query or not row.phonetic_name:
            return 0
        best_score = 0
        allow_partial_variant_match = len(phonetic_tokens) >= 2
        for variant in _normalized_name_variants(row.phonetic_name):
            variant_tokens = tuple(token for token in variant.split() if token)
            variant_token_set = frozenset(variant_tokens)
            variant_sorted = " ".join(sorted(variant_tokens))
            score_candidates = [
                _similarity_ratio(phonetic_query, variant),
                self._token_set_ratio_for_tokens(
                    phonetic_token_set,
                    phonetic_sorted,
                    variant_token_set,
                    variant_sorted,
                ),
            ]
            if allow_partial_variant_match:
                score_candidates.extend(
                    [
                        _partial_similarity_ratio(phonetic_query, variant),
                        _compact_similarity_ratio(phonetic_query, variant),
                    ]
                )
            score = max(*score_candidates)
            ordered_score = _ordered_token_subsequence_score(phonetic_tokens, variant_tokens)
            if ordered_score:
                score = max(score, ordered_score)
            best_score = max(best_score, min(score, 96))
        return best_score

    def _is_confident_fuzzy_match(
        self,
        normalized_query: str,
        query_token_set: frozenset[str],
        phonetic_query_token_set: frozenset[str],
        best_row: ScoreStudentRow,
        best_score: int,
        second_best_score: int,
    ) -> bool:
        if len(query_token_set) < 2:
            return False
        score_gap = best_score - second_best_score
        token_overlap = len(query_token_set & best_row.normalized_token_set)
        phonetic_overlap = len(phonetic_query_token_set & best_row.phonetic_token_set)
        effective_overlap = max(token_overlap, phonetic_overlap)
        if best_score >= 94 and score_gap >= 4:
            return True
        if normalized_query in best_row.normalized_name and best_score >= 88 and score_gap >= 4:
            return True
        if effective_overlap >= min(2, len(query_token_set)) and best_score >= 86 and score_gap >= 6:
            return True
        if len(query_token_set) >= 3 and effective_overlap >= len(query_token_set) - 1 and best_score >= 90 and score_gap >= 4:
            return True
        return False

    def _phonetic_exact_is_ambiguous(
        self,
        index: dict[str, list[str]],
        phonetic_key: str,
        exact_row_key: str,
    ) -> bool:
        if not phonetic_key:
            return False
        return any(row_key != exact_row_key for row_key in index.get(phonetic_key, []))

    def _disambiguate_tied_rows(
        self,
        candidates: list[ScoreStudentRow],
    ) -> tuple[ScoreStudentRow | None, list[ScoreStudentRow]]:
        """KHMER #B: Khi nhiều ứng viên cùng phonetic key, ưu tiên row chưa có điểm.

        Heuristic 80/20: GV nhập điểm tuần tự, thường chỉ 1 trong N ứng viên còn
        rỗng. Nếu chỉ 1 row chưa có cả `pending_score` và `current_score` →
        auto-pick row đó. Nếu không phân biệt được, trả None + danh sách tied.
        """
        if not candidates:
            return None, []
        if len(candidates) == 1:
            return candidates[0], []
        empty_rows = [
            row
            for row in candidates
            if not str(row.pending_score or "").strip() and not str(row.current_score or "").strip()
        ]
        if len(empty_rows) == 1:
            return empty_rows[0], []
        # Mọi row đều có điểm hoặc đều rỗng → để tied list cho caller xử lý.
        return None, list(candidates)

    def _match_student(self, query: str, *, dry_run: bool = False) -> tuple[ScoreStudentRow | None, int]:
        """Looks up a student row by transcribed query.

        Args:
            dry_run: BUG #10 FIX. Khi True, các nhánh tied KHÔNG được phép set
                `_voice_tied_candidates` (side-effect state). Cache đọc/ghi vẫn
                được phép vì cache là read-only về mặt UI state.
        """
        normalized_query = _normalize_diacritic_text(query)
        if not normalized_query:
            return None, 0
        query_tokens = tuple(token for token in normalized_query.split() if token)
        phonetic_query = _voice_phonetic_text(normalized_query)
        phonetic_query_tokens = tuple(token for token in phonetic_query.split() if token)
        phonetic_query_token_set = frozenset(phonetic_query_tokens)
        # BUG-01 FIX: Acquire lock khi đọc dữ liệu chia sẻ với main thread
        # BUG-04 FIX: Evict cache khi vượt VOICE_MATCH_CACHE_MAX_SIZE
        with self._score_data_lock:
            cached_match = self._voice_match_cache.get(normalized_query)
            if cached_match is not None:
                row_key, cached_score = cached_match
                cached_row = self._score_rows_by_key.get(row_key)
                if cached_row is not None:
                    # IMP-C3: LRU — di chuyển entry vừa truy cập lên cuối dict (most recently used)
                    del self._voice_match_cache[normalized_query]
                    self._voice_match_cache[normalized_query] = cached_match
                    # IMP-D3: Đếm cache hit
                    self._voice_cache_hits = getattr(self, "_voice_cache_hits", 0) + 1
                    return cached_row, cached_score
            # IMP-D3: Đếm cache miss
            self._voice_cache_misses = getattr(self, "_voice_cache_misses", 0) + 1
            exact_full = self._student_full_index.get(normalized_query, [])
            if len(exact_full) == 1:
                row = self._score_rows_by_key[exact_full[0]]
                if len(query_tokens) <= 2 and self._phonetic_exact_is_ambiguous(
                    self._student_phonetic_full_index,
                    phonetic_query,
                    row.row_key,
                ):
                    return None, 0
                self._cache_voice_match(normalized_query, row.row_key, 100)
                return row, 100
            if len(query_tokens) == 2 and phonetic_query:
                phonetic_last_two_matches = set(self._student_phonetic_last_two_index.get(phonetic_query, []))
                if len(phonetic_last_two_matches) > 1:
                    tied_candidates = [
                        self._score_rows_by_key[k]
                        for k in phonetic_last_two_matches
                        if k in self._score_rows_by_key
                    ]
                    picked, tied = self._disambiguate_tied_rows(tied_candidates)
                    if picked is not None:
                        # KHMER #B: heuristic chọn row chưa có điểm.
                        # BUG #1 FIX: KHÔNG cache — heuristic phụ thuộc vào
                        # `pending_score`/`current_score` (state có thể đổi).
                        return picked, 90
                    # KHMER #B: cùng rỗng → set picker state cho lệnh tiếp theo.
                    if tied and not dry_run:
                        self._set_voice_tied_candidates([row.row_key for row in tied])
                    return None, 0
            if len(query_tokens) == 1 and phonetic_query:
                phonetic_last_matches = set(self._student_phonetic_last_name_index.get(phonetic_query, []))
                if len(phonetic_last_matches) > 1:
                    tied_candidates = [
                        self._score_rows_by_key[k]
                        for k in phonetic_last_matches
                        if k in self._score_rows_by_key
                    ]
                    picked, tied = self._disambiguate_tied_rows(tied_candidates)
                    if picked is not None:
                        # BUG #1 FIX: KHÔNG cache heuristic pick.
                        return picked, 86
                    if tied and not dry_run:
                        self._set_voice_tied_candidates([row.row_key for row in tied])
                    return None, 0
            exact_last_two = self._student_last_two_index.get(normalized_query, [])
            if len(exact_last_two) == 1:
                row = self._score_rows_by_key[exact_last_two[0]]
                self._cache_voice_match(normalized_query, row.row_key, 96)
                return row, 96
            exact_last = self._student_last_name_index.get(normalized_query, [])
            if len(query_tokens) >= 2 and len(exact_last) == 1:
                row = self._score_rows_by_key[exact_last[0]]
                return row, 94
            if phonetic_query and phonetic_query != normalized_query:
                exact_phonetic_full = self._student_phonetic_full_index.get(phonetic_query, [])
                if len(exact_phonetic_full) == 1:
                    row = self._score_rows_by_key[exact_phonetic_full[0]]
                    return row, 96
                exact_phonetic_last_two = self._student_phonetic_last_two_index.get(phonetic_query, [])
                if len(exact_phonetic_last_two) == 1:
                    row = self._score_rows_by_key[exact_phonetic_last_two[0]]
                    return row, 93
                if len(query_tokens) >= 2:
                    exact_phonetic_last = self._student_phonetic_last_name_index.get(phonetic_query, [])
                    if len(exact_phonetic_last) == 1:
                        row = self._score_rows_by_key[exact_phonetic_last[0]]
                        return row, 90

            # KHMER #A — Tier 3: strict phonetic fallback
            #   Bắt các tên Khmer khi STT đoán sai âm cuối / vần.
            #   Chỉ áp khi tier 1/2 đã thất bại.
            strict_query = _voice_phonetic_text_strict(normalized_query)
            if strict_query and strict_query != phonetic_query:
                strict_full_matches = [
                    row
                    for row in self._score_rows_by_key.values()
                    if row.strict_phonetic_name == strict_query
                ]
                if len(strict_full_matches) == 1:
                    row = strict_full_matches[0]
                    self._cache_voice_match(normalized_query, row.row_key, 88)
                    return row, 88
                if len(strict_full_matches) >= 2:
                    # KHMER #B: tied trên strict-key — ưu tiên row chưa có điểm.
                    picked, tied = self._disambiguate_tied_rows(strict_full_matches)
                    if picked is not None:
                        # BUG #1 FIX: KHÔNG cache heuristic pick.
                        return picked, 84
                    if tied and not dry_run:
                        self._set_voice_tied_candidates([row.row_key for row in tied])
                    return None, 0
                if len(query_tokens) >= 2:
                    strict_last_two_matches = [
                        row
                        for row in self._score_rows_by_key.values()
                        if row.strict_phonetic_last_two == strict_query
                    ]
                    if len(strict_last_two_matches) == 1:
                        row = strict_last_two_matches[0]
                        self._cache_voice_match(normalized_query, row.row_key, 86)
                        return row, 86
                    if len(strict_last_two_matches) >= 2:
                        picked, tied = self._disambiguate_tied_rows(strict_last_two_matches)
                        if picked is not None:
                            # BUG #1 FIX: KHÔNG cache heuristic pick.
                            return picked, 82
                        if tied and not dry_run:
                            self._set_voice_tied_candidates([row.row_key for row in tied])
                        return None, 0
                strict_last_name_matches = [
                    row
                    for row in self._score_rows_by_key.values()
                    if row.strict_phonetic_last_name == strict_query
                ]
                if len(strict_last_name_matches) == 1 and len(query_tokens) >= 2:
                    row = strict_last_name_matches[0]
                    self._cache_voice_match(normalized_query, row.row_key, 82)
                    return row, 82
                if len(strict_last_name_matches) >= 2:
                    picked, tied = self._disambiguate_tied_rows(strict_last_name_matches)
                    if picked is not None and len(query_tokens) >= 2:
                        # BUG #1 FIX: KHÔNG cache heuristic pick.
                        return picked, 80
                    if tied and not dry_run and len(query_tokens) >= 2:
                        self._set_voice_tied_candidates([row.row_key for row in tied])
                    return None, 0

            candidate_keys = self._candidate_row_keys_for_query(normalized_query)
            candidate_rows = (
                [self._score_rows_by_key[row_key] for row_key in candidate_keys if row_key in self._score_rows_by_key]
                if candidate_keys
                else list(self._score_rows_by_key.values())
            )
            query_sorted = " ".join(sorted(query_tokens))
            query_token_set = frozenset(query_tokens)
            phonetic_query_sorted = " ".join(sorted(phonetic_query_tokens))
            best_row: ScoreStudentRow | None = None
            best_score = 0
            second_best_score = 0
            for row in candidate_rows:
                score = max(
                    self._best_fuzzy_score_for_row(
                        normalized_query,
                        query_tokens,
                        query_sorted,
                        query_token_set,
                        row,
                    ),
                    self._best_phonetic_score_for_row(
                        phonetic_query,
                        phonetic_query_tokens,
                        phonetic_query_sorted,
                        phonetic_query_token_set,
                        row,
                    ),
                )
                if normalized_query == row.normalized_last_two:
                    score = max(score, 95)
                elif normalized_query == row.normalized_last_name:
                    score = max(score, 93)
                elif normalized_query in row.normalized_name:
                    score = max(score, min(90, 72 + (len(normalized_query) * 4)))
                if phonetic_query == row.phonetic_last_two:
                    score = max(score, 92)
                elif phonetic_query == row.phonetic_last_name:
                    score = max(score, 88)
                if score > best_score:
                    second_best_score = best_score
                    best_score = score
                    best_row = row
                elif score > second_best_score:
                    second_best_score = score
            if best_row is not None and best_score >= 72 and self._is_confident_fuzzy_match(
                normalized_query,
                query_token_set,
                phonetic_query_token_set,
                best_row,
                best_score,
                second_best_score,
            ):
                return best_row, best_score
            return None, 0

    def _cache_voice_match(self, query: str, row_key: str, score: int) -> None:
        """Lưu kết quả voice match vào cache với giới hạn kích thước (BUG-04 FIX).

        Khi cache vượt VOICE_MATCH_CACHE_MAX_SIZE, xóa ~50% entry cũ nhất.
        Phải gọi trong khi đang giữ _score_data_lock.
        """
        if len(self._voice_match_cache) >= VOICE_MATCH_CACHE_MAX_SIZE:
            # Xóa nửa đầu (entry cũ nhất theo insertion order — Python 3.7+ dict giữ thứ tự)
            keys_to_remove = list(self._voice_match_cache.keys())[: VOICE_MATCH_CACHE_MAX_SIZE // 2]
            for key in keys_to_remove:
                del self._voice_match_cache[key]
        self._voice_match_cache[query] = (row_key, score)
        # IMP-D3: Log cache stats mỗi 50 lần cache miss (tỷ lệ hit/miss)
        total_misses = getattr(self, "_voice_cache_misses", 0)
        if total_misses > 0 and total_misses % 50 == 0:
            total_hits = getattr(self, "_voice_cache_hits", 0)
            total = total_hits + total_misses
            hit_rate = (total_hits / total * 100) if total > 0 else 0
            self._log(f"Voice cache stats: {total_hits} hits / {total_misses} misses ({hit_rate:.0f}% hit rate, {len(self._voice_match_cache)} entries)", tag=LogTag.INFO)

    def _ensure_row_visible_in_tree(self, row_key: str) -> None:
        """L4 FIX: Nếu dòng đang bị search filter ẩn, gỡ filter để voice match hiển thị.

        Khi giáo viên đang lọc danh sách mà đọc tên một học sinh nằm ngoài kết quả
        lọc, dòng đó không có trong `_tree_item_by_key` → không repaint/highlight
        được → không thấy phản hồi. Tự xóa ô tìm để hiện lại toàn bộ.
        """
        if row_key in self._tree_item_by_key:
            return
        if row_key not in self._score_rows_by_key:
            return
        search_var = getattr(self, "_search_var", None)
        if search_var is None or not search_var.get().strip():
            return
        search_var.set("")  # trace -> _filter_score_tree -> _refresh_score_tree

    def _highlight_pending_row(self, row_key: str) -> None:
        self._ensure_row_visible_in_tree(row_key)
        item = self._tree_item_by_key.get(row_key)
        if item:
            self.preview_tree.selection_set(item)
            self.preview_tree.focus(item)
            self.preview_tree.see(item)

    def _apply_voice_match(self, match: VoiceMatchResult) -> None:
        if match.row_key not in self._score_rows_by_key:
            self.voice_status_var.set("⚠️ Học sinh nhận dạng không còn trong danh sách đã quét.")
            return
        row = self._score_rows_by_key[match.row_key]
        existing_str = row.pending_score or row.current_score
        if existing_str:
            existing_str = existing_str.strip()
        if existing_str:
            try:
                existing_value = float(existing_str.replace(",", "."))
            except (ValueError, AttributeError):
                existing_value = None
            if existing_value is not None:
                self._show_score_conflict_dialog(match, existing_str, existing_value)
                return
        self._do_apply_voice_match(match)

    def _do_apply_voice_match(self, match: VoiceMatchResult) -> None:
        if match.row_key not in self._score_rows_by_key:
            return
        self._apply_row_patch(
            match.row_key,
            pending_score=match.score_text,
            status=RowStatus.PENDING,
            recognized_text=match.transcript,
            match_score=match.match_score,
            reason="nhận dạng giọng nói",
        )
        # PERF #4: cập nhật LRU tên gần đây (đẩy lên đầu, deque maxlen tự cắt)
        try:
            student_name = (match.student_name or "").strip()
            if student_name:
                # Xoá entry cũ (case-sensitive theo full name) để re-rank lên đầu
                with self._score_data_lock:
                    try:
                        self._voice_recent_names.remove(student_name)
                    except ValueError:
                        pass
                    self._voice_recent_names.appendleft(student_name)
        except Exception:
            pass
        self._ensure_row_visible_in_tree(match.row_key)
        item = self._tree_item_by_key.get(match.row_key)
        if item:
            self.preview_tree.selection_set(item)
            self.preview_tree.focus(item)
            self.preview_tree.see(item)
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        self.voice_status_var.set(f"✅ {match.student_name} -> {match.score_text} điểm ({match.match_score}%)")
        self.voice_summary_var.set(f"Đã quét {len(self._score_rows_by_key)} học sinh. Còn {pending_count} dòng chờ ghi.")
        self._focus_preview_tree()
        self._beep_score_accepted()  # beep xác nhận điểm đã ghi vào hàng chờ
        self._log(f"PTT: '{match.transcript}' -> {match.student_name} = {match.score_text} điểm ({match.match_score}%).", tag=LogTag.SUCCESS)

    def _show_score_conflict_dialog(self, match: VoiceMatchResult, existing_str: str, existing_value: float) -> None:
        self._beep_needs_confirmation()
        resolved_scores = _build_conflict_resolution_scores(existing_value, match.score_value)
        avg_value = resolved_scores["average"]
        plus_one_value = resolved_scores["plus_one"]
        accumulate_value = resolved_scores["accumulate"]
        avg_text = _format_score_value(avg_value)
        plus_one_text = _format_score_value(plus_one_value)
        accumulate_text = _format_score_value(accumulate_value)
        # BUG-10 FIX: Tạm disable PTT Space binding khi conflict dialog hiện
        _ptt_was_enabled = self._ptt_enabled
        if _ptt_was_enabled:
            self._unbind_ptt_space_bindings()
        # L3 FIX: đánh dấu dialog đang mở để chặn PTT result đã queue apply chồng.
        self._voice_conflict_dialog_open = True
        dlg = tk.Toplevel(self.root)
        dlg.title("Học sinh đã có điểm")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg="#fff8e1")
        body = tk.Frame(dlg, bg="#fff8e1", padx=24, pady=18)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            body, text=f"⚠️  {match.student_name} đã có điểm!",
            font=("Segoe UI", 12, "bold"), bg="#fff8e1", fg="#b8860b", anchor="w",
        ).pack(fill=tk.X, pady=(0, 10))
        info = tk.Frame(body, bg="#fff3cd", highlightthickness=1, highlightbackground="#e0c36a", padx=12, pady=8)
        info.pack(fill=tk.X, pady=(0, 14))
        tk.Label(info, text=f"Điểm hiện tại:  {existing_str}", font=("Segoe UI", 10), bg="#fff3cd", fg="#664d03", anchor="w").pack(fill=tk.X)
        tk.Label(info, text=f"Điểm mới đọc:  {match.score_text}", font=("Segoe UI", 10), bg="#fff3cd", fg="#664d03", anchor="w").pack(fill=tk.X)
        chosen = {"value": False}

        def _restore_ptt_bindings() -> None:
            """BUG-10 FIX: Restore PTT Space binding sau khi dialog đóng."""
            # L3 FIX: gỡ cờ chặn PTT result khi dialog đóng (mọi đường thoát đều
            # đi qua đây: on_choice và WM_DELETE_WINDOW).
            self._voice_conflict_dialog_open = False
            if _ptt_was_enabled and self._ptt_enabled:
                self._bind_ptt_space_bindings()

        def on_choice(choice: int) -> None:
            if chosen["value"]:
                return
            chosen["value"] = True
            dlg.destroy()
            _restore_ptt_bindings()
            if choice == 1:
                self._do_apply_voice_match(match)
                self._log(f"Conflict: thay thế điểm {match.student_name} = {match.score_text}.", tag=LogTag.SUCCESS)
            elif choice == 2:
                self.voice_status_var.set(f"⏭️ Giữ nguyên điểm {existing_str} cho {match.student_name}.")
                self._log(f"Conflict: giữ nguyên điểm {existing_str} cho {match.student_name}.", tag=LogTag.WARNING)
            elif choice == 3:
                avg_match = VoiceMatchResult(
                    row_key=match.row_key, student_name=match.student_name,
                    score_text=avg_text, score_value=avg_value,
                    match_score=match.match_score, transcript=match.transcript,
                )
                self._do_apply_voice_match(avg_match)
                self._log(f"Conflict: trung bình ({existing_str}+{match.score_text})/2 = {avg_text} cho {match.student_name}.", tag=LogTag.SUCCESS)
            elif choice == 4:
                plus_match = VoiceMatchResult(
                    row_key=match.row_key, student_name=match.student_name,
                    score_text=plus_one_text, score_value=plus_one_value,
                    match_score=match.match_score, transcript=match.transcript,
                )
                self._do_apply_voice_match(plus_match)
                self._log(f"Conflict: cộng 1 điểm {existing_str} -> {plus_one_text} cho {match.student_name}.", tag=LogTag.SUCCESS)
            elif choice == 5:
                accumulate_match = VoiceMatchResult(
                    row_key=match.row_key, student_name=match.student_name,
                    score_text=accumulate_text, score_value=accumulate_value,
                    match_score=match.match_score, transcript=match.transcript,
                )
                self._do_apply_voice_match(accumulate_match)
                self._log(f"Conflict: dồn điểm {existing_str} + {match.score_text} = {accumulate_text} (max 10) cho {match.student_name}.", tag=LogTag.SUCCESS)

        btn_style = {"font": ("Segoe UI", 10), "relief": tk.SOLID, "borderwidth": 1,
                      "cursor": "hand2", "activeforeground": "#ffffff", "padx": 10, "pady": 6}
        buttons_data = [
            (f"⌨ 1 ┃  Thay thế bằng điểm mới  →  {match.score_text}", 1, "#2563eb", "#1d4ed8"),
            (f"⌨ 2 ┃  Giữ nguyên điểm hiện tại  →  {existing_str}", 2, "#6b7280", "#4b5563"),
            (f"⌨ 3 ┃  Trung bình ({existing_str} + {match.score_text}) ÷ 2  →  {avg_text}", 3, "#059669", "#047857"),
            (f"⌨ 4 ┃  Cộng thêm 1 điểm  →  {plus_one_text}", 4, "#d97706", "#b45309"),
            (f"⌨ 5 ┃  Dồn điểm ({existing_str} + {match.score_text})  →  {accumulate_text}  (max 10)", 5, "#dc2626", "#b91c1c"),
        ]
        for text, choice, bg_color, active_bg in buttons_data:
            btn = tk.Button(body, text=text, command=lambda c=choice: on_choice(c),
                            bg=bg_color, fg="#ffffff", activebackground=active_bg, **btn_style)
            btn.pack(fill=tk.X, pady=3)
            self._bind_hover(btn, active_bg)

        tk.Label(body, text="Nhấn phím 1-5 hoặc click để chọn  •  Esc = giữ nguyên",
                 font=("Segoe UI", 8), bg="#fff8e1", fg="#9ca3af").pack(pady=(10, 0))
        dlg.bind("1", lambda _e: on_choice(1))
        dlg.bind("2", lambda _e: on_choice(2))
        dlg.bind("3", lambda _e: on_choice(3))
        dlg.bind("4", lambda _e: on_choice(4))
        dlg.bind("5", lambda _e: on_choice(5))
        dlg.bind("<Escape>", lambda _e: on_choice(2))
        # BUG-10 FIX (bổ sung): Xử lý trường hợp user đóng dialog bằng nút X
        dlg.protocol("WM_DELETE_WINDOW", lambda: on_choice(2))
        dlg.update_idletasks()
        pw = self.root.winfo_width()
        ph = self.root.winfo_height()
        px = self.root.winfo_x()
        py = self.root.winfo_y()
        dw = dlg.winfo_width()
        dh = dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw) // 2}+{py + (ph - dh) // 2}")
        dlg.focus_force()
        self.root.bell()

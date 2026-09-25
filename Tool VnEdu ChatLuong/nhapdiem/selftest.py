"""Kiểm tra nhanh logic lõi (`--self-test`)."""

from __future__ import annotations

import tempfile
import threading
import types
from pathlib import Path

from .compat import sr
from .models import ScoreStudentRow
from .scores import (
    _build_score_apply_payload,
    _round_score_to_one_decimal,
    parse_manual_score_text,
)
from .storage import _load_json_object_file, _write_json_atomic_file
from .ui.app import VnEduStandaloneApp
from .voice.parsing import (
    _clean_voice_name_candidate,
    _voice_pick_tied_index,
    voice_parse_score_text,
)
from .voice.phonetics import (
    _normalize_diacritic_text,
    _voice_phonetic_text,
    _voice_phonetic_text_strict,
    _voice_phonetic_token_strict,
)


def _run_self_tests() -> None:
    """Runs fast regression checks for pure logic that does not need the GUI."""
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    def build_test_row(row_key: str, row_index: int, student_name: str) -> ScoreStudentRow:
        normalized_name = _normalize_diacritic_text(student_name)
        name_parts = normalized_name.split()
        phonetic_name = _voice_phonetic_text(normalized_name)
        phonetic_parts = phonetic_name.split()
        return ScoreStudentRow(
            row_key=row_key,
            row_index=row_index,
            row_id=row_key,
            student_code=f"HS{row_index:03d}",
            student_name=student_name,
            current_score="",
            target_input_name=f"score-{row_index}",
            normalized_name=normalized_name,
            normalized_last_name=(name_parts[-1] if name_parts else normalized_name),
            normalized_last_two=(" ".join(name_parts[-2:]) if len(name_parts) >= 2 else normalized_name),
            normalized_sorted_name=" ".join(sorted(name_parts)),
            normalized_token_set=frozenset(name_parts),
            phonetic_name=phonetic_name,
            phonetic_last_name=(phonetic_parts[-1] if phonetic_parts else phonetic_name),
            phonetic_last_two=(" ".join(phonetic_parts[-2:]) if len(phonetic_parts) >= 2 else phonetic_name),
            phonetic_sorted_name=" ".join(sorted(phonetic_parts)),
            phonetic_token_set=frozenset(phonetic_parts),
            strict_phonetic_name=_voice_phonetic_text_strict(normalized_name),
            strict_phonetic_last_name=_voice_phonetic_token_strict(name_parts[-1]) if name_parts else "",
            strict_phonetic_last_two=(
                _voice_phonetic_text_strict(" ".join(name_parts[-2:]))
                if len(name_parts) >= 2
                else _voice_phonetic_text_strict(normalized_name)
            ),
        )

    manual_cases = {
        "10": 10.0,
        "10.0": 10.0,
        "8,75": 8.75,
        "0": 0.0,
        " 7.5 ": 7.5,
        "10.5": None,
        "abc 8": None,
        "": None,
    }
    for raw_value, expected in manual_cases.items():
        actual = parse_manual_score_text(raw_value)
        require(
            actual == expected,
            f"parse_manual_score_text({raw_value!r}) -> {actual!r}, expected {expected!r}",
        )

    voice_cases = {
        "điểm tám phẩy năm": 8.5,
        "Nguyễn Văn A điểm 9": 9.0,
        "mười": 10.0,
        "điểm 10,0": 10.0,
        "điểm 11": None,
    }
    for raw_value, expected in voice_cases.items():
        actual = voice_parse_score_text(raw_value)
        require(
            actual == expected,
            f"voice_parse_score_text({raw_value!r}) -> {actual!r}, expected {expected!r}",
        )

    voice_app = object.__new__(VnEduStandaloneApp)
    voice_app._score_data_lock = threading.Lock()
    voice_app._voice_state_lock = threading.Lock()
    voice_app._score_rows_by_key = {
        "r1": build_test_row("r1", 1, "Nguyễn Văn Năm"),
        "r2": build_test_row("r2", 2, "Trần Thị Sáu"),
        "r3": build_test_row("r3", 3, "Nguyễn Văn An"),
        "r4": build_test_row("r4", 4, "Lê Văn An"),
        "r5": build_test_row("r5", 5, "Nguyễn Diễm"),
    }
    voice_app._student_aliases = {}
    voice_app._student_full_index = {}
    voice_app._student_last_name_index = {}
    voice_app._student_last_two_index = {}
    voice_app._student_token_index = {}
    voice_app._student_phonetic_full_index = {}
    voice_app._student_phonetic_last_name_index = {}
    voice_app._student_phonetic_last_two_index = {}
    voice_app._student_phonetic_token_index = {}
    voice_app._voice_match_cache = {}
    voice_app._voice_cache_hits = 0
    voice_app._voice_cache_misses = 0
    voice_app._ptt_roster_revision = 0
    voice_app._voice_pending_row_key = None
    voice_app._voice_pending_at = 0.0
    voice_app._voice_pending_roster_revision = 0
    VnEduStandaloneApp._rebuild_student_indices(voice_app)

    parser_cases = {
        "Nguyễn Văn Năm điểm tám": ("nguyen van nam", 8.0),
        "Trần Thị Sáu điểm chín": ("tran thi sau", 9.0),
        "em Nguyễn Văn An được 8 điểm": ("nguyen van an", 8.0),
        "thầy cho em Nguyễn Văn An hôm nay được tám điểm": ("nguyen van an", 8.0),
        "tám điểm cho em Nguyễn Văn An": ("nguyen van an", 8.0),
        "Nguyễn Văn An tám phẩy năm": ("nguyen van an", 8.5),
    }
    for raw_value, expected in parser_cases.items():
        pairs = VnEduStandaloneApp._candidate_name_score_pairs(voice_app, raw_value)
        normalized_pairs = [(_normalize_diacritic_text(name), score) for name, score in pairs]
        require(expected in normalized_pairs, f"Voice parser missed {raw_value!r}: {normalized_pairs!r}")
    no_score_pairs = VnEduStandaloneApp._candidate_name_score_pairs(voice_app, "lớp 6 Nguyễn Văn An")
    require(not no_score_pairs, f"Roster-aware parser should not treat class numbers as scores: {no_score_pairs!r}")

    # L2 regression: voice picker phải hỗ trợ cả "một/hai" lẫn "thứ nhất/thứ hai".
    require(
        _voice_pick_tied_index("thu nhat", 2) == (0, ""),
        "L2: 'thứ nhất' must resolve to picker index 0.",
    )
    require(
        _voice_pick_tied_index("thu hai 8", 2) == (1, "8"),
        "L2: 'thứ hai 8' must resolve to index 1 with leftover score '8'.",
    )
    require(
        _voice_pick_tied_index("hai 9", 2) == (1, "9"),
        "L2: 'hai 9' must resolve to index 1 with leftover '9'.",
    )
    require(
        _voice_pick_tied_index("ba", 2) == (None, ""),
        "L2: picker index beyond candidate_count must be rejected.",
    )
    require(
        _voice_pick_tied_index("con vit", 2) == (None, ""),
        "L2: non-picker transcript must not be treated as a pick.",
    )

    require(
        _clean_voice_name_candidate("Nguyễn Diem") == "Nguyễn",
        "Default name cleaner should strip score-word token from generic transcripts.",
    )
    require(
        _clean_voice_name_candidate("Nguyễn Diem", strip_score_words=False) == "Nguyễn Diem",
        "Preserved name cleaner should keep score-word token for real student names.",
    )

    row, score = VnEduStandaloneApp._match_student(voice_app, "Nguyễn Văn Năm")
    require(row is not None and row.student_name == "Nguyễn Văn Năm" and score >= 96, "Full-name match regressed.")
    row, _score = VnEduStandaloneApp._match_student(voice_app, "An")
    require(row is None, "Single-token last-name command should not auto-match.")
    row, _score = VnEduStandaloneApp._match_student(voice_app, "Văn An")
    require(row is None, "Ambiguous last-two-token command should not auto-match.")

    match, message = VnEduStandaloneApp._resolve_voice_command(voice_app, "em Nguyễn Văn An được 8 điểm")
    require(
        match is not None and match.student_name == "Nguyễn Văn An" and match.score_text == "8",
        f"Natural voice command failed: {match!r}, {message!r}",
    )
    match, message = VnEduStandaloneApp._resolve_voice_command(
        voice_app,
        "thầy cho em Nguyễn Văn An hôm nay được tám điểm",
    )
    require(
        match is not None and match.student_name == "Nguyễn Văn An" and match.score_text == "8",
        f"Roster-aware voice command failed: {match!r}, {message!r}",
    )
    match, message = VnEduStandaloneApp._resolve_voice_command(voice_app, "Nguyễn Diem")
    require(
        match is None and message == "NAME_ONLY:Nguyễn Diễm" and voice_app._voice_pending_row_key == "r5",
        f"Preserved name-only fallback failed for score-word name: {match!r}, {message!r}",
    )
    VnEduStandaloneApp._set_voice_pending(voice_app, "r1", roster_revision=0)
    match, message = VnEduStandaloneApp._resolve_voice_command(voice_app, "8", roster_revision=1)
    require(match is None, f"Stale pending row should not survive roster revision changes: {match!r}, {message!r}")
    VnEduStandaloneApp._set_voice_pending(voice_app, "r1", roster_revision=0)
    match, message = VnEduStandaloneApp._resolve_voice_command(voice_app, "lạ 8")
    require(match is None, f"Mixed unknown name+score should not apply pending row: {match!r}, {message!r}")
    match, message = VnEduStandaloneApp._resolve_voice_command(voice_app, "8")
    require(
        match is not None and match.student_name == "Nguyễn Văn Năm" and match.score_text == "8",
        f"Score-only pending command failed: {match!r}, {message!r}",
    )

    recognition_app = object.__new__(VnEduStandaloneApp)
    recognition_app._recognizer_phrase_list_supported = False
    recognition_app._voice_hints_primary = ["Nguyễn Văn Năm"]
    recognition_calls: list[object] = []
    recognition_app._recognize_google_candidates = (  # type: ignore[method-assign]
        lambda _audio_obj, phrase_hints=None, **_kwargs: (
            recognition_calls.append(phrase_hints) or ["không khớp"]
        )
    )
    recognition_app._best_voice_match_from_transcripts = (  # type: ignore[method-assign]
        lambda transcripts, **_kwargs: (None, transcripts[0])
    )
    recognition_app._recognize_google_fallback_text = (  # type: ignore[method-assign]
        lambda _audio_obj, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Fallback should not run when show_all has text.")
        )
    )
    best_match, best_text = VnEduStandaloneApp._recognize_transcripts(recognition_app, object())
    require(best_match is None and best_text == "không khớp", "Recognition no-match text handling regressed.")
    require(recognition_calls == [None], f"Recognition should make one non-hint request, got {recognition_calls!r}")

    class ConfidenceRecognizer:
        def recognize_google(self, _audio_obj: object, language: str = "vi-VN", show_all: bool = False, **_kwargs: object) -> dict[str, object]:
            return {
                "alternative": [
                    {"transcript": "tra", "confidence": 0.1},
                    {"transcript": "tam", "confidence": 0.9},
                ]
            }

    confidence_app = object.__new__(VnEduStandaloneApp)
    confidence_app._recognizer = ConfidenceRecognizer()
    confidence_app._recognizer_phrase_list_supported = False
    confidence_candidates = VnEduStandaloneApp._recognize_google_candidates(confidence_app, object())
    require(
        confidence_candidates == [("tam", 0.9), ("tra", 0.1)],
        f"Recognition confidence ordering regressed: {confidence_candidates!r}",
    )

    # V6 regression: trong cùng một response Google, ứng viên #1 (thứ tự âm học tốt
    # nhất) phải được giữ khi ứng viên xếp sau chỉ hơn fuzzy 1 điểm; nhưng phải cho
    # phép override khi chênh lệch đủ lớn (>= VOICE_TRANSCRIPT_OVERRIDE_MARGIN).
    class _RankMatch:
        def __init__(self, name: str, score: int) -> None:
            self.student_name = name
            self.match_score = score
            self.row_key = name
            self.score_text = "8"
            self.score_value = 8.0
            self.transcript = name

    rank_app = object.__new__(VnEduStandaloneApp)
    rank_resolve = {
        "an": ("Nguyen Van An", 90),
        "ann": ("Nguyen Van Ann", 91),
        "anh": ("Nguyen Van Anh", 97),
    }

    def _fake_resolve(transcript: str, *, roster_revision=None, dry_run=False):
        key = transcript.strip().lower()
        if key in rank_resolve:
            name, score = rank_resolve[key]
            return _RankMatch(name, score), "ok"
        return None, "no"

    rank_app._resolve_voice_command = _fake_resolve  # type: ignore[method-assign]
    small_gap_match, _t = VnEduStandaloneApp._best_voice_match_from_transcripts(
        rank_app, [("An", 0.0), ("Ann", 0.0)]
    )
    require(
        small_gap_match is not None and small_gap_match.student_name == "Nguyen Van An",
        f"V6: small fuzzy gap must respect Google order, got {getattr(small_gap_match, 'student_name', None)!r}",
    )
    big_gap_match, _t2 = VnEduStandaloneApp._best_voice_match_from_transcripts(
        rank_app, [("An", 0.0), ("Anh", 0.0)]
    )
    require(
        big_gap_match is not None and big_gap_match.student_name == "Nguyen Van Anh",
        f"V6: big fuzzy gap must still override, got {getattr(big_gap_match, 'student_name', None)!r}",
    )
    higher_conf_match, _t3 = VnEduStandaloneApp._best_voice_match_from_transcripts(
        rank_app, [("An", 0.2), ("Ann", 0.9)]
    )
    require(
        higher_conf_match is not None and higher_conf_match.student_name == "Nguyen Van Ann",
        f"V6: higher-confidence later alt with >= fuzzy must win, got {getattr(higher_conf_match, 'student_name', None)!r}",
    )

    if sr is not None:
        class UnknownRecognizer:
            def recognize_google(self, _audio_obj: object, language: str = "vi-VN") -> str:
                raise sr.UnknownValueError()

        class RequestFailRecognizer:
            def recognize_google(self, _audio_obj: object, language: str = "vi-VN") -> str:
                raise sr.RequestError("offline")

        fallback_app = object.__new__(VnEduStandaloneApp)
        fallback_app._recognizer = UnknownRecognizer()
        require(
            VnEduStandaloneApp._recognize_google_fallback_text(fallback_app, object()) == "",
            "Fallback STT should quietly ignore UnknownValueError.",
        )
        fallback_app._recognizer = RequestFailRecognizer()
        try:
            VnEduStandaloneApp._recognize_google_fallback_text(fallback_app, object())
        except RuntimeError as error:
            require("Lỗi kết nối Google" in str(error), f"Unexpected fallback RequestError message: {error}")
        else:
            raise AssertionError("Fallback STT should raise on RequestError.")

    class FakeRoot:
        def __init__(self) -> None:
            self.bind_calls: list[tuple[str, str | None, str]] = []
            self.unbind_calls: list[tuple[str, str | None]] = []
            self.counter = 0

        def bind(self, sequence: str, _callback: object, add: str | None = None) -> str:
            self.counter += 1
            funcid = f"func{self.counter}"
            self.bind_calls.append((sequence, add, funcid))
            return funcid

        def unbind(self, sequence: str, funcid: str | None = None) -> None:
            self.unbind_calls.append((sequence, funcid))

    binding_app = object.__new__(VnEduStandaloneApp)
    binding_app.root = FakeRoot()
    binding_app._ptt_bind_ids = {}
    VnEduStandaloneApp._bind_ptt_keyboard(binding_app)
    VnEduStandaloneApp._bind_ptt_keyboard(binding_app)
    require(len(binding_app.root.bind_calls) == 3, f"PTT keyboard should bind once, got {binding_app.root.bind_calls!r}")
    VnEduStandaloneApp._unbind_ptt_space_bindings(binding_app)
    require(
        binding_app.root.unbind_calls == [
            ("<KeyPress-space>", "func1"),
            ("<KeyRelease-space>", "func2"),
        ],
        f"PTT Space unbind should target owned funcids: {binding_app.root.unbind_calls!r}",
    )
    require(
        "focus_out" in binding_app._ptt_bind_ids,
        "Conflict dialog Space unbind should leave FocusOut binding active.",
    )

    feedback_events: list[str] = []
    feedback_app = object.__new__(VnEduStandaloneApp)
    feedback_app._play_beep_sequence = lambda _pattern, event_name: feedback_events.append(event_name)  # type: ignore[method-assign]
    VnEduStandaloneApp._beep_recording_start(feedback_app)
    VnEduStandaloneApp._beep_uncertain_result(feedback_app)
    VnEduStandaloneApp._beep_needs_confirmation(feedback_app)
    VnEduStandaloneApp._beep_voice_error(feedback_app)
    VnEduStandaloneApp._beep_score_accepted(feedback_app)
    require(
        feedback_events == ["chưa chắc", "cần xác nhận", "lỗi voice", "ghi thành công"],
        f"Audio feedback event routing changed unexpectedly: {feedback_events!r}",
    )

    rounding_cases = {
        "8.25": "8.3",
        "8.24": "8.2",
        "9": "9",
        "bad": "bad",
    }
    for raw_value, expected in rounding_cases.items():
        actual = _round_score_to_one_decimal(raw_value)
        require(
            actual == expected,
            f"_round_score_to_one_decimal({raw_value!r}) -> {actual!r}, expected {expected!r}",
        )

    fake_rows = [
        types.SimpleNamespace(
            row_key="r1",
            row_index=1,
            row_id="row-1",
            student_code="HS001",
            student_name="Nguyen Van A",
            current_score="",
            target_input_name="score-1",
            pending_score="8.5",
        ),
        types.SimpleNamespace(
            row_key="r2",
            row_index=2,
            row_id="row-2",
            student_code="HS002",
            student_name="Tran Thi B",
            current_score="",
            target_input_name="",
            pending_score="9",
        ),
    ]
    payload = _build_score_apply_payload(fake_rows, score_field="pending_score")
    require(len(payload) == 1, f"Expected one valid payload row, got {len(payload)}")
    require(payload[0]["proposed_score"] == "8.5", "Payload score formatting changed unexpectedly.")

    with tempfile.TemporaryDirectory() as temp_dir:
        json_path = Path(temp_dir) / "sample.json"
        _write_json_atomic_file(json_path, {"ok": True})
        loaded_payload, backup_path, error = _load_json_object_file(json_path)
        require(
            error is None and backup_path is None and loaded_payload == {"ok": True},
            "Atomic JSON write/read did not round-trip.",
        )

        json_path.write_text("{bad json", encoding="utf-8")
        loaded_payload, backup_path, error = _load_json_object_file(json_path)
        require(loaded_payload == {}, "Corrupt JSON should return an empty payload.")
        require(error is not None, "Corrupt JSON should report the parse error.")
        require(backup_path is not None and backup_path.exists(), "Corrupt JSON should be backed up.")
        require(not json_path.exists(), "Corrupt JSON should be moved out of the active path.")

    print("SELF-TEST PASS: score, voice parser/matcher, recognition flow, PTT binding, and JSON recovery are OK.")

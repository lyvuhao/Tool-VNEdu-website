"""Các dataclass mô tả dữ liệu Sổ điểm, rule nhận xét và kết quả ghi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


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

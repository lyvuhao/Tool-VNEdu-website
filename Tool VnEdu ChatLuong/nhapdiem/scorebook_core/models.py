"""Dataclass cho luồng ghi điểm (chỉ có ở tool Nhập điểm)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ScoreWriteEntry:
    """One student row prepared for score preview/write-back."""

    row_index: int
    row_id: str
    student_code: str
    student_name: str
    target_column_key: str
    target_column_name: str
    current_score: str
    target_input_name: str
    proposed_score: str = ""
    status: str = "scanned"
    reason: str = ""


@dataclass
class ScoreWriteResult:
    """Write-back result summary after applying one score payload."""

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

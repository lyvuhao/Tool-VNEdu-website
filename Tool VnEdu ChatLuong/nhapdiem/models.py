"""Enum và dataclass dùng trong tool Nhập điểm."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Enums — thay thế magic strings bằng type-safe constants
# ---------------------------------------------------------------------------

class RowStatus(str, Enum):
    """Trạng thái hiển thị của mỗi dòng điểm học sinh trong TreeView.

    Kế thừa str để giá trị enum có thể dùng trực tiếp làm text hiển thị UI.
    """

    READY = "Sẵn sàng"
    PENDING = "Chờ ghi"
    SAVED = "Đã ghi"
    FILLED = "Đã điền (chưa lưu)"
    ERROR = "Lỗi ghi"


class AccessScopeMode(str, Enum):
    """Chế độ cache quyền truy cập sổ điểm.

    FULL_MATRIX: đã quét toàn bộ tổ hợp khối-lớp-môn.
    SUBJECT_FAST: chỉ quét nhanh cho 1 môn cụ thể.
    """

    FULL_MATRIX = "full_matrix"
    SUBJECT_FAST = "subject_fast"


class LogTag(str, Enum):
    """Tag phân loại cho hệ thống log nội bộ."""

    INFO = "log_info"
    SUCCESS = "log_success"
    WARNING = "log_warning"


@dataclass
class ScoreStudentRow:
    row_key: str
    row_index: int
    row_id: str
    student_code: str
    student_name: str
    current_score: str
    target_input_name: str
    pending_score: str = ""
    recognized_text: str = ""
    match_score: int = 0
    status: str = RowStatus.READY
    normalized_name: str = ""
    normalized_last_name: str = ""
    normalized_last_two: str = ""
    normalized_sorted_name: str = ""
    normalized_token_set: frozenset[str] = frozenset()
    phonetic_name: str = ""
    phonetic_last_name: str = ""
    phonetic_last_two: str = ""
    phonetic_sorted_name: str = ""
    phonetic_token_set: frozenset[str] = frozenset()
    # KHMER #A: strict phonetic keys (collapse final l/m/p, vần Khmer) — chỉ
    # so trong tier 3 fallback của _match_student để bắt tên Khmer khi STT
    # đoán sai âm cuối / vần.
    strict_phonetic_name: str = ""
    strict_phonetic_last_name: str = ""
    strict_phonetic_last_two: str = ""


@dataclass
class UndoRecord:
    row_key: str
    before: dict[str, object]
    after: dict[str, object]
    reason: str


@dataclass
class VoiceMatchResult:
    row_key: str
    student_name: str
    score_text: str
    score_value: float
    match_score: int
    transcript: str

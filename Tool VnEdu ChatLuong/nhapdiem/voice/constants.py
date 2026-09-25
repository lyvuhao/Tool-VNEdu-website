"""Hằng số và regex cho nhận dạng/parse giọng nói."""

from __future__ import annotations

import re


VOICE_SCORE_NUMBER_REPLACEMENTS = (
    ("mười", "10"),
    ("muời", "10"),
    ("mươi", "10"),
    ("một", "1"),
    ("mốt", "1"),
    ("hai", "2"),
    ("ba", "3"),
    ("bốn", "4"),
    ("tư", "4"),
    ("năm", "5"),
    ("lăm", "5"),
    ("sáu", "6"),
    ("sấu", "6"),
    ("bảy", "7"),
    ("bẩy", "7"),
    ("tám", "8"),
    ("tắm", "8"),
    ("chín", "9"),
    ("chính", "9"),
    ("không", "0"),
    ("linh", "0"),
)


VOICE_SCORE_WORD_PATTERN = re.compile(
    r"\b(?:điểm|điêm|diểm|đểm|điem|diem)\b",
    re.IGNORECASE,
)


VOICE_SCORE_DECIMAL_REPLACEMENTS = (
    ("rưỡi", ".5"),
    ("phẩy", "."),
    ("chấm", "."),
)


VOICE_SCORE_EXTRACT_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)")


VOICE_SCORE_DECIMAL_PATTERN = re.compile(r"(\d)\s*[.,]\s*(\d)")


VOICE_COMMAND_SPACE_PATTERN = re.compile(r"\s+")


VOICE_FILLER_PATTERN = re.compile(r"\b(?:ừm|ừ|ờ|à|ơ|dạ|vâng|nhé|ạ)\b", re.IGNORECASE)


MANUAL_SCORE_PATTERN = re.compile(r"^(?:10(?:[.,]0+)?|[0-9](?:[.,]\d+)?)$")


VOICE_SCORE_VALUE_TOKEN_RE = re.compile(
    r"^(?:10(?:[.,]0+)?|[0-9](?:[.,]\d+)?|mười|muời|mươi|một|mốt|hai|ba|bốn|tư|"
    r"năm|lăm|sáu|sấu|bảy|bẩy|tám|tắm|chín|chính|không|linh)$",
    re.IGNORECASE,
)


VOICE_SCORE_DECIMAL_MARKERS = {"phẩy", "phay", "chấm", "cham"}


VOICE_SCORE_HALF_MARKERS = {"rưỡi", "ruoi"}


VOICE_NAME_PREFIX_TOKENS = {"em", "bạn", "ban", "con", "bé", "be", "hs", "cho"}


VOICE_NAME_TRAILING_TOKENS = {
    "được",
    "duoc",
    "là",
    "la",
    "bằng",
    "bang",
    "cho",
    "điểm",
    "diem",
    "số",
    "so",
}


VOICE_SCORE_ONLY_CUE_TOKENS = VOICE_NAME_TRAILING_TOKENS | {"điểm", "diem"}


VOICE_HINT_PRIMARY_LIMIT = 96


VOICE_HINT_EXTENDED_LIMIT = 180


VOICE_HINT_SMART_LIMIT = 30  # PERF #4: số hint Google khi đã có context (nhỏ → response nhanh, top-1 chính xác hơn)


VOICE_HINT_RECENT_LIMIT = 6  # PERF #4: số tên dùng gần đây giữ trong LRU


VOICE_FAST_ACCEPT_SCORE = 94


VOICE_TRIM_MIN_DURATION = 0.18


VOICE_TRIM_LEAD_MARGIN_MS = 120


VOICE_TRIM_TAIL_MARGIN_MS = 180


VOICE_METER_FLOOR_DB = -58.0


VOICE_METER_CEIL_DB = -16.0


VOICE_PENDING_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# PERF: Hằng số cho recognition pipeline mới (parallel + keep-alive + adaptive)
# ---------------------------------------------------------------------------
VOICE_BOOST_TRIGGER_MAX_AMP = 0.012  # PERF #5: chỉ bật "boosted" attempt khi tín hiệu yếu


VOICE_BOOST_FORCE_MAX_AMP = 0.006    # PERF #5: tín hiệu cực yếu → bỏ "raw", thay bằng "boosted"


VOICE_RECOGNIZE_PARALLEL_TIMEOUT = 6.0  # PERF #1: hard cap cho 1 lần xử lý voice (bao cả 2 attempt song song)


VOICE_HTTP_CONNECT_TIMEOUT = 2.0     # PERF #8: connect timeout cho Google


VOICE_HTTP_READ_TIMEOUT = 4.5        # PERF #8: read timeout cho Google


VOICE_HTTP_PREWARM_TIMEOUT = 3.0


VOICE_HTTP_POOL_SIZE = 4


# --- Audio Enhancement Constants ---
# DEPRECATED (V1–V3 FIX): noise-gate sample-wise và pre-emphasis làm méo waveform
# gửi Google Speech (xóa phụ âm yếu, nghiêng phổ, sai thanh điệu). Đã gỡ khỏi
# _enhance_audio_for_recognition; giữ lại định nghĩa để tương thích ngược, KHÔNG
# dùng trong pipeline nhận dạng nữa.
VOICE_NOISE_GATE_THRESHOLD = 0.008       # [DEPRECATED] không còn dùng


VOICE_PRE_EMPHASIS_COEFF = 0.97          # [DEPRECATED] không còn dùng


VOICE_NORMALIZE_TARGET_PEAK = 0.92       # Chuẩn hóa đỉnh tín hiệu về mức này


VOICE_BOOST_RETRY_GAIN_DB = 8.0          # Tăng gain (dB) khi thử lại nhận dạng tín hiệu yếu


# V6 FIX: Khi Google trả nhiều alternative, phần tử ĐẦU là phán đoán âm học tốt
# nhất (đã sort theo confidence/thứ tự Google). Một alternative xếp sau chỉ được
# phép "qua mặt" alternative trước nếu điểm fuzzy khớp tên cao hơn ÍT NHẤT bằng
# margin này — tránh để nhiễu fuzzy 1 điểm override thứ tự âm học của Google
# (nguyên nhân gây nhầm học sinh khi nhiều tên đọc gần giống nhau).
VOICE_TRANSCRIPT_OVERRIDE_MARGIN = 5


_VOICE_PATTERN_NAME_DIEM_SCORE = re.compile(r"^(.+?)\s+diem\s+(\d+(?:[.,]\d+)?)$", re.IGNORECASE)


_VOICE_PATTERN_NAME_SCORE = re.compile(r"^(.+?)\s+(\d+(?:[.,]\d+)?)\s*(?:diem)?$", re.IGNORECASE)


_VOICE_PATTERN_SCORE_NAME = re.compile(r"^(?:diem\s+)?(\d+(?:[.,]\d+)?)\s+(.+?)$", re.IGNORECASE)


_VOICE_UNDO_PATTERN = re.compile(r"^(?:xóa|xoá|hủy|huỷ|bỏ|xóa đi|hủy đi|bỏ đi|undo)$", re.IGNORECASE)

"""Chuẩn hoá phiên âm tên học sinh (tiếng Việt, tiếng Khmer)."""

from __future__ import annotations

import re
import unicodedata

from .constants import VOICE_COMMAND_SPACE_PATTERN


def _replace_tokens(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    normalized = text
    for source, target in replacements:
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized)
    return normalized


def _normalize_diacritic_text(value: str) -> str:
    text = str(value or "").lower().strip()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", text)
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = normalized.replace("đ", "d")
    # BUG-06 FIX: Chỉ thay y→i cho các âm tiết an toàn (quy→qui, kỳ→ki),
    # KHÔNG thay cho tên riêng kết thúc bằng y (Thủy, Huy, Duy, Nguyệt...)
    # vì sẽ gây sai lệch fuzzy matching.
    # Loại bỏ rule y→i; để fuzzy matching tự xử lý sự khác biệt y/i.
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return VOICE_COMMAND_SPACE_PATTERN.sub(" ", normalized).strip()


def _voice_phonetic_token(token: str) -> str:
    """Normalizes one Vietnamese name token for common speech-recognition confusions."""
    normalized = str(token or "").strip()
    if not normalized:
        return ""

    if normalized.startswith("ngh"):
        normalized = "ng" + normalized[3:]
    elif normalized.startswith("gh"):
        normalized = "g" + normalized[2:]
    elif normalized.startswith("gi"):
        normalized = "d" + normalized[2:]
    elif normalized.startswith(("d", "r")):
        normalized = "d" + normalized[1:]
    elif normalized.startswith(("tr", "ch")):
        normalized = "ch" + normalized[2:]
    elif normalized.startswith(("s", "x")):
        normalized = "x" + normalized[1:]
    elif normalized.startswith(("l", "n")):
        normalized = "n" + normalized[1:]
    # KHMER/SOUTHERN-VN: cụm "qu + y/i/e" giọng Nam Bộ thường bị STT viết
    # thành "v + i/e" (Quyên ↔ Viên, Quy ↔ Vi). Áp TRƯỚC rule c/k/q chung
    # để gom 2 hướng confusion vào cùng phonetic key.
    elif normalized.startswith("quye"):
        normalized = "vie" + normalized[4:]
    elif normalized.startswith(("quy", "qui")):
        normalized = "vi" + normalized[3:]
    elif normalized.startswith("que"):
        normalized = "ve" + normalized[3:]
    elif normalized.startswith(("c", "k", "q")):
        normalized = "k" + normalized[1:]

    if normalized.endswith(("nh", "ng")):
        normalized = normalized[:-2] + "n"
    elif normalized.endswith(("c", "t")):
        normalized = normalized[:-1] + "t"
    return normalized


# ---------------------------------------------------------------------------
# Khmer-aware phonetic helpers (KHMER #A)
# ---------------------------------------------------------------------------
# Tên Khmer phổ biến trong cộng đồng Khmer Nam Bộ thường có:
#   - Họ: Thạch, Lâm, Sơn, Danh, Kim, Châu, Sô (Sô Thi/Sô Phia), Tăng, Trà.
#   - Tên: phụ âm cuối hiếm trong tiếng Việt (l, m chính danh, p), nguyên âm
#     không có hậu tố tiếng Việt chuẩn → Google STT thường thay bằng âm Việt
#     gần nhất ("phel" → "phen", "phi nết" → "phi nét/niết").
# Strict normalizer này CHỈ dùng làm fallback tier 3 trong _match_student,
# KHÔNG đổi `_voice_phonetic_token` hiện hữu để tránh false positive cho
# tên Việt thuần.

KHMER_FAMILY_NAMES: frozenset[str] = frozenset(
    {
        "thach", "lam", "son", "danh", "kim", "chau",
        "so", "tang", "tra", "neang", "ut", "khen",
        "kha", "kien", "kieu",
        # Bổ sung sau feedback từ user (lớp Khmer Nam Bộ phổ biến):
        "lieu",  # Liêu (Liêu Trinh, Liêu Hoàng) — họ Hoa-Khmer
    }
)


# Cặp âm cuối Khmer thường bị STT viết khác đi.
# Format: (suffix gốc, suffix sau khi normalize strict).
# Áp DỤNG SAU khi đã pass `_voice_phonetic_token` thường.
_KHMER_FINAL_REWRITES: tuple[tuple[str, str], ...] = (
    # Khmer "-l" cuối (Phel, Sol) → Việt thường nghe ra "-n".
    ("l", "n"),
    # Khmer "-m" → đôi khi STT giữ, đôi khi rút thành "-n".
    ("m", "n"),
    # Khmer "-p" → STT thay bằng "-t" hoặc giữ nguyên.
    ("p", "t"),
    # Khmer "-k" / "-ch" → đã được _voice_phonetic_token gom về "-t",
    # ở đây giữ làm an toàn.
    ("k", "t"),
)


# Vần Khmer thường bị STT đoán gần đúng — collapse về dạng tổi thiểu.
# Áp dụng trên token đã normalize không dấu.
_KHMER_VOWEL_COLLAPSE: tuple[tuple[str, str], ...] = (
    ("ie", "i"),    # niết / nít / nết → cùng nít/nit
    ("ue", "u"),
    ("uo", "u"),
    ("oa", "a"),
    ("oi", "i"),
    ("ai", "a"),
    ("ay", "a"),
    ("au", "a"),
    ("oo", "o"),
    ("ee", "e"),
)


def _voice_phonetic_token_strict(token: str) -> str:
    """Aggressive phonetic normalizer for Khmer name tokens.

    Stricter than `_voice_phonetic_token`: collapses Khmer-specific final
    consonants and vowel clusters that STT thường viết sai. Dùng làm tier 3
    fallback trong `_match_student`. KHÔNG dùng làm primary key vì sẽ tạo
    false positive cho tên Việt (vd: "trâm" và "trầm" sẽ collapse về cùng key).
    """
    normalized = _voice_phonetic_token(token)
    if not normalized:
        return ""
    # Collapse vần Khmer trước khi xử lý hậu tố.
    for source, target in _KHMER_VOWEL_COLLAPSE:
        normalized = normalized.replace(source, target)
    # Hậu tố Khmer phổ biến.
    for suffix, replacement in _KHMER_FINAL_REWRITES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)] + replacement
            break
    # Khmer monosyllable cuối: collapse `e ↔ i` để Nết / Nít / Niết cùng key.
    # Chỉ áp khi token ngắn (≤4 chars) và kết thúc bằng phụ âm Khmer chuẩn (n/t/p).
    if len(normalized) <= 4 and normalized.endswith(("n", "t", "p", "m")):
        normalized = normalized.replace("e", "i")
        # Cũng collapse `o ↔ u` cho cùng pattern (Phen / Phin / Phun).
        normalized = normalized.replace("o", "u")
    return normalized


def _voice_phonetic_text_strict(value: str) -> str:
    """Builds a strict phonetic key for Khmer-style names.

    Đối với tên có ít nhất 1 họ Khmer trong whitelist, áp thêm 1 lượt collapse
    cho âm cuối hiếm gặp trong tiếng Việt mà STT thường viết khác (vd: "Sà Ry"
    với "y" cuối, sẽ thành "Sà Ri" / "Sà Re"). Tên Việt thuần KHÔNG bị áp để
    tránh false positive cho Mỹ/Lý/Quý.
    """
    normalized_text = _normalize_diacritic_text(value)
    tokens = [_voice_phonetic_token_strict(token) for token in normalized_text.split()]
    if not tokens:
        return ""
    # Khmer-aware: nếu tên có ít nhất 1 token thuộc whitelist, apply rule mạnh
    # cho các token còn lại.
    raw_tokens = [t for t in normalized_text.split() if t]
    has_khmer_family = any(token in KHMER_FAMILY_NAMES for token in raw_tokens)
    if has_khmer_family:
        adjusted: list[str] = []
        for token in tokens:
            if not token:
                continue
            # `y` cuối → `i` (Sà Ry → Sà Ri)
            if len(token) <= 4 and token.endswith("y"):
                token = token[:-1] + "i"
            adjusted.append(token)
        return " ".join(adjusted)
    return " ".join(token for token in tokens if token)


def _looks_like_khmer_name(student_name: str) -> bool:
    """Heuristic: tên có ít nhất một họ thuộc whitelist Khmer."""
    normalized = _normalize_diacritic_text(student_name)
    if not normalized:
        return False
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return False
    return tokens[0] in KHMER_FAMILY_NAMES


# ---------------------------------------------------------------------------
# Vietnamese phonetic confusion pairs (giọng Nam Bộ / vần hiếm)
# ---------------------------------------------------------------------------
# Bảng cặp âm/từ Việt thường lẫn nhau khi STT — dùng để sinh alias tự động cho
# mọi HS có chứa các âm này. KHÁC với Khmer aliases ở chỗ áp cho mọi tên Việt
# (không cần whitelist họ). Mỗi tuple là `(token_a, token_b)` 2 chiều — alias
# sẽ được sinh cho cả 2 hướng.
_VIETNAMESE_NAME_TOKEN_PAIRS: tuple[tuple[str, str], ...] = (
    # Vần ư/â trong từ kết thúc -t (Nhựt ↔ Nhật, Nhứt ↔ Nhất, Hựt ↔ Hật)
    ("nhựt", "nhật"),
    ("nhứt", "nhất"),
    ("nhựn", "nhận"),  # ít gặp, an toàn
    # Vần â/ă (Trâm/Trăm xử lý qua tied; ở đây chỉ thêm cặp hay STT-confuse khác)
    ("dực", "dực"),    # placeholder để dễ thêm cặp khác
    # Cặp giọng Bắc / Nam y/i — chỉ áp cho những từ phổ biến KHÔNG phải họ
    # (tránh phá Lý/Mỹ). Ví dụ: "Quy" cuối tên (Bích Quy) ≈ "Quỳ" / "Kỳ".
    ("kỳ", "kì"),
    ("quý", "quí"),
    # Vần iên/yên (Liên ↔ Liêng, Tiên ↔ Tiêng)
    ("tiên", "tiêng"),
)


# Loại bỏ placeholder và normalize: bộ pairs hoạt động theo dạng key không dấu.
_VIETNAMESE_NAME_TOKEN_PAIRS = tuple(
    (a, b) for a, b in _VIETNAMESE_NAME_TOKEN_PAIRS if a != b
)


def _vietnamese_likely_aliases(student_name: str) -> list[str]:
    """Returns auto-suggested aliases for Vietnamese name confusion pairs.

    Rà mỗi token trong tên; nếu nó (hoặc bản strip dấu) khớp một trong các
    cặp `_VIETNAMESE_NAME_TOKEN_PAIRS`, sinh ra phiên bản thay thế. Trả tối đa
    8 alias duy nhất (case-insensitive theo strip-dấu key).
    """
    raw_name = str(student_name or "").strip()
    if not raw_name:
        return []
    parts = [part for part in raw_name.split() if part]
    if not parts:
        return []
    # Bảng tra 2 chiều: token (lowercase, có dấu) → list[other token]
    swap_map: dict[str, list[str]] = {}
    for left, right in _VIETNAMESE_NAME_TOKEN_PAIRS:
        swap_map.setdefault(left.lower(), []).append(right)
        swap_map.setdefault(right.lower(), []).append(left)
    aliases: list[str] = []
    seen_keys: set[str] = set()

    def _add(candidate: str) -> None:
        cleaned = " ".join(str(candidate or "").split())
        if not cleaned or cleaned.lower() == raw_name.lower():
            return
        key = _normalize_diacritic_text(cleaned)
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        aliases.append(cleaned)

    for index, token in enumerate(parts):
        token_lower = token.lower()
        replacements = swap_map.get(token_lower)
        if not replacements:
            # Thử strip dấu để bắt cả "Nhựt" / "Nhứt" qua key có dấu chuẩn.
            stripped_key = _normalize_diacritic_text(token)
            for left, right in _VIETNAMESE_NAME_TOKEN_PAIRS:
                if _normalize_diacritic_text(left) == stripped_key:
                    replacements = (replacements or []) + [right]
                if _normalize_diacritic_text(right) == stripped_key:
                    replacements = (replacements or []) + [left]
            if not replacements:
                continue
        for replacement in replacements:
            new_parts = list(parts)
            # Giữ nguyên capitalization gốc của token (Title case).
            new_parts[index] = replacement.capitalize() if token[:1].isupper() else replacement.lower()
            _add(" ".join(new_parts))

    return aliases[:8]


def _khmer_likely_aliases(student_name: str) -> list[str]:
    """Returns auto-suggested aliases derived from a Khmer-style student name.

    Sinh ra các biến thể STT tiếng Việt hay nhận sai cho tên Khmer:
        Thạch Phel  → ['Thạch Phen', 'Phen', 'Thạch Phel']
        Tăng Phi Nết → ['Tăng Phi Nít', 'Tăng Phi Niết', 'Phi Nít', 'Phi Niết']
    Chỉ chạy khi `_looks_like_khmer_name` trả True.
    """
    raw_name = str(student_name or "").strip()
    if not raw_name or not _looks_like_khmer_name(raw_name):
        return []
    parts = [part for part in raw_name.split() if part]
    if not parts:
        return []
    aliases: list[str] = []
    seen_keys: set[str] = set()

    def _add(candidate: str) -> None:
        clean_candidate = " ".join(str(candidate or "").split())
        if not clean_candidate or clean_candidate.lower() == raw_name.lower():
            return
        key = _normalize_diacritic_text(clean_candidate)
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        aliases.append(clean_candidate)

    # Suggested replacements cho âm cuối Khmer hay bị STT thay.
    final_letter_swaps: tuple[tuple[str, str], ...] = (
        # Pattern: (regex tail, replacement tail). Áp trên token (không dấu).
        ("el", "en"),
        ("ol", "on"),
        ("al", "an"),
        ("ul", "un"),
        ("om", "on"),
        ("um", "un"),
        ("op", "ot"),
        ("ip", "it"),
        ("up", "ut"),
    )
    # Vowel cluster swaps cho âm giữa Khmer.
    middle_vowel_swaps: tuple[tuple[str, str], ...] = (
        ("et", "it"),     # nết → nít
        ("et", "iet"),    # nết → niết
        ("e", "i"),       # phel → phil (rồi xuống en/in)
        ("oa", "ua"),
        ("ay", "ai"),
    )

    last_idx = len(parts) - 1

    def _swap_part(part: str) -> list[str]:
        normalized = _normalize_diacritic_text(part)
        if not normalized:
            return []
        candidates: set[str] = set()
        for tail, replacement in final_letter_swaps:
            if normalized.endswith(tail):
                candidates.add(normalized[: -len(tail)] + replacement)
        for source, replacement in middle_vowel_swaps:
            if source in normalized:
                candidates.add(normalized.replace(source, replacement, 1))
        # Collapse Khmer "ng" → "n" và ngược lại để bắt cả 2 hướng STT.
        if normalized.endswith("ng"):
            candidates.add(normalized[:-2] + "n")
        elif normalized.endswith("n"):
            candidates.add(normalized[:-1] + "ng")
        return [c for c in candidates if c and c != normalized]

    # Sinh các biến thể chỉ thay tên cuối (most likely STT confusion).
    swap_candidates_last = _swap_part(parts[last_idx])
    for swap in swap_candidates_last:
        rebuilt = " ".join([*parts[:last_idx], swap.capitalize()])
        _add(rebuilt)
    # Sinh biến thể chỉ tên (bỏ họ) — cho phép user nói tên ngắn.
    if last_idx >= 2:
        # 2 token cuối = tên đệm + tên (hoặc 2 tên nếu Khmer)
        short = " ".join(parts[-2:])
        _add(short)
        for swap in swap_candidates_last:
            _add(" ".join([parts[-2], swap.capitalize()]))
    elif last_idx == 1:
        _add(parts[1])
        for swap in swap_candidates_last:
            _add(swap.capitalize())

    # Cũng swap token áp cuối nếu có (Phi Nết → Phi Nít)
    if last_idx >= 1:
        swap_prev = _swap_part(parts[last_idx - 1])
        for swap in swap_prev:
            rebuilt = " ".join([*parts[: last_idx - 1], swap.capitalize(), parts[last_idx]])
            _add(rebuilt)

    return aliases[:8]  # giới hạn để alias index không phình to


def _voice_phonetic_text(value: str) -> str:
    """Builds a speech-oriented comparable key for Vietnamese student names."""
    normalized_text = _normalize_diacritic_text(value)
    tokens = [_voice_phonetic_token(token) for token in normalized_text.split()]
    return " ".join(token for token in tokens if token)

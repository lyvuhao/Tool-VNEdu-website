from __future__ import annotations

import sys
import types

_NHANXET_PRO_SOURCE = '"""VNEDU Auto Nhận Xét V2.\n\nSkeleton mới cho tool nhận xét theo hướng:\n- Attach live Chrome qua Chrome DevTools Protocol.\n- Tự mở VNEDU và đăng nhập nếu cần.\n- Đọc trực tiếp combobox `Khối/Lớp/Môn/Học kỳ` từ cửa sổ `Sổ điểm`.\n- Chuẩn bị kiến trúc cho bước quét schema cột và ghi nhận xét chính xác hơn.\n"""\n\nfrom __future__ import annotations\n\nfrom contextlib import contextmanager\nfrom dataclasses import dataclass, field\nfrom datetime import datetime\nimport json\nimport os\nfrom pathlib import Path\nfrom queue import Empty, Queue\nimport re\nimport shutil\nimport subprocess\nimport threading\nimport time\nimport tkinter as tk\nfrom tkinter import messagebox, ttk\nfrom typing import Callable, Dict, Iterator, List, Tuple\nfrom urllib.parse import quote_plus, urlparse\nfrom urllib.request import urlopen\n\nfrom playwright.sync_api import Error as PlaywrightError, Page, sync_playwright\n\n\n_SCRIPT_DIR = Path(__file__).resolve().parent\nCONFIG_FILE = _SCRIPT_DIR / "nhanxet_v2_config.json"\nDEFAULT_RULE_EXPORT_DIR = _SCRIPT_DIR / "nhanxet_mac_dinh"\nAPP_TITLE = "AUTO Ghi Nhận Xét Học Sinh - Developed by Vu Hao"\nWINDOW_SIZE = "1060x760"\nNUMERIC_COMMENT_SCORE_RE = re.compile(r"^\\s*(?:10(?:[.,]0+)?|[0-9](?:[.,]\\d+)?)\\s*$")\nPASSIVE_SCAN_SKIPPED = object()\nProgressCallback = Callable[[float, str], None]\n\n\n@dataclass\nclass ScoreOption:\n    """One ExtJS combobox option in the VNEDU scorebook screen."""\n\n    option_id: str\n    option_text: str\n\n\n@dataclass\nclass ScoreColumnSchema:\n    """Placeholder for a parsed scorebook leaf column."""\n\n    column_key: str\n    header_path: Tuple[str, ...]\n    leaf_index: int\n    display_name: str = ""\n    editable: bool = False\n    role_hint: str = ""\n    input_kind: str = ""\n    sample_value: str = ""\n    block_index: str = ""\n    child_index: str = ""\n    data_column: str = ""\n    input_name: str = ""\n\n\n@dataclass\nclass CommentRule:\n    """One rule mapping score/value conditions to a comment template."""\n\n    condition: str\n    template: str\n\n\n@dataclass\nclass CommentWriteRow:\n    """One student row prepared for the internal write queue."""\n\n    row_index: int\n    student_code: str\n    student_name: str\n    source_column_key: str\n    source_column_name: str\n    source_value: str\n    comment_input_name: str\n    current_comment: str\n    proposed_comment: str\n    status: str\n    reason: str = ""\n\n\n@dataclass\nclass CommentWriteResult:\n    """Write-back result summary after applying the internal write queue."""\n\n    attempted: int\n    updated: int\n    verified: int\n    skipped: int\n    save_clicked: bool\n    save_verified: bool = False\n    save_verification_mode: str = ""\n    save_verification_detail: str = ""\n    verified_input_names: List[str] = field(default_factory=list)\n    failed_input_names: List[str] = field(default_factory=list)\n    failed_rows: List[str] = field(default_factory=list)\n\n\n@dataclass\nclass ScoreWriteEntry:\n    """One student row prepared for score preview/write-back."""\n\n    row_index: int\n    row_id: str\n    student_code: str\n    student_name: str\n    target_column_key: str\n    target_column_name: str\n    current_score: str\n    target_input_name: str\n    proposed_score: str = ""\n    status: str = "scanned"\n    reason: str = ""\n\n\n@dataclass\nclass ScoreWriteResult:\n    """Write-back result summary after applying one score payload."""\n\n    attempted: int\n    updated: int\n    verified: int\n    skipped: int\n    save_clicked: bool\n    save_verified: bool = False\n    save_verification_mode: str = ""\n    save_verification_detail: str = ""\n    verified_input_names: List[str] = field(default_factory=list)\n    failed_input_names: List[str] = field(default_factory=list)\n    failed_rows: List[str] = field(default_factory=list)\n\n\n@dataclass\nclass ScorebookAccessEntry:\n    """One class-subject permission entry discovered from the live scorebook screen."""\n\n    grade_id: str\n    grade_text: str\n    class_id: str\n    class_text: str\n    subject_id: str\n    subject_text: str\n    term_id: str\n    term_text: str\n    teacher_text: str = ""\n    permission_text: str = ""\n    comment_input_count: int = 0\n    enabled_comment_input_count: int = 0\n\n\n@dataclass\nclass ScorebookDetectedColumns:\n    """Auto-detected source/target columns derived from the parsed live schema."""\n\n    preferred_score_column_key: str = ""\n    preferred_score_reason: str = ""\n    preferred_comment_column_key: str = ""\n    score_candidate_keys: List[str] = field(default_factory=list)\n    average_candidate_keys: List[str] = field(default_factory=list)\n    comment_candidate_keys: List[str] = field(default_factory=list)\n\n\n@dataclass\nclass ScorebookContext:\n    """Current scorebook shell state read from the live browser."""\n\n    grade_options: List[ScoreOption]\n    selected_grade_id: str\n    class_options: List[ScoreOption]\n    selected_class_id: str\n    subject_options: List[ScoreOption]\n    selected_subject_id: str\n    term_options: List[ScoreOption]\n    selected_term_id: str\n    window_id: str = ""\n    window_title: str = ""\n    teacher_text: str = ""\n    permission_text: str = ""\n    comment_input_count: int = 0\n    enabled_comment_input_count: int = 0\n    column_schemas: List[ScoreColumnSchema] = field(default_factory=list)\n    detected_columns: ScorebookDetectedColumns = field(default_factory=ScorebookDetectedColumns)\n    accessible_entries: List[ScorebookAccessEntry] = field(default_factory=list)\n    accessible_grade_id: str = ""\n    accessible_term_id: str = ""\n\n\ndef clamp_progress_value(value: float) -> float:\n    """Clamps one progress value into the GUI-safe 0..100 range."""\n    try:\n        numeric_value = float(value)\n    except (TypeError, ValueError):\n        return 0.0\n    return max(0.0, min(100.0, numeric_value))\n\n\ndef emit_progress(\n    progress_callback: ProgressCallback | None,\n    value: float,\n    message: str = "",\n) -> None:\n    """Sends one progress update when a reporter is available."""\n    if progress_callback is None:\n        return\n    progress_callback(clamp_progress_value(value), str(message or "").strip())\n\n\ndef create_subprogress_reporter(\n    progress_callback: ProgressCallback | None,\n    start_value: float,\n    end_value: float,\n) -> ProgressCallback | None:\n    """Maps one nested 0..100 reporter into a parent progress span."""\n    if progress_callback is None:\n        return None\n\n    clamped_start = clamp_progress_value(start_value)\n    clamped_end = clamp_progress_value(end_value)\n    span = clamped_end - clamped_start\n\n    def report(value: float, message: str = "") -> None:\n        nested_value = clamp_progress_value(value)\n        progress_callback(clamped_start + (span * (nested_value / 100.0)), message)\n\n    return report\n\n\ndef build_progress_caption(progress_value: float, message: str, max_message_length: int = 48) -> str:\n    """Builds a compact progress caption suitable for the canvas-based progress bar."""\n    normalized_value = int(round(clamp_progress_value(progress_value)))\n    normalized_message = str(message or "").strip() or "Sẵn sàng"\n    if len(normalized_message) > max_message_length:\n        normalized_message = normalized_message[: max_message_length - 3].rstrip() + "..."\n    return f"{normalized_value:>3}% | {normalized_message}"\n\n\ndef password_entry_show_value(show_password: bool) -> str:\n    """Returns the Tk `show` value for the password entry."""\n    return "" if show_password else "*"\n\n\ndef merge_score_options(*option_groups: List[ScoreOption]) -> List[ScoreOption]:\n    """Merges option groups by id while preserving first-seen order."""\n    merged: List[ScoreOption] = []\n    seen_ids = set()\n    for group in option_groups:\n        for option in group:\n            option_id = str(option.option_id).strip()\n            option_text = str(option.option_text).strip() or option_id\n            if not option_id or option_id in seen_ids:\n                continue\n            seen_ids.add(option_id)\n            merged.append(ScoreOption(option_id=option_id, option_text=option_text))\n    return merged\n\n\ndef ensure_selected_score_option(\n    options: List[ScoreOption],\n    selected_id: str,\n    selected_text: str,\n) -> List[ScoreOption]:\n    """Keeps the selected option visible even when the live ExtJS store is incomplete."""\n    normalized_id = selected_id.strip()\n    normalized_text = selected_text.strip() or normalized_id\n    if not normalized_id:\n        return merge_score_options(options)\n    return merge_score_options(\n        options,\n        [ScoreOption(option_id=normalized_id, option_text=normalized_text)],\n    )\n\n\ndef resolve_hydrated_score_options(\n    existing_options: List[ScoreOption],\n    live_options: List[ScoreOption],\n    *,\n    selected_id: str,\n    selected_text: str,\n    merge_existing: bool = True,\n    preserve_selected_if_missing: bool = True,\n) -> List[ScoreOption]:\n    """Builds one refreshed option list while controlling whether stale pre-refresh items may survive."""\n    resolved_options = (\n        merge_score_options(existing_options, live_options)\n        if merge_existing\n        else merge_score_options(live_options)\n    )\n    if preserve_selected_if_missing:\n        return ensure_selected_score_option(resolved_options, selected_id, selected_text)\n    return resolved_options\n\n\ndef class_options_from_access_entries(entries: List[ScorebookAccessEntry]) -> List[ScoreOption]:\n    """Builds class options from discovered access entries."""\n    return merge_score_options(\n        [\n            ScoreOption(\n                option_id=entry.class_id.strip(),\n                option_text=entry.class_text.strip() or entry.class_id.strip(),\n            )\n            for entry in entries\n            if entry.class_id.strip()\n        ]\n    )\n\n\ndef subject_options_from_access_entries(\n    entries: List[ScorebookAccessEntry],\n    class_id: str = "",\n) -> List[ScoreOption]:\n    """Builds subject options from discovered access entries."""\n    normalized_class_id = class_id.strip()\n    matching_entries = [\n        entry for entry in entries if not normalized_class_id or entry.class_id.strip() == normalized_class_id\n    ]\n    if not matching_entries and normalized_class_id:\n        matching_entries = list(entries)\n    return merge_score_options(\n        [\n            ScoreOption(\n                option_id=entry.subject_id.strip(),\n                option_text=entry.subject_text.strip() or entry.subject_id.strip(),\n            )\n            for entry in matching_entries\n            if entry.subject_id.strip()\n        ]\n    )\n\n\ndef resolve_accessible_selection(\n    entries: List[ScorebookAccessEntry],\n    preferred_class_id: str = "",\n    preferred_subject_id: str = "",\n) -> Tuple[str, str, List[str]]:\n    """Chooses a valid class-subject pair from the discovered permission matrix."""\n    notes: List[str] = []\n    if not entries:\n        return "", "", notes\n\n    preferred_class_id = preferred_class_id.strip()\n    preferred_subject_id = preferred_subject_id.strip()\n\n    exact_entry = next(\n        (\n            entry\n            for entry in entries\n            if entry.class_id == preferred_class_id and entry.subject_id == preferred_subject_id\n        ),\n        None,\n    )\n    if exact_entry is not None:\n        return exact_entry.class_id, exact_entry.subject_id, notes\n\n    if preferred_class_id:\n        class_entry = next((entry for entry in entries if entry.class_id == preferred_class_id), None)\n        if class_entry is not None:\n            if preferred_subject_id:\n                notes.append("Môn đã chọn không có quyền ở lớp này, app chuyển sang môn hợp lệ đầu tiên.")\n            return class_entry.class_id, class_entry.subject_id, notes\n\n    if preferred_subject_id:\n        subject_entry = next((entry for entry in entries if entry.subject_id == preferred_subject_id), None)\n        if subject_entry is not None:\n            if preferred_class_id:\n                notes.append("Lớp đã chọn không có quyền với môn này, app chuyển sang lớp hợp lệ đầu tiên.")\n            return subject_entry.class_id, subject_entry.subject_id, notes\n\n    fallback_entry = entries[0]\n    if preferred_class_id or preferred_subject_id:\n        notes.append("Tổ hợp lớp/môn đã chọn không có quyền, app chuyển sang tổ hợp hợp lệ đầu tiên.")\n    return fallback_entry.class_id, fallback_entry.subject_id, notes\n\n\ndef apply_access_entries_to_context(\n    context: ScorebookContext,\n    entries: List[ScorebookAccessEntry],\n    grade_id: str,\n    term_id: str,\n) -> ScorebookContext:\n    """Attaches discovered permission entries to the returned scorebook context."""\n    if not entries:\n        context.accessible_entries = []\n        context.accessible_grade_id = ""\n        context.accessible_term_id = ""\n        return context\n    context.class_options = merge_score_options(\n        context.class_options,\n        class_options_from_access_entries(entries),\n    )\n    context.subject_options = merge_score_options(\n        context.subject_options,\n        subject_options_from_access_entries(entries),\n    )\n    context.accessible_entries = list(entries)\n    context.accessible_grade_id = grade_id.strip()\n    context.accessible_term_id = term_id.strip()\n    return context\n\n\ndef parse_condition(condition_str: str):\n    """Parses a score condition into a checker callable."""\n    s = condition_str.strip()\n    if not s:\n        return None\n\n    matched = re.match(r"^<=(\\d+\\.?\\d*)$", s)\n    if matched:\n        value = float(matched.group(1))\n        return lambda score, v=value: score <= v\n\n    matched = re.match(r"^<(\\d+\\.?\\d*)$", s)\n    if matched:\n        value = float(matched.group(1))\n        return lambda score, v=value: score < v\n\n    matched = re.match(r"^>=(\\d+\\.?\\d*)$", s)\n    if matched:\n        value = float(matched.group(1))\n        return lambda score, v=value: score >= v\n\n    matched = re.match(r"^>(\\d+\\.?\\d*)$", s)\n    if matched:\n        value = float(matched.group(1))\n        return lambda score, v=value: score > v\n\n    matched = re.match(r"^=(\\d+\\.?\\d*)$", s)\n    if matched:\n        value = float(matched.group(1))\n        return lambda score, v=value: score == v\n\n    matched = re.match(r"^(\\d+\\.?\\d*)\\s*-\\s*(\\d+\\.?\\d*)$", s)\n    if matched:\n        low = float(matched.group(1))\n        high = float(matched.group(2))\n        return lambda score, lo=low, hi=high: lo <= score <= hi\n\n    matched = re.match(r"^(\\d+\\.?\\d*)$", s)\n    if matched:\n        value = float(matched.group(1))\n        return lambda score, v=value: score == v\n\n    normalized = s.upper()\n    if normalized in ("Đ", "ĐẠT", "DAT", "D"):\n        return lambda score: isinstance(score, str) and score.strip().upper() in (\n            "Đ",\n            "ĐẠT",\n            "DAT",\n            "D",\n        )\n\n    if normalized in ("CĐ", "CD", "CHƯA ĐẠT", "CHUA DAT"):\n        return lambda score: isinstance(score, str) and score.strip().upper() in (\n            "CĐ",\n            "CD",\n            "CHƯA ĐẠT",\n            "CHUA DAT",\n        )\n\n    return None\n\n\ndef looks_like_numeric_comment_score(value: str) -> bool:\n    """Returns whether an existing comment cell actually contains a numeric score-like value."""\n    normalized = str(value or "").strip()\n    if not normalized:\n        return False\n    return bool(NUMERIC_COMMENT_SCORE_RE.fullmatch(normalized))\n\n\ndef describe_condition(condition_str: str) -> str:\n    """Returns a short Vietnamese description for one rule condition."""\n    condition = condition_str.strip()\n    if re.match(r"^<=", condition):\n        return f"Điểm ≤ {condition[2:]}"\n    if re.match(r"^<", condition):\n        return f"Điểm < {condition[1:]}"\n    if re.match(r"^>=", condition):\n        return f"Điểm ≥ {condition[2:]}"\n    if re.match(r"^>", condition):\n        return f"Điểm > {condition[1:]}"\n    if re.match(r"^=", condition):\n        return f"Điểm = {condition[1:]}"\n    if "-" in condition and not condition.startswith("-"):\n        parts = condition.split("-")\n        if len(parts) == 2:\n            return f"Điểm từ {parts[0].strip()} đến {parts[1].strip()}"\n    if re.match(r"^\\d+\\.?\\d*$", condition):\n        return f"Điểm = {condition}"\n    normalized = condition.upper()\n    if normalized in ("Đ", "ĐẠT", "DAT", "D"):\n        return "Xếp loại Đạt"\n    if normalized in ("CĐ", "CD", "CHƯA ĐẠT", "CHUA DAT"):\n        return "Xếp loại Chưa đạt"\n    return "Không hợp lệ"\n\n\ndef compile_comment_rules(rules: List[CommentRule]) -> List[Tuple[Callable[[object], bool], str, str]]:\n    """Compiles UI rules into callable matchers once per analyze/apply run."""\n    compiled: List[Tuple[Callable[[object], bool], str, str]] = []\n    for rule in rules:\n        condition = rule.condition.strip()\n        template = rule.template.strip()\n        if not condition or not template:\n            continue\n        checker = parse_condition(condition)\n        if checker is None:\n            continue\n        compiled.append((checker, template, condition))\n    return compiled\n\n\ndef match_comment_for_value(\n    raw_value: str,\n    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],\n) -> Tuple[str | None, str]:\n    """Returns the first matching comment template and the matched condition."""\n    value = raw_value.strip()\n    if not value:\n        return None, ""\n\n    for checker, template, condition in compiled_rules:\n        try:\n            if checker(value):\n                return template, condition\n        except TypeError:\n            continue\n\n    try:\n        numeric_value = float(value.replace(",", "."))\n    except ValueError:\n        return None, ""\n\n    for checker, template, condition in compiled_rules:\n        try:\n            if checker(numeric_value):\n                return template, condition\n        except TypeError:\n            continue\n\n    return None, ""\n\n\ndef plan_comment_row_write(\n    source_value: str,\n    current_comment: str,\n    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],\n    allow_overwrite_existing_comment: bool = False,\n) -> Tuple[str, str, str]:\n    """Returns the proposed comment, queue status, and explanation for one live score row."""\n    normalized_source_value = str(source_value).strip()\n    normalized_current_comment = str(current_comment).strip()\n    current_comment_is_numeric_score = looks_like_numeric_comment_score(normalized_current_comment)\n    proposed_comment = ""\n\n    if not normalized_source_value:\n        return proposed_comment, "skip_no_score", "Chưa có điểm nguồn."\n\n    if (\n        normalized_current_comment\n        and not current_comment_is_numeric_score\n        and not allow_overwrite_existing_comment\n    ):\n        return (\n            proposed_comment,\n            "skip_existing_comment",\n            "Ô nhận xét hiện đã có dữ liệu, app giữ nguyên để tránh ghi đè.",\n        )\n\n    matched_comment, matched_condition = match_comment_for_value(normalized_source_value, compiled_rules)\n    if matched_comment is None:\n        return (\n            proposed_comment,\n            "skip_unmatched",\n            f"Không khớp rule nào cho giá trị `{normalized_source_value}`.",\n        )\n\n    proposed_comment = matched_comment\n    if proposed_comment.strip() == normalized_current_comment:\n        return proposed_comment, "skip_same", "Nhận xét hiện tại đã giống kết quả dự kiến."\n    return proposed_comment, "ready", f"Khớp rule `{matched_condition}`."\n\n\ndef build_comment_write_row_from_live_data(\n    live_row: Dict[str, object],\n    source_column_key: str,\n    source_column_name: str,\n    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],\n    allow_overwrite_existing_comment: bool = False,\n) -> CommentWriteRow:\n    """Builds one queue row from one live DOM row snapshot and the compiled rule set."""\n    source_value = str(live_row.get("sourceValue", "")).strip()\n    current_comment = str(live_row.get("currentComment", "")).strip()\n    proposed_comment, status, reason = plan_comment_row_write(\n        source_value,\n        current_comment,\n        compiled_rules,\n        allow_overwrite_existing_comment=allow_overwrite_existing_comment,\n    )\n    return CommentWriteRow(\n        row_index=int(live_row.get("rowIndex", 0) or 0),\n        student_code=str(live_row.get("studentCode", "")).strip(),\n        student_name=str(live_row.get("studentName", "")).strip(),\n        source_column_key=source_column_key,\n        source_column_name=source_column_name,\n        source_value=source_value,\n        comment_input_name=str(live_row.get("commentInputName", "")).strip(),\n        current_comment=current_comment,\n        proposed_comment=proposed_comment,\n        status=status,\n        reason=reason,\n    )\n\n\ndef build_comment_write_rows_from_live_data(\n    live_rows: List[Dict[str, object]],\n    source_column_key: str,\n    source_column_name: str,\n    compiled_rules: List[Tuple[Callable[[object], bool], str, str]],\n    allow_overwrite_existing_comment: bool = False,\n) -> List[CommentWriteRow]:\n    """Builds the internal comment queue rows from live DOM rows."""\n    return [\n        build_comment_write_row_from_live_data(\n            live_row,\n            source_column_key=source_column_key,\n            source_column_name=source_column_name,\n            compiled_rules=compiled_rules,\n            allow_overwrite_existing_comment=allow_overwrite_existing_comment,\n        )\n        for live_row in live_rows\n    ]\n\n\ndef ready_comment_write_rows(write_rows: List[CommentWriteRow]) -> List[CommentWriteRow]:\n    """Returns only rows that are ready for DOM write-back."""\n    return [\n        row\n        for row in write_rows\n        if row.status == "ready" and row.comment_input_name.strip() and row.proposed_comment.strip()\n    ]\n\n\ndef build_comment_write_payload(rows_to_apply: List[CommentWriteRow]) -> List[Dict[str, str]]:\n    """Builds the DOM payload used to write comments into live inputs."""\n    return [\n        {"inputName": row.comment_input_name, "text": row.proposed_comment}\n        for row in rows_to_apply\n    ]\n\n\ndef normalize_score_text(value: object) -> str:\n    """Normalizes one score-like value for DOM write/verification."""\n    normalized = str(value or "").strip().replace(\',\', \'.\')\n    if not normalized:\n        return ""\n    try:\n        numeric_value = float(normalized)\n    except (TypeError, ValueError):\n        return normalized\n    if numeric_value.is_integer():\n        return str(int(numeric_value))\n    return f"{numeric_value:.2f}".rstrip(\'0\').rstrip(\'.\')\n\n\ndef coerce_score_write_entries(entries: List[ScoreWriteEntry | Dict[str, object]]) -> List[ScoreWriteEntry]:\n    """Coerces raw payload rows into typed score write entries."""\n    coerced_entries: List[ScoreWriteEntry] = []\n    for item in list(entries or []):\n        if isinstance(item, ScoreWriteEntry):\n            coerced_entries.append(item)\n            continue\n        if not isinstance(item, dict):\n            raise TypeError(f"Score entry không hợp lệ: {type(item)!r}")\n        coerced_entries.append(\n            ScoreWriteEntry(\n                row_index=int(item.get(\'row_index\', item.get(\'rowIndex\', 0)) or 0),\n                row_id=str(item.get(\'row_id\', item.get(\'rowId\', \'\'))).strip(),\n                student_code=str(item.get(\'student_code\', item.get(\'studentCode\', \'\'))).strip(),\n                student_name=str(item.get(\'student_name\', item.get(\'studentName\', \'\'))).strip(),\n                target_column_key=str(item.get(\'target_column_key\', item.get(\'targetColumnKey\', \'\'))).strip(),\n                target_column_name=str(item.get(\'target_column_name\', item.get(\'targetColumnName\', \'\'))).strip(),\n                current_score=str(item.get(\'current_score\', item.get(\'currentScore\', \'\'))).strip(),\n                target_input_name=str(item.get(\'target_input_name\', item.get(\'targetInputName\', \'\'))).strip(),\n                proposed_score=normalize_score_text(item.get(\'proposed_score\', item.get(\'proposedScore\', \'\'))),\n                status=str(item.get(\'status\', \'ready\') or \'ready\').strip() or \'ready\',\n                reason=str(item.get(\'reason\', \'\')).strip(),\n            )\n        )\n    return coerced_entries\n\n\ndef ready_score_write_entries(entries: List[ScoreWriteEntry | Dict[str, object]]) -> List[ScoreWriteEntry]:\n    """Returns only score rows that are ready for DOM write-back."""\n    ready_entries: List[ScoreWriteEntry] = []\n    for entry in coerce_score_write_entries(entries):\n        proposed_score = normalize_score_text(entry.proposed_score)\n        if not entry.target_input_name.strip() or not proposed_score:\n            continue\n        entry.proposed_score = proposed_score\n        ready_entries.append(entry)\n    return ready_entries\n\n\ndef build_score_write_payload(entries: List[ScoreWriteEntry | Dict[str, object]]) -> List[Dict[str, str]]:\n    """Builds the DOM payload used to write scores into live inputs."""\n    return [\n        {"inputName": entry.target_input_name, "text": normalize_score_text(entry.proposed_score)}\n        for entry in ready_score_write_entries(entries)\n    ]\n\n\ndef build_score_write_request_pairs(payload: List[Dict[str, str]]) -> List[Tuple[str, str]]:\n    """Builds conservative `(inputName, value)` pairs expected to surface in one save payload."""\n    pairs: List[Tuple[str, str]] = []\n    seen_pairs = set()\n    for item in payload:\n        input_name = str(item.get("inputName", "")).strip()\n        value = normalize_score_text(str(item.get("text", "")).strip())\n        if not input_name or not value:\n            continue\n        key = (input_name, value)\n        if key in seen_pairs:\n            continue\n        seen_pairs.add(key)\n        pairs.append(key)\n    return pairs\n\n\ndef summarize_score_write_result(\n    entries: List[ScoreWriteEntry | Dict[str, object]],\n    rows_to_apply: List[ScoreWriteEntry],\n    result_payload: Dict[str, object],\n    verification_map: Dict[str, object],\n) -> ScoreWriteResult:\n    """Builds the final score write-back summary from raw DOM execution data."""\n    verified = 0\n    verified_input_names: List[str] = []\n    failed_input_names: List[str] = list(result_payload.get(\'failed\', []))\n    failed_rows: List[str] = list(failed_input_names)\n    for entry in rows_to_apply:\n        actual_value = normalize_score_text(verification_map.get(entry.target_input_name, \'\'))\n        expected_value = normalize_score_text(entry.proposed_score)\n        if actual_value == expected_value:\n            verified += 1\n            verified_input_names.append(entry.target_input_name)\n        elif entry.student_name:\n            failed_input_names.append(entry.target_input_name)\n            failed_rows.append(f"{entry.student_name} ({entry.student_code})")\n        else:\n            failed_input_names.append(entry.target_input_name)\n            failed_rows.append(entry.target_input_name)\n\n    save_clicked = bool(result_payload.get("saveClicked"))\n    save_verified = bool(result_payload.get("saveVerified"))\n    if save_clicked and not save_verified:\n        verified = 0\n        verified_input_names = []\n        for entry in rows_to_apply:\n            failed_input_names.append(entry.target_input_name)\n            if entry.student_name:\n                failed_rows.append(f"{entry.student_name} ({entry.student_code})")\n            else:\n                failed_rows.append(entry.target_input_name)\n\n    return ScoreWriteResult(\n        attempted=len(rows_to_apply),\n        updated=len(list(result_payload.get(\'updated\', []))),\n        verified=verified,\n        skipped=len(coerce_score_write_entries(entries)) - len(rows_to_apply),\n        save_clicked=save_clicked,\n        save_verified=save_verified,\n        save_verification_mode=str(result_payload.get(\'saveVerificationMode\', \'\')).strip(),\n        save_verification_detail=str(result_payload.get(\'saveVerificationDetail\', \'\')).strip(),\n        verified_input_names=list(dict.fromkeys(verified_input_names)),\n        failed_input_names=list(dict.fromkeys(failed_input_names)),\n        failed_rows=list(dict.fromkeys(failed_rows)),\n    )\n\n\ndef select_relevant_server_save_requests(\n    save_requests: List[Dict[str, object]],\n    request_markers: List[str] | None = None,\n) -> List[Dict[str, object]]:\n    """Keeps the most likely mutation requests triggered by one save action."""\n    normalized_markers = [marker.strip().lower() for marker in list(request_markers or []) if marker.strip()]\n    if normalized_markers:\n        marker_matched_requests = []\n        for request in save_requests:\n            request_blob = " ".join(\n                [\n                    str(request.get("url", "")).strip(),\n                    str(request.get("bodyPreview", "")).strip(),\n                    str(request.get("responsePreview", "")).strip(),\n                ]\n            ).lower()\n            if any(marker in request_blob for marker in normalized_markers):\n                marker_matched_requests.append(request)\n        if marker_matched_requests:\n            return marker_matched_requests\n\n    mutation_requests = [\n        request\n        for request in save_requests\n        if str(request.get("method", "")).strip().upper() not in {"", "GET", "HEAD", "OPTIONS"}\n        or bool(str(request.get("bodyPreview", "")).strip())\n    ]\n    return mutation_requests or list(save_requests)\n\n\ndef _json_save_response_state(response_preview: str) -> Tuple[bool | None, str]:\n    """Extracts a success/failure hint from one JSON-like save response preview."""\n    preview = response_preview.strip()\n    if not preview or preview[:1] not in "[{":\n        return None, ""\n    try:\n        payload = json.loads(preview)\n    except (TypeError, ValueError):\n        return None, ""\n\n    if isinstance(payload, dict):\n        if bool(payload.get("error")):\n            return False, str(payload.get("error"))\n        if "success" in payload:\n            return bool(payload.get("success")), str(payload.get("message", "")).strip()\n        if "status" in payload:\n            status_value = str(payload.get("status", "")).strip().lower()\n            if status_value in {"1", "true", "ok", "success"}:\n                return True, str(payload.get("message", "")).strip()\n            if status_value in {"0", "false", "error", "failed", "failure"}:\n                return False, str(payload.get("message", "")).strip()\n        if "message" in payload:\n            message = str(payload.get("message", "")).strip()\n            lowered_message = message.lower()\n            if any(token in lowered_message for token in ("thành công", "thanh cong", "success", "ok")):\n                return True, message\n            if any(token in lowered_message for token in ("thất bại", "that bai", "không thành công", "khong thanh cong", "lỗi", "loi", "error", "exception")):\n                return False, message\n    return None, ""\n\n\ndef _request_blob_contains_expected_score_pair(\n    request_blob: str,\n    input_name: str,\n    value: str,\n) -> bool:\n    """Returns whether one request blob contains strong evidence for a specific score write pair."""\n    normalized_blob = request_blob.strip().lower()\n    normalized_input = input_name.strip().lower()\n    normalized_value = normalize_score_text(value).lower()\n    if not normalized_blob or not normalized_input or not normalized_value:\n        return False\n\n    exact_markers = (\n        f"{normalized_input}={normalized_value}",\n        f"{quote_plus(normalized_input)}={quote_plus(normalized_value)}",\n        f\'"{normalized_input}":"{normalized_value}"\',\n        f"\'{normalized_input}\':\'{normalized_value}\'",\n    )\n    if any(marker in normalized_blob for marker in exact_markers):\n        return True\n\n    input_tokens = tuple(dict.fromkeys((normalized_input, quote_plus(normalized_input))))\n    value_tokens = tuple(dict.fromkeys((normalized_value, quote_plus(normalized_value))))\n    for input_token in input_tokens:\n        if not input_token:\n            continue\n        token_index = normalized_blob.find(input_token)\n        if token_index < 0:\n            continue\n        window_start = max(0, token_index - 48)\n        window_end = min(len(normalized_blob), token_index + len(input_token) + 128)\n        nearby_window = normalized_blob[window_start:window_end]\n        if any(value_token and value_token in nearby_window for value_token in value_tokens):\n            return True\n    return False\n\n\ndef evaluate_server_save_verification(\n    auto_save_requested: bool,\n    save_clicked: bool,\n    save_requests: List[Dict[str, object]],\n    request_markers: List[str] | None = None,\n    expected_score_pairs: List[Tuple[str, str]] | None = None,\n) -> Tuple[bool, str, str]:\n    """Evaluates whether one auto-save run was verified by server-side request/response signals."""\n    if not auto_save_requested:\n        return False, "not_requested", "Không bật tự bấm Lưu."\n    if not save_clicked:\n        return False, "button_missing", "Không tìm thấy nút Lưu để bấm tự động."\n\n    relevant_requests = select_relevant_server_save_requests(save_requests, request_markers=request_markers)\n    if not relevant_requests:\n        return False, "no_request", "Đã bấm Lưu nhưng không bắt được request lưu từ trình duyệt."\n\n    unfinished_requests = [request for request in relevant_requests if not bool(request.get("finished"))]\n    if unfinished_requests:\n        return False, "timeout", "Đã bấm Lưu nhưng request lưu chưa hoàn tất trong thời gian chờ."\n\n    failing_status_requests = []\n    for request in relevant_requests:\n        try:\n            status_code = int(request.get("status", 0) or 0)\n        except (TypeError, ValueError):\n            status_code = 0\n        if status_code not in range(200, 300) and status_code != 304:\n            failing_status_requests.append((status_code, str(request.get("url", "")).strip()))\n    if failing_status_requests:\n        first_status, first_url = failing_status_requests[0]\n        return False, "http_error", f"Request lưu trả về HTTP {first_status}: {first_url}"\n\n    for request in relevant_requests:\n        preview = str(request.get("responsePreview", "")).strip()\n        response_state, response_message = _json_save_response_state(preview)\n        if response_state is False:\n            return False, "server_rejected", response_message or "Server trả về phản hồi lỗi khi lưu."\n\n        lowered_preview = preview.lower()\n        if any(\n            token in lowered_preview\n            for token in (\n                "thất bại",\n                "that bai",\n                "không thành công",\n                "khong thanh cong",\n                "không có quyền",\n                "khong co quyen",\n                "permission denied",\n                "exception",\n            )\n        ):\n            return False, "server_rejected", preview[:180] or "Server trả về phản hồi lỗi khi lưu."\n\n    normalized_pairs = [\n        (input_name.strip(), normalize_score_text(value))\n        for input_name, value in list(expected_score_pairs or [])\n        if input_name.strip() and normalize_score_text(value)\n    ]\n    if normalized_pairs:\n        for request in relevant_requests:\n            request_blob = " ".join(\n                [\n                    str(request.get("url", "")).strip(),\n                    str(request.get("bodyPreview", "")).strip(),\n                    str(request.get("responsePreview", "")).strip(),\n                ]\n            )\n            if any(\n                _request_blob_contains_expected_score_pair(request_blob, input_name, value)\n                for input_name, value in normalized_pairs\n            ):\n                return True, "server_payload", f"Đã xác minh lưu server-side qua {len(relevant_requests)} request có payload điểm khớp."\n        return (\n            False,\n            "payload_not_observed",\n            "Đã bấm Lưu và request thành công nhưng chưa thấy cặp ô điểm/giá trị mong đợi trong payload gửi lên server.",\n        )\n\n    return True, "server_response", f"Đã xác minh lưu server-side qua {len(relevant_requests)} request."\n\n\ndef summarize_comment_write_result(\n    write_rows: List[CommentWriteRow],\n    rows_to_apply: List[CommentWriteRow],\n    result_payload: Dict[str, object],\n    verification_map: Dict[str, object],\n) -> CommentWriteResult:\n    """Builds the final write-back summary from raw DOM execution and verification data."""\n    verified = 0\n    verified_input_names: List[str] = []\n    failed_input_names: List[str] = list(result_payload.get("failed", []))\n    failed_rows: List[str] = list(failed_input_names)\n    for row in rows_to_apply:\n        actual_value = str(verification_map.get(row.comment_input_name, "")).strip()\n        if actual_value == row.proposed_comment.strip():\n            verified += 1\n            verified_input_names.append(row.comment_input_name)\n        elif row.student_name:\n            failed_input_names.append(row.comment_input_name)\n            failed_rows.append(f"{row.student_name} ({row.student_code})")\n        else:\n            failed_input_names.append(row.comment_input_name)\n            failed_rows.append(row.comment_input_name)\n\n    return CommentWriteResult(\n        attempted=len(rows_to_apply),\n        updated=len(list(result_payload.get("updated", []))),\n        verified=verified,\n        skipped=len(write_rows) - len(rows_to_apply),\n        save_clicked=bool(result_payload.get("saveClicked")),\n        save_verified=bool(result_payload.get("saveVerified")),\n        save_verification_mode=str(result_payload.get("saveVerificationMode", "")).strip(),\n        save_verification_detail=str(result_payload.get("saveVerificationDetail", "")).strip(),\n        verified_input_names=list(dict.fromkeys(verified_input_names)),\n        failed_input_names=list(dict.fromkeys(failed_input_names)),\n        failed_rows=list(dict.fromkeys(failed_rows)),\n    )\n\n\nclass VnEduScoreAutomation:\n    """Playwright CDP automation for the VNEDU scorebook screen."""\n\n    def __init__(self, debug_port: int, target_url: str) -> None:\n        self.debug_port = int(debug_port)\n        self.target_url = target_url.strip()\n        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))\n        self._cdp_profile_dir = Path(local_app_data) / "VNEDU-NHANXET-V2-CDP"\n\n    def _cdp_http_endpoint(self) -> str:\n        """Returns the local HTTP endpoint exposed by Chromium DevTools."""\n        return f"http://127.0.0.1:{self.debug_port}"\n\n    def _is_cdp_ready(self) -> bool:\n        """Checks whether a valid Chromium CDP endpoint is reachable."""\n        try:\n            with urlopen(f"{self._cdp_http_endpoint()}/json/version", timeout=2.0) as response:\n                payload = json.loads(response.read().decode("utf-8"))\n        except (OSError, ValueError):\n            return False\n\n        browser_name = str(payload.get("Browser", "")).lower()\n        return bool(payload.get("webSocketDebuggerUrl")) and (\n            "chrome" in browser_name or "chromium" in browser_name or "edg" in browser_name\n        )\n\n    def _find_chromium_executable(self) -> str:\n        """Tries common Windows locations for Chrome or Edge."""\n        candidates = [\n            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",\n            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",\n            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",\n            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",\n            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",\n            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",\n        ]\n\n        for command in ("chrome", "msedge", "chromium"):\n            located = shutil.which(command)\n            if located:\n                candidates.append(Path(located))\n\n        for candidate in candidates:\n            if candidate and candidate.exists() and candidate.is_file():\n                return str(candidate)\n        return ""\n\n    def _start_debug_browser(self) -> None:\n        """Starts a dedicated Chromium session with remote debugging enabled."""\n        executable = self._find_chromium_executable()\n        if not executable:\n            raise RuntimeError("Không tìm thấy Chrome/Edge trên máy để mở phiên debug.")\n\n        self._cdp_profile_dir.mkdir(parents=True, exist_ok=True)\n        args = [\n            executable,\n            f"--remote-debugging-port={self.debug_port}",\n            f"--user-data-dir={self._cdp_profile_dir}",\n            "--new-window",\n            "--no-first-run",\n            "--no-default-browser-check",\n        ]\n        if self.target_url:\n            args.append(self.target_url)\n\n        try:\n            subprocess.Popen(\n                args,\n                stdout=subprocess.DEVNULL,\n                stderr=subprocess.DEVNULL,\n                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),\n            )\n        except OSError as error:\n            raise RuntimeError(f"Không thể khởi động browser debug: {error}") from error\n\n    def _ensure_cdp_server(self) -> None:\n        """Ensures the CDP endpoint exists before any automation action."""\n        if self._is_cdp_ready():\n            return\n\n        self._start_debug_browser()\n        deadline = time.time() + 20\n        while time.time() < deadline:\n            if self._is_cdp_ready():\n                return\n            time.sleep(0.5)\n        raise RuntimeError(\n            f"Không thể mở cổng debug {self.debug_port}. Browser debug chưa sẵn sàng."\n        )\n\n    def _is_internal_browser_url(self, raw_url: str) -> bool:\n        """Returns whether one CDP page URL points to a browser-internal surface."""\n        url = raw_url.strip().lower()\n        return (\n            not url\n            or url.startswith("chrome://")\n            or url.startswith("chrome-extension://")\n            or url.startswith("devtools://")\n            or url.startswith("edge://")\n            or url.startswith("about:")\n        )\n\n    def _target_host(self) -> str:\n        """Returns the configured VNEDU host name when the target URL is valid."""\n        try:\n            return (urlparse(self.target_url).hostname or "").strip().lower()\n        except ValueError:\n            return ""\n\n    def _page_priority(self, page: Page) -> int:\n        """Scores CDP pages so the automation prefers stable VNEDU tabs over transient browser popups."""\n        if page.is_closed():\n            return -1\n\n        page_url = (page.url or "").strip()\n        if self._is_internal_browser_url(page_url):\n            return 0\n\n        parsed_page = urlparse(page_url)\n        if parsed_page.scheme not in {"http", "https"}:\n            return 1\n\n        target_url = self.target_url.strip().lower()\n        page_url_lower = page_url.lower()\n        target_host = self._target_host()\n        page_host = (parsed_page.hostname or "").strip().lower()\n\n        if target_url and target_url in page_url_lower:\n            return 110\n        if target_host and page_host == target_host:\n            return 100\n        if page_host == "user.vnedu.vn" and parsed_page.path.startswith("/sso"):\n            return 95\n        if "vnedu" in page_host:\n            return 85\n        return 50\n\n    def _pick_target_page(self, contexts) -> Page | None:\n        """Selects the most suitable page from the live CDP session."""\n        scored_pages: List[Tuple[int, int, Page]] = []\n        ordinal = 0\n        for context in contexts:\n            for page in context.pages:\n                try:\n                    score = self._page_priority(page)\n                except PlaywrightError:\n                    continue\n                if score >= 0:\n                    scored_pages.append((score, ordinal, page))\n                ordinal += 1\n        if not scored_pages:\n            return None\n        scored_pages.sort(key=lambda item: (item[0], item[1]), reverse=True)\n        best_score, _best_ordinal, best_page = scored_pages[0]\n        if best_score >= 85:\n            return best_page\n        return None\n\n    @contextmanager\n    def _open_page(self) -> Iterator[Page]:\n        """Connects to the live Chromium instance and yields one reusable working page."""\n        self._ensure_cdp_server()\n        with sync_playwright() as playwright:\n            try:\n                browser = playwright.chromium.connect_over_cdp(self._cdp_http_endpoint())\n            except PlaywrightError as error:\n                raise RuntimeError(\n                    f"Kết nối CDP thất bại tại {self._cdp_http_endpoint()}."\n                ) from error\n\n            try:\n                contexts = browser.contexts\n                if not contexts:\n                    raise RuntimeError("Không tìm thấy browser context trong phiên CDP.")\n\n                target_page = self._pick_target_page(contexts)\n                if target_page is None:\n                    target_page = contexts[0].new_page()\n                yield target_page\n            finally:\n                browser.close()\n\n    def _goto_target_page(self, page: Page) -> None:\n        """Navigates to the configured VNEDU URL when the current page differs."""\n        if self.target_url and self.target_url not in (page.url or ""):\n            page.goto(self.target_url, wait_until="domcontentloaded", timeout=45000)\n            page.wait_for_timeout(300)\n\n    def _close_notice_popup(self, page: Page) -> None:\n        """Dismisses VNEDU modal notices that block further clicks."""\n        try:\n            ok_button = page.locator("button:has-text(\'OK\')")\n            if ok_button.count() > 0 and ok_button.first.is_visible():\n                ok_button.first.click(timeout=800)\n                page.wait_for_timeout(200)\n        except PlaywrightError:\n            return\n\n    def _find_username_input(self, page: Page):\n        """Returns a likely username input on the current login form."""\n        selectors = [\n            "input[name*=\'user\' i]",\n            "input[id*=\'user\' i]",\n            "input[name*=\'login\' i]",\n            "input[id*=\'login\' i]",\n            "input[name*=\'account\' i]",\n            "input[id*=\'account\' i]",\n            "input[type=\'email\']",\n        ]\n        for selector in selectors:\n            locator = page.locator(selector)\n            if locator.count() > 0:\n                return locator.first\n\n        fallback = page.locator("input[type=\'text\']")\n        if fallback.count() > 0:\n            return fallback.first\n        raise RuntimeError("Không tìm thấy ô nhập tài khoản trên form đăng nhập.")\n\n    def _login_if_needed_on_page(\n        self,\n        page: Page,\n        username: str = "",\n        password: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> str:\n        """Logs in when a password field is visible."""\n        emit_progress(progress_callback, 5.0, "Đang truy cập trang VNEDU...")\n        self._goto_target_page(page)\n        self._close_notice_popup(page)\n\n        password_input = page.locator("input[type=\'password\']")\n        if password_input.count() == 0:\n            emit_progress(progress_callback, 100.0, "Không cần đăng nhập lại, phiên đã sẵn sàng.")\n            if username.strip() or password:\n                return "Không phát hiện form đăng nhập; có thể phiên đã đăng nhập sẵn."\n            return ""\n\n        if not username.strip() or not password:\n            raise RuntimeError(\n                "Phiên hiện tại đang ở màn hình đăng nhập. Hãy nhập tài khoản và mật khẩu VNEDU."\n            )\n\n        username_input = self._find_username_input(page)\n        emit_progress(progress_callback, 20.0, "Đang điền tài khoản và mật khẩu VNEDU...")\n        username_input.fill(username.strip())\n        password_input.first.fill(password)\n\n        captcha_input = page.locator(\n            "input[name*=\'captcha\' i], input[id*=\'captcha\' i], input[placeholder*=\'captcha\' i]"\n        )\n        if captcha_input.count() > 0 and captcha_input.first.is_visible():\n            captcha_value = captcha_input.first.input_value().strip()\n            if not captcha_value:\n                try:\n                    captcha_input.first.focus()\n                except PlaywrightError:\n                    pass\n                raise RuntimeError(\n                    "Trang đăng nhập VNEDU đang yêu cầu mã captcha. "\n                    "App đã điền sẵn tài khoản và mật khẩu trên tab hiện tại; "\n                    "hãy nhập captcha rồi bấm Đăng nhập thủ công, sau đó nhấn lại \'Đăng nhập + đọc Sổ điểm\'."\n                )\n\n        emit_progress(progress_callback, 40.0, "Đang gửi yêu cầu đăng nhập...")\n        clicked = page.evaluate(\n            """() => {\n            const candidates = Array.from(document.querySelectorAll(\'button, input[type="submit"]\'));\n            const target = candidates.find(el => {\n                const text = (el.innerText || el.value || \'\').trim().toLowerCase();\n                return text.includes(\'đăng nhập\') || text.includes(\'dang nhap\') || text.includes(\'login\');\n            });\n            if (!target) return false;\n            target.click();\n            return true;\n        }"""\n        )\n        if not clicked:\n            password_input.first.press("Enter")\n\n        deadline = time.time() + 20\n        while time.time() < deadline:\n            page.wait_for_timeout(250)\n            self._close_notice_popup(page)\n            elapsed_ratio = min((time.time() - (deadline - 20)) / 20.0, 1.0)\n            emit_progress(\n                progress_callback,\n                40.0 + (elapsed_ratio * 55.0),\n                "Đang chờ VNEDU xác thực đăng nhập...",\n            )\n            if page.locator("input[type=\'password\']").count() == 0:\n                emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")\n                return "Đã gửi đăng nhập và xác thực thành công."\n\n        raise RuntimeError("Đăng nhập chưa thành công. Vui lòng kiểm tra tài khoản hoặc xác thực bổ sung.")\n\n    def _has_scorebook_controls(self, page: Page) -> bool:\n        """Checks whether the active VNEDU window looks like the scorebook screen."""\n        return bool(\n            page.evaluate(\n                """() => {\n                const windows = Array.from(document.querySelectorAll(\'.x-window\'))\n                    .filter(win => /sổ điểm/i.test((win.innerText || \'\')));\n                const active = windows.find(win =>\n                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\')\n                );\n                const root = active || (windows.length ? windows[windows.length - 1] : null);\n                if (!root) return false;\n\n                const labels = Array.from(root.querySelectorAll(\'label.x-form-item-label\'))\n                    .map(label => (label.innerText || \'\').toLowerCase());\n                const required = [\'khối\', \'lớp\', \'môn\', \'học kỳ\'];\n                return required.every(token => labels.some(label => label.includes(token)));\n            }"""\n            )\n        )\n\n    def _find_scorebook_shortcut(self, page: Page, timeout_sec: float = 10.0) -> Dict[str, object]:\n        """Finds the VNEDU desktop shortcut metadata for `Sổ điểm` with retry and debug details."""\n        deadline = time.time() + max(timeout_sec, 1.0)\n        last_snapshot: Dict[str, object] = {}\n        while time.time() < deadline:\n            self._close_notice_popup(page)\n            last_snapshot = page.evaluate(\n                """() => {\n                const normalizeText = (value) => (value || \'\')\n                    .toLowerCase()\n                    .normalize(\'NFD\')\n                    .replace(/[\\\\u0300-\\\\u036f]/g, \'\')\n                    .replace(/đ/g, \'d\')\n                    .replace(/\\\\s+/g, \' \')\n                    .trim();\n\n                const serialize = (el) => ({\n                    id: el.id || \'\',\n                    text: (el.innerText || el.textContent || \'\').trim().replace(/\\\\s+/g, \' \'),\n                    className: el.className || \'\',\n                });\n\n                const desktopCandidates = Array.from(\n                    document.querySelectorAll(\'.ux-desktop-shortcut, .x-view-item, [id$="-shortcut"]\')\n                );\n                const visibleCandidates = desktopCandidates.filter(el => {\n                    const rect = el.getBoundingClientRect();\n                    return rect.width > 0 && rect.height > 0;\n                });\n                const target = document.querySelector(\'#Sổ điểm-shortcut\')\n                    || document.querySelector(\'#So diem-shortcut\')\n                    || visibleCandidates.find(el => {\n                        const haystacks = [\n                            normalizeText(el.id),\n                            normalizeText(el.innerText || el.textContent || \'\'),\n                            normalizeText(el.getAttribute(\'title\') || \'\'),\n                        ];\n                        return haystacks.some(text =>\n                            text.includes(\'so diem\') || text.includes(\'sodiem\')\n                        );\n                    });\n                if (!target) {\n                    return {\n                        found: false,\n                        shortcutCount: visibleCandidates.length,\n                        sampleShortcuts: visibleCandidates.slice(0, 12).map(serialize),\n                    };\n                }\n\n                if (typeof target.scrollIntoView === \'function\') {\n                    target.scrollIntoView({ block: \'center\', inline: \'center\', behavior: \'instant\' });\n                }\n                const rect = target.getBoundingClientRect();\n                return {\n                    found: true,\n                    shortcutCount: visibleCandidates.length,\n                    sampleShortcuts: visibleCandidates.slice(0, 12).map(serialize),\n                    id: target.id || \'\',\n                    text: (target.innerText || target.textContent || \'\').trim(),\n                    className: target.className || \'\',\n                    centerX: rect.x + (rect.width / 2),\n                    centerY: rect.y + (rect.height / 2),\n                    width: rect.width,\n                    height: rect.height,\n                };\n            }"""\n            )\n            if last_snapshot.get("found"):\n                return last_snapshot\n            page.wait_for_timeout(300)\n\n        sample_labels = []\n        for item in list(last_snapshot.get("sampleShortcuts", []))[:6]:\n            if isinstance(item, dict):\n                label = str(item.get("text", "")).strip() or str(item.get("id", "")).strip()\n                if label:\n                    sample_labels.append(label)\n        observed = f"shortcut thấy được: {int(last_snapshot.get(\'shortcutCount\', 0) or 0)}"\n        if sample_labels:\n            observed += f" | mẫu: {\', \'.join(sample_labels)}"\n        raise RuntimeError(\n            "Không tìm thấy shortcut Sổ điểm trên desktop VNEDU. "\n            f"{observed}"\n        )\n\n    def _open_scorebook_from_desktop(\n        self,\n        page: Page,\n        progress_callback: ProgressCallback | None = None,\n    ) -> None:\n        """Opens the scorebook window by double-clicking the live desktop shortcut."""\n        emit_progress(progress_callback, 10.0, "Đang tìm shortcut Sổ điểm...")\n        shortcut = self._find_scorebook_shortcut(page)\n        clicked = False\n        shortcut_id = str(shortcut.get("id", "")).strip()\n        if shortcut_id:\n            clicked = bool(\n                page.evaluate(\n                    """(shortcutId) => {\n                    const target = document.getElementById(shortcutId);\n                    if (!target) return false;\n                    if (typeof target.scrollIntoView === \'function\') {\n                        target.scrollIntoView({ block: \'center\', inline: \'center\', behavior: \'instant\' });\n                    }\n                    target.dispatchEvent(new MouseEvent(\'mousedown\', { bubbles: true, cancelable: true, view: window }));\n                    target.dispatchEvent(new MouseEvent(\'mouseup\', { bubbles: true, cancelable: true, view: window }));\n                    target.dispatchEvent(new MouseEvent(\'click\', { bubbles: true, cancelable: true, view: window }));\n                    target.dispatchEvent(new MouseEvent(\'mousedown\', { bubbles: true, cancelable: true, view: window }));\n                    target.dispatchEvent(new MouseEvent(\'mouseup\', { bubbles: true, cancelable: true, view: window }));\n                    target.dispatchEvent(new MouseEvent(\'dblclick\', { bubbles: true, cancelable: true, view: window }));\n                    return true;\n                }""",\n                    shortcut_id,\n                )\n            )\n        if not clicked:\n            page.mouse.dblclick(float(shortcut["centerX"]), float(shortcut["centerY"]))\n        for _ in range(30):\n            page.wait_for_timeout(400)\n            self._close_notice_popup(page)\n            elapsed_ratio = (_ + 1) / 30.0\n            emit_progress(\n                progress_callback,\n                25.0 + (elapsed_ratio * 70.0),\n                "Đang chờ cửa sổ Sổ điểm mở ra...",\n            )\n            if self._has_scorebook_controls(page):\n                emit_progress(progress_callback, 100.0, "Đã mở cửa sổ Sổ điểm.")\n                return\n        raise RuntimeError("Đã bấm shortcut \'Sổ điểm\' nhưng không mở được cửa sổ Sổ điểm.")\n\n    def _ensure_scorebook_screen(\n        self,\n        page: Page,\n        progress_callback: ProgressCallback | None = None,\n    ) -> None:\n        """Ensures the live VNEDU session ends up on the scorebook window."""\n        emit_progress(progress_callback, 5.0, "Đang kiểm tra cửa sổ Sổ điểm...")\n        self._goto_target_page(page)\n        self._close_notice_popup(page)\n        if self._has_scorebook_controls(page):\n            emit_progress(progress_callback, 100.0, "Cửa sổ Sổ điểm đã sẵn sàng.")\n            return\n        emit_progress(progress_callback, 20.0, "Đang mở cửa sổ Sổ điểm...")\n        self._open_scorebook_from_desktop(\n            page,\n            progress_callback=create_subprogress_reporter(progress_callback, 20.0, 100.0),\n        )\n\n    def _read_scorebook_window_shell(self, page: Page) -> Dict[str, object]:\n        """Reads the active scorebook window identity, combo ids, and window-level badges."""\n        return dict(\n            page.evaluate(\n                """() => {\n                if (typeof Ext === \'undefined\') {\n                    return { ok: false, reason: \'ExtJS không tồn tại trên trang.\' };\n                }\n\n                const windows = Array.from(document.querySelectorAll(\'.x-window\'))\n                    .filter(win => /sổ điểm/i.test((win.innerText || \'\')));\n                const active = windows.find(win =>\n                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\')\n                );\n                const root = active || (windows.length ? windows[windows.length - 1] : null);\n                if (!root) {\n                    return { ok: false, reason: \'Không tìm thấy cửa sổ Sổ điểm đang hoạt động.\' };\n                }\n\n                const labels = Array.from(root.querySelectorAll(\'label.x-form-item-label\'));\n                const findComboIdByLabel = (labelPart) => {\n                    const label = labels.find(item => (item.innerText || \'\').toLowerCase().includes(labelPart));\n                    const field = label ? label.closest(\'.x-field\') : null;\n                    return field ? (field.id || \'\') : \'\';\n                };\n\n                const teacherCell = root.querySelector(\'#gvbm\');\n                const permissionCell = Array.from(root.querySelectorAll(\'td\'))\n                    .find(td => /quyền hạn/i.test((td.innerText || \'\').trim()));\n                const commentInputs = Array.from(root.querySelectorAll(\'input.input_nhan_xet\'));\n\n                return {\n                    ok: true,\n                    windowId: root.id || \'\',\n                    windowTitle: ((root.querySelector(\'.x-window-header-text\')?.innerText) || \'\').trim(),\n                    gradeComboId: findComboIdByLabel(\'khối\'),\n                    classComboId: findComboIdByLabel(\'lớp\'),\n                    subjectComboId: findComboIdByLabel(\'môn\'),\n                    termComboId: findComboIdByLabel(\'học kỳ\'),\n                    teacherText: ((teacherCell?.innerText) || \'\').trim(),\n                    permissionText: ((permissionCell?.innerText) || \'\').trim(),\n                    commentInputCount: commentInputs.length,\n                    enabledCommentInputCount: commentInputs.filter(input => !input.disabled && !input.readOnly).length,\n                };\n            }"""\n            )\n        )\n\n    def _read_scorebook_combo_snapshot(self, page: Page, combo_id: str) -> Dict[str, object]:\n        """Reads one scorebook combobox current value together with its current ExtJS store payload."""\n        normalized_combo_id = combo_id.strip()\n        if not normalized_combo_id:\n            return {"id": "", "text": "", "options": []}\n\n        return dict(\n            page.evaluate(\n                """(comboId) => {\n                if (typeof Ext === \'undefined\') {\n                    return { id: \'\', text: \'\', options: [] };\n                }\n\n                const readField = (record, fieldName) => {\n                    if (!record) return \'\';\n                    try {\n                        if (record.get) {\n                            const value = record.get(fieldName);\n                            if (value !== undefined && value !== null) return value;\n                        }\n                    } catch (error) {}\n                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {\n                        return record.data[fieldName];\n                    }\n                    return \'\';\n                };\n\n                const collectStoreRecords = (source, bucket, visitedSources) => {\n                    if (!source) return;\n                    if (typeof source === \'object\' || typeof source === \'function\') {\n                        if (visitedSources.has(source)) return;\n                        visitedSources.add(source);\n                    }\n                    if (Array.isArray(source)) {\n                        bucket.push(...source);\n                        return;\n                    }\n                    if (typeof source.getRange === \'function\') {\n                        try {\n                            const range = source.getRange();\n                            if (Array.isArray(range)) {\n                                bucket.push(...range);\n                            }\n                        } catch (error) {}\n                    }\n                    if (Array.isArray(source.items)) {\n                        bucket.push(...source.items);\n                    }\n                    if (source.data) {\n                        collectStoreRecords(source.data, bucket, visitedSources);\n                    }\n                    if (typeof source.getSource === \'function\') {\n                        try {\n                            collectStoreRecords(source.getSource(), bucket, visitedSources);\n                        } catch (error) {}\n                    } else if (source.source) {\n                        collectStoreRecords(source.source, bucket, visitedSources);\n                    }\n                };\n\n                const readStoreRecords = (store) => {\n                    if (!store) return [];\n                    const bucket = [];\n                    const visitedSources = new WeakSet();\n                    collectStoreRecords(store.snapshot, bucket, visitedSources);\n                    collectStoreRecords(store.data, bucket, visitedSources);\n                    if (typeof store.getData === \'function\') {\n                        try {\n                            collectStoreRecords(store.getData(), bucket, visitedSources);\n                        } catch (error) {}\n                    }\n                    collectStoreRecords(store, bucket, visitedSources);\n\n                    const deduped = [];\n                    const seen = new Set();\n                    for (const record of bucket) {\n                        const recordId = String(readField(record, \'id\')).trim();\n                        const recordText = String(readField(record, \'ten\') || readField(record, \'value\')).trim();\n                        const key = `${recordId}||${recordText}`;\n                        if (!recordId && !recordText) continue;\n                        if (seen.has(key)) continue;\n                        seen.add(key);\n                        deduped.push(record);\n                    }\n                    return deduped;\n                };\n\n                const cmp = Ext.getCmp(comboId);\n                if (!cmp) {\n                    return { id: \'\', text: \'\', options: [] };\n                }\n                const store = cmp.getStore ? cmp.getStore() : cmp.store;\n                const range = store ? readStoreRecords(store) : [];\n                const currentValue = cmp.getValue ? cmp.getValue() : \'\';\n                const currentText = cmp.getRawValue ? cmp.getRawValue() : \'\';\n                const matchedRecord = range.find(record => {\n                    const recordId = String(readField(record, \'id\'));\n                    const recordText = String(readField(record, \'ten\') || readField(record, \'value\'));\n                    const normalizedValue = currentValue === undefined || currentValue === null ? \'\' : String(currentValue);\n                    const normalizedText = currentText === undefined || currentText === null ? \'\' : String(currentText);\n                    return recordId === normalizedValue || recordText === normalizedValue || recordText === normalizedText;\n                });\n                const normalizedId = matchedRecord\n                    ? String(readField(matchedRecord, \'id\'))\n                    : (currentValue === undefined || currentValue === null ? \'\' : String(currentValue));\n                const normalizedText = currentText === undefined || currentText === null ? \'\' : String(currentText);\n\n                return {\n                    id: normalizedId,\n                    text: normalizedText || (matchedRecord ? String(readField(matchedRecord, \'ten\') || readField(matchedRecord, \'value\')) : \'\'),\n                    options: range.map(record => ({\n                        id: String(readField(record, \'id\')),\n                        ten: String(readField(record, \'ten\') || readField(record, \'value\')),\n                    })),\n                };\n            }""",\n                normalized_combo_id,\n            )\n        )\n\n    def _read_scorebook_hidden_values(self, page: Page, window_id: str = "") -> Dict[str, str]:\n        """Reads hidden scorebook form inputs from the active or specified scorebook window."""\n        return dict(\n            page.evaluate(\n                """(windowId) => {\n                const windows = Array.from(document.querySelectorAll(\'.x-window\'))\n                    .filter(win => /sổ điểm/i.test((win.innerText || \'\')));\n                const active = windows.find(win =>\n                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\')\n                );\n                const root = (windowId && document.getElementById(windowId)) || active || (windows.length ? windows[windows.length - 1] : null);\n                if (!root) {\n                    return {};\n                }\n\n                const form = root.querySelector(\'form\');\n                const hiddenValues = {};\n                if (!form) {\n                    return hiddenValues;\n                }\n                const hiddenInputs = Array.from(form.querySelectorAll(\'input[type="hidden"]\'));\n                for (const input of hiddenInputs) {\n                    const key = (input.name || input.id || \'\').trim();\n                    if (!key) continue;\n                    hiddenValues[key] = String(input.value || \'\');\n                }\n                return hiddenValues;\n            }""",\n                window_id.strip(),\n            )\n        )\n\n    def _read_scorebook_table_snapshot(self, page: Page, window_id: str = "") -> Dict[str, object]:\n        """Reads the visible score table structure from the active or specified scorebook window."""\n        return dict(\n            page.evaluate(\n                """(windowId) => {\n                const windows = Array.from(document.querySelectorAll(\'.x-window\'))\n                    .filter(win => /sổ điểm/i.test((win.innerText || \'\')));\n                const active = windows.find(win =>\n                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\')\n                );\n                const root = (windowId && document.getElementById(windowId)) || active || (windows.length ? windows[windows.length - 1] : null);\n                if (!root) {\n                    return {\n                        headerRows: [],\n                        bodyRows: [],\n                        rowCount: 0,\n                        tableClass: \'\',\n                    };\n                }\n\n                const scoreTable = root.querySelector(\'table.table.tablefix\');\n                if (!scoreTable) {\n                    return {\n                        headerRows: [],\n                        bodyRows: [],\n                        rowCount: 0,\n                        tableClass: \'\',\n                    };\n                }\n\n                const normalizeText = (value) => String(value || \'\').replace(/\\\\s+/g, \' \').trim();\n                const readInput = (input) => {\n                    if (!input) return null;\n                    const tagName = String(input.tagName || \'\').toUpperCase();\n                    const elementValue = tagName === \'SPAN\'\n                        ? (input.textContent || input.innerText || \'\')\n                        : (input.value || \'\');\n                    return {\n                        tagName: input.tagName || \'\',\n                        type: input.type || \'\',\n                        id: input.id || \'\',\n                        name: input.name || \'\',\n                        className: input.className || \'\',\n                        value: elementValue,\n                        b: input.getAttribute(\'b\') || \'\',\n                        c: input.getAttribute(\'c\') || \'\',\n                        bc: input.getAttribute(\'bc\') || \'\',\n                        dd: input.getAttribute(\'dd\') || \'\',\n                    };\n                };\n\n                const headerRows = Array.from(scoreTable.querySelectorAll(\'thead tr\')).map((tr, rowIndex) => ({\n                    rowIndex,\n                    cells: Array.from(tr.querySelectorAll(\'td, th\')).map((cell, cellIndex) => ({\n                        cellIndex,\n                        text: normalizeText(cell.innerText || cell.textContent || \'\'),\n                        className: cell.className || \'\',\n                        colspan: parseInt(cell.getAttribute(\'colspan\') || \'1\', 10) || 1,\n                        rowspan: parseInt(cell.getAttribute(\'rowspan\') || \'1\', 10) || 1,\n                        cn: cell.getAttribute(\'cn\') || \'\',\n                        cl: cell.getAttribute(\'cl\') || \'\',\n                        b: cell.getAttribute(\'b\') || \'\',\n                        c: cell.getAttribute(\'c\') || \'\',\n                    })),\n                }));\n\n                const bodyRows = Array.from(scoreTable.querySelectorAll(\'tbody tr\')).slice(0, 5).map((tr, rowIndex) => ({\n                    rowIndex,\n                    rowId: tr.id || \'\',\n                    cells: Array.from(tr.children).map((cell, cellIndex) => ({\n                        cellIndex,\n                        tagName: cell.tagName || \'\',\n                        text: normalizeText(cell.innerText || cell.textContent || \'\'),\n                        className: cell.className || \'\',\n                        dataLoai: cell.getAttribute(\'data-loai\') || \'\',\n                        dataCot: cell.getAttribute(\'data-cot\') || \'\',\n                        dataHang: cell.getAttribute(\'data-hang\') || \'\',\n                        inputInfo: readInput(cell.querySelector(\'input, textarea, select, span.input_diem, span.input_nhan_xet\')),\n                    })),\n                }));\n\n                return {\n                    headerRows,\n                    bodyRows,\n                    rowCount: scoreTable.querySelectorAll(\'tbody tr\').length,\n                    tableClass: scoreTable.className || \'\',\n                };\n            }""",\n                window_id.strip(),\n            )\n        )\n\n    def _scorebook_snapshot(self, page: Page) -> Dict[str, object]:\n        """Reads scorebook combobox ids, current values, and ExtJS store items."""\n        shell_snapshot = self._read_scorebook_window_shell(page)\n        if not shell_snapshot.get("ok"):\n            raise RuntimeError(str(shell_snapshot.get("reason", "Không đọc được cấu trúc Sổ điểm.")))\n\n        window_id = str(shell_snapshot.get("windowId", "")).strip()\n        hidden_values = self._read_scorebook_hidden_values(page, window_id=window_id)\n        score_table_info = self._read_scorebook_table_snapshot(page, window_id=window_id)\n\n        grade_combo_id = str(shell_snapshot.get("gradeComboId", "")).strip()\n        class_combo_id = str(shell_snapshot.get("classComboId", "")).strip()\n        subject_combo_id = str(shell_snapshot.get("subjectComboId", "")).strip()\n        term_combo_id = str(shell_snapshot.get("termComboId", "")).strip()\n\n        grade_snapshot = self._read_scorebook_combo_snapshot(page, grade_combo_id)\n        class_snapshot = self._read_scorebook_combo_snapshot(page, class_combo_id)\n        subject_snapshot = self._read_scorebook_combo_snapshot(page, subject_combo_id)\n        term_snapshot = self._read_scorebook_combo_snapshot(page, term_combo_id)\n\n        return {\n            "ok": True,\n            "windowId": window_id,\n            "windowTitle": str(shell_snapshot.get("windowTitle", "")).strip(),\n            "gradeComboId": grade_combo_id,\n            "classComboId": class_combo_id,\n            "subjectComboId": subject_combo_id,\n            "termComboId": term_combo_id,\n            "currentGradeId": str(grade_snapshot.get("id", "")).strip(),\n            "currentGradeText": str(grade_snapshot.get("text", "")).strip(),\n            "currentClassId": str(class_snapshot.get("id", "")).strip(),\n            "currentClassText": str(class_snapshot.get("text", "")).strip(),\n            "currentSubjectId": str(subject_snapshot.get("id", "")).strip(),\n            "currentSubjectText": str(subject_snapshot.get("text", "")).strip(),\n            "currentTermId": str(term_snapshot.get("id", "")).strip(),\n            "currentTermText": str(term_snapshot.get("text", "")).strip(),\n            "teacherText": str(shell_snapshot.get("teacherText", "")).strip(),\n            "permissionText": str(shell_snapshot.get("permissionText", "")).strip(),\n            "commentInputCount": int(shell_snapshot.get("commentInputCount", 0) or 0),\n            "enabledCommentInputCount": int(shell_snapshot.get("enabledCommentInputCount", 0) or 0),\n            "hiddenSchoolYear": str(hidden_values.get("iNamHoc", "")).strip(),\n            "hiddenGradeId": str(hidden_values.get("iKhoi", "")).strip(),\n            "hiddenClassId": str(hidden_values.get("iLopId", "")).strip(),\n            "hiddenSubjectId": str(hidden_values.get("iMonHocId", "")).strip(),\n            "hiddenTermId": str(hidden_values.get("iHocKy", "")).strip(),\n            "scoreTableClass": str(score_table_info.get("tableClass", "")).strip(),\n            "scoreTableRowCount": int(score_table_info.get("rowCount", 0) or 0),\n            "scoreTableHeaderRows": list(score_table_info.get("headerRows", [])),\n            "scoreTableBodyRows": list(score_table_info.get("bodyRows", [])),\n            "gradeOptions": list(grade_snapshot.get("options", [])),\n            "classOptions": list(class_snapshot.get("options", [])),\n            "subjectOptions": list(subject_snapshot.get("options", [])),\n            "termOptions": list(term_snapshot.get("options", [])),\n        }\n\n    def _read_combo_store_items(self, page: Page, combo_id: str) -> List[Dict[str, object]]:\n        """Reads the current ExtJS store items for one scorebook combobox."""\n        normalized_combo_id = combo_id.strip()\n        if not normalized_combo_id:\n            return []\n        try:\n            raw_items = page.evaluate(\n                """(comboId) => {\n                if (typeof Ext === \'undefined\') return [];\n                const cmp = Ext.getCmp(comboId);\n                if (!cmp) return [];\n                const store = cmp.getStore ? cmp.getStore() : cmp.store;\n                if (!store) return [];\n\n                const readField = (record, fieldName) => {\n                    if (!record) return \'\';\n                    try {\n                        if (record.get) {\n                            const value = record.get(fieldName);\n                            if (value !== undefined && value !== null) return value;\n                        }\n                    } catch (error) {}\n                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {\n                        return record.data[fieldName];\n                    }\n                    return \'\';\n                };\n\n                const collectStoreRecords = (source, bucket, visitedSources) => {\n                    if (!source || typeof source !== \'object\') {\n                        return;\n                    }\n                    if (visitedSources.has(source)) {\n                        return;\n                    }\n                    visitedSources.add(source);\n                    if (typeof source.getRange === \'function\') {\n                        try {\n                            const range = source.getRange();\n                            if (Array.isArray(range)) {\n                                bucket.push(...range);\n                            }\n                        } catch (error) {}\n                    }\n                    if (Array.isArray(source.items)) {\n                        bucket.push(...source.items);\n                    }\n                    if (source.data) {\n                        collectStoreRecords(source.data, bucket, visitedSources);\n                    }\n                    if (typeof source.getSource === \'function\') {\n                        try {\n                            collectStoreRecords(source.getSource(), bucket, visitedSources);\n                        } catch (error) {}\n                    } else if (source.source) {\n                        collectStoreRecords(source.source, bucket, visitedSources);\n                    }\n                };\n\n                const bucket = [];\n                const visitedSources = new WeakSet();\n                collectStoreRecords(store.snapshot, bucket, visitedSources);\n                collectStoreRecords(store.data, bucket, visitedSources);\n                if (typeof store.getData === \'function\') {\n                    try {\n                        collectStoreRecords(store.getData(), bucket, visitedSources);\n                    } catch (error) {}\n                }\n                collectStoreRecords(store, bucket, visitedSources);\n\n                const deduped = [];\n                const seen = new Set();\n                for (const record of bucket) {\n                    const recordId = String(readField(record, \'id\')).trim();\n                    const recordText = String(readField(record, \'ten\') || readField(record, \'value\')).trim();\n                    const key = `${recordId}||${recordText}`;\n                    if (!recordId && !recordText) continue;\n                    if (seen.has(key)) continue;\n                    seen.add(key);\n                    deduped.push({\n                        id: recordId,\n                        ten: recordText,\n                    });\n                }\n                return deduped;\n            }""",\n                normalized_combo_id,\n            )\n        except PlaywrightError:\n            return []\n        return list(raw_items or [])\n\n    def _load_live_combo_options(self, page: Page, combo_id: str) -> List[Dict[str, object]]:\n        """Expands one combo so dependent ExtJS stores can populate before the app reads them."""\n        normalized_combo_id = combo_id.strip()\n        if not normalized_combo_id:\n            return []\n\n        try:\n            page.evaluate(\n                """(comboId) => {\n                if (typeof Ext === \'undefined\') return false;\n                const cmp = Ext.getCmp(comboId);\n                if (!cmp) return false;\n                try {\n                    if (cmp.onTriggerClick) {\n                        cmp.onTriggerClick();\n                    } else if (cmp.expand) {\n                        cmp.expand();\n                    }\n                    return true;\n                } catch (error) {\n                    return false;\n                }\n            }""",\n                normalized_combo_id,\n            )\n        except PlaywrightError:\n            return self._read_combo_store_items(page, normalized_combo_id)\n\n        best_items = self._read_combo_store_items(page, normalized_combo_id)\n        last_signature: Tuple[Tuple[str, str], ...] = tuple()\n        stable_reads = 0\n        deadline = time.time() + 2.5\n        while time.time() < deadline:\n            page.wait_for_timeout(180)\n            current_items = self._read_combo_store_items(page, normalized_combo_id)\n            if len(current_items) > len(best_items):\n                best_items = current_items\n            current_signature = tuple(\n                (\n                    str(item.get("id", "")).strip(),\n                    str(item.get("ten", "")).strip(),\n                )\n                for item in current_items\n            )\n            if current_signature and current_signature == last_signature:\n                stable_reads += 1\n                if stable_reads >= 1:\n                    if current_items:\n                        best_items = current_items\n                    break\n            else:\n                stable_reads = 0\n            last_signature = current_signature\n\n        try:\n            page.evaluate(\n                """(comboId) => {\n                if (typeof Ext === \'undefined\') return false;\n                const cmp = Ext.getCmp(comboId);\n                if (!cmp) return false;\n                try {\n                    if (cmp.collapse) cmp.collapse();\n                    return true;\n                } catch (error) {\n                    return false;\n                }\n            }""",\n                normalized_combo_id,\n            )\n        except PlaywrightError:\n            pass\n\n        return best_items\n\n    def _hydrate_scorebook_snapshot_options(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        *,\n        include_grade: bool = True,\n        include_class: bool = True,\n        include_subject: bool = True,\n        include_term: bool = True,\n        only_when_incomplete: bool = False,\n        merge_existing: bool = True,\n        preserve_selected_if_missing: bool = True,\n    ) -> Dict[str, object]:\n        """Refreshes combo option payloads so lazily loaded stores are not mistaken for single-value lists."""\n        updated_snapshot = dict(snapshot)\n        combo_specs = []\n        if include_grade:\n            combo_specs.append(("gradeComboId", "gradeOptions", "currentGradeId", "currentGradeText", "hiddenGradeId"))\n        if include_class:\n            combo_specs.append(("classComboId", "classOptions", "currentClassId", "currentClassText", "hiddenClassId"))\n        if include_subject:\n            combo_specs.append(("subjectComboId", "subjectOptions", "currentSubjectId", "currentSubjectText", "hiddenSubjectId"))\n        if include_term:\n            combo_specs.append(("termComboId", "termOptions", "currentTermId", "currentTermText", "hiddenTermId"))\n\n        for combo_id_key, options_key, current_key, current_text_key, hidden_key in combo_specs:\n            combo_id = str(updated_snapshot.get(combo_id_key, "")).strip()\n            if not combo_id:\n                continue\n            existing_options = self._build_options(list(updated_snapshot.get(options_key, [])))\n            if only_when_incomplete and not self._snapshot_options_need_live_refresh(\n                updated_snapshot,\n                options=existing_options,\n                current_key=current_key,\n                hidden_key=hidden_key,\n            ):\n                continue\n            live_options = self._build_options(self._load_live_combo_options(page, combo_id))\n            merged_options = resolve_hydrated_score_options(\n                existing_options,\n                live_options,\n                selected_id=self._effective_snapshot_selected_id(updated_snapshot, current_key, hidden_key),\n                selected_text=str(updated_snapshot.get(current_text_key, "")).strip(),\n                merge_existing=merge_existing,\n                preserve_selected_if_missing=preserve_selected_if_missing,\n            )\n            updated_snapshot[options_key] = [\n                {\n                    "id": option.option_id,\n                    "ten": option.option_text,\n                }\n                for option in merged_options\n            ]\n\n        return updated_snapshot\n\n    def _snapshot_options_need_live_refresh(\n        self,\n        snapshot: Dict[str, object],\n        *,\n        options: List[ScoreOption],\n        current_key: str,\n        hidden_key: str,\n    ) -> bool:\n        """Returns whether one combo snapshot still looks incomplete enough to justify live expansion."""\n        if not options:\n            return True\n        selected_id = self._effective_snapshot_selected_id(snapshot, current_key, hidden_key)\n        if selected_id and all(option.option_id != selected_id for option in options):\n            return True\n        return len(options) <= 1\n\n    def _snapshot_option_ids(self, snapshot: Dict[str, object], options_key: str) -> set[str]:\n        """Returns the normalized option ids currently present in one snapshot store."""\n        return {\n            option.option_id\n            for option in self._build_options(list(snapshot.get(options_key, [])))\n            if option.option_id.strip()\n        }\n\n    def _snapshot_store_excludes_stale_option(\n        self,\n        snapshot: Dict[str, object],\n        *,\n        options_key: str,\n        stale_option_id: str,\n        actual_selected_id: str = "",\n    ) -> bool:\n        """Returns whether one combo store no longer looks tied to a stale parent selection."""\n        normalized_stale_id = stale_option_id.strip()\n        if not normalized_stale_id:\n            return True\n        if actual_selected_id.strip() and actual_selected_id.strip() != normalized_stale_id:\n            return True\n        return normalized_stale_id not in self._snapshot_option_ids(snapshot, options_key)\n\n    def _wait_for_hydrated_scorebook_options(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        *,\n        options_key: str,\n        include_grade: bool = False,\n        include_class: bool = False,\n        include_subject: bool = False,\n        include_term: bool = False,\n        expected_grade_id: str | None = None,\n        expected_class_id: str | None = None,\n        stale_option_id: str = "",\n        timeout_sec: float = 5.0,\n        progress_callback: ProgressCallback | None = None,\n        progress_message: str = "Đang nạp lại dữ liệu combobox từ Sổ điểm...",\n    ) -> Dict[str, object]:\n        """Polls live combo expansion until one dependent option store is fresh enough to trust."""\n        deadline = time.time() + timeout_sec\n        latest_snapshot = dict(snapshot)\n        while time.time() < deadline:\n            latest_snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                latest_snapshot,\n                include_grade=include_grade,\n                include_class=include_class,\n                include_subject=include_subject,\n                include_term=include_term,\n                merge_existing=False,\n                preserve_selected_if_missing=False,\n            )\n            actual_grade_id = self._effective_snapshot_selected_id(latest_snapshot, "currentGradeId", "hiddenGradeId")\n            actual_class_id = self._effective_snapshot_selected_id(latest_snapshot, "currentClassId", "hiddenClassId")\n            option_ids = self._snapshot_option_ids(latest_snapshot, options_key)\n            parent_ready = (\n                (expected_grade_id is None or actual_grade_id == expected_grade_id)\n                and (expected_class_id is None or actual_class_id == expected_class_id)\n            )\n            store_ready = bool(option_ids) and self._snapshot_store_excludes_stale_option(\n                latest_snapshot,\n                options_key=options_key,\n                stale_option_id=stale_option_id,\n                actual_selected_id=(actual_class_id if options_key == "classOptions" else ""),\n            )\n            if parent_ready and store_ready:\n                emit_progress(progress_callback, 100.0, progress_message)\n                return latest_snapshot\n            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)\n            emit_progress(progress_callback, max(10.0, elapsed_ratio * 95.0), progress_message)\n            page.wait_for_timeout(250)\n            refreshed_snapshot, _snapshot_error = self._best_effort_scorebook_snapshot(page)\n            if refreshed_snapshot is not None:\n                latest_snapshot = refreshed_snapshot\n        return latest_snapshot\n\n    def _read_combo_target_state(self, page: Page, combo_id: str, value_id: str) -> Dict[str, object]:\n        """Reads the selectors and target text needed to select one ExtJS combobox option."""\n        return dict(\n            page.evaluate(\n                """({ comboId, valueId }) => {\n                if (typeof Ext === \'undefined\') return { ok: false, reason: \'ExtJS không tồn tại trên trang.\' };\n                const cmp = Ext.getCmp(comboId);\n                if (!cmp) return { ok: false, reason: `Không tìm thấy combobox ${comboId}.` };\n                const store = cmp.getStore ? cmp.getStore() : cmp.store;\n                if (!store) return { ok: false, reason: `Combobox ${comboId} không có store.` };\n\n                const readField = (record, fieldName) => {\n                    if (!record) return \'\';\n                    try {\n                        if (record.get) {\n                            const value = record.get(fieldName);\n                            if (value !== undefined && value !== null) return value;\n                        }\n                    } catch (error) {}\n                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {\n                        return record.data[fieldName];\n                    }\n                    return \'\';\n                };\n\n                const collectStoreRecords = (source, bucket, visitedSources) => {\n                    if (!source) return;\n                    if (typeof source === \'object\' || typeof source === \'function\') {\n                        if (visitedSources.has(source)) return;\n                        visitedSources.add(source);\n                    }\n                    if (Array.isArray(source)) {\n                        bucket.push(...source);\n                        return;\n                    }\n                    if (typeof source.getRange === \'function\') {\n                        try {\n                            const range = source.getRange();\n                            if (Array.isArray(range)) {\n                                bucket.push(...range);\n                            }\n                        } catch (error) {}\n                    }\n                    if (Array.isArray(source.items)) {\n                        bucket.push(...source.items);\n                    }\n                    if (source.data) {\n                        collectStoreRecords(source.data, bucket, visitedSources);\n                    }\n                    if (typeof source.getSource === \'function\') {\n                        try {\n                            collectStoreRecords(source.getSource(), bucket, visitedSources);\n                        } catch (error) {}\n                    } else if (source.source) {\n                        collectStoreRecords(source.source, bucket, visitedSources);\n                    }\n                };\n\n                const readStoreRecords = (currentStore) => {\n                    if (!currentStore) return [];\n                    const bucket = [];\n                    const visitedSources = new WeakSet();\n                    collectStoreRecords(currentStore.snapshot, bucket, visitedSources);\n                    collectStoreRecords(currentStore.data, bucket, visitedSources);\n                    if (typeof currentStore.getData === \'function\') {\n                        try {\n                            collectStoreRecords(currentStore.getData(), bucket, visitedSources);\n                        } catch (error) {}\n                    }\n                    collectStoreRecords(currentStore, bucket, visitedSources);\n\n                    const deduped = [];\n                    const seen = new Set();\n                    for (const record of bucket) {\n                        const recordId = String(readField(record, \'id\')).trim();\n                        const recordText = String(readField(record, \'ten\') || readField(record, \'value\')).trim();\n                        const key = `${recordId}||${recordText}`;\n                        if (!recordId && !recordText) continue;\n                        if (seen.has(key)) continue;\n                        seen.add(key);\n                        deduped.push(record);\n                    }\n                    return deduped;\n                };\n\n                const range = readStoreRecords(store);\n                const target = range.find(record => String(readField(record, \'id\')) === valueId);\n                if (!target) {\n                    return { ok: false, reason: `Không tìm thấy option id=${valueId} trong combobox ${comboId}.` };\n                }\n\n                const field = document.getElementById(comboId);\n                const trigger = field?.querySelector(\'.x-form-arrow-trigger, .x-form-trigger\');\n                const input = field?.querySelector(\'input.x-form-field, input[role="textbox"]\');\n                const targetText = String(readField(target, \'ten\') || readField(target, \'value\') || valueId).trim();\n                const currentValue = cmp.getValue ? cmp.getValue() : \'\';\n                const currentText = cmp.getRawValue ? cmp.getRawValue() : \'\';\n\n                return {\n                    ok: true,\n                    comboId,\n                    valueId,\n                    targetText,\n                    currentValue: currentValue === undefined || currentValue === null ? \'\' : String(currentValue),\n                    currentText: currentText === undefined || currentText === null ? \'\' : String(currentText),\n                    fieldSelector: field?.id ? `#${CSS.escape(field.id)}` : \'\',\n                    triggerSelector: trigger?.id ? `#${CSS.escape(trigger.id)}` : \'\',\n                    inputSelector: input?.id ? `#${CSS.escape(input.id)}` : \'\',\n                };\n            }""",\n                {"comboId": combo_id, "valueId": value_id},\n            )\n        )\n\n    def _select_combo_via_extjs_fallback(self, page: Page, combo_id: str, value_id: str) -> bool:\n        """Falls back to direct ExtJS mutation when the real picker path is unavailable."""\n        return bool(\n            page.evaluate(\n                """({ comboId, valueId }) => {\n                if (typeof Ext === \'undefined\') return false;\n                const cmp = Ext.getCmp(comboId);\n                if (!cmp) return false;\n                const store = cmp.getStore ? cmp.getStore() : cmp.store;\n                if (!store) return false;\n\n                const readField = (record, fieldName) => {\n                    if (!record) return \'\';\n                    try {\n                        if (record.get) {\n                            const value = record.get(fieldName);\n                            if (value !== undefined && value !== null) return value;\n                        }\n                    } catch (error) {}\n                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {\n                        return record.data[fieldName];\n                    }\n                    return \'\';\n                };\n\n                const collectStoreRecords = (source, bucket, visitedSources) => {\n                    if (!source) return;\n                    if (typeof source === \'object\' || typeof source === \'function\') {\n                        if (visitedSources.has(source)) return;\n                        visitedSources.add(source);\n                    }\n                    if (Array.isArray(source)) {\n                        bucket.push(...source);\n                        return;\n                    }\n                    if (typeof source.getRange === \'function\') {\n                        try {\n                            const range = source.getRange();\n                            if (Array.isArray(range)) {\n                                bucket.push(...range);\n                            }\n                        } catch (error) {}\n                    }\n                    if (Array.isArray(source.items)) {\n                        bucket.push(...source.items);\n                    }\n                    if (source.data) {\n                        collectStoreRecords(source.data, bucket, visitedSources);\n                    }\n                    if (typeof source.getSource === \'function\') {\n                        try {\n                            collectStoreRecords(source.getSource(), bucket, visitedSources);\n                        } catch (error) {}\n                    } else if (source.source) {\n                        collectStoreRecords(source.source, bucket, visitedSources);\n                    }\n                };\n\n                const readStoreRecords = (currentStore) => {\n                    if (!currentStore) return [];\n                    const bucket = [];\n                    const visitedSources = new WeakSet();\n                    collectStoreRecords(currentStore.snapshot, bucket, visitedSources);\n                    collectStoreRecords(currentStore.data, bucket, visitedSources);\n                    if (typeof currentStore.getData === \'function\') {\n                        try {\n                            collectStoreRecords(currentStore.getData(), bucket, visitedSources);\n                        } catch (error) {}\n                    }\n                    collectStoreRecords(currentStore, bucket, visitedSources);\n\n                    const deduped = [];\n                    const seen = new Set();\n                    for (const record of bucket) {\n                        const recordId = String(readField(record, \'id\')).trim();\n                        const recordText = String(readField(record, \'ten\') || readField(record, \'value\')).trim();\n                        const key = `${recordId}||${recordText}`;\n                        if (!recordId && !recordText) continue;\n                        if (seen.has(key)) continue;\n                        seen.add(key);\n                        deduped.push(record);\n                    }\n                    return deduped;\n                };\n\n                const target = readStoreRecords(store).find(record => String(readField(record, \'id\')) === valueId);\n                if (!target) return false;\n\n                const targetText = String(readField(target, \'ten\') || readField(target, \'value\') || valueId).trim();\n                try {\n                    if (typeof store.clearFilter === \'function\') {\n                        store.clearFilter();\n                    }\n                } catch (error) {}\n                try {\n                    if (cmp.select) {\n                        cmp.select(target, true);\n                    }\n                } catch (error) {}\n                try {\n                    if (cmp.setValue) {\n                        cmp.setValue(valueId);\n                    }\n                } catch (error) {}\n                try {\n                    if (cmp.setRawValue) {\n                        cmp.setRawValue(targetText);\n                    }\n                } catch (error) {}\n\n                const field = document.getElementById(comboId);\n                const input = (cmp.inputEl && cmp.inputEl.dom)\n                    ? cmp.inputEl.dom\n                    : field?.querySelector(\'input.x-form-field, input[role="textbox"]\');\n\n                if (input) {\n                    input.dispatchEvent(new Event(\'input\', { bubbles: true }));\n                    input.dispatchEvent(new Event(\'change\', { bubbles: true }));\n                }\n\n                try {\n                    if (cmp.collapse) {\n                        cmp.collapse();\n                    }\n                } catch (error) {}\n\n                const currentValue = cmp.getValue ? cmp.getValue() : \'\';\n                return String(currentValue === undefined || currentValue === null ? \'\' : currentValue) === valueId;\n            }""",\n                {"comboId": combo_id, "valueId": value_id},\n            )\n        )\n\n    def _select_combo_via_picker_click(self, page: Page, combo_state: Dict[str, object]) -> bool:\n        """Uses the visible ExtJS picker to select the target option when the trigger is available."""\n        target_text = str(combo_state.get("targetText", "")).strip()\n        if not target_text:\n            return False\n\n        item_pattern = re.compile(rf"^\\s*{re.escape(target_text)}\\s*$")\n        trigger_selector = str(combo_state.get("triggerSelector", "")).strip()\n        input_selector = str(combo_state.get("inputSelector", "")).strip()\n        field_selector = str(combo_state.get("fieldSelector", "")).strip()\n\n        trigger_locator = None\n        if trigger_selector:\n            trigger_locator = page.locator(trigger_selector)\n        elif input_selector:\n            trigger_locator = page.locator(input_selector)\n        elif field_selector:\n            trigger_locator = page.locator(field_selector)\n        else:\n            return False\n\n        try:\n            trigger_locator.click(timeout=2500)\n        except PlaywrightError:\n            try:\n                trigger_locator.click(timeout=2500, force=True)\n            except PlaywrightError:\n                return False\n\n        picker_items = page.locator(".x-boundlist.x-layer:visible .x-boundlist-item")\n        try:\n            picker_items.first.wait_for(state="visible", timeout=2500)\n        except PlaywrightError:\n            return False\n\n        target_item = picker_items.filter(has_text=item_pattern).first\n        try:\n            target_item.wait_for(state="visible", timeout=2500)\n            target_item.click(timeout=2500)\n        except PlaywrightError:\n            try:\n                target_item.click(timeout=2500, force=True)\n            except PlaywrightError:\n                return False\n\n        page.wait_for_timeout(150)\n        return True\n\n    def _set_combo_value(self, page: Page, combo_id: str, value_id: str) -> bool:\n        """Selects one ExtJS combobox value via a real picker click instead of mutating combo internals."""\n        combo_id = combo_id.strip()\n        value_id = value_id.strip()\n        if not combo_id or not value_id:\n            return False\n\n        combo_state = self._read_combo_target_state(page, combo_id, value_id)\n        if not combo_state.get("ok"):\n            return False\n\n        if self._select_combo_via_picker_click(page, combo_state):\n            return True\n        return self._select_combo_via_extjs_fallback(page, combo_id, value_id)\n\n    def _build_scorebook_context(self, snapshot: Dict[str, object]) -> ScorebookContext:\n        """Builds a typed scorebook context from one raw ExtJS snapshot."""\n        column_schemas = self._extract_scorebook_schema_from_snapshot(snapshot)\n        selected_grade_id = self._effective_snapshot_selected_id(\n            snapshot,\n            current_key="currentGradeId",\n            hidden_key="hiddenGradeId",\n        )\n        selected_class_id = self._effective_snapshot_selected_id(\n            snapshot,\n            current_key="currentClassId",\n            hidden_key="hiddenClassId",\n        )\n        selected_subject_id = self._effective_snapshot_selected_id(\n            snapshot,\n            current_key="currentSubjectId",\n            hidden_key="hiddenSubjectId",\n        )\n        selected_term_id = self._effective_snapshot_selected_id(\n            snapshot,\n            current_key="currentTermId",\n            hidden_key="hiddenTermId",\n        )\n        return ScorebookContext(\n            grade_options=ensure_selected_score_option(\n                self._build_options(list(snapshot.get("gradeOptions", []))),\n                selected_grade_id,\n                str(snapshot.get("currentGradeText", "")).strip(),\n            ),\n            selected_grade_id=selected_grade_id,\n            class_options=ensure_selected_score_option(\n                self._build_options(list(snapshot.get("classOptions", []))),\n                selected_class_id,\n                str(snapshot.get("currentClassText", "")).strip(),\n            ),\n            selected_class_id=selected_class_id,\n            subject_options=ensure_selected_score_option(\n                self._build_options(list(snapshot.get("subjectOptions", []))),\n                selected_subject_id,\n                str(snapshot.get("currentSubjectText", "")).strip(),\n            ),\n            selected_subject_id=selected_subject_id,\n            term_options=ensure_selected_score_option(\n                self._build_options(list(snapshot.get("termOptions", []))),\n                selected_term_id,\n                str(snapshot.get("currentTermText", "")).strip(),\n            ),\n            selected_term_id=selected_term_id,\n            window_id=str(snapshot.get("windowId", "")).strip(),\n            window_title=str(snapshot.get("windowTitle", "")).strip(),\n            teacher_text=str(snapshot.get("teacherText", "")).strip(),\n            permission_text=str(snapshot.get("permissionText", "")).strip(),\n            comment_input_count=int(snapshot.get("commentInputCount", 0) or 0),\n            enabled_comment_input_count=int(snapshot.get("enabledCommentInputCount", 0) or 0),\n            column_schemas=column_schemas,\n            detected_columns=self._detect_scorebook_columns(column_schemas),\n        )\n\n    def _effective_snapshot_selected_id(\n        self,\n        snapshot: Dict[str, object],\n        current_key: str,\n        hidden_key: str,\n    ) -> str:\n        """Returns the most reliable selected id from one raw scorebook snapshot."""\n        hidden_value = str(snapshot.get(hidden_key, "")).strip()\n        if hidden_value:\n            return hidden_value\n        return str(snapshot.get(current_key, "")).strip()\n\n    def _requested_scorebook_context_differs(\n        self,\n        snapshot: Dict[str, object],\n        *,\n        grade_id: str = "",\n        class_id: str = "",\n        subject_id: str = "",\n        term_id: str = "",\n    ) -> bool:\n        """Returns whether one requested scorebook context needs live combo synchronization."""\n        requested_specs = (\n            (grade_id, "currentGradeId", "hiddenGradeId"),\n            (class_id, "currentClassId", "hiddenClassId"),\n            (subject_id, "currentSubjectId", "hiddenSubjectId"),\n            (term_id, "currentTermId", "hiddenTermId"),\n        )\n        for requested_id, current_key, hidden_key in requested_specs:\n            normalized_requested_id = requested_id.strip()\n            if not normalized_requested_id:\n                continue\n            if normalized_requested_id != self._effective_snapshot_selected_id(snapshot, current_key, hidden_key):\n                return True\n        return False\n\n    def _build_options(self, items: List[Dict[str, object]]) -> List[ScoreOption]:\n        """Converts raw ExtJS store items into typed options."""\n        options: List[ScoreOption] = []\n        seen_ids = set()\n        for item in items:\n            option_id = str(item.get("id", "")).strip()\n            option_text = str(item.get("ten", "")).strip() or option_id\n            if not option_id or option_id in seen_ids:\n                continue\n            seen_ids.add(option_id)\n            options.append(ScoreOption(option_id=option_id, option_text=option_text))\n        return options\n\n    def _build_access_entries(self, items: List[Dict[str, object]]) -> List[ScorebookAccessEntry]:\n        """Converts raw permission scan items into typed access entries."""\n        entries: List[ScorebookAccessEntry] = []\n        seen_keys = set()\n        for item in items:\n            class_id = str(item.get("classId", "")).strip()\n            subject_id = str(item.get("subjectId", "")).strip()\n            if not class_id or not subject_id:\n                continue\n            key = (class_id, subject_id)\n            if key in seen_keys:\n                continue\n            seen_keys.add(key)\n            entries.append(\n                ScorebookAccessEntry(\n                    grade_id=str(item.get("gradeId", "")).strip(),\n                    grade_text=str(item.get("gradeText", "")).strip(),\n                    class_id=class_id,\n                    class_text=str(item.get("classText", "")).strip(),\n                    subject_id=subject_id,\n                    subject_text=str(item.get("subjectText", "")).strip(),\n                    term_id=str(item.get("termId", "")).strip(),\n                    term_text=str(item.get("termText", "")).strip(),\n                    teacher_text=str(item.get("teacherText", "")).strip(),\n                    permission_text=str(item.get("permissionText", "")).strip(),\n                    comment_input_count=int(item.get("commentInputCount", 0) or 0),\n                    enabled_comment_input_count=int(item.get("enabledCommentInputCount", 0) or 0),\n                )\n            )\n        return entries\n\n    def _finalize_schema_identity(self, schemas: List[ScoreColumnSchema]) -> List[ScoreColumnSchema]:\n        """Ensures repeated schema names and keys remain unique and stable."""\n        name_counts: Dict[str, int] = {}\n        name_totals: Dict[str, int] = {}\n        key_counts: Dict[str, int] = {}\n        key_totals: Dict[str, int] = {}\n        for schema in schemas:\n            base_name = schema.display_name.strip() or f"Cột {schema.leaf_index}"\n            name_totals[base_name] = name_totals.get(base_name, 0) + 1\n            base_key = schema.column_key.strip() or f"col_{schema.leaf_index}"\n            key_totals[base_key] = key_totals.get(base_key, 0) + 1\n\n        updated: List[ScoreColumnSchema] = []\n        for schema in schemas:\n            base_name = schema.display_name.strip() or f"Cột {schema.leaf_index}"\n            name_counts[base_name] = name_counts.get(base_name, 0) + 1\n            if name_totals.get(base_name, 0) > 1:\n                schema.display_name = f"{base_name} ({name_counts[base_name]})"\n            else:\n                schema.display_name = base_name\n\n            base_key = schema.column_key.strip() or f"col_{schema.leaf_index}"\n            key_counts[base_key] = key_counts.get(base_key, 0) + 1\n            if key_totals.get(base_key, 0) > 1:\n                schema.column_key = f"{base_key}_{key_counts[base_key]}"\n            else:\n                schema.column_key = base_key\n            updated.append(schema)\n        return updated\n\n    def _scorebook_leaf_count(self, body_rows: List[Dict[str, object]]) -> int:\n        """Returns the maximum number of visible leaf cells across sampled score rows."""\n        return max((len(list(row.get("cells", []))) for row in body_rows), default=0)\n\n    def _build_scorebook_header_grids(\n        self,\n        header_rows: List[Dict[str, object]],\n        leaf_count: int,\n    ) -> Tuple[List[List[str]], List[List[Dict[str, object] | None]]]:\n        """Expands scorebook header rowspans/colspans into one leaf-aligned grid."""\n        header_grid: List[List[str]] = [["" for _ in range(leaf_count)] for _ in range(len(header_rows))]\n        header_meta_grid: List[List[Dict[str, object] | None]] = [\n            [None for _ in range(leaf_count)] for _ in range(len(header_rows))\n        ]\n\n        for row_index, row in enumerate(header_rows):\n            col_pos = 0\n            for cell in list(row.get("cells", [])):\n                while col_pos < leaf_count and header_meta_grid[row_index][col_pos] is not None:\n                    col_pos += 1\n                colspan = max(1, int(cell.get("colspan", 1) or 1))\n                rowspan = max(1, int(cell.get("rowspan", 1) or 1))\n                label = (\n                    str(cell.get("text", "")).strip()\n                    or str(cell.get("cn", "")).strip()\n                    or str(cell.get("cl", "")).strip()\n                )\n                for row_offset in range(rowspan):\n                    target_row = row_index + row_offset\n                    if target_row >= len(header_rows):\n                        break\n                    for col_offset in range(colspan):\n                        target_col = col_pos + col_offset\n                        if target_col >= leaf_count:\n                            break\n                        header_grid[target_row][target_col] = label\n                        header_meta_grid[target_row][target_col] = dict(cell)\n                col_pos += colspan\n\n        return header_grid, header_meta_grid\n\n    def _sample_scorebook_leaf_cells(\n        self,\n        body_rows: List[Dict[str, object]],\n        leaf_index: int,\n    ) -> List[Dict[str, object]]:\n        """Returns the sampled body cells for one leaf column."""\n        return [\n            row["cells"][leaf_index]\n            for row in body_rows\n            if leaf_index < len(list(row.get("cells", [])))\n        ]\n\n    def _deduped_scorebook_header_path(\n        self,\n        header_grid: List[List[str]],\n        leaf_index: int,\n    ) -> Tuple[str, ...]:\n        """Builds the de-duplicated header label path for one leaf column."""\n        header_path = tuple(\n            label\n            for label in (\n                header_grid[row_index][leaf_index].strip()\n                for row_index in range(len(header_grid))\n            )\n            if label\n        )\n        deduped_header_path: List[str] = []\n        for label in header_path:\n            if not deduped_header_path or deduped_header_path[-1] != label:\n                deduped_header_path.append(label)\n        return tuple(deduped_header_path)\n\n    def _deepest_scorebook_header_meta(\n        self,\n        header_meta_grid: List[List[Dict[str, object] | None]],\n        leaf_index: int,\n    ) -> Dict[str, object]:\n        """Returns the deepest non-empty header metadata cell for one leaf column."""\n        return next(\n            (\n                header_meta_grid[row_index][leaf_index]\n                for row_index in range(len(header_meta_grid) - 1, -1, -1)\n                if header_meta_grid[row_index][leaf_index] is not None\n            ),\n            None,\n        ) or {}\n\n    def _infer_scorebook_column_identity(\n        self,\n        *,\n        input_class: str,\n        td_class: str,\n        normalized_header: str,\n        data_column: str,\n        cn_value: str,\n        block_index: str,\n        child_index: str,\n        header_label: str,\n        leaf_index: int,\n    ) -> Tuple[str, str, str]:\n        """Infers the semantic role, input kind, and stable key for one scorebook column."""\n        role_hint = "static"\n        input_kind = ""\n        column_key = f"col_{leaf_index}"\n\n        if "input_nhan_xet" in input_class:\n            return "comment", "comment", "comment"\n        if "input_diem_tbm" in input_class or "tbhk_tt" in td_class.lower():\n            return "average", "score", "average_term"\n        if "input_diem" in input_class or "txtdiem" in input_class.lower():\n            score_suffix = data_column or cn_value or f"{block_index}_{child_index}"\n            return "score", "score", f"score_{score_suffix}".strip("_")\n        if any(\n            token in normalized_header\n            for token in (\n                "đtbmhk",\n                "tbhk",\n                "tbhk 1",\n                "tbhk 2",\n                "tb cả năm",\n                "tb ca nam",\n                "trung bình học kỳ",\n                "trung binh hoc ky",\n                "trung bình cả năm",\n                "trung binh ca nam",\n            )\n        ):\n            static_average_key = data_column or cn_value or header_label or f"average_{leaf_index}"\n            return (\n                "average",\n                "score",\n                f"average_{re.sub(r\'[^a-z0-9]+\', \'_\', static_average_key.lower()).strip(\'_\') or leaf_index}",\n            )\n        if any(\n            token in normalized_header\n            for token in (\n                "điểm thi lại",\n                "diem thi lai",\n                "thi lại",\n                "thi lai",\n            )\n        ):\n            static_score_key = data_column or cn_value or header_label or f"score_{leaf_index}"\n            return (\n                "score",\n                "score",\n                f"score_{re.sub(r\'[^a-z0-9]+\', \'_\', static_score_key.lower()).strip(\'_\') or leaf_index}",\n            )\n        if "mã hs" in normalized_header:\n            return "student_code", input_kind, "student_code"\n        if "họ và tên" in normalized_header:\n            return "student_name", input_kind, "student_name"\n        if "ngày sinh" in normalized_header:\n            return "birth_date", input_kind, "birth_date"\n        if "liên lạc" in normalized_header:\n            return "contact", input_kind, "contact"\n        if "stt" in normalized_header:\n            return "ordinal", input_kind, "ordinal"\n        return role_hint, input_kind, column_key\n\n    def _scorebook_column_display_name(\n        self,\n        role_hint: str,\n        data_column: str,\n        header_label: str,\n        cn_value: str,\n    ) -> str:\n        """Resolves the UI display name for one inferred scorebook schema column."""\n        if role_hint == "score" and cn_value and data_column:\n            return f"{cn_value} / {data_column}"\n        if role_hint == "score" and cn_value:\n            return cn_value\n        return data_column or header_label\n\n    def _build_scorebook_schema_for_leaf(\n        self,\n        body_rows: List[Dict[str, object]],\n        header_grid: List[List[str]],\n        header_meta_grid: List[List[Dict[str, object] | None]],\n        leaf_index: int,\n    ) -> ScoreColumnSchema:\n        """Builds one typed schema object for a single leaf column."""\n        sample_cells = self._sample_scorebook_leaf_cells(body_rows, leaf_index)\n        first_nonempty_cell = next(\n            (\n                cell\n                for cell in sample_cells\n                if str(cell.get("text", "")).strip() or cell.get("inputInfo")\n            ),\n            sample_cells[0] if sample_cells else {},\n        )\n        input_info = next((cell.get("inputInfo") for cell in sample_cells if cell.get("inputInfo")), None) or {}\n        header_path = self._deduped_scorebook_header_path(header_grid, leaf_index)\n        deepest_meta = self._deepest_scorebook_header_meta(header_meta_grid, leaf_index)\n\n        td_class = str(first_nonempty_cell.get("className", "")).strip()\n        input_class = str(input_info.get("className", "")).strip()\n        header_label = header_path[-1] if header_path else f"Cột {leaf_index}"\n        sample_value = str(input_info.get("value", "")).strip() or str(first_nonempty_cell.get("text", "")).strip()\n        block_index = str(input_info.get("b", "")).strip() or str(deepest_meta.get("b", "")).strip()\n        child_index = str(input_info.get("c", "")).strip() or str(deepest_meta.get("c", "")).strip()\n        data_column = str(first_nonempty_cell.get("dataCot", "")).strip() or str(deepest_meta.get("cl", "")).strip()\n        cn_value = str(deepest_meta.get("cn", "")).strip()\n        normalized_header = " ".join(header_path).lower()\n        editable = any(bool(cell.get("inputInfo")) for cell in sample_cells)\n        role_hint, input_kind, column_key = self._infer_scorebook_column_identity(\n            input_class=input_class,\n            td_class=td_class,\n            normalized_header=normalized_header,\n            data_column=data_column,\n            cn_value=cn_value,\n            block_index=block_index,\n            child_index=child_index,\n            header_label=header_label,\n            leaf_index=leaf_index,\n        )\n        display_name = self._scorebook_column_display_name(\n            role_hint,\n            data_column=data_column,\n            header_label=header_label,\n            cn_value=cn_value,\n        )\n\n        return ScoreColumnSchema(\n            column_key=column_key,\n            header_path=header_path,\n            leaf_index=leaf_index,\n            display_name=display_name,\n            editable=editable,\n            role_hint=role_hint,\n            input_kind=input_kind,\n            sample_value=sample_value,\n            block_index=block_index,\n            child_index=child_index,\n            data_column=data_column,\n            input_name=str(input_info.get("name", "")).strip(),\n        )\n\n    def _extract_scorebook_schema_from_snapshot(self, snapshot: Dict[str, object]) -> List[ScoreColumnSchema]:\n        """Builds leaf-column schema objects from one raw table snapshot."""\n        header_rows = list(snapshot.get("scoreTableHeaderRows", []))\n        body_rows = list(snapshot.get("scoreTableBodyRows", []))\n        if not header_rows or not body_rows:\n            return []\n\n        leaf_count = self._scorebook_leaf_count(body_rows)\n        if leaf_count <= 0:\n            return []\n\n        header_grid, header_meta_grid = self._build_scorebook_header_grids(header_rows, leaf_count)\n        schemas = [\n            self._build_scorebook_schema_for_leaf(\n                body_rows,\n                header_grid,\n                header_meta_grid,\n                leaf_index,\n            )\n            for leaf_index in range(leaf_count)\n        ]\n\n        return self._finalize_schema_identity(schemas)\n\n    def _find_schema_by_key(\n        self, schemas: List[ScoreColumnSchema], column_key: str\n    ) -> ScoreColumnSchema | None:\n        """Finds one parsed schema by its stable key."""\n        column_key = column_key.strip()\n        if not column_key:\n            return None\n        return next((schema for schema in schemas if schema.column_key == column_key), None)\n\n    def _resolve_score_schema(\n        self,\n        schemas: List[ScoreColumnSchema],\n        *,\n        target_column_key: str = "",\n        target_column_label: str = "",\n    ) -> ScoreColumnSchema | None:\n        """Resolves one score schema across contexts where the UI label is stable but the internal key may change."""\n        schema = self._find_schema_by_key(schemas, target_column_key)\n        if schema is not None:\n            return schema\n\n        normalized_label = str(target_column_label or "").strip()\n        if not normalized_label:\n            return None\n\n        label_candidates = [normalized_label]\n        suffix_match = re.match(r"^(.*?)\\s+\\([^)]+\\)$", normalized_label)\n        if suffix_match:\n            base_label = suffix_match.group(1).strip()\n            if base_label and base_label not in label_candidates:\n                label_candidates.append(base_label)\n\n        for label_candidate in label_candidates:\n            matched_schema = next(\n                (\n                    item\n                    for item in schemas\n                    if (item.display_name.strip() or item.column_key) == label_candidate\n                ),\n                None,\n            )\n            if matched_schema is not None:\n                return matched_schema\n        return None\n\n    def _detect_scorebook_columns(self, schemas: List[ScoreColumnSchema]) -> ScorebookDetectedColumns:\n        """Chooses comment/score columns from the parsed schema using deterministic heuristics."""\n        editable_comment_columns = [\n            schema for schema in schemas if schema.editable and schema.role_hint == "comment"\n        ]\n        average_columns = [\n            schema for schema in schemas if schema.role_hint == "average"\n        ]\n        score_columns = [\n            schema for schema in schemas if schema.role_hint == "score"\n        ]\n        editable_average_columns = [schema for schema in average_columns if schema.editable]\n        editable_score_columns = [schema for schema in score_columns if schema.editable]\n        all_score_candidates = sorted(\n            [*average_columns, *score_columns],\n            key=lambda schema: schema.leaf_index,\n        )\n\n        preferred_score_column_key = ""\n        preferred_score_reason = ""\n\n        average_with_data = [schema for schema in average_columns if schema.sample_value.strip()]\n        score_with_data = [schema for schema in score_columns if schema.sample_value.strip()]\n        if average_with_data:\n            preferred_schema = max(average_with_data, key=lambda schema: schema.leaf_index)\n            preferred_score_column_key = preferred_schema.column_key\n            preferred_score_reason = "Ưu tiên cột trung bình có dữ liệu."\n        elif score_with_data:\n            preferred_schema = max(score_with_data, key=lambda schema: schema.leaf_index)\n            preferred_score_column_key = preferred_schema.column_key\n            preferred_score_reason = "Ưu tiên cột điểm ngoài cùng bên phải hiện đã có dữ liệu."\n        elif average_columns:\n            preferred_schema = max(average_columns, key=lambda schema: schema.leaf_index)\n            preferred_score_column_key = preferred_schema.column_key\n            preferred_score_reason = "Fallback sang cột trung bình ngoài cùng bên phải."\n        elif score_columns:\n            preferred_schema = max(score_columns, key=lambda schema: schema.leaf_index)\n            preferred_score_column_key = preferred_schema.column_key\n            preferred_score_reason = "Fallback sang cột điểm ngoài cùng bên phải."\n\n        preferred_comment_column_key = ""\n        if editable_comment_columns:\n            preferred_comment_column_key = min(\n                editable_comment_columns,\n                key=lambda schema: schema.leaf_index,\n            ).column_key\n\n        return ScorebookDetectedColumns(\n            preferred_score_column_key=preferred_score_column_key,\n            preferred_score_reason=preferred_score_reason,\n            preferred_comment_column_key=preferred_comment_column_key,\n            score_candidate_keys=[schema.column_key for schema in all_score_candidates],\n            average_candidate_keys=[schema.column_key for schema in average_columns],\n            comment_candidate_keys=[schema.column_key for schema in editable_comment_columns],\n        )\n\n    def _extract_live_write_rows(\n        self,\n        page: Page,\n        context: ScorebookContext,\n        source_column_key: str,\n        comment_column_key: str,\n    ) -> List[Dict[str, object]]:\n        """Extracts all visible student rows for internal analysis/write from the active scorebook table."""\n        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)\n        comment_schema = self._find_schema_by_key(context.column_schemas, comment_column_key)\n        if source_schema is None:\n            raise RuntimeError(f"Không tìm thấy schema cho cột điểm `{source_column_key}`.")\n        if comment_schema is None:\n            raise RuntimeError(f"Không tìm thấy schema cho cột nhận xét `{comment_column_key}`.")\n\n        student_name_indices = [\n            schema.leaf_index\n            for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index)\n            if schema.role_hint == "student_name"\n        ]\n        student_code_schema = next(\n            (schema for schema in context.column_schemas if schema.role_hint == "student_code"),\n            None,\n        )\n        student_code_index = student_code_schema.leaf_index if student_code_schema is not None else 1\n\n        return list(\n            page.evaluate(\n                """({ sourceIndex, commentIndex, studentNameIndices, studentCodeIndex }) => {\n                const wins = Array.from(document.querySelectorAll(\'.x-window\')).filter(win => /sổ điểm/i.test(win.innerText || \'\'));\n                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\'));\n                const root = active || wins[wins.length - 1];\n                if (!root) return [];\n                const table = root.querySelector(\'table.table.tablefix\');\n                if (!table) return [];\n\n                const normalizeText = (value) => String(value || \'\').replace(/\\\\s+/g, \' \').trim();\n                const cellValue = (cell) => {\n                    if (!cell) return \'\';\n                    const input = cell.querySelector(\'input, textarea, select, span.input_diem, span.input_nhan_xet\');\n                    if (input) {\n                        const tagName = String(input.tagName || \'\').toUpperCase();\n                        if (tagName === \'SPAN\') return normalizeText(input.textContent || input.innerText || \'\');\n                        return normalizeText(input.value || \'\');\n                    }\n                    return normalizeText(cell.innerText || cell.textContent || \'\');\n                };\n\n                return Array.from(table.querySelectorAll(\'tbody tr\')).map((tr, rowIndex) => {\n                    const cells = Array.from(tr.children);\n                    const scoreCell = cells[sourceIndex];\n                    const commentCell = cells[commentIndex];\n                    const commentInput = commentCell ? commentCell.querySelector(\'input, textarea, select, span.input_nhan_xet, span.input_diem\') : null;\n                    const nameParts = studentNameIndices\n                        .map(index => normalizeText(cells[index]?.innerText || cells[index]?.textContent || \'\'))\n                        .filter(Boolean);\n                    let studentName = nameParts.join(\' \');\n                    if (!studentName) {\n                        const fallback = cells\n                            .slice(0, 6)\n                            .map(cell => normalizeText(cell.innerText || cell.textContent || \'\'))\n                            .find(text => text && !/^\\\\d+$/.test(text) && !/\\\\d{2}\\\\/\\\\d{2}\\\\/\\\\d{4}/.test(text));\n                        studentName = fallback || \'\';\n                    }\n                    return {\n                        rowIndex: rowIndex + 1,\n                        rowId: tr.id || \'\',\n                        studentCode: normalizeText(cells[studentCodeIndex]?.innerText || cells[studentCodeIndex]?.textContent || \'\'),\n                        studentName,\n                        sourceValue: cellValue(scoreCell),\n                        currentComment: cellValue(commentCell),\n                        commentInputName: commentInput ? (commentInput.name || commentInput.id || \'\') : \'\',\n                    };\n                });\n            }""",\n                {\n                    "sourceIndex": source_schema.leaf_index,\n                    "commentIndex": comment_schema.leaf_index,\n                    "studentNameIndices": student_name_indices,\n                    "studentCodeIndex": student_code_index,\n                },\n            )\n        )\n\n    def _extract_live_score_entries(\n        self,\n        page: Page,\n        context: ScorebookContext,\n        target_column_key: str,\n        target_column_label: str = "",\n    ) -> List[ScoreWriteEntry]:\n        """Extracts all visible student rows for one target score column from the active scorebook table."""\n        target_schema = self._resolve_score_schema(\n            context.column_schemas,\n            target_column_key=target_column_key,\n            target_column_label=target_column_label,\n        )\n        if target_schema is None:\n            requested_target = target_column_label.strip() or target_column_key\n            raise RuntimeError(f"Không tìm thấy schema cho cột điểm `{requested_target}`.")\n\n        student_name_indices = [\n            schema.leaf_index\n            for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index)\n            if schema.role_hint == "student_name"\n        ]\n        student_code_schema = next(\n            (schema for schema in context.column_schemas if schema.role_hint == "student_code"),\n            None,\n        )\n        student_code_index = student_code_schema.leaf_index if student_code_schema is not None else 1\n\n        live_rows = list(\n            page.evaluate(\n                """({ targetIndex, studentNameIndices, studentCodeIndex }) => {\n                const wins = Array.from(document.querySelectorAll(\'.x-window\')).filter(win => /sổ điểm/i.test(win.innerText || \'\'));\n                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\'));\n                const root = active || wins[wins.length - 1];\n                if (!root) return [];\n                const table = root.querySelector(\'table.table.tablefix\');\n                if (!table) return [];\n\n                const normalizeText = (value) => String(value || \'\').replace(/\\\\s+/g, \' \').trim();\n                const cellValue = (cell) => {\n                    if (!cell) return \'\';\n                    const input = cell.querySelector(\'input, textarea, select, span.input_diem, span.input_nhan_xet\');\n                    if (input) {\n                        const tagName = String(input.tagName || \'\').toUpperCase();\n                        if (tagName === \'SPAN\') return normalizeText(input.textContent || input.innerText || \'\');\n                        return normalizeText(input.value || \'\');\n                    }\n                    return normalizeText(cell.innerText || cell.textContent || \'\');\n                };\n\n                return Array.from(table.querySelectorAll(\'tbody tr\')).map((tr, rowIndex) => {\n                    const cells = Array.from(tr.children);\n                    const targetCell = cells[targetIndex];\n                    const targetInput = targetCell ? targetCell.querySelector(\'input, textarea, select, span.input_diem, span.input_nhan_xet\') : null;\n                    const nameParts = studentNameIndices\n                        .map(index => normalizeText(cells[index]?.innerText || cells[index]?.textContent || \'\'))\n                        .filter(Boolean);\n                    let studentName = nameParts.join(\' \');\n                    if (!studentName) {\n                        const fallback = cells\n                            .slice(0, 6)\n                            .map(cell => normalizeText(cell.innerText || cell.textContent || \'\'))\n                            .find(text => text && !/^\\\\d+$/.test(text) && !/\\\\d{2}\\\\/\\\\d{2}\\\\/\\\\d{4}/.test(text));\n                        studentName = fallback || \'\';\n                    }\n                    return {\n                        rowIndex: rowIndex + 1,\n                        rowId: tr.id || \'\',\n                        studentCode: normalizeText(cells[studentCodeIndex]?.innerText || cells[studentCodeIndex]?.textContent || \'\'),\n                        studentName,\n                        currentScore: cellValue(targetCell),\n                        targetInputName: targetInput ? (targetInput.name || targetInput.id || \'\') : \'\',\n                    };\n                });\n            }""",\n                {\n                    "targetIndex": target_schema.leaf_index,\n                    "studentNameIndices": student_name_indices,\n                    "studentCodeIndex": student_code_index,\n                },\n            )\n        )\n\n        resolved_column_key = target_schema.column_key.strip() or target_column_key\n        target_column_name = target_schema.display_name if target_schema.display_name.strip() else resolved_column_key\n        score_entries: List[ScoreWriteEntry] = []\n        for live_row in live_rows:\n            target_input_name = str(live_row.get("targetInputName", "")).strip()\n            score_entries.append(\n                ScoreWriteEntry(\n                    row_index=int(live_row.get("rowIndex", 0) or 0),\n                    row_id=str(live_row.get("rowId", "")).strip(),\n                    student_code=str(live_row.get("studentCode", "")).strip(),\n                    student_name=str(live_row.get("studentName", "")).strip(),\n                    target_column_key=resolved_column_key,\n                    target_column_name=target_column_name,\n                    current_score=str(live_row.get("currentScore", "")).strip(),\n                    target_input_name=target_input_name,\n                    status=("scanned" if target_input_name else "locked"),\n                    reason=("" if target_input_name else "Ô điểm hiện không cho chỉnh sửa trên giao diện live."),\n                )\n            )\n        return score_entries\n\n    def _scan_score_entries_on_page(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        target_column_key: str,\n        target_column_label: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, List[ScoreWriteEntry]]:\n        """Builds one score-entry preview list from the already-selected live scorebook page."""\n        emit_progress(progress_callback, 12.0, "Đang phân tích cấu trúc Sổ điểm cho cột điểm...")\n        context = self._build_scorebook_context(snapshot)\n        target_schema = self._resolve_score_schema(\n            context.column_schemas,\n            target_column_key=target_column_key,\n            target_column_label=target_column_label,\n        )\n        if target_schema is None:\n            requested_target = target_column_label.strip() or target_column_key\n            raise RuntimeError(f"Không tìm thấy cột điểm `{requested_target}` trong ngữ cảnh hiện tại.")\n        emit_progress(\n            progress_callback,\n            38.0,\n            f"Đang quét học sinh cho cột {target_schema.display_name or target_schema.column_key or target_column_key}...",\n        )\n        entries = self._extract_live_score_entries(\n            page,\n            context,\n            target_column_key=target_schema.column_key,\n            target_column_label=target_column_label,\n        )\n        editable_count = sum(1 for entry in entries if entry.target_input_name.strip())\n        emit_progress(\n            progress_callback,\n            100.0,\n            f"Đã quét {len(entries)} học sinh, {editable_count} ô điểm khả dụng.",\n        )\n        return context, entries\n\n    def scan_score_entries(\n        self,\n        grade_id: str,\n        class_id: str,\n        subject_id: str,\n        term_id: str,\n        target_column_key: str,\n        target_column_label: str = "",\n        username: str = "",\n        password: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, List[ScoreWriteEntry], str, str]:\n        """Scans the live scorebook and returns student rows for one target score column."""\n        with self._open_page() as page:\n            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(\n                page,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n                username=username,\n                password=password,\n                progress_callback=create_subprogress_reporter(progress_callback, 0.0, 62.0),\n            )\n            context, entries = self._scan_score_entries_on_page(\n                page,\n                snapshot,\n                target_column_key=target_column_key,\n                target_column_label=target_column_label,\n                progress_callback=create_subprogress_reporter(progress_callback, 62.0, 100.0),\n            )\n            return context, entries, login_message, selection_message\n\n    def build_comment_write_queue(\n        self,\n        grade_id: str,\n        class_id: str,\n        subject_id: str,\n        term_id: str,\n        source_column_key: str,\n        comment_column_key: str,\n        rules: List[CommentRule],\n        username: str = "",\n        password: str = "",\n        allow_overwrite_existing_comment: bool = False,\n    ) -> Tuple[ScorebookContext, List[CommentWriteRow], str, str]:\n        """Builds the internal write queue that would be written by the current rule set."""\n        compiled_rules = compile_comment_rules(rules)\n        if not compiled_rules:\n            raise RuntimeError("Chưa có rule hợp lệ để phân tích dữ liệu ghi nhận xét.")\n\n        with self._open_page() as page:\n            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(\n                page,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n                username=username,\n                password=password,\n            )\n            context, write_rows = self._build_comment_write_queue_on_page(\n                page,\n                snapshot,\n                source_column_key=source_column_key,\n                comment_column_key=comment_column_key,\n                compiled_rules=compiled_rules,\n                allow_overwrite_existing_comment=allow_overwrite_existing_comment,\n            )\n            return context, write_rows, login_message, selection_message\n\n    def _open_selected_scorebook_snapshot_on_page(\n        self,\n        page: Page,\n        grade_id: str,\n        class_id: str,\n        subject_id: str,\n        term_id: str,\n        username: str = "",\n        password: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[Dict[str, object], str, str]:\n        """Logs in if needed, opens the scorebook screen, and applies one target context on the current page."""\n        emit_progress(progress_callback, 5.0, "Đang chuẩn bị phiên VNEDU để đọc dữ liệu...")\n        login_message = self._login_if_needed_on_page(\n            page,\n            username=username,\n            password=password,\n            progress_callback=create_subprogress_reporter(progress_callback, 5.0, 28.0),\n        )\n        self._ensure_scorebook_screen(\n            page,\n            progress_callback=create_subprogress_reporter(progress_callback, 28.0, 42.0),\n        )\n        snapshot = self._wait_for_scorebook_snapshot(\n            page,\n            timeout_sec=8.0,\n            progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),\n            progress_message="Đang đọc dữ liệu khung Sổ điểm...",\n        )\n        if (\n            not self._requested_scorebook_context_differs(\n                snapshot,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n            )\n            and self._scorebook_snapshot_ready_for_live_score_work(snapshot)\n        ):\n            emit_progress(progress_callback, 100.0, "Đang dùng lại đúng ngữ cảnh Sổ điểm hiện tại.")\n            return snapshot, login_message, ""\n        snapshot = self._wait_for_scorebook_permission_snapshot(\n            page,\n            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,\n            timeout_sec=6.0,\n            progress_callback=create_subprogress_reporter(progress_callback, 58.0, 70.0),\n            progress_message="Đang đọc quyền và thông tin giáo viên...",\n        )\n        snapshot, selection_message = self._select_scorebook_context_on_page(\n            page,\n            snapshot,\n            grade_id=grade_id,\n            class_id=class_id,\n            subject_id=subject_id,\n            term_id=term_id,\n            progress_callback=create_subprogress_reporter(progress_callback, 70.0, 100.0),\n        )\n        return snapshot, login_message, selection_message\n\n    def _scorebook_snapshot_ready_for_live_score_work(self, snapshot: Dict[str, object]) -> bool:\n        """Checks whether the current scorebook snapshot is already usable for score scan/apply."""\n        return bool(\n            str(snapshot.get("windowId", "")).strip()\n            and int(snapshot.get("scoreTableRowCount", 0) or 0) > 0\n            and list(snapshot.get("scoreTableHeaderRows", []))\n            and list(snapshot.get("scoreTableBodyRows", []))\n        )\n\n    def _build_comment_write_queue_on_page(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        source_column_key: str,\n        comment_column_key: str,\n        compiled_rules: List[Tuple[Callable[[object], bool], str, str]],\n        allow_overwrite_existing_comment: bool = False,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, List[CommentWriteRow]]:\n        """Builds one comment write queue from the already-selected live scorebook page."""\n        emit_progress(progress_callback, 10.0, "Đang phân tích cấu trúc Sổ điểm...")\n        context = self._build_scorebook_context(snapshot)\n        emit_progress(progress_callback, 35.0, "Đang đọc dữ liệu từng học sinh từ live Chrome...")\n        live_rows = self._extract_live_write_rows(\n            page,\n            context,\n            source_column_key=source_column_key,\n            comment_column_key=comment_column_key,\n        )\n        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)\n        write_rows = build_comment_write_rows_from_live_data(\n            live_rows,\n            source_column_key=source_column_key,\n            source_column_name=source_schema.display_name if source_schema is not None else source_column_key,\n            compiled_rules=compiled_rules,\n            allow_overwrite_existing_comment=allow_overwrite_existing_comment,\n        )\n        emit_progress(progress_callback, 100.0, "Đã phân tích xong hàng chờ ghi nhận xét.")\n        return context, write_rows\n\n    def _apply_payload_to_active_scorebook(\n        self,\n        page: Page,\n        payload: List[Dict[str, str]],\n        auto_save: bool,\n        progress_callback: ProgressCallback | None = None,\n        field_label: str = "dữ liệu",\n        item_label: str = "dòng",\n    ) -> Dict[str, object]:\n        """Writes one prepared payload into the active scorebook window in batches and optionally clicks Save."""\n        if not payload:\n            return {"updated": [], "failed": [], "saveClicked": False}\n\n        batch_size = 1 if len(payload) <= 12 else (3 if len(payload) <= 30 else 5)\n        updated_input_names: List[str] = []\n        failed_input_names: List[str] = []\n\n        emit_progress(progress_callback, 5.0, f"Đang ghi {field_label}...")\n        for batch_start in range(0, len(payload), batch_size):\n            batch_rows = payload[batch_start : batch_start + batch_size]\n            batch_result = dict(\n                page.evaluate(\n                    """({ rows, finalizeMode }) => {\n                    const wins = Array.from(document.querySelectorAll(\'.x-window\')).filter(win => /sổ điểm/i.test(win.innerText || \'\'));\n                    const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\'));\n                    const root = active || wins[wins.length - 1];\n                    if (!root) {\n                        return { updated: [], failed: rows.map(item => item.inputName), saveClicked: false };\n                    }\n\n                    const setValue = (input, value) => {\n                        if (String(input.tagName || \'\').toUpperCase() === \'SPAN\') {\n                            input.textContent = value;\n                            input.innerText = value;\n                            input.setAttribute(\'data-value\', value);\n                            input.dispatchEvent(new Event(\'input\', { bubbles: true }));\n                            input.dispatchEvent(new Event(\'change\', { bubbles: true }));\n                            return;\n                        }\n                        const proto = input.tagName === \'TEXTAREA\'\n                            ? window.HTMLTextAreaElement.prototype\n                            : window.HTMLInputElement.prototype;\n                        const nativeSetter = Object.getOwnPropertyDescriptor(proto, \'value\');\n                        if (nativeSetter && nativeSetter.set) {\n                            nativeSetter.set.call(input, value);\n                        } else {\n                            input.value = value;\n                        }\n                        input.dispatchEvent(new Event(\'input\', { bubbles: true }));\n                        input.dispatchEvent(new Event(\'change\', { bubbles: true }));\n                    };\n\n                    const updated = [];\n                    const failed = [];\n                    for (const row of rows) {\n                        const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], span[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;\n                        const input = root.querySelector(selector);\n                        if (!input) {\n                            failed.push(row.inputName);\n                            continue;\n                        }\n                        try {\n                            input.focus();\n                            setValue(input, row.text);\n                            updated.push(row.inputName);\n                        } catch (error) {\n                            failed.push(row.inputName);\n                        }\n                    }\n\n                    let saveClicked = false;\n                    if (finalizeMode === \'save\') {\n                        const saveButton = Array.from(root.querySelectorAll(\'button, span, a, div\'))\n                            .find(el => /^lưu$/i.test((el.innerText || \'\').trim()));\n                        if (saveButton) {\n                            saveButton.dispatchEvent(new MouseEvent(\'click\', { bubbles: true }));\n                            saveClicked = true;\n                        }\n                    } else if (finalizeMode === \'blur\') {\n                        const firstReadonlyCell = root.querySelector(\'table.table.tablefix tbody tr td\');\n                        if (firstReadonlyCell) {\n                            firstReadonlyCell.dispatchEvent(new MouseEvent(\'click\', { bubbles: true }));\n                        }\n                    }\n                    return { updated, failed, saveClicked };\n                }""",\n                    {"rows": batch_rows, "finalizeMode": "none"},\n                )\n            )\n            updated_input_names.extend(str(item).strip() for item in list(batch_result.get("updated", [])) if str(item).strip())\n            failed_input_names.extend(str(item).strip() for item in list(batch_result.get("failed", [])) if str(item).strip())\n            processed_rows = min(batch_start + len(batch_rows), len(payload))\n            emit_progress(\n                progress_callback,\n                10.0 + ((processed_rows / max(len(payload), 1)) * 78.0),\n                f"Đang ghi {item_label} {processed_rows}/{len(payload)}...",\n            )\n\n        emit_progress(\n            progress_callback,\n            92.0,\n            "Đang bấm Lưu dữ liệu..." if auto_save else f"Đang chốt {field_label}...",\n        )\n        finalize_result = dict(\n            page.evaluate(\n                """({ finalizeMode }) => {\n                const wins = Array.from(document.querySelectorAll(\'.x-window\')).filter(win => /sổ điểm/i.test(win.innerText || \'\'));\n                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\'));\n                const root = active || wins[wins.length - 1];\n                if (!root) {\n                    return { updated: [], failed: [], saveClicked: false };\n                }\n\n                let saveClicked = false;\n                if (finalizeMode === \'save\') {\n                    const saveButton = Array.from(root.querySelectorAll(\'button, span, a, div\'))\n                        .find(el => /^lưu$/i.test((el.innerText || \'\').trim()));\n                    if (saveButton) {\n                        saveButton.dispatchEvent(new MouseEvent(\'click\', { bubbles: true }));\n                        saveClicked = true;\n                    }\n                } else if (finalizeMode === \'blur\') {\n                    const firstReadonlyCell = root.querySelector(\'table.table.tablefix tbody tr td\');\n                    if (firstReadonlyCell) {\n                        firstReadonlyCell.dispatchEvent(new MouseEvent(\'click\', { bubbles: true }));\n                    }\n                }\n                return { updated: [], failed: [], saveClicked };\n            }""",\n                {"finalizeMode": ("save" if auto_save else "blur")},\n            )\n        )\n        emit_progress(progress_callback, 100.0, f"Đã điền xong {field_label}.")\n        return {\n            "updated": list(dict.fromkeys(updated_input_names)),\n            "failed": list(dict.fromkeys(failed_input_names)),\n            "saveClicked": bool(finalize_result.get("saveClicked")),\n        }\n\n    def _apply_comment_payload_to_active_scorebook(\n        self,\n        page: Page,\n        payload: List[Dict[str, str]],\n        auto_save: bool,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Dict[str, object]:\n        """Writes one prepared comment payload into the active scorebook window."""\n        return self._apply_payload_to_active_scorebook(\n            page,\n            payload,\n            auto_save=auto_save,\n            progress_callback=progress_callback,\n            field_label="dữ liệu vào các ô nhận xét",\n            item_label="nhận xét",\n        )\n\n    def _apply_score_payload_to_active_scorebook(\n        self,\n        page: Page,\n        payload: List[Dict[str, str]],\n        auto_save: bool,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Dict[str, object]:\n        """Writes one prepared score payload into the active scorebook window."""\n        return self._apply_payload_to_active_scorebook(\n            page,\n            payload,\n            auto_save=auto_save,\n            progress_callback=progress_callback,\n            field_label="điểm vào các ô mục tiêu",\n            item_label="điểm",\n        )\n\n    def _arm_scorebook_save_monitor(self, page: Page) -> int:\n        """Installs or resets one browser-side monitor that records save requests after the next click."""\n        return int(\n            page.evaluate(\n                """() => {\n                const previewText = (value) => {\n                    if (value == null) return \'\';\n                    try {\n                        if (typeof URLSearchParams !== \'undefined\' && value instanceof URLSearchParams) {\n                            return value.toString().slice(0, 1200);\n                        }\n                        if (typeof FormData !== \'undefined\' && value instanceof FormData) {\n                            const parts = [];\n                            value.forEach((entryValue, entryKey) => {\n                                parts.push(`${String(entryKey)}=${String(entryValue)}`);\n                            });\n                            return parts.join(\'&\').slice(0, 1200);\n                        }\n                        if (typeof value === \'string\') {\n                            return value.slice(0, 1200);\n                        }\n                        return String(value).slice(0, 1200);\n                    } catch (error) {\n                        return \'\';\n                    }\n                };\n\n                if (!window.__codexScorebookSaveMonitor) {\n                    const monitor = {\n                        nextRunId: 1,\n                        requests: [],\n                    };\n\n                    const originalOpen = XMLHttpRequest.prototype.open;\n                    const originalSend = XMLHttpRequest.prototype.send;\n                    XMLHttpRequest.prototype.open = function(method, url, ...rest) {\n                        this.__codexSaveMethod = method;\n                        this.__codexSaveUrl = url;\n                        return originalOpen.call(this, method, url, ...rest);\n                    };\n                    XMLHttpRequest.prototype.send = function(body) {\n                        const runId = monitor.activeRunId || 0;\n                        const request = {\n                            runId,\n                            transport: \'xhr\',\n                            method: previewText(this.__codexSaveMethod || \'GET\').toUpperCase(),\n                            url: previewText(this.__codexSaveUrl || \'\'),\n                            bodyPreview: previewText(body),\n                            startedAt: Date.now(),\n                            finished: false,\n                            status: 0,\n                            responsePreview: \'\',\n                        };\n                        monitor.requests.push(request);\n                        const finish = () => {\n                            if (request.finished) return;\n                            request.finished = true;\n                            request.status = Number(this.status || 0);\n                            request.finishedAt = Date.now();\n                            try {\n                                request.responsePreview = previewText(this.responseText || \'\');\n                            } catch (error) {\n                                request.responsePreview = \'\';\n                            }\n                        };\n                        this.addEventListener(\'loadend\', finish);\n                        this.addEventListener(\'error\', finish);\n                        this.addEventListener(\'abort\', finish);\n                        return originalSend.call(this, body);\n                    };\n\n                    if (typeof window.fetch === \'function\') {\n                        const originalFetch = window.fetch.bind(window);\n                        window.fetch = function(input, init) {\n                            const runId = monitor.activeRunId || 0;\n                            const request = {\n                                runId,\n                                transport: \'fetch\',\n                                method: previewText((init && init.method) || \'GET\').toUpperCase(),\n                                url: previewText(typeof input === \'string\' ? input : ((input && input.url) || \'\')),\n                                bodyPreview: previewText((init && init.body) || \'\'),\n                                startedAt: Date.now(),\n                                finished: false,\n                                status: 0,\n                                responsePreview: \'\',\n                            };\n                            monitor.requests.push(request);\n                            return originalFetch(input, init).then(\n                                async (response) => {\n                                    request.status = Number(response.status || 0);\n                                    request.finished = true;\n                                    request.finishedAt = Date.now();\n                                    try {\n                                        const clone = response.clone();\n                                        request.responsePreview = previewText(await clone.text());\n                                    } catch (error) {\n                                        request.responsePreview = \'\';\n                                    }\n                                    return response;\n                                },\n                                (error) => {\n                                    request.finished = true;\n                                    request.finishedAt = Date.now();\n                                    request.responsePreview = previewText(error && error.message);\n                                    throw error;\n                                }\n                            );\n                        };\n                    }\n\n                    window.__codexScorebookSaveMonitor = monitor;\n                }\n\n                const monitor = window.__codexScorebookSaveMonitor;\n                const runId = Number(monitor.nextRunId || 1);\n                monitor.nextRunId = runId + 1;\n                monitor.activeRunId = runId;\n                monitor.requests = monitor.requests.filter(item => Number(item.runId || 0) !== runId);\n                return runId;\n            }"""\n            )\n        )\n\n    def _read_scorebook_save_monitor_requests(self, page: Page, run_id: int) -> List[Dict[str, object]]:\n        """Returns browser-observed save requests for one armed save run id."""\n        return list(\n            page.evaluate(\n                """(runId) => {\n                const monitor = window.__codexScorebookSaveMonitor;\n                if (!monitor) return [];\n                return (monitor.requests || [])\n                    .filter(item => Number(item.runId || 0) === Number(runId || 0))\n                    .map(item => ({\n                        transport: String(item.transport || \'\'),\n                        method: String(item.method || \'\'),\n                        url: String(item.url || \'\'),\n                        bodyPreview: String(item.bodyPreview || \'\'),\n                        finished: Boolean(item.finished),\n                        status: Number(item.status || 0),\n                        responsePreview: String(item.responsePreview || \'\'),\n                    }));\n            }""",\n                run_id,\n            )\n        )\n\n    def _verify_scorebook_save_on_server(\n        self,\n        page: Page,\n        save_monitor_run_id: int,\n        auto_save: bool,\n        save_clicked: bool,\n        payload_input_names: List[str],\n        expected_score_pairs: List[Tuple[str, str]] | None = None,\n        timeout_sec: float = 8.0,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Dict[str, object]:\n        """Waits for server-side save requests and classifies the save outcome."""\n        if not auto_save or not save_clicked:\n            emit_progress(progress_callback, 100.0, "Không cần chờ xác minh lưu tự động.")\n            save_verified, verification_mode, verification_detail = evaluate_server_save_verification(\n                auto_save_requested=auto_save,\n                save_clicked=save_clicked,\n                save_requests=[],\n                request_markers=payload_input_names,\n                expected_score_pairs=expected_score_pairs,\n            )\n            return {\n                "saveVerified": save_verified,\n                "saveVerificationMode": verification_mode,\n                "saveVerificationDetail": verification_detail,\n            }\n\n        deadline = time.time() + timeout_sec\n        latest_requests: List[Dict[str, object]] = []\n        normalized_expected_pairs = [\n            (input_name.strip(), normalize_score_text(value))\n            for input_name, value in list(expected_score_pairs or [])\n            if input_name.strip() and normalize_score_text(value)\n        ]\n        while time.time() < deadline:\n            latest_requests = self._read_scorebook_save_monitor_requests(page, save_monitor_run_id)\n            relevant_requests = select_relevant_server_save_requests(\n                latest_requests,\n                request_markers=payload_input_names,\n            )\n            payload_pair_observed = not normalized_expected_pairs or any(\n                _request_blob_contains_expected_score_pair(\n                    " ".join(\n                        [\n                            str(request.get("url", "")).strip(),\n                            str(request.get("bodyPreview", "")).strip(),\n                            str(request.get("responsePreview", "")).strip(),\n                        ]\n                    ),\n                    input_name,\n                    value,\n                )\n                for request in latest_requests\n                for input_name, value in normalized_expected_pairs\n            )\n            if (\n                relevant_requests\n                and all(bool(request.get("finished")) for request in relevant_requests)\n                and payload_pair_observed\n            ):\n                emit_progress(progress_callback, 100.0, "Đã nhận phản hồi lưu dữ liệu từ server.")\n                break\n            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)\n            emit_progress(\n                progress_callback,\n                max(10.0, elapsed_ratio * 95.0),\n                "Đang chờ VNEDU phản hồi thao tác Lưu...",\n            )\n            page.wait_for_timeout(250)\n\n        save_verified, verification_mode, verification_detail = evaluate_server_save_verification(\n            auto_save_requested=auto_save,\n            save_clicked=save_clicked,\n            save_requests=latest_requests,\n            request_markers=payload_input_names,\n            expected_score_pairs=expected_score_pairs,\n        )\n        emit_progress(progress_callback, 100.0, "Đã hoàn tất xác minh lưu dữ liệu.")\n        return {\n            "saveVerified": save_verified,\n            "saveVerificationMode": verification_mode,\n            "saveVerificationDetail": verification_detail,\n        }\n\n    def _read_scorebook_payload_values(\n        self,\n        page: Page,\n        payload: List[Dict[str, str]],\n    ) -> Dict[str, object]:\n        """Reads the current DOM values for one prepared scorebook payload."""\n        return dict(\n            page.evaluate(\n                """(rows) => {\n                const wins = Array.from(document.querySelectorAll(\'.x-window\')).filter(win => /sổ điểm/i.test(win.innerText || \'\'));\n                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || \'\'));\n                const root = active || wins[wins.length - 1];\n                const values = {};\n                if (!root) return values;\n                for (const row of rows) {\n                    const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], span[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;\n                    const input = root.querySelector(selector);\n                    values[row.inputName] = input\n                        ? (\n                            String(input.tagName || \'\').toUpperCase() === \'SPAN\'\n                                ? String(input.textContent || input.innerText || \'\')\n                                : String(input.value || \'\')\n                        )\n                        : \'\';\n                }\n                return values;\n            }""",\n                payload,\n            )\n        )\n\n    def _wait_for_expected_scorebook_payload_values(\n        self,\n        page: Page,\n        payload: List[Dict[str, str]],\n        timeout_sec: float = 0.8,\n        poll_ms: int = 120,\n        progress_callback: ProgressCallback | None = None,\n        progress_message: str = "Đang chờ giao diện phản ánh điểm vừa ghi...",\n    ) -> Dict[str, object]:\n        """Polls live inputs until the DOM reflects the expected payload, or the deadline expires."""\n        timeout_window = max(timeout_sec, 0.1)\n        deadline = time.time() + timeout_window\n        latest_values: Dict[str, object] = {}\n        while True:\n            latest_values = self._read_scorebook_payload_values(page, payload)\n            if self._scorebook_payload_matches_expected(payload, latest_values):\n                emit_progress(progress_callback, 100.0, "Đã xác minh xong dữ liệu vừa ghi trên giao diện.")\n                return latest_values\n            if time.time() >= deadline:\n                emit_progress(progress_callback, 100.0, "Đã hết thời gian chờ phản ánh dữ liệu trên giao diện.")\n                return latest_values\n            elapsed_ratio = min((time.time() - (deadline - timeout_window)) / timeout_window, 1.0)\n            emit_progress(progress_callback, max(10.0, elapsed_ratio * 95.0), progress_message)\n            page.wait_for_timeout(max(int(poll_ms), 40))\n\n    def _scorebook_payload_matches_expected(\n        self,\n        payload: List[Dict[str, str]],\n        verification_map: Dict[str, object],\n    ) -> bool:\n        """Returns whether every payload input already exposes the expected value in the DOM."""\n        for item in payload:\n            input_name = str(item.get("inputName", "")).strip()\n            expected_value = str(item.get("text", "")).strip()\n            actual_value = str(verification_map.get(input_name, "")).strip()\n            if actual_value != expected_value:\n                return False\n        return True\n\n    def _read_comment_payload_values(\n        self,\n        page: Page,\n        payload: List[Dict[str, str]],\n    ) -> Dict[str, object]:\n        """Reads the current DOM values for one prepared comment payload."""\n        return self._read_scorebook_payload_values(page, payload)\n\n    def apply_comment_write_rows(\n        self,\n        grade_id: str,\n        class_id: str,\n        subject_id: str,\n        term_id: str,\n        write_rows: List[CommentWriteRow],\n        username: str = "",\n        password: str = "",\n        auto_save: bool = True,\n    ) -> Tuple[ScorebookContext, CommentWriteResult, str, str]:\n        """Writes analyzed rows back into the live scorebook and verifies the updated values."""\n        rows_to_apply = ready_comment_write_rows(write_rows)\n        if not rows_to_apply:\n            raise RuntimeError("Không có dòng nào trong hàng chờ ghi sẵn sàng để nhập nhận xét.")\n\n        with self._open_page() as page:\n            _snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(\n                page,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n                username=username,\n                password=password,\n            )\n            context, result = self._apply_comment_write_rows_on_page(\n                page,\n                write_rows,\n                auto_save=auto_save,\n            )\n            return context, result, login_message, selection_message\n\n    def _apply_comment_write_rows_on_page(\n        self,\n        page: Page,\n        write_rows: List[CommentWriteRow],\n        auto_save: bool = True,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, CommentWriteResult]:\n        """Writes one analyzed comment queue back into the already-selected live scorebook page."""\n        rows_to_apply = ready_comment_write_rows(write_rows)\n        if not rows_to_apply:\n            raise RuntimeError("Không có dòng nào trong hàng chờ ghi sẵn sàng để nhập nhận xét.")\n\n        emit_progress(progress_callback, 8.0, "Đang chuẩn bị dữ liệu để ghi lên cột nhận xét...")\n        payload = build_comment_write_payload(rows_to_apply)\n        emit_progress(progress_callback, 16.0, "Đang gắn bộ theo dõi thao tác Lưu...")\n        save_monitor_run_id = self._arm_scorebook_save_monitor(page)\n        result_payload = self._apply_comment_payload_to_active_scorebook(\n            page,\n            payload,\n            auto_save=auto_save,\n            progress_callback=create_subprogress_reporter(progress_callback, 16.0, 68.0),\n        )\n        result_payload.update(\n            self._verify_scorebook_save_on_server(\n                page,\n                save_monitor_run_id=save_monitor_run_id,\n                auto_save=auto_save,\n                save_clicked=bool(result_payload.get("saveClicked")),\n                payload_input_names=[str(item.get("inputName", "")).strip() for item in payload],\n                progress_callback=create_subprogress_reporter(progress_callback, 68.0, 84.0),\n            )\n        )\n        emit_progress(progress_callback, 88.0, "Đang xác minh lại dữ liệu vừa ghi trên giao diện...")\n        page.wait_for_timeout(800)\n        verification_map = self._read_comment_payload_values(\n            page,\n            payload,\n        )\n        emit_progress(progress_callback, 94.0, "Đang tải lại ngữ cảnh Sổ điểm sau khi ghi...")\n        context = self._build_scorebook_context(self._scorebook_snapshot(page))\n        result = summarize_comment_write_result(\n            write_rows,\n            rows_to_apply,\n            result_payload=result_payload,\n            verification_map=verification_map,\n        )\n        emit_progress(progress_callback, 100.0, "Đã hoàn tất thao tác ghi nhận xét.")\n        return context, result\n\n    def apply_score_entries(\n        self,\n        grade_id: str,\n        class_id: str,\n        subject_id: str,\n        term_id: str,\n        entries: List[ScoreWriteEntry | Dict[str, object]],\n        username: str = "",\n        password: str = "",\n        auto_save: bool = False,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, ScoreWriteResult, str, str]:\n        """Writes score entries back into the live scorebook and verifies the updated DOM values."""\n        rows_to_apply = ready_score_write_entries(entries)\n        if not rows_to_apply:\n            raise RuntimeError("Không có dòng điểm nào sẵn sàng để ghi lên VNEDU.")\n\n        with self._open_page() as page:\n            selected_snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(\n                page,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n                username=username,\n                password=password,\n                progress_callback=create_subprogress_reporter(progress_callback, 0.0, 36.0),\n            )\n            active_context = self._build_scorebook_context(selected_snapshot)\n            context, result = self._apply_score_entries_on_page(\n                page,\n                rows_to_apply,\n                context=active_context,\n                auto_save=auto_save,\n                progress_callback=create_subprogress_reporter(progress_callback, 36.0, 100.0),\n            )\n            return context, result, login_message, selection_message\n\n    def _apply_score_entries_on_page(\n        self,\n        page: Page,\n        entries: List[ScoreWriteEntry | Dict[str, object]],\n        context: ScorebookContext | None = None,\n        auto_save: bool = False,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, ScoreWriteResult]:\n        """Writes one score-entry queue back into the already-selected live scorebook page."""\n        rows_to_apply = ready_score_write_entries(entries)\n        if not rows_to_apply:\n            raise RuntimeError("Không có dòng điểm nào sẵn sàng để ghi lên VNEDU.")\n\n        emit_progress(progress_callback, 8.0, "Đang chuẩn bị dữ liệu để ghi lên cột điểm...")\n        payload = build_score_write_payload(rows_to_apply)\n        expected_score_pairs = build_score_write_request_pairs(payload)\n        emit_progress(progress_callback, 16.0, "Đang gắn bộ theo dõi thao tác Lưu..." if auto_save else "Đang chuẩn bị xác minh DOM sau khi ghi điểm...")\n        save_monitor_run_id = self._arm_scorebook_save_monitor(page)\n        result_payload = self._apply_score_payload_to_active_scorebook(\n            page,\n            payload,\n            auto_save=auto_save,\n            progress_callback=create_subprogress_reporter(progress_callback, 16.0, 68.0),\n        )\n        result_payload.update(\n            self._verify_scorebook_save_on_server(\n                page,\n                save_monitor_run_id=save_monitor_run_id,\n                auto_save=auto_save,\n                save_clicked=bool(result_payload.get("saveClicked")),\n                payload_input_names=[str(item.get("inputName", "")).strip() for item in payload],\n                progress_callback=create_subprogress_reporter(progress_callback, 68.0, 84.0),\n            )\n        )\n        emit_progress(progress_callback, 88.0, "Đang xác minh lại điểm vừa ghi trên giao diện...")\n        verification_map = self._wait_for_expected_scorebook_payload_values(\n            page,\n            payload,\n            timeout_sec=0.8,\n            poll_ms=120,\n            progress_callback=create_subprogress_reporter(progress_callback, 88.0, 94.0),\n        )\n        result = summarize_score_write_result(\n            entries,\n            rows_to_apply,\n            result_payload=result_payload,\n            verification_map=verification_map,\n        )\n        emit_progress(progress_callback, 100.0, "Đã hoàn tất thao tác ghi điểm.")\n        active_context = context if context is not None else self._build_scorebook_context(self._scorebook_snapshot(page))\n        return active_context, result\n\n    def analyze_and_apply_comment_rows(\n        self,\n        grade_id: str,\n        class_id: str,\n        subject_id: str,\n        term_id: str,\n        source_column_key: str,\n        comment_column_key: str,\n        rules: List[CommentRule],\n        username: str = "",\n        password: str = "",\n        auto_save: bool = True,\n        allow_overwrite_existing_comment: bool = False,\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[\n        ScorebookContext,\n        List[CommentWriteRow],\n        str,\n        str,\n        ScorebookContext,\n        CommentWriteResult | None,\n        str,\n        str,\n    ]:\n        """Runs analyze and apply on one live page session to avoid reopening the same scorebook twice."""\n        emit_progress(progress_callback, 3.0, "Đang kiểm tra rule và chuẩn bị ghi nhận xét...")\n        compiled_rules = compile_comment_rules(rules)\n        if not compiled_rules:\n            raise RuntimeError("Chưa có rule hợp lệ để phân tích dữ liệu ghi nhận xét.")\n\n        emit_progress(progress_callback, 8.0, "Đang kết nối live Chrome qua CDP...")\n        with self._open_page() as page:\n            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(\n                page,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n                username=username,\n                password=password,\n                progress_callback=create_subprogress_reporter(progress_callback, 8.0, 42.0),\n            )\n            queue_context, write_rows = self._build_comment_write_queue_on_page(\n                page,\n                snapshot,\n                source_column_key=source_column_key,\n                comment_column_key=comment_column_key,\n                compiled_rules=compiled_rules,\n                allow_overwrite_existing_comment=allow_overwrite_existing_comment,\n                progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),\n            )\n            if not ready_comment_write_rows(write_rows):\n                emit_progress(progress_callback, 100.0, "Không có dòng nào sẵn sàng để ghi nhận xét.")\n                return (\n                    queue_context,\n                    write_rows,\n                    login_message,\n                    selection_message,\n                    queue_context,\n                    None,\n                    "",\n                    "",\n                )\n            apply_context, apply_result = self._apply_comment_write_rows_on_page(\n                page,\n                write_rows,\n                auto_save=auto_save,\n                progress_callback=create_subprogress_reporter(progress_callback, 58.0, 100.0),\n            )\n            emit_progress(progress_callback, 100.0, "Đã hoàn tất quá trình ghi nhận xét.")\n            return (\n                queue_context,\n                write_rows,\n                login_message,\n                selection_message,\n                apply_context,\n                apply_result,\n                "",\n                "",\n            )\n\n    def _option_text_by_id(self, options: List[ScoreOption], option_id: str) -> str:\n        """Finds the human-readable text for one score option id."""\n        option_id = option_id.strip()\n        for option in options:\n            if option.option_id == option_id:\n                return option.option_text\n        return option_id\n\n    def _subject_options_by_class_for_current_grade(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        grade_id: str,\n        term_id: str,\n        class_options: List[ScoreOption],\n    ) -> Tuple[List[Dict[str, object]], Dict[str, object]]:\n        """Enumerates subject options per class and restores the original live scorebook context afterwards."""\n        original_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n        original_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")\n        original_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")\n        original_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n        current_snapshot = self._hydrate_scorebook_snapshot_options(\n            page,\n            snapshot,\n            include_grade=False,\n            include_class=True,\n            include_subject=True,\n            include_term=False,\n        )\n        class_subject_specs: List[Dict[str, object]] = []\n\n        for class_option in class_options:\n            class_id = class_option.option_id.strip()\n            if not class_id:\n                continue\n            current_class_id = self._effective_snapshot_selected_id(current_snapshot, "currentClassId", "hiddenClassId")\n            if current_class_id != class_id:\n                class_combo_id = str(current_snapshot.get("classComboId", "")).strip()\n                if not class_combo_id or not self._set_combo_value(page, class_combo_id, class_id):\n                    raise RuntimeError(f"Không thể chọn lớp id={class_id} trong lúc dò quyền lớp/môn.")\n                current_snapshot = self._wait_for_scorebook_snapshot(\n                    page,\n                    expected_grade_id=grade_id or None,\n                    expected_class_id=class_id,\n                    expected_term_id=term_id or None,\n                    required_store_keys=("subject",),\n                    timeout_sec=6.0,\n                )\n                actual_class_id = self._effective_snapshot_selected_id(current_snapshot, "currentClassId", "hiddenClassId")\n                if actual_class_id != class_id:\n                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={class_id} để dò quyền.")\n            current_snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                current_snapshot,\n                include_grade=False,\n                include_class=False,\n                include_subject=True,\n                include_term=False,\n                merge_existing=False,\n                preserve_selected_if_missing=False,\n            )\n            subject_options = self._build_options(list(current_snapshot.get("subjectOptions", [])))\n            if not subject_options:\n                continue\n            class_subject_specs.append(\n                {\n                    "classId": class_option.option_id,\n                    "classText": class_option.option_text,\n                    "subjectOptions": [\n                        {\n                            "option_id": option.option_id,\n                            "option_text": option.option_text,\n                        }\n                        for option in subject_options\n                    ],\n                }\n            )\n\n        if self._requested_scorebook_context_differs(\n            current_snapshot,\n            grade_id=original_grade_id,\n            class_id=original_class_id,\n            subject_id=original_subject_id,\n            term_id=original_term_id,\n        ):\n            current_snapshot, _ = self._select_scorebook_context_on_page(\n                page,\n                current_snapshot,\n                grade_id=original_grade_id,\n                class_id=original_class_id,\n                subject_id=original_subject_id,\n                term_id=original_term_id,\n            )\n\n        return class_subject_specs, current_snapshot\n\n    def _can_comment_scorebook(self, permission_text: str, enabled_comment_input_count: int) -> bool:\n        """Returns whether the current scorebook payload allows comment editing."""\n        normalized_permission = permission_text.strip().lower()\n        if enabled_comment_input_count <= 0:\n            return False\n        return "không có quyền" not in normalized_permission and "khong co quyen" not in normalized_permission\n\n    def _best_effort_scorebook_snapshot(self, page: Page) -> Tuple[Dict[str, object] | None, Exception | None]:\n        """Reads one scorebook snapshot while treating transient live-tab errors as retryable."""\n        try:\n            return self._scorebook_snapshot(page), None\n        except (RuntimeError, PlaywrightError) as error:\n            return None, error\n\n    def _finish_scorebook_snapshot_wait(\n        self,\n        last_snapshot: Dict[str, object],\n        last_error: Exception | None,\n        timeout_sec: float,\n        purpose: str,\n    ) -> Dict[str, object]:\n        """Returns the last good snapshot, or raises the last read error if none succeeded."""\n        if last_snapshot:\n            return last_snapshot\n        if last_error is not None:\n            raise RuntimeError(\n                f"Hết thời gian chờ {purpose} trong {timeout_sec:.1f}s. Lỗi cuối: {last_error}"\n            ) from last_error\n        raise RuntimeError(f"Hết thời gian chờ {purpose} trong {timeout_sec:.1f}s nhưng chưa đọc được snapshot nào.")\n\n    def _discover_accessible_entries_for_current_grade(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        grade_id: str,\n        term_id: str,\n    ) -> List[ScorebookAccessEntry]:\n        """Scans the current grade-term matrix and keeps only class-subject pairs with comment rights."""\n        grade_id = grade_id.strip()\n        term_id = term_id.strip()\n        snapshot = self._hydrate_scorebook_snapshot_options(\n            page,\n            snapshot,\n            include_grade=False,\n            include_class=True,\n            include_subject=True,\n            include_term=False,\n        )\n        class_options = self._build_options(list(snapshot.get("classOptions", [])))\n        if not grade_id or not term_id or not class_options:\n            return []\n\n        grade_text = self._option_text_by_id(self._build_options(list(snapshot.get("gradeOptions", []))), grade_id)\n        term_text = self._option_text_by_id(self._build_options(list(snapshot.get("termOptions", []))), term_id)\n        school_year = str(snapshot.get("hiddenSchoolYear", "")).strip() or str(snapshot.get("currentSchoolYear", "")).strip()\n        window_id = str(snapshot.get("windowId", "")).strip()\n        if not school_year or not window_id:\n            return []\n        class_subject_specs, _restored_snapshot = self._subject_options_by_class_for_current_grade(\n            page,\n            snapshot,\n            grade_id=grade_id,\n            term_id=term_id,\n            class_options=class_options,\n        )\n        if not class_subject_specs:\n            return []\n\n        raw_entries = page.evaluate(\n            """async ({ schoolYear, windowId, gradeId, gradeText, termId, termText, classSubjectSpecs }) => {\n            const endpoint = \'/v5/?load=edu.so_diem.nhap\';\n            const base = {\n                app_nam_hoc: schoolYear,\n                nam_hoc: schoolYear,\n                iKhoi: gradeId,\n                iHocKyId: termId,\n                winid: windowId,\n            };\n\n            const parseRoleText = (doc) => {\n                const roleCell = Array.from(doc.querySelectorAll(\'td\')).find(td =>\n                    /quyền hạn/i.test((td.textContent || \'\').trim())\n                );\n                return (roleCell?.textContent || \'\').trim();\n            };\n\n            const parseTeacherText = (doc) => ((doc.querySelector(\'#gvbm\')?.textContent) || \'\').trim();\n            const tasks = [];\n            for (const classSpec of classSubjectSpecs) {\n                for (const subjectOption of classSpec.subjectOptions || []) {\n                    tasks.push({\n                        classOption: {\n                            option_id: classSpec.classId,\n                            option_text: classSpec.classText,\n                        },\n                        subjectOption,\n                    });\n                }\n            }\n\n            const results = [];\n            const concurrency = 6;\n            for (let index = 0; index < tasks.length; index += concurrency) {\n                const batch = tasks.slice(index, index + concurrency);\n                const batchResults = await Promise.all(batch.map(async (task) => {\n                    const params = new URLSearchParams({\n                        ...base,\n                        iLopId: task.classOption.option_id,\n                        iMonHocId: task.subjectOption.option_id,\n                    });\n\n                    try {\n                        const response = await fetch(endpoint, {\n                            method: \'POST\',\n                            credentials: \'include\',\n                            headers: {\n                                \'Content-Type\': \'application/x-www-form-urlencoded; charset=UTF-8\',\n                            },\n                            body: params.toString(),\n                        });\n                        const html = await response.text();\n                        const doc = new DOMParser().parseFromString(html, \'text/html\');\n                        const commentInputs = Array.from(doc.querySelectorAll(\'input.input_nhan_xet\'));\n                        const enabledCommentInputCount = commentInputs.filter(input => !input.disabled && !input.readOnly).length;\n                        return {\n                            gradeId,\n                            gradeText,\n                            classId: task.classOption.option_id,\n                            classText: task.classOption.option_text,\n                            subjectId: task.subjectOption.option_id,\n                            subjectText: task.subjectOption.option_text,\n                            termId,\n                            termText,\n                            teacherText: parseTeacherText(doc),\n                            permissionText: parseRoleText(doc),\n                            commentInputCount: commentInputs.length,\n                            enabledCommentInputCount,\n                        };\n                    } catch (error) {\n                        return {\n                            gradeId,\n                            gradeText,\n                            classId: task.classOption.option_id,\n                            classText: task.classOption.option_text,\n                            subjectId: task.subjectOption.option_id,\n                            subjectText: task.subjectOption.option_text,\n                            termId,\n                            termText,\n                            teacherText: \'\',\n                            permissionText: `Lỗi dò quyền: ${String(error)}`,\n                            commentInputCount: 0,\n                            enabledCommentInputCount: 0,\n                        };\n                    }\n                }));\n                results.push(...batchResults);\n            }\n            return results;\n        }""",\n            {\n                "schoolYear": school_year,\n                "windowId": window_id,\n                "gradeId": grade_id,\n                "gradeText": grade_text,\n                "termId": term_id,\n                "termText": term_text,\n                "classSubjectSpecs": class_subject_specs,\n            },\n        )\n        entries = self._build_access_entries(list(raw_entries or []))\n        return [\n            entry\n            for entry in entries\n            if self._can_comment_scorebook(entry.permission_text, entry.enabled_comment_input_count)\n        ]\n\n    def _wait_for_scorebook_snapshot(\n        self,\n        page: Page,\n        expected_grade_id: str | None = None,\n        expected_class_id: str | None = None,\n        expected_subject_id: str | None = None,\n        expected_term_id: str | None = None,\n        stale_class_option_id: str = "",\n        required_store_keys: Tuple[str, ...] | None = None,\n        timeout_sec: float = 6.0,\n        progress_callback: ProgressCallback | None = None,\n        progress_message: str = "Đang chờ Sổ điểm đồng bộ dữ liệu...",\n    ) -> Dict[str, object]:\n        """Waits until scorebook combobox state reaches the expected ids."""\n        deadline = time.time() + timeout_sec\n        last_snapshot: Dict[str, object] = {}\n        last_error: Exception | None = None\n        if required_store_keys is None:\n            required_store_key_set = {"class", "subject", "term"}\n        else:\n            required_store_key_set = {str(key).strip().lower() for key in required_store_keys if str(key).strip()}\n        while time.time() < deadline:\n            snapshot, snapshot_error = self._best_effort_scorebook_snapshot(page)\n            if snapshot is None:\n                last_error = snapshot_error\n                page.wait_for_timeout(250)\n                continue\n            last_snapshot = snapshot\n            last_error = None\n\n            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")\n            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")\n            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n\n            grade_ready = expected_grade_id is None or actual_grade_id == expected_grade_id\n            class_ready = expected_class_id is None or actual_class_id == expected_class_id\n            subject_ready = expected_subject_id is None or actual_subject_id == expected_subject_id\n            term_ready = expected_term_id is None or actual_term_id == expected_term_id\n\n            class_store_ready = True\n            if "class" in required_store_key_set and str(snapshot.get("classComboId", "")).strip():\n                class_store_ready = bool(self._snapshot_option_ids(snapshot, "classOptions")) and self._snapshot_store_excludes_stale_option(\n                    snapshot,\n                    options_key="classOptions",\n                    stale_option_id=stale_class_option_id,\n                    actual_selected_id=actual_class_id,\n                )\n\n            subject_store_ready = True\n            if "subject" in required_store_key_set and str(snapshot.get("subjectComboId", "")).strip():\n                subject_store_ready = len(list(snapshot.get("subjectOptions", []))) > 0\n\n            term_store_ready = True\n            if "term" in required_store_key_set and str(snapshot.get("termComboId", "")).strip():\n                term_store_ready = len(list(snapshot.get("termOptions", []))) > 0\n\n            if (\n                grade_ready\n                and class_ready\n                and subject_ready\n                and term_ready\n                and class_store_ready\n                and subject_store_ready\n                and term_store_ready\n            ):\n                emit_progress(progress_callback, 100.0, progress_message)\n                return snapshot\n            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)\n            emit_progress(progress_callback, max(5.0, elapsed_ratio * 95.0), progress_message)\n            page.wait_for_timeout(250)\n        return self._finish_scorebook_snapshot_wait(\n            last_snapshot,\n            last_error,\n            timeout_sec,\n            "Sổ điểm đồng bộ Khối/Lớp/Môn/Học kỳ",\n        )\n\n    def _wait_for_scorebook_permission_snapshot(\n        self,\n        page: Page,\n        expected_class_id: str | None = None,\n        expected_subject_id: str | None = None,\n        expected_term_id: str | None = None,\n        timeout_sec: float = 6.0,\n        progress_callback: ProgressCallback | None = None,\n        progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",\n    ) -> Dict[str, object]:\n        """Waits until the scorebook body catches up and exposes teacher/permission badges."""\n        deadline = time.time() + timeout_sec\n        last_snapshot: Dict[str, object] = {}\n        last_error: Exception | None = None\n        while time.time() < deadline:\n            snapshot, snapshot_error = self._best_effort_scorebook_snapshot(page)\n            if snapshot is None:\n                last_error = snapshot_error\n                page.wait_for_timeout(250)\n                continue\n            last_snapshot = snapshot\n            last_error = None\n            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")\n            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")\n            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n            class_ready = expected_class_id is None or actual_class_id == expected_class_id\n            subject_ready = expected_subject_id is None or actual_subject_id == expected_subject_id\n            term_ready = expected_term_id is None or actual_term_id == expected_term_id\n            content_ready = bool(\n                str(snapshot.get("permissionText", "")).strip()\n                or str(snapshot.get("teacherText", "")).strip()\n                or int(snapshot.get("commentInputCount", 0) or 0) > 0\n            )\n            if class_ready and subject_ready and term_ready and content_ready:\n                emit_progress(progress_callback, 100.0, progress_message)\n                return snapshot\n            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)\n            emit_progress(progress_callback, max(5.0, elapsed_ratio * 95.0), progress_message)\n            page.wait_for_timeout(250)\n        return self._finish_scorebook_snapshot_wait(\n            last_snapshot,\n            last_error,\n            timeout_sec,\n            "Sổ điểm cập nhật quyền/giáo viên",\n        )\n\n    def _resolve_option_id(\n        self,\n        options: List[ScoreOption],\n        current_id: str,\n        preferred_id: str,\n        label: str,\n        notes: List[str],\n        fail_closed_on_missing_preferred: bool = False,\n    ) -> str:\n        """Resolves a preferred combo id while tolerating stale GUI selections."""\n        valid_ids = {item.option_id for item in options}\n        preferred_id = preferred_id.strip()\n        current_id = current_id.strip()\n\n        if preferred_id and preferred_id in valid_ids:\n            return preferred_id\n\n        if preferred_id and preferred_id not in valid_ids:\n            if fail_closed_on_missing_preferred:\n                notes.append(f"{label} đã đổi dữ liệu trên web và không còn khớp với lựa chọn đang yêu cầu.")\n                return ""\n            fallback_id = current_id if current_id in valid_ids else (options[0].option_id if options else "")\n            if fallback_id:\n                notes.append(f"{label} đã đổi dữ liệu trên web, app dùng lựa chọn hiện có gần nhất.")\n                return fallback_id\n\n        if current_id and current_id in valid_ids:\n            return current_id\n\n        return options[0].option_id if options else ""\n\n    def _resolve_accessible_selection(\n        self,\n        entries: List[ScorebookAccessEntry],\n        preferred_class_id: str = "",\n        preferred_subject_id: str = "",\n    ) -> Tuple[str, str, List[str]]:\n        """Chooses a valid class-subject pair from the discovered permission matrix."""\n        return resolve_accessible_selection(\n            entries,\n            preferred_class_id=preferred_class_id,\n            preferred_subject_id=preferred_subject_id,\n        )\n\n    def _apply_access_entries_to_context(\n        self,\n        context: ScorebookContext,\n        entries: List[ScorebookAccessEntry],\n        grade_id: str,\n        term_id: str,\n    ) -> ScorebookContext:\n        """Attaches discovered permission entries to the returned scorebook context."""\n        return apply_access_entries_to_context(\n            context,\n            entries,\n            grade_id=grade_id,\n            term_id=term_id,\n        )\n\n    def _select_scorebook_grade_term_on_page(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        grade_id: str = "",\n        term_id: str = "",\n    ) -> Tuple[Dict[str, object], str]:\n        """Applies grade and term first, without assuming the old class remains valid."""\n        notes: List[str] = []\n        target_grade_id = grade_id.strip()\n        target_term_id = term_id.strip()\n        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n        requested_grade_diff = bool(target_grade_id and current_grade_id != target_grade_id)\n        requested_term_diff = bool(target_term_id and current_term_id != target_term_id)\n        context_requires_sync = requested_grade_diff or requested_term_diff\n\n        if requested_grade_diff:\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=True,\n                include_class=False,\n                include_subject=False,\n                include_term=False,\n            )\n        elif requested_term_diff:\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=False,\n                include_class=False,\n                include_subject=False,\n                include_term=True,\n            )\n\n        if target_grade_id:\n            grade_options = self._build_options(list(snapshot.get("gradeOptions", [])))\n            target_grade_id = self._resolve_option_id(\n                grade_options,\n                current_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"),\n                preferred_id=target_grade_id,\n                label="Khối",\n                notes=notes,\n            )\n            grade_combo_id = str(snapshot.get("gradeComboId", "")).strip()\n            current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n            if grade_combo_id and target_grade_id and current_grade_id != target_grade_id:\n                if not self._set_combo_value(page, grade_combo_id, target_grade_id):\n                    raise RuntimeError(f"Không thể chọn khối id={target_grade_id} trên Sổ điểm.")\n                snapshot = self._wait_for_scorebook_snapshot(\n                    page,\n                    expected_grade_id=target_grade_id,\n                    required_store_keys=("term",) if target_term_id else (),\n                    timeout_sec=6.0,\n                )\n                actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n                if actual_grade_id != target_grade_id:\n                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")\n                if target_term_id:\n                    snapshot = self._hydrate_scorebook_snapshot_options(\n                        page,\n                        snapshot,\n                        include_grade=False,\n                        include_class=False,\n                        include_subject=False,\n                        include_term=True,\n                        merge_existing=False,\n                        preserve_selected_if_missing=False,\n                    )\n\n        if target_term_id:\n            term_options = self._build_options(list(snapshot.get("termOptions", [])))\n            target_term_id = self._resolve_option_id(\n                term_options,\n                current_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId"),\n                preferred_id=target_term_id,\n                label="Học kỳ",\n                notes=notes,\n            )\n            term_combo_id = str(snapshot.get("termComboId", "")).strip()\n            current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n            if term_combo_id and target_term_id and current_term_id != target_term_id:\n                if not self._set_combo_value(page, term_combo_id, target_term_id):\n                    raise RuntimeError(f"Không thể chọn học kỳ id={target_term_id} trên Sổ điểm.")\n                snapshot = self._wait_for_scorebook_snapshot(\n                    page,\n                    expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,\n                    expected_term_id=target_term_id,\n                    required_store_keys=(),\n                    timeout_sec=6.0,\n                )\n                actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n                if actual_term_id != target_term_id:\n                    snapshot = self._wait_for_scorebook_permission_snapshot(\n                        page,\n                        expected_term_id=target_term_id,\n                        timeout_sec=8.0,\n                    )\n                    actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n                if actual_term_id != target_term_id:\n                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn học kỳ id={target_term_id}.")\n\n        if context_requires_sync:\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=requested_grade_diff,\n                include_class=False,\n                include_subject=False,\n                include_term=requested_grade_diff or requested_term_diff,\n                merge_existing=False,\n            )\n\n        return snapshot, " ".join(part for part in notes if part).strip()\n\n    def _select_scorebook_context_on_page(\n        self,\n        page: Page,\n        snapshot: Dict[str, object],\n        grade_id: str = "",\n        class_id: str = "",\n        subject_id: str = "",\n        term_id: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[Dict[str, object], str]:\n        """Applies scorebook combo selections directly on the live VNEDU window."""\n        notes: List[str] = []\n        emit_progress(progress_callback, 5.0, "Đang so sánh ngữ cảnh Khối/Lớp/Môn/Học kỳ...")\n        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n        current_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")\n        current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")\n        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n        stale_class_id_before_grade_change = current_class_id\n        requested_grade_diff = bool(grade_id.strip() and grade_id.strip() != current_grade_id)\n        requested_class_diff = bool(class_id.strip() and class_id.strip() != current_class_id)\n        requested_subject_diff = bool(subject_id.strip() and subject_id.strip() != current_subject_id)\n        requested_term_diff = bool(term_id.strip() and term_id.strip() != current_term_id)\n        context_requires_sync = (\n            requested_grade_diff\n            or requested_class_diff\n            or requested_subject_diff\n            or requested_term_diff\n        )\n        if context_requires_sync:\n            emit_progress(progress_callback, 10.0, "Đang nạp danh sách lựa chọn hiện tại từ Sổ điểm...")\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=requested_grade_diff,\n                include_class=requested_grade_diff or requested_class_diff,\n                include_subject=requested_grade_diff or requested_subject_diff,\n                include_term=requested_grade_diff or requested_term_diff,\n            )\n\n        grade_options = self._build_options(list(snapshot.get("gradeOptions", [])))\n        target_grade_id = self._resolve_option_id(\n            grade_options,\n            current_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"),\n            preferred_id=grade_id,\n            label="Khối",\n            notes=notes,\n        )\n        grade_combo_id = str(snapshot.get("gradeComboId", "")).strip()\n        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n        if grade_combo_id and target_grade_id and current_grade_id != target_grade_id:\n            emit_progress(progress_callback, 18.0, "Đang đổi Khối trên Sổ điểm...")\n            if not self._set_combo_value(page, grade_combo_id, target_grade_id):\n                raise RuntimeError(f"Không thể chọn khối id={target_grade_id} trên Sổ điểm.")\n            snapshot = self._wait_for_scorebook_snapshot(\n                page,\n                expected_grade_id=target_grade_id,\n                stale_class_option_id=stale_class_id_before_grade_change,\n                required_store_keys=("class",),\n                timeout_sec=6.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 18.0, 30.0),\n                progress_message="Đang đồng bộ dữ liệu sau khi đổi Khối...",\n            )\n            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n            if actual_grade_id != target_grade_id:\n                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")\n            snapshot = self._wait_for_hydrated_scorebook_options(\n                page,\n                snapshot,\n                options_key="classOptions",\n                include_grade=False,\n                include_class=True,\n                include_subject=True,\n                include_term=True,\n                expected_grade_id=target_grade_id,\n                stale_option_id=stale_class_id_before_grade_change,\n                timeout_sec=5.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 30.0, 34.0),\n                progress_message="Đang nạp lại danh sách Lớp/Môn sau khi đổi Khối...",\n            )\n\n        class_options = self._build_options(list(snapshot.get("classOptions", [])))\n        requested_class_id = class_id.strip()\n        target_class_id = self._resolve_option_id(\n            class_options,\n            current_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),\n            preferred_id=class_id,\n            label="Lớp",\n            notes=notes,\n            fail_closed_on_missing_preferred=bool(requested_class_id),\n        )\n        if requested_class_id and not target_class_id:\n            available_classes = ", ".join(option.option_text for option in class_options[:6])\n            available_suffix = (\n                f" Các lớp hiện có sau khi đổi Khối: {available_classes}."\n                if available_classes\n                else " Web không trả về danh sách Lớp hợp lệ sau khi đổi Khối."\n            )\n            raise RuntimeError("Lớp đang chọn không còn tồn tại trong ngữ cảnh hiện tại." + available_suffix)\n        class_combo_id = str(snapshot.get("classComboId", "")).strip()\n        current_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")\n        if class_combo_id and target_class_id and current_class_id != target_class_id:\n            emit_progress(progress_callback, 34.0, "Đang đổi Lớp trên Sổ điểm...")\n            if not self._set_combo_value(page, class_combo_id, target_class_id):\n                raise RuntimeError(f"Không thể chọn lớp id={target_class_id} trên Sổ điểm.")\n            snapshot = self._wait_for_scorebook_snapshot(\n                page,\n                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,\n                expected_class_id=target_class_id,\n                required_store_keys=("subject",),\n                timeout_sec=6.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 34.0, 48.0),\n                progress_message="Đang đồng bộ dữ liệu sau khi đổi Lớp...",\n            )\n            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")\n            if actual_class_id != target_class_id:\n                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={target_class_id}.")\n            snapshot = self._wait_for_hydrated_scorebook_options(\n                page,\n                snapshot,\n                options_key="subjectOptions",\n                include_grade=False,\n                include_class=True,\n                include_subject=True,\n                include_term=False,\n                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,\n                expected_class_id=target_class_id,\n                timeout_sec=5.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 48.0, 52.0),\n                progress_message="Đang nạp lại danh sách Môn sau khi đổi Lớp...",\n            )\n\n        subject_options = self._build_options(list(snapshot.get("subjectOptions", [])))\n        requested_subject_id = subject_id.strip()\n        target_subject_id = self._resolve_option_id(\n            subject_options,\n            current_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),\n            preferred_id=subject_id,\n            label="Môn",\n            notes=notes,\n            fail_closed_on_missing_preferred=bool(requested_subject_id),\n        )\n        if requested_subject_id and not target_subject_id:\n            available_subjects = ", ".join(option.option_text for option in subject_options[:6])\n            available_suffix = (\n                f" Các môn hiện có cho Lớp này: {available_subjects}."\n                if available_subjects\n                else " Web không trả về danh sách Môn hợp lệ cho Lớp hiện tại."\n            )\n            raise RuntimeError("Môn đang chọn không còn tồn tại trong ngữ cảnh hiện tại." + available_suffix)\n        subject_combo_id = str(snapshot.get("subjectComboId", "")).strip()\n        current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")\n        if subject_combo_id and target_subject_id and current_subject_id != target_subject_id:\n            emit_progress(progress_callback, 52.0, "Đang đổi Môn trên Sổ điểm...")\n            if not self._set_combo_value(page, subject_combo_id, target_subject_id):\n                raise RuntimeError(f"Không thể chọn môn id={target_subject_id} trên Sổ điểm.")\n            snapshot = self._wait_for_scorebook_snapshot(\n                page,\n                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,\n                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                expected_subject_id=target_subject_id,\n                required_store_keys=(),\n                timeout_sec=6.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 52.0, 66.0),\n                progress_message="Đang đồng bộ dữ liệu sau khi đổi Môn...",\n            )\n            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")\n            if actual_subject_id != target_subject_id:\n                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn môn id={target_subject_id}.")\n\n        term_options = self._build_options(list(snapshot.get("termOptions", [])))\n        target_term_id = self._resolve_option_id(\n            term_options,\n            current_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId"),\n            preferred_id=term_id,\n            label="Học kỳ",\n            notes=notes,\n        )\n        term_combo_id = str(snapshot.get("termComboId", "")).strip()\n        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n        if term_combo_id and target_term_id and current_term_id != target_term_id:\n            emit_progress(progress_callback, 70.0, "Đang đổi Học kỳ trên Sổ điểm...")\n            if not self._set_combo_value(page, term_combo_id, target_term_id):\n                raise RuntimeError(f"Không thể chọn học kỳ id={target_term_id} trên Sổ điểm.")\n            snapshot = self._wait_for_scorebook_snapshot(\n                page,\n                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,\n                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n                expected_term_id=target_term_id,\n                required_store_keys=(),\n                timeout_sec=6.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 70.0, 84.0),\n                progress_message="Đang đồng bộ dữ liệu sau khi đổi Học kỳ...",\n            )\n            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n            if actual_term_id != target_term_id:\n                snapshot = self._wait_for_scorebook_permission_snapshot(\n                    page,\n                    expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                    expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n                    expected_term_id=target_term_id,\n                    timeout_sec=8.0,\n                    progress_callback=create_subprogress_reporter(progress_callback, 84.0, 90.0),\n                    progress_message="Đang chờ quyền và giáo viên cập nhật sau khi đổi Học kỳ...",\n                )\n                actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n            if actual_term_id != target_term_id:\n                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn học kỳ id={target_term_id}.")\n\n        emit_progress(progress_callback, 92.0, "Đang xác minh ngữ cảnh cuối cùng trên Sổ điểm...")\n        snapshot = self._wait_for_scorebook_permission_snapshot(\n            page,\n            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,\n            timeout_sec=6.0,\n            progress_callback=create_subprogress_reporter(progress_callback, 92.0, 100.0),\n            progress_message="Đang xác minh quyền và giáo viên cho ngữ cảnh mới...",\n        )\n        if context_requires_sync:\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=requested_grade_diff,\n                include_class=requested_grade_diff or requested_class_diff,\n                include_subject=requested_grade_diff or requested_class_diff or requested_subject_diff,\n                include_term=requested_grade_diff or requested_term_diff,\n                merge_existing=False,\n            )\n        emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")\n        return snapshot, " ".join(notes).strip()\n\n    def load_scorebook_context(\n        self,\n        username: str = "",\n        password: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, str]:\n        """Loads the visible scorebook shell from the live browser."""\n        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")\n        with self._open_page() as page:\n            login_message = self._login_if_needed_on_page(\n                page,\n                username=username,\n                password=password,\n                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 30.0),\n            )\n            self._ensure_scorebook_screen(\n                page,\n                progress_callback=create_subprogress_reporter(progress_callback, 30.0, 45.0),\n            )\n            snapshot = self._wait_for_scorebook_snapshot(\n                page,\n                timeout_sec=8.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 45.0, 65.0),\n                progress_message="Đang đọc dữ liệu khung Sổ điểm...",\n            )\n            snapshot = self._wait_for_scorebook_permission_snapshot(\n                page,\n                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,\n                timeout_sec=6.0,\n                progress_callback=create_subprogress_reporter(progress_callback, 65.0, 80.0),\n                progress_message="Đang đọc quyền hạn và giáo viên...",\n            )\n            emit_progress(progress_callback, 82.0, "Đang nạp danh sách Khối/Lớp/Môn/Học kỳ...")\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=True,\n                include_class=True,\n                include_subject=True,\n                include_term=True,\n                only_when_incomplete=True,\n            )\n            emit_progress(progress_callback, 94.0, "Đang dựng ngữ cảnh Sổ điểm trong GUI...")\n            context = self._build_scorebook_context(snapshot)\n            emit_progress(progress_callback, 100.0, "Đã đọc xong dữ liệu Sổ điểm.")\n            return context, login_message\n\n    def load_accessible_scorebook_context(\n        self,\n        username: str = "",\n        password: str = "",\n    ) -> Tuple[ScorebookContext, str, str]:\n        """Loads the current scorebook shell, then filters it to only class-subject pairs with permission."""\n        with self._open_page() as page:\n            login_message = self._login_if_needed_on_page(page, username=username, password=password)\n            self._ensure_scorebook_screen(page)\n            snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)\n            snapshot = self._wait_for_scorebook_permission_snapshot(\n                page,\n                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,\n                timeout_sec=6.0,\n            )\n            snapshot = self._hydrate_scorebook_snapshot_options(\n                page,\n                snapshot,\n                include_grade=True,\n                include_class=True,\n                include_subject=True,\n                include_term=True,\n            )\n            grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")\n            term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")\n            entries = self._discover_accessible_entries_for_current_grade(page, snapshot, grade_id=grade_id, term_id=term_id)\n            selection_message_parts: List[str] = []\n            if entries:\n                class_id, subject_id, fallback_notes = self._resolve_accessible_selection(\n                    entries,\n                    preferred_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),\n                    preferred_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),\n                )\n                if class_id and subject_id:\n                    snapshot, select_notes = self._select_scorebook_context_on_page(\n                        page,\n                        snapshot,\n                        grade_id=grade_id,\n                        class_id=class_id,\n                        subject_id=subject_id,\n                        term_id=term_id,\n                    )\n                    if select_notes:\n                        selection_message_parts.append(select_notes)\n                selection_message_parts.extend(fallback_notes)\n            context = self._build_scorebook_context(snapshot)\n            self._apply_access_entries_to_context(context, entries, grade_id=grade_id, term_id=term_id)\n            if entries:\n                selection_message_parts.append(\n                    f"Đã dò quyền lớp/môn cho {self._option_text_by_id(context.grade_options, grade_id)}: "\n                    f"{len(entries)} tổ hợp có thể nhập nhận xét."\n                )\n            else:\n                selection_message_parts.append("Không tìm thấy tổ hợp lớp/môn nào có quyền nhập nhận xét trong khối hiện tại.")\n            return context, login_message, " ".join(part for part in selection_message_parts if part).strip()\n\n    def select_scorebook_context(\n        self,\n        grade_id: str = "",\n        class_id: str = "",\n        subject_id: str = "",\n        term_id: str = "",\n        username: str = "",\n        password: str = "",\n        progress_callback: ProgressCallback | None = None,\n    ) -> Tuple[ScorebookContext, str, str]:\n        """Applies the requested scorebook context on the live browser and returns the refreshed shell."""\n        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")\n        with self._open_page() as page:\n            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(\n                page,\n                grade_id=grade_id,\n                class_id=class_id,\n                subject_id=subject_id,\n                term_id=term_id,\n                username=username,\n                password=password,\n                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 100.0),\n            )\n            emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")\n            return self._build_scorebook_context(snapshot), login_message, selection_message\n\n    def select_accessible_scorebook_context(\n        self,\n        grade_id: str = "",\n        class_id: str = "",\n        subject_id: str = "",\n        term_id: str = "",\n        username: str = "",\n        password: str = "",\n    ) -> Tuple[ScorebookContext, str, str]:\n        """Applies grade-term, scans permission matrix, then lands on one valid class-subject pair only."""\n        with self._open_page() as page:\n            login_message = self._login_if_needed_on_page(page, username=username, password=password)\n            self._ensure_scorebook_screen(page)\n            snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)\n            snapshot = self._wait_for_scorebook_permission_snapshot(\n                page,\n                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,\n                timeout_sec=6.0,\n            )\n            initial_context = self._build_scorebook_context(snapshot)\n            target_grade_id = grade_id.strip() or initial_context.selected_grade_id\n            target_term_id = term_id.strip() or initial_context.selected_term_id\n            snapshot, selection_message = self._select_scorebook_grade_term_on_page(\n                page,\n                snapshot,\n                grade_id=target_grade_id,\n                term_id=target_term_id,\n            )\n            current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or target_grade_id\n            current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or target_term_id\n            entries = self._discover_accessible_entries_for_current_grade(\n                page,\n                snapshot,\n                grade_id=current_grade_id,\n                term_id=current_term_id,\n            )\n            effective_class_id, effective_subject_id, fallback_notes = self._resolve_accessible_selection(\n                entries,\n                preferred_class_id=class_id,\n                preferred_subject_id=subject_id,\n            )\n            if effective_class_id and effective_subject_id:\n                snapshot, post_select_message = self._select_scorebook_context_on_page(\n                    page,\n                    snapshot,\n                    grade_id=current_grade_id,\n                    class_id=effective_class_id,\n                    subject_id=effective_subject_id,\n                    term_id=current_term_id,\n                )\n                if post_select_message:\n                    selection_message = f"{selection_message} {post_select_message}".strip()\n            context = self._build_scorebook_context(snapshot)\n            self._apply_access_entries_to_context(\n                context,\n                entries,\n                grade_id=current_grade_id,\n                term_id=current_term_id,\n            )\n            final_message_parts = [selection_message, *fallback_notes]\n            if entries:\n                final_message_parts.append(\n                    f"Đã dò quyền lớp/môn cho {self._option_text_by_id(context.grade_options, current_grade_id)}: "\n                    f"{len(entries)} tổ hợp có thể nhập nhận xét."\n                )\n            else:\n                final_message_parts.append("Không tìm thấy tổ hợp lớp/môn nào có quyền nhập nhận xét trong khối hiện tại.")\n            return context, login_message, " ".join(part for part in final_message_parts if part).strip()\n\n    def discover_accessible_entries_for_current_context(\n        self,\n        username: str = "",\n        password: str = "",\n        expected_grade_id: str = "",\n        expected_term_id: str = "",\n    ) -> Tuple[ScorebookContext, List[ScorebookAccessEntry], str]:\n        """Scans permission entries for the current live scorebook grade-term without changing combo selections."""\n        with self._open_page() as page:\n            login_message = self._login_if_needed_on_page(page, username=username, password=password)\n            self._ensure_scorebook_screen(page)\n            snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)\n            snapshot = self._wait_for_scorebook_permission_snapshot(\n                page,\n                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,\n                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,\n                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,\n                timeout_sec=6.0,\n            )\n            context = self._build_scorebook_context(snapshot)\n            grade_id = context.selected_grade_id\n            term_id = context.selected_term_id\n            if expected_grade_id.strip() and grade_id != expected_grade_id.strip():\n                raise RuntimeError("Ngữ cảnh live Chrome đã đổi khối trước khi quét quyền nền hoàn tất.")\n            if expected_term_id.strip() and term_id != expected_term_id.strip():\n                raise RuntimeError("Ngữ cảnh live Chrome đã đổi học kỳ trước khi quét quyền nền hoàn tất.")\n            entries = self._discover_accessible_entries_for_current_grade(\n                page,\n                snapshot,\n                grade_id=grade_id,\n                term_id=term_id,\n            )\n            self._apply_access_entries_to_context(context, entries, grade_id=grade_id, term_id=term_id)\n            return context, entries, login_message\n\n    def open_target_page(self, progress_callback: ProgressCallback | None = None) -> str:\n        """Opens the VNEDU target page in the connected browser session."""\n        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")\n        with self._open_page() as page:\n            emit_progress(progress_callback, 35.0, "Đang mở trang VNEDU...")\n            self._goto_target_page(page)\n            emit_progress(progress_callback, 100.0, "Đã mở trang VNEDU.")\n            return page.url\n\n\nclass AutoNhanXetV2App:\n    """Tkinter skeleton for the Playwright/CDP-based VNEDU comment tool."""\n\n    def __init__(self, root: tk.Tk) -> None:\n        self.root = root\n        self.root.title(APP_TITLE)\n        self.root.geometry(WINDOW_SIZE)\n        self.root.minsize(980, 680)\n\n        self.port_var = tk.StringVar(value="9224")\n        self.url_var = tk.StringVar(value="https://vemzezsoasgdsoctrang.vnedu.vn/v5/")\n        self.username_var = tk.StringVar()\n        self.password_var = tk.StringVar()\n        self.show_password_var = tk.BooleanVar(value=False)\n        self.status_var = tk.StringVar(value="Chưa kết nối")\n\n        self.grade_var = tk.StringVar()\n        self.class_var = tk.StringVar()\n        self.subject_var = tk.StringVar()\n        self.term_var = tk.StringVar()\n        self.detected_score_var = tk.StringVar(value="(chưa dò)")\n        self.detected_comment_var = tk.StringVar(value="(chưa dò)")\n        self.detected_candidates_var = tk.StringVar(value="")\n        self.detected_reason_var = tk.StringVar(value="")\n        self.score_source_var = tk.StringVar(value="(chưa dò)")\n        self.num_forms_var = tk.StringVar(value="7")\n        self.auto_save_var = tk.BooleanVar(value=True)\n        self.allow_comment_overwrite_var = tk.BooleanVar(value=False)\n\n        self.grade_label_to_id: Dict[str, str] = {}\n        self.class_label_to_id: Dict[str, str] = {}\n        self.subject_label_to_id: Dict[str, str] = {}\n        self.term_label_to_id: Dict[str, str] = {}\n        self.score_source_label_to_key: Dict[str, str] = {}\n        self.current_context: ScorebookContext | None = None\n        self.score_forms: List[Dict[str, tk.StringVar]] = []\n        self.access_entry_cache: Dict[Tuple[str, str, str, str], List[ScorebookAccessEntry]] = {}\n        self._access_scan_inflight: set[Tuple[str, str, str, str]] = set()\n        self._access_cache_identity: Tuple[str, str] | None = None\n        self._matrix_access_scan_enabled = False\n        self.preferred_score_source_key = ""\n        self._suspend_context_events = False\n        self._suspend_config_autosave = True\n        self._config_autosave_after_id: str | None = None\n        self._rule_busy_widgets: List[tk.Misc] = []\n        self._log_history: List[str] = []\n        self._progress_value = 0.0\n        self._progress_message = "Sẵn sàng"\n\n        self._busy = False\n        self._foreground_task_token = 0\n        self._context_apply_after_id: str | None = None\n        self._context_apply_delay_ms = 350\n        self._poll_after_id: str | None = None\n        self._automation_lock = threading.Lock()\n        self._passive_scan_delay_ms = 900\n        self._result_queue: Queue[\n            Tuple[\n                object | None,\n                Exception | None,\n                Callable[[object], None],\n                Callable[[Exception], None] | None,\n            ]\n        ] = Queue()\n        self._passive_result_queue: Queue[\n            Tuple[\n                object | None,\n                Exception | None,\n                Callable[[object], None],\n                Callable[[Exception], None] | None,\n            ]\n        ] = Queue()\n        self._progress_queue: Queue[Tuple[int, float, str]] = Queue()\n        self._busy_widgets: List[Tuple[tk.Misc, str]] = []\n\n        self._build_ui()\n        self._load_config()\n        self._sync_access_cache_identity()\n        self._bind_config_autosave_traces()\n        self._suspend_config_autosave = False\n        self.root.bind("<Destroy>", self._on_root_destroy, add="+")\n        self._poll_after_id = self.root.after(50, self._poll_background_results)\n\n    def _register_busy_widget(self, widget: tk.Misc, normal_state: str) -> tk.Misc:\n        """Tracks one widget so the busy-state guard can disable and restore it later."""\n        self._busy_widgets.append((widget, normal_state))\n        return widget\n\n    def _on_root_destroy(self, event: tk.Event | None = None) -> None:\n        """Cancels pending Tk callbacks when the root window is being destroyed."""\n        if event is not None and event.widget is not self.root:\n            return\n        for attr_name in ("_poll_after_id", "_context_apply_after_id", "_config_autosave_after_id"):\n            after_id = getattr(self, attr_name, None)\n            if not after_id:\n                continue\n            setattr(self, attr_name, None)\n            try:\n                self.root.after_cancel(after_id)\n            except (tk.TclError, RuntimeError):\n                continue\n\n    def _primary_action_button_options(self) -> Dict[str, object]:\n        """Returns the shared visual styling for the main live-action buttons."""\n        return {\n            "bg": "#f0c93d",\n            "fg": "#000000",\n            "activebackground": "#ddb62f",\n            "activeforeground": "#000000",\n            "disabledforeground": "#a6924a",\n            "font": ("Segoe UI", 10, "bold"),\n            "relief": tk.SOLID,\n            "borderwidth": 1,\n            "padx": 14,\n            "pady": 2,\n            "highlightthickness": 0,\n        }\n\n    def _apply_comments_button_options(self) -> Dict[str, object]:\n        """Returns the visual styling for the apply-comments action button."""\n        return {\n            "bg": "#58b957",\n            "fg": "#000000",\n            "activebackground": "#499f49",\n            "activeforeground": "#000000",\n            "disabledforeground": "#6f9c6f",\n            "font": ("Segoe UI", 10, "bold"),\n            "relief": tk.SOLID,\n            "borderwidth": 1,\n            "padx": 14,\n            "pady": 2,\n            "highlightthickness": 0,\n        }\n\n    def _build_session_frame(self, parent: tk.Misc) -> None:\n        """Builds the VNEDU session/config controls at the top of the window."""\n        session_frame = ttk.LabelFrame(parent, text="1. Phiên VNEDU")\n        session_frame.pack(fill=tk.X)\n\n        ttk.Label(session_frame, text="CDP Port:").grid(row=0, column=0, padx=6, pady=6, sticky="w")\n        self.port_entry = ttk.Entry(session_frame, textvariable=self.port_var, width=10)\n        self.port_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")\n        self._register_busy_widget(self.port_entry, "normal")\n\n        ttk.Label(session_frame, text="URL:").grid(row=0, column=2, padx=6, pady=6, sticky="w")\n        self.url_entry = ttk.Entry(session_frame, textvariable=self.url_var, width=58)\n        self.url_entry.grid(row=0, column=3, padx=6, pady=6, sticky="we")\n        self._register_busy_widget(self.url_entry, "normal")\n\n        ttk.Label(session_frame, text="Tài khoản:").grid(row=1, column=0, padx=6, pady=6, sticky="w")\n        self.username_entry = ttk.Entry(session_frame, textvariable=self.username_var, width=26)\n        self.username_entry.grid(row=1, column=1, padx=6, pady=6, sticky="w")\n        self._register_busy_widget(self.username_entry, "normal")\n\n        ttk.Label(session_frame, text="Mật khẩu:").grid(row=1, column=2, padx=6, pady=6, sticky="w")\n        password_row = ttk.Frame(session_frame)\n        password_row.grid(row=1, column=3, padx=6, pady=6, sticky="w")\n        self.password_entry = ttk.Entry(\n            password_row,\n            textvariable=self.password_var,\n            width=26,\n            show=password_entry_show_value(bool(self.show_password_var.get())),\n        )\n        self.password_entry.pack(side=tk.LEFT)\n        self._register_busy_widget(self.password_entry, "normal")\n        self.show_password_check = ttk.Checkbutton(\n            password_row,\n            text="Hiện mật khẩu",\n            variable=self.show_password_var,\n            command=self._apply_password_visibility,\n        )\n        self.show_password_check.pack(side=tk.LEFT, padx=(8, 0))\n        self._register_busy_widget(self.show_password_check, "normal")\n\n        button_row = ttk.Frame(session_frame)\n        button_row.grid(row=2, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="we")\n\n        self.open_button = ttk.Button(button_row, text="Mở VNEDU", command=self.on_open_web)\n        self.open_button.pack(side=tk.LEFT, padx=(0, 6))\n        self._register_busy_widget(self.open_button, "normal")\n\n        self.save_session_button = ttk.Button(\n            button_row,\n            text="Lưu cấu hình",\n            command=self.on_save_config,\n        )\n        self.save_session_button.pack(side=tk.LEFT, padx=(0, 6))\n        self._register_busy_widget(self.save_session_button, "normal")\n\n        self.load_shell_button = tk.Button(\n            button_row,\n            text="ĐĂNG NHẬP & LOAD DATA",\n            command=self.on_load_scorebook_shell,\n            **self._primary_action_button_options(),\n        )\n        self.load_shell_button.pack(side=tk.LEFT)\n        self._register_busy_widget(self.load_shell_button, "normal")\n\n        self.progress_canvas = tk.Canvas(\n            button_row,\n            height=24,\n            background="#edf5ed",\n            highlightthickness=1,\n            highlightbackground="#b5cbb5",\n            relief=tk.FLAT,\n        )\n        self.progress_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(18, 0))\n        self._progress_fill_id = self.progress_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")\n        self._progress_text_id = self.progress_canvas.create_text(\n            8,\n            12,\n            anchor="w",\n            fill="#1f4729",\n            font=("Segoe UI", 9, "bold"),\n            text=build_progress_caption(self._progress_value, self._progress_message),\n        )\n        self.progress_canvas.bind("<Configure>", self._on_progress_canvas_configure)\n        self._render_progress_bar()\n\n        ttk.Label(session_frame, textvariable=self.status_var, foreground="#2f5d50").grid(\n            row=3, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="w"\n        )\n        session_frame.columnconfigure(3, weight=1)\n\n    def _build_context_frame(self, parent: tk.Misc) -> None:\n        """Builds the scorebook context selectors and apply button."""\n        context_frame = ttk.LabelFrame(parent, text="2. Ngữ cảnh Sổ điểm (beta)")\n        context_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))\n\n        ttk.Label(context_frame, text="Khối:").grid(row=0, column=0, padx=6, pady=6, sticky="w")\n        self.grade_combo = ttk.Combobox(context_frame, textvariable=self.grade_var, state="readonly", width=20)\n        self.grade_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")\n\n        ttk.Label(context_frame, text="Lớp:").grid(row=0, column=2, padx=6, pady=6, sticky="w")\n        self.class_combo = ttk.Combobox(context_frame, textvariable=self.class_var, state="readonly", width=20)\n        self.class_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")\n\n        ttk.Label(context_frame, text="Môn:").grid(row=1, column=0, padx=6, pady=6, sticky="w")\n        self.subject_combo = ttk.Combobox(context_frame, textvariable=self.subject_var, state="readonly", width=20)\n        self.subject_combo.grid(row=1, column=1, padx=6, pady=6, sticky="we")\n\n        ttk.Label(context_frame, text="Học kỳ:").grid(row=1, column=2, padx=6, pady=6, sticky="w")\n        self.term_combo = ttk.Combobox(context_frame, textvariable=self.term_var, state="readonly", width=20)\n        self.term_combo.grid(row=1, column=3, padx=6, pady=6, sticky="we")\n\n        context_button_row = ttk.Frame(context_frame)\n        context_button_row.grid(row=2, column=0, columnspan=4, padx=6, pady=(0, 6), sticky="w")\n\n        self.apply_context_button = ttk.Button(\n            context_button_row,\n            text="Áp ngữ cảnh lên web",\n            command=self.on_apply_selected_context,\n        )\n        self.apply_context_button.pack(side=tk.LEFT)\n\n        self._register_busy_widget(self.grade_combo, "readonly")\n        self._register_busy_widget(self.class_combo, "readonly")\n        self._register_busy_widget(self.subject_combo, "readonly")\n        self._register_busy_widget(self.term_combo, "readonly")\n        self._register_busy_widget(self.apply_context_button, "normal")\n\n        self.grade_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)\n        self.class_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)\n        self.subject_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)\n        self.term_combo.bind("<<ComboboxSelected>>", self.on_context_selection_changed)\n        context_frame.columnconfigure(1, weight=1)\n        context_frame.columnconfigure(3, weight=1)\n\n    def _build_detected_columns_frame(self, parent: tk.Misc) -> None:\n        """Builds the read-only panel showing detected score/comment columns."""\n        detected_frame = ttk.LabelFrame(parent, text="3. Cột tự nhận diện")\n        detected_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))\n        ttk.Label(detected_frame, text="Cột điểm dùng để xét:").grid(row=0, column=0, padx=6, pady=4, sticky="nw")\n        self.score_source_combo = ttk.Combobox(\n            detected_frame,\n            textvariable=self.score_source_var,\n            state="readonly",\n            width=28,\n        )\n        self.score_source_combo.grid(row=0, column=1, padx=6, pady=4, sticky="we")\n        self.score_source_combo.bind("<<ComboboxSelected>>", self.on_score_source_selection_changed)\n        self._register_busy_widget(self.score_source_combo, "readonly")\n        ttk.Label(detected_frame, text="Cột điểm đề xuất:").grid(row=1, column=0, padx=6, pady=4, sticky="nw")\n        ttk.Label(\n            detected_frame,\n            textvariable=self.detected_score_var,\n            justify=tk.LEFT,\n            wraplength=360,\n        ).grid(\n            row=1, column=1, padx=6, pady=4, sticky="w"\n        )\n        ttk.Label(detected_frame, text="Cột nhận xét:").grid(row=2, column=0, padx=6, pady=4, sticky="nw")\n        ttk.Label(\n            detected_frame,\n            textvariable=self.detected_comment_var,\n            justify=tk.LEFT,\n            wraplength=360,\n        ).grid(\n            row=2, column=1, padx=6, pady=4, sticky="w"\n        )\n        ttk.Label(detected_frame, text="Ứng viên điểm:").grid(row=3, column=0, padx=6, pady=4, sticky="nw")\n        ttk.Label(\n            detected_frame,\n            textvariable=self.detected_candidates_var,\n            justify=tk.LEFT,\n            wraplength=360,\n        ).grid(row=3, column=1, padx=6, pady=4, sticky="w")\n        ttk.Label(detected_frame, text="Lý do chọn:").grid(row=4, column=0, padx=6, pady=4, sticky="nw")\n        ttk.Label(\n            detected_frame,\n            textvariable=self.detected_reason_var,\n            justify=tk.LEFT,\n            wraplength=360,\n        ).grid(row=4, column=1, padx=6, pady=4, sticky="w")\n        detected_frame.columnconfigure(1, weight=1)\n\n    def _build_rules_frame(self, parent: tk.Misc) -> None:\n        """Builds the rule editor, apply controls, and scrollable rule form area."""\n        rules_frame = ttk.LabelFrame(parent, text="4. Rule nhận xét")\n        rules_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))\n        rule_ctrl_row = ttk.Frame(rules_frame)\n        rule_ctrl_row.pack(fill=tk.X, padx=6, pady=(6, 4))\n        ttk.Label(rule_ctrl_row, text="Số rule:").pack(side=tk.LEFT)\n        self.num_forms_entry = ttk.Entry(rule_ctrl_row, textvariable=self.num_forms_var, width=6)\n        self.num_forms_entry.pack(side=tk.LEFT, padx=(6, 8))\n        self._register_busy_widget(self.num_forms_entry, "normal")\n\n        self.build_rules_button = ttk.Button(\n            rule_ctrl_row,\n            text="Tạo form rule",\n            command=self.on_build_rule_forms,\n        )\n        self.build_rules_button.pack(side=tk.LEFT, padx=(0, 6))\n        self._register_busy_widget(self.build_rules_button, "normal")\n\n        self.export_default_rules_button = ttk.Button(\n            rule_ctrl_row,\n            text="Nhận xét mặc định",\n            command=self.on_export_default_rules,\n        )\n        self.export_default_rules_button.pack(side=tk.LEFT, padx=(0, 6))\n        self._register_busy_widget(self.export_default_rules_button, "normal")\n\n        self.apply_comments_button = tk.Button(\n            rule_ctrl_row,\n            text="GHI NHẬN XÉT",\n            command=self.on_apply_comments,\n            **self._apply_comments_button_options(),\n        )\n        self.apply_comments_button.pack(side=tk.RIGHT)\n        self._register_busy_widget(self.apply_comments_button, "normal")\n\n        self.auto_save_check = ttk.Checkbutton(\n            rule_ctrl_row,\n            text="Tự bấm Lưu",\n            variable=self.auto_save_var,\n        )\n        self.auto_save_check.pack(side=tk.LEFT, padx=(12, 0))\n        self._register_busy_widget(self.auto_save_check, "normal")\n\n        self.allow_comment_overwrite_check = ttk.Checkbutton(\n            rule_ctrl_row,\n            text="Cho phép ghi đè nhận xét chữ đã có",\n            variable=self.allow_comment_overwrite_var,\n        )\n        self.allow_comment_overwrite_check.pack(side=tk.LEFT, padx=(12, 0))\n        self._register_busy_widget(self.allow_comment_overwrite_check, "normal")\n\n        rule_hint = ttk.Label(\n            rules_frame,\n            text=(\n                "Rule đầu tiên khớp sẽ được dùng. "\n                "App dùng cột điểm bạn chọn ở trên để ghi trực tiếp lên web."\n            ),\n            justify=tk.LEFT,\n        )\n        rule_hint.pack(fill=tk.X, padx=6, pady=(0, 4))\n\n        self.rule_canvas = tk.Canvas(rules_frame, height=180, highlightthickness=0)\n        self.rule_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=(0, 6))\n        self.rule_scrollbar = ttk.Scrollbar(rules_frame, orient=tk.VERTICAL, command=self.rule_canvas.yview)\n        self.rule_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=(0, 6))\n        self.rule_canvas.configure(yscrollcommand=self.rule_scrollbar.set)\n        self.rule_form_container = ttk.Frame(self.rule_canvas)\n        self.rule_canvas_window = self.rule_canvas.create_window(\n            (0, 0),\n            window=self.rule_form_container,\n            anchor="nw",\n        )\n        self.rule_form_container.bind(\n            "<Configure>",\n            lambda _event: self.rule_canvas.configure(scrollregion=self.rule_canvas.bbox("all")),\n        )\n        self.rule_canvas.bind(\n            "<Configure>",\n            lambda event: self.rule_canvas.itemconfigure(self.rule_canvas_window, width=event.width),\n        )\n\n    def _build_ui(self) -> None:\n        """Builds the initial skeleton GUI."""\n        main = ttk.Frame(self.root, padding=10)\n        main.pack(fill=tk.BOTH, expand=True)\n        self._build_session_frame(main)\n\n        top_panels_row = ttk.Frame(main)\n        top_panels_row.pack(fill=tk.X, pady=(10, 0))\n        self._build_context_frame(top_panels_row)\n        self._build_detected_columns_frame(top_panels_row)\n        self._build_rules_frame(main)\n\n    def _build_automation(self) -> VnEduScoreAutomation:\n        """Builds the CDP automation object from current UI state."""\n        self._sync_access_cache_identity()\n        try:\n            port = int(self.port_var.get().strip())\n        except ValueError as error:\n            raise ValueError("CDP Port phải là số nguyên hợp lệ.") from error\n\n        url = self.url_var.get().strip()\n        if not url:\n            raise ValueError("URL VNEDU không được để trống.")\n        return VnEduScoreAutomation(debug_port=port, target_url=url)\n\n    def _log(self, message: str) -> None:\n        """Stores one timestamped diagnostic line without rendering a GUI log panel."""\n        timestamp = datetime.now().strftime("%H:%M:%S")\n        line = f"[{timestamp}] {message}"\n        self._log_history.append(line)\n        if len(self._log_history) > 300:\n            self._log_history = self._log_history[-300:]\n        print(line)\n\n    def _apply_password_visibility(self) -> None:\n        """Toggles whether the password entry reveals the current VNEDU password."""\n        show_value = password_entry_show_value(bool(self.show_password_var.get()))\n        try:\n            self.password_entry.configure(show=show_value)\n        except (AttributeError, tk.TclError):\n            return\n\n    def _on_progress_canvas_configure(self, _event: tk.Event | None = None) -> None:\n        """Re-renders the progress canvas whenever its available width changes."""\n        self._render_progress_bar()\n\n    def _render_progress_bar(self) -> None:\n        """Draws the custom green progress bar together with the latest caption."""\n        canvas = getattr(self, "progress_canvas", None)\n        if canvas is None:\n            return\n        try:\n            canvas_width = max(int(canvas.winfo_width()), 1)\n            canvas_height = max(int(canvas.winfo_height()), 1)\n        except tk.TclError:\n            return\n\n        fill_width = int((canvas_width - 2) * (clamp_progress_value(self._progress_value) / 100.0))\n        canvas.coords(\n            self._progress_fill_id,\n            1,\n            1,\n            1 + max(fill_width, 0),\n            max(canvas_height - 1, 1),\n        )\n        canvas.itemconfigure(\n            self._progress_fill_id,\n            fill=("#2fa34a" if self._progress_value > 0 else "#dfe9df"),\n        )\n        canvas.coords(self._progress_text_id, 8, canvas_height / 2)\n        canvas.itemconfigure(\n            self._progress_text_id,\n            text=build_progress_caption(self._progress_value, self._progress_message),\n        )\n\n    def _set_status_text(self, status_text: str, sync_progress_caption: bool = True) -> None:\n        """Updates the status line and optionally keeps the progress caption in sync with it."""\n        normalized_text = str(status_text or "").strip()\n        if not normalized_text:\n            return\n        self.status_var.set(normalized_text)\n        if sync_progress_caption:\n            self._progress_message = normalized_text\n            self._render_progress_bar()\n\n    def _set_progress(self, value: float, status_text: str = "") -> None:\n        """Updates the visible progress bar and optionally mirrors the text into the status line."""\n        self._progress_value = clamp_progress_value(value)\n        if status_text:\n            self._set_status_text(status_text, sync_progress_caption=True)\n        self._render_progress_bar()\n\n    def _make_progress_reporter(self, task_token: int) -> ProgressCallback:\n        """Creates one thread-safe reporter that forwards worker progress back to the Tk thread."""\n\n        def report(value: float, message: str = "") -> None:\n            self._progress_queue.put((task_token, clamp_progress_value(value), str(message or "").strip()))\n\n        return report\n\n    def _set_busy(self, is_busy: bool, status_text: str = "") -> None:\n        """Freezes interactive controls while a background automation task is running."""\n        self._busy = bool(is_busy)\n        for widget, normal_state in self._busy_widgets:\n            try:\n                widget.configure(state=("disabled" if is_busy else normal_state))\n            except tk.TclError:\n                continue\n\n        try:\n            self.root.configure(cursor="watch" if is_busy else "")\n        except tk.TclError:\n            pass\n\n        if status_text:\n            self._set_progress(self._progress_value if not is_busy else 0.0, status_text)\n\n    def _cancel_pending_context_apply(self) -> None:\n        """Cancels one queued auto-apply request for the scorebook context selectors."""\n        after_id = getattr(self, "_context_apply_after_id", None)\n        if not after_id:\n            return\n        self._context_apply_after_id = None\n        try:\n            self.root.after_cancel(after_id)\n        except (tk.TclError, AttributeError):\n            return\n\n    def _selected_context_matches_current_context(self) -> bool:\n        """Checks whether the GUI comboboxes still point to the current live scorebook context."""\n        if self.current_context is None:\n            return True\n        selected_grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id)\n        selected_class_id = self._selected_option_id(self.class_var, self.class_label_to_id)\n        selected_subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id)\n        selected_term_id = self._selected_option_id(self.term_var, self.term_label_to_id)\n        return (\n            selected_grade_id == self.current_context.selected_grade_id\n            and selected_class_id == self.current_context.selected_class_id\n            and selected_subject_id == self.current_context.selected_subject_id\n            and selected_term_id == self.current_context.selected_term_id\n        )\n\n    def _schedule_context_apply(self) -> None:\n        """Debounces UI selection changes so one burst of combobox edits triggers one live apply."""\n        self._cancel_pending_context_apply()\n        try:\n            self._context_apply_after_id = self.root.after(\n                self._context_apply_delay_ms,\n                self._run_debounced_context_apply,\n            )\n        except (tk.TclError, AttributeError):\n            self._context_apply_after_id = None\n            self.on_apply_selected_context()\n\n    def _run_debounced_context_apply(self) -> None:\n        """Runs one delayed context apply if the UI selection is still different from live context."""\n        self._context_apply_after_id = None\n        if self._suspend_context_events or self._busy or self.current_context is None:\n            return\n        if self._selected_context_matches_current_context():\n            return\n        self.on_apply_selected_context()\n\n    def _run_background_task(\n        self,\n        busy_text: str,\n        worker: Callable[[ProgressCallback], object],\n        on_success: Callable[[object], None],\n        on_error: Callable[[Exception], None] | None = None,\n    ) -> None:\n        """Runs one long browser automation task off the Tk main thread."""\n        if self._busy:\n            self._log("Đang có tác vụ khác chạy, bỏ qua lệnh mới.")\n            return\n\n        self._cancel_pending_context_apply()\n        self._foreground_task_token += 1\n        self._set_busy(True, busy_text)\n        task_token = self._foreground_task_token\n        progress_reporter = self._make_progress_reporter(task_token)\n        progress_reporter(2.0, busy_text)\n\n        def background_worker() -> None:\n            result: object | None = None\n            captured_error: Exception | None = None\n            try:\n                with self._automation_lock:\n                    result = worker(progress_reporter)\n            except Exception as error:  # noqa: BLE001 - UI thread will handle the result\n                captured_error = error\n            self._result_queue.put((result, captured_error, on_success, on_error))\n\n        threading.Thread(target=background_worker, daemon=True).start()\n\n    def _run_passive_background_task(\n        self,\n        worker: Callable[[], object],\n        on_success: Callable[[object], None],\n        on_error: Callable[[Exception], None] | None = None,\n    ) -> None:\n        """Runs a non-blocking background task that does not freeze the main UI."""\n\n        def background_worker() -> None:\n            result: object | None = None\n            captured_error: Exception | None = None\n            try:\n                result = worker()\n            except Exception as error:  # noqa: BLE001 - main thread callback will handle the result\n                captured_error = error\n            self._passive_result_queue.put((result, captured_error, on_success, on_error))\n\n        threading.Thread(target=background_worker, daemon=True).start()\n\n    def _access_scan_block_reason(\n        self,\n        scan_key: Tuple[str, str, str, str],\n        expected_task_token: int,\n    ) -> str:\n        """Explains why one passive access scan should no longer touch the live UI/browser state."""\n        if expected_task_token != self._foreground_task_token:\n            return "đã có tác vụ chính mới"\n        if self._busy:\n            return "đang có tác vụ chính chạy"\n        if self.current_context is None:\n            return "không còn ngữ cảnh hiện tại"\n        current_scan_key = self._access_cache_key(\n            self.current_context.selected_grade_id,\n            self.current_context.selected_term_id,\n        )\n        if current_scan_key != scan_key:\n            return "ngữ cảnh hiện tại đã đổi"\n        return ""\n\n    def _poll_background_results(self) -> None:\n        """Processes background worker results back on the Tk main thread."""\n        try:\n            while True:\n                task_token, progress_value, progress_message = self._progress_queue.get_nowait()\n                if task_token == self._foreground_task_token and self._busy:\n                    self._set_progress(progress_value, progress_message)\n        except Empty:\n            pass\n\n        try:\n            while True:\n                result, error, on_success, on_error = self._passive_result_queue.get_nowait()\n                try:\n                    if error is not None:\n                        if on_error is not None:\n                            on_error(error)\n                    else:\n                        on_success(result)\n                except Exception as callback_error:  # noqa: BLE001 - passive callback guard\n                    self._log(f"Lỗi callback nền: {callback_error}")\n        except Empty:\n            pass\n\n        try:\n            while True:\n                result, error, on_success, on_error = self._result_queue.get_nowait()\n                self._set_busy(False)\n                try:\n                    if error is not None:\n                        if on_error is not None:\n                            on_error(error)\n                        self._set_progress(0.0, self.status_var.get() or "Tác vụ thất bại")\n                    else:\n                        on_success(result)\n                        self._set_progress(100.0, self.status_var.get() or "Đã hoàn tất")\n                except Exception as callback_error:  # noqa: BLE001 - final guard for Tk callbacks\n                    messagebox.showerror("Lỗi nội bộ", str(callback_error))\n                    self._log(f"Lỗi callback UI: {callback_error}")\n        except Empty:\n            pass\n\n        try:\n            self._poll_after_id = self.root.after(50, self._poll_background_results)\n        except tk.TclError:\n            self._poll_after_id = None\n            return\n\n    def _render_option_combo(\n        self,\n        combo: ttk.Combobox,\n        variable: tk.StringVar,\n        label_map: Dict[str, str],\n        options: List[ScoreOption],\n        selected_id: str,\n        empty_label: str,\n    ) -> None:\n        """Renders one read-only combobox from typed options."""\n        label_map.clear()\n        labels: List[str] = []\n        for option in options:\n            label = option.option_text.strip() or option.option_id\n            if label in label_map:\n                label = f"{label} ({option.option_id})"\n            label_map[label] = option.option_id\n            labels.append(label)\n\n        combo["values"] = labels\n        target_label = next((label for label, option_id in label_map.items() if option_id == selected_id), "")\n        variable.set(target_label or (labels[0] if labels else empty_label))\n\n    def _selected_option_id(self, variable: tk.StringVar, label_map: Dict[str, str]) -> str:\n        """Maps the current GUI combobox label back to its underlying option id."""\n        return label_map.get(variable.get().strip(), "").strip()\n\n    def _schema_by_key(\n        self, schemas: List[ScoreColumnSchema], column_key: str\n    ) -> ScoreColumnSchema | None:\n        """Finds one parsed schema by key inside the UI layer."""\n        column_key = column_key.strip()\n        if not column_key:\n            return None\n        return next((schema for schema in schemas if schema.column_key == column_key), None)\n\n    def _score_source_candidate_schemas(\n        self,\n        schemas: List[ScoreColumnSchema],\n        detected: ScorebookDetectedColumns,\n    ) -> List[ScoreColumnSchema]:\n        """Returns score-like columns that can be used as the source for comment rules."""\n        candidate_keys = list(detected.score_candidate_keys)\n        candidate_schemas: List[ScoreColumnSchema] = []\n        seen_keys = set()\n\n        for column_key in candidate_keys:\n            schema = self._schema_by_key(schemas, column_key)\n            if schema is None or schema.column_key in seen_keys:\n                continue\n            seen_keys.add(schema.column_key)\n            candidate_schemas.append(schema)\n\n        fallback_schemas = [\n            schema\n            for schema in sorted(schemas, key=lambda item: item.leaf_index)\n            if schema.role_hint in {"score", "average"}\n        ]\n        for schema in fallback_schemas:\n            if schema.column_key in seen_keys:\n                continue\n            seen_keys.add(schema.column_key)\n            candidate_schemas.append(schema)\n        return candidate_schemas\n\n    def _schema_source_label(self, schema: ScoreColumnSchema) -> str:\n        """Builds one stable combobox label for a selectable score source column."""\n        header_text = " / ".join(\n            part.strip()\n            for part in schema.header_path\n            if part.strip()\n        )\n        display_name = schema.display_name.strip() or schema.column_key\n        if header_text and display_name not in header_text:\n            return f"{header_text} [{display_name}]"\n        return header_text or display_name\n\n    def _render_score_source_combo(\n        self,\n        schemas: List[ScoreColumnSchema],\n        detected: ScorebookDetectedColumns,\n    ) -> None:\n        """Populates the score-source combobox from the live parsed schema."""\n        selectable_schemas = self._score_source_candidate_schemas(schemas, detected)\n        previous_selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)\n        self.score_source_label_to_key.clear()\n        labels: List[str] = []\n        for schema in selectable_schemas:\n            label = self._schema_source_label(schema)\n            if label in self.score_source_label_to_key:\n                label = f"{label} ({schema.column_key})"\n            self.score_source_label_to_key[label] = schema.column_key\n            labels.append(label)\n\n        self.score_source_combo["values"] = labels\n        selected_key = ""\n        if self.score_source_label_to_key:\n            if previous_selected_key in self.score_source_label_to_key.values():\n                selected_key = previous_selected_key\n            elif self.preferred_score_source_key.strip() in self.score_source_label_to_key.values():\n                selected_key = self.preferred_score_source_key.strip()\n            elif detected.preferred_score_column_key.strip() in self.score_source_label_to_key.values():\n                selected_key = detected.preferred_score_column_key.strip()\n            else:\n                selected_key = next(iter(self.score_source_label_to_key.values()), "")\n\n        target_label = next(\n            (label for label, column_key in self.score_source_label_to_key.items() if column_key == selected_key),\n            "",\n        )\n        self.score_source_var.set(target_label or "(chưa dò)")\n        self.preferred_score_source_key = selected_key\n\n    def _bind_config_autosave_traces(self) -> None:\n        """Binds lightweight autosave traces for config-backed UI fields."""\n        self.num_forms_var.trace_add("write", self._schedule_config_autosave)\n        self.auto_save_var.trace_add("write", self._schedule_config_autosave)\n        self.allow_comment_overwrite_var.trace_add("write", self._schedule_config_autosave)\n        self.show_password_var.trace_add("write", self._schedule_config_autosave)\n\n    def _schedule_config_autosave(self, *_args: object) -> None:\n        """Debounces config writes so rule edits persist without spamming disk writes."""\n        if self._suspend_config_autosave:\n            return\n        if self._config_autosave_after_id is not None:\n            self.root.after_cancel(self._config_autosave_after_id)\n        self._config_autosave_after_id = self.root.after(700, self._flush_config_autosave)\n\n    def _flush_config_autosave(self) -> None:\n        """Writes the current config snapshot to disk for autosave-triggered changes."""\n        self._config_autosave_after_id = None\n        if self._suspend_config_autosave:\n            return\n        try:\n            self._save_config()\n        except Exception as error:  # noqa: BLE001 - autosave should not break the UI\n            self._log(f"Lỗi auto-save cấu hình: {error}")\n\n    def _render_detected_columns(\n        self,\n        schemas: List[ScoreColumnSchema],\n        detected: ScorebookDetectedColumns,\n    ) -> None:\n        """Shows the auto-detected score/comment columns in the GUI."""\n        score_schema = self._schema_by_key(schemas, detected.preferred_score_column_key)\n        comment_schema = self._schema_by_key(schemas, detected.preferred_comment_column_key)\n\n        self.detected_score_var.set(\n            self._schema_source_label(score_schema) if score_schema is not None else "(chưa xác định)"\n        )\n        self.detected_comment_var.set(\n            self._schema_source_label(comment_schema) if comment_schema is not None else "(chưa xác định)"\n        )\n\n        candidate_labels = []\n        for column_key in detected.score_candidate_keys:\n            schema = self._schema_by_key(schemas, column_key)\n            if schema is None:\n                continue\n            candidate_labels.append(self._schema_source_label(schema))\n        self.detected_candidates_var.set(", ".join(candidate_labels) if candidate_labels else "(không có)")\n        self.detected_reason_var.set(detected.preferred_score_reason or "")\n\n    def _selected_score_source_key(self, context: ScorebookContext) -> str:\n        """Returns the user-selected source score column key with safe fallbacks."""\n        selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)\n        if selected_key and self._schema_by_key(context.column_schemas, selected_key) is not None:\n            return selected_key\n\n        detected_key = context.detected_columns.preferred_score_column_key.strip()\n        if detected_key and self._schema_by_key(context.column_schemas, detected_key) is not None:\n            return detected_key\n\n        candidate_schemas = self._score_source_candidate_schemas(\n            context.column_schemas,\n            context.detected_columns,\n        )\n        if candidate_schemas:\n            return candidate_schemas[0].column_key\n        return ""\n\n    def _log_detected_columns(\n        self,\n        schemas: List[ScoreColumnSchema],\n        detected: ScorebookDetectedColumns,\n    ) -> None:\n        """Writes the auto-detected score/comment columns to the log panel."""\n        score_schema = self._schema_by_key(schemas, detected.preferred_score_column_key)\n        comment_schema = self._schema_by_key(schemas, detected.preferred_comment_column_key)\n        if score_schema is not None:\n            self._log(f"Cột điểm đề xuất: {score_schema.display_name}")\n        else:\n            self._log("Chưa auto-detect được cột điểm đề xuất.")\n        if comment_schema is not None:\n            self._log(f"Cột nhận xét đích: {comment_schema.display_name}")\n        else:\n            self._log("Chưa auto-detect được cột nhận xét.")\n        if detected.preferred_score_reason:\n            self._log(detected.preferred_score_reason)\n\n    def _seed_default_rules(self, count: int) -> List[CommentRule]:\n        """Returns a default rule template set for quick first use."""\n        defaults = [\n            CommentRule(">=8", "Hoàn thành tốt yêu cầu cần đạt của bộ môn, chủ động, tự giác trong học tập và rèn luyện."),\n            CommentRule("6.5-7.9", "Hoàn thành khá tốt nội dung kiến thức đã học, vận dụng được vào bài thực hành, chăm chỉ trong học tập."),\n            CommentRule("6-6.4", "Tiếp thu được các kiến thức cơ bản của môn học, có ý thức tự giác, tương đối chủ động trong học tập."),\n            CommentRule("5-5.9", "Hoàn thành được các yêu cầu của bộ môn, chủ động hơn trong học tập, tăng cường rèn luyện kỹ năng giải bài tập."),\n            CommentRule("<5", "Chưa hoàn thành các yêu cầu cần đạt của bộ môn, còn thụ động, tăng cường luyện tập kỹ năng thực hành."),\n            CommentRule("Đ", "Hoàn thành tốt yêu cầu cần đạt của bộ môn, chủ động, tự giác trong học tập và rèn luyện."),\n            CommentRule("CĐ", "Chưa hoàn thành tốt nội dung kiến thức môn học."),\n        ]\n        return defaults[:count]\n\n    def _sanitize_rule_filename_part(self, value: str) -> str:\n        """Converts one rule label into a Windows-safe filename fragment."""\n        sanitized = value.strip()\n        replacements = (\n            (">=", "ge_"),\n            ("<=", "le_"),\n            (">", "gt_"),\n            ("<", "lt_"),\n            ("=", "eq_"),\n        )\n        for old, new in replacements:\n            sanitized = sanitized.replace(old, new)\n        sanitized = re.sub(r\'[<>:"/\\\\\\\\|?*]+\', "_", sanitized)\n        sanitized = re.sub(r"\\s+", "_", sanitized)\n        sanitized = sanitized.strip("._")\n        return sanitized or "rule"\n\n    def _write_default_rule_files(self, target_dir: Path | None = None) -> List[Path]:\n        """Writes the built-in default rule set into seven plain-text files."""\n        export_dir = target_dir or DEFAULT_RULE_EXPORT_DIR\n        export_dir.mkdir(parents=True, exist_ok=True)\n\n        rules = self._seed_default_rules(7)\n        created_files: List[Path] = []\n        for index, rule in enumerate(rules, start=1):\n            filename = f"{index:02d}_{self._sanitize_rule_filename_part(rule.condition)}.txt"\n            file_path = export_dir / filename\n            content = (\n                f"STT: {index}\\n"\n                f"Điều kiện: {rule.condition}\\n"\n                "Mẫu nhận xét:\\n"\n                f"{rule.template}\\n"\n            )\n            file_path.write_text(content, encoding="utf-8")\n            created_files.append(file_path)\n        return created_files\n\n    def _build_rule_forms(self, count: int, initial_rules: List[CommentRule] | None = None) -> None:\n        """Rebuilds the rule form list inside the scrollable rule panel."""\n        if self._rule_busy_widgets:\n            self._busy_widgets = [\n                item for item in self._busy_widgets if item[0] not in self._rule_busy_widgets\n            ]\n            self._rule_busy_widgets = []\n        for widget in self.rule_form_container.winfo_children():\n            widget.destroy()\n        self.score_forms = []\n\n        header = ttk.Frame(self.rule_form_container)\n        header.pack(fill=tk.X, pady=(0, 4))\n        ttk.Label(header, text="STT", width=6).pack(side=tk.LEFT, padx=(0, 4))\n        ttk.Label(header, text="Điều kiện", width=18).pack(side=tk.LEFT, padx=(0, 4))\n        ttk.Label(header, text="Mẫu nhận xét", anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)\n\n        seeded_rules = list(initial_rules or self._seed_default_rules(count))\n        for index in range(count):\n            row_frame = ttk.Frame(self.rule_form_container)\n            row_frame.pack(fill=tk.X, pady=2)\n            ttk.Label(row_frame, text=f"{index + 1}", width=6).pack(side=tk.LEFT, padx=(0, 4))\n\n            condition_var = tk.StringVar()\n            template_var = tk.StringVar()\n            condition_entry = ttk.Entry(row_frame, textvariable=condition_var, width=18)\n            template_entry = ttk.Entry(row_frame, textvariable=template_var)\n            condition_entry.pack(side=tk.LEFT, padx=(0, 4))\n            template_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)\n\n            self.score_forms.append({"condition": condition_var, "template": template_var})\n            self._busy_widgets.append((condition_entry, "normal"))\n            self._busy_widgets.append((template_entry, "normal"))\n            self._rule_busy_widgets.extend([condition_entry, template_entry])\n            condition_var.trace_add("write", self._schedule_config_autosave)\n            template_var.trace_add("write", self._schedule_config_autosave)\n\n            if index < len(seeded_rules):\n                condition_var.set(seeded_rules[index].condition)\n                template_var.set(seeded_rules[index].template)\n\n    def on_build_rule_forms(self) -> None:\n        """Rebuilds the rule forms from the requested form count."""\n        existing_rules = self._collect_comment_rules()\n        try:\n            count = int(self.num_forms_var.get().strip())\n        except ValueError:\n            messagebox.showwarning("Thiếu rule", "Số rule phải là số nguyên hợp lệ.")\n            self._log("Bỏ qua tạo form rule vì số lượng rule không hợp lệ.")\n            return\n        if count < 1 or count > 20:\n            messagebox.showwarning("Thiếu rule", "Số rule phải nằm trong khoảng 1-20.")\n            self._log("Bỏ qua tạo form rule vì số lượng nằm ngoài khoảng 1-20.")\n            return\n        self._build_rule_forms(count, initial_rules=existing_rules)\n        self._schedule_config_autosave()\n        self._log(f"Đã tạo {count} form rule.")\n\n    def on_export_default_rules(self) -> None:\n        """Restores the built-in default rule set and clears any saved custom config."""\n        default_count = 7\n        default_rules = self._seed_default_rules(default_count)\n        try:\n            if self._config_autosave_after_id is not None:\n                self.root.after_cancel(self._config_autosave_after_id)\n            self._config_autosave_after_id = None\n            self._suspend_config_autosave = True\n            self.num_forms_var.set(str(default_count))\n            self._build_rule_forms(default_count, initial_rules=default_rules)\n            if CONFIG_FILE.exists():\n                CONFIG_FILE.unlink()\n        except Exception as error:  # noqa: BLE001 - user-facing reset action\n            messagebox.showerror("Lỗi nhận xét mặc định", str(error))\n            self._log(f"Lỗi khôi phục nhận xét mặc định: {error}")\n            return\n        finally:\n            self._suspend_config_autosave = False\n\n        self._log("Đã khôi phục 7 rule nhận xét mặc định trong GUI.")\n        self._log("Đã xóa file cấu hình tùy biến; lần mở sau app sẽ dùng lại rule mặc định cho đến khi bạn chỉnh sửa.")\n        messagebox.showinfo(\n            "Nhận xét mặc định",\n            (\n                "Đã khôi phục 7 rule nhận xét mặc định.\\n"\n                "Config tùy biến cũ đã được xóa. Khi bạn sửa rule sau đó, app sẽ tự tạo lại config."\n            ),\n        )\n\n    def _collect_comment_rules(self) -> List[CommentRule]:\n        """Collects the current rule form values from the GUI."""\n        return [\n            CommentRule(\n                condition=form["condition"].get().strip(),\n                template=form["template"].get().strip(),\n            )\n            for form in self.score_forms\n        ]\n\n    def _warn_rule_overlap(self, rules: List[CommentRule]) -> None:\n        """Warns when numeric test points match more than one rule."""\n        compiled_rules = compile_comment_rules(rules)\n        if not compiled_rules:\n            return\n        overlaps: List[str] = []\n        for probe in [0, 1, 2, 3, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10]:\n            matched_conditions = []\n            for checker, _template, condition in compiled_rules:\n                try:\n                    if checker(probe):\n                        matched_conditions.append(condition)\n                except TypeError:\n                    continue\n            if len(matched_conditions) > 1:\n                overlaps.append(f"{probe}: {\', \'.join(matched_conditions)}")\n        if overlaps:\n            messagebox.showwarning(\n                "Cảnh báo rule chồng lấn",\n                "Một số điểm khớp nhiều rule. App sẽ lấy rule đầu tiên khớp từ trên xuống.\\n\\n"\n                + "\\n".join(overlaps[:6]),\n            )\n\n    def _validate_comment_rules(self) -> List[CommentRule]:\n        """Validates the current rule forms and returns typed rules."""\n        rules = self._collect_comment_rules()\n        if not rules:\n            raise RuntimeError("Chưa có form rule nào.")\n\n        for index, rule in enumerate(rules, start=1):\n            if not rule.condition.strip():\n                raise RuntimeError(f"Rule {index}: chưa nhập điều kiện.")\n            if not rule.template.strip():\n                raise RuntimeError(f"Rule {index}: chưa nhập mẫu nhận xét.")\n            if parse_condition(rule.condition) is None:\n                raise RuntimeError(\n                    f"Rule {index}: điều kiện `{rule.condition}` không hợp lệ ({describe_condition(rule.condition)})."\n                )\n\n        self._warn_rule_overlap(rules)\n        return rules\n\n    def _write_status_label(self, status: str) -> str:\n        """Maps internal write-queue status codes to short Vietnamese labels."""\n        return {\n            "ready": "Sẵn sàng",\n            "skip_no_score": "Chưa có điểm",\n            "skip_comment_score": "Đã có điểm nhận xét",\n            "skip_existing_comment": "Đã có nhận xét chữ",\n            "skip_unmatched": "Không khớp rule",\n            "skip_same": "Đã giống",\n        }.get(status, status)\n\n    def _write_status_counts(self, rows: List[CommentWriteRow]) -> Dict[str, int]:\n        """Aggregates analyzed write rows by internal status code."""\n        counts: Dict[str, int] = {}\n        for row in rows:\n            counts[row.status] = counts.get(row.status, 0) + 1\n        return counts\n\n    def _warn_students_with_numeric_comment_scores(\n        self,\n        rows: List[CommentWriteRow],\n        allow_overwrite_existing_comment: bool,\n    ) -> None:\n        """Shows a warning when some comment cells already contain numeric score values."""\n        if allow_overwrite_existing_comment:\n            return\n        blocked_rows = [row for row in rows if row.status == "skip_comment_score"]\n        if not blocked_rows:\n            return\n\n        blocked_lines = [\n            f"{row.student_name or \'(không rõ tên)\'}"\n            f"{f\' ({row.student_code})\' if row.student_code else \'\'}: {row.current_comment}"\n            for row in blocked_rows[:12]\n        ]\n        more_count = len(blocked_rows) - len(blocked_lines)\n        message = "Tên học sinh đã có điểm nhận xét:\\n" + "\\n".join(blocked_lines)\n        if more_count > 0:\n            message += f"\\n... và thêm {more_count} học sinh khác."\n        messagebox.showwarning("Đã có điểm nhận xét", message)\n\n    def _context_has_comment_permission(self, context: ScorebookContext) -> bool:\n        """Returns whether the current scorebook context allows comment editing."""\n        normalized_permission = context.permission_text.strip().lower()\n        if "không có quyền" in normalized_permission or "khong co quyen" in normalized_permission:\n            return False\n        return context.enabled_comment_input_count > 0\n\n    def _schedule_access_scan_for_context(\n        self,\n        context: ScorebookContext,\n        reason: str,\n    ) -> None:\n        """Starts a passive permission scan for the current grade-term so filtered lists can update later."""\n        if not getattr(self, "_matrix_access_scan_enabled", True):\n            return\n        self._sync_access_cache_identity()\n        scan_key = self._access_cache_key(context.selected_grade_id, context.selected_term_id)\n        scan_grade_id = context.selected_grade_id.strip()\n        scan_term_id = context.selected_term_id.strip()\n        if not scan_grade_id or not scan_term_id:\n            return\n        if scan_key in self.access_entry_cache:\n            return\n        if scan_key in self._access_scan_inflight:\n            return\n\n        try:\n            automation = self._build_automation()\n        except Exception as error:  # noqa: BLE001 - background warmup should stay non-blocking\n            self._log(f"Bỏ qua dò quyền nền: {error}")\n            return\n\n        username = self.username_var.get().strip()\n        password = self.password_var.get()\n        expected_task_token = self._foreground_task_token\n        self._access_scan_inflight.add(scan_key)\n        self._log(\n            f"Đang dò quyền lớp/môn nền cho khối {context.selected_grade_id}, học kỳ {context.selected_term_id}"\n            f" ({reason})..."\n        )\n\n        def worker() -> object:\n            if self._access_scan_block_reason(scan_key, expected_task_token):\n                return PASSIVE_SCAN_SKIPPED\n            if not self._automation_lock.acquire(blocking=False):\n                return PASSIVE_SCAN_SKIPPED\n            try:\n                if self._access_scan_block_reason(scan_key, expected_task_token):\n                    return PASSIVE_SCAN_SKIPPED\n                return automation.discover_accessible_entries_for_current_context(\n                    username=username,\n                    password=password,\n                    expected_grade_id=scan_grade_id,\n                    expected_term_id=scan_term_id,\n                )\n            finally:\n                self._automation_lock.release()\n\n        def on_success(result: object) -> None:\n            self._access_scan_inflight.discard(scan_key)\n            skip_reason = self._access_scan_block_reason(scan_key, expected_task_token)\n            if result is PASSIVE_SCAN_SKIPPED:\n                if skip_reason:\n                    self._log(\n                        f"Bỏ qua dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."\n                    )\n                return\n            if skip_reason:\n                self._log(\n                    f"Bỏ qua cập nhật kết quả dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."\n                )\n                return\n            scanned_context, entries, login_message = result if isinstance(result, tuple) else (None, None, "")\n            if not isinstance(scanned_context, ScorebookContext) or not isinstance(entries, list):\n                raise RuntimeError("Không nhận được dữ liệu quét quyền nền hợp lệ.")\n            if login_message:\n                self._log(login_message)\n            if not entries:\n                self._log(\n                    "Dò quyền nền chưa trả về ma trận lớp/môn đầy đủ; "\n                    "app giữ danh sách live hiện tại và không cache kết quả rỗng."\n                )\n            else:\n                self.access_entry_cache[scan_key] = list(entries)\n                self._log(\n                    f"Đã cập nhật nền quyền lớp/môn cho khối {scan_grade_id}, học kỳ {scan_term_id}: "\n                    f"{len(entries)} tổ hợp hợp lệ."\n                )\n            if self.current_context is None:\n                return\n            if self._access_scan_block_reason(scan_key, expected_task_token):\n                return\n\n            scanned_context.selected_grade_id = self.current_context.selected_grade_id\n            scanned_context.selected_class_id = self.current_context.selected_class_id\n            scanned_context.selected_subject_id = self.current_context.selected_subject_id\n            scanned_context.selected_term_id = self.current_context.selected_term_id\n            if entries:\n                scanned_context = apply_access_entries_to_context(\n                    scanned_context,\n                    entries,\n                    grade_id=scan_grade_id,\n                    term_id=scan_term_id,\n                )\n            self._apply_scorebook_context(scanned_context)\n\n        def on_error(error: Exception) -> None:\n            self._access_scan_inflight.discard(scan_key)\n            self._log(f"Lỗi dò quyền nền: {error}")\n\n        def launch_passive_scan() -> None:\n            if scan_key not in self._access_scan_inflight:\n                return\n            skip_reason = self._access_scan_block_reason(scan_key, expected_task_token)\n            if skip_reason:\n                self._access_scan_inflight.discard(scan_key)\n                self._log(\n                    f"Bỏ qua khởi động dò quyền nền cho khối {scan_grade_id}, học kỳ {scan_term_id} vì {skip_reason}."\n                )\n                return\n            self._run_passive_background_task(worker=worker, on_success=on_success, on_error=on_error)\n\n        try:\n            self.root.after(self._passive_scan_delay_ms, launch_passive_scan)\n        except tk.TclError:\n            self._access_scan_inflight.discard(scan_key)\n            return\n\n    def _selected_scorebook_context_ids(self) -> Tuple[str, str, str, str]:\n        """Reads the currently selected scorebook ids from the GUI with safe fallbacks."""\n        if self.current_context is None:\n            raise RuntimeError("Chưa có ScorebookContext để xác định ngữ cảnh hiện tại.")\n\n        grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id) or self.current_context.selected_grade_id\n        class_id = self._selected_option_id(self.class_var, self.class_label_to_id) or self.current_context.selected_class_id\n        subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id) or self.current_context.selected_subject_id\n        term_id = self._selected_option_id(self.term_var, self.term_label_to_id) or self.current_context.selected_term_id\n\n        if not grade_id or not class_id or not subject_id or not term_id:\n            raise RuntimeError("Ngữ cảnh Khối/Lớp/Môn/Học kỳ chưa đầy đủ.")\n        return grade_id, class_id, subject_id, term_id\n\n    def _resolve_selected_columns(\n        self,\n        context: ScorebookContext,\n    ) -> Tuple[ScoreColumnSchema, ScoreColumnSchema]:\n        """Returns the selected source score column and detected target comment column."""\n        source_column_key = self._selected_score_source_key(context)\n        detected = context.detected_columns\n        source_schema = self._schema_by_key(context.column_schemas, source_column_key)\n        comment_schema = self._schema_by_key(context.column_schemas, detected.preferred_comment_column_key)\n        if source_schema is None:\n            raise RuntimeError("Chưa chọn được cột điểm nguồn hợp lệ trên bảng điểm hiện tại.")\n        if comment_schema is None:\n            raise RuntimeError("Chưa tự nhận diện được cột nhận xét đích trên bảng điểm hiện tại.")\n        return source_schema, comment_schema\n\n    def _background_credentials(self) -> Tuple[str, str]:\n        """Returns the current username/password pair for background automation calls."""\n        return self.username_var.get().strip(), self.password_var.get()\n\n    def _collect_comment_apply_request(self) -> Dict[str, object]:\n        """Collects the UI state required for one apply-comments run."""\n        if self.current_context is None:\n            raise RuntimeError("Hãy đọc Sổ điểm trước khi ghi nhận xét.")\n\n        automation = self._build_automation()\n        rules = self._validate_comment_rules()\n        grade_id, class_id, subject_id, term_id = self._selected_scorebook_context_ids()\n        source_schema, comment_schema = self._resolve_selected_columns(self.current_context)\n        username, password = self._background_credentials()\n        return {\n            "automation": automation,\n            "rules": rules,\n            "grade_id": grade_id,\n            "class_id": class_id,\n            "subject_id": subject_id,\n            "term_id": term_id,\n            "source_column_key": source_schema.column_key,\n            "comment_column_key": comment_schema.column_key,\n            "username": username,\n            "password": password,\n            "auto_save": bool(self.auto_save_var.get()),\n            "allow_overwrite_existing_comment": bool(self.allow_comment_overwrite_var.get()),\n        }\n\n    def _run_comment_apply(self, request: Dict[str, object]) -> object:\n        """Executes one full analyze-and-apply cycle for the current UI rule set."""\n        automation = request["automation"]\n        return automation.analyze_and_apply_comment_rows(\n            grade_id=request["grade_id"],\n            class_id=request["class_id"],\n            subject_id=request["subject_id"],\n            term_id=request["term_id"],\n            source_column_key=request["source_column_key"],\n            comment_column_key=request["comment_column_key"],\n            rules=request["rules"],\n            username=request["username"],\n            password=request["password"],\n            auto_save=bool(request["auto_save"]),\n            allow_overwrite_existing_comment=bool(request["allow_overwrite_existing_comment"]),\n            progress_callback=request.get("progress_callback"),\n        )\n\n    def _handle_comment_apply_success(\n        self,\n        result: object,\n        auto_save: bool,\n        allow_overwrite_existing_comment: bool,\n    ) -> None:\n        """Handles the UI/logging side after one apply-comments run completes."""\n        (\n            queue_context,\n            write_rows,\n            queue_login_message,\n            queue_selection_message,\n            apply_context,\n            apply_result,\n            apply_login_message,\n            apply_selection_message,\n        ) = result if isinstance(result, tuple) else (None, None, "", "", None, None, "", "")\n        if (\n            not isinstance(queue_context, ScorebookContext)\n            or not isinstance(write_rows, list)\n            or not isinstance(apply_context, ScorebookContext)\n            or (apply_result is not None and not isinstance(apply_result, CommentWriteResult))\n        ):\n            raise RuntimeError("Không nhận được kết quả ghi nhận xét hợp lệ từ live Chrome.")\n        if queue_login_message:\n            self._log(queue_login_message)\n        if queue_selection_message:\n            self._log(queue_selection_message)\n        counts = self._write_status_counts(write_rows)\n        self._warn_students_with_numeric_comment_scores(\n            write_rows,\n            allow_overwrite_existing_comment=allow_overwrite_existing_comment,\n        )\n        self._log(\n            "Đã phân tích dữ liệu trước khi ghi: "\n            f"{len(write_rows)} dòng, "\n            f"{counts.get(\'ready\', 0)} sẵn sàng, "\n            f"{counts.get(\'skip_no_score\', 0)} chưa có điểm, "\n            f"{counts.get(\'skip_existing_comment\', 0)} đã có nhận xét chữ, "\n            f"{counts.get(\'skip_unmatched\', 0)} không khớp rule, "\n            f"{counts.get(\'skip_same\', 0)} đã giống."\n        )\n        if apply_result is None:\n            self._apply_scorebook_context(apply_context)\n            no_ready_lines = [\n                "Không có học sinh nào sẵn sàng để ghi nhận xét.",\n            ]\n            if counts.get("skip_existing_comment", 0) and not allow_overwrite_existing_comment:\n                no_ready_lines.append(\n                    "Các ô đã có nhận xét chữ đang được giữ nguyên. "\n                    "Muốn thay thế có chủ đích, hãy bật \'Cho phép ghi đè nhận xét chữ đã có\'."\n                )\n            no_ready_lines.append(\n                "Hãy kiểm tra lại rule, cột điểm nguồn, hoặc các ô nhận xét chữ đã có."\n            )\n            messagebox.showwarning(\n                "Không có dòng để ghi",\n                "\\n".join(no_ready_lines),\n            )\n            self._log("Không có dòng nào sẵn sàng để ghi sau khi phân tích dữ liệu.")\n            self._set_status_text("Không có dòng để ghi")\n            return\n        if apply_login_message:\n            self._log(apply_login_message)\n        if apply_selection_message:\n            self._log(apply_selection_message)\n        self._apply_scorebook_context(apply_context)\n        if not apply_result.save_clicked and auto_save:\n            self._log("Không tìm thấy nút Lưu để bấm tự động; dữ liệu mới chỉ được xác minh trong ô nhập hiện tại.")\n        if auto_save and apply_result.save_clicked:\n            if apply_result.save_verified:\n                self._log(apply_result.save_verification_detail or "Đã xác minh Lưu thành công ở mức server-side.")\n            else:\n                self._log(\n                    "Cảnh báo xác minh Lưu: "\n                    + (apply_result.save_verification_detail or "Đã bấm Lưu nhưng chưa xác minh được phản hồi server.")\n                )\n        if not auto_save:\n            self._log("Đã ghi vào ô nhận xét nhưng chưa bấm Lưu tự động; cần kiểm tra và lưu thủ công nếu VNEDU yêu cầu.")\n\n        failed_suffix = ""\n        if apply_result.failed_rows:\n            failed_suffix = f" Lỗi/không xác minh được: {\', \'.join(apply_result.failed_rows[:5])}"\n            if len(apply_result.failed_rows) > 5:\n                failed_suffix += f" ... (+{len(apply_result.failed_rows) - 5})"\n        self._log(\n            "Kết quả ghi nhận xét: "\n            f"đã thử {apply_result.attempted}, "\n            f"điền vào {apply_result.updated}, "\n            f"xác minh {apply_result.verified}, "\n            f"bỏ qua {apply_result.skipped}."\n            f"{failed_suffix}"\n        )\n        messagebox.showinfo(\n            "Đã ghi nhận xét",\n            (\n                f"Đã thử ghi {apply_result.attempted} dòng.\\n"\n                f"Xác minh thành công: {apply_result.verified}\\n"\n                f"Bỏ qua: {apply_result.skipped}\\n"\n                f"Tự bấm Lưu: {\'Có\' if apply_result.save_clicked else \'Không\'}\\n"\n                f"Xác minh Lưu server-side: {\'Có\' if apply_result.save_verified else \'Không\'}"\n            ),\n        )\n\n    def _handle_comment_apply_error(self, error: Exception) -> None:\n        """Handles one apply-comments failure on the UI thread."""\n        messagebox.showerror("Lỗi ghi nhận xét", str(error))\n        self._log(f"Lỗi ghi nhận xét: {error}")\n        self._set_status_text("Ghi nhận xét thất bại")\n\n    def on_apply_comments(self) -> None:\n        """Builds the write queue internally and writes comments back into the live scorebook."""\n        if self.current_context is None:\n            messagebox.showwarning("Chưa có dữ liệu", "Hãy đọc Sổ điểm trước khi ghi nhận xét.")\n            self._log("Bỏ qua ghi nhận xét vì chưa có ScorebookContext.")\n            return\n\n        try:\n            request = self._collect_comment_apply_request()\n        except Exception as error:  # noqa: BLE001 - validation path\n            messagebox.showerror("Lỗi ghi nhận xét", str(error))\n            self._log(f"Lỗi ghi nhận xét: {error}")\n            return\n\n        self._run_background_task(\n            "Đang phân tích dữ liệu và ghi nhận xét lên live Chrome...",\n            worker=lambda progress: self._run_comment_apply(\n                {\n                    **request,\n                    "progress_callback": progress,\n                }\n            ),\n            on_success=lambda result: self._handle_comment_apply_success(\n                result,\n                auto_save=bool(request["auto_save"]),\n                allow_overwrite_existing_comment=bool(request["allow_overwrite_existing_comment"]),\n            ),\n            on_error=self._handle_comment_apply_error,\n        )\n\n    def _normalize_access_cache_url(self, target_url: str) -> str:\n        """Normalizes the VNEDU target URL so equivalent session URLs share one cache namespace."""\n        normalized_url = target_url.strip()\n        if not normalized_url:\n            return ""\n        parsed = urlparse(normalized_url)\n        if parsed.scheme or parsed.netloc:\n            normalized_path = parsed.path.rstrip("/")\n            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{normalized_path}"\n        return normalized_url.rstrip("/").lower()\n\n    def _current_access_cache_identity(self) -> Tuple[str, str]:\n        """Returns the current in-memory cache identity derived from URL and username fields."""\n        current_url = self._normalize_access_cache_url(self.url_var.get())\n        current_username = self.username_var.get().strip()\n        return current_url, current_username\n\n    def _sync_access_cache_identity(self) -> Tuple[str, str]:\n        """Clears stale permission cache state when the active VNEDU URL or username changes."""\n        current_identity = self._current_access_cache_identity()\n        previous_identity = self._access_cache_identity\n        if previous_identity is None:\n            self._access_cache_identity = current_identity\n            return current_identity\n        if previous_identity != current_identity:\n            self.access_entry_cache.clear()\n            self._access_scan_inflight.clear()\n            self._access_cache_identity = current_identity\n        return current_identity\n\n    def _access_cache_key(self, grade_id: str, term_id: str) -> Tuple[str, str, str, str]:\n        """Builds the cache key for one scanned grade-term permission matrix within one VNEDU session identity."""\n        current_url, current_username = self._access_cache_identity or ("", "")\n        return current_url, current_username, grade_id.strip(), term_id.strip()\n\n    def _cache_context_access_entries(self, context: ScorebookContext) -> None:\n        """Stores discovered permission entries so grade-term scans can be reused later."""\n        if not getattr(self, "_matrix_access_scan_enabled", True):\n            return\n        self._sync_access_cache_identity()\n        if not context.accessible_entries:\n            return\n        cache_grade_id = context.accessible_grade_id or context.selected_grade_id\n        cache_term_id = context.accessible_term_id or context.selected_term_id\n        if not cache_grade_id or not cache_term_id:\n            return\n        cache_key = self._access_cache_key(\n            cache_grade_id,\n            cache_term_id,\n        )\n        self.access_entry_cache[cache_key] = list(context.accessible_entries)\n\n    def _with_cached_access_entries(self, context: ScorebookContext) -> ScorebookContext:\n        """Reattaches cached permission entries to a plain scorebook context when possible."""\n        if not getattr(self, "_matrix_access_scan_enabled", True):\n            return context\n        self._sync_access_cache_identity()\n        if context.accessible_entries:\n            return context\n        cache_key = self._access_cache_key(context.selected_grade_id, context.selected_term_id)\n        if cache_key in self.access_entry_cache:\n            cached_entries = self.access_entry_cache.get(cache_key, [])\n            if cached_entries:\n                context.class_options = merge_score_options(\n                    context.class_options,\n                    class_options_from_access_entries(cached_entries),\n                )\n                context.subject_options = merge_score_options(\n                    context.subject_options,\n                    subject_options_from_access_entries(cached_entries),\n                )\n                context.accessible_entries = list(cached_entries)\n                context.accessible_grade_id = context.selected_grade_id\n                context.accessible_term_id = context.selected_term_id\n        return context\n\n    def _build_minimal_access_entries_from_context(self, context: ScorebookContext) -> List[ScorebookAccessEntry]:\n        """Builds a one-item permission fallback from the current live context when it is clearly writable."""\n        if not self._context_has_comment_permission(context):\n            return []\n\n        grade_id = context.selected_grade_id.strip()\n        class_id = context.selected_class_id.strip()\n        subject_id = context.selected_subject_id.strip()\n        term_id = context.selected_term_id.strip()\n        if not grade_id or not class_id or not subject_id or not term_id:\n            return []\n\n        grade_text = next(\n            (option.option_text for option in context.grade_options if option.option_id == grade_id),\n            grade_id,\n        )\n        class_text = next(\n            (option.option_text for option in context.class_options if option.option_id == class_id),\n            class_id,\n        )\n        subject_text = next(\n            (option.option_text for option in context.subject_options if option.option_id == subject_id),\n            subject_id,\n        )\n        term_text = next(\n            (option.option_text for option in context.term_options if option.option_id == term_id),\n            term_id,\n        )\n\n        return [\n            ScorebookAccessEntry(\n                grade_id=grade_id,\n                grade_text=grade_text,\n                class_id=class_id,\n                class_text=class_text,\n                subject_id=subject_id,\n                subject_text=subject_text,\n                term_id=term_id,\n                term_text=term_text,\n                teacher_text=context.teacher_text,\n                permission_text=context.permission_text,\n                comment_input_count=context.comment_input_count,\n                enabled_comment_input_count=context.enabled_comment_input_count,\n            )\n        ]\n\n    def _accessible_class_options(self, context: ScorebookContext) -> List[ScoreOption]:\n        """Returns only classes that appear in the discovered permission matrix."""\n        if not context.accessible_entries and not (context.accessible_grade_id or context.accessible_term_id):\n            return list(context.class_options)\n        if not context.accessible_entries:\n            return []\n\n        allowed_ids = {\n            entry.class_id.strip()\n            for entry in context.accessible_entries\n            if entry.class_id.strip()\n        }\n        if not allowed_ids:\n            return []\n\n        return merge_score_options(\n            [option for option in context.class_options if option.option_id.strip() in allowed_ids],\n            class_options_from_access_entries(context.accessible_entries),\n        )\n\n    def _accessible_subject_options(self, context: ScorebookContext, class_id: str) -> List[ScoreOption]:\n        """Returns only subjects that are permitted for the selected class."""\n        if not context.accessible_entries and not (context.accessible_grade_id or context.accessible_term_id):\n            return list(context.subject_options)\n        if not context.accessible_entries:\n            return []\n\n        normalized_class_id = class_id.strip()\n        candidate_entries = [\n            entry\n            for entry in context.accessible_entries\n            if not normalized_class_id or entry.class_id.strip() == normalized_class_id\n        ]\n        if not candidate_entries:\n            candidate_entries = list(context.accessible_entries)\n\n        allowed_ids = {\n            entry.subject_id.strip()\n            for entry in candidate_entries\n            if entry.subject_id.strip()\n        }\n        if not allowed_ids:\n            return []\n\n        return merge_score_options(\n            [option for option in context.subject_options if option.option_id.strip() in allowed_ids],\n            subject_options_from_access_entries(candidate_entries, class_id=normalized_class_id),\n        )\n\n    def _normalize_context_for_ui_scope(self, context: ScorebookContext) -> ScorebookContext:\n        """Keeps the UI bound to the exact live context instead of any broader permission matrix."""\n        if getattr(self, "_matrix_access_scan_enabled", True):\n            return context\n        context.accessible_entries = []\n        context.accessible_grade_id = ""\n        context.accessible_term_id = ""\n        return context\n\n    def _apply_scorebook_context(self, context: ScorebookContext) -> None:\n        """Hydrates the GUI shell from the latest scorebook snapshot."""\n        context = self._normalize_context_for_ui_scope(context)\n        context = self._with_cached_access_entries(context)\n        self._cache_context_access_entries(context)\n        permission_matrix_ready = bool(\n            context.accessible_entries\n            or context.accessible_grade_id\n            or context.accessible_term_id\n        )\n        accessible_class_options = self._accessible_class_options(context)\n        selected_class_ids = {option.option_id for option in accessible_class_options}\n        if accessible_class_options and context.selected_class_id not in selected_class_ids:\n            context.selected_class_id = accessible_class_options[0].option_id\n\n        accessible_subject_options = self._accessible_subject_options(context, context.selected_class_id)\n        selected_subject_ids = {option.option_id for option in accessible_subject_options}\n        if accessible_subject_options and context.selected_subject_id not in selected_subject_ids:\n            context.selected_subject_id = accessible_subject_options[0].option_id\n\n        self.current_context = context\n        self._render_detected_columns(context.column_schemas, context.detected_columns)\n        self._suspend_context_events = True\n        try:\n            self._render_score_source_combo(context.column_schemas, context.detected_columns)\n            self._render_option_combo(\n                self.grade_combo,\n                self.grade_var,\n                self.grade_label_to_id,\n                context.grade_options,\n                context.selected_grade_id,\n                "(chưa đọc)",\n            )\n            self._render_option_combo(\n                self.class_combo,\n                self.class_var,\n                self.class_label_to_id,\n                accessible_class_options,\n                context.selected_class_id,\n                "(chưa đọc)",\n            )\n            self._render_option_combo(\n                self.subject_combo,\n                self.subject_var,\n                self.subject_label_to_id,\n                accessible_subject_options,\n                context.selected_subject_id,\n                "(chưa đọc)",\n            )\n            self._render_option_combo(\n                self.term_combo,\n                self.term_var,\n                self.term_label_to_id,\n                context.term_options,\n                context.selected_term_id,\n                "(chưa đọc)",\n            )\n        finally:\n            self._suspend_context_events = False\n        class_wording = "lớp có quyền" if permission_matrix_ready else "lớp hiện có"\n        subject_wording = "môn có quyền" if permission_matrix_ready else "môn hiện có"\n        window_title = context.window_title or "Sổ điểm"\n        self._set_status_text(\n            f"Đã đọc ngữ cảnh {window_title}: "\n            f"{len(context.grade_options)} khối, "\n            f"{len(accessible_class_options)} {class_wording}, "\n            f"{len(accessible_subject_options)} {subject_wording}, "\n            f"{len(context.term_options)} học kỳ, "\n            f"{len(context.column_schemas)} cột."\n        )\n\n    def _save_config(self) -> None:\n        """Persists session fields together with the current rule preferences."""\n        payload = {\n            "debug_port": self.port_var.get().strip(),\n            "target_url": self.url_var.get().strip(),\n            "username": self.username_var.get().strip(),\n            "num_forms": self.num_forms_var.get().strip(),\n            "auto_save": bool(self.auto_save_var.get()),\n            "allow_comment_overwrite": bool(self.allow_comment_overwrite_var.get()),\n            "show_password": bool(self.show_password_var.get()),\n            "preferred_score_source_key": self._selected_option_id(\n                self.score_source_var,\n                self.score_source_label_to_key,\n            ) or self.preferred_score_source_key,\n            "rules": [\n                {\n                    "condition": form["condition"].get().strip(),\n                    "template": form["template"].get().strip(),\n                }\n                for form in self.score_forms\n                if form["condition"].get().strip() or form["template"].get().strip()\n            ],\n        }\n        with CONFIG_FILE.open("w", encoding="utf-8") as handle:\n            json.dump(payload, handle, ensure_ascii=False, indent=2)\n\n    def _load_config(self) -> None:\n        """Loads session fields and restores the last saved rule form layout."""\n        default_count = 7\n        default_rules = self._seed_default_rules(default_count)\n        if not CONFIG_FILE.exists():\n            self.num_forms_var.set(str(default_count))\n            self._build_rule_forms(default_count, initial_rules=default_rules)\n            return\n        try:\n            with CONFIG_FILE.open("r", encoding="utf-8") as handle:\n                payload = json.load(handle)\n        except Exception as error:  # noqa: BLE001 - config corruption should not stop app startup\n            self._log(f"Không tải được cấu hình V2: {error}")\n            self.num_forms_var.set(str(default_count))\n            self._build_rule_forms(default_count, initial_rules=default_rules)\n            return\n\n        self.port_var.set(str(payload.get("debug_port", self.port_var.get())))\n        self.url_var.set(str(payload.get("target_url", self.url_var.get())))\n        self.username_var.set(str(payload.get("username", self.username_var.get())))\n        self.auto_save_var.set(bool(payload.get("auto_save", self.auto_save_var.get())))\n        self.allow_comment_overwrite_var.set(\n            bool(payload.get("allow_comment_overwrite", self.allow_comment_overwrite_var.get()))\n        )\n        self.show_password_var.set(bool(payload.get("show_password", self.show_password_var.get())))\n        self._apply_password_visibility()\n        self.preferred_score_source_key = str(payload.get("preferred_score_source_key", "")).strip()\n\n        try:\n            rule_count = int(str(payload.get("num_forms", self.num_forms_var.get())).strip())\n        except ValueError:\n            rule_count = len(payload.get("rules", payload.get("forms", []))) or default_count\n        rule_count = min(max(rule_count, 1), 20)\n        self.num_forms_var.set(str(rule_count))\n\n        raw_rules = payload.get("rules", payload.get("forms", []))\n        loaded_rules: List[CommentRule] = []\n        if isinstance(raw_rules, list):\n            for item in raw_rules:\n                if not isinstance(item, dict):\n                    continue\n                loaded_rules.append(\n                    CommentRule(\n                        condition=str(item.get("condition", "")).strip(),\n                        template=str(item.get("template", "")).strip(),\n                    )\n                )\n        self._build_rule_forms(rule_count, initial_rules=(loaded_rules or default_rules[:rule_count]))\n\n    def on_save_config(self) -> None:\n        """Saves the current CDP/session shell fields to disk."""\n        try:\n            self._save_config()\n        except Exception as error:  # noqa: BLE001 - user-facing save command\n            messagebox.showerror("Lỗi lưu", str(error))\n            self._log(f"Lỗi lưu cấu hình: {error}")\n            return\n        messagebox.showinfo("Đã lưu", f"Đã lưu cấu hình vào {CONFIG_FILE}")\n        self._log(f"Đã lưu cấu hình vào {CONFIG_FILE}")\n\n    def on_open_web(self) -> None:\n        """Opens the VNEDU target URL in the connected browser session."""\n        try:\n            automation = self._build_automation()\n        except Exception as error:  # noqa: BLE001 - validation path\n            messagebox.showerror("Lỗi mở web", str(error))\n            self._log(f"Lỗi mở web: {error}")\n            return\n\n        self._run_background_task(\n            "Đang mở VNEDU qua live Chrome...",\n            worker=lambda progress: automation.open_target_page(progress_callback=progress),\n            on_success=lambda result: (\n                self._set_status_text(f"Đã mở trang: {result}"),\n                self._log(f"Đã mở VNEDU: {result}"),\n            ),\n            on_error=lambda error: (\n                messagebox.showerror("Lỗi mở web", str(error)),\n                self._log(f"Lỗi mở web: {error}"),\n                self._set_status_text("Lỗi mở VNEDU"),\n            ),\n        )\n\n    def _handle_load_scorebook_shell_success(self, result: object) -> None:\n        """Applies one freshly loaded live scorebook context onto the UI."""\n        context, login_message = result if isinstance(result, tuple) else (None, "")\n        if not isinstance(context, ScorebookContext):\n            raise RuntimeError("Không nhận được ScorebookContext hợp lệ.")\n        if login_message:\n            self._log(login_message)\n        self._apply_scorebook_context(context)\n        if context.permission_text:\n            self._log(context.permission_text)\n        if context.teacher_text:\n            self._log(context.teacher_text)\n        self._log_detected_columns(context.column_schemas, context.detected_columns)\n        self._log("Đã đọc nhanh khung Khối/Lớp/Môn/Học kỳ từ live Chrome.")\n\n    def _handle_load_scorebook_shell_error(self, error: Exception) -> None:\n        """Handles one scorebook-shell load failure on the UI thread."""\n        messagebox.showerror("Lỗi đọc Sổ điểm", str(error))\n        self._log(f"Lỗi đọc Sổ điểm: {error}")\n        self._set_status_text("Chưa đọc được Sổ điểm")\n\n    def on_load_scorebook_shell(self) -> None:\n        """Logs in if needed, then reads the scorebook shell from the live browser."""\n        try:\n            automation = self._build_automation()\n        except Exception as error:  # noqa: BLE001 - validation path\n            messagebox.showerror("Lỗi đọc Sổ điểm", str(error))\n            self._log(f"Lỗi đọc Sổ điểm: {error}")\n            return\n\n        username, password = self._background_credentials()\n\n        self._run_background_task(\n            "Đang đăng nhập và đọc nhanh Sổ điểm...",\n            worker=lambda progress: automation.load_scorebook_context(\n                username=username,\n                password=password,\n                progress_callback=progress,\n            ),\n            on_success=self._handle_load_scorebook_shell_success,\n            on_error=self._handle_load_scorebook_shell_error,\n        )\n\n    def on_context_selection_changed(self, _event: tk.Event | None = None) -> None:\n        """Auto-applies context changes when the user picks a different combobox value."""\n        if self._suspend_context_events or self._busy or self.current_context is None:\n            return\n\n        if self._selected_context_matches_current_context():\n            self._cancel_pending_context_apply()\n            return\n\n        self._schedule_context_apply()\n\n    def on_score_source_selection_changed(self, _event: tk.Event | None = None) -> None:\n        """Keeps the chosen score source column for the next direct write run."""\n        if self._suspend_context_events or self.current_context is None:\n            return\n        selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)\n        self.preferred_score_source_key = selected_key\n        self._schedule_config_autosave()\n        if not selected_key:\n            return\n        selected_schema = self._schema_by_key(self.current_context.column_schemas, selected_key)\n        if selected_schema is not None:\n            self._log(f"Đã chọn cột điểm dùng để xét: {selected_schema.display_name}")\n\n    def _collect_selected_context_apply_request(self) -> Dict[str, object]:\n        """Collects the current UI selection and cache state for one live context apply run."""\n        if self.current_context is None:\n            raise RuntimeError("Hãy đọc Sổ điểm trước khi áp ngữ cảnh.")\n\n        automation = self._build_automation()\n        requested_grade_id = self._selected_option_id(self.grade_var, self.grade_label_to_id)\n        requested_class_id = self._selected_option_id(self.class_var, self.class_label_to_id)\n        requested_subject_id = self._selected_option_id(self.subject_var, self.subject_label_to_id)\n        requested_term_id = self._selected_option_id(self.term_var, self.term_label_to_id)\n        username, password = self._background_credentials()\n        requested_scan_key = self._access_cache_key(\n            requested_grade_id or self.current_context.selected_grade_id,\n            requested_term_id or self.current_context.selected_term_id,\n        )\n        requested_scan_grade_id = requested_grade_id or self.current_context.selected_grade_id\n        requested_scan_term_id = requested_term_id or self.current_context.selected_term_id\n        matrix_access_scan_enabled = getattr(self, "_matrix_access_scan_enabled", True)\n        has_cached_matrix = matrix_access_scan_enabled and requested_scan_key in self.access_entry_cache\n        cached_entries = (\n            list(self.access_entry_cache.get(requested_scan_key, []))\n            if matrix_access_scan_enabled and has_cached_matrix\n            else []\n        )\n\n        fallback_notes: List[str] = []\n        effective_class_id = requested_class_id\n        effective_subject_id = requested_subject_id\n        if cached_entries:\n            effective_class_id, effective_subject_id, fallback_notes = resolve_accessible_selection(\n                cached_entries,\n                preferred_class_id=requested_class_id,\n                preferred_subject_id=requested_subject_id,\n            )\n\n        return {\n            "automation": automation,\n            "requested_grade_id": requested_grade_id,\n            "requested_class_id": requested_class_id,\n            "requested_subject_id": requested_subject_id,\n            "requested_term_id": requested_term_id,\n            "effective_class_id": effective_class_id,\n            "effective_subject_id": effective_subject_id,\n            "requested_scan_key": requested_scan_key,\n            "requested_scan_grade_id": requested_scan_grade_id,\n            "requested_scan_term_id": requested_scan_term_id,\n            "has_cached_matrix": has_cached_matrix,\n            "cached_entries": cached_entries,\n            "fallback_notes": fallback_notes,\n            "previous_context": self.current_context,\n            "username": username,\n            "password": password,\n        }\n\n    def _run_selected_context_apply(self, request: Dict[str, object]) -> object:\n        """Executes one live scorebook context apply request from the UI layer."""\n        automation = request["automation"]\n        return automation.select_scorebook_context(\n            grade_id=request["requested_grade_id"],\n            class_id=request["effective_class_id"],\n            subject_id=request["effective_subject_id"],\n            term_id=request["requested_term_id"],\n            username=request["username"],\n            password=request["password"],\n            progress_callback=request.get("progress_callback"),\n        )\n\n    def _handle_selected_context_apply_success(\n        self,\n        result: object,\n        request: Dict[str, object],\n    ) -> None:\n        """Handles one successful live context apply back on the UI thread."""\n        context, login_message, selection_message = result if isinstance(result, tuple) else (None, "", "")\n        if not isinstance(context, ScorebookContext):\n            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi áp ngữ cảnh.")\n        if getattr(self, "_matrix_access_scan_enabled", True) and request["has_cached_matrix"] and not (\n            context.accessible_entries\n            or context.accessible_grade_id\n            or context.accessible_term_id\n        ):\n            context.accessible_entries = list(request["cached_entries"])\n            context.accessible_grade_id = request["requested_scan_grade_id"]\n            context.accessible_term_id = request["requested_scan_term_id"]\n        if login_message:\n            self._log(login_message)\n        if selection_message:\n            self._log(selection_message)\n        for note in request["fallback_notes"]:\n            self._log(note)\n        self._apply_scorebook_context(context)\n        if context.permission_text:\n            self._log(context.permission_text)\n        if context.teacher_text:\n            self._log(context.teacher_text)\n        self._log_detected_columns(context.column_schemas, context.detected_columns)\n        self._log("Đã áp ngữ cảnh Khối/Lớp/Môn/Học kỳ lên live Chrome.")\n\n    def _handle_selected_context_apply_error(\n        self,\n        error: Exception,\n        previous_context: ScorebookContext | None,\n    ) -> None:\n        """Handles one apply-context failure and restores the previous UI context when possible."""\n        if previous_context is not None:\n            self._apply_scorebook_context(previous_context)\n        messagebox.showerror("Lỗi áp ngữ cảnh", str(error))\n        self._log(f"Lỗi áp ngữ cảnh: {error}")\n        self._set_status_text("Áp ngữ cảnh thất bại")\n\n    def on_apply_selected_context(self) -> None:\n        """Applies the current GUI combo selections back to the live VNEDU scorebook."""\n        self._cancel_pending_context_apply()\n        if self.current_context is None:\n            messagebox.showwarning("Chưa có dữ liệu", "Hãy đọc Sổ điểm trước khi áp ngữ cảnh.")\n            self._log("Bỏ qua áp ngữ cảnh vì chưa có ScorebookContext.")\n            return\n\n        try:\n            request = self._collect_selected_context_apply_request()\n        except Exception as error:  # noqa: BLE001 - validation path\n            messagebox.showerror("Lỗi áp ngữ cảnh", str(error))\n            self._log(f"Lỗi áp ngữ cảnh: {error}")\n            return\n\n        self._run_background_task(\n            "Đang áp ngữ cảnh Sổ điểm lên live Chrome...",\n            worker=lambda progress: self._run_selected_context_apply(\n                {\n                    **request,\n                    "progress_callback": progress,\n                }\n            ),\n            on_success=lambda result: self._handle_selected_context_apply_success(result, request),\n            on_error=lambda error: self._handle_selected_context_apply_error(\n                error,\n                request["previous_context"],\n            ),\n        )\n\n\ndef main() -> None:\n    """Program entry point."""\n    root = tk.Tk()\n    style = ttk.Style()\n    if "vista" in style.theme_names():\n        style.theme_use("vista")\n    AutoNhanXetV2App(root)\n    root.mainloop()\n\n\nif __name__ == "__main__":\n    main()\n'
_embedded_nhanxet_pro = types.ModuleType('_embedded_nhanxet_pro')
_embedded_nhanxet_pro.__file__ = __file__
sys.modules[_embedded_nhanxet_pro.__name__] = _embedded_nhanxet_pro
exec(compile(_NHANXET_PRO_SOURCE, str(__file__) + '::embedded_nhanxet_pro', 'exec'), _embedded_nhanxet_pro.__dict__)
ScoreColumnSchema = _embedded_nhanxet_pro.ScoreColumnSchema
ScoreOption = _embedded_nhanxet_pro.ScoreOption
ScorebookAccessEntry = _embedded_nhanxet_pro.ScorebookAccessEntry
ScorebookContext = _embedded_nhanxet_pro.ScorebookContext
VnEduScoreAutomation = _embedded_nhanxet_pro.VnEduScoreAutomation
build_progress_caption = _embedded_nhanxet_pro.build_progress_caption
clamp_progress_value = _embedded_nhanxet_pro.clamp_progress_value
create_subprogress_reporter = _embedded_nhanxet_pro.create_subprogress_reporter
merge_score_options = _embedded_nhanxet_pro.merge_score_options
class_options_from_access_entries = _embedded_nhanxet_pro.class_options_from_access_entries
subject_options_from_access_entries = _embedded_nhanxet_pro.subject_options_from_access_entries
password_entry_show_value = _embedded_nhanxet_pro.password_entry_show_value

from collections import deque
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    wait as futures_wait,
)
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import inspect
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import re
from difflib import SequenceMatcher
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, Sequence
import unicodedata

try:
    import nhanxet_pro as _pyinstaller_nhanxet_pro_hint
except ModuleNotFoundError:  # pragma: no cover - build hint only
    _pyinstaller_nhanxet_pro_hint = None

try:
    import winsound
except ImportError:  # pragma: no cover - không có trên Linux/macOS
    winsound = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency at runtime
    np = None

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency at runtime
    sd = None

try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover - optional dependency at runtime
    sr = None

try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except ImportError:  # pragma: no cover - optional dependency at runtime
    rapidfuzz_fuzz = None

try:
    from fuzzywuzzy import fuzz as fuzzywuzzy_fuzz
except ImportError:  # pragma: no cover - optional dependency at runtime
    fuzzywuzzy_fuzz = None

try:  # PERF: HTTP keep-alive cho Google Speech API (cắt 200–500ms/request)
    import requests as _perf_requests
    from requests.adapters import HTTPAdapter as _PerfHTTPAdapter
except ImportError:  # pragma: no cover - optional dependency at runtime
    _perf_requests = None
    _PerfHTTPAdapter = None

try:  # PERF #3: in-process FLAC encoder (~70–120ms/clip nhanh hơn flac.exe của sr)
    import soundfile as _perf_soundfile
except ImportError:  # pragma: no cover - optional dependency at runtime
    _perf_soundfile = None


APP_TITLE = "Auto Nhập điểm bằng giọng nói"
APP_VERSION = "1.4.0"  # IMP-D4: Version number hiển thị trên title bar
WINDOW_SIZE = "1500x920"
CONFIG_FILE = Path(__file__).with_name("vnedu_standalone_config.json")
ACCESS_CACHE_FILE = Path(__file__).with_name("vnedu_access_cache.json")
ALIAS_FILE = Path(__file__).with_name("student_aliases.json")
SNAPSHOT_FILE = Path(__file__).with_name("vnedu_scorebook_snapshot.json")
PAYLOAD_FILE = Path(__file__).with_name("vnedu_feature_payload.json")
ACCESS_CACHE_SCHEMA_VERSION = 1
ProgressCallback = Callable[[float, str], None]
AUDIO_SAMPLE_RATE = 16000

APP_BG = "#f4f7fb"
APP_PANEL_BG = "#ffffff"
APP_PANEL_BORDER = "#d7dee8"
APP_TEXT = "#172033"
APP_MUTED = "#64748b"
APP_PRIMARY = "#2563eb"
APP_SUCCESS = "#16a34a"
APP_WARNING = "#facc15"
APP_DANGER = "#dc2626"
APP_TABLE_HEADER = "#eaf1fb"
APP_TABLE_LINE = "#cbd5e1"
APP_COMPACT_WIDTH = 1440
APP_SIDEBAR_MIN_WIDTH = 420
APP_SIDEBAR_WIDTH = 520


def get_tk_work_area(widget: tk.Misc | None = None) -> tuple[int, int, int, int]:
    """Return the current monitor work area in Tk logical pixels, excluding the taskbar."""
    screen_w = int(widget.winfo_screenwidth()) if widget else 1280
    screen_h = int(widget.winfo_screenheight()) if widget else 720
    try:
        import ctypes

        user32 = ctypes.windll.user32
        phys_x = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
        phys_y = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
        phys_w = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
        phys_h = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
        if phys_w <= 0 or phys_h <= 0:
            raise RuntimeError("Invalid virtual screen metrics")

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        hwnd = 0
        if widget is not None:
            try:
                hwnd = int(widget.winfo_id())
            except Exception:
                hwnd = 0

        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
        else:
            rect = RECT()
            if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                raise RuntimeError("SPI_GETWORKAREA failed")

        scale_x = screen_w / max(phys_w, 1)
        scale_y = screen_h / max(phys_h, 1)
        x = int(round((int(rect.left) - phys_x) * scale_x))
        y = int(round((int(rect.top) - phys_y) * scale_y))
        w = int(round((int(rect.right) - int(rect.left)) * scale_x))
        h = int(round((int(rect.bottom) - int(rect.top)) * scale_y))
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, screen_w, screen_h


def fit_geometry_to_work_area(
    widget: tk.Misc,
    preferred_w: int,
    preferred_h: int,
    *,
    margin: int = 12,
    min_w: int = 1040,
    min_h: int = 660,
    chrome_w: int = 32,
    chrome_h: int = 52,
) -> tuple[int, int, int, int]:
    """Clamp Tk client geometry so the full native window stays inside work area."""
    work_x, work_y, work_w, work_h = get_tk_work_area(widget)
    available_w = max(1, work_w - margin * 2 - max(0, int(chrome_w)))
    available_h = max(1, work_h - margin * 2 - max(0, int(chrome_h)))
    min_w = min(max(1, int(min_w)), available_w)
    min_h = min(max(1, int(min_h)), available_h)
    width = min(max(int(preferred_w), min_w), available_w)
    height = min(max(int(preferred_h), min_h), available_h)
    x = work_x + (work_w - width) // 2
    y = work_y + margin
    return width, height, max(work_x + margin, x), max(work_y + margin, y)


def apply_app_styles(style: ttk.Style) -> None:
    """Apply one consistent desktop design system for the Tk/ttk interface."""
    try:
        style.theme_use("clam" if "clam" in style.theme_names() else style.theme_use())
    except tk.TclError:
        pass
    style.configure(".", font=("Segoe UI", 10))
    style.configure("TFrame", background=APP_BG)
    style.configure("TLabelframe", background=APP_BG, bordercolor=APP_PANEL_BORDER, relief="solid")
    style.configure(
        "TLabelframe.Label",
        background=APP_BG,
        foreground=APP_TEXT,
        font=("Segoe UI", 10, "bold"),
    )
    style.configure("TLabel", background=APP_BG, foreground=APP_TEXT)
    style.configure("TCheckbutton", background=APP_BG, foreground=APP_TEXT)
    style.configure("TRadiobutton", background=APP_BG, foreground=APP_TEXT)
    style.configure("TEntry", padding=(5, 4))
    style.configure("TCombobox", padding=(5, 4))
    style.configure("TButton", padding=(12, 6), foreground=APP_TEXT)
    style.map("TButton", background=[("active", "#edf2f7")])
    style.configure(
        "Treeview",
        background=APP_PANEL_BG,
        fieldbackground=APP_PANEL_BG,
        foreground=APP_TEXT,
        rowheight=30,
        bordercolor=APP_TABLE_LINE,
        lightcolor=APP_TABLE_LINE,
        darkcolor=APP_TABLE_LINE,
    )
    style.configure(
        "Treeview.Heading",
        background=APP_TABLE_HEADER,
        foreground=APP_TEXT,
        font=("Segoe UI", 10, "bold"),
        padding=(7, 6),
        relief="solid",
    )
    style.map(
        "Treeview",
        background=[("selected", "#bfdbfe")],
        foreground=[("selected", "#0f172a")],
    )

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

# Google Speech v2 endpoint + key Chromium công khai (giống speech_recognition dùng).
_VOICE_GOOGLE_ENDPOINT = "http://www.google.com/speech-api/v2/recognize"
_VOICE_GOOGLE_DEFAULT_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"

_VOICE_HTTP_SESSION_LOCK = threading.Lock()
_VOICE_HTTP_SESSION: Any | None = None
_VOICE_HTTP_PREWARM_DONE = False


def _voice_http_session() -> Any | None:
    """Returns one shared HTTPS session with keep-alive enabled.

    PERF #2: tạo Session 1 lần và tái sử dụng → tiết kiệm 200–500ms TLS mỗi request.
    Trả về None nếu chưa cài `requests`; khi đó pipeline sẽ fallback về urllib (sr gốc).
    """
    global _VOICE_HTTP_SESSION, _VOICE_HTTP_PREWARM_DONE
    if _perf_requests is None or _PerfHTTPAdapter is None:
        return None
    with _VOICE_HTTP_SESSION_LOCK:
        if _VOICE_HTTP_SESSION is None:
            session = _perf_requests.Session()
            adapter = _PerfHTTPAdapter(
                pool_connections=VOICE_HTTP_POOL_SIZE,
                pool_maxsize=VOICE_HTTP_POOL_SIZE,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            _VOICE_HTTP_SESSION = session
        if not _VOICE_HTTP_PREWARM_DONE:
            _VOICE_HTTP_PREWARM_DONE = True

            def _warm_up() -> None:
                try:
                    _VOICE_HTTP_SESSION.head(  # type: ignore[union-attr]
                        _VOICE_GOOGLE_ENDPOINT,
                        timeout=VOICE_HTTP_PREWARM_TIMEOUT,
                        allow_redirects=False,
                    )
                except Exception:
                    pass

            threading.Thread(
                target=_warm_up,
                name="VoiceHTTPPrewarm",
                daemon=True,
            ).start()
        return _VOICE_HTTP_SESSION


def _voice_encode_flac_fast(audio_int16: Any, sample_rate: int) -> bytes | None:
    """Encodes int16 audio to FLAC in-process via libsndfile.

    PERF #3: Bỏ qua `flac.exe` của speech_recognition (chậm 70–120ms cho clip 1s
    do spawn subprocess). libsndfile in-process chỉ tốn ~5–15ms.

    Returns FLAC bytes, hoặc None nếu `soundfile` chưa cài / audio không hợp lệ
    (caller fallback về `sr.AudioData.get_flac_data()` cũ).
    """
    if _perf_soundfile is None or np is None or audio_int16 is None:
        return None
    try:
        if hasattr(audio_int16, "size") and audio_int16.size <= 0:
            return None
        # libsndfile yêu cầu PCM. int16 là tối ưu cho 16kHz speech (8kbps thực).
        if audio_int16.dtype != np.int16:
            audio_int16 = audio_int16.astype(np.int16)
        import io as _io  # nội bộ — chỉ dùng khi encode FLAC

        buffer = _io.BytesIO()
        _perf_soundfile.write(buffer, audio_int16, int(sample_rate), format="FLAC")
        return buffer.getvalue()
    except Exception:  # pragma: no cover - guard libsndfile errors
        return None


def _voice_recognize_google_session(
    recognizer: Any,
    audio_obj: Any,
    *,
    language: str = "vi-VN",
    show_all: bool = True,
    phrase_hints: list[str] | None = None,
    api_key: str | None = None,
    connect_timeout: float = VOICE_HTTP_CONNECT_TIMEOUT,
    read_timeout: float = VOICE_HTTP_READ_TIMEOUT,
    flac_bytes: bytes | None = None,
    sample_rate_override: int | None = None,
) -> Any:
    """Calls Google Speech v2 over a keep-alive session and returns the parsed payload.

    Compatible với `sr.Recognizer.recognize_google` về mặt return: trả dict khi
    show_all=True, raise `sr.UnknownValueError` khi không nghe được, raise
    `sr.RequestError` khi mạng/HTTP lỗi.

    Args:
        flac_bytes: Tuỳ chọn — FLAC bytes đã encode sẵn (PERF #3) để bỏ qua
            `audio_obj.get_flac_data()` (gọi flac.exe chậm). Khi truyền vào,
            `sample_rate_override` cũng phải set.
    """
    if sr is None:
        raise RuntimeError("Thiếu speech_recognition để gọi Google Speech.")
    # Nếu caller truyền hint VÀ recognizer gốc hỗ trợ `phrase_list`, dùng path gốc
    # (mất keep-alive nhưng giữ tính năng hint thật). Endpoint v2 công khai chưa hỗ
    # trợ hint qua query, nên session bỏ qua hint là tương đương no-op.
    if phrase_hints:
        try:
            kwargs: dict[str, Any] = {"language": language, "show_all": show_all}
            kwargs["phrase_list"] = list(phrase_hints)
            return recognizer.recognize_google(audio_obj, **kwargs)  # type: ignore[no-any-return]
        except TypeError:
            # sr không hỗ trợ phrase_list → bỏ hint, đi tiếp vào session keep-alive bên dưới.
            pass
    session = _voice_http_session()
    if session is None:
        # Không có requests — fallback hoàn toàn về sr gốc (urllib mỗi lần mở TLS mới).
        return recognizer.recognize_google(  # type: ignore[no-any-return]
            audio_obj,
            language=language,
            show_all=show_all,
        )

    # PERF #3: ưu tiên FLAC đã pre-encode bằng libsndfile.
    if flac_bytes is not None and sample_rate_override:
        flac_data = flac_bytes
        effective_sample_rate = int(sample_rate_override)
    else:
        try:
            flac_data = audio_obj.get_flac_data(
                convert_rate=None if audio_obj.sample_rate >= 8000 else 8000,
                convert_width=2,
            )
            effective_sample_rate = int(audio_obj.sample_rate)
        except AttributeError:
            # audio_obj không phải sr.AudioData chuẩn (ví dụ: mock trong self-test).
            # Fallback về recognizer gốc — không có keep-alive nhưng vẫn chạy đúng.
            # Chỉ truyền các kwargs cần thiết để khớp với mock recognizer trong test.
            kwargs: dict[str, Any] = {"language": language}
            if show_all:
                kwargs["show_all"] = True
            if phrase_hints:
                kwargs["phrase_list"] = phrase_hints
            return recognizer.recognize_google(audio_obj, **kwargs)  # type: ignore[no-any-return]
        except Exception as error:  # pragma: no cover - sr internal
            raise sr.RequestError(f"Không encode được audio: {error}") from error

    headers = {
        "Content-Type": f"audio/x-flac; rate={effective_sample_rate}",
    }
    params = {
        "client": "chromium",
        "lang": language,
        "key": api_key or _VOICE_GOOGLE_DEFAULT_KEY,
    }
    # NOTE: Endpoint Google Speech v2 (key Chromium công khai) KHÔNG hỗ trợ
    # `phrase_list`/`speechContext` qua query param. Smart hints chỉ có tác dụng
    # khi `speech_recognition` nâng cấp tham số `phrase_list` thật (lúc đó
    # `_recognizer_phrase_list_supported = True` và caller sẽ rớt vào nhánh
    # fallback urllib bên dưới). Ta cố ý bỏ trống để tránh gửi param vô dụng.
    _ = phrase_hints  # noqa: F841 - giữ tham số để API tương thích sr.recognize_google

    try:
        response = session.post(
            _VOICE_GOOGLE_ENDPOINT,
            params=params,
            data=flac_data,
            headers=headers,
            timeout=(connect_timeout, read_timeout),
        )
    except _perf_requests.exceptions.RequestException as error:  # type: ignore[union-attr]
        raise sr.RequestError(f"Lỗi kết nối Google: {error}") from error

    if response.status_code != 200:
        raise sr.RequestError(
            f"Google Speech HTTP {response.status_code}",
        )

    body_text = response.text or ""
    actual_payload: dict[str, Any] | None = None
    for raw_line in body_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("result"):
            actual_payload = parsed
            break

    if actual_payload is None:
        if show_all:
            raise sr.UnknownValueError()
        raise sr.UnknownValueError()

    if show_all:
        # Trả result đầu tiên (giống sr gốc).
        first_result = actual_payload["result"][0] if actual_payload.get("result") else {}
        return first_result if isinstance(first_result, dict) else actual_payload

    # show_all=False → trả best transcript dạng str
    alternatives = []
    for entry in actual_payload.get("result", []):
        if isinstance(entry, dict):
            alternatives = entry.get("alternative", []) or []
            if alternatives:
                break
    if not alternatives:
        raise sr.UnknownValueError()
    best = max(
        (alt for alt in alternatives if isinstance(alt, dict) and alt.get("transcript")),
        key=lambda alt: float(alt.get("confidence", 0.0) or 0.0),
        default=None,
    )
    if best is None:
        raise sr.UnknownValueError()
    return str(best.get("transcript", "")).strip()

# --- Audio Enhancement Constants ---
# DEPRECATED (V1–V3 FIX): noise-gate sample-wise và pre-emphasis làm méo waveform
# gửi Google Speech (xóa phụ âm yếu, nghiêng phổ, sai thanh điệu). Đã gỡ khỏi
# _enhance_audio_for_recognition; giữ lại định nghĩa để tương thích ngược, KHÔNG
# dùng trong pipeline nhận dạng nữa.
VOICE_NOISE_GATE_THRESHOLD = 0.008       # [DEPRECATED] không còn dùng
VOICE_PRE_EMPHASIS_COEFF = 0.97          # [DEPRECATED] không còn dùng
VOICE_NORMALIZE_TARGET_PEAK = 0.92       # Chuẩn hóa đỉnh tín hiệu về mức này
VOICE_BOOST_RETRY_GAIN_DB = 8.0          # Tăng gain (dB) khi thử lại nhận dạng tín hiệu yếu

# --- Bug-fix Constants ---
UNDO_STACK_MAX_SIZE = 200                # Giới hạn undo stack để tránh memory leak (BUG-03)
VOICE_MATCH_CACHE_MAX_SIZE = 500         # Giới hạn voice match cache (BUG-04)
LOG_MAX_LINES = 2000                     # Giới hạn dòng log hiển thị (BUG-12)
LOG_TRIM_LINES = 500                     # Số dòng xóa khi vượt LOG_MAX_LINES
MIC_CONSECUTIVE_ERROR_MAX = 3            # Tự tắt PTT sau N lần lỗi mic liên tiếp (IMP-C2)
VOICE_PENDING_CONFIDENCE_DECAY_RATE = 2.0  # Giảm confidence 2 điểm/giây kể từ pending (IMP-C8)
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


def _timestamp_for_backup() -> str:
    """Builds a filesystem-safe timestamp for local recovery files."""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _json_temp_file(path: Path) -> Path:
    """Returns a unique temp path next to the final JSON file."""
    return path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.{int(time.time() * 1000)}.tmp"
    )


def _backup_unreadable_json_file(path: Path) -> Path | None:
    """Renames an unreadable JSON file so the app can regenerate a clean one."""
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.stem}.corrupt-{_timestamp_for_backup()}{path.suffix}")
    try:
        path.replace(backup_path)
    except OSError:
        return None
    return backup_path


def _load_json_object_file(path: Path) -> tuple[dict[str, object], Path | None, Exception | None]:
    """Loads a JSON object and backs up corrupt content instead of reusing it."""
    if not path.exists():
        return {}, None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as error:  # noqa: BLE001 - caller logs a user-facing message
        return {}, _backup_unreadable_json_file(path), error
    if not isinstance(payload, dict):
        error = ValueError(f"{path.name} phải chứa JSON object ở cấp gốc.")
        return {}, _backup_unreadable_json_file(path), error
    return payload, None, None


def _write_json_atomic_file(path: Path, payload: object) -> None:
    """Writes JSON through a flushed temp file and same-directory replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = _json_temp_file(path)
    try:
        with tmp_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_file.replace(path)
    finally:
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass


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


def _normalize_permission_text_for_score_access(permission_text: str) -> str:
    """Normalizes one VNEDU permission string for reliable score-access checks."""
    normalized = unicodedata.normalize("NFD", str(permission_text or "").strip().lower())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return " ".join(normalized.split())


def _permission_allows_score_entry(permission_text: str) -> bool:
    """Returns whether one VNEDU permission string explicitly allows score entry."""
    normalized = _normalize_permission_text_for_score_access(permission_text)
    if not normalized:
        return False
    if "khong co quyen nhap diem" in normalized or "khong co quyen" in normalized:
        return False
    return "giao vien bo mon" in normalized


def _rewrite_access_message_for_score_ui(message: str) -> str:
    """Rewords embedded permission-scan messages so they match the score-entry GUI."""
    normalized = str(message or "").strip()
    if not normalized:
        return ""
    replacements = (
        ("có quyền nhập nhận xét", "có quyền nhập điểm"),
        ("không có quyền nhập nhận xét", "không có quyền nhập điểm"),
        ("có thể nhập nhận xét", "có thể nhập điểm"),
        ("quyền nhập nhận xét", "quyền nhập điểm"),
        ("dò quyền lớp/môn", "dò quyền nhập điểm lớp/môn"),
        ("nhập nhận xét", "nhập điểm"),
    )
    for old_text, new_text in replacements:
        normalized = normalized.replace(old_text, new_text)
    return normalized


def _patched_can_comment_scorebook(self: VnEduScoreAutomation, permission_text: str, enabled_comment_input_count: int) -> bool:
    """Filters live class-subject pairs by score-entry permission instead of comment cells."""
    _ = enabled_comment_input_count
    return _permission_allows_score_entry(permission_text)


def _patched_apply_access_entries_to_context(
    self: VnEduScoreAutomation,
    context: ScorebookContext,
    entries: list[object],
    grade_id: str,
    term_id: str,
) -> ScorebookContext:
    """Keeps the scanned grade-term scope even when no class has score-entry permission."""
    normalized_grade_id = str(grade_id or "").strip()
    normalized_term_id = str(term_id or "").strip()
    resolved_context = _embedded_nhanxet_pro.apply_access_entries_to_context(
        context,
        list(entries or []),
        grade_id=normalized_grade_id,
        term_id=normalized_term_id,
    )
    if entries:
        return resolved_context
    resolved_context.accessible_entries = []
    resolved_context.accessible_grade_id = normalized_grade_id
    resolved_context.accessible_term_id = normalized_term_id
    return resolved_context


def _patched_subject_options_by_class_for_current_grade(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    grade_id: str,
    term_id: str,
    class_options: list[ScoreOption],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Builds class-subject specs without requiring the hidden class id to settle immediately."""
    original_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
    original_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
    original_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
    original_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
    current_snapshot = self._hydrate_scorebook_snapshot_options(
        page,
        snapshot,
        include_grade=False,
        include_class=True,
        include_subject=True,
        include_term=False,
    )
    class_subject_specs: list[dict[str, object]] = []

    for class_option in class_options:
        class_id = class_option.option_id.strip()
        if not class_id:
            continue
        current_class_id = self._effective_snapshot_selected_id(
            current_snapshot,
            "currentClassId",
            "hiddenClassId",
        )
        if current_class_id != class_id:
            class_combo_id = str(current_snapshot.get("classComboId", "")).strip()
            if not class_combo_id or not self._set_combo_value(page, class_combo_id, class_id):
                raise RuntimeError(f"Không thể chọn lớp id={class_id} trong lúc dò quyền lớp/môn.")
            current_snapshot = self._wait_for_hydrated_scorebook_options(
                page,
                current_snapshot,
                options_key="subjectOptions",
                include_grade=False,
                include_class=False,
                include_subject=True,
                include_term=False,
                expected_grade_id=grade_id or None,
                timeout_sec=6.0,
            )
        current_snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            current_snapshot,
            include_grade=False,
            include_class=False,
            include_subject=True,
            include_term=False,
            merge_existing=False,
            preserve_selected_if_missing=False,
        )
        subject_options = self._build_options(list(current_snapshot.get("subjectOptions", [])))
        if not subject_options:
            subject_combo_id = str(current_snapshot.get("subjectComboId", "")).strip()
            if subject_combo_id:
                subject_options = self._build_options(self._load_live_combo_options(page, subject_combo_id))
        if not subject_options:
            continue
        class_subject_specs.append(
            {
                "classId": class_option.option_id,
                "classText": class_option.option_text,
                "subjectOptions": [
                    {
                        "option_id": option.option_id,
                        "option_text": option.option_text,
                    }
                    for option in subject_options
                ],
            }
        )

    if self._requested_scorebook_context_differs(
        current_snapshot,
        grade_id=original_grade_id,
        class_id=original_class_id,
        subject_id=original_subject_id,
        term_id=original_term_id,
    ):
        current_snapshot, _ = self._select_scorebook_context_on_page(
            page,
            current_snapshot,
            grade_id=original_grade_id,
            class_id=original_class_id,
            subject_id=original_subject_id,
            term_id=original_term_id,
        )

    return class_subject_specs, current_snapshot


def _patched_find_visible_input(
    self: VnEduScoreAutomation,
    page: object,
    selectors: list[str],
):
    """Returns the first visible input that matches any selector, falling back to the first match."""
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        if count > 0:
            return locator.first
    return None


def _patched_find_username_input(self: VnEduScoreAutomation, page: object):
    """Returns a visible username input instead of the first hidden text field in DOM order."""
    selectors = [
        "input[name*='user' i]",
        "input[id*='user' i]",
        "input[name*='login' i]",
        "input[id*='login' i]",
        "input[name*='account' i]",
        "input[id*='account' i]",
        "input[type='email']",
        "input[type='text']",
    ]
    username_input = self._find_visible_input(page, selectors)
    if username_input is None:
        raise RuntimeError("Không tìm thấy ô nhập tài khoản trên form đăng nhập.")
    return username_input


def _patched_read_login_surface_state(
    self: VnEduScoreAutomation,
    page: object,
) -> dict[str, object]:
    """Reads one compact VNEDU login/shell state so login success is not inferred from password DOM alone."""
    state = {
        "url": str(getattr(page, "url", "") or "").strip(),
        "login_form_visible": False,
        "password_visible": False,
        "captcha_visible": False,
        "shell_visible": False,
        "scorebook_visible": False,
        "error_text": "",
    }
    try:
        state["scorebook_visible"] = bool(self._has_scorebook_controls(page))
    except Exception:
        state["scorebook_visible"] = False
    if state["scorebook_visible"]:
        state["shell_visible"] = True
        return state

    try:
        snapshot = page.evaluate(
            """() => {
            const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
                    return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const normalizeText = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visiblePasswords = Array.from(document.querySelectorAll("input[type='password']")).filter(isVisible);
            const visibleUsers = Array.from(document.querySelectorAll("input[type='text'], input[type='email']")).filter(isVisible);
            const visibleLoginButtons = Array.from(document.querySelectorAll("button, input[type='submit'], a"))
                .filter(isVisible)
                .filter(el => {
                    const text = normalizeText(el.innerText || el.value || el.getAttribute('title') || '').toLowerCase();
                    return text.includes('đăng nhập') || text.includes('dang nhap') || text.includes('login');
                });
            const visibleCaptcha = Array.from(
                document.querySelectorAll("input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='captcha' i]")
            ).filter(isVisible);
            const shellVisible = Boolean(
                document.querySelector('.ux-taskbar, #ux-taskbar, .ux-desktop-shortcut, #x-desktop, .x-desktop')
            );
            const errorKeywords = [
                'sai tài khoản',
                'sai mật khẩu',
                'tài khoản hoặc mật khẩu',
                'đăng nhập không thành công',
                'đăng nhập thất bại',
                'không thể đăng nhập',
                'mã xác thực',
                'captcha',
            ];
            const textCandidates = Array.from(document.querySelectorAll('div, span, td, p, label, li'))
                .filter(isVisible)
                .map(el => normalizeText(el.innerText || el.textContent || ''))
                .filter(text => text && text.length <= 220);
            const errorText = textCandidates.find(text => {
                const normalized = text.toLowerCase();
                return errorKeywords.some(keyword => normalized.includes(keyword));
            }) || '';
            return {
                loginFormVisible: Boolean(visiblePasswords.length && (visibleUsers.length || visibleLoginButtons.length)),
                passwordVisible: Boolean(visiblePasswords.length),
                captchaVisible: Boolean(visibleCaptcha.length),
                shellVisible,
                errorText,
            };
        }"""
        )
    except Exception:
        snapshot = {}

    state["login_form_visible"] = bool(snapshot.get("loginFormVisible"))
    state["password_visible"] = bool(snapshot.get("passwordVisible"))
    state["captcha_visible"] = bool(snapshot.get("captchaVisible"))
    state["shell_visible"] = bool(snapshot.get("shellVisible"))
    state["error_text"] = str(snapshot.get("errorText", "") or "").strip()
    return state


def _patched_login_if_needed_on_page(
    self: VnEduScoreAutomation,
    page: object,
    username: str = "",
    password: str = "",
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Logs in using visible login controls and confirms success from VNEDU shell state instead of password DOM removal."""
    emit_progress = _embedded_nhanxet_pro.emit_progress
    emit_progress(progress_callback, 5.0, "Đang truy cập trang VNEDU...")
    self._goto_target_page(page)
    self._close_notice_popup(page)

    login_state = self._read_login_surface_state(page)
    if not login_state["login_form_visible"]:
        emit_progress(progress_callback, 100.0, "Không cần đăng nhập lại, phiên đã sẵn sàng.")
        if username.strip() or password:
            return "Không phát hiện form đăng nhập; có thể phiên đã đăng nhập sẵn."
        return ""

    if not username.strip() or not password:
        raise RuntimeError(
            "Phiên hiện tại đang ở màn hình đăng nhập. Hãy nhập tài khoản và mật khẩu VNEDU."
        )

    password_input = self._find_visible_input(page, ["input[type='password']"])
    if password_input is None:
        raise RuntimeError("Không tìm thấy ô nhập mật khẩu đang hiển thị trên form đăng nhập.")
    username_input = self._find_username_input(page)
    emit_progress(progress_callback, 20.0, "Đang điền tài khoản và mật khẩu VNEDU...")
    username_input.fill(username.strip())
    password_input.fill(password)

    captcha_input = page.locator(
        "input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='captcha' i]"
    )
    if captcha_input.count() > 0:
        try:
            visible_captcha = next(
                (
                    captcha_input.nth(index)
                    for index in range(captcha_input.count())
                    if captcha_input.nth(index).is_visible()
                ),
                None,
            )
        except Exception:
            visible_captcha = captcha_input.first
        if visible_captcha is not None:
            captcha_value = visible_captcha.input_value().strip()
            if not captcha_value:
                try:
                    visible_captcha.focus()
                except Exception:
                    pass
                raise RuntimeError(
                    "Trang đăng nhập VNEDU đang yêu cầu mã captcha. "
                    "App đã điền sẵn tài khoản và mật khẩu trên tab hiện tại; "
                    "hãy nhập captcha rồi bấm Đăng nhập thủ công, sau đó nhấn lại 'Đăng nhập + load Sổ điểm'."
                )

    emit_progress(progress_callback, 40.0, "Đang gửi yêu cầu đăng nhập...")
    clicked = page.evaluate(
        """() => {
        const isVisible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };
        const candidates = Array.from(document.querySelectorAll('button, input[type="submit"], a')).filter(isVisible);
        const target = candidates.find(el => {
            const text = (el.innerText || el.value || el.getAttribute('title') || '').trim().toLowerCase();
            return text.includes('đăng nhập') || text.includes('dang nhap') || text.includes('login');
        });
        if (!target) return false;
        target.click();
        return true;
    }"""
    )
    if not clicked:
        password_input.press("Enter")

    started_at = time.time()
    deadline = started_at + 30.0
    last_state = login_state
    while time.time() < deadline:
        page.wait_for_timeout(250)
        self._close_notice_popup(page)
        last_state = self._read_login_surface_state(page)
        if last_state["error_text"]:
            raise RuntimeError(f"Đăng nhập VNEDU thất bại: {last_state['error_text']}")
        if last_state["scorebook_visible"] or last_state["shell_visible"]:
            emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")
            return "Đã gửi đăng nhập và xác thực thành công."
        if not last_state["login_form_visible"] and not last_state["password_visible"]:
            emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")
            return "Đã gửi đăng nhập và xác thực thành công."
        elapsed_ratio = min((time.time() - started_at) / 30.0, 1.0)
        emit_progress(
            progress_callback,
            40.0 + (elapsed_ratio * 55.0),
            "Đang chờ VNEDU xác thực đăng nhập...",
        )

    if last_state.get("captcha_visible"):
        raise RuntimeError(
            "Đăng nhập VNEDU chưa hoàn tất vì hệ thống đang yêu cầu captcha/xác thực bổ sung."
        )
    if last_state.get("error_text"):
        raise RuntimeError(f"Đăng nhập VNEDU thất bại: {last_state['error_text']}")
    raise RuntimeError(
        "Đăng nhập VNEDU chưa được xác nhận hoàn tất. Form đăng nhập vẫn còn hiển thị hoặc phiên CDP chưa chuyển sang màn hình làm việc."
    )



# ---------------------------------------------------------------------------
#  NOTE: Original monkey-patching lines removed.
#  All overrides are now in the VnEduScoreEntryAutomation subclass below.
# ---------------------------------------------------------------------------

_ORIGINAL_WAIT_FOR_SCOREBOOK_PERMISSION_SNAPSHOT = (
    VnEduScoreAutomation._wait_for_scorebook_permission_snapshot
)
_ORIGINAL_SELECT_SCOREBOOK_CONTEXT_ON_PAGE = (
    VnEduScoreAutomation._select_scorebook_context_on_page
)
_ORIGINAL_LOAD_ACCESSIBLE_SCOREBOOK_CONTEXT = (
    VnEduScoreAutomation.load_accessible_scorebook_context
)
_ORIGINAL_SELECT_ACCESSIBLE_SCOREBOOK_CONTEXT = (
    VnEduScoreAutomation.select_accessible_scorebook_context
)


def _live_scorebook_snapshot_or_fallback(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    """Reads one fresh scorebook snapshot and falls back to the provided cached snapshot."""
    live_snapshot, _error = self._best_effort_scorebook_snapshot(page)
    if isinstance(live_snapshot, dict) and live_snapshot:
        return live_snapshot
    return dict(snapshot or {})


def _snapshot_mismatch_messages(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    *,
    expected_class_id: str | None = None,
    expected_subject_id: str | None = None,
    expected_term_id: str | None = None,
) -> list[str]:
    """Builds human-readable mismatch details for one live scorebook snapshot."""
    messages: list[str] = []
    actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
    actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
    actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
    if expected_class_id is not None and actual_class_id != expected_class_id.strip():
        messages.append(
            f"Lớp thực tế là `{snapshot.get('currentClassText', '') or actual_class_id or '(trống)'}` "
            f"(id={actual_class_id or '(trống)'}), khác lớp mong đợi id={expected_class_id.strip()}"
        )
    if expected_subject_id is not None and actual_subject_id != expected_subject_id.strip():
        messages.append(
            f"Môn thực tế là `{snapshot.get('currentSubjectText', '') or actual_subject_id or '(trống)'}` "
            f"(id={actual_subject_id or '(trống)'}), khác môn mong đợi id={expected_subject_id.strip()}"
        )
    if expected_term_id is not None and actual_term_id != expected_term_id.strip():
        messages.append(
            f"Học kỳ thực tế là `{snapshot.get('currentTermText', '') or actual_term_id or '(trống)'}` "
            f"(id={actual_term_id or '(trống)'}), khác học kỳ mong đợi id={expected_term_id.strip()}"
        )
    return messages


def _patched_wait_for_scorebook_permission_snapshot(
    self: VnEduScoreAutomation,
    page: object,
    expected_class_id: str | None = None,
    expected_subject_id: str | None = None,
    expected_term_id: str | None = None,
    timeout_sec: float = 6.0,
    progress_callback: ProgressCallback | None = None,
    progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",
) -> dict[str, object]:
    """Waits for one permission snapshot and fails closed if the live page never reaches the expected ids."""
    snapshot = _ORIGINAL_WAIT_FOR_SCOREBOOK_PERMISSION_SNAPSHOT(
        self,
        page,
        expected_class_id=expected_class_id,
        expected_subject_id=expected_subject_id,
        expected_term_id=expected_term_id,
        timeout_sec=timeout_sec,
        progress_callback=progress_callback,
        progress_message=progress_message,
    )
    snapshot = _live_scorebook_snapshot_or_fallback(self, page, snapshot)
    mismatches = _snapshot_mismatch_messages(
        self,
        snapshot,
        expected_class_id=expected_class_id,
        expected_subject_id=expected_subject_id,
        expected_term_id=expected_term_id,
    )
    if mismatches:
        raise RuntimeError(
            "Sổ điểm chưa đồng bộ đúng quyền/ngữ cảnh sau khi chuyển lớp-môn-học kỳ. "
            + " ".join(mismatches)
        )
    return snapshot


def _patched_select_scorebook_context_on_page(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    grade_id: str = "",
    class_id: str = "",
    subject_id: str = "",
    term_id: str = "",
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, object], str]:
    """Refreshes the incoming snapshot from the live page before deciding whether a selection step can be skipped."""
    fresh_snapshot = _live_scorebook_snapshot_or_fallback(self, page, snapshot)
    return _ORIGINAL_SELECT_SCOREBOOK_CONTEXT_ON_PAGE(
        self,
        page,
        fresh_snapshot,
        grade_id=grade_id,
        class_id=class_id,
        subject_id=subject_id,
        term_id=term_id,
        progress_callback=progress_callback,
    )


# NOTE: Monkey-patching for _wait_for_scorebook_permission_snapshot
#       and _select_scorebook_context_on_page removed — now in subclass.


def _snapshot_has_option_id(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    options_key: str,
    option_id: str,
) -> bool:
    """Checks whether one snapshot option store contains the requested id."""
    normalized_id = str(option_id or "").strip()
    if not normalized_id:
        return False
    return normalized_id in {
        option.option_id.strip()
        for option in self._build_options(list(snapshot.get(options_key, [])))
        if option.option_id.strip()
    }


def _ordered_grade_ids_for_access_scan(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
) -> list[str]:
    """Returns grade ids ordered with the currently selected grade first."""
    ordered_ids: list[str] = []
    current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
    if current_grade_id:
        ordered_ids.append(current_grade_id)
    for option in self._build_options(list(snapshot.get("gradeOptions", []))):
        option_id = option.option_id.strip()
        if option_id and option_id not in ordered_ids:
            ordered_ids.append(option_id)
    return ordered_ids


def _pick_subject_id_for_fast_access_scan(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    preferred_subject_id: str = "",
) -> str:
    """Chooses the best subject id for the fast class-only permission scan."""
    normalized_preferred_id = str(preferred_subject_id or "").strip()
    if normalized_preferred_id and _snapshot_has_option_id(self, snapshot, "subjectOptions", normalized_preferred_id):
        return normalized_preferred_id
    current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
    if current_subject_id and _snapshot_has_option_id(self, snapshot, "subjectOptions", current_subject_id):
        return current_subject_id
    subject_options = self._build_options(list(snapshot.get("subjectOptions", [])))
    return next((option.option_id.strip() for option in subject_options if option.option_id.strip()), "")


def _ordered_subject_ids_for_access_scan(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    *,
    preferred_subject_id: str = "",
    exclude_subject_ids: set[str] | None = None,
) -> list[str]:
    """Returns subject ids ordered by preference for one fast permission probe."""
    ordered_ids: list[str] = []
    excluded_ids = {str(item or "").strip() for item in (exclude_subject_ids or set()) if str(item or "").strip()}

    def add_subject(subject_id: str) -> None:
        normalized_subject_id = str(subject_id or "").strip()
        if (
            not normalized_subject_id
            or normalized_subject_id in excluded_ids
            or normalized_subject_id in ordered_ids
            or not _snapshot_has_option_id(self, snapshot, "subjectOptions", normalized_subject_id)
        ):
            return
        ordered_ids.append(normalized_subject_id)

    add_subject(preferred_subject_id)
    add_subject(self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"))
    for option in self._build_options(list(snapshot.get("subjectOptions", []))):
        add_subject(option.option_id)
    return ordered_ids


def _discover_first_accessible_subject_in_snapshot(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    grade_id: str,
    term_id: str,
    preferred_subject_id: str = "",
    exclude_subject_ids: set[str] | None = None,
) -> tuple[list[object], str]:
    """Scans subjects in priority order and returns the first accessible subject hit."""
    subject_ids = _ordered_subject_ids_for_access_scan(
        self,
        snapshot,
        preferred_subject_id=preferred_subject_id,
        exclude_subject_ids=exclude_subject_ids,
    )
    for candidate_subject_id in subject_ids:
        entries = _discover_accessible_entries_for_fixed_subject(
            self,
            page,
            snapshot,
            grade_id=grade_id,
            term_id=term_id,
            subject_id=candidate_subject_id,
        )
        if entries:
            return entries, candidate_subject_id
    return [], ""


def _discover_accessible_entries_for_fixed_subject(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    grade_id: str,
    term_id: str,
    subject_id: str,
) -> list[object]:
    """Fast-path permission scan for one grade-term-subject across all classes."""
    normalized_grade_id = str(grade_id or "").strip()
    normalized_term_id = str(term_id or "").strip()
    normalized_subject_id = str(subject_id or "").strip()
    if not normalized_grade_id or not normalized_term_id or not normalized_subject_id:
        return []

    hydrated_snapshot = self._hydrate_scorebook_snapshot_options(
        page,
        snapshot,
        include_grade=False,
        include_class=True,
        include_subject=True,
        include_term=False,
        merge_existing=False,
        preserve_selected_if_missing=False,
    )
    class_options = self._build_options(list(hydrated_snapshot.get("classOptions", [])))
    if not class_options:
        return []
    if not _snapshot_has_option_id(self, hydrated_snapshot, "subjectOptions", normalized_subject_id):
        return []

    school_year = (
        str(hydrated_snapshot.get("hiddenSchoolYear", "")).strip()
        or str(hydrated_snapshot.get("currentSchoolYear", "")).strip()
    )
    window_id = str(hydrated_snapshot.get("windowId", "")).strip()
    if not school_year or not window_id:
        return []

    grade_text = self._option_text_by_id(
        self._build_options(list(hydrated_snapshot.get("gradeOptions", []))),
        normalized_grade_id,
    )
    term_text = self._option_text_by_id(
        self._build_options(list(hydrated_snapshot.get("termOptions", []))),
        normalized_term_id,
    )
    subject_text = self._option_text_by_id(
        self._build_options(list(hydrated_snapshot.get("subjectOptions", []))),
        normalized_subject_id,
    )
    if not subject_text:
        subject_text = str(hydrated_snapshot.get("currentSubjectText", "")).strip()

    raw_entries = page.evaluate(
        """async ({ schoolYear, windowId, gradeId, gradeText, termId, termText, subjectId, subjectText, classOptions }) => {
        const endpoint = '/v5/?load=edu.so_diem.nhap';
        const base = {
            app_nam_hoc: schoolYear,
            nam_hoc: schoolYear,
            iKhoi: gradeId,
            iHocKyId: termId,
            iMonHocId: subjectId,
            winid: windowId,
        };

        const parseRoleText = (doc) => {
            const roleCell = Array.from(doc.querySelectorAll('td')).find(td =>
                /quyền hạn/i.test((td.textContent || '').trim())
            );
            return (roleCell?.textContent || '').trim();
        };

        const parseTeacherText = (doc) => ((doc.querySelector('#gvbm')?.textContent) || '').trim();
        const tasks = (classOptions || []).map((classOption) => ({ classOption }));
        const results = [];
        const concurrency = 8;
        for (let index = 0; index < tasks.length; index += concurrency) {
            const batch = tasks.slice(index, index + concurrency);
            const batchResults = await Promise.all(batch.map(async (task) => {
                const params = new URLSearchParams({
                    ...base,
                    iLopId: task.classOption.option_id,
                });
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        },
                        body: params.toString(),
                    });
                    const html = await response.text();
                    const doc = new DOMParser().parseFromString(html, 'text/html');
                    const commentInputs = Array.from(doc.querySelectorAll('input.input_nhan_xet'));
                    const enabledCommentInputCount = commentInputs.filter(input => !input.disabled && !input.readOnly).length;
                    return {
                        gradeId,
                        gradeText,
                        classId: task.classOption.option_id,
                        classText: task.classOption.option_text,
                        subjectId,
                        subjectText,
                        termId,
                        termText,
                        teacherText: parseTeacherText(doc),
                        permissionText: parseRoleText(doc),
                        commentInputCount: commentInputs.length,
                        enabledCommentInputCount,
                    };
                } catch (error) {
                    return {
                        gradeId,
                        gradeText,
                        classId: task.classOption.option_id,
                        classText: task.classOption.option_text,
                        subjectId,
                        subjectText,
                        termId,
                        termText,
                        teacherText: '',
                        permissionText: `Lỗi dò quyền: ${String(error)}`,
                        commentInputCount: 0,
                        enabledCommentInputCount: 0,
                    };
                }
            }));
            results.push(...batchResults);
        }
        return results;
    }""",
        {
            "schoolYear": school_year,
            "windowId": window_id,
            "gradeId": normalized_grade_id,
            "gradeText": grade_text,
            "termId": normalized_term_id,
            "termText": term_text,
            "subjectId": normalized_subject_id,
            "subjectText": subject_text,
            "classOptions": [
                {
                    "option_id": option.option_id,
                    "option_text": option.option_text,
                }
                for option in class_options
            ],
        },
    )
    entries = self._build_access_entries(list(raw_entries or []))
    return [
        entry
        for entry in entries
        if self._can_comment_scorebook(entry.permission_text, entry.enabled_comment_input_count)
    ]


def _finalize_accessible_context_result(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    entries: list[object],
    grade_id: str,
    term_id: str,
    preferred_class_id: str = "",
    preferred_subject_id: str = "",
    message_parts: list[str] | None = None,
    access_scope_mode: str = "full_matrix",
    access_scope_subject_id: str = "",
) -> tuple[object, str]:
    """Selects one accessible class-subject pair and builds the final context payload."""
    normalized_grade_id = str(grade_id or "").strip()
    normalized_term_id = str(term_id or "").strip()
    final_message_parts = list(message_parts or [])
    working_snapshot = dict(snapshot)
    if entries:
        class_id, subject_id, fallback_notes = self._resolve_accessible_selection(
            list(entries),
            preferred_class_id=preferred_class_id,
            preferred_subject_id=preferred_subject_id,
        )
        if class_id and subject_id:
            working_snapshot, select_notes = self._select_scorebook_context_on_page(
                page,
                working_snapshot,
                grade_id=normalized_grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=normalized_term_id,
            )
            if select_notes:
                final_message_parts.append(select_notes)
        final_message_parts.extend(fallback_notes)
    context = self._build_scorebook_context(working_snapshot)
    self._apply_access_entries_to_context(
        context,
        list(entries),
        grade_id=normalized_grade_id,
        term_id=normalized_term_id,
    )
    setattr(context, "_access_scope_mode", str(access_scope_mode or "").strip())
    setattr(context, "_access_scope_subject_id", str(access_scope_subject_id or "").strip())
    return context, " ".join(part for part in final_message_parts if str(part or "").strip()).strip()


def _patched_load_accessible_scorebook_context(
    self: VnEduScoreAutomation,
    username: str = "",
    password: str = "",
) -> tuple[object, str, str]:
    """Loads one permission-filtered scorebook shell with multi-phase grade/subject fallback."""
    with self._open_page() as page:
        login_message = self._login_if_needed_on_page(page, username=username, password=password)
        self._ensure_scorebook_screen(page)
        snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
        )
        snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=True,
            include_class=True,
            include_subject=True,
            include_term=True,
            merge_existing=False,
            preserve_selected_if_missing=False,
        )

        original_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        original_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        original_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        target_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        grade_order = _ordered_grade_ids_for_access_scan(self, snapshot)
        visited_snapshots: dict[str, dict[str, object]] = {}
        primary_subject_id = _pick_subject_id_for_fast_access_scan(
            self,
            snapshot,
            preferred_subject_id=original_subject_id,
        )

        def get_grade_snapshot(candidate_grade_id: str) -> dict[str, object]:
            working_snapshot = visited_snapshots.get(candidate_grade_id)
            if working_snapshot is not None:
                return working_snapshot
            if candidate_grade_id == self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"):
                working_snapshot = dict(snapshot)
                working_snapshot = self._hydrate_scorebook_snapshot_options(
                    page,
                    working_snapshot,
                    include_grade=False,
                    include_class=True,
                    include_subject=True,
                    include_term=False,
                    merge_existing=False,
                    preserve_selected_if_missing=False,
                )
            else:
                # PERF/ACCURACY: khi đổi sang khối khác, class store reload bất đồng
                # bộ. Chờ store class fresh (loại bỏ option thuộc khối cũ) bằng
                # _wait_for_hydrated_scorebook_options thay vì hydrate thẳng — đảm
                # bảo không đọc nhầm danh sách lớp của khối trước đó.
                stale_class_id = self._effective_snapshot_selected_id(
                    snapshot, "currentClassId", "hiddenClassId"
                )
                changed_snapshot, _notes = self._select_scorebook_grade_term_on_page(
                    page,
                    snapshot,
                    grade_id=candidate_grade_id,
                    term_id=target_term_id,
                )
                working_snapshot = self._wait_for_hydrated_scorebook_options(
                    page,
                    changed_snapshot,
                    options_key="classOptions",
                    include_class=True,
                    include_subject=True,
                    expected_grade_id=candidate_grade_id,
                    stale_option_id=stale_class_id,
                    timeout_sec=5.0,
                )
            visited_snapshots[candidate_grade_id] = working_snapshot
            return working_snapshot

        def grade_text(candidate_snapshot: dict[str, object], candidate_grade_id: str) -> str:
            return self._option_text_by_id(
                self._build_options(list(candidate_snapshot.get("gradeOptions", []))),
                candidate_grade_id,
            )

        def subject_text(candidate_snapshot: dict[str, object], candidate_subject_id: str) -> str:
            return self._option_text_by_id(
                self._build_options(list(candidate_snapshot.get("subjectOptions", []))),
                candidate_subject_id,
            )

        current_grade_snapshot = get_grade_snapshot(original_grade_id)

        if primary_subject_id:
            current_grade_entries = _discover_accessible_entries_for_fixed_subject(
                self,
                page,
                current_grade_snapshot,
                grade_id=original_grade_id,
                term_id=target_term_id,
                subject_id=primary_subject_id,
            )
            if current_grade_entries:
                current_grade_text = grade_text(current_grade_snapshot, original_grade_id)
                context, selection_message = _finalize_accessible_context_result(
                    self,
                    page,
                    current_grade_snapshot,
                    entries=current_grade_entries,
                    grade_id=original_grade_id,
                    term_id=target_term_id,
                    preferred_class_id=original_class_id,
                    preferred_subject_id=primary_subject_id,
                    message_parts=[
                        f"Đã dò nhanh quyền nhập điểm theo môn hiện tại cho {current_grade_text}: {len(current_grade_entries)} lớp hợp lệ."
                    ],
                    access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                    access_scope_subject_id=primary_subject_id,
                )
                return context, login_message, selection_message

        for candidate_grade_id in grade_order:
            if not primary_subject_id or candidate_grade_id == original_grade_id:
                continue
            working_snapshot = get_grade_snapshot(candidate_grade_id)
            fast_entries = _discover_accessible_entries_for_fixed_subject(
                self,
                page,
                working_snapshot,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                subject_id=primary_subject_id,
            )
            if not fast_entries:
                continue
            candidate_grade_text = grade_text(working_snapshot, candidate_grade_id)
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                working_snapshot,
                entries=fast_entries,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_class_id="",
                preferred_subject_id=primary_subject_id,
                message_parts=[
                    f"Khối hiện tại không có quyền nhập điểm cho môn đang chọn, app chuyển sang {candidate_grade_text}.",
                    f"Đã dò nhanh quyền nhập điểm theo môn hiện tại cho {candidate_grade_text}: {len(fast_entries)} lớp hợp lệ.",
                ],
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=primary_subject_id,
            )
            return context, login_message, selection_message

        current_grade_entries, fallback_subject_id = _discover_first_accessible_subject_in_snapshot(
            self,
            page,
            current_grade_snapshot,
            grade_id=original_grade_id,
            term_id=target_term_id,
            preferred_subject_id=primary_subject_id,
            exclude_subject_ids={primary_subject_id} if primary_subject_id else set(),
        )
        if current_grade_entries and fallback_subject_id:
            current_grade_text = grade_text(current_grade_snapshot, original_grade_id)
            fallback_subject_text = subject_text(current_grade_snapshot, fallback_subject_id) or fallback_subject_id
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                current_grade_snapshot,
                entries=current_grade_entries,
                grade_id=original_grade_id,
                term_id=target_term_id,
                preferred_class_id=original_class_id,
                preferred_subject_id=fallback_subject_id,
                message_parts=[
                    f"Khối hiện tại không có quyền nhập điểm cho môn đang chọn, app chuyển sang môn {fallback_subject_text} trong cùng khối.",
                    f"Đã dò nhanh quyền nhập điểm cho {current_grade_text} / {fallback_subject_text}: {len(current_grade_entries)} lớp hợp lệ.",
                ],
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=fallback_subject_id,
            )
            return context, login_message, selection_message

        for candidate_grade_id in grade_order:
            if candidate_grade_id == original_grade_id:
                continue
            working_snapshot = get_grade_snapshot(candidate_grade_id)
            entries, fallback_subject_id = _discover_first_accessible_subject_in_snapshot(
                self,
                page,
                working_snapshot,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_subject_id=primary_subject_id,
                exclude_subject_ids={primary_subject_id} if primary_subject_id else set(),
            )
            if not entries or not fallback_subject_id:
                continue
            candidate_grade_text = grade_text(working_snapshot, candidate_grade_id)
            fallback_subject_text = subject_text(working_snapshot, fallback_subject_id) or fallback_subject_id
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                working_snapshot,
                entries=entries,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_class_id="",
                preferred_subject_id=fallback_subject_id,
                message_parts=[
                    f"Môn đang chọn không có quyền ở các khối đã dò trước đó, app chuyển sang {candidate_grade_text} / {fallback_subject_text}.",
                    f"Đã dò nhanh quyền nhập điểm cho {candidate_grade_text} / {fallback_subject_text}: {len(entries)} lớp hợp lệ.",
                ],
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=fallback_subject_id,
            )
            return context, login_message, selection_message

        for candidate_grade_id in grade_order:
            working_snapshot = get_grade_snapshot(candidate_grade_id)
            entries = self._discover_accessible_entries_for_current_grade(
                page,
                working_snapshot,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
            )
            if not entries:
                continue
            candidate_grade_text = grade_text(working_snapshot, candidate_grade_id)
            message_parts = []
            if candidate_grade_id != original_grade_id:
                message_parts.append(
                    f"App fallback sang dò toàn bộ ma trận quyền và chọn {candidate_grade_text} vì các nhánh quét nhanh không tìm thấy quyền phù hợp."
                )
            else:
                message_parts.append("App fallback sang dò toàn bộ ma trận quyền trong khối hiện tại.")
            message_parts.append(
                f"Đã dò toàn bộ quyền nhập điểm cho {candidate_grade_text}: {len(entries)} tổ hợp lớp/môn hợp lệ."
            )
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                working_snapshot,
                entries=entries,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_class_id=original_class_id if candidate_grade_id == original_grade_id else "",
                preferred_subject_id=original_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.FULL_MATRIX,
            )
            return context, login_message, selection_message

        context = self._build_scorebook_context(snapshot)
        self._apply_access_entries_to_context(context, [], grade_id=original_grade_id, term_id=target_term_id)
        return (
            context,
            login_message,
            "Không tìm thấy lớp/môn nào có quyền nhập điểm ở tất cả các khối hiện có.",
        )


def _patched_select_accessible_scorebook_context(
    self: VnEduScoreAutomation,
    grade_id: str = "",
    class_id: str = "",
    subject_id: str = "",
    term_id: str = "",
    username: str = "",
    password: str = "",
) -> tuple[object, str, str]:
    """Applies one target grade-term and uses a fast subject-first scan with same-grade subject fallback."""
    with self._open_page() as page:
        login_message = self._login_if_needed_on_page(page, username=username, password=password)
        self._ensure_scorebook_screen(page)
        snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
        )
        initial_context = self._build_scorebook_context(snapshot)
        target_grade_id = grade_id.strip() or initial_context.selected_grade_id
        target_term_id = term_id.strip() or initial_context.selected_term_id
        snapshot, selection_message = self._select_scorebook_grade_term_on_page(
            page,
            snapshot,
            grade_id=target_grade_id,
            term_id=target_term_id,
        )
        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or target_grade_id
        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or target_term_id
        snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=False,
            include_class=True,
            include_subject=True,
            include_term=False,
            merge_existing=False,
            preserve_selected_if_missing=False,
        )

        preferred_subject_id = subject_id.strip() or _pick_subject_id_for_fast_access_scan(
            self,
            snapshot,
            preferred_subject_id=initial_context.selected_subject_id,
        )
        entries: list[object] = []
        if preferred_subject_id:
            entries = _discover_accessible_entries_for_fixed_subject(
                self,
                page,
                snapshot,
                grade_id=current_grade_id,
                term_id=current_term_id,
                subject_id=preferred_subject_id,
            )
        if entries:
            message_parts = [selection_message] if selection_message else []
            message_parts.append(
                f"Đã dò nhanh quyền nhập điểm theo môn hiện tại: {len(entries)} lớp hợp lệ."
            )
            context, final_message = _finalize_accessible_context_result(
                self,
                page,
                snapshot,
                entries=entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
                preferred_class_id=class_id,
                preferred_subject_id=preferred_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=preferred_subject_id,
            )
            return context, login_message, final_message

        entries, fallback_subject_id = _discover_first_accessible_subject_in_snapshot(
            self,
            page,
            snapshot,
            grade_id=current_grade_id,
            term_id=current_term_id,
            preferred_subject_id=preferred_subject_id,
            exclude_subject_ids={preferred_subject_id} if preferred_subject_id else set(),
        )
        if entries and fallback_subject_id:
            fallback_subject_text = self._option_text_by_id(
                self._build_options(list(snapshot.get("subjectOptions", []))),
                fallback_subject_id,
            ) or fallback_subject_id
            message_parts = [selection_message] if selection_message else []
            message_parts.append(
                f"Môn đang chọn không có quyền trong khối này, app chuyển sang môn {fallback_subject_text}."
            )
            message_parts.append(
                f"Đã dò nhanh quyền nhập điểm cho môn {fallback_subject_text}: {len(entries)} lớp hợp lệ."
            )
            context, final_message = _finalize_accessible_context_result(
                self,
                page,
                snapshot,
                entries=entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
                preferred_class_id=class_id,
                preferred_subject_id=fallback_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=fallback_subject_id,
            )
            return context, login_message, final_message

        entries = self._discover_accessible_entries_for_current_grade(
            page,
            snapshot,
            grade_id=current_grade_id,
            term_id=current_term_id,
        )
        message_parts = [selection_message] if selection_message else []
        if entries:
            message_parts.append(
                f"Đã dò toàn bộ quyền nhập điểm cho khối đang chọn: {len(entries)} tổ hợp lớp/môn hợp lệ."
            )
            context, final_message = _finalize_accessible_context_result(
                self,
                page,
                snapshot,
                entries=entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
                preferred_class_id=class_id,
                preferred_subject_id=subject_id.strip() or preferred_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.FULL_MATRIX,
            )
            return context, login_message, final_message

        context = self._build_scorebook_context(snapshot)
        self._apply_access_entries_to_context(
            context,
            [],
            grade_id=current_grade_id,
            term_id=current_term_id,
        )
        message_parts.append("Khối/Học kỳ hiện tại không có lớp/môn nào được phép nhập điểm.")
        return context, login_message, " ".join(part for part in message_parts if part).strip()


# NOTE: Monkey-patching for load_accessible_scorebook_context
#       and select_accessible_scorebook_context removed — now in subclass.


class VnEduScoreEntryAutomation(VnEduScoreAutomation):
    """Subclass chuyên biệt cho chức năng nhập điểm bằng giọng nói.

    Thay thế monkey-patching pattern cũ bằng proper method overrides.
    Tất cả _patched_* functions được ủy thác (delegate) từ đây.
    """

    def _load_live_combo_options(self, page: object, combo_id: str) -> list[dict[str, object]]:
        """PERF (accuracy-safe): thoát nhanh chu trình expand ExtJS khi store đã ổn định.

        Bản gốc luôn expand → poll 180ms → chờ stable → collapse (deadline 2.5s) cho
        MỌI combo, kể cả khi store đã nạp đầy. Trong luồng dò quyền, hàm này được gọi
        ~8 lần (grade/class/subject/term × nhiều lần hydrate) nên là bottleneck chính.

        Tối ưu giữ NGUYÊN ngữ nghĩa "stability detection" (đảm bảo 100% chính xác,
        không đọc danh sách cũ sau khi đổi combo cha):
          1. Đọc store trực tiếp. Nếu có >=2 option, đọc xác nhận lần 2 sau một khoảng
             ngắn. Hai lần đọc GIỐNG HỆT nhau => store đã ổn định (không ở giữa quá
             trình reload bất đồng bộ) => trả về ngay, KHÔNG expand.
          2. Nếu chưa ổn định / lazy (<=1 option hoặc 2 lần đọc khác nhau) => expand
             rồi poll như cũ nhưng với ngân sách siết chặt hơn (90ms / 1.5s).

        Cơ chế "2 lần đọc giống nhau mới tin" giống hệt yêu cầu stable của bản gốc,
        nên không nới lỏng độ chính xác; chỉ cắt thời gian chờ ở nhánh đã ổn định.
        """
        normalized_combo_id = str(combo_id or "").strip()
        if not normalized_combo_id:
            return []

        def _signature(items: list[dict[str, object]]) -> tuple[tuple[str, str], ...]:
            return tuple(
                (str(item.get("id", "")).strip(), str(item.get("ten", "")).strip())
                for item in items
            )

        # FAST PATH: store đã ổn định và đầy => xác nhận bằng 2 lần đọc rồi trả ngay.
        first_items = self._read_combo_store_items(page, normalized_combo_id)
        if len(first_items) >= 2:
            try:
                page.wait_for_timeout(70)
            except Exception:
                return first_items
            confirm_items = self._read_combo_store_items(page, normalized_combo_id)
            if confirm_items and _signature(confirm_items) == _signature(first_items):
                return confirm_items
            # 2 lần đọc khác nhau => store có thể đang reload => đi xuống nhánh expand.
            first_items = confirm_items if len(confirm_items) > len(first_items) else first_items

        # SLOW PATH: expand để materialize store lazy, rồi poll chờ ổn định.
        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try {
                    if (cmp.onTriggerClick) { cmp.onTriggerClick(); }
                    else if (cmp.expand) { cmp.expand(); }
                    return true;
                } catch (error) { return false; }
            }""",
                normalized_combo_id,
            )
        except Exception:
            return first_items

        best_items = first_items
        last_signature: tuple[tuple[str, str], ...] = tuple()
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                page.wait_for_timeout(90)
            except Exception:
                break
            current_items = self._read_combo_store_items(page, normalized_combo_id)
            if len(current_items) > len(best_items):
                best_items = current_items
            current_signature = _signature(current_items)
            if current_signature and current_signature == last_signature:
                if current_items:
                    best_items = current_items
                break
            last_signature = current_signature

        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try { if (cmp.collapse) cmp.collapse(); return true; }
                catch (error) { return false; }
            }""",
                normalized_combo_id,
            )
        except Exception:
            pass

        return best_items

    def _can_comment_scorebook(
        self, permission_text: str, enabled_comment_input_count: int
    ) -> bool:
        return _patched_can_comment_scorebook(
            self, permission_text, enabled_comment_input_count
        )

    def _apply_access_entries_to_context(
        self,
        context: ScorebookContext,
        entries: list[object],
        grade_id: str,
        term_id: str,
    ) -> ScorebookContext:
        return _patched_apply_access_entries_to_context(
            self, context, entries, grade_id, term_id
        )

    def _subject_options_by_class_for_current_grade(
        self,
        page: object,
        snapshot: dict[str, object],
        grade_id: str,
        term_id: str,
        class_options: list[ScoreOption],
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        return _patched_subject_options_by_class_for_current_grade(
            self, page, snapshot, grade_id, term_id, class_options
        )

    def _find_visible_input(self, page: object, selectors: list[str]):
        return _patched_find_visible_input(self, page, selectors)

    def _find_username_input(self, page: object):
        return _patched_find_username_input(self, page)

    def _read_login_surface_state(
        self, page: object
    ) -> dict[str, object]:
        return _patched_read_login_surface_state(self, page)

    def _login_if_needed_on_page(
        self,
        page: object,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        return _patched_login_if_needed_on_page(
            self, page, username, password, progress_callback
        )

    def _wait_for_scorebook_permission_snapshot(
        self,
        page: object,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",
    ) -> dict[str, object]:
        return _patched_wait_for_scorebook_permission_snapshot(
            self,
            page,
            expected_class_id,
            expected_subject_id,
            expected_term_id,
            timeout_sec,
            progress_callback,
            progress_message,
        )

    def _select_scorebook_context_on_page(
        self,
        page: object,
        snapshot: dict[str, object],
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[dict[str, object], str]:
        return _patched_select_scorebook_context_on_page(
            self, page, snapshot, grade_id, class_id, subject_id,
            term_id, progress_callback,
        )

    def load_accessible_scorebook_context(
        self,
        username: str = "",
        password: str = "",
    ) -> tuple[object, str, str]:
        return _patched_load_accessible_scorebook_context(
            self, username, password
        )

    def select_accessible_scorebook_context(
        self,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        username: str = "",
        password: str = "",
    ) -> tuple[object, str, str]:
        return _patched_select_accessible_scorebook_context(
            self, grade_id, class_id, subject_id, term_id,
            username, password,
        )


# ---------------------------------------------------------------------------
#  FAST DIRECT FETCH: bypass ExtJS combo cascade, call VNEDU API directly
# ---------------------------------------------------------------------------

_FAST_SCORE_FETCH_JS = """async ({ params, targetColumnKey, windowId }) => {
    const resp = await fetch('/v5/?load=edu.so_diem.nhap', {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
        body: new URLSearchParams(params).toString(),
    });
    const html = await resp.text();
    if (!html || html.length < 100) return { error: 'empty_response' };

    // Inject into live page so future writes work correctly
    const panel = document.getElementById(windowId + '_panel_content');
    if (panel) panel.innerHTML = html;
    const hKhoi = document.getElementById('iKhoi');
    if (hKhoi) hKhoi.value = params.iKhoi || '';
    const hLop = document.getElementById('iLopId');
    if (hLop) hLop.value = params.iLopId || '';
    const hMon = document.getElementById('iMonHocId');
    if (hMon) hMon.value = params.iMonHocId || '';
    const hHK = document.getElementById('iHocKy');
    if (hHK) hHK.value = params.iHocKyId || '';

    // Parse the HTML with DOMParser
    const doc = new DOMParser().parseFromString(html, 'text/html');

    const permCell = Array.from(doc.querySelectorAll('td')).find(td =>
        /quyền hạn/i.test((td.textContent || '').trim()));
    const permissionText = (permCell?.textContent || '').trim();
    const teacherText = (doc.querySelector('#gvbm')?.textContent || '').trim();

    const commentInputs = Array.from(doc.querySelectorAll('input.input_nhan_xet'));
    const enabledCommentCount = commentInputs.filter(
        inp => !inp.disabled && !inp.readOnly).length;

    const table = doc.querySelector('table.tablefix');
    const numHdrRows = 2;

    // --- Enhanced column schema extraction with role inference ---
    // H2 FIX: build the schema FIRST so the target input can be located by its
    // leaf-column index (mirrors the slow path's target_schema.leaf_index),
    // instead of fragile b/c parsing from the column key string. This makes
    // average/static/score columns all resolvable.
    const schemas = [];
    let targetLeafIndex = -1;
    if (table && table.rows.length > 2) {
        const firstDataRow = table.rows[numHdrRows];
        const leafCount = firstDataRow ? firstDataRow.cells.length : 0;

        // Build leaf-aligned header grid (expand colspan/rowspan)
        const hdrGrid = Array.from({length: numHdrRows}, () => new Array(leafCount).fill(''));
        const hdrMeta = Array.from({length: numHdrRows}, () => new Array(leafCount).fill(null));
        for (let r = 0; r < numHdrRows && r < table.rows.length; r++) {
            let col = 0;
            for (let c = 0; c < table.rows[r].cells.length; c++) {
                const cell = table.rows[r].cells[c];
                const text = (cell.textContent || '').trim();
                const cs = cell.colSpan || 1;
                const rs = cell.rowSpan || 1;
                while (col < leafCount && hdrMeta[r][col] !== null) col++;
                for (let dr = 0; dr < rs && (r + dr) < numHdrRows; dr++) {
                    for (let dc = 0; dc < cs; dc++) {
                        const tc = col + dc;
                        if (tc < leafCount && hdrMeta[r + dr][tc] === null) {
                            hdrGrid[r + dr][tc] = text;
                            hdrMeta[r + dr][tc] = {
                                cn: cell.getAttribute('cn') || '',
                                cl: cell.getAttribute('cl') || '',
                            };
                        }
                    }
                }
                col += cs;
            }
        }

        for (let li = 0; li < leafCount; li++) {
            // M2 FIX: scan up to 5 data rows (mirrors slow path sample_cells) so a
            // readonly/transfer first row does not mislabel an editable column.
            const sampleRowLimit = Math.min(table.rows.length, numHdrRows + 5);
            let inp = null;
            let editable = false;
            let sampleValue = '';
            for (let sr = numHdrRows; sr < sampleRowLimit; sr++) {
                const sampleCell = table.rows[sr].cells[li];
                if (!sampleCell) continue;
                const candidate = sampleCell.querySelector('input, textarea');
                if (candidate) {
                    if (inp === null) inp = candidate;
                    if (!candidate.readOnly && !candidate.disabled) editable = true;
                    if (!sampleValue && (candidate.value || '').trim()) sampleValue = (candidate.value || '').trim();
                } else if (!sampleValue) {
                    const cellText = (sampleCell.textContent || '').trim();
                    if (cellText) sampleValue = cellText;
                }
            }
            const td = firstDataRow.cells[li];
            const inputClass = inp ? (inp.className || '') : '';
            const tdClass = (inp && inp.closest ? (inp.closest('td')?.className || '') : (td ? td.className || '' : ''));
            const dataColumn = (inp && inp.closest ? (inp.closest('td')?.getAttribute('data-cot') || '') : (td ? td.getAttribute('data-cot') || '' : ''));
            const blockIndex = inp ? (inp.getAttribute('b') || '') : '';
            const childIndex = inp ? (inp.getAttribute('c') || '') : '';
            const inputName = inp ? (inp.name || '') : '';

            // De-duplicated header path
            const rawPath = [];
            for (let r = 0; r < numHdrRows; r++) {
                const lbl = (hdrGrid[r][li] || '').trim();
                if (lbl && (rawPath.length === 0 || rawPath[rawPath.length - 1] !== lbl))
                    rawPath.push(lbl);
            }
            // Deepest header cn value
            let cnValue = '';
            for (let r = numHdrRows - 1; r >= 0; r--) {
                const m = hdrMeta[r][li];
                if (m && m.cn) { cnValue = m.cn; break; }
            }

            const headerLabel = rawPath.length > 0 ? rawPath[rawPath.length - 1] : ('Col ' + li);
            const normHdr = rawPath.join(' ').toLowerCase();

            // --- Column identity inference (mirrors Python _infer_scorebook_column_identity) ---
            let roleHint = 'static', inputKind = '', columnKey = 'col_' + li;
            const icLow = inputClass.toLowerCase();
            const tdLow = tdClass.toLowerCase();
            if (icLow.includes('input_nhan_xet')) {
                roleHint = 'comment'; inputKind = 'comment'; columnKey = 'comment';
            } else if (icLow.includes('input_diem_tbm') || tdLow.includes('tbhk_tt')) {
                roleHint = 'average'; inputKind = 'score'; columnKey = 'average_term';
            } else if (icLow.includes('input_diem') || icLow.includes('txtdiem')) {
                const sfx = dataColumn || cnValue || (blockIndex + '_' + childIndex);
                roleHint = 'score'; inputKind = 'score';
                columnKey = 'score_' + sfx.replace(/_+$/g, '');
            } else {
                const avgTk = ['\\u0111tbmhk','tbhk','tbhk 1','tbhk 2',
                    'tb c\\u1ea3 n\\u0103m','tb ca nam',
                    'trung b\\u00ecnh h\\u1ecdc k\\u1ef3','trung binh hoc ky',
                    'trung b\\u00ecnh c\\u1ea3 n\\u0103m','trung binh ca nam'];
                const scTk = ['\\u0111i\\u1ec3m thi l\\u1ea1i','diem thi lai',
                    'thi l\\u1ea1i','thi lai'];
                if (avgTk.some(t => normHdr.includes(t))) {
                    const k = (dataColumn || cnValue || headerLabel || ('average_' + li))
                        .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
                    roleHint = 'average'; inputKind = 'score';
                    columnKey = 'average_' + (k || li);
                } else if (scTk.some(t => normHdr.includes(t))) {
                    const k = (dataColumn || cnValue || headerLabel || ('score_' + li))
                        .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
                    roleHint = 'score'; inputKind = 'score';
                    columnKey = 'score_' + (k || li);
                } else if (normHdr.includes('m\\u00e3 hs') || normHdr.includes('ma hs')) {
                    roleHint = 'student_code'; columnKey = 'student_code';
                } else if (normHdr.includes('h\\u1ecd v\\u00e0 t\\u00ean')
                        || normHdr.includes('ho va ten')) {
                    roleHint = 'student_name'; columnKey = 'student_name';
                } else if (normHdr.includes('ng\\u00e0y sinh') || normHdr.includes('ngay sinh')) {
                    roleHint = 'birth_date'; columnKey = 'birth_date';
                } else if (normHdr.includes('li\\u00ean l\\u1ea1c') || normHdr.includes('lien lac')) {
                    roleHint = 'contact'; columnKey = 'contact';
                } else if (normHdr.includes('stt')) {
                    roleHint = 'ordinal'; columnKey = 'ordinal';
                }
            }

            // Display name inference (mirrors Python _scorebook_column_display_name)
            let displayName = dataColumn || headerLabel;
            if (roleHint === 'score' && cnValue && dataColumn)
                displayName = cnValue + ' / ' + dataColumn;
            else if (roleHint === 'score' && cnValue)
                displayName = cnValue;

            // H2 FIX: remember the leaf index that matches the requested column key.
            if (targetLeafIndex < 0 && columnKey === String(targetColumnKey || '')) {
                targetLeafIndex = li;
            }

            schemas.push({
                column_key: columnKey,
                display_name: displayName,
                header_path: rawPath,
                leaf_index: li,
                editable: editable,
                role_hint: roleHint,
                input_kind: inputKind,
                sample_value: sampleValue,
                block_index: blockIndex,
                child_index: childIndex,
                data_column: dataColumn,
                input_name: inputName,
            });
        }
    }

    // H2 FIX: Extract per-student rows using leaf-column indices derived from the
    // schema (not b/c parsed from the key string). This resolves the target input
    // for score / average / static columns alike, mirroring the slow path.
    let codeLeafIndex = -1;
    let nameLeafIndices = [];
    for (const s of schemas) {
        if (s.role_hint === 'student_code' && codeLeafIndex < 0) codeLeafIndex = s.leaf_index;
        if (s.role_hint === 'student_name') nameLeafIndices.push(s.leaf_index);
    }
    if (codeLeafIndex < 0) codeLeafIndex = 1;

    const entries = [];
    if (table && targetLeafIndex >= 0) {
        for (let r = numHdrRows; r < table.rows.length; r++) {
            const row = table.rows[r];
            if (row.cells.length <= targetLeafIndex) continue;
            const codeCell = row.cells[codeLeafIndex];
            const studentCode = codeCell ? (codeCell.textContent || '').trim() : '';
            let fullName = '';
            if (nameLeafIndices.length > 0) {
                fullName = nameLeafIndices
                    .map(idx => (row.cells[idx]?.textContent || '').trim())
                    .filter(Boolean)
                    .join(' ')
                    .trim();
            }
            if (!fullName) {
                // Fallback: first non-numeric, non-date cell among the first 6 columns.
                for (let ci = 0; ci < Math.min(6, row.cells.length); ci++) {
                    const t = (row.cells[ci]?.textContent || '').trim();
                    if (t && !/^\\d+$/.test(t) && !/\\d{2}\\/\\d{2}\\/\\d{4}/.test(t)) { fullName = t; break; }
                }
            }
            const targetCell = row.cells[targetLeafIndex];
            const targetInput = targetCell
                ? targetCell.querySelector('input, textarea, select, span.input_diem, span.input_nhan_xet')
                : null;
            let targetInputName = '';
            let currentScore = '';
            if (targetInput) {
                targetInputName = targetInput.name || targetInput.id || '';
                const tag = String(targetInput.tagName || '').toUpperCase();
                currentScore = tag === 'SPAN'
                    ? (targetInput.textContent || targetInput.innerText || '').trim()
                    : (targetInput.value || '').trim();
            }
            let studentId = targetInput ? (targetInput.getAttribute && targetInput.getAttribute('a') || '') : '';
            if (!studentId) {
                const anyInput = row.querySelector('input[a]');
                if (anyInput) studentId = anyInput.getAttribute('a') || '';
            }
            // Skip fully empty rows (no code, no name, no input).
            if (!studentCode && !fullName && !targetInputName) continue;
            entries.push({
                row_index: r - numHdrRows,
                row_id: studentId || studentCode,
                student_code: studentCode,
                student_name: fullName,
                current_score: currentScore,
                target_input_name: targetInputName,
            });
        }
    }

    return {
        entries,
        permissionText,
        teacherText,
        commentInputCount: commentInputs.length,
        enabledCommentCount,
        schemas,
        targetLeafIndex,
    };
}"""


def _direct_fetch_score_entries(
    automation: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    grade_id: str,
    class_id: str,
    subject_id: str,
    term_id: str,
    target_column_key: str,
) -> dict[str, object] | None:
    """Fetches the score table via a single direct POST, skipping all ExtJS combo cascades.

    Returns a dict with 'entries', 'permissionText', 'teacherText', 'schemas', etc.
    Returns None if the fast path cannot be used.
    """
    school_year = (
        str(snapshot.get("hiddenSchoolYear", "")).strip()
        or str(snapshot.get("currentSchoolYear", "")).strip()
    )
    window_id = str(snapshot.get("windowId", "")).strip()
    if not school_year or not window_id:
        return None

    params = {
        "app_nam_hoc": school_year,
        "nam_hoc": school_year,
        "winid": window_id,
        "iLopId": str(class_id),
        "iKhoi": str(grade_id),
        "iMonHocId": str(subject_id),
        "iHocKyId": str(term_id),
        "deleteMode": "0",
        "thang": "0",
        "muc": "",
        "dot_diem_id": "0",
        "dot_diem_data": "",
        "dot_id": "0",
        "dm_cua_mon_hoc": "0",
        "dm_cua_khoi_hoc": "0",
        "dm_cua_toi": "0",
    }
    result = page.evaluate(
        _FAST_SCORE_FETCH_JS,
        {
            "params": params,
            "targetColumnKey": target_column_key,
            "windowId": window_id,
        },
    )
    if not isinstance(result, dict) or result.get("error"):
        return None
    return result



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


def _audio_meter_level(audio_chunk: Any) -> float:
    if np is None or audio_chunk is None:
        return 0.0
    try:
        audio_array = np.asarray(audio_chunk, dtype=np.float32)
    except Exception:
        return 0.0
    if audio_array.size <= 0:
        return 0.0
    peak = float(np.max(np.abs(audio_array)))
    if peak <= 1e-5:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(audio_array))))
    rms_db = 20.0 * math.log10(max(rms, 1e-5))
    normalized_db = (rms_db - VOICE_METER_FLOOR_DB) / max(VOICE_METER_CEIL_DB - VOICE_METER_FLOOR_DB, 1.0)
    normalized_peak = min(1.0, peak * 1.75)
    return max(0.0, min(1.0, max(normalized_db, normalized_peak * 0.82)))


class PTTCaptureStream:
    """Keeps the microphone stream warm so PTT starts recording immediately."""

    def __init__(
        self,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_duration: float = 0.05,
        preroll_ms: int = 220,
        max_record_seconds: int = 20,
    ) -> None:
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self.chunk_size = max(1, int(sample_rate * chunk_duration))
        self._max_chunks = max(1, int(max_record_seconds / chunk_duration))
        self._preroll_chunks = max(1, int(preroll_ms / (chunk_duration * 1000)))

        self._ring: deque[Any] = deque(maxlen=max(self._preroll_chunks * 4, 40))
        self._record_chunks: list[Any] = []
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._running = False
        self._recording = False
        self._meter_level = 0.0
        self._meter_updated_at = 0.0

    def start(self) -> bool:
        if np is None or sd is None:
            return False
        with self._lock:
            if self._running and self._stream is not None:
                return True

        try:
            def _on_audio(indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
                try:
                    chunk = np.array(indata, dtype=np.float32, copy=True)
                except Exception:
                    return
                live_level = _audio_meter_level(chunk)
                with self._lock:
                    if not self._running:
                        return
                    self._ring.append(chunk)
                    if live_level >= self._meter_level:
                        self._meter_level = (self._meter_level * 0.32) + (live_level * 0.68)
                    else:
                        self._meter_level = (self._meter_level * 0.84) + (live_level * 0.16)
                    self._meter_updated_at = time.perf_counter()
                    if self._recording:
                        if len(self._record_chunks) < self._max_chunks:
                            self._record_chunks.append(chunk)
                        else:
                            self._recording = False

            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.chunk_size,
                callback=_on_audio,
            )
            stream.start()
            with self._lock:
                self._stream = stream
                self._running = True
                self._recording = False
                self._record_chunks = []
                self._meter_level = 0.0
                self._meter_updated_at = 0.0
            return True
        except Exception:
            with self._lock:
                self._stream = None
                self._running = False
                self._recording = False
                self._record_chunks = []
                self._meter_level = 0.0
                self._meter_updated_at = 0.0
            return False

    def begin_recording(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            preroll = list(self._ring)[-self._preroll_chunks:] if self._ring else []
            self._record_chunks = [chunk.copy() for chunk in preroll]
            self._recording = True
            return True

    def end_recording(self) -> Any | None:
        with self._lock:
            self._recording = False
            chunks = self._record_chunks
            self._record_chunks = []
        if not chunks:
            return None
        try:
            return np.concatenate(chunks, axis=0)
        except Exception:
            return None

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._running = False
            self._recording = False
            self._record_chunks = []
            self._meter_level = 0.0
            self._meter_updated_at = 0.0
            self._ring.clear()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def current_level(self) -> float:
        with self._lock:
            if not self._running:
                return 0.0
            level = float(self._meter_level)
            updated_at = float(self._meter_updated_at or 0.0)
        if updated_at <= 0.0:
            return max(0.0, min(1.0, level))
        elapsed = max(0.0, time.perf_counter() - updated_at)
        decayed_level = max(0.0, level - (elapsed * 1.8))
        return max(0.0, min(1.0, decayed_level))


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


def parse_manual_score_text(score_text: object) -> float | None:
    """Parses a manually typed VNEDU score without accepting surrounding words."""
    if score_text is None:
        return None
    normalized_text = str(score_text).strip()
    if not normalized_text or not MANUAL_SCORE_PATTERN.fullmatch(normalized_text):
        return None
    try:
        score_value = float(normalized_text.replace(",", "."))
    except ValueError:
        return None
    return score_value if 0 <= score_value <= 10 else None


def _format_score_value(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _clamp_score_value(value: float, minimum: float = 0.0, maximum: float = 10.0) -> float:
    """Keeps one score inside the valid VNEDU range."""
    return max(minimum, min(maximum, float(value)))


def _round_score_to_one_decimal(value: object) -> str:
    """Rounds one score to a single decimal place using half-up semantics."""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    normalized = text.replace(",", ".")
    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation:
        return text
    if decimal_value.as_tuple().exponent >= -1:
        return _format_score_value(normalized)
    rounded_value = decimal_value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return _format_score_value(str(rounded_value))


def _build_conflict_resolution_scores(existing_value: float, incoming_value: float) -> dict[str, float]:
    """Builds the derived score options shown in the conflict dialog."""
    return {
        "average": (existing_value + incoming_value) / 2,
        "plus_one": _clamp_score_value(existing_value + 1.0),
        "accumulate": _clamp_score_value(existing_value + incoming_value),
    }


def _build_apply_confirmation_summary(
    payload: list[dict[str, str]],
    rows_by_key: dict[str, object],
    preview_limit: int = 10,
) -> str:
    """Builds the confirmation summary using the exact scores that will be submitted."""
    summary_lines: list[str] = []
    for item in payload[:preview_limit]:
        row = rows_by_key.get(item.get("row_key", ""))
        row_name = getattr(row, "student_name", "").strip() if row is not None else ""
        name = row_name or item.get("student_name") or item.get("row_key", "?")
        summary_lines.append(f"  • {name}: {item.get('proposed_score', '?')}")
    if len(payload) > preview_limit:
        summary_lines.append(f"  ... và {len(payload) - preview_limit} học sinh nữa")
    return "\n".join(summary_lines)


def _build_score_apply_payload(
    rows: list[object],
    *,
    score_field: str,
) -> list[dict[str, str]]:
    """Builds the score apply payload from one chosen score source on each row."""
    payload: list[dict[str, str]] = []
    for row in rows:
        score_value = parse_manual_score_text(getattr(row, score_field, ""))
        proposed_score = _format_score_value(score_value)
        target_input_name = str(getattr(row, "target_input_name", "") or "").strip()
        if not proposed_score or not target_input_name:
            continue
        payload.append(
            {
                "row_key": str(getattr(row, "row_key", "") or ""),
                "row_index": str(getattr(row, "row_index", "") or ""),
                "row_id": str(getattr(row, "row_id", "") or ""),
                "student_code": str(getattr(row, "student_code", "") or ""),
                "student_name": str(getattr(row, "student_name", "") or ""),
                "current_score": str(getattr(row, "current_score", "") or ""),
                "target_input_name": target_input_name,
                "proposed_score": proposed_score,
            }
        )
    return payload


def _is_text_input_focus(widget: object | None) -> bool:
    """Returns whether the focused widget should keep receiving Space normally."""
    if widget is None:
        return False
    try:
        widget_class = str(widget.winfo_class() or "").strip().lower()
    except Exception:
        widget_class = widget.__class__.__name__.strip().lower()
    return widget_class == "text" or widget_class.endswith("entry") or widget_class.endswith("combobox")


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


class VnEduStandaloneApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")  # IMP-D4
        self.root.configure(background=APP_BG)
        target_w, target_h, target_x, target_y = fit_geometry_to_work_area(
            self.root,
            preferred_w=1500,
            preferred_h=920,
            margin=12,
            min_w=1040,
            min_h=660,
            chrome_h=52,
        )
        self.root.geometry(f"{target_w}x{target_h}+{target_x}+{target_y}")
        self.root.minsize(min(1120, target_w), min(700, target_h))
        _work_x, _work_y, _work_w, _work_h = get_tk_work_area(self.root)
        self.root.maxsize(_work_w, _work_h)

        self.port_var = tk.StringVar(value="9224")
        self.url_var = tk.StringVar(value="https://vemzezsoasgdsoctrang.vnedu.vn/v5/")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Chưa kết nối")
        self.feature_name_var = tk.StringVar(value="PTT nhập điểm")

        self.grade_var = tk.StringVar(value="(chưa đọc)")
        self.class_var = tk.StringVar(value="(chưa đọc)")
        self.subject_var = tk.StringVar(value="(chưa đọc)")
        self.term_var = tk.StringVar(value="(chưa đọc)")
        self.window_title_var = tk.StringVar(value="(chưa có)")
        self.teacher_var = tk.StringVar(value="(chưa có)")
        self.permission_var = tk.StringVar(value="(chưa có)")
        self.column_count_var = tk.StringVar(value="0")
        self.comment_input_var = tk.StringVar(value="0")

        self.target_score_column_var = tk.StringVar(value="(chưa dò)")
        self.auto_scan_rows_var = tk.BooleanVar(value=True)  # always True (auto-apply on combo change)
        self.auto_save_scores_var = tk.BooleanVar(value=True)
        self.voice_status_var = tk.StringVar(value="⏸️ Bộ đàm đang tắt")
        self.voice_summary_var = tk.StringVar(value="Hãy load Sổ điểm rồi chọn cột điểm đích để bắt đầu.")

        self.grade_label_to_id: Dict[str, str] = {}
        self.class_label_to_id: Dict[str, str] = {}
        self.subject_label_to_id: Dict[str, str] = {}
        self.term_label_to_id: Dict[str, str] = {}
        self.target_score_label_to_key: Dict[str, str] = {}
        self.current_context: ScorebookContext | None = None
        self._full_access_scope_cache: dict[tuple[str, str], list[object]] = {}
        self._subject_access_scope_cache: dict[tuple[str, str, str], list[object]] = {}
        self._empty_access_scope_target_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self._loaded_access_cache_namespace = ""
        self._suspend_context_autosync = False
        self._context_autosync_delay_ms = 650
        self._context_autosync_after_id: str | None = None
        self._last_applied_context_ids: tuple[str, str, str, str] = ("", "", "", "")
        self._invalidated_context_fields: set[str] = set()
        self._suspend_target_score_autoscan = False
        self._last_scanned_target_key = ""
        self._auto_scan_after_id: str | None = None
        self._retry_apply_after_scan = False
        self._repair_scan_backup_rows: dict[str, ScoreStudentRow] | None = None
        self._repair_scan_backup_undo: list[UndoRecord] | None = None

        self._busy = False
        self._busy_widgets: list[tuple[tk.Misc, str]] = []
        self._foreground_task_token = 0
        self._progress_value = 0.0
        self._progress_message = "Sẵn sàng"
        self._automation_lock = threading.Lock()
        self._score_data_lock = threading.Lock()  # BUG-01 FIX: bảo vệ _score_rows_by_key + indices giữa main thread và PTT worker
        self._voice_state_lock = threading.Lock()
        # THREAD-SAFETY INVARIANTS:
        # - _score_data_lock bảo vệ: _score_rows_by_key, _student_*_index, _voice_match_cache
        # - Mọi READ/WRITE từ PTT worker thread PHẢI acquire lock trước
        # - Main thread PHẢI acquire lock khi WRITE (clear, replace, rebuild indices)
        # - Main thread read-only (UI callbacks) được bảo vệ bởi GIL cho atomic reads
        self._progress_queue: Queue[tuple[int, float, str]] = Queue()
        self._result_queue: Queue[
            tuple[object | None, Exception | None, Callable[[object], None], Callable[[Exception], None] | None]
        ] = Queue()
        self._poll_after_id: str | None = None

        self._score_rows_by_key: dict[str, ScoreStudentRow] = {}
        self._tree_item_by_key: dict[str, str] = {}
        self._student_full_index: dict[str, list[str]] = {}
        self._student_last_name_index: dict[str, list[str]] = {}
        self._student_last_two_index: dict[str, list[str]] = {}
        self._student_token_index: dict[str, set[str]] = {}
        self._student_phonetic_full_index: dict[str, list[str]] = {}
        self._student_phonetic_last_name_index: dict[str, list[str]] = {}
        self._student_phonetic_last_two_index: dict[str, list[str]] = {}
        self._student_phonetic_token_index: dict[str, set[str]] = {}
        self._undo_stack: list[UndoRecord] = []
        self._tree_editor: ttk.Entry | None = None
        self._tree_editor_info: dict[str, str] = {}

        self._voice_hints: list[str] = []
        self._voice_hints_primary: list[str] = []
        self._voice_hints_extended: list[str] = []
        self._voice_recent_names: deque[str] = deque(maxlen=VOICE_HINT_RECENT_LIMIT)  # PERF #4: LRU tên dùng gần đây
        self._voice_match_cache: dict[str, tuple[str, int]] = {}
        self._student_aliases: dict[str, list[str]] = {}  # student_name → [alias1, alias2, ...]
        self._recognizer: Any | None = None
        self._recognizer_phrase_list_supported: bool | None = None
        self._ptt_capture: PTTCaptureStream | None = None
        self._space_pressed = False
        self._ptt_enabled = False
        self._ptt_recording = False
        self._ptt_audio_buffer: Any | None = None
        self._ptt_lock = threading.Lock()
        self._ptt_queue: Queue[tuple[int, int, Any, float] | None] = Queue()
        self._ptt_worker: threading.Thread | None = None
        self._ptt_worker_shutdown = False
        self._ptt_request_seq = 0
        self._ptt_latest_request_id = 0
        self._mic_consecutive_errors = 0  # IMP-C2: Đếm lỗi mic liên tiếp
        self._ptt_roster_revision = 0
        self._ptt_bind_ids: dict[str, tuple[str, str]] = {}
        # PERF #1: Pool 2 thread để chạy 2 attempt Google Speech song song.
        self._voice_recognize_executor: ThreadPoolExecutor | None = None
        self._voice_executor_lock = threading.Lock()
        self._voice_meter_level = 0.0
        self._voice_meter_peak = 0.0
        self._voice_pending_row_key: str | None = None
        self._voice_pending_at: float = 0.0
        self._voice_pending_roster_revision: int = 0
        # KHMER #B: Voice picker state cho cặp tên trùng phonetic (Trâm/Trăm).
        # Lưu danh sách ứng viên khi `_match_student` phát hiện tied; PTT tiếp theo
        # user nói "một"/"hai"/"ba"... để chọn từ list này.
        self._voice_tied_candidates: list[str] = []
        self._voice_tied_at: float = 0.0
        self._voice_tied_revision: int = 0
        # L3 FIX: chặn PTT result mới khi dialog xung đột điểm đang mở (dialog
        # grab_set nhưng không set _busy, nên cần cờ riêng để không apply chồng
        # hoặc mở dialog thứ hai từ một kết quả PTT đã queue trước đó).
        self._voice_conflict_dialog_open = False
        self._compact_layout = False

        self._build_ui()
        self._load_config()
        self._load_aliases()
        self._ensure_access_cache_loaded()
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self.root.bind("<Control-z>", self._on_undo_shortcut, add="+")
        # IMP-B1: Ctrl+S shortcut để ghi điểm lên web
        self.root.bind("<Control-s>", lambda _e: self.on_apply_pending_scores(), add="+")
        # IMP-B2: Escape hủy recording khi đang giữ Space
        self.root.bind("<Escape>", self._on_escape_cancel_recording, add="+")
        # IMP-D6: F1 hiện dialog phím tắt
        self.root.bind("<F1>", self._show_help_dialog, add="+")
        # Theo dõi trạng thái cửa sổ để ẩn/hiện left panel khi Restore Down / Maximize
        self._prev_window_state = "normal"
        self.root.bind("<Configure>", self._on_window_state_change, add="+")
        self._poll_after_id = self.root.after(50, self._poll_results)
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.after_idle(self._sync_responsive_layout)

    def _build_ui(self) -> None:
        section_padding = (10, 7, 10, 8)
        main = ttk.Frame(self.root, padding=(14, 12, 14, 10))
        self._main_frame = main
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=0, minsize=APP_SIDEBAR_WIDTH)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left_panel = ttk.Frame(main)
        self._left_panel = left_panel
        left_panel.configure(width=APP_SIDEBAR_WIDTH)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_panel.grid_propagate(False)
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(2, weight=0)

        right_panel = ttk.Frame(main)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(0, weight=6)
        right_panel.rowconfigure(1, weight=1)

        session = ttk.LabelFrame(left_panel, text="1. Phiên VNEDU", padding=section_padding)
        session.grid(row=0, column=0, sticky="ew")
        ttk.Label(session, text="CDP Port:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        port_entry = ttk.Entry(session, textvariable=self.port_var, width=12)
        port_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self._register_busy(port_entry, "normal")
        self._create_tooltip(port_entry, "Chrome DevTools debug port (mặc định: 9222)")
        ttk.Label(session, text="URL:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        url_entry = ttk.Entry(session, textvariable=self.url_var, width=28, state="readonly")
        url_entry.grid(row=1, column=1, columnspan=3, padx=6, pady=6, sticky="we")
        self._create_tooltip(url_entry, "URL trang VNEDU — tự động lấy từ trình duyệt Chrome")
        credentials_row = ttk.Frame(session)
        credentials_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=6, pady=6)
        credentials_row.columnconfigure(0, weight=1)

        account_row = ttk.Frame(credentials_row)
        account_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        account_row.columnconfigure(1, weight=1)
        ttk.Label(account_row, text="Tài khoản:").grid(row=0, column=0, padx=(0, 8), pady=0, sticky="w")
        user_entry = ttk.Entry(account_row, textvariable=self.username_var, width=20)
        user_entry.grid(row=0, column=1, sticky="ew")
        self._register_busy(user_entry, "normal")
        self._create_tooltip(user_entry, "Tên đăng nhập VNEDU (thường là SĐT giáo viên)")
        user_entry.bind("<Return>", lambda _e: self.on_load_scorebook_shell())

        password_group = ttk.Frame(credentials_row)
        password_group.grid(row=1, column=0, sticky="ew")
        password_group.columnconfigure(1, weight=1)
        ttk.Label(password_group, text="Mật khẩu:").grid(row=0, column=0, padx=(0, 8), pady=0, sticky="w")
        pw_row = ttk.Frame(password_group)
        pw_row.grid(row=0, column=1, sticky="ew")
        pw_row.columnconfigure(0, weight=1)
        self.password_entry = ttk.Entry(
            pw_row,
            textvariable=self.password_var,
            width=20,
            show=password_entry_show_value(False),
        )
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self._register_busy(self.password_entry, "normal")
        self.password_entry.bind("<Return>", lambda _e: self.on_load_scorebook_shell())
        show_check = ttk.Checkbutton(
            pw_row,
            text="Hiện mật khẩu",
            variable=self.show_password_var,
            command=self._apply_password_visibility,
        )
        show_check.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self._register_busy(show_check, "normal")
        button_row = ttk.Frame(session)
        button_row.grid(row=3, column=0, columnspan=4, padx=6, pady=(0, 8), sticky="we")
        button_row.columnconfigure(1, weight=1)
        load_button = tk.Button(
            button_row,
            text="ĐĂNG NHẬP + VÀO SỔ ĐIỂM",
            command=self.on_load_scorebook_shell,
            bg=APP_WARNING,
            fg="#111827",
            activebackground="#eab308",
            activeforeground="#111827",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=14,
            pady=2,
            highlightthickness=0,
        )
        load_button.pack(side=tk.LEFT, padx=(0, 6))
        self._bind_hover(load_button, "#eab308")
        self._register_busy(load_button, "normal")
        self.session_status_label = ttk.Label(
            button_row,
            textvariable=self.status_var,
            foreground="#2f5d50",
            wraplength=210,
            justify=tk.LEFT,
        )
        self.session_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        self._bind_dynamic_wrap(self.session_status_label, reference=button_row, padding=245, min_width=160)
        session.columnconfigure(1, weight=1)
        session.columnconfigure(3, weight=1)

        context = ttk.LabelFrame(left_panel, text="2. Ngữ cảnh Sổ điểm", padding=section_padding)
        context.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(context, text="Khối:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.grade_combo = ttk.Combobox(context, textvariable=self.grade_var, state="disabled", width=18)
        self.grade_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")
        self.grade_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.grade_combo, "readonly")
        ttk.Label(context, text="Lớp:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.class_combo = ttk.Combobox(context, textvariable=self.class_var, state="disabled", width=18)
        self.class_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")
        self.class_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.class_combo, "readonly")
        ttk.Label(context, text="Môn:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.subject_combo = ttk.Combobox(context, textvariable=self.subject_var, state="disabled", width=18)
        self.subject_combo.grid(row=1, column=1, padx=6, pady=6, sticky="we")
        self.subject_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.subject_combo, "readonly")
        ttk.Label(context, text="Học kỳ:").grid(row=1, column=2, padx=6, pady=6, sticky="w")
        self.term_combo = ttk.Combobox(context, textvariable=self.term_var, state="disabled", width=18)
        self.term_combo.grid(row=1, column=3, padx=6, pady=6, sticky="we")
        self.term_combo.bind("<<ComboboxSelected>>", self._on_context_combo_selected, add="+")
        self._register_busy(self.term_combo, "readonly")
        self.progress_canvas = tk.Canvas(
            context,
            height=28,
            background="#eefdf3",
            highlightthickness=1,
            highlightbackground="#bbf7d0",
            relief=tk.FLAT,
        )
        self.progress_canvas.grid(row=2, column=0, columnspan=4, padx=6, pady=(6, 4), sticky="ew")
        self._progress_fill_id = self.progress_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")
        self._progress_text_id = self.progress_canvas.create_text(
            8,
            14,
            anchor="w",
            fill="#1f4729",
            font=("Segoe UI", 9, "bold"),
            text=build_progress_caption(0.0, self._progress_message),
        )
        self.progress_canvas.bind("<Configure>", lambda _e: self._render_progress())
        context.columnconfigure(1, weight=1)
        context.columnconfigure(3, weight=1)

        info = ttk.LabelFrame(left_panel, text="3. Thông tin phiên", padding=section_padding)
        info.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        for row, title, var in [
            (0, "Cửa sổ:", self.window_title_var),
            (1, "Giáo viên:", self.teacher_var),
            (2, "Quyền:", self.permission_var),
            (3, "Số cột:", self.column_count_var),
            (4, "Ô nhận xét:", self.comment_input_var),
        ]:
            ttk.Label(info, text=title).grid(row=row, column=0, padx=6, pady=4, sticky="nw")
            info_value = ttk.Label(info, textvariable=var, justify=tk.LEFT, wraplength=320)
            info_value.grid(
                row=row,
                column=1,
                padx=6,
                pady=4,
                sticky="ew",
            )
            self._bind_dynamic_wrap(info_value, reference=info, padding=54, min_width=200)
        info_help = ttk.Label(
            info,
            text="App sẽ quét danh sách học sinh, nhận dạng tên + điểm và chuẩn bị hàng chờ ghi xuống live browser.",
            wraplength=320,
            justify=tk.LEFT,
        )
        info.rowconfigure(6, weight=1)
        info.columnconfigure(1, weight=1)

        score_frame = ttk.LabelFrame(right_panel, text="4. Nhập Điểm Bằng Giọng Nói", padding=(10, 8, 10, 10))
        score_frame.grid(row=0, column=0, sticky="nsew")
        score_frame.columnconfigure(0, weight=1)

        controls = ttk.Frame(score_frame)
        controls.pack(fill=tk.X, pady=(2, 0))
        for column in range(4):
            controls.columnconfigure(column, weight=1, uniform="score_controls")
        ttk.Label(controls, text="Cột điểm đích:").grid(row=0, column=0, padx=(0, 6), pady=4, sticky="w")
        self.target_score_combo = ttk.Combobox(
            controls,
            textvariable=self.target_score_column_var,
            state="readonly",
            width=24,
        )
        self.target_score_combo.grid(row=0, column=1, columnspan=3, padx=(0, 0), pady=4, sticky="ew")
        self.target_score_combo.bind("<<ComboboxSelected>>", self._on_target_score_selected, add="+")
        self._register_busy(self.target_score_combo, "readonly")
        self.btn_ptt_toggle = tk.Button(
            controls,
            text="🎤 BỘ ĐÀM: TẮT",
            command=self.toggle_ptt,
            bg="#64748b",
            fg="#ffffff",
            activebackground="#475569",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=12,
            pady=2,
            highlightthickness=0,
        )
        self.btn_ptt_toggle.grid(row=1, column=0, padx=(0, 6), pady=(8, 4), sticky="ew")
        self._bind_hover(self.btn_ptt_toggle, "#475569")
        self._register_busy(self.btn_ptt_toggle, "normal")
        clear_button = ttk.Button(controls, text="Xóa điểm chờ", command=self.on_clear_pending_scores)
        clear_button.grid(row=1, column=1, padx=(0, 6), pady=(8, 4), sticky="ew")
        self._register_busy(clear_button, "normal")
        round_button = ttk.Button(controls, text="LÀM TRÒN ĐIỂM", command=self.on_round_pending_scores)
        round_button.grid(row=1, column=2, padx=(0, 6), pady=(8, 4), sticky="ew")
        self._register_busy(round_button, "normal")
        apply_score_button = tk.Button(
            controls,
            text="GHI ĐIỂM LÊN WEB",
            command=self.on_apply_pending_scores,
            bg=APP_SUCCESS,
            fg="#ffffff",
            activebackground="#15803d",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=12,
            pady=2,
            highlightthickness=0,
        )
        apply_score_button.grid(row=1, column=3, padx=(0, 0), pady=(8, 4), sticky="ew")
        self._bind_hover(apply_score_button, "#15803d")
        self._register_busy(apply_score_button, "normal")
        auto_save_check = ttk.Checkbutton(
            controls,
            text="Tự bấm Lưu sau khi ghi",
            variable=self.auto_save_scores_var,
        )
        auto_save_check.grid(row=2, column=0, padx=(0, 12), pady=(4, 0), sticky="w")
        self._register_busy(auto_save_check, "normal")
        self.voice_meter_canvas = tk.Canvas(
            controls,
            height=28,
            background="#eefdf3",
            highlightthickness=1,
            highlightbackground="#bbf7d0",
            relief=tk.FLAT,
        )
        self.voice_meter_canvas.grid(row=2, column=1, columnspan=3, padx=(6, 0), pady=(4, 0), sticky="ew")
        self._voice_meter_fill_id = self.voice_meter_canvas.create_rectangle(0, 0, 0, 0, fill="#2fa34a", outline="")
        self._voice_meter_peak_id = self.voice_meter_canvas.create_line(0, 4, 0, 24, fill="#176a34", width=2)
        self._voice_meter_text_id = self.voice_meter_canvas.create_text(
            10,
            14,
            anchor="w",
            fill="#20532b",
            font=("Segoe UI", 9, "bold"),
            text="Micro realtime: bộ đàm đang tắt",
        )
        self.voice_meter_canvas.bind("<Configure>", lambda _e: self._render_voice_meter())
        controls_help = ttk.Label(
            controls,
            text="Chọn cột điểm để app tự quét danh sách. Bạn vẫn có thể sửa trực tiếp cột Điểm chờ trong bảng xem trước.",
            wraplength=520,
            justify=tk.LEFT,
        )

        voice_row = ttk.Frame(score_frame)
        voice_row.pack(fill=tk.X, pady=(6, 6))
        voice_row.columnconfigure(0, weight=1)
        voice_status = ttk.Label(voice_row, textvariable=self.voice_status_var, foreground="#1f6f43", justify=tk.LEFT)
        voice_status.grid(
            row=0,
            column=0,
            sticky="ew",
        )
        self._bind_dynamic_wrap(voice_status, reference=voice_row, padding=24, min_width=180)
        voice_help = ttk.Label(
            voice_row,
            text="Giữ Space: đọc 'Tên + điểm' hoặc tách riêng 'Tên' rồi 'điểm'. Nói 'xóa' để undo. Ctrl+Z / Delete / Double-click để sửa.",
            wraplength=540,
            justify=tk.LEFT,
        )

        summary_panel = tk.Frame(
            score_frame,
            bg="#eff6ff",
            highlightthickness=1,
            highlightbackground="#bfdbfe",
            bd=0,
        )
        summary_panel.pack(fill=tk.X, pady=(0, 8))
        summary_panel.grid_columnconfigure(0, weight=1)
        summary_label = tk.Label(
            summary_panel,
            textvariable=self.voice_summary_var,
            bg="#eff6ff",
            fg="#1e3a8a",
            anchor="w",
            justify=tk.LEFT,
            wraplength=580,
            padx=10,
            pady=8,
        )
        summary_label.grid(row=0, column=0, sticky="ew")
        self._bind_dynamic_wrap(summary_label, reference=summary_panel, padding=24, min_width=260)

        tree_frame = ttk.Frame(score_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(1, weight=1)
        # IMP-B7: Search/filter box để tìm học sinh nhanh
        search_row = ttk.Frame(tree_frame)
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="🔍 Tìm:").grid(row=0, column=0, padx=(0, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_args: self._filter_score_tree())
        search_entry = ttk.Entry(search_row, textvariable=self._search_var, width=30)
        search_entry.grid(row=0, column=1, sticky="ew")
        tree_container = ttk.Frame(tree_frame)
        tree_container.grid(row=1, column=0, sticky="nsew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)
        columns = (
            "row_index",
            "student_name",
            "current_score",
            "pending_score",
            "recognized_text",
            "match_score",
            "status",
        )
        self.preview_tree = ttk.Treeview(
            tree_container,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=17,
        )
        headings = {
            "row_index": "STT",
            "student_name": "Họ tên",
            "current_score": "Điểm hiện tại",
            "pending_score": "Điểm chờ ghi",
            "recognized_text": "Lần nhận dạng gần nhất",
            "match_score": "Khớp",
            "status": "Trạng thái",
        }
        widths = {
            "row_index": 56,
            "student_name": 320,
            "current_score": 100,
            "pending_score": 108,
            "recognized_text": 300,
            "match_score": 72,
            "status": 160,
        }
        min_widths = {
            "row_index": 48,
            "student_name": 240,
            "current_score": 88,
            "pending_score": 96,
            "recognized_text": 210,
            "match_score": 64,
            "status": 100,
        }
        anchors = {
            "row_index": tk.CENTER,
            "student_name": tk.W,
            "current_score": tk.CENTER,
            "pending_score": tk.CENTER,
            "recognized_text": tk.W,
            "match_score": tk.CENTER,
            "status": tk.W,
        }
        for column in columns:
            self.preview_tree.heading(column, text=headings[column], anchor=anchors[column])
            self.preview_tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                anchor=anchors[column],
                stretch=column in {"student_name", "recognized_text", "status"},
            )
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.preview_tree.yview)
        tree_scroll_y.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        tree_scroll_x = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.preview_tree.xview)
        tree_scroll_x.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.preview_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.preview_tree.bind("<Double-1>", self._on_tree_double_click)
        self.preview_tree.bind("<Delete>", self._on_tree_delete)
        self.preview_tree.bind("<Button-3>", self._on_tree_right_click)
        self.preview_tree.tag_configure("pending", foreground="#9a3412", background="#fff7ed")
        self.preview_tree.tag_configure("saved", foreground="#166534", background="#ecfdf5")
        self.preview_tree.tag_configure("filled", foreground="#854d0e", background="#fef9c3")
        self.preview_tree.tag_configure("error", foreground="#991b1b", background="#fee2e2")
        self._autosize_student_name_column()

        log_frame = ttk.LabelFrame(right_panel, text="6. Nhật ký", padding=section_padding)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=7, wrap="word", font=("Consolas", 10), state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("log_success", background="#d4edda")
        self.log_text.tag_configure("log_warning", background="#f8d7da")
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.log_text.configure(yscrollcommand=scroll.set)
        self._render_progress()

    def _register_busy(self, widget: tk.Misc, normal_state: str) -> None:
        self._busy_widgets.append((widget, normal_state))

    def _bind_hover(self, btn: tk.Button, hover_bg: str) -> None:
        """Adds state-aware mouse-enter/leave hover color to a tk.Button."""
        def on_enter(_event: tk.Event) -> None:
            try:
                btn._hover_prev_bg = btn.cget("bg")  # type: ignore[attr-defined]
                btn.configure(bg=hover_bg)
            except tk.TclError:
                pass

        def on_leave(_event: tk.Event) -> None:
            try:
                btn.configure(bg=getattr(btn, "_hover_prev_bg", btn.cget("bg")))
            except tk.TclError:
                pass

        btn.bind("<Enter>", on_enter, add="+")
        btn.bind("<Leave>", on_leave, add="+")

    def _on_root_destroy(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self.root:
            return
        self._stop_tree_editor(commit=False)
        self._disable_ptt()
        self._stop_ptt_worker()
        self._shutdown_voice_recognize_executor()  # PERF #1: dọn pool song song
        self._cancel_context_autosync()
        # BUG-07 FIX: Cancel pending auto-scan để tránh TclError sau khi root bị destroy
        self._cancel_pending_auto_scan()
        # BUG-11 FIX: Lưu config và access cache trước khi đóng app
        try:
            self._save_config()
        except Exception as _save_err:
            print(f"[DEBUG] Config save on close failed: {_save_err}")
        try:
            self._save_access_cache()
        except Exception as _cache_err:
            print(f"[DEBUG] Access cache save on close failed: {_cache_err}")
        if self._poll_after_id is None:
            return
        try:
            self.root.after_cancel(self._poll_after_id)
        except (tk.TclError, RuntimeError):
            pass
        self._poll_after_id = None

    def _on_window_state_change(self, event: tk.Event) -> None:
        """Switch to a compact workspace only when the actual window width is narrow."""
        if event.widget is not self.root:
            return
        self._sync_responsive_layout()

    def _sync_responsive_layout(self) -> None:
        """Show/hide secondary panels based on the current usable window width."""
        try:
            current_state = self.root.state()
            current_width = int(self.root.winfo_width())
        except tk.TclError:
            return
        if current_width <= 1:
            try:
                self.root.after(50, self._sync_responsive_layout)
            except tk.TclError:
                pass
            return
        compact = current_width < APP_COMPACT_WIDTH
        if (
            current_state == self._prev_window_state
            and compact == self._compact_layout
            and bool(self._left_panel.winfo_ismapped())
        ):
            return
        self._prev_window_state = current_state
        self._compact_layout = compact
        # Giữ sidebar luôn thấy được; chỉ rút gọn bảng khi cửa sổ thật sự hẹp.
        self._left_panel.grid()
        sidebar_width = max(
            APP_SIDEBAR_MIN_WIDTH,
            min(APP_SIDEBAR_WIDTH, int(current_width * 0.34)),
        )
        try:
            self._left_panel.configure(width=sidebar_width)
        except tk.TclError:
            pass
        self._main_frame.columnconfigure(0, weight=0, minsize=sidebar_width, uniform="")
        self._main_frame.columnconfigure(1, weight=1, uniform="")
        self._apply_tree_headings(compact=compact)

    def _apply_tree_headings(self, *, compact: bool = False) -> None:
        """IMP-D2: Cập nhật column headings — rút gọn khi compact (restore-down)."""
        if not hasattr(self, "preview_tree"):
            return
        full_headings = {
            "row_index": "STT",
            "student_name": "Họ tên",
            "current_score": "Điểm hiện tại",
            "pending_score": "Điểm chờ ghi",
            "recognized_text": "Lần nhận dạng gần nhất",
            "match_score": "Khớp",
            "status": "Trạng thái",
        }
        compact_headings = {
            "row_index": "#",
            "student_name": "Họ tên",
            "current_score": "HT",
            "pending_score": "Chờ",
            "recognized_text": "Nhận dạng",
            "match_score": "%",
            "status": "TT",
        }
        headings = compact_headings if compact else full_headings
        for col, text in headings.items():
            try:
                self.preview_tree.heading(col, text=text)
            except tk.TclError:
                pass

    def _create_tooltip(self, widget: tk.Widget, text: str) -> None:
        """IMP-D5: Tạo tooltip đơn giản cho widget — hiện sau 600ms hover."""
        tooltip_window = [None]  # dùng list để có thể thay đổi trong closure

        def _show(event: tk.Event) -> None:
            if tooltip_window[0] is not None:
                return
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
            label = tk.Label(tw, text=text, justify=tk.LEFT, background="#ffffdd",
                             relief=tk.SOLID, borderwidth=1, font=("Segoe UI", 9), padx=6, pady=4)
            label.pack()
            tooltip_window[0] = tw

        def _hide(_event: tk.Event | None = None) -> None:
            tw = tooltip_window[0]
            if tw is not None:
                tw.destroy()
                tooltip_window[0] = None

        def _schedule(event: tk.Event) -> None:
            widget._tooltip_after_id = widget.after(600, lambda: _show(event))  # type: ignore[attr-defined]

        def _cancel(_event: tk.Event | None = None) -> None:
            after_id = getattr(widget, "_tooltip_after_id", None)
            if after_id is not None:
                widget.after_cancel(after_id)
                widget._tooltip_after_id = None  # type: ignore[attr-defined]
            _hide()

        widget.bind("<Enter>", _schedule, add="+")
        widget.bind("<Leave>", _cancel, add="+")
        widget.bind("<ButtonPress>", _cancel, add="+")

    def _dispatch_ui_callback(self, callback: Callable[[], None]) -> bool:
        """Schedules one UI callback safely from any worker thread."""
        try:
            self.root.after(0, callback)
            return True
        except (tk.TclError, RuntimeError):
            return False

    def _append_log_line(self, line: str, *, tag: str = "") -> None:
        """Writes one log line into the Tk text widget on the UI thread."""
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, line + "\n", tag or ())
            # BUG-12 FIX: Trim log khi vượt LOG_MAX_LINES để tránh memory leak và GUI chậm
            current_lines = int(self.log_text.index("end-1c").split(".")[0])
            if current_lines > LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{LOG_TRIM_LINES}.0")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        except tk.TclError:
            return

    def _log(self, message: str, *, tag: str = "") -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        print(line)
        if threading.current_thread() is threading.main_thread():
            self._append_log_line(line, tag=tag)
            return
        self._dispatch_ui_callback(lambda line=line, tag=tag: self._append_log_line(line, tag=tag))

    def _set_busy(self, busy: bool, status_text: str = "") -> None:
        self._busy = busy
        for widget, normal_state in self._busy_widgets:
            try:
                widget.configure(state=("disabled" if busy else normal_state))
            except tk.TclError:
                continue
        self._sync_context_combo_states()
        try:
            self.root.configure(cursor="watch" if busy else "")
        except tk.TclError:
            pass
        if status_text:
            self._set_progress(0.0 if busy else self._progress_value, status_text)

    def _set_progress(self, value: float, message: str = "") -> None:
        self._progress_value = clamp_progress_value(value)
        if message:
            self.status_var.set(message)
            self._progress_message = message
        self._render_progress()

    def _render_progress(self) -> None:
        try:
            width = max(int(self.progress_canvas.winfo_width()), 1)
            height = max(int(self.progress_canvas.winfo_height()), 1)
        except (AttributeError, tk.TclError):
            return
        fill = int((width - 2) * (self._progress_value / 100.0))
        self.progress_canvas.coords(self._progress_fill_id, 1, 1, 1 + max(fill, 0), max(height - 1, 1))
        # IMP-D1: Đổi màu progress bar theo trạng thái (xanh = OK, vàng = đang chạy, đỏ = lỗi)
        if self._progress_value <= 0:
            bar_color = "#dfe9df"
        elif self._progress_value >= 100:
            bar_color = "#2fa34a"  # xanh = hoàn thành
        elif "lỗi" in self._progress_message.lower() or "thất bại" in self._progress_message.lower():
            bar_color = "#e74c3c"  # đỏ = lỗi
        else:
            bar_color = "#f39c12"  # vàng = đang xử lý
        self.progress_canvas.itemconfigure(self._progress_fill_id, fill=bar_color)
        self.progress_canvas.coords(self._progress_text_id, 8, height / 2)
        self.progress_canvas.itemconfigure(
            self._progress_text_id,
            text=build_progress_caption(self._progress_value, self._progress_message, max_message_length=34),
        )

    def _reset_voice_meter(self) -> None:
        self._voice_meter_level = 0.0
        self._voice_meter_peak = 0.0
        self._render_voice_meter()

    def _sync_voice_meter(self) -> None:
        capture_level = 0.0
        if self._ptt_enabled and self._ptt_capture is not None:
            try:
                capture_level = self._ptt_capture.current_level()
            except Exception:
                capture_level = 0.0
        self._voice_meter_level = max(0.0, min(1.0, float(capture_level)))
        if self._voice_meter_level >= self._voice_meter_peak:
            self._voice_meter_peak = self._voice_meter_level
        else:
            self._voice_meter_peak = max(self._voice_meter_level, self._voice_meter_peak - 0.045)
        self._render_voice_meter()

    def _render_voice_meter(self) -> None:
        try:
            canvas = self.voice_meter_canvas
            width = max(int(canvas.winfo_width()), 1)
            height = max(int(canvas.winfo_height()), 1)
        except (AttributeError, tk.TclError):
            return

        inner_left = 2
        inner_top = 2
        inner_right = max(width - 2, inner_left)
        inner_bottom = max(height - 2, inner_top)
        inner_width = max(inner_right - inner_left, 1)
        level_width = int(inner_width * self._voice_meter_level)
        peak_x = inner_left + int(inner_width * self._voice_meter_peak)

        if not self._ptt_enabled:
            fill_color = "#d9e2da"
            peak_color = "#b8c5ba"
            meter_text = "Micro realtime: bộ đàm đang tắt"
        elif self._ptt_recording:
            fill_color = "#27ae60"
            peak_color = "#176a34"
            meter_text = f"Đang nghe realtime: {int(self._voice_meter_level * 100):02d}%"
        else:
            fill_color = "#2fa34a"
            peak_color = "#176a34"
            meter_text = f"Mức thu âm realtime: {int(self._voice_meter_level * 100):02d}%"

        canvas.coords(
            self._voice_meter_fill_id,
            inner_left,
            inner_top,
            inner_left + max(level_width, 0),
            inner_bottom,
        )
        canvas.itemconfigure(self._voice_meter_fill_id, fill=fill_color)
        canvas.coords(self._voice_meter_peak_id, peak_x, 5, peak_x, max(height - 5, 5))
        canvas.itemconfigure(self._voice_meter_peak_id, fill=peak_color, state=("normal" if self._ptt_enabled else "hidden"))
        canvas.coords(self._voice_meter_text_id, 10, height / 2)
        canvas.itemconfigure(self._voice_meter_text_id, text=meter_text, fill="#20532b")

    def _progress_reporter(self, task_token: int) -> ProgressCallback:
        def report(value: float, message: str = "") -> None:
            self._progress_queue.put((task_token, clamp_progress_value(value), str(message or "").strip()))
        return report

    def _run_background(
        self,
        busy_text: str,
        worker: Callable[[ProgressCallback], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._busy:
            self._log("Đang có tác vụ khác chạy, bỏ qua lệnh mới.")
            return
        self._foreground_task_token += 1
        task_token = self._foreground_task_token
        self._set_busy(True, busy_text)
        reporter = self._progress_reporter(task_token)
        reporter(2.0, busy_text)

        def background_worker() -> None:
            result = None
            captured_error = None
            try:
                with self._automation_lock:
                    result = worker(reporter)
            except Exception as error:  # noqa: BLE001
                captured_error = error
            self._result_queue.put((result, captured_error, on_success, on_error))

        threading.Thread(target=background_worker, daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                task_token, progress_value, progress_message = self._progress_queue.get_nowait()
                if self._busy and task_token == self._foreground_task_token:
                    self._set_progress(progress_value, progress_message)
        except Empty:
            pass
        try:
            while True:
                result, error, on_success, on_error = self._result_queue.get_nowait()
                self._set_busy(False)
                try:
                    if error is not None:
                        if on_error is not None:
                            on_error(error)
                        self._set_progress(0.0, self.status_var.get() or "Tác vụ thất bại")
                    else:
                        on_success(result)
                        self._set_progress(100.0, self.status_var.get() or "Đã hoàn tất")
                except Exception as callback_error:  # noqa: BLE001
                    messagebox.showerror("Lỗi nội bộ", str(callback_error))
                    self._log(f"Lỗi callback UI: {callback_error}")
                    self._set_progress(0.0, "Lỗi callback UI")
        except Empty:
            pass
        self._sync_voice_meter()
        try:
            self._poll_after_id = self.root.after(50, self._poll_results)
        except tk.TclError:
            self._poll_after_id = None

    def _build_automation(self) -> VnEduScoreAutomation:
        self._ensure_access_cache_loaded()
        try:
            debug_port = int(self.port_var.get().strip())
        except ValueError as error:
            raise ValueError("CDP Port phải là số nguyên hợp lệ.") from error
        target_url = self.url_var.get().strip()
        if not target_url:
            raise ValueError("URL VNEDU không được để trống.")
        # SECURITY: Validate URL scheme to prevent non-HTTP connections
        if not target_url.lower().startswith(("http://", "https://")):
            raise ValueError("URL VNEDU phải bắt đầu bằng http:// hoặc https://.")
        return VnEduScoreEntryAutomation(debug_port=debug_port, target_url=target_url)

    def _render_options(self, combo: ttk.Combobox, variable: tk.StringVar, label_map: Dict[str, str], options: list[object], selected_id: str) -> None:
        label_map.clear()
        labels: list[str] = []
        for option in options:
            option_id = str(getattr(option, "option_id", "")).strip()
            option_text = str(getattr(option, "option_text", "")).strip() or option_id
            if not option_id:
                continue
            label = option_text if option_text not in label_map else f"{option_text} ({option_id})"
            label_map[label] = option_id
            labels.append(label)
        combo["values"] = labels
        # IMP-B4: Enable combo khi có data, disable khi rỗng
        combo["state"] = "readonly" if labels else "disabled"
        selected_label = next((label for label, option_id in label_map.items() if option_id == selected_id), "")
        variable.set(selected_label or (labels[0] if labels else "(chưa đọc)"))

    def _selected_id(self, variable: tk.StringVar, label_map: Dict[str, str]) -> str:
        return label_map.get(variable.get().strip(), "").strip()

    def _selected_context_ids(self) -> tuple[str, str, str, str]:
        return (
            self._selected_id(self.grade_var, self.grade_label_to_id),
            self._selected_id(self.class_var, self.class_label_to_id),
            self._selected_id(self.subject_var, self.subject_label_to_id),
            self._selected_id(self.term_var, self.term_label_to_id),
        )

    def _context_request_ids(self) -> tuple[str, str, str, str]:
        selected_grade_id, selected_class_id, selected_subject_id, selected_term_id = self._selected_context_ids()
        if self.current_context is None:
            return (selected_grade_id, selected_class_id, selected_subject_id, selected_term_id)
        return (
            selected_grade_id or self.current_context.selected_grade_id,
            "" if "class" in self._invalidated_context_fields else (selected_class_id or self.current_context.selected_class_id),
            "" if "subject" in self._invalidated_context_fields else (selected_subject_id or self.current_context.selected_subject_id),
            selected_term_id or self.current_context.selected_term_id,
        )

    def _invalidate_context_combo(
        self,
        combo: ttk.Combobox,
        variable: tk.StringVar,
        label_map: Dict[str, str],
        placeholder: str,
    ) -> None:
        label_map.clear()
        combo["values"] = []
        variable.set(placeholder)

    def _sync_context_combo_states(self) -> None:
        combo_specs = (
            (self.grade_combo, False),
            (self.class_combo, "class" in self._invalidated_context_fields),
            (self.subject_combo, "subject" in self._invalidated_context_fields),
            (self.term_combo, False),
        )
        for combo, is_invalidated in combo_specs:
            try:
                combo.configure(state=("disabled" if self._busy or is_invalidated else "readonly"))
            except tk.TclError:
                continue

    def _invalidate_dependent_context_state(self, changed_field: str, selected_ids: tuple[str, str, str, str]) -> None:
        if self.current_context is None:
            return
        selected_grade_id, selected_class_id, _selected_subject_id, _selected_term_id = selected_ids
        current_grade_id, current_class_id, _current_subject_id, _current_term_id = self._last_applied_context_ids
        if changed_field == "grade" and selected_grade_id and selected_grade_id != current_grade_id:
            self._invalidated_context_fields.update({"class", "subject"})
            self._invalidate_context_combo(
                self.class_combo,
                self.class_var,
                self.class_label_to_id,
                "(đang cập nhật lớp...)",
            )
            self._invalidate_context_combo(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                "(đang cập nhật môn...)",
            )
            self._sync_context_combo_states()
        elif changed_field == "class" and selected_class_id and selected_class_id != current_class_id:
            self._invalidated_context_fields.add("subject")
            self._invalidate_context_combo(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                "(đang cập nhật môn...)",
            )
            self._sync_context_combo_states()

    def _cancel_context_autosync(self) -> None:
        if self._context_autosync_after_id is None:
            return
        try:
            self.root.after_cancel(self._context_autosync_after_id)
        except tk.TclError:
            pass
        self._context_autosync_after_id = None

    def _cancel_pending_auto_scan(self) -> None:
        if self._auto_scan_after_id is None:
            return
        try:
            self.root.after_cancel(self._auto_scan_after_id)
        except tk.TclError:
            pass
        self._auto_scan_after_id = None

    def _apply_password_visibility(self) -> None:
        try:
            self.password_entry.configure(show=password_entry_show_value(bool(self.show_password_var.get())))
        except tk.TclError:
            pass

    def _score_column_candidates(self, context: ScorebookContext) -> list[ScoreColumnSchema]:
        return [schema for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index) if schema.editable and schema.role_hint in {"score", "average"}]

    def _render_score_columns(self, context: ScorebookContext) -> None:
        self._suspend_target_score_autoscan = True
        try:
            self.target_score_label_to_key.clear()
            labels: list[str] = []
            for schema in self._score_column_candidates(context):
                label = schema.display_name.strip() or schema.column_key
                if label in self.target_score_label_to_key:
                    label = f"{label} ({schema.column_key})"
                self.target_score_label_to_key[label] = schema.column_key
                labels.append(label)
            self.target_score_combo["values"] = labels
            selected_key = self._selected_id(self.target_score_column_var, self.target_score_label_to_key)
            if selected_key not in self.target_score_label_to_key.values():
                preferred_key = context.detected_columns.preferred_score_column_key.strip()
                if preferred_key in self.target_score_label_to_key.values():
                    selected_key = preferred_key
                else:
                    selected_key = next(iter(self.target_score_label_to_key.values()), "")
            selected_label = next((label for label, key in self.target_score_label_to_key.items() if key == selected_key), "")
            self.target_score_column_var.set(selected_label or "(chưa dò)")
        finally:
            self._suspend_target_score_autoscan = False

    def _selected_target_score_key(self) -> str:
        return self.target_score_label_to_key.get(self.target_score_column_var.get().strip(), "").strip()

    def _on_context_combo_selected(self, _event: tk.Event | None = None) -> str | None:
        if self._suspend_context_autosync or self._busy:
            return "break"
        if self.current_context is None:
            return "break"
        selected_ids = self._selected_context_ids()
        changed_widget = getattr(_event, "widget", None)
        if changed_widget is self.grade_combo:
            self._invalidate_dependent_context_state("grade", selected_ids)
        elif changed_widget is self.class_combo:
            self._invalidate_dependent_context_state("class", selected_ids)
        request_ids = self._context_request_ids()
        if not request_ids[0] or not request_ids[3]:
            return "break"
        if request_ids == self._last_applied_context_ids:
            return "break"
        self._cancel_context_autosync()
        self._context_autosync_after_id = self.root.after(
            self._context_autosync_delay_ms,
            lambda expected_ids=request_ids: self._run_context_autosync(expected_ids),
        )
        return "break"

    def _run_context_autosync(self, expected_ids: tuple[str, str, str, str]) -> None:
        self._context_autosync_after_id = None
        if self._busy or self.current_context is None:
            return
        request_ids = self._context_request_ids()
        if request_ids != expected_ids:
            return
        if request_ids == self._last_applied_context_ids:
            return
        self.on_apply_selected_context()

    def _on_target_score_selected(self, _event: tk.Event | None = None) -> str | None:
        if self._suspend_target_score_autoscan or self._busy:
            return "break"
        if self.current_context is None:
            return "break"
        target_key = self._selected_target_score_key()
        if not target_key:
            return "break"
        if target_key == self._last_scanned_target_key and self._score_rows_by_key:
            self._focus_preview_tree()
            return "break"
        self.root.after_idle(self.on_scan_score_rows)
        return "break"

    def _clear_score_rows(self, reason: str = "", log_reason: bool = False) -> None:
        self._cancel_context_autosync()
        self._cancel_pending_auto_scan()
        self._ptt_roster_revision += 1
        # BUG-01 FIX: Acquire lock khi xóa dữ liệu chia sẻ với PTT worker
        with self._score_data_lock:
            self._score_rows_by_key.clear()
            self._tree_item_by_key.clear()
            self._student_full_index.clear()
            self._student_last_name_index.clear()
            self._student_last_two_index.clear()
            self._student_token_index.clear()
            self._student_phonetic_full_index.clear()
            self._student_phonetic_last_name_index.clear()
            self._student_phonetic_last_two_index.clear()
            self._student_phonetic_token_index.clear()
            self._voice_match_cache.clear()
            # BUG #15 FIX: _voice_recent_names cũng được mutate dưới
            # _score_data_lock ở các site khác (build_smart_hints,
            # _do_apply_voice_match) → clear() phải nằm trong lock.
            self._voice_recent_names.clear()
        self._voice_hints = []
        self._voice_hints_primary = []
        self._voice_hints_extended = []
        self._clear_voice_pending()
        self._clear_voice_tied_candidates()  # KHMER #B: roster đổi → reset picker
        self._undo_stack.clear()
        self._last_scanned_target_key = ""
        self._refresh_score_tree()
        self.voice_summary_var.set(reason or "Chưa có danh sách học sinh.")
        if reason and log_reason:
            self._log(reason)

    def _access_cache_namespace(self) -> str:
        """Builds one persistent cache namespace for the current VNEDU account."""
        target_url = " ".join(self.url_var.get().strip().lower().split()).rstrip("/")
        username = " ".join(self.username_var.get().strip().lower().split())
        if not target_url or not username:
            return ""
        return f"{target_url}|{username}"

    def _clear_access_scope_caches(self) -> None:
        """Drops all in-memory permission caches for the current session namespace."""
        self._full_access_scope_cache.clear()
        self._subject_access_scope_cache.clear()
        self._empty_access_scope_target_cache.clear()

    def _encode_access_matrix_key(self, grade_id: str, term_id: str) -> str:
        """Serializes one grade-term key for JSON persistence."""
        return f"{grade_id.strip()}|{term_id.strip()}"

    def _encode_access_subject_key(self, grade_id: str, term_id: str, subject_id: str) -> str:
        """Serializes one grade-term-subject key for JSON persistence."""
        return f"{grade_id.strip()}|{term_id.strip()}|{subject_id.strip()}"

    def _serialize_access_entry(self, entry: object) -> dict[str, object]:
        """Converts one permission entry into JSON-safe data."""
        return {
            "grade_id": str(getattr(entry, "grade_id", "")).strip(),
            "grade_text": str(getattr(entry, "grade_text", "")).strip(),
            "class_id": str(getattr(entry, "class_id", "")).strip(),
            "class_text": str(getattr(entry, "class_text", "")).strip(),
            "subject_id": str(getattr(entry, "subject_id", "")).strip(),
            "subject_text": str(getattr(entry, "subject_text", "")).strip(),
            "term_id": str(getattr(entry, "term_id", "")).strip(),
            "term_text": str(getattr(entry, "term_text", "")).strip(),
            "teacher_text": str(getattr(entry, "teacher_text", "")).strip(),
            "permission_text": str(getattr(entry, "permission_text", "")).strip(),
            "comment_input_count": int(getattr(entry, "comment_input_count", 0) or 0),
            "enabled_comment_input_count": int(getattr(entry, "enabled_comment_input_count", 0) or 0),
        }

    def _deserialize_access_entries(self, raw_entries: object) -> list[ScorebookAccessEntry]:
        """Restores cached permission entries from disk."""
        restored_entries: list[ScorebookAccessEntry] = []
        if not isinstance(raw_entries, list):
            return restored_entries
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            try:
                restored_entries.append(
                    ScorebookAccessEntry(
                        grade_id=str(raw_entry.get("grade_id", "")).strip(),
                        grade_text=str(raw_entry.get("grade_text", "")).strip(),
                        class_id=str(raw_entry.get("class_id", "")).strip(),
                        class_text=str(raw_entry.get("class_text", "")).strip(),
                        subject_id=str(raw_entry.get("subject_id", "")).strip(),
                        subject_text=str(raw_entry.get("subject_text", "")).strip(),
                        term_id=str(raw_entry.get("term_id", "")).strip(),
                        term_text=str(raw_entry.get("term_text", "")).strip(),
                        teacher_text=str(raw_entry.get("teacher_text", "")).strip(),
                        permission_text=str(raw_entry.get("permission_text", "")).strip(),
                        comment_input_count=int(raw_entry.get("comment_input_count", 0) or 0),
                        enabled_comment_input_count=int(raw_entry.get("enabled_comment_input_count", 0) or 0),
                    )
                )
            except Exception:
                continue
        return restored_entries

    def _ensure_access_cache_loaded(self) -> None:
        """Loads the persisted permission cache for the current URL+username namespace."""
        namespace = self._access_cache_namespace()
        if namespace == self._loaded_access_cache_namespace:
            return
        self._clear_access_scope_caches()
        self._loaded_access_cache_namespace = namespace
        if not namespace or not ACCESS_CACHE_FILE.exists():
            return
        payload, backup_path, error = _load_json_object_file(ACCESS_CACHE_FILE)
        if error is not None:
            self._log(f"Không tải được cache quyền nhập điểm: {error}")
            if backup_path is not None:
                self._log(f"Đã chuyển file cache lỗi sang {backup_path}")
            return
        if int(payload.get("version", 0) or 0) != ACCESS_CACHE_SCHEMA_VERSION:
            return
        namespaces = payload.get("namespaces", {})
        if not isinstance(namespaces, dict):
            return
        namespace_payload = namespaces.get(namespace, {})
        if not isinstance(namespace_payload, dict):
            return
        raw_full = namespace_payload.get("full_matrices", {})
        if isinstance(raw_full, dict):
            for serialized_key, raw_entries in raw_full.items():
                if not isinstance(serialized_key, str):
                    continue
                parts = serialized_key.split("|", 1)
                if len(parts) != 2:
                    continue
                restored_entries = self._deserialize_access_entries(raw_entries)
                if restored_entries:
                    self._full_access_scope_cache[(parts[0].strip(), parts[1].strip())] = restored_entries
        raw_subject = namespace_payload.get("subject_scopes", {})
        if isinstance(raw_subject, dict):
            for serialized_key, raw_entries in raw_subject.items():
                if not isinstance(serialized_key, str):
                    continue
                parts = serialized_key.split("|", 2)
                if len(parts) != 3:
                    continue
                restored_entries = self._deserialize_access_entries(raw_entries)
                if restored_entries:
                    self._subject_access_scope_cache[
                        (parts[0].strip(), parts[1].strip(), parts[2].strip())
                    ] = restored_entries
        raw_empty = namespace_payload.get("empty_targets", {})
        if isinstance(raw_empty, dict):
            for serialized_key, raw_target in raw_empty.items():
                if not isinstance(serialized_key, str) or not isinstance(raw_target, dict):
                    continue
                parts = serialized_key.split("|", 1)
                if len(parts) != 2:
                    continue
                self._empty_access_scope_target_cache[(parts[0].strip(), parts[1].strip())] = (
                    str(raw_target.get("class_id", "")).strip(),
                    str(raw_target.get("subject_id", "")).strip(),
                )

    def _save_access_cache(self) -> None:
        """Persists the current permission caches for faster future grade/term switches."""
        namespace = self._access_cache_namespace()
        if not namespace:
            return
        payload: dict[str, object] = {"version": ACCESS_CACHE_SCHEMA_VERSION, "namespaces": {}}
        if ACCESS_CACHE_FILE.exists():
            loaded_payload, backup_path, error = _load_json_object_file(ACCESS_CACHE_FILE)
            if error is None:
                payload = loaded_payload
            elif backup_path is not None:
                self._log(f"Cache quyền cũ bị lỗi, đã backup sang {backup_path}")
        payload["version"] = ACCESS_CACHE_SCHEMA_VERSION
        namespaces = payload.get("namespaces", {})
        if not isinstance(namespaces, dict):
            namespaces = {}
        namespaces[namespace] = {
            "full_matrices": {
                self._encode_access_matrix_key(grade_id, term_id): [
                    self._serialize_access_entry(entry) for entry in entries
                ]
                for (grade_id, term_id), entries in self._full_access_scope_cache.items()
            },
            "subject_scopes": {
                self._encode_access_subject_key(grade_id, term_id, subject_id): [
                    self._serialize_access_entry(entry) for entry in entries
                ]
                for (grade_id, term_id, subject_id), entries in self._subject_access_scope_cache.items()
            },
            "empty_targets": {
                self._encode_access_matrix_key(grade_id, term_id): {
                    "class_id": class_id,
                    "subject_id": subject_id,
                }
                for (grade_id, term_id), (class_id, subject_id) in self._empty_access_scope_target_cache.items()
            },
        }
        payload["namespaces"] = namespaces
        try:
            _write_json_atomic_file(ACCESS_CACHE_FILE, payload)
        except Exception as error:  # noqa: BLE001
            self._log(f"Không lưu được cache quyền nhập điểm: {error}")

    def _context_has_access_scope(self, context: ScorebookContext) -> bool:
        """Returns whether the current context already carries one score-entry permission matrix."""
        return bool(
            context.accessible_entries
            or context.accessible_grade_id.strip()
            or context.accessible_term_id.strip()
        )

    def _access_matrix_key(self, grade_id: str, term_id: str) -> tuple[str, str]:
        """Builds one cache key for a full permission matrix inside one grade-term scope."""
        return (grade_id.strip(), term_id.strip())

    def _access_subject_key(self, grade_id: str, term_id: str, subject_id: str) -> tuple[str, str, str]:
        """Builds one cache key for a subject-scoped permission probe."""
        return (grade_id.strip(), term_id.strip(), subject_id.strip())

    def _set_context_access_scope(
        self,
        context: ScorebookContext,
        entries: list[object],
        grade_id: str,
        term_id: str,
        *,
        scope_mode: str = "",
        scope_subject_id: str = "",
    ) -> ScorebookContext:
        """Injects one known permission scope back into a context snapshot."""
        context.accessible_entries = list(entries)
        context.accessible_grade_id = grade_id.strip()
        context.accessible_term_id = term_id.strip()
        setattr(context, "_access_scope_mode", scope_mode.strip())
        setattr(context, "_access_scope_subject_id", scope_subject_id.strip())
        return context

    def _lookup_cached_access_scope(
        self,
        grade_id: str,
        term_id: str,
        *,
        subject_id: str = "",
    ) -> tuple[list[object], str, str]:
        """Returns one cached permission scope, preferring exact subject matches before full matrices."""
        normalized_grade_id = grade_id.strip()
        normalized_term_id = term_id.strip()
        normalized_subject_id = subject_id.strip()
        if not normalized_grade_id or not normalized_term_id:
            return [], "", ""
        if normalized_subject_id:
            subject_entries = self._subject_access_scope_cache.get(
                self._access_subject_key(normalized_grade_id, normalized_term_id, normalized_subject_id),
                [],
            )
            if subject_entries:
                return list(subject_entries), AccessScopeMode.SUBJECT_FAST, normalized_subject_id
        full_entries = self._full_access_scope_cache.get(
            self._access_matrix_key(normalized_grade_id, normalized_term_id),
            [],
        )
        if full_entries:
            return list(full_entries), AccessScopeMode.FULL_MATRIX, ""
        return [], "", ""

    def _lookup_cached_empty_access_target(self, grade_id: str, term_id: str) -> tuple[str, str] | None:
        """Returns one cached stable class-subject pair for grade-terms known to have no write permission."""
        normalized_key = self._access_matrix_key(grade_id, term_id)
        if normalized_key not in self._empty_access_scope_target_cache:
            return None
        empty_class_id, empty_subject_id = self._empty_access_scope_target_cache.get(normalized_key, ("", ""))
        return (str(empty_class_id).strip(), str(empty_subject_id).strip())

    def _resolve_cached_lookup_subject_id(self, grade_id: str, term_id: str, requested_subject_id: str) -> str:
        """Chooses one subject id for cache lookup even when the UI temporarily invalidates the subject combobox."""
        normalized_grade_id = grade_id.strip()
        normalized_term_id = term_id.strip()
        normalized_requested_subject_id = requested_subject_id.strip()
        if normalized_requested_subject_id:
            return normalized_requested_subject_id
        current_subject_id = ""
        if self.current_context is not None:
            current_subject_id = self.current_context.selected_subject_id.strip()
        candidate_subject_ids: list[str] = []
        seen_subject_ids: set[str] = set()
        for cached_grade_id, cached_term_id, cached_subject_id in self._subject_access_scope_cache:
            if cached_grade_id != normalized_grade_id or cached_term_id != normalized_term_id:
                continue
            normalized_cached_subject_id = str(cached_subject_id).strip()
            if not normalized_cached_subject_id or normalized_cached_subject_id in seen_subject_ids:
                continue
            seen_subject_ids.add(normalized_cached_subject_id)
            candidate_subject_ids.append(normalized_cached_subject_id)
        if current_subject_id and current_subject_id in seen_subject_ids:
            return current_subject_id
        if len(candidate_subject_ids) == 1:
            return candidate_subject_ids[0]
        return ""

    def _cache_access_scope(self, context: ScorebookContext) -> None:
        """Stores discovered permission scopes so repeated grade/subject switches can reuse them safely."""
        grade_id = context.accessible_grade_id.strip() or context.selected_grade_id.strip()
        term_id = context.accessible_term_id.strip() or context.selected_term_id.strip()
        if not grade_id or not term_id:
            return
        scope_mode = str(getattr(context, "_access_scope_mode", "")).strip()
        scope_subject_id = str(getattr(context, "_access_scope_subject_id", "")).strip()
        entries = list(context.accessible_entries)
        matrix_key = self._access_matrix_key(grade_id, term_id)
        cache_changed = False
        if not entries:
            if self._context_has_access_scope(context):
                next_empty_target = (
                    context.selected_class_id.strip(),
                    context.selected_subject_id.strip(),
                )
                if self._empty_access_scope_target_cache.get(matrix_key) != next_empty_target:
                    self._empty_access_scope_target_cache[matrix_key] = next_empty_target
                    cache_changed = True
                if matrix_key in self._full_access_scope_cache:
                    self._full_access_scope_cache.pop(matrix_key, None)
                    cache_changed = True
                subject_keys_to_remove = [
                    key for key in self._subject_access_scope_cache if key[0] == grade_id and key[1] == term_id
                ]
                for subject_key in subject_keys_to_remove:
                    self._subject_access_scope_cache.pop(subject_key, None)
                    cache_changed = True
            if cache_changed:
                self._save_access_cache()
            return
        if matrix_key in self._empty_access_scope_target_cache:
            self._empty_access_scope_target_cache.pop(matrix_key, None)
            cache_changed = True
        if scope_mode == AccessScopeMode.FULL_MATRIX:
            if self._full_access_scope_cache.get(matrix_key) != entries:
                self._full_access_scope_cache[matrix_key] = entries
                cache_changed = True
            subject_keys_to_remove = [
                key for key in self._subject_access_scope_cache if key[0] == grade_id and key[1] == term_id
            ]
            for subject_key in subject_keys_to_remove:
                self._subject_access_scope_cache.pop(subject_key, None)
                cache_changed = True
            per_subject: dict[str, list[object]] = {}
            for entry in entries:
                entry_subject_id = str(getattr(entry, "subject_id", "")).strip()
                if not entry_subject_id:
                    continue
                per_subject.setdefault(entry_subject_id, []).append(entry)
            for entry_subject_id, subject_entries in per_subject.items():
                subject_key = self._access_subject_key(grade_id, term_id, entry_subject_id)
                if self._subject_access_scope_cache.get(subject_key) != list(subject_entries):
                    self._subject_access_scope_cache[subject_key] = list(subject_entries)
                    cache_changed = True
            if cache_changed:
                self._save_access_cache()
            return
        if scope_mode == AccessScopeMode.SUBJECT_FAST and scope_subject_id:
            subject_key = self._access_subject_key(grade_id, term_id, scope_subject_id)
            if self._subject_access_scope_cache.get(subject_key) != entries:
                self._subject_access_scope_cache[subject_key] = entries
                cache_changed = True
        if cache_changed:
            self._save_access_cache()

    def _invalidate_access_scope_cache(
        self,
        grade_id: str,
        term_id: str,
        *,
        subject_id: str = "",
    ) -> None:
        """Drops one stale permission cache branch so the next run re-discovers it live."""
        matrix_key = self._access_matrix_key(grade_id, term_id)
        cache_changed = False
        normalized_subject_id = subject_id.strip()
        if normalized_subject_id:
            subject_key = self._access_subject_key(grade_id, term_id, normalized_subject_id)
            if subject_key in self._subject_access_scope_cache:
                self._subject_access_scope_cache.pop(subject_key, None)
                cache_changed = True
        else:
            if matrix_key in self._full_access_scope_cache:
                self._full_access_scope_cache.pop(matrix_key, None)
                cache_changed = True
            if matrix_key in self._empty_access_scope_target_cache:
                self._empty_access_scope_target_cache.pop(matrix_key, None)
                cache_changed = True
            subject_keys_to_remove = [
                key for key in self._subject_access_scope_cache if key[0] == grade_id.strip() and key[1] == term_id.strip()
            ]
            for subject_key in subject_keys_to_remove:
                self._subject_access_scope_cache.pop(subject_key, None)
                cache_changed = True
        if cache_changed:
            self._save_access_cache()

    def _copy_access_scope(
        self,
        context: ScorebookContext,
        source_context: ScorebookContext | None,
    ) -> ScorebookContext:
        """Preserves one already-scanned permission matrix when the returned snapshot omits it."""
        if self._context_has_access_scope(context):
            return context
        target_grade_id = context.selected_grade_id.strip() or context.accessible_grade_id.strip()
        target_term_id = context.selected_term_id.strip() or context.accessible_term_id.strip()
        if not target_grade_id or not target_term_id:
            return context
        target_subject_id = context.selected_subject_id.strip()
        cached_entries, cached_mode, cached_subject_id = self._lookup_cached_access_scope(
            target_grade_id,
            target_term_id,
            subject_id=target_subject_id,
        )
        if cached_entries:
            return self._set_context_access_scope(
                context,
                cached_entries,
                target_grade_id,
                target_term_id,
                scope_mode=cached_mode,
                scope_subject_id=cached_subject_id,
            )
        cached_empty_target = self._lookup_cached_empty_access_target(target_grade_id, target_term_id)
        if cached_empty_target is not None:
            return self._set_context_access_scope(
                context,
                [],
                target_grade_id,
                target_term_id,
                scope_mode=AccessScopeMode.FULL_MATRIX,
                scope_subject_id="",
            )
        if source_context is None:
            return context
        if not self._context_has_access_scope(source_context):
            return context
        source_grade_id = source_context.accessible_grade_id.strip() or source_context.selected_grade_id.strip()
        source_term_id = source_context.accessible_term_id.strip() or source_context.selected_term_id.strip()
        if target_grade_id != source_grade_id or target_term_id != source_term_id:
            return context
        source_scope_mode = str(getattr(source_context, "_access_scope_mode", "")).strip()
        source_scope_subject_id = str(getattr(source_context, "_access_scope_subject_id", "")).strip()
        if (
            source_scope_mode == AccessScopeMode.SUBJECT_FAST
            and target_subject_id
            and source_scope_subject_id
            and target_subject_id != source_scope_subject_id
        ):
            return context
        return self._set_context_access_scope(
            context,
            list(source_context.accessible_entries),
            source_grade_id,
            source_term_id,
            scope_mode=source_scope_mode,
            scope_subject_id=source_scope_subject_id,
        )

    def _accessible_class_options(self, context: ScorebookContext) -> list[ScoreOption]:
        """Returns only classes that belong to the score-entry permission matrix."""
        if not self._context_has_access_scope(context):
            return list(context.class_options)
        if not context.accessible_entries:
            return []
        allowed_ids = {
            entry.class_id.strip()
            for entry in context.accessible_entries
            if entry.class_id.strip()
        }
        if not allowed_ids:
            return []
        return merge_score_options(
            [option for option in context.class_options if option.option_id.strip() in allowed_ids],
            class_options_from_access_entries(context.accessible_entries),
        )

    def _accessible_subject_options(self, context: ScorebookContext, class_id: str) -> list[ScoreOption]:
        """Returns only subjects that remain valid for the selected accessible class."""
        if not self._context_has_access_scope(context):
            return list(context.subject_options)
        if str(getattr(context, "_access_scope_mode", "")).strip() == AccessScopeMode.SUBJECT_FAST:
            return list(context.subject_options)
        if not context.accessible_entries:
            return []
        normalized_class_id = class_id.strip()
        candidate_entries = [
            entry
            for entry in context.accessible_entries
            if not normalized_class_id or entry.class_id.strip() == normalized_class_id
        ]
        if not candidate_entries:
            candidate_entries = list(context.accessible_entries)
        allowed_ids = {
            entry.subject_id.strip()
            for entry in candidate_entries
            if entry.subject_id.strip()
        }
        if not allowed_ids:
            return []
        return merge_score_options(
            [option for option in context.subject_options if option.option_id.strip() in allowed_ids],
            subject_options_from_access_entries(candidate_entries, class_id=normalized_class_id),
        )

    def _repair_access_context_selection(self, context: ScorebookContext) -> ScorebookContext:
        """Repairs stale selected class-subject ids against the discovered permission matrix."""
        if not self._context_has_access_scope(context):
            return context
        if not context.accessible_entries:
            context.selected_class_id = ""
            context.selected_subject_id = ""
            return context
        resolved_class_id, resolved_subject_id, _notes = _embedded_nhanxet_pro.resolve_accessible_selection(
            list(context.accessible_entries),
            preferred_class_id=context.selected_class_id,
            preferred_subject_id=context.selected_subject_id,
        )
        if resolved_class_id:
            context.selected_class_id = resolved_class_id
        if resolved_subject_id:
            context.selected_subject_id = resolved_subject_id
        return context

    def _selected_access_entry(self, context: ScorebookContext) -> object | None:
        """Returns the access-entry row that matches the currently selected class-subject pair."""
        if not context.accessible_entries:
            return None
        selected_class_id = context.selected_class_id.strip()
        selected_subject_id = context.selected_subject_id.strip()
        if str(getattr(context, "_access_scope_mode", "")).strip() == AccessScopeMode.SUBJECT_FAST:
            access_scope_subject_id = str(getattr(context, "_access_scope_subject_id", "")).strip()
            if access_scope_subject_id and selected_subject_id and selected_subject_id != access_scope_subject_id:
                return None
        exact_entry = next(
            (
                entry
                for entry in context.accessible_entries
                if entry.class_id.strip() == selected_class_id and entry.subject_id.strip() == selected_subject_id
            ),
            None,
        )
        if exact_entry is not None:
            return exact_entry
        if selected_class_id:
            class_entry = next(
                (entry for entry in context.accessible_entries if entry.class_id.strip() == selected_class_id),
                None,
            )
            if class_entry is not None:
                return class_entry
        return context.accessible_entries[0]

    def _clean_context_display_text(self, value: str) -> str:
        """Normalizes browser text before it is shown in the Tkinter status shell."""
        return " ".join(str(value or "").replace("\xa0", " ").split()).strip()

    def _effective_teacher_text(self, context: ScorebookContext) -> str:
        """Returns the teacher line that matches the filtered accessible class-subject pair."""
        access_entry = self._selected_access_entry(context)
        if access_entry is not None:
            teacher_text = self._clean_context_display_text(getattr(access_entry, "teacher_text", ""))
            if teacher_text:
                return teacher_text
        return self._clean_context_display_text(context.teacher_text)

    def _effective_permission_text(self, context: ScorebookContext) -> str:
        """Returns the permission line that matches the filtered accessible class-subject pair."""
        access_entry = self._selected_access_entry(context)
        if access_entry is not None:
            permission_text = self._clean_context_display_text(getattr(access_entry, "permission_text", ""))
            if permission_text:
                return permission_text
        return self._clean_context_display_text(context.permission_text)

    def _log_effective_context_identity(self, context: ScorebookContext) -> None:
        """Logs the teacher/permission lines that correspond to the filtered score-entry scope."""
        effective_permission_text = self._effective_permission_text(context)
        effective_teacher_text = self._effective_teacher_text(context)
        if effective_permission_text:
            self._log(effective_permission_text)
        if effective_teacher_text:
            self._log(effective_teacher_text)

    def _apply_context(self, context: ScorebookContext, clear_score_rows: bool = True) -> None:
        context = self._copy_access_scope(context, self.current_context)
        access_scope_ready = self._context_has_access_scope(context)
        accessible_class_options = self._accessible_class_options(context)
        selected_class_ids = {option.option_id for option in accessible_class_options}
        if accessible_class_options:
            if context.selected_class_id not in selected_class_ids:
                context.selected_class_id = accessible_class_options[0].option_id
        elif access_scope_ready:
            context.selected_class_id = ""

        accessible_subject_options = self._accessible_subject_options(context, context.selected_class_id)
        selected_subject_ids = {option.option_id for option in accessible_subject_options}
        if accessible_subject_options:
            if context.selected_subject_id not in selected_subject_ids:
                context.selected_subject_id = accessible_subject_options[0].option_id
        elif access_scope_ready:
            context.selected_subject_id = ""

        self.current_context = context
        self._cache_access_scope(context)
        self._cancel_context_autosync()
        self._invalidated_context_fields.clear()
        self._suspend_context_autosync = True
        try:
            self._render_options(self.grade_combo, self.grade_var, self.grade_label_to_id, context.grade_options, context.selected_grade_id)
            self._render_options(
                self.class_combo,
                self.class_var,
                self.class_label_to_id,
                accessible_class_options,
                context.selected_class_id,
            )
            if access_scope_ready and not accessible_class_options:
                self.class_var.set("(không có lớp được nhập điểm)")
            self._render_options(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                accessible_subject_options,
                context.selected_subject_id,
            )
            if access_scope_ready and not accessible_subject_options:
                self.subject_var.set("(không có môn được nhập điểm)")
            self._render_options(self.term_combo, self.term_var, self.term_label_to_id, context.term_options, context.selected_term_id)
        finally:
            self._suspend_context_autosync = False
        self._sync_context_combo_states()
        self._last_applied_context_ids = (
            context.selected_grade_id.strip(),
            context.selected_class_id.strip(),
            context.selected_subject_id.strip(),
            context.selected_term_id.strip(),
        )
        self.window_title_var.set(context.window_title.strip() or "(không có tiêu đề)")
        self.teacher_var.set(self._effective_teacher_text(context) or "(chưa có)")
        self.permission_var.set(self._effective_permission_text(context) or "(chưa có)")
        self.column_count_var.set(str(len(context.column_schemas)))
        self.comment_input_var.set(str(context.enabled_comment_input_count or context.comment_input_count or 0))
        self._render_score_columns(context)
        if clear_score_rows:
            self._clear_score_rows("Ngữ cảnh đã đổi. Hãy quét lại danh sách học sinh.", log_reason=False)
        class_wording = "lớp được nhập điểm" if access_scope_ready else "lớp"
        subject_wording = "môn được nhập điểm" if access_scope_ready else "môn"
        self._set_progress(
            self._progress_value,
            (
                f"Đã đọc Sổ điểm: {len(context.grade_options)} khối, "
                f"{len(accessible_class_options)} {class_wording}, "
                f"{len(accessible_subject_options)} {subject_wording}, "
                f"{len(context.term_options)} học kỳ."
            ),
        )


    def _require_context(self) -> ScorebookContext:
        if self.current_context is None:
            raise RuntimeError("Hãy đăng nhập và load Sổ điểm trước.")
        return self.current_context

    def _summary(self) -> str:
        context = self._require_context()
        grade_id, class_id, subject_id, term_id = self._selected_context_ids()
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        lines = [
            f"Cửa sổ: {context.window_title or '(không có)'}",
            f"Giáo viên: {self._effective_teacher_text(context) or '(chưa có)'}",
            f"Quyền: {self._effective_permission_text(context) or '(chưa có)'}",
            f"Khối: {self.grade_var.get()} [{grade_id or context.selected_grade_id}]",
            f"Lớp: {self.class_var.get()} [{class_id or context.selected_class_id}]",
            f"Môn: {self.subject_var.get()} [{subject_id or context.selected_subject_id}]",
            f"Học kỳ: {self.term_var.get()} [{term_id or context.selected_term_id}]",
            f"Cột điểm đích: {self.target_score_column_var.get() or '(chưa chọn)'}",
            f"Số cột phát hiện: {len(context.column_schemas)}",
            f"Số ô nhận xét khả dụng: {context.enabled_comment_input_count or context.comment_input_count or 0}",
            f"Số học sinh đã quét: {len(self._score_rows_by_key)}",
            f"Số dòng đang chờ ghi: {pending_count}",
            f"Tên tác vụ mở rộng: {self.feature_name_var.get().strip() or '(chưa nhập)'}",
        ]
        return "\n".join(lines)

    def _save_config(self) -> None:
        """Lưu config ra file JSON theo atomic write pattern (IMP-A1)."""
        payload = {
            "debug_port": self.port_var.get().strip(),
            "username": self.username_var.get().strip(),
            "show_password": bool(self.show_password_var.get()),
            "feature_name": self.feature_name_var.get().strip(),
            "auto_scan_rows": bool(self.auto_scan_rows_var.get()),
            "auto_save_scores": bool(self.auto_save_scores_var.get()),
            "cdp_url": self.url_var.get().strip(),  # IMP-C6: Lưu CDP URL vào config
        }
        _write_json_atomic_file(CONFIG_FILE, payload)

    def _load_config(self) -> None:
        if not CONFIG_FILE.exists():
            return
        payload, backup_path, error = _load_json_object_file(CONFIG_FILE)
        if error is not None:
            self._log(f"Không tải được cấu hình standalone: {error}")
            if backup_path is not None:
                self._log(f"Đã chuyển file cấu hình lỗi sang {backup_path}")
            return
        self.port_var.set(str(payload.get("debug_port", self.port_var.get())))
        self.username_var.set(str(payload.get("username", self.username_var.get())))
        self.show_password_var.set(bool(payload.get("show_password", self.show_password_var.get())))
        self.feature_name_var.set(str(payload.get("feature_name", self.feature_name_var.get())))
        self.auto_save_scores_var.set(bool(payload.get("auto_save_scores", self.auto_save_scores_var.get())))
        # IMP-C6: Khôi phục CDP URL từ config (nếu có)
        saved_url = str(payload.get("cdp_url", "")).strip()
        # SECURITY: Validate URL scheme khi load từ config
        if saved_url and saved_url.lower().startswith(("http://", "https://")):
            self.url_var.set(saved_url)
        self._apply_password_visibility()

    # ---- Biệt danh (Alias) cho học sinh ----

    def _load_aliases(self) -> None:
        """Tải biệt danh học sinh từ file JSON."""
        if not ALIAS_FILE.exists():
            return
        data, backup_path, error = _load_json_object_file(ALIAS_FILE)
        if error is not None:
            self._log(f"Không tải được biệt danh: {error}")
            if backup_path is not None:
                self._log(f"Đã chuyển file biệt danh lỗi sang {backup_path}")
            return
        self._student_aliases = {
            str(k): [str(v) for v in vs] if isinstance(vs, list) else [str(vs)]
            for k, vs in data.items()
            if k and vs
        }
        self._log(f"Đã tải {sum(len(v) for v in self._student_aliases.values())} biệt danh học sinh.")

    def _save_aliases(self) -> None:
        """Lưu biệt danh học sinh ra file JSON theo atomic write pattern (IMP-A2)."""
        try:
            clean = {k: v for k, v in self._student_aliases.items() if v}
            _write_json_atomic_file(ALIAS_FILE, clean)
        except Exception as error:  # noqa: BLE001
            self._log(f"Không lưu được biệt danh: {error}")

    def _get_aliases_for_row(self, row_key: str) -> list[str]:
        """Lấy danh sách biệt danh của một học sinh theo row_key."""
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return []
        return list(self._student_aliases.get(row.student_name, []))

    def _on_tree_right_click(self, event: tk.Event) -> None:
        """Hiển thị context menu khi nhấn chuột phải vào treeview."""
        item = self.preview_tree.identify_row(event.y)
        if not item:
            return
        self.preview_tree.selection_set(item)
        self.preview_tree.focus(item)

        # Tìm row_key từ tree item
        row_key = None
        for key, tree_item in self._tree_item_by_key.items():
            if tree_item == item:
                row_key = key
                break
        if row_key is None:
            return
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return

        aliases = self._student_aliases.get(row.student_name, [])
        khmer_suggestions = (
            _khmer_likely_aliases(row.student_name) if _looks_like_khmer_name(row.student_name) else []
        )
        # Lọc gợi ý Khmer ra khỏi alias đã có (so qua normalize để tránh sai diacritics).
        existing_alias_keys = {_normalize_diacritic_text(a) for a in aliases}
        new_khmer_suggestions = [
            suggestion
            for suggestion in khmer_suggestions
            if _normalize_diacritic_text(suggestion) not in existing_alias_keys
        ]
        menu = tk.Menu(self.root, tearoff=0, font=("Segoe UI", 10))
        menu.add_command(
            label=f"📝 Đặt biệt danh cho \"{row.student_name}\"",
            command=lambda rk=row_key: self._show_alias_dialog(rk),
        )
        if new_khmer_suggestions:
            sample = ", ".join(new_khmer_suggestions[:3]) + (
                "..." if len(new_khmer_suggestions) > 3 else ""
            )
            menu.add_command(
                label=f"🌏 Gợi ý alias Khmer ({len(new_khmer_suggestions)}: {sample})",
                command=lambda rk=row_key, names=new_khmer_suggestions: self._apply_khmer_aliases(rk, names),
            )
        # Quick action: sinh alias Khmer cho TẤT CẢ row có họ Khmer trong roster.
        roster_khmer_count = sum(
            1
            for candidate in self._score_rows_by_key.values()
            if _looks_like_khmer_name(candidate.student_name)
        )
        if roster_khmer_count >= 2:
            menu.add_command(
                label=f"🌏 Auto sinh alias Khmer cho cả lớp ({roster_khmer_count} HS)",
                command=self._apply_khmer_aliases_for_all,
            )
        # Vietnamese phonetic aliases (Nhựt/Nhật, Quý/Quí, ...)
        vietnamese_suggestions = _vietnamese_likely_aliases(row.student_name)
        new_vn_suggestions = [
            suggestion
            for suggestion in vietnamese_suggestions
            if _normalize_diacritic_text(suggestion) not in existing_alias_keys
        ]
        if new_vn_suggestions:
            vn_sample = ", ".join(new_vn_suggestions[:3]) + (
                "..." if len(new_vn_suggestions) > 3 else ""
            )
            menu.add_command(
                label=f"🇻🇳 Gợi ý alias Việt ({len(new_vn_suggestions)}: {vn_sample})",
                command=lambda rk=row_key, names=new_vn_suggestions: self._apply_khmer_aliases(rk, names),
            )
        roster_vn_alias_count = sum(
            1
            for candidate in self._score_rows_by_key.values()
            if _vietnamese_likely_aliases(candidate.student_name)
        )
        if roster_vn_alias_count >= 2:
            menu.add_command(
                label=f"🇻🇳 Auto sinh alias Việt cho cả lớp ({roster_vn_alias_count} HS)",
                command=self._apply_vietnamese_aliases_for_all,
            )
        if aliases:
            alias_text = ", ".join(aliases)
            menu.add_command(
                label=f"🏷️ Biệt danh hiện tại: {alias_text}",
                state=tk.DISABLED,
            )
            menu.add_command(
                label="🗑️ Xóa tất cả biệt danh",
                command=lambda rk=row_key: self._remove_all_aliases(rk),
            )
        menu.post(event.x_root, event.y_root)
        # BUG-05 FIX: Destroy menu widget khi đóng để tránh memory leak
        menu.bind("<Unmap>", lambda _e: menu.after(50, menu.destroy))

    def _show_alias_dialog(self, row_key: str) -> None:
        """
        Hiển thị hộp thoại đặt biệt danh cho học sinh.

        Args:
            row_key: Key của hàng học sinh trong _score_rows_by_key.
        """
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return
        existing_aliases = self._student_aliases.get(row.student_name, [])

        dlg = tk.Toplevel(self.root)
        dlg.title("Đặt biệt danh")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg="#f0f4f8")

        body = tk.Frame(dlg, bg="#f0f4f8", padx=24, pady=18)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            body,
            text="🏷️  ĐẶT BIỆT DANH CHO HỌC SINH",
            font=("Segoe UI", 12, "bold"),
            bg="#f0f4f8",
            fg="#1a365d",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        # Wrapper frame tạo viền đen 1px đều 4 cạnh (workaround Tkinter relief bug trên Windows)
        info_border = tk.Frame(body, bg="#2d3748", bd=0, highlightthickness=0)
        info_border.pack(fill=tk.X, pady=(0, 12))
        info_frame = tk.Frame(info_border, bg="#e2e8f0", bd=0, highlightthickness=0, padx=12, pady=8)
        info_frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(
            info_frame,
            text=f"Họ tên:  {row.student_name}",
            font=("Segoe UI", 10, "bold"),
            bg="#e2e8f0",
            fg="#2d3748",
            anchor="w",
        ).pack(fill=tk.X)
        if existing_aliases:
            tk.Label(
                info_frame,
                text=f"Biệt danh hiện tại:  {', '.join(existing_aliases)}",
                font=("Segoe UI", 9),
                bg="#e2e8f0",
                fg="#4a5568",
                anchor="w",
            ).pack(fill=tk.X, pady=(4, 0))

        tk.Label(
            body,
            text="Nhập biệt danh mới (VD: Đức, em Đức, Minh Đức...):",
            font=("Segoe UI", 10),
            bg="#f0f4f8",
            fg="#4a5568",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))

        alias_var = tk.StringVar()
        alias_entry = tk.Entry(
            body,
            textvariable=alias_var,
            font=("Segoe UI", 11),
            relief=tk.SOLID,
            borderwidth=1,
        )
        alias_entry.pack(fill=tk.X, pady=(0, 4))
        alias_entry.focus_set()

        hint_label = tk.Label(
            body,
            text="💡 Mỗi biệt danh cách nhau bằng dấu phẩy. VD: Đức, em Đức",
            font=("Segoe UI", 9),
            bg="#f0f4f8",
            fg="#718096",
            anchor="w",
        )
        hint_label.pack(fill=tk.X, pady=(0, 12))

        error_var = tk.StringVar()
        error_label = tk.Label(
            body,
            textvariable=error_var,
            font=("Segoe UI", 9),
            bg="#f0f4f8",
            fg="#e53e3e",
            anchor="w",
        )
        error_label.pack(fill=tk.X, pady=(0, 8))

        btn_frame = tk.Frame(body, bg="#f0f4f8")
        btn_frame.pack(fill=tk.X)

        def on_save() -> None:
            raw = alias_var.get().strip()
            if not raw:
                error_var.set("⚠️ Vui lòng nhập ít nhất 1 biệt danh.")
                return
            new_aliases = [a.strip() for a in raw.split(",") if a.strip()]
            if not new_aliases:
                error_var.set("⚠️ Biệt danh không hợp lệ.")
                return
            # Kiểm tra biệt danh chỉ gồm số
            for alias in new_aliases:
                if re.fullmatch(r"\d+(?:[.,]\d+)?", alias):
                    error_var.set(f"⚠️ Biệt danh '{alias}' không được chỉ là số (sẽ nhầm với điểm).")
                    return
            duplicate_error = ""
            with self._score_data_lock:
                # Kiểm tra trùng với biệt danh học sinh khác
                for other_name, other_aliases in self._student_aliases.items():
                    if other_name == row.student_name:
                        continue
                    for alias in new_aliases:
                        normalized_alias = _normalize_diacritic_text(alias)
                        for existing in other_aliases:
                            if _normalize_diacritic_text(existing) == normalized_alias:
                                duplicate_error = f"⚠️ Biệt danh '{alias}' đã được dùng cho '{other_name}'."
                                break
                        if duplicate_error:
                            break
                    if duplicate_error:
                        break
                if not duplicate_error:
                    # Gộp vào danh sách hiện tại (không trùng)
                    current = list(self._student_aliases.get(row.student_name, []))
                    for alias in new_aliases:
                        normalized_new = _normalize_diacritic_text(alias)
                        already_exists = False
                        for existing in current:
                            if _normalize_diacritic_text(existing) == normalized_new:
                                already_exists = True
                                break
                        if not already_exists:
                            current.append(alias)
                    self._student_aliases[row.student_name] = current
                    self._rebuild_student_indices()
            if duplicate_error:
                error_var.set(duplicate_error)
                return
            self._save_aliases()
            if self._ptt_enabled:
                self._build_voice_hints()
            dlg.destroy()
            self._log(f"Đã đặt biệt danh cho {row.student_name}: {', '.join(current)}", tag=LogTag.SUCCESS)

        def on_cancel() -> None:
            dlg.destroy()

        tk.Button(
            btn_frame,
            text="✅ Lưu biệt danh",
            command=on_save,
            bg="#2fa34a",
            fg="#ffffff",
            activebackground="#23803a",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.SOLID,
            borderwidth=1,
            padx=16,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_frame,
            text="❌ Hủy",
            command=on_cancel,
            bg="#95a5a6",
            fg="#ffffff",
            activebackground="#7f8c8d",
            activeforeground="#ffffff",
            font=("Segoe UI", 10),
            relief=tk.SOLID,
            borderwidth=1,
            padx=16,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        alias_entry.bind("<Return>", lambda _e: on_save())
        alias_entry.bind("<Escape>", lambda _e: on_cancel())

        # Căn giữa dialog
        dlg.update_idletasks()
        dlg_w = dlg.winfo_width()
        dlg_h = dlg.winfo_height()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        pos_x = root_x + (root_w - dlg_w) // 2
        pos_y = root_y + (root_h - dlg_h) // 2
        dlg.geometry(f"+{pos_x}+{pos_y}")

    def _remove_all_aliases(self, row_key: str) -> None:
        """Xóa tất cả biệt danh của một học sinh."""
        row = self._score_rows_by_key.get(row_key)
        if row is None:
            return
        if row.student_name in self._student_aliases:
            with self._score_data_lock:
                removed = self._student_aliases.pop(row.student_name, [])
                self._rebuild_student_indices()
            self._save_aliases()
            if self._ptt_enabled:
                self._build_voice_hints()
            self._log(f"Đã xóa biệt danh của {row.student_name}: {', '.join(removed)}", tag=LogTag.WARNING)

    def _apply_khmer_aliases(self, row_key: str, suggested_aliases: list[str]) -> None:
        """KHMER #A: Áp dụng list alias gợi ý cho 1 học sinh Khmer."""
        row = self._score_rows_by_key.get(row_key)
        if row is None or not suggested_aliases:
            return
        added: list[str] = []
        with self._score_data_lock:
            current = list(self._student_aliases.get(row.student_name, []))
            existing_keys = {_normalize_diacritic_text(a) for a in current}
            # Cũng tránh đụng alias của học sinh khác (alias unique toàn lớp).
            other_keys: dict[str, str] = {}
            for other_name, other_aliases in self._student_aliases.items():
                if other_name == row.student_name:
                    continue
                for alias in other_aliases:
                    other_keys[_normalize_diacritic_text(alias)] = other_name
                other_keys[_normalize_diacritic_text(other_name)] = other_name
            for suggestion in suggested_aliases:
                key = _normalize_diacritic_text(suggestion)
                if not key or key in existing_keys:
                    continue
                if key in other_keys:
                    self._log(
                        f"Bỏ qua alias \"{suggestion}\" cho {row.student_name} vì trùng với HS khác: {other_keys[key]}",
                        tag=LogTag.WARNING,
                    )
                    continue
                current.append(suggestion)
                existing_keys.add(key)
                added.append(suggestion)
            if added:
                self._student_aliases[row.student_name] = current
                self._rebuild_student_indices()
        if added:
            self._save_aliases()
            if self._ptt_enabled:
                self._build_voice_hints()
            self._log(
                f"KHMER: thêm alias cho {row.student_name}: {', '.join(added)}",
                tag=LogTag.SUCCESS,
            )

    def _apply_khmer_aliases_for_all(self) -> None:
        """KHMER #A: Quét toàn bộ roster, sinh và apply alias Khmer cho HS phù hợp."""
        applied_total = 0
        skipped_already = 0
        with self._score_data_lock:
            khmer_rows = [
                row
                for row in self._score_rows_by_key.values()
                if _looks_like_khmer_name(row.student_name)
            ]
        if not khmer_rows:
            self._log("Không có HS nào có họ Khmer trong roster hiện tại.", tag=LogTag.INFO)
            return
        for row in khmer_rows:
            suggestions = _khmer_likely_aliases(row.student_name)
            if not suggestions:
                continue
            existing = self._student_aliases.get(row.student_name, [])
            existing_keys = {_normalize_diacritic_text(a) for a in existing}
            new_suggestions = [
                s for s in suggestions if _normalize_diacritic_text(s) not in existing_keys
            ]
            if not new_suggestions:
                skipped_already += 1
                continue
            self._apply_khmer_aliases(row.row_key, new_suggestions)
            applied_total += 1
        summary = (
            f"KHMER auto-alias: cập nhật {applied_total}/{len(khmer_rows)} HS"
            f"{f', bỏ qua {skipped_already} HS đã có đủ alias' if skipped_already else ''}."
        )
        self._log(summary, tag=LogTag.SUCCESS)
        try:
            messagebox.showinfo("Auto sinh alias Khmer", summary, parent=self.root)
        except tk.TclError:
            pass

    def _apply_vietnamese_aliases_for_all(self) -> None:
        """VIETNAMESE #A: Quét toàn bộ roster, sinh và apply alias Việt cho HS có cặp âm dễ lẫn (Nhựt/Nhật, Quý/Quí, ...)."""
        applied_total = 0
        skipped_already = 0
        with self._score_data_lock:
            vn_rows = [
                row
                for row in self._score_rows_by_key.values()
                if _vietnamese_likely_aliases(row.student_name)
            ]
        if not vn_rows:
            self._log("Không có HS nào có cặp âm Việt dễ lẫn trong roster hiện tại.", tag=LogTag.INFO)
            return
        for row in vn_rows:
            suggestions = _vietnamese_likely_aliases(row.student_name)
            if not suggestions:
                continue
            existing = self._student_aliases.get(row.student_name, [])
            existing_keys = {_normalize_diacritic_text(a) for a in existing}
            new_suggestions = [
                s for s in suggestions if _normalize_diacritic_text(s) not in existing_keys
            ]
            if not new_suggestions:
                skipped_already += 1
                continue
            # Tận dụng helper Khmer (đã có guard duplicate cross-row).
            self._apply_khmer_aliases(row.row_key, new_suggestions)
            applied_total += 1
        summary = (
            f"VIETNAMESE auto-alias: cập nhật {applied_total}/{len(vn_rows)} HS"
            f"{f', bỏ qua {skipped_already} HS đã có đủ alias' if skipped_already else ''}."
        )
        self._log(summary, tag=LogTag.SUCCESS)
        try:
            messagebox.showinfo("Auto sinh alias Việt", summary, parent=self.root)
        except tk.TclError:
            pass

    def _load_scorebook_shell_with_access(
        self,
        automation: VnEduScoreAutomation,
        username: str,
        password: str,
        progress_callback: ProgressCallback | None = None,
    ) -> object:
        """Loads one scorebook shell and prefers the score-permission filtered variant when available."""
        if hasattr(automation, "load_accessible_scorebook_context"):
            quick_shell_progress = create_subprogress_reporter(progress_callback, 5.0, 38.0)
            cached_apply_progress = create_subprogress_reporter(progress_callback, 38.0, 100.0)
            if progress_callback is not None:
                progress_callback(5.0, "Đang đọc nhanh Sổ điểm và kiểm tra cache quyền nhập điểm...")
            quick_shell_result = automation.load_scorebook_context(
                username=username,
                password=password,
                progress_callback=quick_shell_progress,
            )
            if isinstance(quick_shell_result, tuple) and quick_shell_result and isinstance(quick_shell_result[0], ScorebookContext):
                quick_context, quick_login_message = quick_shell_result[:2]
                quick_context = self._copy_access_scope(self._repair_access_context_selection(quick_context), None)
                if self._context_has_access_scope(quick_context):
                    current_permission_text = self._effective_permission_text(quick_context) or quick_context.permission_text
                    if quick_context.accessible_entries and _permission_allows_score_entry(current_permission_text):
                        if progress_callback is not None:
                            progress_callback(100.0, "Đã đọc xong dữ liệu Sổ điểm bằng cache quyền.")
                        return (
                            self._repair_access_context_selection(quick_context),
                            quick_login_message,
                            "Dùng cache quyền nhập điểm đã lưu để đọc nhanh Sổ điểm.",
                        )
                    if quick_context.selected_grade_id and quick_context.selected_term_id:
                        cached_result = self._select_scorebook_context_with_access(
                            automation,
                            grade_id=quick_context.selected_grade_id,
                            class_id=quick_context.selected_class_id,
                            subject_id=quick_context.selected_subject_id,
                            term_id=quick_context.selected_term_id,
                            username=username,
                            password=password,
                            progress_callback=cached_apply_progress,
                        )
                        if isinstance(cached_result, tuple) and cached_result and isinstance(cached_result[0], ScorebookContext):
                            cached_context, cached_login_message, cached_selection_message = cached_result[:3]
                            combined_login_message = " ".join(
                                part
                                for part in (quick_login_message, cached_login_message)
                                if str(part or "").strip()
                            ).strip()
                            combined_selection_message = " ".join(
                                part
                                for part in (
                                    "Dùng cache quyền nhập điểm để mở nhanh ngữ cảnh hợp lệ.",
                                    cached_selection_message,
                                )
                                if str(part or "").strip()
                            ).strip()
                            return (
                                self._repair_access_context_selection(cached_context),
                                combined_login_message,
                                combined_selection_message,
                            )
            if progress_callback is not None:
                progress_callback(38.0, "Không đủ cache phù hợp, app chuyển sang dò quyền nhập điểm live...")
            result = automation.load_accessible_scorebook_context(username=username, password=password)
            if isinstance(result, tuple) and result and isinstance(result[0], ScorebookContext):
                result = (self._repair_access_context_selection(result[0]), *result[1:])
            if progress_callback is not None:
                progress_callback(100.0, "Đã đọc xong dữ liệu Sổ điểm và quyền nhập điểm.")
            return result
        return automation.load_scorebook_context(
            username=username,
            password=password,
            progress_callback=progress_callback,
        )

    def _cached_transition_kind(
        self,
        *,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
    ) -> str:
        """Classifies cached context switches so narrow fast paths can be used safely."""
        if self.current_context is None:
            return ""
        current_grade_id = self.current_context.selected_grade_id.strip()
        current_class_id = self.current_context.selected_class_id.strip()
        current_subject_id = self.current_context.selected_subject_id.strip()
        current_term_id = self.current_context.selected_term_id.strip()
        normalized_grade_id = grade_id.strip() or current_grade_id
        normalized_class_id = class_id.strip() or current_class_id
        normalized_subject_id = subject_id.strip() or current_subject_id
        normalized_term_id = term_id.strip() or current_term_id
        if (
            normalized_grade_id == current_grade_id
            and normalized_term_id == current_term_id
            and normalized_subject_id == current_subject_id
            and normalized_class_id
            and normalized_class_id != current_class_id
        ):
            return "class_only"
        if (
            normalized_grade_id == current_grade_id
            and normalized_class_id == current_class_id
            and normalized_subject_id == current_subject_id
            and normalized_term_id
            and normalized_term_id != current_term_id
        ):
            return "term_only"
        return ""

    def _cached_scope_still_valid(
        self,
        context: ScorebookContext,
        *,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        expect_access: bool,
    ) -> bool:
        """Verifies that one cache-backed context still matches the live page after selection."""
        if context.selected_grade_id.strip() != grade_id.strip():
            return False
        if class_id.strip() and context.selected_class_id.strip() != class_id.strip():
            return False
        if subject_id.strip() and context.selected_subject_id.strip() != subject_id.strip():
            return False
        if term_id.strip() and context.selected_term_id.strip() != term_id.strip():
            return False
        permission_text = self._effective_permission_text(context) or context.permission_text
        if expect_access:
            return _permission_allows_score_entry(permission_text)
        return not _permission_allows_score_entry(permission_text)

    def _try_fast_cached_context_select(
        self,
        automation: VnEduScoreAutomation,
        *,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str,
        password: str,
        cached_entries: list[object],
        cached_mode: str,
        cached_subject_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[ScorebookContext, str, str] | None:
        """Uses a narrow live select path for cached class-only and term-only switches."""
        transition_kind = self._cached_transition_kind(
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
        )
        if not transition_kind:
            return None
        target_class_id = class_id.strip()
        target_subject_id = subject_id.strip()
        target_term_id = term_id.strip()
        target_grade_id = grade_id.strip()
        if not (target_grade_id and target_class_id and target_subject_id and target_term_id):
            return None
        try:
            with automation._open_page() as page:
                login_message = automation._login_if_needed_on_page(page, username=username, password=password)
                if not automation._has_scorebook_controls(page):
                    return None
                snapshot = automation._best_effort_scorebook_snapshot(page)
                if not isinstance(snapshot, dict) or not automation._scorebook_snapshot_ready_for_live_score_work(snapshot):
                    snapshot = automation._wait_for_scorebook_snapshot(page, timeout_sec=5.0)
                current_grade_id = automation._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
                current_class_id = automation._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
                current_subject_id = automation._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
                current_term_id = automation._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
                if transition_kind == "class_only":
                    if current_grade_id != target_grade_id or current_subject_id != target_subject_id or current_term_id != target_term_id:
                        return None
                elif transition_kind == "term_only":
                    if current_grade_id != target_grade_id or current_subject_id != target_subject_id or current_class_id != target_class_id:
                        return None
                if progress_callback is not None:
                    fast_caption = "Đang đổi lớp nhanh bằng cache quyền..." if transition_kind == "class_only" else "Đang đổi học kỳ nhanh bằng cache quyền..."
                    progress_callback(10.0, fast_caption)
                updated_snapshot, selection_message = automation._select_scorebook_context_on_page(
                    page,
                    snapshot,
                    grade_id=target_grade_id,
                    class_id=target_class_id,
                    subject_id=target_subject_id,
                    term_id=target_term_id,
                )
                context = automation._build_scorebook_context(updated_snapshot)
        except Exception:
            return None
        context = self._set_context_access_scope(
            context,
            cached_entries,
            target_grade_id,
            target_term_id,
            scope_mode=cached_mode,
            scope_subject_id=cached_subject_id,
        )
        context = self._repair_access_context_selection(context)
        if not self._cached_scope_still_valid(
            context,
            grade_id=target_grade_id,
            class_id=target_class_id,
            subject_id=target_subject_id,
            term_id=target_term_id,
            expect_access=bool(cached_entries),
        ):
            return None
        if progress_callback is not None:
            progress_callback(100.0, "Đã áp nhanh ngữ cảnh bằng cache quyền.")
        selection_parts = [
            "Dùng fast path cache để đổi ngữ cảnh nhanh hơn.",
            selection_message,
        ]
        return (
            context,
            login_message,
            " ".join(part for part in selection_parts if str(part).strip()).strip(),
        )

    def _select_scorebook_context_with_access(
        self,
        automation: VnEduScoreAutomation,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str,
        password: str,
        progress_callback: ProgressCallback | None = None,
    ) -> object:
        """Applies one context while preferring the permission-filtered score workflow."""
        requested_grade_id = grade_id.strip()
        requested_term_id = term_id.strip()
        requested_subject_id = self._resolve_cached_lookup_subject_id(
            requested_grade_id,
            requested_term_id,
            subject_id,
        )
        cached_entries, cached_mode, cached_subject_id = self._lookup_cached_access_scope(
            requested_grade_id,
            requested_term_id,
            subject_id=requested_subject_id,
        )
        if cached_entries:
            resolved_class_id, resolved_subject_id, fallback_notes = _embedded_nhanxet_pro.resolve_accessible_selection(
                list(cached_entries),
                preferred_class_id=class_id,
                preferred_subject_id=subject_id,
            )
            fast_result = self._try_fast_cached_context_select(
                automation,
                grade_id=requested_grade_id,
                class_id=resolved_class_id,
                subject_id=resolved_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                cached_entries=list(cached_entries),
                cached_mode=cached_mode,
                cached_subject_id=cached_subject_id,
                progress_callback=progress_callback,
            )
            if fast_result is not None:
                context, login_message, selection_message = fast_result
                message_parts = [selection_message]
                message_parts.extend(fallback_notes)
                return context, login_message, " ".join(
                    part for part in message_parts if str(part).strip()
                ).strip()
            if progress_callback is not None:
                progress_callback(5.0, "Đang áp ngữ cảnh bằng cache quyền nhập điểm...")
            result = automation.select_scorebook_context(
                grade_id=requested_grade_id,
                class_id=resolved_class_id,
                subject_id=resolved_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 100.0),
            )
            if isinstance(result, tuple) and len(result) >= 3 and isinstance(result[0], ScorebookContext):
                context, login_message, selection_message = result[:3]
                context = self._set_context_access_scope(
                    context,
                    cached_entries,
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=cached_mode,
                    scope_subject_id=cached_subject_id,
                )
                context = self._repair_access_context_selection(context)
                if not self._cached_scope_still_valid(
                    context,
                    grade_id=requested_grade_id,
                    class_id=resolved_class_id,
                    subject_id=resolved_subject_id,
                    term_id=requested_term_id,
                    expect_access=True,
                ):
                    self._invalidate_access_scope_cache(
                        requested_grade_id,
                        requested_term_id,
                        subject_id=(cached_subject_id or resolved_subject_id) if cached_mode == AccessScopeMode.SUBJECT_FAST else "",
                    )
                else:
                    message_parts = ["Dùng cache quyền nhập điểm để áp ngữ cảnh nhanh hơn."]
                    message_parts.extend(fallback_notes)
                    if selection_message:
                        message_parts.append(selection_message)
                    return context, login_message, " ".join(part for part in message_parts if str(part).strip()).strip()
            else:
                return result
        cached_empty_target = self._lookup_cached_empty_access_target(requested_grade_id, requested_term_id)
        if cached_empty_target is not None:
            cached_class_id, cached_subject_id = cached_empty_target
            if progress_callback is not None:
                progress_callback(5.0, "Đang áp ngữ cảnh bằng cache không có quyền nhập điểm...")
            result = automation.select_scorebook_context(
                grade_id=requested_grade_id,
                class_id=cached_class_id,
                subject_id=cached_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 100.0),
            )
            if isinstance(result, tuple) and len(result) >= 3 and isinstance(result[0], ScorebookContext):
                context, login_message, selection_message = result[:3]
                context = self._set_context_access_scope(
                    context,
                    [],
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=AccessScopeMode.FULL_MATRIX,
                    scope_subject_id="",
                )
                if self._cached_scope_still_valid(
                    context,
                    grade_id=requested_grade_id,
                    class_id=cached_class_id,
                    subject_id=cached_subject_id,
                    term_id=requested_term_id,
                    expect_access=False,
                ):
                    return (
                        context,
                        login_message,
                        " ".join(
                            part
                            for part in (
                                "Dùng cache quyền: khối/học kỳ này hiện không có lớp được nhập điểm.",
                                selection_message,
                            )
                            if str(part).strip()
                        ).strip(),
                    )
                self._invalidate_access_scope_cache(requested_grade_id, requested_term_id)
            else:
                return result
        if hasattr(automation, "select_accessible_scorebook_context"):
            if progress_callback is not None:
                progress_callback(5.0, "Đang áp ngữ cảnh và dò quyền nhập điểm...")
            result = automation.select_accessible_scorebook_context(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
            )
            if isinstance(result, tuple) and result and isinstance(result[0], ScorebookContext):
                result = (self._repair_access_context_selection(result[0]), *result[1:])
            if progress_callback is not None:
                progress_callback(100.0, "Đã áp xong ngữ cảnh và dò quyền nhập điểm.")
            return result
        return automation.select_scorebook_context(
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
            username=username,
            password=password,
            progress_callback=progress_callback,
        )

    def _scan_score_rows_with_access(
        self,
        automation: VnEduScoreAutomation,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        target_column_key: str,
        target_column_label: str,
        username: str,
        password: str,
        progress_callback: ProgressCallback | None = None,
    ) -> object:
        """Refreshes permission-filtered context first, then scans the chosen live score column."""
        if not hasattr(automation, "select_accessible_scorebook_context"):
            return automation.scan_score_entries(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                target_column_key=target_column_key,
                target_column_label=target_column_label,
                username=username,
                password=password,
                progress_callback=progress_callback,
            )

        requested_grade_id = grade_id.strip()
        requested_term_id = term_id.strip()
        requested_subject_id = self._resolve_cached_lookup_subject_id(
            requested_grade_id,
            requested_term_id,
            subject_id,
        )
        cached_entries, cached_mode, cached_subject_scope_id = self._lookup_cached_access_scope(
            requested_grade_id,
            requested_term_id,
            subject_id=requested_subject_id,
        )
        login_message = ""
        selection_message = ""
        if cached_entries:
            resolved_class_id, resolved_subject_id, fallback_notes = _embedded_nhanxet_pro.resolve_accessible_selection(
                list(cached_entries),
                preferred_class_id=class_id,
                preferred_subject_id=subject_id,
            )
            fast_result = self._try_fast_cached_context_select(
                automation,
                grade_id=requested_grade_id,
                class_id=resolved_class_id,
                subject_id=resolved_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                cached_entries=list(cached_entries),
                cached_mode=cached_mode,
                cached_subject_id=cached_subject_scope_id,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 42.0),
            )
            if fast_result is not None:
                access_context, login_message, selection_message = fast_result
                selection_message = " ".join(
                    part for part in (*fallback_notes, selection_message) if str(part).strip()
                ).strip()
            else:
                access_result = automation.select_scorebook_context(
                    grade_id=requested_grade_id,
                    class_id=resolved_class_id,
                    subject_id=resolved_subject_id,
                    term_id=requested_term_id,
                    username=username,
                    password=password,
                    progress_callback=create_subprogress_reporter(progress_callback, 5.0, 42.0),
                )
                if not isinstance(access_result, tuple) or len(access_result) < 3:
                    raise RuntimeError("Kết quả select_scorebook_context không hợp lệ khi dùng cache quyền.")
                access_context, login_message, selection_message = access_result[:3]
                if not isinstance(access_context, ScorebookContext):
                    raise RuntimeError("Không nhận được ScorebookContext hợp lệ khi dùng cache quyền.")
                access_context = self._set_context_access_scope(
                    access_context,
                    cached_entries,
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=cached_mode,
                    scope_subject_id=cached_subject_scope_id,
                )
                access_context = self._repair_access_context_selection(access_context)
                if not self._cached_scope_still_valid(
                    access_context,
                    grade_id=requested_grade_id,
                    class_id=resolved_class_id,
                    subject_id=resolved_subject_id,
                    term_id=requested_term_id,
                    expect_access=True,
                ):
                    self._invalidate_access_scope_cache(
                        requested_grade_id,
                        requested_term_id,
                        subject_id=(cached_subject_scope_id or resolved_subject_id) if cached_mode == AccessScopeMode.SUBJECT_FAST else "",
                    )
                    selection_result = automation.select_accessible_scorebook_context(
                        grade_id=grade_id,
                        class_id=class_id,
                        subject_id=subject_id,
                        term_id=term_id,
                        username=username,
                        password=password,
                    )
                    if not isinstance(selection_result, tuple) or len(selection_result) < 3:
                        raise RuntimeError("Kết quả select_accessible_scorebook_context không hợp lệ.")
                    access_context, login_message, selection_message = selection_result[:3]
                    if not isinstance(access_context, ScorebookContext):
                        raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi dò quyền nhập điểm.")
                    access_context = self._repair_access_context_selection(access_context)
                    if self._context_has_access_scope(access_context) and not access_context.accessible_entries:
                        access_context.selected_class_id = ""
                        access_context.selected_subject_id = ""
                else:
                    selection_message = " ".join(
                        part
                        for part in ("Dùng cache quyền nhập điểm để quét nhanh hơn.", *fallback_notes, selection_message)
                        if str(part).strip()
                    ).strip()
        else:
            cached_empty_target = self._lookup_cached_empty_access_target(requested_grade_id, requested_term_id)
            if cached_empty_target is not None:
                cached_class_id, cached_subject_id = cached_empty_target
                access_result = automation.select_scorebook_context(
                    grade_id=requested_grade_id,
                    class_id=cached_class_id,
                    subject_id=cached_subject_id,
                    term_id=requested_term_id,
                    username=username,
                    password=password,
                    progress_callback=create_subprogress_reporter(progress_callback, 5.0, 42.0),
                )
                if not isinstance(access_result, tuple) or len(access_result) < 3:
                    raise RuntimeError("Kết quả select_scorebook_context không hợp lệ khi dùng cache không quyền.")
                access_context, login_message, selection_message = access_result[:3]
                if not isinstance(access_context, ScorebookContext):
                    raise RuntimeError("Không nhận được ScorebookContext hợp lệ khi dùng cache không quyền.")
                access_context = self._set_context_access_scope(
                    access_context,
                    [],
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=AccessScopeMode.FULL_MATRIX,
                    scope_subject_id="",
                )
                if self._cached_scope_still_valid(
                    access_context,
                    grade_id=requested_grade_id,
                    class_id=cached_class_id,
                    subject_id=cached_subject_id,
                    term_id=requested_term_id,
                    expect_access=False,
                ):
                    selection_message = " ".join(
                        part
                        for part in (
                            "Dùng cache quyền: khối/học kỳ này hiện không có lớp được nhập điểm.",
                            selection_message,
                        )
                        if str(part).strip()
                    ).strip()
                else:
                    self._invalidate_access_scope_cache(requested_grade_id, requested_term_id)
                    selection_result = automation.select_accessible_scorebook_context(
                        grade_id=grade_id,
                        class_id=class_id,
                        subject_id=subject_id,
                        term_id=term_id,
                        username=username,
                        password=password,
                    )
                    if not isinstance(selection_result, tuple) or len(selection_result) < 3:
                        raise RuntimeError("Kết quả select_accessible_scorebook_context không hợp lệ.")
                    access_context, login_message, selection_message = selection_result[:3]
                    if not isinstance(access_context, ScorebookContext):
                        raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi dò quyền nhập điểm.")
                    access_context = self._repair_access_context_selection(access_context)
                    if self._context_has_access_scope(access_context) and not access_context.accessible_entries:
                        access_context.selected_class_id = ""
                        access_context.selected_subject_id = ""
            else:
                selection_result = automation.select_accessible_scorebook_context(
                    grade_id=grade_id,
                    class_id=class_id,
                    subject_id=subject_id,
                    term_id=term_id,
                    username=username,
                    password=password,
                )
                if not isinstance(selection_result, tuple) or len(selection_result) < 3:
                    raise RuntimeError("Kết quả select_accessible_scorebook_context không hợp lệ.")
                access_context, login_message, selection_message = selection_result[:3]
                if not isinstance(access_context, ScorebookContext):
                    raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi dò quyền nhập điểm.")
                access_context = self._repair_access_context_selection(access_context)
                if self._context_has_access_scope(access_context) and not access_context.accessible_entries:
                    access_context.selected_class_id = ""
                    access_context.selected_subject_id = ""
        if progress_callback is not None:
            progress_callback(42.0, "Đang quét danh sách học sinh theo lớp có quyền nhập điểm...")
        if not access_context.selected_class_id or not access_context.selected_subject_id:
            if progress_callback is not None:
                progress_callback(100.0, "Khối/Học kỳ hiện tại không có lớp được nhập điểm.")
            return access_context, [], login_message, selection_message

        # ---------- Fast Direct Score Fetch (bypass ExtJS combo cascade) ----------
        _fast_grade = access_context.selected_grade_id or grade_id
        _fast_class = access_context.selected_class_id
        _fast_subject = access_context.selected_subject_id
        _fast_term = access_context.selected_term_id or term_id
        try:
            with automation._open_page() as _fast_page:
                _fast_login = automation._login_if_needed_on_page(
                    _fast_page, username=username, password=password,
                )
                _fast_snap_result = automation._best_effort_scorebook_snapshot(_fast_page)
                _fast_snapshot = _fast_snap_result[0] if isinstance(_fast_snap_result, tuple) else _fast_snap_result
                if not isinstance(_fast_snapshot, dict):
                    _fast_snapshot = automation._wait_for_scorebook_snapshot(_fast_page, timeout_sec=5.0)
                if isinstance(_fast_snapshot, dict):
                    _fast_data = _direct_fetch_score_entries(
                        automation, _fast_page, _fast_snapshot,
                        grade_id=_fast_grade, class_id=_fast_class,
                        subject_id=_fast_subject, term_id=_fast_term,
                        target_column_key=target_column_key,
                    )
                    if _fast_data is not None and _fast_data.get("entries"):
                        _fast_entries_raw = list(_fast_data.get("entries") or [])
                        # H2 FIX: Fast path is only valid when the target column was
                        # actually resolved to live input fields. If targetLeafIndex
                        # was not found, or no row exposes a target_input_name, the
                        # write step would silently lose every score — so fall back
                        # to the robust slow scan instead of returning broken rows.
                        _fast_target_leaf = int(_fast_data.get("targetLeafIndex", -1) or -1)
                        _fast_linked = sum(
                            1 for _e in _fast_entries_raw
                            if str(_e.get("target_input_name", "") or "").strip()
                        )
                        if _fast_target_leaf < 0 or _fast_linked == 0:
                            self._log(
                                "Fast score fetch không gắn được ô nhập cho cột điểm này "
                                f"(targetLeafIndex={_fast_target_leaf}, linked={_fast_linked}); "
                                "chuyển sang quét chuẩn.",
                                tag=LogTag.WARNING,
                            )
                            raise RuntimeError("fast_fetch_no_target_input")
                        # --- Permission validation for multi-teacher accuracy ---
                        _fast_perm = str(_fast_data.get("permissionText") or "").strip()
                        _perm_lower = _fast_perm.lower()
                        if "không có quyền" in _perm_lower or "khong co quyen" in _perm_lower:
                            raise RuntimeError("Fast fetch: giáo viên không có quyền nhập điểm cho lớp/môn này.")

                        # Update context metadata
                        if _fast_perm:
                            access_context.permission_text = _fast_perm
                        _fast_teacher = str(_fast_data.get("teacherText") or "").strip()
                        if _fast_teacher:
                            access_context.teacher_text = _fast_teacher
                        cmt_count = _fast_data.get("commentInputCount")
                        if cmt_count:
                            access_context.comment_input_count = int(cmt_count)
                        en_cmt = _fast_data.get("enabledCommentCount")
                        if en_cmt:
                            access_context.enabled_comment_input_count = int(en_cmt)

                        # --- Ensure context IDs match the actual fetched selection ---
                        access_context.selected_grade_id = _fast_grade
                        access_context.selected_class_id = _fast_class
                        access_context.selected_subject_id = _fast_subject
                        access_context.selected_term_id = _fast_term

                        # --- Build column schemas for this teacher's subject/grade ---
                        _raw_schemas = _fast_data.get("schemas") or []
                        if _raw_schemas:
                            _column_schemas = []
                            for _rs in _raw_schemas:
                                _column_schemas.append(ScoreColumnSchema(
                                    column_key=str(_rs.get("column_key", "")),
                                    header_path=tuple(str(h) for h in (_rs.get("header_path") or [])),
                                    leaf_index=int(_rs.get("leaf_index", 0)),
                                    display_name=str(_rs.get("display_name", "")),
                                    editable=bool(_rs.get("editable", False)),
                                    role_hint=str(_rs.get("role_hint", "static")),
                                    input_kind=str(_rs.get("input_kind", "")),
                                    sample_value=str(_rs.get("sample_value", "")),
                                    block_index=str(_rs.get("block_index", "")),
                                    child_index=str(_rs.get("child_index", "")),
                                    data_column=str(_rs.get("data_column", "")),
                                    input_name=str(_rs.get("input_name", "")),
                                ))
                            _column_schemas = automation._finalize_schema_identity(_column_schemas)
                            access_context.column_schemas = _column_schemas
                            access_context.detected_columns = automation._detect_scorebook_columns(_column_schemas)

                        _fast_entries = list(_fast_data["entries"])
                        if progress_callback is not None:
                            progress_callback(100.0, f"Đã quét nhanh {len(_fast_entries)} HS qua direct API fetch.")
                        _fcl = " ".join(
                            part for part in (login_message, _fast_login or "") if str(part or "").strip()
                        ).strip()
                        _fcs = " ".join(
                            part for part in (selection_message, "Direct API fetch.") if str(part or "").strip()
                        ).strip()
                        return access_context, _fast_entries, _fcl, _fcs
        except Exception as _fast_err:  # noqa: BLE001 — graceful fallback to slow scan
            # BUG-08 FIX: Log lỗi thay vì nuốt im lặng để hỗ trợ debug
            self._log(
                f"Fast score fetch fallback: {type(_fast_err).__name__}: {_fast_err}",
                tag=LogTag.WARNING,
            )
            pass
        # ---------- End Fast Direct Score Fetch ----------

        scan_result = automation.scan_score_entries(
            grade_id=access_context.selected_grade_id or grade_id,
            class_id=access_context.selected_class_id,
            subject_id=access_context.selected_subject_id,
            term_id=access_context.selected_term_id or term_id,
            target_column_key=target_column_key,
            target_column_label=target_column_label,
            username=username,
            password=password,
            progress_callback=create_subprogress_reporter(progress_callback, 42.0, 100.0),
        )
        if not isinstance(scan_result, tuple) or len(scan_result) < 4:
            raise RuntimeError("Kết quả scan_score_entries không hợp lệ sau khi dò quyền nhập điểm.")
        scan_context, entries, scan_login_message, scan_selection_message = scan_result[:4]
        if not isinstance(scan_context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi quét học sinh.")
        scan_context = self._copy_access_scope(scan_context, access_context)
        combined_login = " ".join(part for part in (login_message, scan_login_message) if str(part or "").strip()).strip()
        combined_selection = " ".join(
            part for part in (selection_message, scan_selection_message) if str(part or "").strip()
        ).strip()
        return scan_context, entries, combined_login, combined_selection

    def on_load_scorebook_shell(self) -> None:
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi load Sổ điểm", str(error))
            self._log(f"Lỗi load Sổ điểm: {error}")
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        self._run_background(
            "Đang đăng nhập và vào Sổ điểm...",
            lambda progress: self._load_scorebook_shell_with_access(
                automation,
                username=username,
                password=password,
                progress_callback=progress,
            ),
            self._handle_load_scorebook_success,
            lambda error: (messagebox.showerror("Lỗi load Sổ điểm", str(error)), self._log(f"Lỗi load Sổ điểm: {error}"), self._set_progress(0.0, "Chưa load được Sổ điểm")),
        )

    def _schedule_auto_scan(self) -> None:
        if not self.auto_scan_rows_var.get():
            return
        if not self._selected_target_score_key():
            return
        self._cancel_pending_auto_scan()
        self._auto_scan_after_id = self.root.after(30, self._run_scheduled_auto_scan)

    def _run_scheduled_auto_scan(self) -> None:
        self._auto_scan_after_id = None
        if self._busy or self.current_context is None:
            return
        if not self.auto_scan_rows_var.get():
            return
        if not self._selected_target_score_key():
            return
        self.on_scan_score_rows()

    def _handle_load_scorebook_success(self, result: object) -> None:
        context = None
        login_message = ""
        selection_message = ""
        if isinstance(result, tuple):
            if len(result) >= 3:
                context, login_message, selection_message = result[:3]
            elif len(result) >= 2:
                context, login_message = result[:2]
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ.")
        if login_message:
            self._log(login_message)
        rewritten_selection_message = _rewrite_access_message_for_score_ui(selection_message)
        if rewritten_selection_message:
            self._log(rewritten_selection_message)
        self._apply_context(context)
        self._log_effective_context_identity(context)
        self._log("Đã vào Sổ điểm và đọc xong khung Khối/Lớp/Môn/Học kỳ.")
        self._schedule_auto_scan()

    def _handle_apply_context_error(self, error: Exception) -> None:
        if self.current_context is not None and self._invalidated_context_fields:
            self._apply_context(self.current_context, clear_score_rows=False)
        messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
        self._log(f"Lỗi áp ngữ cảnh: {error}")
        self._set_progress(0.0, "Áp ngữ cảnh thất bại")

    def on_apply_selected_context(self) -> None:
        if self.current_context is None:
            messagebox.showwarning("Chưa có dữ liệu", "Hãy load Sổ điểm trước khi áp ngữ cảnh.")
            self._log("Bỏ qua áp ngữ cảnh vì chưa có dữ liệu Sổ điểm.")
            return
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
            self._log(f"Lỗi áp ngữ cảnh: {error}")
            return
        grade_id, class_id, subject_id, term_id = self._context_request_ids()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        target_column_key = self._selected_target_score_key()
        target_column_label = self.target_score_column_var.get().strip()
        if (
            self.auto_scan_rows_var.get()
            and target_column_key
            and hasattr(automation, "scan_score_entries")
        ):
            self._run_background(
                "Đang áp ngữ cảnh, dò quyền nhập điểm và quét học sinh...",
                lambda progress: self._scan_score_rows_with_access(
                    automation,
                    grade_id=grade_id,
                    class_id=class_id,
                    subject_id=subject_id,
                    term_id=term_id,
                    target_column_key=target_column_key,
                    target_column_label=target_column_label,
                    username=username,
                    password=password,
                    progress_callback=progress,
                ),
                self._handle_scan_score_rows_success,
                self._handle_apply_context_error,
            )
            return
        self._run_background(
            "Đang áp ngữ cảnh Sổ điểm và dò quyền nhập điểm...",
            lambda progress: self._select_scorebook_context_with_access(
                automation,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=progress,
            ),
            self._handle_apply_context_success,
            self._handle_apply_context_error,
        )

    def _handle_apply_context_success(self, result: object) -> None:
        context = None
        login_message = ""
        selection_message = ""
        if isinstance(result, tuple):
            if len(result) >= 3:
                context, login_message, selection_message = result[:3]
            elif len(result) >= 2:
                context, login_message = result[:2]
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi áp ngữ cảnh.")
        if login_message:
            self._log(login_message)
        rewritten_selection_message = _rewrite_access_message_for_score_ui(selection_message)
        if rewritten_selection_message:
            self._log(rewritten_selection_message)
        self._apply_context(context)
        self._log_effective_context_identity(context)
        self._log("Đã áp ngữ cảnh Khối/Lớp/Môn/Học kỳ lên live browser.")
        self._schedule_auto_scan()

    def _clone_score_rows(self, rows: dict[str, ScoreStudentRow] | None = None) -> dict[str, ScoreStudentRow]:
        source_rows = self._score_rows_by_key if rows is None else rows
        cloned_rows: dict[str, ScoreStudentRow] = {}
        for row_key, row in source_rows.items():
            row_payload = asdict(row)
            row_payload["normalized_token_set"] = frozenset(row_payload.get("normalized_token_set", ()))
            row_payload["phonetic_token_set"] = frozenset(row_payload.get("phonetic_token_set", ()))
            cloned_rows[row_key] = ScoreStudentRow(**row_payload)
        return cloned_rows

    def _replace_score_rows(
        self,
        rows: dict[str, ScoreStudentRow],
        *,
        undo_stack: list[UndoRecord] | None = None,
    ) -> tuple[int, int]:
        with self._score_data_lock:
            self._score_rows_by_key = dict(sorted(rows.items(), key=lambda item: item[1].row_index))
            if undo_stack is None:
                self._undo_stack.clear()
            else:
                self._undo_stack = list(undo_stack)
            self._rebuild_student_indices()
            pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
            scanned_count = len(self._score_rows_by_key)
        self._refresh_score_tree()
        self._build_voice_hints()
        self.voice_summary_var.set(f"Đã quét {scanned_count} học sinh. Đang có {pending_count} dòng chờ ghi.")
        return scanned_count, pending_count

    def _restore_repair_scan_backup(self, log_message: str) -> None:
        backup_rows = self._repair_scan_backup_rows
        backup_undo = self._repair_scan_backup_undo
        if backup_rows is not None:
            self._replace_score_rows(backup_rows, undo_stack=backup_undo or [])
        self._repair_scan_backup_rows = None
        self._repair_scan_backup_undo = None
        self._retry_apply_after_scan = False
        if log_message:
            self._log(log_message)

    def _hydrate_score_rows(self, entries: list[object]) -> None:
        self._ptt_roster_revision += 1
        previous_rows = self._score_rows_by_key
        preserve_pending_for_same_target = bool(
            self._last_scanned_target_key
            and self._last_scanned_target_key == self._selected_target_score_key()
        )
        previous_rows_by_student_code: dict[str, ScoreStudentRow] = {}
        previous_rows_by_name: dict[str, ScoreStudentRow] = {}
        if preserve_pending_for_same_target:
            for previous_row in previous_rows.values():
                if not previous_row.pending_score:
                    continue
                previous_student_code = previous_row.student_code.strip()
                if previous_student_code and previous_student_code not in previous_rows_by_student_code:
                    previous_rows_by_student_code[previous_student_code] = previous_row
                previous_normalized_name = previous_row.normalized_name or _normalize_diacritic_text(previous_row.student_name)
                if previous_normalized_name and previous_normalized_name not in previous_rows_by_name:
                    previous_rows_by_name[previous_normalized_name] = previous_row
        reused_previous_row_keys: set[str] = set()
        rows: dict[str, ScoreStudentRow] = {}
        for entry in entries:
            row_index = int(_entry_value(entry, "row_index", 0) or 0)
            row_id = str(_entry_value(entry, "row_id", "")).strip()
            student_code = str(_entry_value(entry, "student_code", "")).strip()
            student_name = str(_entry_value(entry, "student_name", "")).strip()
            current_score = _format_score_value(_entry_value(entry, "current_score", ""))
            target_input_name = str(_entry_value(entry, "target_input_name", "")).strip()
            row_key = row_id or f"{student_code}:{row_index}"
            normalized_name = _normalize_diacritic_text(student_name)
            name_parts = normalized_name.split()
            normalized_last_name = name_parts[-1] if name_parts else normalized_name
            normalized_last_two = " ".join(name_parts[-2:]) if len(name_parts) >= 2 else normalized_name
            normalized_sorted_name = " ".join(sorted(name_parts))
            normalized_token_set = frozenset(name_parts)
            phonetic_name = _voice_phonetic_text(normalized_name)
            phonetic_parts = phonetic_name.split()
            phonetic_last_name = phonetic_parts[-1] if phonetic_parts else phonetic_name
            phonetic_last_two = " ".join(phonetic_parts[-2:]) if len(phonetic_parts) >= 2 else phonetic_name
            phonetic_sorted_name = " ".join(sorted(phonetic_parts))
            phonetic_token_set = frozenset(phonetic_parts)
            # KHMER #A: pre-compute strict phonetic keys (chỉ dùng tier 3 fallback).
            strict_phonetic_name = _voice_phonetic_text_strict(normalized_name)
            strict_phonetic_parts = strict_phonetic_name.split()
            strict_phonetic_last_name = (
                strict_phonetic_parts[-1] if strict_phonetic_parts else strict_phonetic_name
            )
            strict_phonetic_last_two = (
                " ".join(strict_phonetic_parts[-2:])
                if len(strict_phonetic_parts) >= 2
                else strict_phonetic_name
            )
            preview_row = ScoreStudentRow(
                row_key=row_key,
                row_index=row_index,
                row_id=row_id,
                student_code=student_code,
                student_name=student_name,
                current_score=current_score,
                target_input_name=target_input_name,
                normalized_name=normalized_name,
                normalized_last_name=normalized_last_name,
                normalized_last_two=normalized_last_two,
                normalized_sorted_name=normalized_sorted_name,
                normalized_token_set=normalized_token_set,
                phonetic_name=phonetic_name,
                phonetic_last_name=phonetic_last_name,
                phonetic_last_two=phonetic_last_two,
                phonetic_sorted_name=phonetic_sorted_name,
                phonetic_token_set=phonetic_token_set,
                strict_phonetic_name=strict_phonetic_name,
                strict_phonetic_last_name=strict_phonetic_last_name,
                strict_phonetic_last_two=strict_phonetic_last_two,
            )
            previous_row = previous_rows.get(row_key)
            if previous_row is None and preserve_pending_for_same_target:
                fallback_row = previous_rows_by_student_code.get(student_code) or previous_rows_by_name.get(normalized_name)
                if fallback_row is not None and fallback_row.row_key not in reused_previous_row_keys:
                    previous_row = fallback_row
            if previous_row is not None and previous_row.pending_score and (
                previous_row.target_input_name == target_input_name or preserve_pending_for_same_target
            ):
                reused_previous_row_keys.add(previous_row.row_key)
                preview_row.pending_score = previous_row.pending_score
                preview_row.recognized_text = previous_row.recognized_text
                preview_row.match_score = previous_row.match_score
                preview_row.status = previous_row.status
            rows[row_key] = preview_row
        self._replace_score_rows(rows)

    def _rebuild_student_indices(self) -> None:
        self._student_full_index.clear()
        self._student_last_name_index.clear()
        self._student_last_two_index.clear()
        self._student_token_index.clear()
        self._student_phonetic_full_index.clear()
        self._student_phonetic_last_name_index.clear()
        self._student_phonetic_last_two_index.clear()
        self._student_phonetic_token_index.clear()
        self._voice_match_cache.clear()
        for row_key, row in self._score_rows_by_key.items():
            if row.normalized_name:
                self._student_full_index.setdefault(row.normalized_name, []).append(row_key)
            if row.normalized_last_name:
                self._student_last_name_index.setdefault(row.normalized_last_name, []).append(row_key)
            if row.normalized_last_two:
                self._student_last_two_index.setdefault(row.normalized_last_two, []).append(row_key)
            for token in row.normalized_token_set:
                if len(token) >= 2:
                    self._student_token_index.setdefault(token, set()).add(row_key)
            if row.phonetic_name:
                self._student_phonetic_full_index.setdefault(row.phonetic_name, []).append(row_key)
            if row.phonetic_last_name:
                self._student_phonetic_last_name_index.setdefault(row.phonetic_last_name, []).append(row_key)
            if row.phonetic_last_two:
                self._student_phonetic_last_two_index.setdefault(row.phonetic_last_two, []).append(row_key)
            for token in row.phonetic_token_set:
                if len(token) >= 2:
                    self._student_phonetic_token_index.setdefault(token, set()).add(row_key)
            # Thêm biệt danh vào các index tìm kiếm
            aliases = self._student_aliases.get(row.student_name, [])
            for alias in aliases:
                normalized_alias = _normalize_diacritic_text(alias)
                if not normalized_alias:
                    continue
                # Alias được coi như tên đầy đủ → score 100 khi exact match
                self._student_full_index.setdefault(normalized_alias, []).append(row_key)
                # Thêm từng token của alias vào token index
                alias_tokens = normalized_alias.split()
                for token in alias_tokens:
                    if len(token) >= 2:
                        self._student_token_index.setdefault(token, set()).add(row_key)
                # Nếu alias có ≥2 từ, thêm vào last_two index
                if len(alias_tokens) >= 2:
                    alias_last_two = " ".join(alias_tokens[-2:])
                    self._student_last_two_index.setdefault(alias_last_two, []).append(row_key)
                # Từ cuối của alias → last_name index
                if alias_tokens:
                    self._student_last_name_index.setdefault(alias_tokens[-1], []).append(row_key)
                phonetic_alias = _voice_phonetic_text(alias)
                phonetic_alias_tokens = phonetic_alias.split()
                if phonetic_alias:
                    self._student_phonetic_full_index.setdefault(phonetic_alias, []).append(row_key)
                for token in phonetic_alias_tokens:
                    if len(token) >= 2:
                        self._student_phonetic_token_index.setdefault(token, set()).add(row_key)
                if len(phonetic_alias_tokens) >= 2:
                    phonetic_last_two = " ".join(phonetic_alias_tokens[-2:])
                    self._student_phonetic_last_two_index.setdefault(phonetic_last_two, []).append(row_key)
                if phonetic_alias_tokens:
                    self._student_phonetic_last_name_index.setdefault(phonetic_alias_tokens[-1], []).append(row_key)

    def _tree_values_for_row(self, row: ScoreStudentRow) -> tuple[object, ...]:
        return (
            row.row_index,
            row.student_name,
            row.current_score,
            row.pending_score,
            row.recognized_text,
            f"{row.match_score}%" if row.match_score else "",
            row.status,
        )

    def _autosize_student_name_column(self) -> None:
        if not hasattr(self, "preview_tree"):
            return
        try:
            style = ttk.Style(self.root)
            font_spec = style.lookup("Treeview", "font") or ("Segoe UI", 10)
            tree_font = tkfont.Font(font=font_spec)
            header_width = tree_font.measure("Họ tên") + 28
            longest_name_width = max(
                (tree_font.measure(row.student_name or "") for row in self._score_rows_by_key.values()),
                default=0,
            )
            target_width = max(header_width, longest_name_width + 28, 150)
            target_width = min(target_width, 320)
            self.preview_tree.column("student_name", width=target_width, minwidth=max(140, min(target_width, 220)))
        except tk.TclError:
            return

    def _focus_preview_tree(self) -> None:
        if self._tree_editor is not None:
            return

        def _apply_focus() -> None:
            try:
                if hasattr(self, "preview_tree") and self.preview_tree.winfo_exists():
                    self.preview_tree.focus_set()
                else:
                    self.root.focus_set()
            except tk.TclError:
                pass

        try:
            self.root.after_idle(_apply_focus)
        except tk.TclError:
            pass

    def _bind_dynamic_wrap(
        self,
        widget: tk.Misc,
        *,
        reference: tk.Misc | None = None,
        padding: int = 24,
        min_width: int = 160,
    ) -> None:
        target = reference or widget

        def _update_wrap(_event: tk.Event | None = None) -> None:
            try:
                width = int(target.winfo_width())
                if width <= 1:
                    width = int(widget.winfo_width())
                widget.configure(wraplength=max(min_width, width - padding))
            except tk.TclError:
                pass

        try:
            target.bind("<Configure>", _update_wrap, add="+")
            self.root.after_idle(_update_wrap)
        except tk.TclError:
            pass

    def _row_tag(self, row: ScoreStudentRow) -> str:
        lowered = row.status.lower()
        if "lỗi" in lowered:
            return "error"
        if "chưa lưu" in lowered:
            return "filled"
        if "đã ghi" in lowered:
            return "saved"
        if row.pending_score:
            return "pending"
        return ""

    def _refresh_score_tree(self) -> None:
        if not hasattr(self, "preview_tree"):
            return
        self._stop_tree_editor(commit=False)
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        self._tree_item_by_key.clear()
        # IMP-B7: Lọc theo search filter nếu có
        search_text = self._search_var.get().strip().lower() if hasattr(self, "_search_var") else ""
        for row_key, row in self._score_rows_by_key.items():
            if search_text and search_text not in row.student_name.lower():
                continue
            item = self.preview_tree.insert("", tk.END, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))
            self._tree_item_by_key[row_key] = item
        self._autosize_student_name_column()

    def _filter_score_tree(self) -> None:
        """IMP-B7: Lọc treeview theo search text — gọi khi user gõ vào ô tìm kiếm."""
        self._refresh_score_tree()

    def on_scan_score_rows(self) -> None:
        if self._busy:
            return
        if not self._retry_apply_after_scan:
            self._repair_scan_backup_rows = None
            self._repair_scan_backup_undo = None
        if self._invalidated_context_fields:
            messagebox.showinfo("Ngữ cảnh đang cập nhật", "Hãy chờ app cập nhật xong Lớp/Môn cho ngữ cảnh mới rồi quét danh sách học sinh.")
            return
        if self.current_context is None:
            messagebox.showwarning("Chưa có ngữ cảnh", "Hãy load Sổ điểm trước khi quét danh sách học sinh.")
            return
        target_column_key = self._selected_target_score_key()
        if not target_column_key:
            messagebox.showwarning("Chưa chọn cột điểm", "Hãy chọn cột điểm đích trước khi app tải danh sách học sinh.")
            return
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi khởi tạo automation", str(error))
            self._log(f"Lỗi scan học sinh: {error}")
            return
        if not hasattr(automation, "scan_score_entries"):
            messagebox.showerror("Thiếu tính năng", "Automation hiện tại chưa có API scan_score_entries.")
            return
        grade_id, class_id, subject_id, term_id = self._context_request_ids()
        if not class_id or not subject_id:
            messagebox.showinfo(
                "Không có quyền nhập điểm",
                "Ngữ cảnh hiện tại không có lớp/môn nào được phép nhập điểm.",
            )
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        target_column_label = self.target_score_column_var.get().strip()
        self._run_background(
            "Đang quét toàn bộ học sinh và cột điểm...",
            lambda progress: automation.scan_score_entries(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                target_column_key=target_column_key,
                target_column_label=target_column_label,
                username=username,
                password=password,
                progress_callback=progress,
            ),
            self._handle_scan_score_rows_success,
            self._handle_scan_score_rows_error,
        )

    def _handle_scan_score_rows_success(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) < 4:
            raise RuntimeError("Kết quả scan_score_entries không hợp lệ.")
        context, entries, login_message, selection_message = result[:4]
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi quét học sinh.")
        repair_backup_rows = self._repair_scan_backup_rows
        repair_backup_undo = self._repair_scan_backup_undo
        backup_pending_count = sum(1 for row in repair_backup_rows.values() if row.pending_score) if repair_backup_rows else 0
        scanned_entries = list(entries or [])
        if login_message:
            self._log(login_message)
        rewritten_selection_message = _rewrite_access_message_for_score_ui(selection_message)
        if rewritten_selection_message:
            self._log(rewritten_selection_message)
        if repair_backup_rows is not None and (
            not scanned_entries
            or not any(str(_entry_value(entry, "target_input_name", "")).strip() for entry in scanned_entries)
        ):
            self._restore_repair_scan_backup(
                "Quét sửa không đọc được liên kết ô nhập live; app đã giữ nguyên toàn bộ điểm chờ cũ."
            )
            messagebox.showwarning(
                "Không khôi phục được liên kết live",
                "App không khôi phục được ô nhập live cho cột điểm này nên đã giữ nguyên toàn bộ Điểm chờ ghi cũ. Hãy quét lại thủ công hoặc tải lại Sổ điểm trước khi ghi.",
            )
            return
        context = self._copy_access_scope(context, self.current_context)
        self._apply_context(context, clear_score_rows=False)
        self._log_effective_context_identity(context)
        self._hydrate_score_rows(scanned_entries)
        if repair_backup_rows is not None:
            repaired_pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
            repaired_linked_pending_count = sum(
                1 for row in self._score_rows_by_key.values() if row.pending_score and row.target_input_name
            )
            if repaired_pending_count < backup_pending_count or (
                backup_pending_count > 0 and repaired_linked_pending_count == 0
            ):
                self._restore_repair_scan_backup(
                    "Quét sửa không bảo toàn được điểm chờ cũ; app đã khôi phục lại dữ liệu trước khi quét sửa."
                )
                messagebox.showwarning(
                    "Đã giữ lại điểm chờ cũ",
                    "Lần quét sửa vừa rồi không bảo toàn được toàn bộ Điểm chờ ghi, nên app đã tự khôi phục lại dữ liệu cũ để tránh mất điểm đã đọc.",
                )
                return
        self._last_scanned_target_key = self._selected_target_score_key()
        self._focus_preview_tree()
        self._log(f"Đã quét {len(self._score_rows_by_key)} học sinh cho cột {self.target_score_column_var.get()}.")
        if self._ptt_enabled:
            self._build_voice_hints()
        self._repair_scan_backup_rows = None
        self._repair_scan_backup_undo = None
        if self._retry_apply_after_scan:
            self._log("Đã quét lại xong; app đang thử ghi điểm lại.")
            self.root.after(0, self.on_apply_pending_scores)

    def _handle_scan_score_rows_error(self, error: Exception) -> None:
        self._repair_scan_backup_rows = None
        self._repair_scan_backup_undo = None
        self._retry_apply_after_scan = False
        messagebox.showerror("Lỗi quét học sinh", str(error))
        self._log(f"Lỗi quét học sinh: {error}")
        self._set_progress(0.0, "Quét học sinh thất bại")

    def _pending_rows(self, row_keys: list[str] | None = None) -> list[ScoreStudentRow]:
        candidates = self._score_rows_by_key.values() if row_keys is None else [self._score_rows_by_key[key] for key in row_keys if key in self._score_rows_by_key]
        return [row for row in candidates if row.pending_score and row.target_input_name]

    def _selected_preview_row_keys(self) -> list[str]:
        if not hasattr(self, "preview_tree"):
            return []
        selected_items = set(self.preview_tree.selection())
        if not selected_items:
            return []
        return [row_key for row_key, tree_item in self._tree_item_by_key.items() if tree_item in selected_items]

    def _build_apply_payload(self, row_keys: list[str] | None = None, *, score_field: str = "pending_score") -> list[dict[str, str]]:
        candidates = self._score_rows_by_key.values() if row_keys is None else [self._score_rows_by_key[key] for key in row_keys if key in self._score_rows_by_key]
        return _build_score_apply_payload(list(candidates), score_field=score_field)

    def on_apply_pending_scores(self) -> None:
        if not self._stop_tree_editor(commit=True):
            return
        if self._invalidated_context_fields:
            messagebox.showinfo("Ngữ cảnh đang cập nhật", "Hãy chờ app cập nhật xong Lớp/Môn cho ngữ cảnh mới rồi mới ghi điểm lên web.")
            return
        payload = self._build_apply_payload()
        apply_from_current_selection = False
        if not payload:
            if any(row.pending_score for row in self._score_rows_by_key.values()):
                if self._retry_apply_after_scan:
                    self._retry_apply_after_scan = False
                    messagebox.showinfo(
                        "Cần quét lại dữ liệu",
                        "Đang có Điểm chờ ghi nhưng app chưa gắn được ô nhập live cho cột điểm này. Hãy quét lại danh sách học sinh rồi bấm GHI lại.",
                    )
                    return
                if self.current_context is not None and self._selected_target_score_key():
                    self._repair_scan_backup_rows = self._clone_score_rows()
                    self._repair_scan_backup_undo = list(self._undo_stack)
                    self._retry_apply_after_scan = True
                    self._log("Phát hiện điểm chờ nhưng thiếu liên kết ô nhập live; app đang quét lại danh sách để khôi phục target_input_name.")
                    self.on_scan_score_rows()
                    return
                messagebox.showinfo(
                    "Cần quét lại dữ liệu",
                    "Đang có Điểm chờ ghi nhưng app chưa gắn được ô nhập live cho cột điểm này. Hãy quét lại danh sách học sinh rồi bấm GHI lại.",
                )
                return
            selected_row_keys = self._selected_preview_row_keys()
            payload = self._build_apply_payload(selected_row_keys, score_field="current_score")
            if payload:
                apply_from_current_selection = True
            else:
                messagebox.showinfo("Chưa có dữ liệu", "Chưa có Điểm chờ ghi. Nếu muốn ghi lại Điểm hiện tại, hãy chọn ít nhất 1 dòng có điểm trong Treeview rồi bấm lại.")
                return
        self._retry_apply_after_scan = False
        # IMP-A4: Confirm trước khi ghi điểm lên web, hiển thị tóm tắt
        summary_text = _build_apply_confirmation_summary(payload, self._score_rows_by_key)
        confirmation_message = (
            f"Chưa có Điểm chờ ghi. App sẽ dùng Điểm hiện tại của {len(payload)} dòng đang chọn để ghi lên VNEDU:\n\n{summary_text}\n\nBạn có chắc muốn tiếp tục?"
            if apply_from_current_selection
            else f"Sẽ ghi {len(payload)} điểm lên VNEDU:\n\n{summary_text}\n\nBạn có chắc muốn tiếp tục?"
        )
        if not messagebox.askyesno(
            "Xác nhận ghi điểm lên web",
            confirmation_message,
        ):
            return
        if self.current_context is None:
            messagebox.showwarning("Chưa có ngữ cảnh", "Hãy load Sổ điểm trước khi ghi điểm.")
            return
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi khởi tạo automation", str(error))
            self._log(f"Lỗi ghi điểm: {error}")
            return
        if not hasattr(automation, "apply_score_entries"):
            messagebox.showerror("Thiếu tính năng", "Automation hiện tại chưa có API apply_score_entries.")
            return
        grade_id, class_id, subject_id, term_id = self._context_request_ids()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        auto_save = bool(self.auto_save_scores_var.get())
        submitted_entries_by_input_name = {
            item["target_input_name"]: {
                "row_key": item["row_key"],
                "proposed_score": item["proposed_score"],
            }
            for item in payload
        }

        self._run_background(
            "Đang ghi điểm lên live VNEDU..." if apply_from_current_selection else "Đang ghi các điểm chờ lên live VNEDU...",
            lambda progress: automation.apply_score_entries(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                entries=payload,
                username=username,
                password=password,
                auto_save=auto_save,
                progress_callback=progress,
            ),
            lambda result: self._handle_apply_pending_scores_success(result, submitted_entries_by_input_name, auto_save=auto_save),
            lambda error: (messagebox.showerror("Lỗi ghi điểm", str(error)), self._log(f"Lỗi ghi điểm: {error}"), self._set_progress(0.0, "Ghi điểm thất bại")),
        )

    def _handle_apply_pending_scores_success(
        self,
        result: object,
        submitted_entries_by_input_name: dict[str, dict[str, str]],
        auto_save: bool = True,
    ) -> None:
        if not isinstance(result, tuple) or len(result) < 4:
            raise RuntimeError("Kết quả apply_score_entries không hợp lệ.")
        context, write_result, login_message, selection_message = result[:4]
        if isinstance(context, ScorebookContext):
            self._apply_context(context, clear_score_rows=False)
        if login_message:
            self._log(login_message)
        if selection_message:
            self._log(selection_message)

        verified_names = list(_entry_value(write_result, "verified_input_names", [])) if write_result is not None else []
        failed_names = list(_entry_value(write_result, "failed_input_names", [])) if write_result is not None else []
        attempted = int(_entry_value(write_result, "attempted", len(verified_names))) if write_result is not None else 0
        save_verified = bool(_entry_value(write_result, "save_verified", False)) if write_result is not None else False
        save_detail = str(_entry_value(write_result, "save_verification_detail", "")).strip() if write_result is not None else ""

        # H1 FIX: Một dòng chỉ được coi là "Đã ghi" (lưu lên VNEDU) khi đã bật tự
        # bấm Lưu VÀ server xác nhận. Nếu chỉ điền vào DOM mà chưa Lưu, giữ nguyên
        # pending_score để tránh mất điểm và đánh dấu trạng thái "Đã điền (chưa lưu)".
        truly_saved = bool(auto_save and save_verified)
        saved_count = 0
        filled_count = 0
        # LOCK FIX: mutate row data dưới _score_data_lock để PTT worker đọc nhất
        # quán (handler chạy main thread, worker đọc cùng field qua hint/match).
        # Tk widget update gom ra ngoài lock theo đúng pattern _apply_row_patch.
        rows_to_repaint: list[str] = []
        with self._score_data_lock:
            for input_name in verified_names:
                submitted_entry = submitted_entries_by_input_name.get(str(input_name).strip(), {})
                row_key = str(submitted_entry.get("row_key", "")).strip()
                row = self._score_rows_by_key.get(row_key)
                if row is None:
                    continue
                submitted_score = str(submitted_entry.get("proposed_score", "") or "").strip()
                if truly_saved:
                    if submitted_score:
                        row.current_score = submitted_score
                    row.pending_score = ""
                    row.status = RowStatus.SAVED
                    saved_count += 1
                else:
                    # Điểm đã được điền vào ô nhập trên trình duyệt nhưng CHƯA lưu lên
                    # server. Giữ pending_score để người dùng có thể Lưu lại sau.
                    if submitted_score:
                        row.pending_score = submitted_score
                    row.status = RowStatus.FILLED
                    filled_count += 1
                rows_to_repaint.append(row.row_key)

            for input_name in failed_names:
                submitted_entry = submitted_entries_by_input_name.get(str(input_name).strip(), {})
                row_key = str(submitted_entry.get("row_key", "")).strip()
                row = self._score_rows_by_key.get(row_key)
                if row is None:
                    continue
                row.status = RowStatus.ERROR
                rows_to_repaint.append(row.row_key)

            pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
            student_count = len(self._score_rows_by_key)

        for repaint_key in rows_to_repaint:
            row = self._score_rows_by_key.get(repaint_key)
            if row is None:
                continue
            item = self._tree_item_by_key.get(repaint_key)
            if item:
                self.preview_tree.item(item, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))

        self.voice_summary_var.set(f"Đã quét {student_count} học sinh. Còn {pending_count} dòng chờ ghi.")
        if truly_saved:
            self._log(
                f"Đã LƯU {saved_count}/{attempted} dòng điểm lên VNEDU. "
                f"Xác minh lưu server: có{f' ({save_detail})' if save_detail else ''}.",
                tag=LogTag.SUCCESS,
            )
        elif filled_count:
            self._log(
                f"Đã điền {filled_count}/{attempted} dòng vào ô nhập nhưng CHƯA lưu lên VNEDU. "
                "Hãy bật 'Tự bấm Lưu sau khi ghi' rồi ghi lại, hoặc bấm Lưu thủ công trên trình duyệt. "
                "Điểm chờ được giữ nguyên để tránh mất dữ liệu.",
                tag=LogTag.WARNING,
            )
        else:
            self._log(
                f"Ghi điểm chưa thành công ({attempted} dòng đã thử). "
                f"Xác minh lưu server: không{f' ({save_detail})' if save_detail else ''}.",
                tag=LogTag.WARNING,
            )


    def on_clear_pending_scores(self) -> None:
        if not self._stop_tree_editor(commit=True):
            return
        if not any(row.pending_score for row in self._score_rows_by_key.values()):
            return
        # IMP-A3: Confirm trước khi xóa tất cả điểm chờ
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        if not messagebox.askyesno(
            "Xác nhận xóa điểm chờ",
            f"Bạn có chắc muốn xóa {pending_count} điểm chờ ghi?\n\nThao tác này không thể hoàn tác.",
            icon="warning",
        ):
            return
        # BUG #20: snapshot row_keys trước khi mutate, tránh edge-case re-entry
        # nếu sau này _apply_row_patch trigger callback đụng vào dict gốc.
        rows_to_clear = [
            (row_key, row)
            for row_key, row in self._score_rows_by_key.items()
            if row.pending_score
        ]
        for row_key, row in rows_to_clear:
            self._apply_row_patch(
                row_key,
                pending_score="",
                status=RowStatus.READY,
                recognized_text=row.recognized_text,
                match_score=0,
                reason="xóa điểm chờ",
            )
        self._update_score_summary()

    def on_round_pending_scores(self) -> None:
        if not self._stop_tree_editor(commit=True):
            return
        candidate_rows = [
            (row_key, row)
            for row_key, row in self._score_rows_by_key.items()
            if row.pending_score or row.current_score
        ]
        if not candidate_rows:
            messagebox.showinfo("Chưa có dữ liệu", "Chưa có điểm chờ hoặc điểm hiện tại để làm tròn.")
            return

        rounded_count = 0
        for row_key, row in candidate_rows:
            source_score = row.pending_score or row.current_score
            rounded_score = _round_score_to_one_decimal(source_score)
            if rounded_score == source_score:
                continue
            self._apply_row_patch(
                row_key,
                pending_score=rounded_score,
                status=RowStatus.PENDING,
                recognized_text=row.recognized_text,
                match_score=row.match_score,
                reason=("làm tròn điểm chờ" if row.pending_score else "làm tròn từ điểm hiện tại"),
            )
            rounded_count += 1

        if rounded_count > 0:
            status_message = f"Đã làm tròn {rounded_count} điểm tới 1 chữ số thập phân."
        else:
            status_message = "Không có điểm chờ hoặc điểm hiện tại nào cần làm tròn."
        self.voice_status_var.set(status_message)
        self._log(status_message)

    def _row_snapshot(self, row: ScoreStudentRow) -> dict[str, object]:
        return {
            "pending_score": row.pending_score,
            "recognized_text": row.recognized_text,
            "match_score": row.match_score,
            "status": row.status,
            "current_score": row.current_score,
        }

    def _apply_row_state(self, row_key: str, snapshot: dict[str, object]) -> None:
        # BUG #15 hardening: mutate row data dưới _score_data_lock để PTT worker
        # đọc consistent. Tk widget update vẫn ở ngoài lock vì luôn chạy main thread.
        with self._score_data_lock:
            row = self._score_rows_by_key.get(row_key)
            if row is None:
                return
            row.pending_score = str(snapshot.get("pending_score", row.pending_score) or "")
            row.recognized_text = str(snapshot.get("recognized_text", row.recognized_text) or "")
            row.match_score = int(snapshot.get("match_score", row.match_score) or 0)
            row.status = str(snapshot.get("status", row.status) or row.status)
            row.current_score = str(snapshot.get("current_score", row.current_score) or "")
        item = self._tree_item_by_key.get(row_key)
        if item:
            self.preview_tree.item(item, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))

    def _apply_row_patch(
        self,
        row_key: str,
        *,
        pending_score: str,
        status: str,
        recognized_text: str,
        match_score: int,
        reason: str,
        current_score: str | None = None,
        push_undo: bool = True,
    ) -> None:
        # BUG #15 hardening: lock cho phần mutate row + undo append; UI update
        # ngoài lock vì là main thread và phụ thuộc snapshot row sau mutation.
        with self._score_data_lock:
            row = self._score_rows_by_key.get(row_key)
            if row is None:
                return
            before = self._row_snapshot(row)
            row.pending_score = pending_score
            row.status = status
            row.recognized_text = recognized_text
            row.match_score = match_score
            if current_score is not None:
                row.current_score = current_score
            after = self._row_snapshot(row)
            if push_undo and before != after:
                self._undo_stack.append(UndoRecord(row_key=row_key, before=before, after=after, reason=reason))
                # BUG-03 FIX: Giới hạn undo stack để tránh memory leak
                while len(self._undo_stack) > UNDO_STACK_MAX_SIZE:
                    self._undo_stack.pop(0)
        item = self._tree_item_by_key.get(row_key)
        if item:
            self.preview_tree.item(item, values=self._tree_values_for_row(row), tags=(self._row_tag(row),))
        # IMP-B6: Cập nhật pending count + undo count trên summary
        self._update_score_summary()

    def _update_score_summary(self) -> None:
        """IMP-B6 + IMP-D8: Cập nhật voice_summary_var với pending count, undo count và phân bố điểm."""
        if not self._score_rows_by_key:
            return
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        undo_count = len(self._undo_stack)
        undo_hint = f"  •  Undo: {undo_count}" if undo_count > 0 else ""
        # IMP-D8: Tính phân bố điểm chờ (min/max/avg)
        pending_values = []
        for row in self._score_rows_by_key.values():
            if row.pending_score:
                try:
                    pending_values.append(float(row.pending_score.replace(",", ".")))
                except (ValueError, AttributeError):
                    pass
        stats_hint = ""
        if pending_values:
            min_v = min(pending_values)
            max_v = max(pending_values)
            avg_v = sum(pending_values) / len(pending_values)
            stats_hint = f"  •  Điểm: {min_v:.1f}↓ {avg_v:.1f}~ {max_v:.1f}↑"
        self.voice_summary_var.set(
            f"Đã quét {len(self._score_rows_by_key)} học sinh. Còn {pending_count} dòng chờ ghi.{undo_hint}{stats_hint}"
        )

    def _on_undo_shortcut(self, _event: tk.Event | None = None) -> str:
        focused = self.root.focus_get()
        if self._tree_editor is not None and focused is self._tree_editor:
            return "break"
        if not self._undo_stack:
            return "break"
        undo = self._undo_stack.pop()
        if undo.row_key in self._score_rows_by_key:
            self._apply_row_state(undo.row_key, undo.before)
            self._log(f"Undo: {self._score_rows_by_key[undo.row_key].student_name} ({undo.reason}).")
            self._update_score_summary()
        return "break"

    def _on_tree_delete(self, _event: tk.Event | None = None) -> str:
        selected_items = list(self.preview_tree.selection())
        # IMP-C7: Đếm số dòng có điểm chờ sẽ bị xóa
        rows_with_pending = []
        for item in selected_items:
            row_key = next((key for key, tree_item in self._tree_item_by_key.items() if tree_item == item), "")
            row = self._score_rows_by_key.get(row_key)
            if row is not None and row.pending_score:
                rows_with_pending.append((row_key, row))
        if not rows_with_pending:
            return "break"
        # IMP-C7: Xác nhận trước khi xóa nếu có ≥1 dòng
        if len(rows_with_pending) == 1:
            confirm_msg = f"Xóa điểm chờ ({rows_with_pending[0][1].pending_score}) của {rows_with_pending[0][1].student_name}?"
        else:
            confirm_msg = f"Xóa điểm chờ của {len(rows_with_pending)} học sinh?"
        if not messagebox.askyesno("Xác nhận xóa", confirm_msg, parent=self.root):
            return "break"
        for row_key, row in rows_with_pending:
            self._apply_row_patch(
                row_key,
                pending_score="",
                status=RowStatus.READY,
                recognized_text=row.recognized_text,
                match_score=0,
                reason="xóa bằng phím Delete",
            )
        self._update_score_summary()
        return "break"

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item = self.preview_tree.identify_row(event.y)
        column = self.preview_tree.identify_column(event.x)
        if not item or column != "#4":
            self._stop_tree_editor(commit=False)
            return
        row_key = next((key for key, tree_item in self._tree_item_by_key.items() if tree_item == item), "")
        if row_key:
            self._start_tree_editor(row_key, item, column)

    def _start_tree_editor(self, row_key: str, item: str, column: str) -> None:
        self._stop_tree_editor(commit=False)
        bbox = self.preview_tree.bbox(item, column)
        if not bbox:
            return
        x, y, width, height = bbox
        row = self._score_rows_by_key[row_key]
        editor = ttk.Entry(self.preview_tree)
        editor.insert(0, row.pending_score)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.select_range(0, tk.END)
        editor.bind("<Return>", lambda _e: self._stop_tree_editor(commit=True))
        editor.bind("<Escape>", lambda _e: self._stop_tree_editor(commit=False))
        editor.bind("<FocusOut>", lambda _e: self._stop_tree_editor(commit=True))
        # IMP-B3: Tab nhảy sang row kế, Shift+Tab quay lại row trước
        editor.bind("<Tab>", lambda _e: self._tree_editor_navigate(row_key, direction=1))
        editor.bind("<Shift-Tab>", lambda _e: self._tree_editor_navigate(row_key, direction=-1))
        self._tree_editor = editor
        self._tree_editor_info = {"row_key": row_key}

    def _tree_editor_navigate(self, current_row_key: str, *, direction: int) -> str:
        """IMP-B3: Tab/Shift+Tab navigate giữa các row trong tree editor.

        Commit giá trị hiện tại, rồi mở editor ở row kế (direction=1)
        hoặc row trước (direction=-1) trên cột pending_score (#4).
        """
        if not self._stop_tree_editor(commit=True):
            return "break"
        all_keys = list(self._score_rows_by_key.keys())
        if current_row_key not in all_keys:
            return "break"
        current_idx = all_keys.index(current_row_key)
        next_idx = current_idx + direction
        if next_idx < 0 or next_idx >= len(all_keys):
            return "break"
        next_key = all_keys[next_idx]
        next_item = self._tree_item_by_key.get(next_key)
        if next_item:
            self.preview_tree.see(next_item)
            self.preview_tree.selection_set(next_item)
            self._start_tree_editor(next_key, next_item, "#4")
        return "break"

    def _stop_tree_editor(self, commit: bool) -> bool:
        if self._tree_editor is None:
            return True
        editor = self._tree_editor
        row_key = self._tree_editor_info.get("row_key", "")
        value = editor.get().strip()
        row = self._score_rows_by_key.get(row_key)
        normalized_pending = ""
        should_clear_pending = False
        if commit and row is not None:
            if value:
                score_value = parse_manual_score_text(value)
                if score_value is None:
                    messagebox.showwarning("Điểm không hợp lệ", "Hãy nhập điểm từ 0 đến 10.")
                    try:
                        editor.focus_set()
                        editor.select_range(0, tk.END)
                    except tk.TclError:
                        pass
                    return False
                normalized_pending = _format_score_value(score_value)
            else:
                should_clear_pending = True
        try:
            editor.destroy()
        except tk.TclError:
            pass
        self._tree_editor = None
        self._tree_editor_info = {}
        if not commit or not row_key or row_key not in self._score_rows_by_key:
            return True
        row = self._score_rows_by_key[row_key]
        if not should_clear_pending:
            self._apply_row_patch(
                row_key,
                pending_score=normalized_pending,
                status=RowStatus.PENDING,
                recognized_text=row.recognized_text,
                match_score=row.match_score,
                reason="sửa tay trong Treeview",
            )
        else:
            self._apply_row_patch(
                row_key,
                pending_score="",
                status=RowStatus.READY,
                recognized_text=row.recognized_text,
                match_score=0,
                reason="xóa tay trong Treeview",
            )
        pending_count = sum(1 for preview_row in self._score_rows_by_key.values() if preview_row.pending_score)
        self.voice_summary_var.set(f"Đã quét {len(self._score_rows_by_key)} học sinh. Còn {pending_count} dòng chờ ghi.")
        return True

    def _build_voice_hints(self) -> None:
        primary_hints: list[str] = []
        extended_hints: list[str] = []
        seen: set[str] = set()

        def add_hint(text: str, *, primary: bool) -> None:
            candidate = str(text or "").strip()
            if not candidate:
                return
            normalized = _normalize_diacritic_text(candidate)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            extended_hints.append(candidate)
            if primary:
                primary_hints.append(candidate)

        for row in self._score_rows_by_key.values():
            add_hint(row.student_name, primary=True)
        for row in self._score_rows_by_key.values():
            parts = row.student_name.split()
            if len(parts) >= 2:
                add_hint(" ".join(parts[-2:]), primary=True)
        for row in self._score_rows_by_key.values():
            parts = row.student_name.split()
            if parts:
                add_hint(parts[-1], primary=False)

        # Thêm biệt danh làm primary hints (ưu tiên cao nhất)
        for student_name, aliases in self._student_aliases.items():
            for alias in aliases:
                add_hint(alias, primary=True)
                # Thêm combo "alias + điểm" vào extended hints
                short_scores_alias = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
                for s in short_scores_alias:
                    add_hint(f"{alias} {s}", primary=False)

        score_hints = [
            "điểm", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
            "0.5", "1.5", "2.5", "3.5", "4.5", "5.5", "6.5", "7.5", "8.5", "9.5",
            "không", "một", "hai", "ba", "bốn", "tư", "năm", "sáu", "bảy", "tám", "chín", "mười",
            "rưỡi", "phẩy", "xóa", "hủy",
        ]
        for hint in score_hints:
            add_hint(hint, primary=True)

        short_scores = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
        for row in self._score_rows_by_key.values():
            parts = row.student_name.split()
            short_name = " ".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")
            if short_name:
                for s in short_scores:
                    add_hint(f"{short_name} {s}", primary=False)
                    if len(extended_hints) >= VOICE_HINT_EXTENDED_LIMIT:
                        break
            if len(extended_hints) >= VOICE_HINT_EXTENDED_LIMIT:
                break

        self._voice_hints_primary = primary_hints[:VOICE_HINT_PRIMARY_LIMIT]
        self._voice_hints_extended = extended_hints[:VOICE_HINT_EXTENDED_LIMIT]
        self._voice_hints = list(self._voice_hints_extended)

    def _build_smart_voice_hints(self, roster_revision: int | None = None) -> list[str]:
        """Builds a compact phrase-hint list focused on the current context.

        PERF #4: thay vì gửi 96 tên mỗi request, chỉ gửi ≤ VOICE_HINT_SMART_LIMIT:
            1. Tên đang pending (xác suất cao nhất sẽ được nói tiếp).
            2. Tên dùng gần đây (LRU).
            3. Tên còn READY chưa nhập điểm.
            4. Bộ token điểm (số chữ + số digit).
        Lý do: payload phrase nhỏ → Google trả top-1 chính xác hơn, response gọn hơn.
        """
        ordered: list[str] = []
        seen_keys: set[str] = set()

        def add(value: str) -> None:
            candidate = str(value or "").strip()
            if not candidate:
                return
            key = _normalize_diacritic_text(candidate)
            if not key or key in seen_keys:
                return
            seen_keys.add(key)
            ordered.append(candidate)

        # 1. Pending row (rất cao xác suất).
        try:
            pending_key, _at, _rev = self._valid_voice_pending_snapshot(roster_revision=roster_revision)
        except Exception:
            pending_key = None
        with self._score_data_lock:
            if pending_key:
                pending_row = self._score_rows_by_key.get(pending_key)
                if pending_row and pending_row.student_name:
                    add(pending_row.student_name)
                    parts = pending_row.student_name.split()
                    if len(parts) >= 2:
                        add(" ".join(parts[-2:]))
            # 2. LRU tên dùng gần đây.
            for name in list(self._voice_recent_names):
                add(name)
                parts = name.split()
                if len(parts) >= 2:
                    add(" ".join(parts[-2:]))
            # 3. Tên còn READY (chưa pending) — ưu tiên những row chưa có pending_score
            ready_rows = [
                row
                for row in self._score_rows_by_key.values()
                if not row.pending_score and not row.current_score
            ]
            for row in ready_rows:
                if len(ordered) >= VOICE_HINT_SMART_LIMIT - 8:
                    break
                add(row.student_name)
            # 4. Nếu còn slot, thêm tên có current_score nhưng chưa pending (vẫn có thể được sửa)
            for row in self._score_rows_by_key.values():
                if len(ordered) >= VOICE_HINT_SMART_LIMIT - 8:
                    break
                if row.pending_score:
                    continue
                add(row.student_name)

        # 5. Bộ token điểm cố định (cần để Google bắt đúng số chữ).
        for token in (
            "không", "một", "hai", "ba", "bốn", "tư",
            "năm", "sáu", "bảy", "tám", "chín", "mười",
            "phẩy", "rưỡi", "điểm",
        ):
            if len(ordered) >= VOICE_HINT_SMART_LIMIT:
                break
            add(token)
        return ordered[:VOICE_HINT_SMART_LIMIT]

    def _ensure_voice_recognize_executor(self) -> ThreadPoolExecutor:
        """Returns a 2-worker pool for parallel Google Speech requests."""
        with self._voice_executor_lock:
            executor = self._voice_recognize_executor
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="VoiceRecog",
                )
                self._voice_recognize_executor = executor
            return executor

    def _shutdown_voice_recognize_executor(self) -> None:
        with self._voice_executor_lock:
            executor = self._voice_recognize_executor
            self._voice_recognize_executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # Python <3.9 chưa có cancel_futures
                executor.shutdown(wait=False)
            except Exception:
                pass

    def _require_voice_ready(self) -> None:
        if np is None or sd is None:
            raise RuntimeError("Thiếu thư viện audio. Cần cài: pip install sounddevice numpy")
        if sr is None:
            raise RuntimeError("Thiếu speech_recognition. Cần cài: pip install SpeechRecognition")
        if not self._score_rows_by_key:
            raise RuntimeError("Chưa có danh sách học sinh. Hãy chọn cột điểm đích trước.")

    def _voice_roster_revision(self, roster_revision: int | None = None) -> int:
        """Returns the roster revision that one voice operation must stay bound to."""
        return self._ptt_roster_revision if roster_revision is None else roster_revision

    def _clear_voice_pending(self) -> None:
        """Clears the name-only voice pending state under its own lock."""
        with self._voice_state_lock:
            self._voice_pending_row_key = None
            self._voice_pending_at = 0.0
            self._voice_pending_roster_revision = 0

    def _clear_voice_pending_if_current(self, row_key: str, pending_at: float, roster_revision: int) -> None:
        """Clears pending state only if another voice pass has not replaced it."""
        with self._voice_state_lock:
            if (
                self._voice_pending_row_key == row_key
                and self._voice_pending_at == pending_at
                and self._voice_pending_roster_revision == roster_revision
            ):
                self._voice_pending_row_key = None
                self._voice_pending_at = 0.0
                self._voice_pending_roster_revision = 0

    def _set_voice_pending(self, row_key: str, roster_revision: int | None = None) -> None:
        """Stores one name-only voice match for the matching roster revision."""
        with self._voice_state_lock:
            self._voice_pending_row_key = row_key
            self._voice_pending_at = time.perf_counter()
            self._voice_pending_roster_revision = self._voice_roster_revision(roster_revision)
            # BUG #2 FIX: Pending row mới → tied list cũ không còn áp dụng được.
            #   Tránh trường hợp user nói "Trâm" (tied), rồi nói "An" (pending An),
            #   rồi nói "một 8" — sẽ pick từ tied cũ thay vì điểm 1 cho An.
            self._voice_tied_candidates = []
            self._voice_tied_at = 0.0
            self._voice_tied_revision = 0

    def _valid_voice_pending_snapshot(self, roster_revision: int | None = None) -> tuple[str | None, float, int]:
        """Returns a pending voice row only when it belongs to the active roster revision."""
        expected_revision = self._voice_roster_revision(roster_revision)
        now = time.perf_counter()
        with self._voice_state_lock:
            pending_key = self._voice_pending_row_key
            pending_at = self._voice_pending_at
            pending_revision = self._voice_pending_roster_revision
            if not pending_key:
                return None, 0.0, expected_revision
            if pending_revision != expected_revision or (now - pending_at) > VOICE_PENDING_TIMEOUT:
                self._voice_pending_row_key = None
                self._voice_pending_at = 0.0
                self._voice_pending_roster_revision = 0
                return None, 0.0, expected_revision
            return pending_key, pending_at, pending_revision

    # ------------------------------------------------------------------
    # KHMER #B — Voice picker state for tied phonetic candidates
    # ------------------------------------------------------------------

    def _clear_voice_tied_candidates(self) -> None:
        """Resets the tied-candidates picker state."""
        with self._voice_state_lock:
            self._voice_tied_candidates = []
            self._voice_tied_at = 0.0
            self._voice_tied_revision = 0

    def _set_voice_tied_candidates(
        self,
        row_keys: list[str],
        roster_revision: int | None = None,
    ) -> None:
        """Stores a tied-candidates list for the next picker command.

        Khi `_match_student` thấy ≥ 2 row cùng phonetic không phân biệt được,
        gọi hàm này để PTT lệnh tiếp theo (vd "một"/"hai" + điểm) chọn được
        đúng row. Mỗi lần ghi đè list cũ để tránh state cũ tồn đọng.
        """
        cleaned_keys = [str(k).strip() for k in row_keys if str(k).strip()]
        if not cleaned_keys:
            return
        with self._voice_state_lock:
            self._voice_tied_candidates = list(cleaned_keys)
            self._voice_tied_at = time.perf_counter()
            self._voice_tied_revision = self._voice_roster_revision(roster_revision)
            # Tied list active → clear pending row để tránh xung đột.
            self._voice_pending_row_key = None
            self._voice_pending_at = 0.0
            self._voice_pending_roster_revision = 0

    def _valid_voice_tied_snapshot(
        self,
        roster_revision: int | None = None,
    ) -> tuple[list[str], float, int]:
        """Returns the active tied-candidates list (or empty if expired/stale)."""
        expected_revision = self._voice_roster_revision(roster_revision)
        now = time.perf_counter()
        with self._voice_state_lock:
            if not self._voice_tied_candidates:
                return [], 0.0, expected_revision
            if (
                self._voice_tied_revision != expected_revision
                or (now - self._voice_tied_at) > VOICE_PENDING_TIMEOUT
            ):
                self._voice_tied_candidates = []
                self._voice_tied_at = 0.0
                self._voice_tied_revision = 0
                return [], 0.0, expected_revision
            return list(self._voice_tied_candidates), self._voice_tied_at, self._voice_tied_revision

    # ---- Beep sound helpers ----

    def _play_beep_sequence(self, pattern: Sequence[tuple[int, int, int]], event_name: str) -> None:
        """Plays one named audio feedback pattern without blocking the UI thread."""
        if winsound is None:
            return

        def _run_sequence() -> None:
            try:
                for frequency, duration_ms, pause_ms in pattern:
                    if frequency > 0 and duration_ms > 0:
                        winsound.Beep(frequency, duration_ms)
                    if pause_ms > 0:
                        time.sleep(pause_ms / 1000.0)
            except Exception as error:  # noqa: BLE001 - fallback for Windows audio devices
                try:
                    winsound.MessageBeep()
                except Exception as fallback_error:  # noqa: BLE001
                    if not getattr(self, "_beep_failure_logged", False):
                        self._beep_failure_logged = True
                        self._log(
                            f"Không phát được âm báo {event_name}: {error}; fallback: {fallback_error}",
                            tag=LogTag.WARNING,
                        )

        threading.Thread(target=_run_sequence, daemon=True, name=f"VnEduBeep-{event_name}").start()

    def _play_beep(self, frequency: int = 1000, duration_ms: int = 100) -> None:
        """Phát tiếng beep không chặn GUI thread (chạy trong daemon thread)."""
        self._play_beep_sequence(((frequency, duration_ms, 0),), "beep")

    def _flash_recording_feedback(self) -> None:
        """Shows recording start visually without injecting sound into the microphone."""
        if not hasattr(self, "btn_ptt_toggle"):
            return
        def restore_button_color() -> None:
            if not self._ptt_enabled or not hasattr(self, "btn_ptt_toggle"):
                return
            try:
                self.btn_ptt_toggle.config(bg=APP_SUCCESS, activebackground="#15803d")
                self.btn_ptt_toggle._hover_prev_bg = APP_SUCCESS  # type: ignore[attr-defined]
            except tk.TclError:
                pass

        try:
            self.btn_ptt_toggle.config(bg=APP_DANGER, activebackground="#b91c1c")
            self.btn_ptt_toggle._hover_prev_bg = APP_DANGER  # type: ignore[attr-defined]
            self.root.after(140, restore_button_color)
        except tk.TclError:
            pass

    def _beep_recording_start(self) -> None:
        """Start-recording feedback: visual only, no audio contamination."""
        self._flash_recording_feedback()

    def _beep_uncertain_result(self) -> None:
        """Low tone for a processed transcript that is not safe to auto-apply."""
        self._play_beep_sequence(((620, 90, 0),), "chưa chắc")

    def _beep_needs_confirmation(self) -> None:
        """Distinct two-tone prompt for name-only/conflict confirmation states."""
        self._play_beep_sequence(((880, 70, 50), (660, 110, 0)), "cần xác nhận")

    def _beep_voice_error(self) -> None:
        """Warning tone for audio/STT errors."""
        self._play_beep_sequence(((420, 150, 70), (420, 150, 0)), "lỗi voice")

    def _beep_score_accepted(self) -> None:
        """Tiếng beep xác nhận đã ghi điểm vào hàng chờ."""
        self._play_beep_sequence(((1000, 70, 45), (1400, 90, 0)), "ghi thành công")

    def _bind_ptt_root_event(self, key: str, sequence: str, callback: Callable[[tk.Event], object]) -> None:
        """Binds one PTT-owned root event once and remembers its Tk funcid."""
        if key in self._ptt_bind_ids:
            return
        try:
            funcid = self.root.bind(sequence, callback, add="+")
        except tk.TclError:
            return
        if funcid:
            self._ptt_bind_ids[key] = (sequence, funcid)

    def _unbind_ptt_root_event(self, key: str) -> None:
        """Unbinds only the PTT-owned callback for one root event."""
        binding = self._ptt_bind_ids.pop(key, None)
        if binding is None:
            return
        sequence, funcid = binding
        try:
            self.root.unbind(sequence, funcid)
        except tk.TclError:
            pass

    def _bind_ptt_space_bindings(self) -> None:
        """Enables Space press/release handlers owned by PTT."""
        self._bind_ptt_root_event("space_press", "<KeyPress-space>", self._on_space_press)
        self._bind_ptt_root_event("space_release", "<KeyRelease-space>", self._on_space_release)

    def _unbind_ptt_space_bindings(self) -> None:
        """Disables only PTT Space handlers without touching other shortcuts."""
        self._unbind_ptt_root_event("space_press")
        self._unbind_ptt_root_event("space_release")

    def _bind_ptt_keyboard(self) -> None:
        """Enables all keyboard handlers owned by the PTT mode."""
        self._bind_ptt_space_bindings()
        self._bind_ptt_root_event("focus_out", "<FocusOut>", self._on_root_focus_out)

    def _unbind_ptt_keyboard(self) -> None:
        """Disables all keyboard handlers owned by the PTT mode."""
        self._unbind_ptt_space_bindings()
        self._unbind_ptt_root_event("focus_out")

    def toggle_ptt(self) -> None:
        if self._ptt_enabled:
            self._disable_ptt()
            return
        try:
            self._require_voice_ready()
            self._init_recognizer()
            self._ensure_ptt_capture_stream()
            self._ensure_ptt_worker()
            # PERF #2: bật session HTTP keep-alive + prewarm TLS handshake ngay khi
            # user bật bộ đàm (không phải đợi đến request đầu tiên).
            _voice_http_session()
            self._ensure_voice_recognize_executor()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Không bật được bộ đàm", str(error))
            self._log(f"Lỗi bộ đàm: {error}")
            return
        self._build_voice_hints()
        self._ptt_enabled = True
        self._space_pressed = False
        self.btn_ptt_toggle.config(text="🎤 BỘ ĐÀM: BẬT", bg=APP_SUCCESS, activebackground="#15803d")
        self.btn_ptt_toggle._hover_prev_bg = APP_SUCCESS  # type: ignore[attr-defined]  # đồng bộ hover
        self.voice_status_var.set("⏸️ Sẵn sàng. Giữ Space để nói, thả ra để xử lý.")
        self._bind_ptt_keyboard()
        self._render_voice_meter()
        self._focus_preview_tree()
        self._log("Bộ đàm đã bật.")

    def _disable_ptt(self) -> None:
        self._ptt_enabled = False
        self._space_pressed = False
        self._ptt_recording = False
        self._ptt_audio_buffer = None
        self._clear_voice_pending()
        self._clear_voice_tied_candidates()  # KHMER #B
        self._unbind_ptt_keyboard()
        if self._ptt_capture is not None:
            self._ptt_capture.stop()
            self._ptt_capture = None
        if hasattr(self, "btn_ptt_toggle"):
            try:
                self.btn_ptt_toggle.config(text="🎤 BỘ ĐÀM: TẮT", bg="#64748b", activebackground="#475569")
                self.btn_ptt_toggle._hover_prev_bg = "#64748b"  # type: ignore[attr-defined]  # đồng bộ hover
            except tk.TclError:
                pass
        if hasattr(self, "voice_status_var"):
            try:
                self.voice_status_var.set("⏸️ Bộ đàm đang tắt")
            except tk.TclError:
                pass
        self._reset_voice_meter()

    def _on_root_focus_out(self, _event: tk.Event | None = None) -> None:
        """BUG-02 FIX: Reset Space key state khi cửa sổ mất focus.

        Khi user Alt+Tab hoặc click ra ngoài trong lúc giữ Space,
        KeyRelease-space không fire → _space_pressed kẹt True vĩnh viễn.
        Handler này đảm bảo trạng thái được reset.
        """
        if self._space_pressed:
            self._space_pressed = False
            self._stop_ptt_recording()

    def _on_escape_cancel_recording(self, _event: tk.Event | None = None) -> str:
        """IMP-B2: Hủy recording khi nhấn Escape trong lúc đang giữ Space.

        Nếu đang recording (Space pressed), hủy ngay và không xử lý audio.
        Nếu không đang recording, không làm gì (để Escape hoạt động bình thường).
        """
        if self._space_pressed and self._ptt_recording:
            self._space_pressed = False
            # Hủy recording mà không xử lý audio
            with self._ptt_lock:
                if self._ptt_recording:
                    self._ptt_recording = False
            try:
                if self._ptt_capture is not None:
                    self._ptt_capture.end_recording()
            except Exception:
                pass
            self._ptt_audio_buffer = None
            self._render_voice_meter()
            self.voice_status_var.set("⏹️ Đã hủy ghi âm.")
            self._log("Hủy ghi âm bằng Escape.", tag=LogTag.WARNING)
            return "break"
        return ""

    def _show_help_dialog(self, _event: tk.Event | None = None) -> str:
        """IMP-D6: Hiện dialog tổng hợp phím tắt và hướng dẫn sử dụng."""
        help_text = (
            "╔═══════════════════════════════════════════╗\n"
            "║          PHÍM TẮT & HƯỚNG DẪN            ║\n"
            "╠═══════════════════════════════════════════╣\n"
            "║                                           ║\n"
            "║  🎤 Giọng nói (PTT):                     ║\n"
            "║  • Space (giữ)   : Ghi âm giọng nói      ║\n"
            "║  • Space (thả)   : Xử lý nhận dạng       ║\n"
            "║  • Escape         : Hủy ghi âm đang giữ  ║\n"
            "║                                           ║\n"
            "║  📝 Bảng điểm:                            ║\n"
            "║  • Double-click   : Sửa điểm chờ         ║\n"
            "║  • Delete         : Xóa điểm chờ         ║\n"
            "║  • Tab / Shift+Tab: Nhảy row khi sửa     ║\n"
            "║  • Enter          : Xác nhận sửa          ║\n"
            "║  • Ctrl+Z         : Undo lần cuối         ║\n"
            "║  • Ctrl+S         : Ghi điểm lên web      ║\n"
            "║                                           ║\n"
            "║  🔢 Khi trùng điểm (1-5):                 ║\n"
            "║  • 1: Thay thế bằng giá trị mới           ║\n"
            "║  • 2: Giữ nguyên điểm cũ                  ║\n"
            "║  • 3: Lấy trung bình                      ║\n"
            "║  • 4: Cộng thêm 1 điểm                    ║\n"
            "║  • 5: Dồn điểm (tối đa 10)                ║\n"
            "║                                           ║\n"
            "║  ⌨️ Khác:                                  ║\n"
            "║  • F1             : Hiện dialog này        ║\n"
            "╚═══════════════════════════════════════════╝"
        )
        messagebox.showinfo("Hướng dẫn sử dụng", help_text, parent=self.root)
        return "break"

    def _init_recognizer(self) -> None:
        if self._recognizer is not None:
            return
        if sr is None:
            raise RuntimeError("Thiếu speech_recognition.")
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = False
        recognizer.pause_threshold = 0.5
        self._recognizer = recognizer
        try:
            signature = inspect.signature(recognizer.recognize_google)
            self._recognizer_phrase_list_supported = "phrase_list" in signature.parameters
        except (TypeError, ValueError):
            self._recognizer_phrase_list_supported = False

    def _ensure_ptt_capture_stream(self) -> None:
        if self._ptt_capture is None:
            self._ptt_capture = PTTCaptureStream()
        if not self._ptt_capture.start():
            raise RuntimeError("Không thể mở stream micro cho bộ đàm.")

    def _ensure_ptt_worker(self) -> None:
        if self._ptt_worker is not None and self._ptt_worker.is_alive():
            return
        self._ptt_worker_shutdown = False
        self._ptt_worker = threading.Thread(
            target=self._ptt_worker_loop,
            daemon=True,
            name="VnEduPTTWorker",
        )
        self._ptt_worker.start()

    def _stop_ptt_worker(self) -> None:
        self._ptt_worker_shutdown = True
        try:
            self._ptt_queue.put_nowait(None)
        except Exception:
            pass

    def _ptt_worker_loop(self) -> None:
        while not self._ptt_worker_shutdown:
            task = self._ptt_queue.get()
            if task is None:
                continue
            request_id, roster_revision, audio_data, started_at = task
            # BUG-09 FIX: Wrap trong try-except để worker thread không chết khi gặp exception bất ngờ
            try:
                self._process_ptt_audio_task(request_id, roster_revision, audio_data, started_at)
            except Exception as worker_err:
                print(f"[DEBUG] PTT worker error (request {request_id}): {type(worker_err).__name__}: {worker_err}")
                try:
                    self._dispatch_ui_callback(
                        lambda: (
                            self._beep_voice_error(),
                            self.voice_status_var.set("⚠️ Lỗi xử lý giọng nói. Thử lại."),
                        )
                    )
                except Exception:
                    pass

    def _on_space_press(self, _event: tk.Event) -> str | None:
        if not self._ptt_enabled or self._space_pressed or self._busy:
            return "break"
        focused = self.root.focus_get()
        if _is_text_input_focus(focused):
            return None
        self._space_pressed = self._start_ptt_recording()
        return "break"

    def _on_space_release(self, _event: tk.Event) -> str | None:
        if not self._space_pressed:
            return None if _is_text_input_focus(self.root.focus_get()) else "break"
        self._space_pressed = False
        self._stop_ptt_recording()
        return "break"

    def _start_ptt_recording(self) -> bool:
        if self._ptt_capture is None or self._recognizer is None:
            return False
        if not self._ptt_lock.acquire(blocking=False):
            return False
        try:
            if self._ptt_recording:
                return False
            self._ptt_audio_buffer = None
            try:
                started = self._ptt_capture.begin_recording()
            except Exception:
                started = False
            if not started:
                # IMP-C2: Đếm lỗi mic liên tiếp, tự tắt PTT sau MIC_CONSECUTIVE_ERROR_MAX lần
                self._mic_consecutive_errors += 1
                if self._mic_consecutive_errors >= MIC_CONSECUTIVE_ERROR_MAX:
                    self.voice_status_var.set(f"❌ Mic lỗi {self._mic_consecutive_errors} lần liên tiếp — PTT tạm tắt.")
                    self._beep_voice_error()
                    self._render_voice_meter()
                    self._mic_consecutive_errors = 0
                    # Tắt PTT trên main thread (gọi qua after vì đang có thể ở worker)
                    try:
                        self.root.after(0, self._disable_ptt)
                    except Exception:
                        pass
                    return False
                self._beep_voice_error()
                self.voice_status_var.set(f"⚠️ Không thể bắt đầu ghi âm (lần {self._mic_consecutive_errors}/{MIC_CONSECUTIVE_ERROR_MAX}).")
                self._render_voice_meter()
                return False
            self._mic_consecutive_errors = 0  # IMP-C2: Reset đếm lỗi khi ghi âm thành công
            self._ptt_recording = True
            self._beep_recording_start()  # beep báo hiệu bắt đầu thu âm
            pending_key, _pending_at, _pending_revision = self._valid_voice_pending_snapshot()
            with self._score_data_lock:
                pending_row = self._score_rows_by_key.get(pending_key) if pending_key else None
            if pending_row:
                self.voice_status_var.set(f"🔴 Đang nghe điểm cho {pending_row.student_name}...")
            else:
                self.voice_status_var.set("🔴 Đang nghe... thả Space để xử lý")
            self._render_voice_meter()
            return True
        finally:
            self._ptt_lock.release()

    def _stop_ptt_recording(self) -> None:
        # BUG-14 FIX: Acquire lock để đồng bộ với _start_ptt_recording và audio callback
        with self._ptt_lock:
            if not self._ptt_recording:
                return
            self._ptt_recording = False
        self.voice_status_var.set("⏳ Đang xử lý giọng nói...")
        self._render_voice_meter()
        try:
            if self._ptt_capture is not None:
                self._ptt_audio_buffer = self._ptt_capture.end_recording()
        except Exception:
            self._ptt_audio_buffer = None
        if self._ptt_audio_buffer is None:
            self._beep_voice_error()
            self.voice_status_var.set("⚠️ Không ghi được âm thanh.")
            return
        self._ptt_request_seq += 1
        self._ptt_latest_request_id = self._ptt_request_seq
        self._ptt_queue.put((self._ptt_request_seq, self._ptt_roster_revision, self._ptt_audio_buffer, time.perf_counter()))

    def _process_ptt_audio(self) -> None:
        if self._ptt_audio_buffer is None:
            return
        self._ptt_request_seq += 1
        self._ptt_latest_request_id = self._ptt_request_seq
        self._process_ptt_audio_task(self._ptt_request_seq, self._ptt_roster_revision, self._ptt_audio_buffer, time.perf_counter())

    def _trim_ptt_audio(self, audio_float: Any) -> Any:
        if np is None or audio_float is None:
            return audio_float
        if getattr(audio_float, "size", 0) <= 0:
            return audio_float
        max_amp = float(np.max(np.abs(audio_float)))
        if max_amp <= 0.0:
            return audio_float
        trim_threshold = max(0.0035, max_amp * 0.12)
        active_indices = np.flatnonzero(np.abs(audio_float) >= trim_threshold)
        if active_indices.size == 0:
            return audio_float
        lead_margin = int((VOICE_TRIM_LEAD_MARGIN_MS / 1000.0) * AUDIO_SAMPLE_RATE)
        tail_margin = int((VOICE_TRIM_TAIL_MARGIN_MS / 1000.0) * AUDIO_SAMPLE_RATE)
        start_index = max(0, int(active_indices[0]) - lead_margin)
        end_index = min(len(audio_float), int(active_indices[-1]) + tail_margin + 1)
        trimmed = audio_float[start_index:end_index]
        if len(trimmed) / AUDIO_SAMPLE_RATE < VOICE_TRIM_MIN_DURATION:
            return audio_float
        return trimmed

    def _enhance_audio_for_recognition(self, audio: Any, *, boost_db: float = 0.0) -> Any:
        """Chuẩn hóa biên độ tín hiệu trước khi gửi Google Speech API.

        THIẾT KẾ (V1–V3 FIX): Google Speech nhận raw waveform và tự trích đặc
        trưng âm học, nên KHÔNG nên áp pre-emphasis / noise-gate / soft-clip mặc
        định. Các bước đó làm méo phổ, xóa phụ âm yếu (s, x, th, ph, h, âm cuối)
        và bóp dynamic range → giảm độ chính xác với nguyên âm, thanh điệu và
        đuôi từ tiếng Việt (đúng loại lỗi gây nhầm tên).

        - Mặc định (boost_db=0): CHỈ peak-normalize nhẹ để mức âm nhất quán,
          giữ nguyên hình dạng sóng tự nhiên mà model Google kỳ vọng.
        - Khi boost_db>0 (tín hiệu yếu): normalize → +gain → tanh soft-clip để
          tránh vỡ tiếng. tanh chỉ áp khi thực sự có nguy cơ clip do khuếch đại.

        Args:
            audio: numpy float32 array mono.
            boost_db: tăng gain (dB) cho lần thử tín hiệu yếu. Mặc định 0.

        Returns:
            numpy float32 array đã chuẩn hóa.
        """
        if np is None or audio is None or getattr(audio, "size", 0) == 0:
            return audio

        enhanced = audio.astype(np.float32, copy=True)

        # Peak normalization — đưa đỉnh tín hiệu về mức chuẩn, giữ hình dạng sóng.
        peak = float(np.max(np.abs(enhanced)))
        if peak > 1e-5:
            enhanced = enhanced * (VOICE_NORMALIZE_TARGET_PEAK / peak)

        # Chỉ khi cần khuếch đại (tín hiệu yếu): +gain rồi tanh soft-clip chống vỡ tiếng.
        if boost_db > 0.0:
            linear_gain = 10.0 ** (boost_db / 20.0)
            enhanced = np.tanh(enhanced * linear_gain)

        return enhanced.astype(np.float32)

    def _audio_to_speech_obj(self, audio_float: Any) -> Any:
        """
        Chuyển đổi audio float32 → sr.AudioData cho Google Speech API.

        Args:
            audio_float: numpy float32 array (giá trị trong khoảng [-1, 1]).

        Returns:
            sr.AudioData object sẵn sàng cho recognize_google().
        """
        audio_int16 = (audio_float * 32767).astype(np.int16)
        return sr.AudioData(audio_int16.tobytes(), AUDIO_SAMPLE_RATE, sample_width=2)

    def _audio_to_recognition_payload(self, audio_float: Any) -> tuple[Any, bytes | None, int]:
        """Chuyển đổi audio float32 → (sr.AudioData, FLAC bytes, sample_rate).

        PERF #3: pre-encode FLAC bằng libsndfile (~5–15ms) thay vì để
        `audio_obj.get_flac_data()` spawn `flac.exe` (~70–120ms) trong hot path.
        Trả `flac_bytes=None` khi `soundfile` không có hoặc encode lỗi — caller
        rớt về path cũ tự nhiên.
        """
        audio_int16 = (audio_float * 32767).astype(np.int16)
        audio_data = sr.AudioData(audio_int16.tobytes(), AUDIO_SAMPLE_RATE, sample_width=2)
        flac_bytes = _voice_encode_flac_fast(audio_int16, AUDIO_SAMPLE_RATE)
        return audio_data, flac_bytes, AUDIO_SAMPLE_RATE

    def _is_ptt_result_current(self, request_id: int, roster_revision: int) -> bool:
        return (
            self._ptt_enabled
            and not self._busy
            and not self._voice_conflict_dialog_open
            and request_id == self._ptt_latest_request_id
            and roster_revision == self._ptt_roster_revision
        )

    def _dispatch_ptt_result(self, request_id: int, roster_revision: int, callback: Callable[[], None]) -> None:
        def guarded_callback() -> None:
            if not self._is_ptt_result_current(request_id, roster_revision):
                return
            callback()

        self._dispatch_ui_callback(guarded_callback)

    def _process_ptt_audio_task(self, request_id: int, roster_revision: int, audio_data: Any, _started_at: float) -> None:
        if np is None or sr is None or self._recognizer is None:
            self._dispatch_ptt_result(
                request_id,
                roster_revision,
                lambda: (self._beep_voice_error(), self.voice_status_var.set("❌ Thiếu thư viện nhận dạng.")),
            )
            return
        try:
            audio_float = audio_data.flatten().astype(np.float32)
            duration = len(audio_float) / AUDIO_SAMPLE_RATE
            if duration < 0.25:
                # IMP-C1: Hiện thời lượng cụ thể để user biết cần giữ lâu hơn bao nhiêu
                dur_ms = int(duration * 1000)
                self._dispatch_ptt_result(
                    request_id,
                    roster_revision,
                    lambda _d=dur_ms: (
                        self._beep_voice_error(),
                        self.voice_status_var.set(f"⚠️ Quá ngắn ({_d}ms). Hãy giữ Space ≥ 250ms."),
                    ),
                )
                return
            max_amp = float(np.max(np.abs(audio_float)))
            if max_amp < 0.003:
                # IMP-C1: Hiện cường độ tín hiệu cụ thể để user biết mức hiện tại
                db_val = round(20 * math.log10(max_amp + 1e-10), 1)
                self._dispatch_ptt_result(
                    request_id,
                    roster_revision,
                    lambda _db=db_val: (
                        self._beep_voice_error(),
                        self.voice_status_var.set(f"⚠️ Tín hiệu quá yếu ({_db}dB). Nói gần micro hơn."),
                    ),
                )
                return
            trimmed_audio = self._trim_ptt_audio(audio_float)

            # PERF #5: chỉ dùng "boosted" khi tín hiệu yếu thật sự; dùng "raw" còn lại.
            #          → cắt 1 attempt cho 80% case bình thường.
            primary_audio = self._enhance_audio_for_recognition(trimmed_audio)
            if max_amp < VOICE_BOOST_FORCE_MAX_AMP:
                # Tín hiệu cực yếu — boost luôn cho secondary (hơn raw).
                secondary_audio = self._enhance_audio_for_recognition(
                    trimmed_audio,
                    boost_db=VOICE_BOOST_RETRY_GAIN_DB,
                )
                secondary_label = "boosted"
            elif max_amp < VOICE_BOOST_TRIGGER_MAX_AMP:
                # Tín hiệu hơi yếu — vẫn ưu tiên raw, để boost làm "tertiary" sau.
                secondary_audio = trimmed_audio
                secondary_label = "raw"
            else:
                secondary_audio = trimmed_audio
                secondary_label = "raw"

            # PERF #4: smart hints chỉ chứa context hiện tại (≤ 30 phrase) — Google trả top-1 chuẩn hơn.
            smart_hints = self._build_smart_voice_hints(roster_revision=roster_revision) if (
                self._recognizer_phrase_list_supported is not False
            ) else None

            # PERF #3: pre-encode FLAC một lần (libsndfile in-process) — bỏ qua flac.exe.
            primary_audio_obj, primary_flac, primary_sr = self._audio_to_recognition_payload(primary_audio)
            if secondary_audio is primary_audio:
                secondary_audio_obj = primary_audio_obj
                secondary_flac = primary_flac
                secondary_sr = primary_sr
            else:
                secondary_audio_obj, secondary_flac, secondary_sr = self._audio_to_recognition_payload(secondary_audio)

            # PERF #1: chạy song song 2 attempt (primary có hint + secondary không hint).
            #          Future nào về trước có match đủ tốt thì cancel cái còn lại.
            executor = self._ensure_voice_recognize_executor()

            def attempt_primary() -> tuple[VoiceMatchResult | None, str, str]:
                match, text = self._recognize_transcripts(
                    primary_audio_obj,
                    roster_revision=roster_revision,
                    phrase_hints=smart_hints,
                    allow_no_hint_retry=False,
                    flac_bytes=primary_flac,
                    sample_rate=primary_sr,
                )
                return match, text, "enhanced+hint"

            def attempt_secondary() -> tuple[VoiceMatchResult | None, str, str]:
                match, text = self._recognize_transcripts(
                    secondary_audio_obj,
                    roster_revision=roster_revision,
                    phrase_hints=None,
                    allow_no_hint_retry=False,
                    flac_bytes=secondary_flac,
                    sample_rate=secondary_sr,
                )
                return match, text, secondary_label

            primary_future = executor.submit(attempt_primary)
            secondary_future = executor.submit(attempt_secondary)
            futures = {primary_future, secondary_future}

            best_match: VoiceMatchResult | None = None
            best_text = ""
            best_attempt_label = ""
            best_rank = -1
            primary_done = False
            secondary_done = False
            primary_error: BaseException | None = None
            secondary_error: BaseException | None = None
            remaining_timeout = VOICE_RECOGNIZE_PARALLEL_TIMEOUT
            wait_started_at = time.perf_counter()
            while futures:
                done, _pending = futures_wait(
                    futures,
                    timeout=max(0.05, remaining_timeout),
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    # Hết thời gian — huỷ phần còn lại để không treo PTT worker.
                    for fut in futures:
                        fut.cancel()
                    break
                for fut in done:
                    futures.discard(fut)
                    try:
                        attempt_match, attempt_text, attempt_label = fut.result()
                    except Exception as recog_error:  # noqa: BLE001
                        if fut is primary_future:
                            primary_error = recog_error
                            primary_done = True
                        else:
                            secondary_error = recog_error
                            secondary_done = True
                        continue
                    if fut is primary_future:
                        primary_done = True
                    else:
                        secondary_done = True
                    if attempt_match is not None:
                        rank = self._voice_match_rank(attempt_match, attempt_text)
                        if rank > best_rank:
                            best_match = attempt_match
                            best_text = attempt_text
                            best_attempt_label = attempt_label
                            best_rank = rank
                        # Match đủ chắc chắn → huỷ tiếp, không cần chờ kết quả còn lại.
                        if attempt_match.match_score >= VOICE_FAST_ACCEPT_SCORE:
                            for other in futures:
                                other.cancel()
                            futures.clear()
                            break
                    elif attempt_text and not best_text:
                        best_text = attempt_text
                        best_attempt_label = attempt_label
                if not futures:
                    break
                remaining_timeout = VOICE_RECOGNIZE_PARALLEL_TIMEOUT - (time.perf_counter() - wait_started_at)
                if remaining_timeout <= 0.05:
                    for fut in futures:
                        fut.cancel()
                    break

            # Nếu cả hai attempt cùng raise (cùng do mạng), surface lỗi để user biết.
            if best_match is None and not best_text and primary_done and secondary_done and primary_error and secondary_error:
                raise primary_error

            # Nếu chỉ 1 attempt raise (mạng chập), log để dễ chẩn đoán nhưng không fail vội.
            if primary_error is not None and secondary_error is None:
                self._log(f"PTT primary error: {primary_error}", tag=LogTag.WARNING)
            elif secondary_error is not None and primary_error is None:
                self._log(f"PTT secondary error: {secondary_error}", tag=LogTag.WARNING)

            if best_match is not None and best_attempt_label not in ("enhanced+hint", "enhanced"):
                self._log(f"PTT secondary {best_attempt_label}: nhận dạng thành công.", tag=LogTag.INFO)

            # PERF #5 (tertiary): chỉ chạy thêm "boosted" khi cả 2 attempt cùng fail và
            # tín hiệu KHÔNG quá yếu (đã không boost ở primary). Tránh phí 1 RTT trong
            # 80% case. Khi tín hiệu cực yếu thì boosted đã làm secondary rồi.
            if (
                best_match is None
                and not best_text
                and VOICE_BOOST_FORCE_MAX_AMP <= max_amp < VOICE_BOOST_TRIGGER_MAX_AMP
            ):
                tertiary_audio = self._enhance_audio_for_recognition(
                    trimmed_audio,
                    boost_db=VOICE_BOOST_RETRY_GAIN_DB,
                )
                tertiary_audio_obj, tertiary_flac, tertiary_sr = self._audio_to_recognition_payload(tertiary_audio)
                try:
                    tert_match, tert_text = self._recognize_transcripts(
                        tertiary_audio_obj,
                        roster_revision=roster_revision,
                        phrase_hints=None,
                        allow_no_hint_retry=False,
                        flac_bytes=tertiary_flac,
                        sample_rate=tertiary_sr,
                    )
                except Exception:  # noqa: BLE001
                    tert_match, tert_text = None, ""
                if tert_match is not None:
                    best_match = tert_match
                    best_text = tert_text
                    best_attempt_label = "boosted"
                    self._log("PTT tertiary boosted: nhận dạng thành công.", tag=LogTag.INFO)
                elif tert_text and not best_text:
                    best_text = tert_text
                    best_attempt_label = "boosted"

            # PERF #1b: fallback recognize không show_all để có ít nhất 1 transcript text
            # khi cả 2 attempt parallel không trả alternative nào. Chỉ chạy khi thật sự cần
            # (tránh +1 RTT cho case match thành công ở trên).
            if best_match is None and not best_text:
                try:
                    fallback_text = self._recognize_google_fallback_text(
                        primary_audio_obj,
                        flac_bytes=primary_flac,
                        sample_rate=primary_sr,
                    )
                except Exception:  # noqa: BLE001
                    fallback_text = ""
                if fallback_text:
                    fallback_match, _msg = self._resolve_voice_command(
                        fallback_text,
                        roster_revision=roster_revision,
                        dry_run=True,  # BUG #10 FIX: probe trước, commit sau khi xác nhận thắng
                    )
                    if fallback_match is not None:
                        best_match = fallback_match
                    best_text = fallback_text
                    best_attempt_label = "fallback_text"

            if best_match is None and best_text:
                undo_cleaned = self._preprocess_voice_text(best_text)
                if _VOICE_UNDO_PATTERN.search(undo_cleaned):
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda: (self._on_undo_shortcut(), self.voice_status_var.set("↩️ Đã undo lệnh trước.")),
                    )
                    return
            if best_match is None:
                # BUG #10 FIX: Probe pipeline chạy `dry_run=True` nên tied/pending
                # state KHÔNG được set. Nếu best_text non-empty và transcript
                # thắng có khả năng gây tied/name_only, re-resolve với
                # dry_run=False để commit state cho UI status đúng.
                if best_text:
                    try:
                        self._resolve_voice_command(
                            best_text,
                            roster_revision=roster_revision,
                            dry_run=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                # KHMER #B: tied list active sau khi resolve fail → hướng dẫn
                # user nói "một"/"hai" để chọn. Ưu tiên trên cả pending/no-match
                # message vì state này yêu cầu input rất cụ thể.
                tied_keys, _at, _rev = self._valid_voice_tied_snapshot(
                    roster_revision=roster_revision
                )
                if tied_keys:
                    with self._score_data_lock:
                        tied_names = [
                            self._score_rows_by_key[k].student_name
                            for k in tied_keys
                            if k in self._score_rows_by_key
                        ]
                    if tied_names:
                        labelled = "  •  ".join(
                            f"{i + 1}. {name}" for i, name in enumerate(tied_names[:5])
                        )
                        suggestion_token = "/".join(
                            ["một", "hai", "ba", "bốn", "năm"][: len(tied_names)]
                        )
                        self._dispatch_ptt_result(
                            request_id,
                            roster_revision,
                            lambda lbl=labelled, sug=suggestion_token, names=list(tied_names): (
                                self._beep_needs_confirmation(),
                                self.voice_status_var.set(
                                    f"🔢 Trùng tên: {lbl} — nói '{sug}' rồi điểm."
                                ),
                                self._log(
                                    f"PTT TIED ({len(names)} HS): {', '.join(names)}",
                                    tag=LogTag.WARNING,
                                ),
                            ),
                        )
                        return
                pending_key, _pending_at, _pending_revision = self._valid_voice_pending_snapshot(
                    roster_revision=roster_revision
                )
                # THREAD-SAFETY: đọc _score_rows_by_key từ PTT worker — cần lock
                with self._score_data_lock:
                    pending_row = self._score_rows_by_key.get(pending_key) if pending_key else None
                if pending_row is not None:
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda name=pending_row.student_name, key=pending_key: (
                            self._beep_needs_confirmation(),
                            self.voice_status_var.set(f"🎯 Nghe '{name}' — nói tiếp điểm số..."),
                            self._log(f"PTT name-only: '{name}'", tag=LogTag.WARNING),
                            self._highlight_pending_row(key),
                        ),
                    )
                elif best_text:
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda text=best_text: (
                            self._beep_uncertain_result(),
                            self.voice_status_var.set(f"⚠️ Nghe '{text}' nhưng chưa ghép được học sinh."),
                            self._log(f"PTT không match: '{text}'", tag=LogTag.WARNING),
                        ),
                    )
                else:
                    self._dispatch_ptt_result(
                        request_id,
                        roster_revision,
                        lambda: (
                            self._beep_voice_error(),
                            self.voice_status_var.set("⚠️ Không nhận dạng được. Hãy thử lại."),
                        ),
                    )
                return
            # BUG #10 FIX: Trước khi commit best_match qua UI, re-resolve transcript
            # thắng với dry_run=False để side-effect state (clear pending/tied,
            # cập nhật cache) đúng với transcript được apply. Tránh trường hợp
            # transcript probe sau cùng overwrite state, hoặc state không được
            # commit khi transcript thắng nằm giữa list alternative.
            if best_text:
                try:
                    self._resolve_voice_command(
                        best_text,
                        roster_revision=roster_revision,
                        dry_run=False,
                    )
                except Exception:  # noqa: BLE001
                    pass
            self._dispatch_ptt_result(request_id, roster_revision, lambda match=best_match: self._apply_voice_match(match))
        except Exception as error:  # noqa: BLE001
            self._dispatch_ptt_result(
                request_id,
                roster_revision,
                lambda err=error: (
                    self._beep_voice_error(),
                    self.voice_status_var.set(f"❌ Lỗi xử lý giọng nói: {err}"),
                ),
            )

    def _recognize_google_candidates(
        self,
        audio_obj: Any,
        phrase_hints: list[str] | None = None,
        *,
        flac_bytes: bytes | None = None,
        sample_rate: int | None = None,
    ) -> list[tuple[str, float]]:
        transcripts: list[tuple[str, float]] = []
        raw: Any = None
        hint_list = list(phrase_hints or [])
        # PERF #2: ưu tiên gọi qua HTTPS session keep-alive (nếu có `requests`).
        # PERF #3: truyền `flac_bytes` đã encode sẵn để bỏ qua flac.exe.
        # Nếu hint không được hỗ trợ thì gọi không hint, sr.UnknownValueError → trả [].
        try:
            if hint_list and self._recognizer_phrase_list_supported is not False:
                try:
                    raw = _voice_recognize_google_session(
                        self._recognizer,
                        audio_obj,
                        language="vi-VN",
                        show_all=True,
                        phrase_hints=hint_list,
                    )
                    self._recognizer_phrase_list_supported = True
                except TypeError:
                    self._recognizer_phrase_list_supported = False
                    raw = _voice_recognize_google_session(
                        self._recognizer,
                        audio_obj,
                        language="vi-VN",
                        show_all=True,
                        flac_bytes=flac_bytes,
                        sample_rate_override=sample_rate,
                    )
            else:
                raw = _voice_recognize_google_session(
                    self._recognizer,
                    audio_obj,
                    language="vi-VN",
                    show_all=True,
                    flac_bytes=flac_bytes,
                    sample_rate_override=sample_rate,
                )
        except sr.UnknownValueError:
            return transcripts
        except sr.RequestError as error:
            raise RuntimeError(f"Lỗi kết nối Google: {error}") from error

        if isinstance(raw, dict):
            alternatives = raw.get("alternative", [])
            if not alternatives:
                for result_entry in raw.get("result", []):
                    if isinstance(result_entry, dict):
                        alternatives = result_entry.get("alternative", [])
                        if alternatives:
                            break
            candidate_entries: list[tuple[str, float, int]] = []
            for index, alternative in enumerate(alternatives[:6]):
                transcript = str(alternative.get("transcript", "")).strip()
                if not transcript:
                    continue
                confidence_value = alternative.get("confidence", 0.0)
                try:
                    confidence = float(confidence_value)
                except (TypeError, ValueError):
                    confidence = 0.0
                candidate_entries.append((transcript, confidence, index))
            candidate_entries.sort(key=lambda item: (item[1], -item[2]), reverse=True)
            seen_transcripts: set[str] = set()
            for transcript, confidence, _index in candidate_entries:
                if transcript in seen_transcripts:
                    continue
                seen_transcripts.add(transcript)
                transcripts.append((transcript, confidence))
        elif isinstance(raw, str) and raw.strip():
            transcripts.append((raw.strip(), 0.0))
        return transcripts

    def _recognize_google_fallback_text(
        self,
        audio_obj: Any,
        *,
        flac_bytes: bytes | None = None,
        sample_rate: int | None = None,
    ) -> str:
        try:
            fallback = _voice_recognize_google_session(
                self._recognizer,
                audio_obj,
                language="vi-VN",
                show_all=False,
                flac_bytes=flac_bytes,
                sample_rate_override=sample_rate,
            )
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as error:
            raise RuntimeError(f"Lỗi kết nối Google: {error}") from error
        except Exception as error:
            raise RuntimeError(f"Lỗi Google fallback: {error}") from error
        return fallback.strip() if isinstance(fallback, str) else ""

    def _voice_match_rank(self, match: VoiceMatchResult, transcript: str, confidence: float = 0.0) -> int:
        normalized_length = len(_normalize_diacritic_text(transcript))
        confidence_bonus = int(max(0.0, min(1.0, confidence)) * 100)
        return (match.match_score * 1000) + (confidence_bonus * 10) + normalized_length

    def _best_voice_match_from_transcripts(
        self,
        transcripts: list[object],
        *,
        stop_score: int = VOICE_FAST_ACCEPT_SCORE,
        roster_revision: int | None = None,
    ) -> tuple[VoiceMatchResult | None, str]:
        # V6 FIX: `transcripts` đã được sort theo thứ tự ưu tiên của Google
        # (confidence desc → index asc), nên ứng viên ĐẦU TIÊN khớp được là phán
        # đoán âm học tốt nhất. Một ứng viên xếp sau chỉ được phép qua mặt khi
        # điểm fuzzy cao hơn ít nhất VOICE_TRANSCRIPT_OVERRIDE_MARGIN, hoặc khi nó
        # có confidence cao hơn rõ rệt mà điểm fuzzy không thấp hơn. Điều này
        # tránh để nhiễu fuzzy 1 điểm lật ngược thứ tự của Google.
        best_match: VoiceMatchResult | None = None
        best_match_score = -1
        best_confidence = -1.0
        best_text = ""
        if transcripts:
            first_candidate = transcripts[0]
            if isinstance(first_candidate, tuple):
                best_text = str(first_candidate[0]).strip()
            else:
                best_text = str(first_candidate).strip()
        for candidate in transcripts:
            if isinstance(candidate, tuple):
                transcript = str(candidate[0]).strip()
                try:
                    confidence = float(candidate[1])
                except (TypeError, ValueError):
                    confidence = 0.0
            else:
                transcript = str(candidate).strip()
                confidence = 0.0
            if not transcript:
                continue
            match_result, _message = self._resolve_voice_command(
                transcript,
                roster_revision=roster_revision,
                dry_run=True,  # BUG #10 FIX: probe mode — không commit state khi xếp hạng
            )
            if match_result is None:
                continue
            should_override = (
                best_match is None
                or (match_result.match_score - best_match_score) >= VOICE_TRANSCRIPT_OVERRIDE_MARGIN
                or (confidence > best_confidence + 1e-6 and match_result.match_score >= best_match_score)
            )
            if should_override:
                best_match = match_result
                best_match_score = match_result.match_score
                best_confidence = confidence
                best_text = transcript
            if best_match is not None and best_match.match_score >= stop_score:
                break
        return best_match, best_text

    def _recognize_transcripts(
        self,
        audio_obj: Any,
        *,
        roster_revision: int | None = None,
        phrase_hints: list[str] | None = None,
        allow_no_hint_retry: bool = True,
        flac_bytes: bytes | None = None,
        sample_rate: int | None = None,
    ) -> tuple[VoiceMatchResult | None, str]:
        """Runs one Google Speech request and matches a student command.

        Args:
            audio_obj: sr.AudioData đã encode (FLAC/LINEAR16) sẵn sàng gửi.
            roster_revision: chống stale matching khi roster đã đổi.
            phrase_hints: danh sách hint ưu tiên cho lần đầu. Khi None, đoán theo
                bối cảnh hiện tại bằng `_build_smart_voice_hints`.
            allow_no_hint_retry: nếu True (legacy), khi attempt-có-hint fail thì
                thử lại không hint trong CÙNG hàm. Pipeline parallel mới đặt False
                vì vòng song song bên ngoài đã đảm trách attempt no-hint.
            flac_bytes / sample_rate: PERF #3 — FLAC bytes đã pre-encode bằng
                libsndfile, kèm sample rate gốc. Khi truyền vào, session helper
                bỏ qua flac.exe của sr (~70–120ms/clip).
        """
        attempt_specs: list[list[str] | None] = []
        # Lần 1: hint (ưu tiên smart hints).
        if self._recognizer_phrase_list_supported is not False:
            if phrase_hints is None:
                resolved_hints = self._build_smart_voice_hints(roster_revision=roster_revision)
            else:
                resolved_hints = list(phrase_hints)
            if resolved_hints:
                attempt_specs.append(resolved_hints)
        # Lần 2 (legacy): no-hint retry, chỉ chạy khi caller cho phép.
        if allow_no_hint_retry or not attempt_specs:
            attempt_specs.append(None)

        best_match: VoiceMatchResult | None = None
        best_text = ""
        best_rank = -1
        for hint_list in attempt_specs:
            transcripts = self._recognize_google_candidates(
                audio_obj,
                phrase_hints=hint_list,
                flac_bytes=flac_bytes,
                sample_rate=sample_rate,
            )
            if not transcripts:
                continue
            attempt_match, attempt_text = self._best_voice_match_from_transcripts(
                transcripts,
                roster_revision=roster_revision,
            )
            if attempt_match is not None:
                attempt_rank = self._voice_match_rank(attempt_match, attempt_text)
                if attempt_rank > best_rank:
                    best_rank = attempt_rank
                    best_match = attempt_match
                    best_text = attempt_text
                if attempt_match.match_score >= VOICE_FAST_ACCEPT_SCORE:
                    return attempt_match, attempt_text
            elif not best_text:
                first_candidate = transcripts[0]
                if isinstance(first_candidate, tuple):
                    best_text = str(first_candidate[0]).strip()
                else:
                    best_text = str(first_candidate).strip()

        if (
            allow_no_hint_retry
            and best_match is None
            and not best_text
        ):
            fallback_text = self._recognize_google_fallback_text(
                audio_obj,
                flac_bytes=flac_bytes,
                sample_rate=sample_rate,
            )
            if fallback_text:
                fallback_match, _message = self._resolve_voice_command(
                    fallback_text,
                    roster_revision=roster_revision,
                )
                if fallback_match is not None:
                    return fallback_match, fallback_text
                if not best_text:
                    best_text = fallback_text
        return best_match, best_text

    def _preprocess_voice_text(self, text: str) -> str:
        cleaned = str(text or "").lower().strip()
        cleaned = VOICE_FILLER_PATTERN.sub(" ", cleaned)
        cleaned = cleaned.replace(":", " ").replace(";", " ")
        cleaned = VOICE_SCORE_DECIMAL_PATTERN.sub(r"\1.\2", cleaned)
        cleaned = cleaned.replace(",", " ")
        cleaned = VOICE_SCORE_WORD_PATTERN.sub(" diem ", cleaned)
        cleaned = VOICE_COMMAND_SPACE_PATTERN.sub(" ", cleaned).strip()
        return cleaned

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

    def on_show_summary(self) -> None:
        """Hiển thị tóm tắt ngữ cảnh sổ điểm hiện tại."""
        try:
            summary = self._summary()
        except Exception as error:  # noqa: BLE001
            messagebox.showwarning("Chưa có ngữ cảnh", str(error))
            self._log(f"Không thể hiện tóm tắt: {error}")
            return
        messagebox.showinfo("Tóm tắt ngữ cảnh", summary)
        self._log("Đã hiển thị tóm tắt ngữ cảnh hiện tại.")

    def on_copy_summary(self) -> None:
        try:
            summary = self._summary()
        except Exception as error:  # noqa: BLE001
            messagebox.showwarning("Chưa có ngữ cảnh", str(error))
            self._log(f"Không thể sao chép tóm tắt: {error}")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(summary)
        self.root.update_idletasks()
        self._log("Đã sao chép tóm tắt ngữ cảnh vào clipboard.")
        self._set_progress(self._progress_value, "Đã sao chép tóm tắt ngữ cảnh")

    def on_export_snapshot(self) -> None:
        try:
            context = self._require_context()
            payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "summary": self._summary(),
                "context": asdict(context),
                "pending_rows": [
                    {
                        "row_index": row.row_index,
                        "student_code": row.student_code,
                        "student_name": row.student_name,
                        "current_score": row.current_score,
                        "pending_score": row.pending_score,
                        "recognized_text": row.recognized_text,
                        "match_score": row.match_score,
                        "status": row.status,
                    }
                    for row in self._score_rows_by_key.values()
                    if row.pending_score
                ],
            }
            _write_json_atomic_file(SNAPSHOT_FILE, payload)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi xuất snapshot", str(error))
            self._log(f"Lỗi xuất snapshot: {error}")
            return
        self._log(f"Đã xuất snapshot ngữ cảnh vào {SNAPSHOT_FILE}")
        self._set_progress(self._progress_value, "Đã xuất snapshot JSON")
        messagebox.showinfo("Đã xuất", f"Đã ghi snapshot vào {SNAPSHOT_FILE}")

    def on_export_feature_payload(self) -> None:
        try:
            context = self._require_context()
            payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "feature_name": self.feature_name_var.get().strip() or "PTT nhập điểm",
                "selected_context": {
                    "grade_text": self.grade_var.get(),
                    "class_text": self.class_var.get(),
                    "subject_text": self.subject_var.get(),
                    "term_text": self.term_var.get(),
                    "grade_id": self._selected_id(self.grade_var, self.grade_label_to_id) or context.selected_grade_id,
                    "class_id": self._selected_id(self.class_var, self.class_label_to_id) or context.selected_class_id,
                    "subject_id": self._selected_id(self.subject_var, self.subject_label_to_id) or context.selected_subject_id,
                    "term_id": self._selected_id(self.term_var, self.term_label_to_id) or context.selected_term_id,
                    "target_score_column_key": self._selected_target_score_key(),
                    "target_score_column_text": self.target_score_column_var.get(),
                },
                "window_title": context.window_title,
                "teacher_text": context.teacher_text,
                "permission_text": context.permission_text,
                "column_count": len(context.column_schemas),
                "enabled_comment_input_count": context.enabled_comment_input_count,
                "scanned_student_count": len(self._score_rows_by_key),
                "pending_score_count": sum(1 for row in self._score_rows_by_key.values() if row.pending_score),
            }
            _write_json_atomic_file(PAYLOAD_FILE, payload)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi xuất payload", str(error))
            self._log(f"Lỗi xuất payload: {error}")
            return
        self._log(f"Đã xuất payload tác vụ vào {PAYLOAD_FILE}")
        self._set_progress(self._progress_value, "Đã xuất payload tác vụ")
        messagebox.showinfo("Đã xuất", f"Đã ghi payload vào {PAYLOAD_FILE}")


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


def main() -> None:
    """Entry point chính cho ứng dụng nhập điểm VNEDU.

    IMP-C4: Hỗ trợ CLI arguments (--debug, --config-file).
    IMP-C5: Bọc mainloop trong try-except để ghi crash report.
    """
    import argparse
    parser = argparse.ArgumentParser(description="VNEDU Score Entry Tool")
    parser.add_argument("--debug", action="store_true", help="Bật chế độ debug (hiện log chi tiết)")
    parser.add_argument("--config-file", type=str, default=None, help="Đường dẫn file config thay thế")
    parser.add_argument("--self-test", action="store_true", help="Chạy kiểm tra nhanh logic lõi rồi thoát")
    args = parser.parse_args()

    if args.self_test:
        _run_self_tests()
        return

    # IMP-C4: Ghi đè CONFIG_FILE nếu user chỉ định
    if args.config_file:
        global CONFIG_FILE
        CONFIG_FILE = Path(args.config_file)

    root = tk.Tk()
    style = ttk.Style(root)
    apply_app_styles(style)
    app = VnEduStandaloneApp(root)
    # IMP-C4: Bật debug mode nếu có flag
    if args.debug:
        app._log("🐛 Debug mode enabled via --debug flag.", tag=LogTag.INFO)
    # IMP-C5: Bọc mainloop trong try-except để ghi crash report
    try:
        root.mainloop()
    except Exception as fatal_err:
        crash_report_path = Path.home() / "Desktop" / "nhapdiem_crash_report.txt"
        try:
            import traceback
            with crash_report_path.open("w", encoding="utf-8") as crash_f:
                crash_f.write(f"CRASH REPORT — {datetime.now().isoformat()}\n")
                crash_f.write("=" * 60 + "\n")
                crash_f.write(traceback.format_exc())
            print(f"[FATAL] Crash report saved to: {crash_report_path}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()

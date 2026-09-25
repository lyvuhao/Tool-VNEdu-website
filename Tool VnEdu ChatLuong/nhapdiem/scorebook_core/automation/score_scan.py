"""Nhận diện cột điểm và quét dữ liệu điểm hiện có."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from playwright.sync_api import Page

from nhanxet.config import ProgressCallback
from nhanxet.models import ScorebookContext, ScoreColumnSchema
from nhanxet.progress import create_subprogress_reporter, emit_progress

from ..models import ScoreWriteEntry


class ScoreScanMixin:
    """Nhận diện cột điểm và quét dữ liệu điểm hiện có."""

    def _resolve_score_schema(
        self,
        schemas: List[ScoreColumnSchema],
        *,
        target_column_key: str = "",
        target_column_label: str = "",
    ) -> ScoreColumnSchema | None:
        """Resolves one score schema across contexts where the UI label is stable but the internal key may change."""
        schema = self._find_schema_by_key(schemas, target_column_key)
        if schema is not None:
            return schema

        normalized_label = str(target_column_label or "").strip()
        if not normalized_label:
            return None

        label_candidates = [normalized_label]
        suffix_match = re.match(r"^(.*?)\s+\([^)]+\)$", normalized_label)
        if suffix_match:
            base_label = suffix_match.group(1).strip()
            if base_label and base_label not in label_candidates:
                label_candidates.append(base_label)

        for label_candidate in label_candidates:
            matched_schema = next(
                (
                    item
                    for item in schemas
                    if (item.display_name.strip() or item.column_key) == label_candidate
                ),
                None,
            )
            if matched_schema is not None:
                return matched_schema
        return None

    def _extract_live_write_rows(
        self,
        page: Page,
        context: ScorebookContext,
        source_column_key: str,
        comment_column_key: str,
    ) -> List[Dict[str, object]]:
        """Extracts all visible student rows for internal analysis/write from the active scorebook table."""
        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)
        comment_schema = self._find_schema_by_key(context.column_schemas, comment_column_key)
        if source_schema is None:
            raise RuntimeError(f"Không tìm thấy schema cho cột điểm `{source_column_key}`.")
        if comment_schema is None:
            raise RuntimeError(f"Không tìm thấy schema cho cột nhận xét `{comment_column_key}`.")

        student_name_indices = [
            schema.leaf_index
            for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index)
            if schema.role_hint == "student_name"
        ]
        student_code_schema = next(
            (schema for schema in context.column_schemas if schema.role_hint == "student_code"),
            None,
        )
        student_code_index = student_code_schema.leaf_index if student_code_schema is not None else 1

        return list(
            page.evaluate(
                """({ sourceIndex, commentIndex, studentNameIndices, studentCodeIndex }) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                if (!root) return [];
                const table = root.querySelector('table.table.tablefix');
                if (!table) return [];

                const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const cellValue = (cell) => {
                    if (!cell) return '';
                    const input = cell.querySelector('input, textarea, select, span.input_diem, span.input_nhan_xet');
                    if (input) {
                        const tagName = String(input.tagName || '').toUpperCase();
                        if (tagName === 'SPAN') return normalizeText(input.textContent || input.innerText || '');
                        return normalizeText(input.value || '');
                    }
                    return normalizeText(cell.innerText || cell.textContent || '');
                };

                return Array.from(table.querySelectorAll('tbody tr')).map((tr, rowIndex) => {
                    const cells = Array.from(tr.children);
                    const scoreCell = cells[sourceIndex];
                    const commentCell = cells[commentIndex];
                    const commentInput = commentCell ? commentCell.querySelector('input, textarea, select, span.input_nhan_xet, span.input_diem') : null;
                    const nameParts = studentNameIndices
                        .map(index => normalizeText(cells[index]?.innerText || cells[index]?.textContent || ''))
                        .filter(Boolean);
                    let studentName = nameParts.join(' ');
                    if (!studentName) {
                        const fallback = cells
                            .slice(0, 6)
                            .map(cell => normalizeText(cell.innerText || cell.textContent || ''))
                            .find(text => text && !/^\\d+$/.test(text) && !/\\d{2}\\/\\d{2}\\/\\d{4}/.test(text));
                        studentName = fallback || '';
                    }
                    return {
                        rowIndex: rowIndex + 1,
                        rowId: tr.id || '',
                        studentCode: normalizeText(cells[studentCodeIndex]?.innerText || cells[studentCodeIndex]?.textContent || ''),
                        studentName,
                        sourceValue: cellValue(scoreCell),
                        currentComment: cellValue(commentCell),
                        commentInputName: commentInput ? (commentInput.name || commentInput.id || '') : '',
                    };
                });
            }""",
                {
                    "sourceIndex": source_schema.leaf_index,
                    "commentIndex": comment_schema.leaf_index,
                    "studentNameIndices": student_name_indices,
                    "studentCodeIndex": student_code_index,
                },
            )
        )

    def _extract_live_score_entries(
        self,
        page: Page,
        context: ScorebookContext,
        target_column_key: str,
        target_column_label: str = "",
    ) -> List[ScoreWriteEntry]:
        """Extracts all visible student rows for one target score column from the active scorebook table."""
        target_schema = self._resolve_score_schema(
            context.column_schemas,
            target_column_key=target_column_key,
            target_column_label=target_column_label,
        )
        if target_schema is None:
            requested_target = target_column_label.strip() or target_column_key
            raise RuntimeError(f"Không tìm thấy schema cho cột điểm `{requested_target}`.")

        student_name_indices = [
            schema.leaf_index
            for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index)
            if schema.role_hint == "student_name"
        ]
        student_code_schema = next(
            (schema for schema in context.column_schemas if schema.role_hint == "student_code"),
            None,
        )
        student_code_index = student_code_schema.leaf_index if student_code_schema is not None else 1

        live_rows = list(
            page.evaluate(
                """({ targetIndex, studentNameIndices, studentCodeIndex }) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                if (!root) return [];
                const table = root.querySelector('table.table.tablefix');
                if (!table) return [];

                const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const cellValue = (cell) => {
                    if (!cell) return '';
                    const input = cell.querySelector('input, textarea, select, span.input_diem, span.input_nhan_xet');
                    if (input) {
                        const tagName = String(input.tagName || '').toUpperCase();
                        if (tagName === 'SPAN') return normalizeText(input.textContent || input.innerText || '');
                        return normalizeText(input.value || '');
                    }
                    return normalizeText(cell.innerText || cell.textContent || '');
                };

                return Array.from(table.querySelectorAll('tbody tr')).map((tr, rowIndex) => {
                    const cells = Array.from(tr.children);
                    const targetCell = cells[targetIndex];
                    const targetInput = targetCell ? targetCell.querySelector('input, textarea, select, span.input_diem, span.input_nhan_xet') : null;
                    const nameParts = studentNameIndices
                        .map(index => normalizeText(cells[index]?.innerText || cells[index]?.textContent || ''))
                        .filter(Boolean);
                    let studentName = nameParts.join(' ');
                    if (!studentName) {
                        const fallback = cells
                            .slice(0, 6)
                            .map(cell => normalizeText(cell.innerText || cell.textContent || ''))
                            .find(text => text && !/^\\d+$/.test(text) && !/\\d{2}\\/\\d{2}\\/\\d{4}/.test(text));
                        studentName = fallback || '';
                    }
                    return {
                        rowIndex: rowIndex + 1,
                        rowId: tr.id || '',
                        studentCode: normalizeText(cells[studentCodeIndex]?.innerText || cells[studentCodeIndex]?.textContent || ''),
                        studentName,
                        currentScore: cellValue(targetCell),
                        targetInputName: targetInput ? (targetInput.name || targetInput.id || '') : '',
                    };
                });
            }""",
                {
                    "targetIndex": target_schema.leaf_index,
                    "studentNameIndices": student_name_indices,
                    "studentCodeIndex": student_code_index,
                },
            )
        )

        resolved_column_key = target_schema.column_key.strip() or target_column_key
        target_column_name = target_schema.display_name if target_schema.display_name.strip() else resolved_column_key
        score_entries: List[ScoreWriteEntry] = []
        for live_row in live_rows:
            target_input_name = str(live_row.get("targetInputName", "")).strip()
            score_entries.append(
                ScoreWriteEntry(
                    row_index=int(live_row.get("rowIndex", 0) or 0),
                    row_id=str(live_row.get("rowId", "")).strip(),
                    student_code=str(live_row.get("studentCode", "")).strip(),
                    student_name=str(live_row.get("studentName", "")).strip(),
                    target_column_key=resolved_column_key,
                    target_column_name=target_column_name,
                    current_score=str(live_row.get("currentScore", "")).strip(),
                    target_input_name=target_input_name,
                    status=("scanned" if target_input_name else "locked"),
                    reason=("" if target_input_name else "Ô điểm hiện không cho chỉnh sửa trên giao diện live."),
                )
            )
        return score_entries

    def _scan_score_entries_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        target_column_key: str,
        target_column_label: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, List[ScoreWriteEntry]]:
        """Builds one score-entry preview list from the already-selected live scorebook page."""
        emit_progress(progress_callback, 12.0, "Đang phân tích cấu trúc Sổ điểm cho cột điểm...")
        context = self._build_scorebook_context(snapshot)
        target_schema = self._resolve_score_schema(
            context.column_schemas,
            target_column_key=target_column_key,
            target_column_label=target_column_label,
        )
        if target_schema is None:
            requested_target = target_column_label.strip() or target_column_key
            raise RuntimeError(f"Không tìm thấy cột điểm `{requested_target}` trong ngữ cảnh hiện tại.")
        emit_progress(
            progress_callback,
            38.0,
            f"Đang quét học sinh cho cột {target_schema.display_name or target_schema.column_key or target_column_key}...",
        )
        entries = self._extract_live_score_entries(
            page,
            context,
            target_column_key=target_schema.column_key,
            target_column_label=target_column_label,
        )
        editable_count = sum(1 for entry in entries if entry.target_input_name.strip())
        emit_progress(
            progress_callback,
            100.0,
            f"Đã quét {len(entries)} học sinh, {editable_count} ô điểm khả dụng.",
        )
        return context, entries

    def scan_score_entries(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        target_column_key: str,
        target_column_label: str = "",
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, List[ScoreWriteEntry], str, str]:
        """Scans the live scorebook and returns student rows for one target score column."""
        with self._open_page() as page:
            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 0.0, 62.0),
            )
            context, entries = self._scan_score_entries_on_page(
                page,
                snapshot,
                target_column_key=target_column_key,
                target_column_label=target_column_label,
                progress_callback=create_subprogress_reporter(progress_callback, 62.0, 100.0),
            )
            return context, entries, login_message, selection_message

    def _open_selected_scorebook_snapshot_on_page(
        self,
        page: Page,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[Dict[str, object], str, str]:
        """Logs in if needed, opens the scorebook screen, and applies one target context on the current page."""
        emit_progress(progress_callback, 5.0, "Đang chuẩn bị phiên VNEDU để đọc dữ liệu...")
        login_message = self._login_if_needed_on_page(
            page,
            username=username,
            password=password,
            progress_callback=create_subprogress_reporter(progress_callback, 5.0, 28.0),
        )
        self._ensure_scorebook_screen(
            page,
            progress_callback=create_subprogress_reporter(progress_callback, 28.0, 42.0),
        )
        snapshot = self._wait_for_scorebook_snapshot(
            page,
            timeout_sec=8.0,
            progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),
            progress_message="Đang đọc dữ liệu khung Sổ điểm...",
        )
        if (
            not self._requested_scorebook_context_differs(
                snapshot,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
            )
            and self._scorebook_snapshot_ready_for_live_score_work(snapshot)
        ):
            emit_progress(progress_callback, 100.0, "Đang dùng lại đúng ngữ cảnh Sổ điểm hiện tại.")
            return snapshot, login_message, ""
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
            progress_callback=create_subprogress_reporter(progress_callback, 58.0, 70.0),
            progress_message="Đang đọc quyền và thông tin giáo viên...",
        )
        snapshot, selection_message = self._select_scorebook_context_on_page(
            page,
            snapshot,
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
            progress_callback=create_subprogress_reporter(progress_callback, 70.0, 100.0),
        )
        return snapshot, login_message, selection_message

    def _scorebook_snapshot_ready_for_live_score_work(self, snapshot: Dict[str, object]) -> bool:
        """Checks whether the current scorebook snapshot is already usable for score scan/apply."""
        return bool(
            str(snapshot.get("windowId", "")).strip()
            and int(snapshot.get("scoreTableRowCount", 0) or 0) > 0
            and list(snapshot.get("scoreTableHeaderRows", []))
            and list(snapshot.get("scoreTableBodyRows", []))
        )

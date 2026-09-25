"""Đọc bảng Sổ điểm và làm tươi option combobox."""

from __future__ import annotations

import time
from typing import Dict, List

from playwright.sync_api import Page

from nhanxet.config import ProgressCallback
from nhanxet.models import ScoreOption
from nhanxet.progress import emit_progress

from ..score_write import resolve_hydrated_score_options


class ScoreSnapshotMixin:
    """Đọc bảng Sổ điểm và làm tươi option combobox."""

    def _read_scorebook_table_snapshot(self, page: Page, window_id: str = "") -> Dict[str, object]:
        """Reads the visible score table structure from the active or specified scorebook window."""
        return dict(
            page.evaluate(
                """(windowId) => {
                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = (windowId && document.getElementById(windowId)) || active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) {
                    return {
                        headerRows: [],
                        bodyRows: [],
                        rowCount: 0,
                        tableClass: '',
                    };
                }

                const scoreTable = root.querySelector('table.table.tablefix');
                if (!scoreTable) {
                    return {
                        headerRows: [],
                        bodyRows: [],
                        rowCount: 0,
                        tableClass: '',
                    };
                }

                const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const readInput = (input) => {
                    if (!input) return null;
                    const tagName = String(input.tagName || '').toUpperCase();
                    const elementValue = tagName === 'SPAN'
                        ? (input.textContent || input.innerText || '')
                        : (input.value || '');
                    return {
                        tagName: input.tagName || '',
                        type: input.type || '',
                        id: input.id || '',
                        name: input.name || '',
                        className: input.className || '',
                        value: elementValue,
                        b: input.getAttribute('b') || '',
                        c: input.getAttribute('c') || '',
                        bc: input.getAttribute('bc') || '',
                        dd: input.getAttribute('dd') || '',
                    };
                };

                const headerRows = Array.from(scoreTable.querySelectorAll('thead tr')).map((tr, rowIndex) => ({
                    rowIndex,
                    cells: Array.from(tr.querySelectorAll('td, th')).map((cell, cellIndex) => ({
                        cellIndex,
                        text: normalizeText(cell.innerText || cell.textContent || ''),
                        className: cell.className || '',
                        colspan: parseInt(cell.getAttribute('colspan') || '1', 10) || 1,
                        rowspan: parseInt(cell.getAttribute('rowspan') || '1', 10) || 1,
                        cn: cell.getAttribute('cn') || '',
                        cl: cell.getAttribute('cl') || '',
                        b: cell.getAttribute('b') || '',
                        c: cell.getAttribute('c') || '',
                    })),
                }));

                const bodyRows = Array.from(scoreTable.querySelectorAll('tbody tr')).slice(0, 5).map((tr, rowIndex) => ({
                    rowIndex,
                    rowId: tr.id || '',
                    cells: Array.from(tr.children).map((cell, cellIndex) => ({
                        cellIndex,
                        tagName: cell.tagName || '',
                        text: normalizeText(cell.innerText || cell.textContent || ''),
                        className: cell.className || '',
                        dataLoai: cell.getAttribute('data-loai') || '',
                        dataCot: cell.getAttribute('data-cot') || '',
                        dataHang: cell.getAttribute('data-hang') || '',
                        inputInfo: readInput(cell.querySelector('input, textarea, select, span.input_diem, span.input_nhan_xet')),
                    })),
                }));

                return {
                    headerRows,
                    bodyRows,
                    rowCount: scoreTable.querySelectorAll('tbody tr').length,
                    tableClass: scoreTable.className || '',
                };
            }""",
                window_id.strip(),
            )
        )

    def _hydrate_scorebook_snapshot_options(
        self,
        page: Page,
        snapshot: Dict[str, object],
        *,
        include_grade: bool = True,
        include_class: bool = True,
        include_subject: bool = True,
        include_term: bool = True,
        only_when_incomplete: bool = False,
        merge_existing: bool = True,
        preserve_selected_if_missing: bool = True,
    ) -> Dict[str, object]:
        """Refreshes combo option payloads so lazily loaded stores are not mistaken for single-value lists."""
        updated_snapshot = dict(snapshot)
        combo_specs = []
        if include_grade:
            combo_specs.append(("gradeComboId", "gradeOptions", "currentGradeId", "currentGradeText", "hiddenGradeId"))
        if include_class:
            combo_specs.append(("classComboId", "classOptions", "currentClassId", "currentClassText", "hiddenClassId"))
        if include_subject:
            combo_specs.append(("subjectComboId", "subjectOptions", "currentSubjectId", "currentSubjectText", "hiddenSubjectId"))
        if include_term:
            combo_specs.append(("termComboId", "termOptions", "currentTermId", "currentTermText", "hiddenTermId"))

        for combo_id_key, options_key, current_key, current_text_key, hidden_key in combo_specs:
            combo_id = str(updated_snapshot.get(combo_id_key, "")).strip()
            if not combo_id:
                continue
            existing_options = self._build_options(list(updated_snapshot.get(options_key, [])))
            if only_when_incomplete and not self._snapshot_options_need_live_refresh(
                updated_snapshot,
                options=existing_options,
                current_key=current_key,
                hidden_key=hidden_key,
            ):
                continue
            live_options = self._build_options(self._load_live_combo_options(page, combo_id))
            merged_options = resolve_hydrated_score_options(
                existing_options,
                live_options,
                selected_id=self._effective_snapshot_selected_id(updated_snapshot, current_key, hidden_key),
                selected_text=str(updated_snapshot.get(current_text_key, "")).strip(),
                merge_existing=merge_existing,
                preserve_selected_if_missing=preserve_selected_if_missing,
            )
            updated_snapshot[options_key] = [
                {
                    "id": option.option_id,
                    "ten": option.option_text,
                }
                for option in merged_options
            ]

        return updated_snapshot

    def _snapshot_options_need_live_refresh(
        self,
        snapshot: Dict[str, object],
        *,
        options: List[ScoreOption],
        current_key: str,
        hidden_key: str,
    ) -> bool:
        """Returns whether one combo snapshot still looks incomplete enough to justify live expansion."""
        if not options:
            return True
        selected_id = self._effective_snapshot_selected_id(snapshot, current_key, hidden_key)
        if selected_id and all(option.option_id != selected_id for option in options):
            return True
        return len(options) <= 1

    def _snapshot_option_ids(self, snapshot: Dict[str, object], options_key: str) -> set[str]:
        """Returns the normalized option ids currently present in one snapshot store."""
        return {
            option.option_id
            for option in self._build_options(list(snapshot.get(options_key, [])))
            if option.option_id.strip()
        }

    def _snapshot_store_excludes_stale_option(
        self,
        snapshot: Dict[str, object],
        *,
        options_key: str,
        stale_option_id: str,
        actual_selected_id: str = "",
    ) -> bool:
        """Returns whether one combo store no longer looks tied to a stale parent selection."""
        normalized_stale_id = stale_option_id.strip()
        if not normalized_stale_id:
            return True
        if actual_selected_id.strip() and actual_selected_id.strip() != normalized_stale_id:
            return True
        return normalized_stale_id not in self._snapshot_option_ids(snapshot, options_key)

    def _wait_for_hydrated_scorebook_options(
        self,
        page: Page,
        snapshot: Dict[str, object],
        *,
        options_key: str,
        include_grade: bool = False,
        include_class: bool = False,
        include_subject: bool = False,
        include_term: bool = False,
        expected_grade_id: str | None = None,
        expected_class_id: str | None = None,
        stale_option_id: str = "",
        timeout_sec: float = 5.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang nạp lại dữ liệu combobox từ Sổ điểm...",
    ) -> Dict[str, object]:
        """Polls live combo expansion until one dependent option store is fresh enough to trust."""
        deadline = time.time() + timeout_sec
        latest_snapshot = dict(snapshot)
        while time.time() < deadline:
            latest_snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                latest_snapshot,
                include_grade=include_grade,
                include_class=include_class,
                include_subject=include_subject,
                include_term=include_term,
                merge_existing=False,
                preserve_selected_if_missing=False,
            )
            actual_grade_id = self._effective_snapshot_selected_id(latest_snapshot, "currentGradeId", "hiddenGradeId")
            actual_class_id = self._effective_snapshot_selected_id(latest_snapshot, "currentClassId", "hiddenClassId")
            option_ids = self._snapshot_option_ids(latest_snapshot, options_key)
            parent_ready = (
                (expected_grade_id is None or actual_grade_id == expected_grade_id)
                and (expected_class_id is None or actual_class_id == expected_class_id)
            )
            store_ready = bool(option_ids) and self._snapshot_store_excludes_stale_option(
                latest_snapshot,
                options_key=options_key,
                stale_option_id=stale_option_id,
                actual_selected_id=(actual_class_id if options_key == "classOptions" else ""),
            )
            if parent_ready and store_ready:
                emit_progress(progress_callback, 100.0, progress_message)
                return latest_snapshot
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(progress_callback, max(10.0, elapsed_ratio * 95.0), progress_message)
            page.wait_for_timeout(250)
            refreshed_snapshot, _snapshot_error = self._best_effort_scorebook_snapshot(page)
            if refreshed_snapshot is not None:
                latest_snapshot = refreshed_snapshot
        return latest_snapshot

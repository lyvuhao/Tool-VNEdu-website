"""Đọc snapshot cửa sổ Sổ điểm (combo, giá trị ẩn, bảng)."""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from playwright.sync_api import Error as PlaywrightError, Page

from ..access import ensure_selected_score_option, merge_score_options


class SnapshotMixin:
    """Đọc snapshot cửa sổ Sổ điểm (combo, giá trị ẩn, bảng)."""

    def _read_scorebook_window_shell(self, page: Page) -> Dict[str, object]:
        """Reads the active scorebook window identity, combo ids, and window-level badges."""
        return dict(
            page.evaluate(
                """() => {
                if (typeof Ext === 'undefined') {
                    return { ok: false, reason: 'ExtJS không tồn tại trên trang.' };
                }

                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) {
                    return { ok: false, reason: 'Không tìm thấy cửa sổ Sổ điểm đang hoạt động.' };
                }

                const labels = Array.from(root.querySelectorAll('label.x-form-item-label'));
                const findComboIdByLabel = (labelPart) => {
                    const label = labels.find(item => (item.innerText || '').toLowerCase().includes(labelPart));
                    const field = label ? label.closest('.x-field') : null;
                    return field ? (field.id || '') : '';
                };

                const teacherCell = root.querySelector('#gvbm');
                const permissionCell = Array.from(root.querySelectorAll('td'))
                    .find(td => /quyền hạn/i.test((td.innerText || '').trim()));
                const commentInputs = Array.from(root.querySelectorAll('input.input_nhan_xet'));

                return {
                    ok: true,
                    windowId: root.id || '',
                    windowTitle: ((root.querySelector('.x-window-header-text')?.innerText) || '').trim(),
                    gradeComboId: findComboIdByLabel('khối'),
                    classComboId: findComboIdByLabel('lớp'),
                    subjectComboId: findComboIdByLabel('môn'),
                    termComboId: findComboIdByLabel('học kỳ'),
                    teacherText: ((teacherCell?.innerText) || '').trim(),
                    permissionText: ((permissionCell?.innerText) || '').trim(),
                    commentInputCount: commentInputs.length,
                    enabledCommentInputCount: commentInputs.filter(input => !input.disabled && !input.readOnly).length,
                };
            }"""
            )
        )

    def _read_scorebook_combo_snapshot(self, page: Page, combo_id: str) -> Dict[str, object]:
        """Reads one scorebook combobox current value together with its current ExtJS store payload."""
        normalized_combo_id = combo_id.strip()
        if not normalized_combo_id:
            return {"id": "", "text": "", "options": []}

        return dict(
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') {
                    return { id: '', text: '', options: [] };
                }

                const readField = (record, fieldName) => {
                    if (!record) return '';
                    try {
                        if (record.get) {
                            const value = record.get(fieldName);
                            if (value !== undefined && value !== null) return value;
                        }
                    } catch (error) {}
                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {
                        return record.data[fieldName];
                    }
                    return '';
                };

                const collectStoreRecords = (source, bucket, visitedSources) => {
                    if (!source) return;
                    if (typeof source === 'object' || typeof source === 'function') {
                        if (visitedSources.has(source)) return;
                        visitedSources.add(source);
                    }
                    if (Array.isArray(source)) {
                        bucket.push(...source);
                        return;
                    }
                    if (typeof source.getRange === 'function') {
                        try {
                            const range = source.getRange();
                            if (Array.isArray(range)) {
                                bucket.push(...range);
                            }
                        } catch (error) {}
                    }
                    if (Array.isArray(source.items)) {
                        bucket.push(...source.items);
                    }
                    if (source.data) {
                        collectStoreRecords(source.data, bucket, visitedSources);
                    }
                    if (typeof source.getSource === 'function') {
                        try {
                            collectStoreRecords(source.getSource(), bucket, visitedSources);
                        } catch (error) {}
                    } else if (source.source) {
                        collectStoreRecords(source.source, bucket, visitedSources);
                    }
                };

                const readStoreRecords = (store) => {
                    if (!store) return [];
                    const bucket = [];
                    const visitedSources = new WeakSet();
                    collectStoreRecords(store.snapshot, bucket, visitedSources);
                    collectStoreRecords(store.data, bucket, visitedSources);
                    if (typeof store.getData === 'function') {
                        try {
                            collectStoreRecords(store.getData(), bucket, visitedSources);
                        } catch (error) {}
                    }
                    collectStoreRecords(store, bucket, visitedSources);

                    const deduped = [];
                    const seen = new Set();
                    for (const record of bucket) {
                        const recordId = String(readField(record, 'id')).trim();
                        const recordText = String(readField(record, 'ten') || readField(record, 'value')).trim();
                        const key = `${recordId}||${recordText}`;
                        if (!recordId && !recordText) continue;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        deduped.push(record);
                    }
                    return deduped;
                };

                const cmp = Ext.getCmp(comboId);
                if (!cmp) {
                    return { id: '', text: '', options: [] };
                }
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                const range = store ? readStoreRecords(store) : [];
                const currentValue = cmp.getValue ? cmp.getValue() : '';
                const currentText = cmp.getRawValue ? cmp.getRawValue() : '';
                const matchedRecord = range.find(record => {
                    const recordId = String(readField(record, 'id'));
                    const recordText = String(readField(record, 'ten') || readField(record, 'value'));
                    const normalizedValue = currentValue === undefined || currentValue === null ? '' : String(currentValue);
                    const normalizedText = currentText === undefined || currentText === null ? '' : String(currentText);
                    return recordId === normalizedValue || recordText === normalizedValue || recordText === normalizedText;
                });
                const normalizedId = matchedRecord
                    ? String(readField(matchedRecord, 'id'))
                    : (currentValue === undefined || currentValue === null ? '' : String(currentValue));
                const normalizedText = currentText === undefined || currentText === null ? '' : String(currentText);

                return {
                    id: normalizedId,
                    text: normalizedText || (matchedRecord ? String(readField(matchedRecord, 'ten') || readField(matchedRecord, 'value')) : ''),
                    options: range.map(record => ({
                        id: String(readField(record, 'id')),
                        ten: String(readField(record, 'ten') || readField(record, 'value')),
                    })),
                };
            }""",
                normalized_combo_id,
            )
        )

    def _read_scorebook_hidden_values(self, page: Page, window_id: str = "") -> Dict[str, str]:
        """Reads hidden scorebook form inputs from the active or specified scorebook window."""
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
                    return {};
                }

                const form = root.querySelector('form');
                const hiddenValues = {};
                if (!form) {
                    return hiddenValues;
                }
                const hiddenInputs = Array.from(form.querySelectorAll('input[type="hidden"]'));
                for (const input of hiddenInputs) {
                    const key = (input.name || input.id || '').trim();
                    if (!key) continue;
                    hiddenValues[key] = String(input.value || '');
                }
                return hiddenValues;
            }""",
                window_id.strip(),
            )
        )

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
                    return {
                        tagName: input.tagName || '',
                        type: input.type || '',
                        id: input.id || '',
                        name: input.name || '',
                        className: input.className || '',
                        value: input.value || '',
                        b: input.getAttribute('b') || '',
                        c: input.getAttribute('c') || '',
                        bc: input.getAttribute('bc') || '',
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
                        inputInfo: readInput(cell.querySelector('input, textarea, select')),
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

    def _scorebook_snapshot(self, page: Page) -> Dict[str, object]:
        """Reads scorebook combobox ids, current values, and ExtJS store items."""
        shell_snapshot = self._read_scorebook_window_shell(page)
        if not shell_snapshot.get("ok"):
            raise RuntimeError(str(shell_snapshot.get("reason", "Không đọc được cấu trúc Sổ điểm.")))

        window_id = str(shell_snapshot.get("windowId", "")).strip()
        hidden_values = self._read_scorebook_hidden_values(page, window_id=window_id)
        score_table_info = self._read_scorebook_table_snapshot(page, window_id=window_id)

        grade_combo_id = str(shell_snapshot.get("gradeComboId", "")).strip()
        class_combo_id = str(shell_snapshot.get("classComboId", "")).strip()
        subject_combo_id = str(shell_snapshot.get("subjectComboId", "")).strip()
        term_combo_id = str(shell_snapshot.get("termComboId", "")).strip()

        grade_snapshot = self._read_scorebook_combo_snapshot(page, grade_combo_id)
        class_snapshot = self._read_scorebook_combo_snapshot(page, class_combo_id)
        subject_snapshot = self._read_scorebook_combo_snapshot(page, subject_combo_id)
        term_snapshot = self._read_scorebook_combo_snapshot(page, term_combo_id)

        return {
            "ok": True,
            "windowId": window_id,
            "windowTitle": str(shell_snapshot.get("windowTitle", "")).strip(),
            "gradeComboId": grade_combo_id,
            "classComboId": class_combo_id,
            "subjectComboId": subject_combo_id,
            "termComboId": term_combo_id,
            "currentGradeId": str(grade_snapshot.get("id", "")).strip(),
            "currentGradeText": str(grade_snapshot.get("text", "")).strip(),
            "currentClassId": str(class_snapshot.get("id", "")).strip(),
            "currentClassText": str(class_snapshot.get("text", "")).strip(),
            "currentSubjectId": str(subject_snapshot.get("id", "")).strip(),
            "currentSubjectText": str(subject_snapshot.get("text", "")).strip(),
            "currentTermId": str(term_snapshot.get("id", "")).strip(),
            "currentTermText": str(term_snapshot.get("text", "")).strip(),
            "teacherText": str(shell_snapshot.get("teacherText", "")).strip(),
            "permissionText": str(shell_snapshot.get("permissionText", "")).strip(),
            "commentInputCount": int(shell_snapshot.get("commentInputCount", 0) or 0),
            "enabledCommentInputCount": int(shell_snapshot.get("enabledCommentInputCount", 0) or 0),
            "hiddenSchoolYear": str(hidden_values.get("iNamHoc", "")).strip(),
            "hiddenGradeId": str(hidden_values.get("iKhoi", "")).strip(),
            "hiddenClassId": str(hidden_values.get("iLopId", "")).strip(),
            "hiddenSubjectId": str(hidden_values.get("iMonHocId", "")).strip(),
            "hiddenTermId": str(hidden_values.get("iHocKy", "")).strip(),
            "scoreTableClass": str(score_table_info.get("tableClass", "")).strip(),
            "scoreTableRowCount": int(score_table_info.get("rowCount", 0) or 0),
            "scoreTableHeaderRows": list(score_table_info.get("headerRows", [])),
            "scoreTableBodyRows": list(score_table_info.get("bodyRows", [])),
            "gradeOptions": list(grade_snapshot.get("options", [])),
            "classOptions": list(class_snapshot.get("options", [])),
            "subjectOptions": list(subject_snapshot.get("options", [])),
            "termOptions": list(term_snapshot.get("options", [])),
        }

    def _read_combo_store_items(self, page: Page, combo_id: str) -> List[Dict[str, object]]:
        """Reads the current ExtJS store items for one scorebook combobox."""
        normalized_combo_id = combo_id.strip()
        if not normalized_combo_id:
            return []
        try:
            raw_items = page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return [];
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return [];
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                if (!store) return [];

                const readField = (record, fieldName) => {
                    if (!record) return '';
                    try {
                        if (record.get) {
                            const value = record.get(fieldName);
                            if (value !== undefined && value !== null) return value;
                        }
                    } catch (error) {}
                    if (record.data && record.data[fieldName] !== undefined && record.data[fieldName] !== null) {
                        return record.data[fieldName];
                    }
                    return '';
                };

                const collectStoreRecords = (source, bucket, visitedSources) => {
                    if (!source || typeof source !== 'object') {
                        return;
                    }
                    if (visitedSources.has(source)) {
                        return;
                    }
                    visitedSources.add(source);
                    if (typeof source.getRange === 'function') {
                        try {
                            const range = source.getRange();
                            if (Array.isArray(range)) {
                                bucket.push(...range);
                            }
                        } catch (error) {}
                    }
                    if (Array.isArray(source.items)) {
                        bucket.push(...source.items);
                    }
                    if (source.data) {
                        collectStoreRecords(source.data, bucket, visitedSources);
                    }
                    if (typeof source.getSource === 'function') {
                        try {
                            collectStoreRecords(source.getSource(), bucket, visitedSources);
                        } catch (error) {}
                    } else if (source.source) {
                        collectStoreRecords(source.source, bucket, visitedSources);
                    }
                };

                const bucket = [];
                const visitedSources = new WeakSet();
                collectStoreRecords(store.snapshot, bucket, visitedSources);
                collectStoreRecords(store.data, bucket, visitedSources);
                if (typeof store.getData === 'function') {
                    try {
                        collectStoreRecords(store.getData(), bucket, visitedSources);
                    } catch (error) {}
                }
                collectStoreRecords(store, bucket, visitedSources);

                const deduped = [];
                const seen = new Set();
                for (const record of bucket) {
                    const recordId = String(readField(record, 'id')).trim();
                    const recordText = String(readField(record, 'ten') || readField(record, 'value')).trim();
                    const key = `${recordId}||${recordText}`;
                    if (!recordId && !recordText) continue;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    deduped.push({
                        id: recordId,
                        ten: recordText,
                    });
                }
                return deduped;
            }""",
                normalized_combo_id,
            )
        except PlaywrightError:
            return []
        return list(raw_items or [])

    def _load_live_combo_options(self, page: Page, combo_id: str) -> List[Dict[str, object]]:
        """Expands one combo so dependent ExtJS stores can populate before the app reads them."""
        normalized_combo_id = combo_id.strip()
        if not normalized_combo_id:
            return []

        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try {
                    if (cmp.onTriggerClick) {
                        cmp.onTriggerClick();
                    } else if (cmp.expand) {
                        cmp.expand();
                    }
                    return true;
                } catch (error) {
                    return false;
                }
            }""",
                normalized_combo_id,
            )
        except PlaywrightError:
            return self._read_combo_store_items(page, normalized_combo_id)

        best_items = self._read_combo_store_items(page, normalized_combo_id)
        last_signature: Tuple[Tuple[str, str], ...] = tuple()
        stable_reads = 0
        deadline = time.time() + 2.5
        while time.time() < deadline:
            page.wait_for_timeout(180)
            current_items = self._read_combo_store_items(page, normalized_combo_id)
            if len(current_items) > len(best_items):
                best_items = current_items
            current_signature = tuple(
                (
                    str(item.get("id", "")).strip(),
                    str(item.get("ten", "")).strip(),
                )
                for item in current_items
            )
            if current_signature and current_signature == last_signature:
                stable_reads += 1
                if stable_reads >= 1:
                    if current_items:
                        best_items = current_items
                    break
            else:
                stable_reads = 0
            last_signature = current_signature

        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try {
                    if (cmp.collapse) cmp.collapse();
                    return true;
                } catch (error) {
                    return false;
                }
            }""",
                normalized_combo_id,
            )
        except PlaywrightError:
            pass

        return best_items

    def _hydrate_scorebook_snapshot_options(
        self,
        page: Page,
        snapshot: Dict[str, object],
        *,
        include_grade: bool = True,
        include_class: bool = True,
        include_subject: bool = True,
        include_term: bool = True,
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
            live_options = self._build_options(self._load_live_combo_options(page, combo_id))
            merged_options = ensure_selected_score_option(
                merge_score_options(existing_options, live_options),
                self._effective_snapshot_selected_id(updated_snapshot, current_key, hidden_key),
                str(updated_snapshot.get(current_text_key, "")).strip(),
            )
            updated_snapshot[options_key] = [
                {
                    "id": option.option_id,
                    "ten": option.option_text,
                }
                for option in merged_options
            ]

        return updated_snapshot

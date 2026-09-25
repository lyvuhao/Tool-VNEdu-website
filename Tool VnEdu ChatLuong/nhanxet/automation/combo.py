"""Chọn giá trị trong các combobox ExtJS của Sổ điểm."""

from __future__ import annotations

import re
from typing import Dict

from playwright.sync_api import Error as PlaywrightError, Page


class ComboMixin:
    """Chọn giá trị trong các combobox ExtJS của Sổ điểm."""

    def _read_combo_target_state(self, page: Page, combo_id: str, value_id: str) -> Dict[str, object]:
        """Reads the selectors and target text needed to select one ExtJS combobox option."""
        return dict(
            page.evaluate(
                """({ comboId, valueId }) => {
                if (typeof Ext === 'undefined') return { ok: false, reason: 'ExtJS không tồn tại trên trang.' };
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return { ok: false, reason: `Không tìm thấy combobox ${comboId}.` };
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                if (!store) return { ok: false, reason: `Combobox ${comboId} không có store.` };

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

                const readStoreRecords = (currentStore) => {
                    if (!currentStore) return [];
                    const bucket = [];
                    const visitedSources = new WeakSet();
                    collectStoreRecords(currentStore.snapshot, bucket, visitedSources);
                    collectStoreRecords(currentStore.data, bucket, visitedSources);
                    if (typeof currentStore.getData === 'function') {
                        try {
                            collectStoreRecords(currentStore.getData(), bucket, visitedSources);
                        } catch (error) {}
                    }
                    collectStoreRecords(currentStore, bucket, visitedSources);

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

                const range = readStoreRecords(store);
                const target = range.find(record => String(readField(record, 'id')) === valueId);
                if (!target) {
                    return { ok: false, reason: `Không tìm thấy option id=${valueId} trong combobox ${comboId}.` };
                }

                const field = document.getElementById(comboId);
                const trigger = field?.querySelector('.x-form-arrow-trigger, .x-form-trigger');
                const input = field?.querySelector('input.x-form-field, input[role="textbox"]');
                const targetText = String(readField(target, 'ten') || readField(target, 'value') || valueId).trim();
                const currentValue = cmp.getValue ? cmp.getValue() : '';
                const currentText = cmp.getRawValue ? cmp.getRawValue() : '';

                return {
                    ok: true,
                    comboId,
                    valueId,
                    targetText,
                    currentValue: currentValue === undefined || currentValue === null ? '' : String(currentValue),
                    currentText: currentText === undefined || currentText === null ? '' : String(currentText),
                    fieldSelector: field?.id ? `#${CSS.escape(field.id)}` : '',
                    triggerSelector: trigger?.id ? `#${CSS.escape(trigger.id)}` : '',
                    inputSelector: input?.id ? `#${CSS.escape(input.id)}` : '',
                };
            }""",
                {"comboId": combo_id, "valueId": value_id},
            )
        )

    def _select_combo_via_extjs_fallback(self, page: Page, combo_id: str, value_id: str) -> bool:
        """Falls back to direct ExtJS mutation when the real picker path is unavailable."""
        return bool(
            page.evaluate(
                """({ comboId, valueId }) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                const store = cmp.getStore ? cmp.getStore() : cmp.store;
                if (!store) return false;

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

                const readStoreRecords = (currentStore) => {
                    if (!currentStore) return [];
                    const bucket = [];
                    const visitedSources = new WeakSet();
                    collectStoreRecords(currentStore.snapshot, bucket, visitedSources);
                    collectStoreRecords(currentStore.data, bucket, visitedSources);
                    if (typeof currentStore.getData === 'function') {
                        try {
                            collectStoreRecords(currentStore.getData(), bucket, visitedSources);
                        } catch (error) {}
                    }
                    collectStoreRecords(currentStore, bucket, visitedSources);

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

                const target = readStoreRecords(store).find(record => String(readField(record, 'id')) === valueId);
                if (!target) return false;

                const targetText = String(readField(target, 'ten') || readField(target, 'value') || valueId).trim();
                try {
                    if (typeof store.clearFilter === 'function') {
                        store.clearFilter();
                    }
                } catch (error) {}
                try {
                    if (cmp.select) {
                        cmp.select(target, true);
                    }
                } catch (error) {}
                try {
                    if (cmp.setValue) {
                        cmp.setValue(valueId);
                    }
                } catch (error) {}
                try {
                    if (cmp.setRawValue) {
                        cmp.setRawValue(targetText);
                    }
                } catch (error) {}

                const field = document.getElementById(comboId);
                const input = (cmp.inputEl && cmp.inputEl.dom)
                    ? cmp.inputEl.dom
                    : field?.querySelector('input.x-form-field, input[role="textbox"]');

                if (input) {
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }

                try {
                    if (cmp.collapse) {
                        cmp.collapse();
                    }
                } catch (error) {}

                const currentValue = cmp.getValue ? cmp.getValue() : '';
                return String(currentValue === undefined || currentValue === null ? '' : currentValue) === valueId;
            }""",
                {"comboId": combo_id, "valueId": value_id},
            )
        )

    def _select_combo_via_picker_click(self, page: Page, combo_state: Dict[str, object]) -> bool:
        """Uses the visible ExtJS picker to select the target option when the trigger is available."""
        target_text = str(combo_state.get("targetText", "")).strip()
        if not target_text:
            return False

        item_pattern = re.compile(rf"^\s*{re.escape(target_text)}\s*$")
        trigger_selector = str(combo_state.get("triggerSelector", "")).strip()
        input_selector = str(combo_state.get("inputSelector", "")).strip()
        field_selector = str(combo_state.get("fieldSelector", "")).strip()

        trigger_locator = None
        if trigger_selector:
            trigger_locator = page.locator(trigger_selector)
        elif input_selector:
            trigger_locator = page.locator(input_selector)
        elif field_selector:
            trigger_locator = page.locator(field_selector)
        else:
            return False

        try:
            trigger_locator.click(timeout=2500)
        except PlaywrightError:
            try:
                trigger_locator.click(timeout=2500, force=True)
            except PlaywrightError:
                return False

        picker_items = page.locator(".x-boundlist.x-layer:visible .x-boundlist-item")
        try:
            picker_items.first.wait_for(state="visible", timeout=2500)
        except PlaywrightError:
            return False

        target_item = picker_items.filter(has_text=item_pattern).first
        try:
            target_item.wait_for(state="visible", timeout=2500)
            target_item.click(timeout=2500)
        except PlaywrightError:
            try:
                target_item.click(timeout=2500, force=True)
            except PlaywrightError:
                return False

        page.wait_for_timeout(150)
        return True

    def _set_combo_value(self, page: Page, combo_id: str, value_id: str) -> bool:
        """Selects one ExtJS combobox value via a real picker click instead of mutating combo internals."""
        combo_id = combo_id.strip()
        value_id = value_id.strip()
        if not combo_id or not value_id:
            return False

        combo_state = self._read_combo_target_state(page, combo_id, value_id)
        if not combo_state.get("ok"):
            return False

        if self._select_combo_via_picker_click(page, combo_state):
            return True
        return self._select_combo_via_extjs_fallback(page, combo_id, value_id)

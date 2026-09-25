"""Đọc/chọn dropdown Tuần, Lớp và danh sách lớp."""

import re

from ..compat import PlaywrightTimeout
from .config import logger


class DropdownMixin:
    """Đọc/chọn dropdown Tuần, Lớp và danh sách lớp."""

    # -----------------------------------------------------------------
    # 3.2: DROPDOWN OPERATIONS (Tuần, Lớp)
    # -----------------------------------------------------------------

    def get_dropdown_options(self, dropdown_label):
        """Đọc tất cả options từ dropdown trên trang VnEdu.

        VnEdu dùng ExtJS 4.x combobox (KHÔNG phải <select> HTML).
        Tìm combobox qua Ext.ComponentQuery → đọc store data.
        Fallback sang <select> nếu không có ExtJS.

        Args:
            dropdown_label: "tuan" | "lop" | "cap"

        Returns:
            (success, data) — data = dict{options, currentValue, ...} nếu success,
                              hoặc error string nếu fail
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''(label) => {
                // === Map label → ExtJS combobox name ===
                const nameMap = {
                    'tuan': 'cboTuanHoc',
                    'lop': 'cboLopHoc',
                    'cap': 'cboCapHoc'
                };
                const comboName = nameMap[label];
                if (!comboName) {
                    return {ok: false, error: 'Label không hợp lệ: ' + label};
                }

                // === Chiến lược 1: ExtJS combobox (VnEdu v5) ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const c of combos) {
                            let name = '';
                            try { name = c.getName ? c.getName() : (c.name || ''); } catch(e) {}
                            if (name !== comboName) continue;

                            // Đọc store data
                            const store = c.store;
                            if (!store) {
                                return {ok: false, error: 'Combobox không có store: ' + comboName};
                            }

                            const options = [];
                            const items = (store.data && store.data.items) ? store.data.items : [];
                            const vf = c.valueField || 'id';
                            const df = c.displayField || 'name';
                            const currentVal = String(c.getValue ? c.getValue() : '');

                            for (const rec of items) {
                                const d = rec.data || rec;
                                options.push({
                                    value: String(d[vf] != null ? d[vf] : ''),
                                    text: String(d[df] != null ? d[df] : (d.name || d.ten || '')),
                                    selected: String(d[vf]) === currentVal
                                });
                            }

                            return {
                                ok: true,
                                type: 'extjs',
                                comboId: c.id || '',
                                comboName: name,
                                selectId: c.id || '',
                                selectName: name,
                                options: options,
                                currentValue: currentVal,
                                currentText: c.getRawValue ? c.getRawValue() : ''
                            };
                        }
                        return {ok: false, error: 'Không tìm thấy ExtJS combobox: ' + comboName};
                    } catch(e) {
                        // ExtJS lỗi → fallback sang <select>
                    }
                }

                // === Chiến lược 2: Fallback — tìm <select> HTML ===
                const keywords = {
                    'tuan': ['tuan', 'Tuan', 'week', 'ddlTuan', 'cboTuanHoc'],
                    'lop': ['lop', 'Lop', 'class', 'ddlLop', 'cboLopHoc'],
                    'cap': ['cap', 'Cap', 'level', 'ddlCap', 'cboCapHoc'],
                };
                const kws = keywords[label] || [label];

                let select = null;
                for (const sel of document.querySelectorAll('select')) {
                    const id = (sel.id || '').toLowerCase();
                    const name = (sel.name || '').toLowerCase();
                    for (const kw of kws) {
                        if (id.includes(kw.toLowerCase()) || name.includes(kw.toLowerCase())) {
                            select = sel;
                            break;
                        }
                    }
                    if (select) break;
                }

                if (!select) {
                    return {ok: false, error: 'Không tìm thấy dropdown (cả ExtJS lẫn select): ' + label};
                }

                const options = [];
                for (const opt of select.options) {
                    options.push({
                        value: opt.value,
                        text: opt.text.trim(),
                        selected: opt.selected
                    });
                }
                return {
                    ok: true,
                    type: 'select',
                    selectId: select.id,
                    selectName: select.name,
                    options: options,
                    currentValue: select.value,
                    currentText: select.options[select.selectedIndex]?.text?.trim() || ''
                };
            }''', dropdown_label)

            if result.get("ok"):
                # Cache combobox/selector info cho lần sau
                combo_id = result.get("comboId") or result.get("selectId", "")
                combo_name = result.get("comboName") or result.get("selectName", "")
                if combo_id:
                    self._cached_selectors[f"select_{dropdown_label}"] = combo_id
                if combo_name:
                    self._cached_selectors[f"combo_name_{dropdown_label}"] = combo_name
                logger.info(
                    f"Dropdown '{dropdown_label}': type={result.get('type','?')}, "
                    f"{len(result.get('options', []))} options, "
                    f"current='{result.get('currentText', '')}'"
                )
                return True, result
            else:
                return False, result.get("error", "Unknown error")

        except PlaywrightTimeout:
            return False, f"Timeout đọc dropdown '{dropdown_label}'"
        except Exception as e:
            return False, f"Lỗi đọc dropdown: {type(e).__name__}: {str(e)[:80]}"

    def select_dropdown(self, dropdown_label, target_text):
        """Chọn giá trị trong dropdown VnEdu.

        VnEdu dùng ExtJS 4.x combobox → setValue() + fireEvent('select').
        Fallback sang <select> nếu không có ExtJS.

        Args:
            dropdown_label: "tuan" | "lop"
            target_text: Text hiển thị của option (VD: "Tuần 25", "6A4")

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            # Bước 1: Tìm và chọn dropdown bằng JS
            result = self.page.evaluate('''(args) => {
                const [label, targetText] = args;

                // === Map label → ExtJS combobox name ===
                const nameMap = {
                    'tuan': 'cboTuanHoc',
                    'lop': 'cboLopHoc',
                    'cap': 'cboCapHoc'
                };
                const comboName = nameMap[label];

                // === Chiến lược 1: ExtJS combobox ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery && comboName) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const c of combos) {
                            let name = '';
                            try { name = c.getName ? c.getName() : (c.name || ''); } catch(e) {}
                            if (name !== comboName) continue;

                            const store = c.store;
                            if (!store) {
                                return {ok: false, error: 'Combobox không có store: ' + comboName};
                            }

                            const items = (store.data && store.data.items) ? store.data.items : [];
                            const vf = c.valueField || 'id';
                            const df = c.displayField || 'name';

                            // Tìm record khớp text — ưu tiên EXACT trước, sau đó mới
                            // fuzzy có ràng buộc biên để tránh "6A1" khớp nhầm "6A10"
                            // hoặc "Tuần 1" khớp nhầm "Tuần 10".
                            let matchRec = null;
                            const target = targetText.trim().toLowerCase();

                            // Pass 1: exact match
                            for (const rec of items) {
                                const d = rec.data || rec;
                                const text = String(d[df] != null ? d[df] : '').trim().toLowerCase();
                                if (text === target) {
                                    matchRec = rec;
                                    break;
                                }
                            }

                            // Pass 2: prefix match có ràng buộc biên (ký tự ngay sau
                            // target phải là hết chuỗi hoặc không phải chữ/số)
                            if (!matchRec && target) {
                                for (const rec of items) {
                                    const d = rec.data || rec;
                                    const text = String(d[df] != null ? d[df] : '').trim().toLowerCase();
                                    if (!text.startsWith(target)) continue;
                                    const nextChar = text.charAt(target.length);
                                    if (nextChar === '' || !/[a-z0-9]/.test(nextChar)) {
                                        matchRec = rec;
                                        break;
                                    }
                                }
                            }

                            // Thử tìm linh hoạt hơn (chỉ so sánh số)
                            if (!matchRec) {
                                const targetNum = targetText.replace(/\\D/g, '');
                                if (targetNum) {
                                    for (const rec of items) {
                                        const d = rec.data || rec;
                                        const text = String(d[df] != null ? d[df] : '');
                                        const optNum = text.replace(/\\D/g, '');
                                        if (optNum === targetNum) {
                                            matchRec = rec;
                                            break;
                                        }
                                    }
                                }
                            }

                            // Thử tìm theo value trực tiếp (nếu truyền số Tuần)
                            if (!matchRec) {
                                const targetNum = targetText.replace(/\\D/g, '');
                                if (targetNum) {
                                    for (const rec of items) {
                                        const d = rec.data || rec;
                                        if (String(d[vf]) === targetNum) {
                                            matchRec = rec;
                                            break;
                                        }
                                    }
                                }
                            }

                            if (!matchRec) {
                                return {
                                    ok: false,
                                    error: 'Không tìm thấy option: ' + targetText,
                                    available: items.slice(0, 10).map(r => {
                                        const d = r.data || r;
                                        return String(d[df] != null ? d[df] : '');
                                    })
                                };
                            }

                            // Chọn giá trị qua ExtJS API
                            const newValue = (matchRec.data || matchRec)[vf];
                            c.setValue(newValue);
                            c.fireEvent('select', c, [matchRec]);

                            return {
                                ok: true,
                                type: 'extjs',
                                selectedValue: String(newValue),
                                selectedText: c.getRawValue ? c.getRawValue() : String(targetText)
                            };
                        }
                        return {ok: false, error: 'Không tìm thấy ExtJS combobox: ' + comboName};
                    } catch(e) {
                        // ExtJS lỗi → fallback
                    }
                }

                // === Chiến lược 2: Fallback — <select> HTML ===
                const keywords = {
                    'tuan': ['tuan', 'Tuan', 'ddlTuan', 'cboTuanHoc'],
                    'lop': ['lop', 'Lop', 'ddlLop', 'cboLopHoc'],
                };
                const kws = keywords[label] || [label];

                let select = null;
                for (const sel of document.querySelectorAll('select')) {
                    const id = (sel.id || '').toLowerCase();
                    const name = (sel.name || '').toLowerCase();
                    for (const kw of kws) {
                        if (id.includes(kw.toLowerCase()) || name.includes(kw.toLowerCase())) {
                            select = sel;
                            break;
                        }
                    }
                    if (select) break;
                }
                if (!select) {
                    return {ok: false, error: 'Không tìm thấy dropdown (cả ExtJS lẫn select): ' + label};
                }

                // Tìm option khớp text — exact trước, prefix có ràng buộc biên sau
                let matchIdx = -1;
                const target = targetText.trim().toLowerCase();

                // Pass 1: exact match
                for (let i = 0; i < select.options.length; i++) {
                    const optText = select.options[i].text.trim().toLowerCase();
                    if (optText === target) {
                        matchIdx = i;
                        break;
                    }
                }

                // Pass 2: prefix match có ràng buộc biên (tránh "6A1" khớp "6A10")
                if (matchIdx < 0 && target) {
                    for (let i = 0; i < select.options.length; i++) {
                        const optText = select.options[i].text.trim().toLowerCase();
                        if (!optText.startsWith(target)) continue;
                        const nextChar = optText.charAt(target.length);
                        if (nextChar === '' || !/[a-z0-9]/.test(nextChar)) {
                            matchIdx = i;
                            break;
                        }
                    }
                }
                if (matchIdx < 0) {
                    const targetNum = targetText.replace(/\\D/g, '');
                    if (targetNum) {
                        for (let i = 0; i < select.options.length; i++) {
                            const optNum = select.options[i].text.replace(/\\D/g, '');
                            if (optNum === targetNum) {
                                matchIdx = i;
                                break;
                            }
                        }
                    }
                }
                if (matchIdx < 0) {
                    return {
                        ok: false,
                        error: 'Không tìm thấy option: ' + targetText,
                        available: Array.from(select.options).map(o => o.text.trim()).slice(0, 10)
                    };
                }

                select.selectedIndex = matchIdx;
                select.value = select.options[matchIdx].value;
                select.dispatchEvent(new Event('change', {bubbles: true}));
                select.dispatchEvent(new Event('input', {bubbles: true}));

                return {
                    ok: true,
                    type: 'select',
                    selectedValue: select.value,
                    selectedText: select.options[matchIdx].text.trim()
                };
            }''', [dropdown_label, target_text])

            if not result.get("ok"):
                err = result.get("error", "Unknown")
                avail = result.get("available", [])
                if avail:
                    err += f"\nCó sẵn: {', '.join(avail)}"
                return False, err

            # Bước 2: Chờ page cập nhật (AJAX hoặc full reload) theo điều kiện thật
            wait_ok, wait_state = self._wait_page_update(
                timeout_s=12,
                dropdown_label=dropdown_label,
                target_text=result.get("selectedText", target_text),
            )
            if not wait_ok:
                logger.debug(
                    f"Dropdown wait timeout for {dropdown_label}={target_text}: {wait_state}"
                )

            selected = result.get("selectedText", target_text)
            logger.info(f"Selected {dropdown_label}: {selected} (via {result.get('type', '?')})")
            return True, f"Đã chọn: {selected}"

        except PlaywrightTimeout:
            return False, f"Timeout chọn dropdown '{dropdown_label}'"
        except Exception as e:
            return False, f"Lỗi chọn dropdown: {type(e).__name__}: {str(e)[:80]}"

    def select_tuan(self, tuan_text):
        """Chọn Tuần từ dropdown.

        Args:
            tuan_text: "Tuần 25" hoặc "25" (tự thêm prefix)

        Returns:
            (success: bool, message: str)
        """
        # Normalize: nếu chỉ là số, thêm "Tuần "
        text = str(tuan_text).strip()
        if text.isdigit():
            text = f"Tuần {text}"
        return self.select_dropdown("tuan", text)

    def select_lop(self, lop_text):
        """Chọn Lớp từ dropdown.

        Args:
            lop_text: "6A1", "9A4", etc.

        Returns:
            (success: bool, message: str)
        """
        return self.select_dropdown("lop", str(lop_text).strip())

    def get_tuan_options(self):
        """Lấy danh sách Tuần có sẵn.

        Returns:
            (success, list[str]) — danh sách text options
        """
        ok, data = self.get_dropdown_options("tuan")
        if ok:
            return True, [opt["text"] for opt in data.get("options", [])]
        return False, data

    def get_lop_options_with_meta(self):
        """Lấy danh sách lớp kèm metadata ổn định từ combobox/store/DOM.

        Returns:
            (success, list[dict]) — mỗi phần tử có ít nhất `text`, có thể kèm
            `value`, `khoi`, `cap`, `source`.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const out = [];
                const byKey = new Map();

                const cleanText = (text) => String(text == null ? '' : text)
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const looksLikeClassName = (text) => {
                    const value = cleanText(text);
                    if (!value || value.startsWith('--')) return false;
                    if (/^tu[aà]n\\s*\\d+/i.test(value)) return false;
                    if (/^kh[oố]i\\s*\\d+/i.test(value)) return false;
                    if (value.length > 12 || /\\s/.test(value)) return false;
                    return /^\\d{1,2}[A-Za-zÀ-ỹ][A-Za-z0-9À-ỹ._/-]*$/.test(value);
                };
                const inferKhoi = (text) => {
                    const match = cleanText(text).match(/^(\\d{1,2})/);
                    return match ? match[1] : '';
                };
                const add = (payload, source) => {
                    const valueText = cleanText(payload && payload.text);
                    if (!looksLikeClassName(valueText)) return;
                    const key = valueText.toLowerCase();
                    const normalized = {
                        text: valueText,
                        value: cleanText(payload && payload.value),
                        khoi: cleanText(payload && payload.khoi) || inferKhoi(valueText),
                        cap: cleanText(payload && payload.cap),
                        source: source || cleanText(payload && payload.source),
                    };
                    if (byKey.has(key)) {
                        const current = byKey.get(key);
                        for (const field of ['value', 'khoi', 'cap', 'source']) {
                            if (!current[field] && normalized[field]) current[field] = normalized[field];
                        }
                        return;
                    }
                    byKey.set(key, normalized);
                    out.push(normalized);
                };

                const getCombo = () => {
                    if (!(window.Ext && Ext.ComponentQuery)) return null;
                    const combos = Ext.ComponentQuery.query('combobox');
                    const looksLikeClassStore = (combo) => {
                        try {
                            const rawValue = cleanText(combo.getRawValue ? combo.getRawValue() : combo.rawValue || '');
                            if (looksLikeClassName(rawValue)) return true;
                        } catch (e) {}
                        try {
                            const store = combo.getStore ? combo.getStore() : combo.store;
                            const items = store && store.getRange ? store.getRange() : (store && store.data && store.data.items) || [];
                            const displayField = combo.displayField || 'ten';
                            let sampleHits = 0;
                            for (const rec of Array.from(items).slice(0, 12)) {
                                let candidate = '';
                                try { candidate = rec.get ? rec.get(displayField) : ''; } catch (e) {}
                                if (!candidate && rec && rec.data) candidate = rec.data[displayField] || rec.data.ten || rec.data.lop || rec.data.ma_lop;
                                if (looksLikeClassName(candidate)) sampleHits++;
                            }
                            return sampleHits > 0;
                        } catch (e) {}
                        return false;
                    };
                    for (const combo of combos) {
                        try {
                            const name = combo.getName ? combo.getName() : (combo.name || '');
                            if (name === 'cboLopHoc' && looksLikeClassStore(combo)) return combo;
                        } catch (e) {}
                    }
                    return null;
                };

                const readRecord = (rec, fields) => {
                    for (const field of fields) {
                        try {
                            if (rec && rec.get && rec.get(field) != null) return rec.get(field);
                        } catch (e) {}
                        try {
                            if (rec && rec.data && rec.data[field] != null) return rec.data[field];
                        } catch (e2) {}
                        try {
                            if (rec && rec[field] != null) return rec[field];
                        } catch (e3) {}
                    }
                    return '';
                };

                const combo = getCombo();
                if (combo) {
                    try {
                        const currentText = combo.getRawValue ? combo.getRawValue() : combo.rawValue;
                        add({text: currentText}, 'current_raw');
                    } catch (e) {}

                    let picker = null;
                    try {
                        if (combo.expand) combo.expand();
                    } catch (e) {}
                    try {
                        picker = combo.getPicker ? combo.getPicker() : combo.picker;
                    } catch (e) {}

                    const store = (() => {
                        try { return combo.getStore ? combo.getStore() : combo.store; } catch (e) {}
                        return combo.store || null;
                    })();
                    const displayField = combo.displayField || 'ten';
                    const valueField = combo.valueField || 'id';
                    const fieldCandidates = [displayField, 'ten', 'name', 'text', 'label', 'lop', 'ma_lop'];
                    const valueCandidates = [valueField, 'id', 'value', 'ma_lop', 'lop_hoc_id'];
                    const khoiCandidates = ['khoi', 'khoi_hoc', 'grade', 'khoiHoc'];
                    const capCandidates = ['cap', 'cap_hoc', 'capHoc', 'caphoc'];

                    const readStoreItems = (items, source) => {
                        if (!items) return;
                        for (const rec of Array.from(items)) {
                            add({
                                text: readRecord(rec, fieldCandidates),
                                value: readRecord(rec, valueCandidates),
                                khoi: readRecord(rec, khoiCandidates),
                                cap: readRecord(rec, capCandidates),
                            }, source);
                        }
                    };

                    if (store) {
                        try {
                            if (store.getRange) readStoreItems(store.getRange(), 'store.getRange');
                        } catch (e) {}
                        try {
                            if (store.data && store.data.items) readStoreItems(store.data.items, 'store.data');
                        } catch (e) {}
                        try {
                            if (store.snapshot && store.snapshot.items) readStoreItems(store.snapshot.items, 'store.snapshot');
                        } catch (e) {}
                        try {
                            if (store.allData && store.allData.items) readStoreItems(store.allData.items, 'store.allData');
                        } catch (e) {}
                    }

                    try {
                        const root = picker && picker.el && picker.el.dom ? picker.el.dom : null;
                        if (root) {
                            for (const node of root.querySelectorAll('.x-boundlist-item, .x-combo-list-item, option')) {
                                add({text: node.textContent || node.innerText || ''}, 'picker_dom');
                            }
                        }
                    } catch (e) {}

                    try {
                        for (const node of document.querySelectorAll('.x-boundlist-item, .x-combo-list-item')) {
                            add({text: node.textContent || node.innerText || ''}, 'boundlist_dom');
                        }
                    } catch (e) {}

                    try {
                        if (combo.collapse) combo.collapse();
                    } catch (e) {}
                }

                for (const sel of document.querySelectorAll('select')) {
                    const key = String(sel.id || sel.name || '').toLowerCase();
                    if (!key.includes('lop') && !key.includes('class') && !key.includes('cbolophoc')) continue;
                    for (const opt of Array.from(sel.options || [])) {
                        add({
                            text: opt.textContent || opt.innerText || opt.text || '',
                            value: opt.value || '',
                        }, 'select');
                    }
                }

                return {ok: out.length > 0, options: out};
            }''')
            if not result.get("ok"):
                return False, "Không tìm thấy danh sách lớp từ combobox/store/DOM"
            return True, list(result.get("options", []) or [])
        except PlaywrightTimeout:
            return False, "Timeout đọc danh sách lớp"
        except Exception as e:
            return False, f"Lỗi đọc danh sách lớp: {type(e).__name__}: {str(e)[:120]}"

    def get_lop_options(self):
        """Lấy danh sách text lớp có sẵn."""
        ok, records_or_error = self.get_lop_options_with_meta()
        if not ok:
            return False, records_or_error
        options = [
            str(item.get("text", "")).strip()
            for item in list(records_or_error or [])
            if str(item.get("text", "")).strip()
        ]
        return True, options

    def fetch_lop_options_for_weeks_service(self, tuan_nums, timeout_s=10.0, concurrency=6):
        """Đọc lớp thật của nhiều tuần qua service, không thay đổi dropdown trên web."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        ordered_weeks = []
        seen_weeks = set()
        for item in list(tuan_nums or []):
            try:
                week_num = int(item)
            except Exception:
                continue
            if week_num < 1 or week_num > 52 or week_num in seen_weeks:
                continue
            seen_weeks.add(week_num)
            ordered_weeks.append(week_num)
        if not ordered_weeks:
            return False, "Danh sách tuần cần quét đang trống"

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    const cleanText = (value) => String(value == null ? '' : value)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const looksLikeClassName = (value) => {
                        const text = cleanText(value);
                        if (!text || text.length > 16 || /\\s/.test(text)) return false;
                        return /^\\d{1,2}[A-Za-zÀ-ỹ][A-Za-z0-9À-ỹ._/-]*$/.test(text);
                    };
                    const getCombo = (name) => {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        for (const combo of Ext.ComponentQuery.query('combobox')) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    };
                    const firstValue = (record, fields) => {
                        for (const field of fields) {
                            if (record && record[field] != null && cleanText(record[field])) {
                                return cleanText(record[field]);
                            }
                        }
                        return '';
                    };
                    const findRecordArray = (payload) => {
                        const candidates = [];
                        const visit = (value, depth) => {
                            if (depth > 5 || value == null) return;
                            if (Array.isArray(value)) {
                                candidates.push(value);
                                for (const item of value.slice(0, 4)) {
                                    if (item && typeof item === 'object') visit(item, depth + 1);
                                }
                                return;
                            }
                            if (typeof value !== 'object') return;
                            for (const child of Object.values(value)) visit(child, depth + 1);
                        };
                        visit(payload, 0);
                        let best = [];
                        let bestScore = -1;
                        for (const items of candidates) {
                            let score = 0;
                            for (const record of items.slice(0, 30)) {
                                if (!record || typeof record !== 'object') continue;
                                const label = firstValue(record, [
                                    'ten', 'name', 'text', 'label', 'lop', 'ten_lop',
                                    'lop_hoc', 'ma_lop', 'display'
                                ]);
                                if (looksLikeClassName(label)) score += 4;
                                if (firstValue(record, ['id', 'value', 'lop_id', 'lop_hoc_id', 'ma_lop'])) {
                                    score += 1;
                                }
                            }
                            if (score > bestScore) {
                                bestScore = score;
                                best = items;
                            }
                        }
                        return bestScore > 0 ? best : [];
                    };

                    const capCombo = getCombo('cboCapHoc');
                    const capHoc = capCombo
                        ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                        : 2;
                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken)
                        : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                        : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );

                    async function fetchWeek(weekNum) {
                        const params = new URLSearchParams();
                        params.set('my_token', String(token || ''));
                        params.set('my_user_id', String(userId || ''));
                        params.set('app_nam_hoc', namHoc);
                        params.set('ma_quyen', 'view_detail,export,ket_chuyen');
                        params.set('cap_hoc', String(capHoc || 2));
                        params.set('tuan_hoc', String(weekNum));
                        params.set('page', '1');
                        params.set('start', '0');
                        params.set('limit', '500');
                        const url = './?call=app.sodaubai.serv.so_dau_bai.getDanhSachLopByCap&'
                            + params.toString();
                        const controller = new AbortController();
                        const timeoutId = setTimeout(
                            () => controller.abort(),
                            Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000)
                        );
                        try {
                            const response = await fetch(url, {
                                method: 'GET',
                                credentials: 'same-origin',
                                signal: controller.signal,
                            });
                            const rawText = await response.text();
                            if (!response.ok) {
                                return {
                                    week: weekNum,
                                    ok: false,
                                    error: 'HTTP ' + response.status,
                                };
                            }
                            let payload = null;
                            try {
                                payload = JSON.parse(rawText);
                            } catch (e) {
                                return {
                                    week: weekNum,
                                    ok: false,
                                    error: 'Service lớp không trả JSON hợp lệ',
                                };
                            }
                            const rawRecords = findRecordArray(payload);
                            const records = [];
                            const seen = new Set();
                            for (const record of rawRecords) {
                                if (!record || typeof record !== 'object') continue;
                                const text = firstValue(record, [
                                    'ten', 'name', 'text', 'label', 'lop', 'ten_lop',
                                    'lop_hoc', 'ma_lop', 'display'
                                ]);
                                if (!looksLikeClassName(text)) continue;
                                const key = text.toLocaleLowerCase('vi');
                                if (seen.has(key)) continue;
                                seen.add(key);
                                const inferredKhoi = (text.match(/^(\\d{1,2})/) || [])[1] || '';
                                records.push({
                                    text,
                                    value: firstValue(record, [
                                        'id', 'value', 'lop_id', 'lop_hoc_id', 'ma_lop'
                                    ]),
                                    khoi: firstValue(record, [
                                        'khoi', 'khoi_hoc', 'ma_khoi', 'khoiHoc', 'grade'
                                    ]) || inferredKhoi,
                                    cap: firstValue(record, [
                                        'cap', 'cap_hoc', 'capHoc', 'caphoc'
                                    ]) || cleanText(capHoc),
                                    source: 'service.getDanhSachLopByCap',
                                });
                            }
                            return {week: weekNum, ok: true, records};
                        } catch (error) {
                            const isTimeout = String(error && error.name ? error.name : '') === 'AbortError';
                            return {
                                week: weekNum,
                                ok: false,
                                error: isTimeout
                                    ? 'Timeout đọc service lớp'
                                    : String(error && error.message ? error.message : error),
                            };
                        } finally {
                            clearTimeout(timeoutId);
                        }
                    }

                    const queue = Array.from(args.weeks || []);
                    const results = [];
                    const workerCount = Math.max(
                        1,
                        Math.min(parseInt(args.concurrency || 4, 10) || 4, queue.length)
                    );
                    async function worker() {
                        while (queue.length) {
                            const weekNum = queue.shift();
                            if (!weekNum) break;
                            results.push(await fetchWeek(weekNum));
                        }
                    }
                    await Promise.all(Array.from({length: workerCount}, () => worker()));
                    results.sort((a, b) => a.week - b.week);

                    const byClass = new Map();
                    const classesByWeek = {};
                    const weekErrors = [];
                    const weeksScanned = [];
                    for (const item of results) {
                        if (!item.ok) {
                            weekErrors.push({week: item.week, message: item.error || 'Lỗi không xác định'});
                            continue;
                        }
                        weeksScanned.push(item.week);
                        classesByWeek[String(item.week)] = [];
                        for (const record of item.records || []) {
                            const key = record.text.toLocaleLowerCase('vi');
                            classesByWeek[String(item.week)].push(record.text);
                            if (!byClass.has(key)) {
                                byClass.set(key, {...record, weeks: [item.week]});
                                continue;
                            }
                            const current = byClass.get(key);
                            if (!current.weeks.includes(item.week)) current.weeks.push(item.week);
                            for (const field of ['value', 'khoi', 'cap', 'source']) {
                                if (!current[field] && record[field]) current[field] = record[field];
                            }
                        }
                    }
                    const records = Array.from(byClass.values()).sort(
                        (a, b) => a.text.localeCompare(b.text, 'vi', {numeric: true})
                    );
                    return {
                        ok: records.length > 0,
                        options: records.map((item) => item.text),
                        records,
                        classes_by_week: classesByWeek,
                        weeks_scanned: weeksScanned,
                        week_errors: weekErrors,
                        source: 'service',
                        error: records.length ? '' : 'Service không trả về lớp hợp lệ',
                    };
                }''',
                {
                    "weeks": ordered_weeks,
                    "timeoutMs": max(int(timeout_s * 1000), 3000),
                    "concurrency": max(1, min(int(concurrency or 4), 8)),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Service không trả về danh sách lớp")
        except PlaywrightTimeout:
            return False, "Timeout quét danh sách lớp qua service"
        except Exception as e:
            return False, f"Lỗi quét lớp qua service: {type(e).__name__}: {str(e)[:140]}"

    def discover_lop_options_for_weeks(self, tuan_nums=None, restore_selection=True):
        """Quét danh sách lớp xuất hiện trong nhiều tuần để tránh thiếu lớp theo học kỳ."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        original_selection = {}
        try:
            ok_current, current = self.get_current_selection()
            if ok_current and isinstance(current, dict):
                original_selection = current
        except Exception:
            original_selection = {}

        try:
            if tuan_nums is None:
                ok_weeks, week_options = self.get_tuan_options()
                if not ok_weeks:
                    return False, f"Không đọc được danh sách tuần: {week_options}"
                parsed_weeks = []
                for item in week_options:
                    match = re.search(r"\d+", str(item or ""))
                    if match:
                        parsed_weeks.append(int(match.group()))
                tuan_nums = parsed_weeks

            ordered_weeks = []
            seen_weeks = set()
            for item in list(tuan_nums or []):
                try:
                    week_num = int(item)
                except Exception:
                    continue
                if week_num < 1 or week_num > 52 or week_num in seen_weeks:
                    continue
                seen_weeks.add(week_num)
                ordered_weeks.append(week_num)

            if not ordered_weeks:
                ok_lop, options = self.get_lop_options()
                return (True, {"options": options, "weeks_scanned": []}) if ok_lop else (False, options)

            ok_service, service_payload = self.fetch_lop_options_for_weeks_service(ordered_weeks)
            if ok_service:
                return True, service_payload
            logger.warning(f"Service quét lớp không khả dụng, fallback UI: {service_payload}")

            class_names = []
            seen_classes = set()
            class_records = {}
            week_errors = []
            scanned_weeks = []
            for week_num in ordered_weeks:
                if self.should_stop:
                    break
                ok_select, msg_select = self.select_tuan(f"Tuần {week_num}")
                if not ok_select:
                    week_errors.append({"week": week_num, "message": msg_select})
                    continue
                scanned_weeks.append(week_num)
                ok_lop, lop_options = self.get_lop_options_with_meta()
                if not ok_lop:
                    week_errors.append({"week": week_num, "message": str(lop_options)})
                    continue
                for item in list(lop_options or []):
                    normalized = str(item.get("text", "") or "").strip()
                    key = normalized.lower()
                    if not normalized or key in seen_classes:
                        if normalized and key in class_records:
                            existing = class_records[key]
                            for field in ("value", "khoi", "cap", "source"):
                                incoming = str(item.get(field, "") or "").strip()
                                if not existing.get(field) and incoming:
                                    existing[field] = incoming
                        continue
                    seen_classes.add(key)
                    class_names.append(normalized)
                    class_records[key] = {
                        "text": normalized,
                        "value": str(item.get("value", "") or "").strip(),
                        "khoi": str(item.get("khoi", "") or "").strip(),
                        "cap": str(item.get("cap", "") or "").strip(),
                        "source": str(item.get("source", "") or "").strip(),
                    }

            if restore_selection and original_selection:
                try:
                    if original_selection.get("tuan"):
                        self.select_tuan(original_selection.get("tuan"))
                    if original_selection.get("lop"):
                        self.select_lop(original_selection.get("lop"))
                except Exception:
                    pass

            return True, {
                "options": class_names,
                "records": [class_records[key] for key in sorted(class_records.keys())],
                "weeks_scanned": scanned_weeks,
                "week_errors": week_errors,
            }
        except Exception as e:
            return False, f"Lỗi quét lớp theo tuần: {type(e).__name__}: {str(e)[:140]}"

    def get_current_selection(self):
        """Đọc Tuần + Lớp đang chọn hiện tại.

        Returns:
            (success, dict) — {tuan: str, lop: str}
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            result = self.page.evaluate('''() => {
                const info = {tuan: '', lop: '', cap: ''};
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            const name = combo.getName ? combo.getName() : (combo.name || '');
                            const rawText = combo.getRawValue ? combo.getRawValue() : (combo.rawValue || '');
                            if (name === 'cboTuanHoc' && rawText) info.tuan = String(rawText).trim();
                            else if (name === 'cboLopHoc' && rawText) info.lop = String(rawText).trim();
                            else if (name === 'cboCapHoc' && rawText) info.cap = String(rawText).trim();
                        }
                    } catch (e) {}
                }
                for (const sel of document.querySelectorAll('select')) {
                    const id = (sel.id || sel.name || '').toLowerCase();
                    const text = sel.options[sel.selectedIndex]?.text?.trim() || '';
                    if (!info.tuan && id.includes('tuan')) info.tuan = text;
                    else if (!info.lop && id.includes('lop')) info.lop = text;
                    else if (!info.cap && id.includes('cap')) info.cap = text;
                }
                return info;
            }''')
            return True, result
        except Exception as e:
            return False, str(e)

    def get_current_user_full_name(self):
        """Đọc họ tên người dùng hiện tại từ session VnEdu.

        Returns:
            (success, str) — tên đầy đủ giáo viên đang đăng nhập
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            result = self.page.evaluate(
                '''() => {
                    const candidates = [
                        typeof phpviet_user_full_name !== 'undefined' ? phpviet_user_full_name : '',
                        typeof phpviet_user_name !== 'undefined' ? phpviet_user_name : '',
                    ];
                    for (const value of candidates) {
                        const text = String(value || '').trim();
                        if (text) return text;
                    }
                    return '';
                }'''
            )
            user_full_name = str(result or "").strip()
            if user_full_name:
                return True, user_full_name
            return False, "Không đọc được tên người dùng hiện tại từ session"
        except Exception as e:
            return False, str(e)

"""Tiện ích trang: option form, chờ cập nhật, inspect, reload."""

import time

from ..compat import PlaywrightTimeout
from .config import logger, NAV_TIMEOUT_MS, POST_CLICK_DELAY


class PageUtilsMixin:
    """Tiện ích trang: option form, chờ cập nhật, inspect, reload."""

    # -----------------------------------------------------------------
    # 3.7: PAGE UTILITIES
    # -----------------------------------------------------------------

    @staticmethod
    def _seed_current_subject_mapping(form_data):
        """Bổ sung map cho Môn học đang được chọn sẵn từ options live hiện tại.

        Tránh bỏ sót trường hợp popup mở ra đã đứng ở đúng Môn học cần dùng
        (ví dụ Ngoại ngữ), khi đó combo Phân môn đã có dữ liệu thật nhưng
        không phát sinh change event để vòng scan phụ thuộc ghi map.
        """
        data = dict(form_data or {})
        current_value = str(data.get("current_mon_hoc_value") or "").strip()
        current_text = str(data.get("current_mon_hoc_text") or "").strip()
        current_options = list(data.get("phan_mon", []) or [])
        raw_map = data.get("phan_mon_by_mon_hoc", {}) or {}
        normalized_map = {
            str(key): list(options or [])
            for key, options in raw_map.items()
            if key is not None
        }
        mon_options = list(data.get("mon_hoc", []) or [])

        if not current_value and current_text:
            for option in mon_options:
                if str(option.get("text") or "").strip() == current_text:
                    current_value = str(option.get("value") or "").strip()
                    break

        if current_value and current_options and not normalized_map.get(current_value):
            # Chỉ seed khi XÁC ĐỊNH CHẮC môn hiện tại (qua value hoặc text).
            # KHÔNG đoán theo kiểu "môn duy nhất thiếu map == môn của popup":
            # nếu popup đang ở môn khác, đoán như vậy sẽ gán sai phân môn và
            # khiến worker fill nhầm. Thà để thiếu map (fail-closed) để bị chặn
            # với thông điệp "bấm Quét Form lại" còn hơn ghi sai dữ liệu.
            normalized_map[current_value] = current_options

        data["phan_mon_by_mon_hoc"] = normalized_map
        return data

    def read_form_options(self, row_index=0, max_tries=15):
        """Mở form popup "chi tiết tiết học" → đọc dropdown options → đóng form.

        VnEdu có NHIỀU loại nút ➕ trên trang (ý kiến GVCN, chi tiết tiết học).
        Method này thử click từng nút ➕ bắt đầu từ row_index, kiểm tra popup
        title — nếu đúng form "chi tiết tiết học" thì đọc options, nếu sai
        (ví dụ "ý kiến GVCN") thì đóng và thử nút tiếp theo.

        Args:
            row_index: int — bắt đầu từ nút ➕ nào (default 0)
            max_tries: int — số lần thử tối đa (default 15)

        Returns:
            (success: bool, data: dict|str)
            data khi success: {
                'phan_mon': [{'value': '...', 'text': '...'}, ...],
                'phan_mon_by_mon_hoc': {'mon_hoc_value': [{value, text}, ...], ...},
                'xep_loai': [{'value': '...', 'text': '...'}, ...],
                'mon_hoc': [{'value': '...', 'text': '...'}, ...],
                'mon_hoc_field': str (tên field trên VnEdu),
                'current_mon_hoc_value': str (Môn học đang selected trong popup),
                'current_mon_hoc_text': str (raw text Môn học đang hiển thị trong popup)
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        # Bước 1: Thử click từng nút ➕ cho đến khi mở đúng form
        correct_form_opened = False
        tried = 0

        for btn_idx in range(row_index, row_index + max_tries):
            tried += 1
            try:
                ok, msg = self.click_add_button(btn_idx)
                if not ok:
                    logger.debug(f"read_form_options: btn #{btn_idx} click fail: {msg}")
                    break  # Hết nút → dừng

                popup_check = {"type": "none"}
                for _popup_try in range(10):
                    time.sleep(POST_CLICK_DELAY)
                    popup_check = self.page.evaluate('''() => {
                        if (typeof Ext === 'undefined' || !Ext.ComponentQuery)
                            return {type: 'no_ext'};
                        var wins = Ext.ComponentQuery.query('window');
                        for (var i = 0; i < wins.length; i++) {
                            var w = wins[i];
                            if (!w.isVisible || !w.isVisible()) continue;
                            var title = (w.title || '').toLowerCase();
                            // Form "chi tiết tiết học" — ĐÚNG
                            if (title.indexOf('chi ti') >= 0 &&
                                (title.indexOf('ti\u1EBFt h\u1ECDc') >= 0 ||
                                 title.indexOf('so dau bai') >= 0 ||
                                 title.indexOf('s\u1ED5 \u0111\u1EA7u b\u00E0i') >= 0)) {
                                return {type: 'lesson_detail', title: w.title};
                            }
                            // Form khác (ý kiến GVCN, etc.)
                            if (title.indexOf('ki\u1EBFn') >= 0 ||
                                title.indexOf('gvcn') >= 0 ||
                                title.indexOf('ch\u1EE7 nhi\u1EC7m') >= 0 ||
                                title.indexOf('y kien') >= 0) {
                                return {type: 'gvcn', title: w.title};
                            }
                            // Popup khác có form
                            var f = w.down('form');
                            if (f) {
                                return {type: 'unknown', title: w.title};
                            }
                        }
                        return {type: 'none'};
                    }''')
                    if popup_check.get("type") != "none":
                        break

                popup_type = popup_check.get("type", "none")
                popup_title = popup_check.get("title", "")
                logger.info(
                    f"read_form_options: btn #{btn_idx} → popup "
                    f"type={popup_type}, title={popup_title}"
                )

                if popup_type == "lesson_detail":
                    correct_form_opened = True
                    break
                elif popup_type in ("gvcn", "unknown"):
                    # Sai form → đóng và thử nút tiếp theo
                    logger.info(f"Wrong popup '{popup_title}' → closing, trying next btn")
                    self.close_form()
                    time.sleep(0.12)
                    continue
                elif popup_type == "none":
                    # Không có popup → có thể click chưa hoạt động, thử tiếp
                    time.sleep(0.10)
                    continue
                else:
                    # no_ext
                    return False, "ExtJS not available"

            except Exception as e_try:
                logger.debug(f"read_form_options: btn #{btn_idx} exception: {e_try}")
                try:
                    self.close_form()
                except Exception:
                    pass
                time.sleep(0.12)
                continue

        if not correct_form_opened:
            return False, (
                f"Không tìm thấy form 'chi tiết tiết học' sau {tried} nút ➕. "
                f"Có thể trang chưa load đúng."
            )

        # Bước 2: Đọc combobox options từ ExtJS store
        try:
            verified_result = self.page.evaluate('''async () => {
                const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                function normalize(value) {
                    try {
                        return String(value == null ? '' : value)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .trim()
                            .toLowerCase();
                    } catch (e) {
                        return String(value == null ? '' : value).trim().toLowerCase();
                    }
                }

                function findPopupFormPanel() {
                    let formPanel = null;
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        if (!(w.isVisible && w.isVisible())) continue;
                        const title = (w.title || '').toLowerCase();
                        if (title.indexOf('chi ti') >= 0 ||
                            title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                            const f = w.down('form');
                            if (f) {
                                formPanel = f;
                                break;
                            }
                        }
                    }
                    return formPanel;
                }

                function getFieldStore(field) {
                    if (!field) return null;
                    try {
                        if (field.getStore && typeof field.getStore === 'function') {
                            return field.getStore();
                        }
                    } catch (e) {}
                    try {
                        return field.store || null;
                    } catch (e2) {
                        return null;
                    }
                }

                function getStoreCount(store) {
                    if (!store) return 0;
                    try { return store.getCount ? store.getCount() : 0; } catch (e) { return 0; }
                }

                function getFieldName(field) {
                    if (!field) return '';
                    try { return field.getName ? field.getName() : (field.name || ''); } catch (e) {}
                    return field.name || '';
                }

                function getComboOptions(field) {
                    const options = [];
                    if (!field) return options;
                    const store = getFieldStore(field);
                    if (!store) return options;

                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
                    try {
                        store.each(function(record) {
                            const text = (
                                record.get(displayField) != null
                                    ? record.get(displayField)
                                    : (record.get('name') || record.get('ten') || '')
                            );
                            options.push({
                                value: String(record.get(valueField) != null ? record.get(valueField) : ''),
                                text: String(text != null ? text : '')
                            });
                        });
                    } catch (e) {}
                    return options;
                }

                function findFieldByCandidates(form, candidates, labelKeywords) {
                    const tried = candidates || [];
                    for (let i = 0; i < tried.length; i++) {
                        try {
                            const direct = form.findField(tried[i]);
                            if (direct) return direct;
                        } catch (e) {}
                    }

                    try {
                        const fields = form.getFields().items || [];
                        for (let i = 0; i < fields.length; i++) {
                            const field = fields[i];
                            const label = normalize(field.fieldLabel || field.boxLabel || '');
                            for (let j = 0; j < (labelKeywords || []).length; j++) {
                                if (label.indexOf(labelKeywords[j]) >= 0) {
                                    return field;
                                }
                            }
                        }
                    } catch (e2) {}
                    return null;
                }

                function findRecordInStore(store, value, field) {
                    if (!store) return null;
                    const valueField = field.valueField || 'id';
                    let record = null;
                    try {
                        if (store.findRecord) record = store.findRecord(valueField, value);
                    } catch (e) {}
                    if (!record) {
                        const intValue = parseInt(value, 10);
                        if (!isNaN(intValue)) {
                            try {
                                if (store.findRecord) record = store.findRecord(valueField, intValue);
                            } catch (e2) {}
                        }
                    }
                    return record;
                }

                async function ensureComboStoreLoaded(field, timeoutMs) {
                    if (!field) return {ok: false, count: 0};
                    const start = Date.now();
                    while ((Date.now() - start) < timeoutMs) {
                        const store = getFieldStore(field);
                        const count = getStoreCount(store);
                        if (count > 0) {
                            try { if (field.isExpanded) field.collapse(); } catch (e) {}
                            return {ok: true, count: count};
                        }

                        const fieldName = getFieldName(field);
                        try {
                            const allCombos = Ext.ComponentQuery.query('combobox');
                            for (let i = 0; i < allCombos.length; i++) {
                                const srcCombo = allCombos[i];
                                if (srcCombo === field) continue;
                                let srcName = '';
                                try {
                                    srcName = srcCombo.getName ? srcCombo.getName() : (srcCombo.name || '');
                                } catch (eName) {}
                                if (!fieldName || srcName !== fieldName) continue;

                                const srcStore = getFieldStore(srcCombo);
                                const srcCount = getStoreCount(srcStore);
                                if (!srcStore || srcCount <= 0) continue;

                                try {
                                    field.bindStore(srcStore, true);
                                    const reboundStore = getFieldStore(field);
                                    if (getStoreCount(reboundStore) > 0) {
                                        return {ok: true, count: getStoreCount(reboundStore)};
                                    }
                                } catch (bindErr) {}
                            }
                        } catch (allErr) {}

                        try { if (field.expand) field.expand(); } catch (expandErr) {}
                        await sleep(80);
                    }

                    const available = getComboOptions(field);
                    return {ok: available.length > 0, count: available.length};
                }

                async function setComboValue(field, value) {
                    if (!field) return false;
                    const storeInfo = await ensureComboStoreLoaded(field, 2200);
                    if (!storeInfo.ok) return false;

                    const store = getFieldStore(field);
                    const record = findRecordInStore(store, value, field);
                    if (!record) return false;

                    const valueField = field.valueField || 'id';
                    field.setValue(record.get(valueField));
                    try { field.lastSelection = [record]; } catch (eLast) {}
                    try { field.fireEvent('select', field, [record]); } catch (eSelect) {}
                    try { field.validate(); } catch (eValidate) {}
                    return true;
                }

                function buildOptionsSignature(options) {
                    return (options || [])
                        .slice(0, 30)
                        .map(opt => String(opt.value || '') + '|' + String(opt.text || ''))
                        .join('||');
                }

                async function waitForDependentCombo(parentField, childField, expectedParentValue, timeoutMs, previousSignature) {
                    if (!parentField || !childField) return {ok: false, count: 0};
                    let lastSignature = '';
                    let stableCount = 0;
                    let sawLoading = false;
                    const start = Date.now();
                    const baselineSignature = String(previousSignature || '');

                    while ((Date.now() - start) < timeoutMs) {
                        await ensureComboStoreLoaded(childField, 400);

                        let parentValue = '';
                        try { parentValue = String(parentField.getValue() || ''); } catch (e) {}
                        const options = getComboOptions(childField);
                        let loading = false;
                        try {
                            const childStore = getFieldStore(childField);
                            loading = !!(childStore && childStore.isLoading && childStore.isLoading());
                        } catch (e2) {}
                        if (loading) {
                            sawLoading = true;
                        }

                        const signature = buildOptionsSignature(options);

                        if (parentValue === String(expectedParentValue) && !loading) {
                            stableCount = (signature && signature === lastSignature)
                                ? stableCount + 1
                                : 1;
                            if (
                                stableCount >= 2 &&
                                (
                                    signature !== baselineSignature ||
                                    sawLoading ||
                                    !baselineSignature
                                )
                            ) {
                                return {
                                    ok: true,
                                    count: options.length,
                                    signature: signature,
                                    changed: signature !== baselineSignature,
                                    sawLoading: sawLoading,
                                };
                            }
                        } else {
                            stableCount = 0;
                        }

                        lastSignature = signature;
                        await sleep(80);
                    }

                    const currentOptions = getComboOptions(childField);
                    return {
                        ok: false,
                        count: currentOptions.length,
                        signature: buildOptionsSignature(currentOptions),
                        changed: buildOptionsSignature(currentOptions) !== baselineSignature,
                        sawLoading: sawLoading,
                    };
                }

                if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                    return {ok: false, error: 'ExtJS not available'};
                }

                const formPanel = findPopupFormPanel();
                if (!formPanel || !formPanel.getForm) {
                    return {ok: false, error: 'Form panel not found in popup'};
                }

                const form = formPanel.getForm();
                const data = {
                    phan_mon: [],
                    phan_mon_by_mon_hoc: {},
                    xep_loai: [],
                    mon_hoc: [],
                    current_mon_hoc_value: '',
                    current_mon_hoc_text: ''
                };

                const monHocNames = [
                    'mon_hoc_id', 'mon_hoc', 'monhoc_id', 'monhoc',
                    'subject_id', 'ma_mon_hoc'
                ];
                const monField = findFieldByCandidates(form, monHocNames, ['mon hoc']);
                const phanField = findFieldByCandidates(form, ['phan_mon_id'], ['phan mon']);
                const xepLoaiField = findFieldByCandidates(form, ['xep_loai'], ['xep loai']);

                if (monField) {
                    await ensureComboStoreLoaded(monField, 1200);
                    data.mon_hoc = getComboOptions(monField);
                    data.mon_hoc_field = getFieldName(monField);
                    try { data.current_mon_hoc_value = String(monField.getValue() || ''); } catch (e) {}
                    try {
                        data.current_mon_hoc_text = String(
                            (monField.getRawValue ? monField.getRawValue() : monField.rawValue) || ''
                        );
                    } catch (e) {}
                }

                if (xepLoaiField) {
                    await ensureComboStoreLoaded(xepLoaiField, 900);
                    data.xep_loai = getComboOptions(xepLoaiField);
                }

                if (phanField) {
                    await ensureComboStoreLoaded(phanField, 900);
                    data.phan_mon = getComboOptions(phanField);
                }

                if (monField && phanField && data.mon_hoc.length > 0) {
                    let originalMonValue = '';
                    try { originalMonValue = String(monField.getValue() || ''); } catch (e) {}

                    for (let i = 0; i < data.mon_hoc.length; i++) {
                        const monOpt = data.mon_hoc[i];
                        const previousSignature = buildOptionsSignature(getComboOptions(phanField));
                        const setOk = await setComboValue(monField, monOpt.value);
                        if (!setOk) continue;

                        const waitInfo = await waitForDependentCombo(
                            monField,
                            phanField,
                            monOpt.value,
                            1200,
                            previousSignature
                        );
                        if (!waitInfo.ok) {
                            continue;
                        }
                        data.phan_mon_by_mon_hoc[String(monOpt.value)] = getComboOptions(phanField);
                    }

                    if (originalMonValue) {
                        const previousSignature = buildOptionsSignature(getComboOptions(phanField));
                        const restored = await setComboValue(monField, originalMonValue);
                        if (restored) {
                            const restoredWait = await waitForDependentCombo(
                                monField,
                                phanField,
                                originalMonValue,
                                900,
                                previousSignature
                            );
                            const restoredOptions = getComboOptions(phanField);
                            if (restoredWait.ok && restoredOptions.length > 0) {
                                data.phan_mon = restoredOptions;
                            }
                        }
                    }
                }

                return {ok: true, data: data};
            }''')

            # Bước 3: Đóng form
            self.close_form()

            if verified_result.get("ok"):
                data = self._seed_current_subject_mapping(verified_result.get("data", {}))
                pm_count = len(data.get("phan_mon", []))
                xl_count = len(data.get("xep_loai", []))
                mh_count = len(data.get("mon_hoc", []))
                pm_map_count = len(data.get("phan_mon_by_mon_hoc", {}))
                mh_field = data.get("mon_hoc_field", "?")
                logger.info(
                    f"Form options: phan_mon={pm_count}, xep_loai={xl_count}, "
                    f"mon_hoc={mh_count} (field={mh_field}, mapped={pm_map_count})"
                )
                return True, data

            return False, verified_result.get("error", "Unknown error")

        except Exception as e:
            # Cố đóng form nếu còn mở
            try:
                self.close_form()
            except Exception:
                pass
            return False, f"Exception: {type(e).__name__}: {str(e)[:80]}"

    def _wait_page_update(self, timeout_s=15, dropdown_label=None, target_text=None):
        """Chờ page cập nhật sau khi thay đổi dropdown.

        Ưu tiên wait theo tín hiệu thật: network idle, loadmask biến mất,
        dropdown phản ánh giá trị mới và state ổn định.
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=int(timeout_s * 1000))
        except PlaywrightTimeout:
            logger.debug("networkidle timeout — falling back to DOM/update polling")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

        deadline = time.time() + max(timeout_s, 0.5)
        stable_hits = 0
        last_state = {}
        while time.time() < deadline:
            try:
                state = self.page.evaluate('''(args) => {
                    const label = String(args.label || '');
                    const targetText = String(args.targetText || '').trim().toLowerCase();

                    function normalize(text) {
                        return String(text == null ? '' : text).trim().toLowerCase();
                    }

                    function digitsOnly(text) {
                        return String(text == null ? '' : text).replace(/\\D/g, '');
                    }

                    function textMatches(currentText) {
                        if (!targetText) return true;
                        const current = normalize(currentText);
                        if (!current) return false;
                        if (current === targetText || current.indexOf(targetText) >= 0) return true;
                        const currentNum = digitsOnly(current);
                        const targetNum = digitsOnly(targetText);
                        return !!(currentNum && targetNum && currentNum === targetNum);
                    }

                    let ajaxBusy = false;
                    try {
                        ajaxBusy = !!(
                            typeof Ext !== 'undefined' &&
                            Ext.Ajax &&
                            Ext.Ajax.isLoading &&
                            Ext.Ajax.isLoading()
                        );
                    } catch (e) {}

                    let loadMaskVisible = false;
                    try {
                        const masks = document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask');
                        for (const mask of masks) {
                            if (mask.offsetParent !== null) {
                                loadMaskVisible = true;
                                break;
                            }
                        }
                    } catch (e2) {}

                    let selectedText = '';
                    if (label) {
                        const nameMap = {
                            'tuan': 'cboTuanHoc',
                            'lop': 'cboLopHoc',
                            'cap': 'cboCapHoc'
                        };
                        const comboName = nameMap[label];
                        if (comboName && typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                            try {
                                const combos = Ext.ComponentQuery.query('combobox');
                                for (const combo of combos) {
                                    const name = combo.getName ? combo.getName() : (combo.name || '');
                                    if (name !== comboName) continue;
                                    selectedText = combo.getRawValue
                                        ? String(combo.getRawValue() || '')
                                        : '';
                                    break;
                                }
                            } catch (e3) {}
                        }

                        if (!selectedText) {
                            const keywords = {
                                'tuan': ['tuan', 'cboTuanHoc'],
                                'lop': ['lop', 'cboLopHoc'],
                                'cap': ['cap', 'cboCapHoc'],
                            };
                            const kws = keywords[label] || [label];
                            for (const sel of document.querySelectorAll('select')) {
                                const id = normalize(sel.id || '');
                                const name = normalize(sel.name || '');
                                let matched = false;
                                for (const kw of kws) {
                                    if (id.indexOf(normalize(kw)) >= 0 || name.indexOf(normalize(kw)) >= 0) {
                                        matched = true;
                                        break;
                                    }
                                }
                                if (!matched) continue;
                                const idx = sel.selectedIndex;
                                if (idx >= 0 && sel.options[idx]) {
                                    selectedText = String(sel.options[idx].text || '');
                                }
                                break;
                            }
                        }
                    }

                    return {
                        ready: document.readyState === 'complete',
                        ajaxBusy: ajaxBusy,
                        loadMaskVisible: loadMaskVisible,
                        selectedText: selectedText,
                        selectedOk: textMatches(selectedText),
                    };
                }''', {"label": dropdown_label, "targetText": target_text})
            except Exception as e:
                last_state = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
                time.sleep(0.08)
                continue

            last_state = state or {}
            ready_now = (
                last_state.get("ready")
                and not last_state.get("ajaxBusy")
                and not last_state.get("loadMaskVisible")
                and last_state.get("selectedOk", True)
            )
            if ready_now:
                stable_hits += 1
                if stable_hits >= 2:
                    return True, last_state
            else:
                stable_hits = 0
            time.sleep(0.08)

        return False, last_state

    def inspect_page(self):
        """Dump cấu trúc trang VnEdu để debug.

        Trả về thông tin: dropdowns (ExtJS + HTML select), tables,
        buttons, forms, add buttons.
        Dùng khi cần tìm selectors chính xác.

        Returns:
            (success, dict)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const info = {
                    url: location.href,
                    title: document.title,
                    selects: [],
                    extCombos: [],
                    tables: [],
                    forms: [],
                    buttons: [],
                    addButtons: 0,
                };

                // === ExtJS Comboboxes (VnEdu v5 dùng ExtJS 4.x) ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const c of combos) {
                            let name = '';
                            try { name = c.getName ? c.getName() : (c.name || ''); } catch(e) {}
                            let storeCount = 0;
                            try {
                                const store = c.store;
                                storeCount = (store && store.data && store.data.items)
                                    ? store.data.items.length : 0;
                            } catch(e) {}
                            const hidden = c.isHidden ? c.isHidden() : false;
                            if (hidden) continue;
                            info.extCombos.push({
                                id: c.id || '',
                                name: name,
                                value: String(c.getValue ? c.getValue() : ''),
                                rawValue: c.getRawValue ? c.getRawValue() : '',
                                storeCount: storeCount
                            });
                        }
                    } catch(e) {}
                }

                // === HTML <select> (fallback) ===
                for (const sel of document.querySelectorAll('select')) {
                    info.selects.push({
                        id: sel.id,
                        name: sel.name,
                        optionCount: sel.options.length,
                        currentText: sel.options[sel.selectedIndex]?.text?.trim() || '',
                        visible: sel.offsetParent !== null
                    });
                }

                // === Tables ===
                for (const t of document.querySelectorAll('table')) {
                    const rows = t.querySelectorAll('tr');
                    info.tables.push({
                        id: t.id,
                        className: (t.className || '').substring(0, 50),
                        rows: rows.length,
                        hasRowspan: t.querySelector('td[rowspan]') !== null
                    });
                }

                // === Add buttons (a.add_tiet_so_dau_bai) ===
                info.addButtons = document.querySelectorAll('a.add_tiet_so_dau_bai').length;

                // === Forms ===
                for (const f of document.querySelectorAll('form')) {
                    info.forms.push({
                        id: f.id,
                        action: f.action?.substring(0, 80) || '',
                        method: f.method,
                        inputCount: f.querySelectorAll('input').length
                    });
                }

                // === Visible buttons ===
                for (const btn of document.querySelectorAll(
                    'input[type="button"], input[type="submit"], button'
                )) {
                    if (btn.offsetParent === null) continue;
                    info.buttons.push({
                        tag: btn.tagName,
                        id: btn.id,
                        text: (btn.value || btn.innerText || '').trim().substring(0, 30),
                    });
                }

                return info;
            }''')

            return True, result

        except Exception as e:
            return False, f"Lỗi inspect: {type(e).__name__}: {str(e)[:80]}"

    def setup_dialog_auto_accept(self):
        """Đăng ký auto-accept cho tất cả alert/confirm dialogs (idempotent).

        Chỉ giữ DUY NHẤT một listener: gỡ handler cũ (nếu có) trước khi gắn
        mới, tránh tình trạng nhiều listener cùng accept() một dialog gây lỗi
        "dialog already handled". An toàn gọi nhiều lần.
        """
        if not self.is_connected:
            return

        try:
            def on_dialog(dialog):
                try:
                    logger.info(f"Dialog: {dialog.type} — {dialog.message[:80]}")
                    dialog.accept()
                except Exception as e_accept:
                    # Dialog có thể đã được xử lý/đóng — không để văng lỗi.
                    logger.debug(f"Dialog accept ignored: {e_accept}")

            # Gỡ handler cũ nếu đã đăng ký trước đó
            try:
                if getattr(self, "_dialog_handler", None):
                    self.page.remove_listener("dialog", self._dialog_handler)
            except Exception:
                pass

            self._dialog_handler = on_dialog
            self.page.on("dialog", on_dialog)
            logger.info("Dialog auto-accept enabled")
        except Exception as e:
            logger.debug(f"Dialog setup error: {e}")

    def reload_page(self):
        """Reload trang VnEdu.

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            self.page.reload(wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
            time.sleep(1)
            return True, "Đã reload"
        except Exception as e:
            return False, f"Lỗi reload: {str(e)[:80]}"

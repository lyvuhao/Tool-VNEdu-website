"""JavaScript chạy trong popup "Chi tiết tiết học" (ExtJS 4) của Sổ đầu bài — dùng bởi `fill_form`.

Tách khỏi `form_fill.py` để đọc/sửa từng phần. `JS_FILL_POPUP_FORM` là các phần ghép lại theo đúng thứ tự;
nội dung giống hệt đoạn JS nằm trong `fill_form` trước đây (có test kiểm tra).
"""


# Bước 1: expand / bindStore các combobox lazy-load (Môn học, Phân môn, Xếp loại) để store được nạp.
JS_EXPAND_POPUP_COMBOS = '''() => {
                if (typeof Ext === 'undefined') return {ok: false, msg: 'no_ext'};

                // Tìm popup form "chi tiết tiết học"
                var wins = Ext.ComponentQuery.query('window');
                var popupForm = null;
                for (var i = 0; i < wins.length; i++) {
                    var w = wins[i];
                    if (!w.isVisible || !w.isVisible()) continue;
                    var title = (w.title || '').toLowerCase();
                    if (title.indexOf('chi ti') >= 0 ||
                        title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                        var fp = w.down('form');
                        if (fp && fp.getForm) { popupForm = fp.getForm(); break; }
                    }
                }
                if (!popupForm) return {ok: false, msg: 'popup_not_found'};

                // Tìm các combobox cần expand (store rỗng)
                var comboNames = ['mon_hoc_id', 'phan_mon_id', 'xep_loai'];
                var result = {};
                for (var ci = 0; ci < comboNames.length; ci++) {
                    var cname = comboNames[ci];
                    var field = popupForm.findField(cname);
                    if (!field) { result[cname] = 'not_found'; continue; }

                    // Kiểm tra store hiện tại
                    var store = null;
                    try { store = field.getStore ? field.getStore() : field.store; } catch(eg) {}
                    if (!store) {
                        try { store = field.store; } catch(es) {}
                    }
                    var count = 0;
                    try { count = store ? store.getCount() : 0; } catch(ec) {}

                    if (count > 0) {
                        result[cname] = 'loaded=' + count;
                        continue;
                    }

                    // Store rỗng → Strategy 1: bindStore từ page-level combo
                    var bound = false;
                    try {
                        var allCombos = Ext.ComponentQuery.query('combobox');
                        for (var j = 0; j < allCombos.length; j++) {
                            var src = allCombos[j];
                            if (src === field) continue;
                            var srcName = '';
                            try { srcName = src.getName ? src.getName() : ''; } catch(en) {}
                            if (srcName !== cname) continue;
                            var srcStore = src.getStore ? src.getStore() : null;
                            if (!srcStore || srcStore.getCount() === 0) continue;

                            // Tìm thấy source → bindStore (chia sẻ store)
                            try {
                                field.bindStore(srcStore, true);
                                var newCount = field.getStore().getCount();
                                if (newCount > 0) {
                                    result[cname] = 'bind_ok=' + newCount + ' from #' + src.id;
                                    bound = true;
                                }
                            } catch(eb) {
                                result[cname] = 'bind_err=' + eb.message;
                            }
                            break;
                        }
                    } catch(eAll) {
                        result[cname] = 'search_err=' + eAll.message;
                    }

                    // Strategy 2: Expand dropdown để trigger internal load
                    if (!bound) {
                        try {
                            field.expand();
                            result[cname] = 'expanded';
                        } catch(ex) {
                            result[cname] = 'expand_err=' + ex.message;
                        }
                    }
                }
                return {ok: true, combos: result};
            }'''

# Bước 2: thu gọn combobox đang mở và đọc số record trong store (dùng để chờ store nạp xong).
JS_COLLAPSE_VERIFY_POPUP_COMBOS = '''() => {
                if (typeof Ext === 'undefined') return {ok: false};
                var wins = Ext.ComponentQuery.query('window');
                var popupForm = null;
                for (var i = 0; i < wins.length; i++) {
                    var w = wins[i];
                    if (!w.isVisible || !w.isVisible()) continue;
                    var title = (w.title || '').toLowerCase();
                    if (title.indexOf('chi ti') >= 0 ||
                        title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0) {
                        var fp = w.down('form');
                        if (fp && fp.getForm) { popupForm = fp.getForm(); break; }
                    }
                }
                if (!popupForm) return {ok: false, msg: 'no_form'};

                var comboNames = ['mon_hoc_id', 'phan_mon_id', 'xep_loai'];
                var stores = {};
                for (var ci = 0; ci < comboNames.length; ci++) {
                    var cname = comboNames[ci];
                    var field = popupForm.findField(cname);
                    if (!field) { stores[cname] = -1; continue; }
                    // Collapse nếu đang mở
                    try { if (field.isExpanded) field.collapse(); } catch(ec) {}
                    // Đọc store count
                    var count = 0;
                    try {
                        var st = field.getStore ? field.getStore() : field.store;
                        count = st ? st.getCount() : 0;
                    } catch(e) {}
                    stores[cname] = count;
                }
                return {ok: true, stores: stores};
            }'''


# ---- Bước 3: điền form, chia theo nhóm hàm JS ----

# Mở đầu: hàm async nhận `args`, tiện ích sleep() và normalize() (bỏ dấu, chữ thường).
JS_FILL_PRELUDE = '''async (args) => {
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

'''

# Tìm form ExtJS của popup "Chi tiết tiết học" (dự phòng: form đang hiện bất kỳ).
JS_FILL_FIND_POPUP = '''                function findPopupFormPanel() {
                    let formPanel = null;
                    try {
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
                    } catch (e) {}

                    if (!formPanel) {
                        try {
                            const allForms = Ext.ComponentQuery.query('form');
                            for (const f of allForms) {
                                if (f.isVisible && f.isVisible() && f.getForm) {
                                    formPanel = f;
                                    break;
                                }
                            }
                        } catch (e2) {}
                    }
                    return formPanel;
                }

'''

# Đọc store / tên field / text / danh sách option của combobox.
JS_FILL_STORE_HELPERS = '''                function getFieldStore(field) {
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

                function getComboRawText(field) {
                    if (!field) return '';
                    try { if (field.getRawValue) return String(field.getRawValue() || ''); } catch (e) {}
                    try {
                        if (field.inputEl && field.inputEl.dom) {
                            return String(field.inputEl.dom.value || '');
                        }
                    } catch (e2) {}
                    return '';
                }

                function isComboField(field) {
                    if (!field) return false;
                    try {
                        const xtype = field.getXType ? field.getXType() : (field.xtype || '');
                        if (xtype === 'combobox' || xtype === 'combo') return true;
                    } catch (e) {}
                    try { return !!(field.getStore && typeof field.getStore === 'function'); } catch (e2) {}
                    return false;
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

                function buildOptionsSignature(options) {
                    return (options || [])
                        .slice(0, 30)
                        .map(opt => String(opt.value || '') + '|' + String(opt.text || ''))
                        .join('||');
                }

'''

# Tìm field theo tên ứng viên hoặc nhãn; tìm record trong store theo value/text.
JS_FILL_LOOKUP = '''                function findFieldByCandidates(form, candidates, labelKeywords) {
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

                function findRecordInStore(store, value, expectedText, field) {
                    if (!store) return null;
                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
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

                    if (!record && expectedText) {
                        const expectedNorm = normalize(expectedText);
                        try {
                            store.each(function(rec) {
                                if (record) return;
                                const text = rec.get(displayField) != null
                                    ? rec.get(displayField)
                                    : (rec.get('name') || rec.get('ten') || '');
                                if (normalize(text) === expectedNorm) {
                                    record = rec;
                                }
                            });
                        } catch (e3) {}
                    }
                    return record;
                }

'''

# Chờ store combobox có dữ liệu (bindStore từ combo cùng tên, expand); chờ combo phụ thuộc (Môn -> Phân môn).
JS_FILL_COMBO_LOADING = '''                async function ensureComboStoreLoaded(field, timeoutMs) {
                    if (!field) {
                        return {ok: false, count: 0, diag: 'field_missing'};
                    }

                    let lastDiag = '';
                    const start = Date.now();
                    while ((Date.now() - start) < timeoutMs) {
                        let store = getFieldStore(field);
                        let count = getStoreCount(store);
                        if (count > 0) {
                            try { if (field.isExpanded) field.collapse(); } catch (e) {}
                            return {ok: true, count: count, diag: lastDiag || 'loaded'};
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
                                    store = getFieldStore(field);
                                    count = getStoreCount(store);
                                    if (count > 0) {
                                        return {
                                            ok: true,
                                            count: count,
                                            diag: 'bind:' + (srcCombo.id || srcName || '?')
                                        };
                                    }
                                } catch (bindErr) {
                                    lastDiag = 'bind_err:' + bindErr.message;
                                }
                            }
                        } catch (allErr) {
                            lastDiag = 'search_err:' + allErr.message;
                        }

                        try { if (field.expand) field.expand(); } catch (expandErr) {
                            lastDiag = 'expand_err:' + expandErr.message;
                        }
                        await sleep(150);
                    }

                    const available = getComboOptions(field);
                    return {
                        ok: available.length > 0,
                        count: available.length,
                        diag: lastDiag || 'timeout',
                        available: available.slice(0, 20)
                    };
                }

                async function waitForDependentCombo(parentField, childField, expectedParentValue, timeoutMs) {
                    if (!parentField || !childField) {
                        return {ok: false, count: 0, available: []};
                    }

                    let lastSignature = '';
                    let stableCount = 0;
                    const start = Date.now();
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

                        const signature = options
                            .slice(0, 20)
                            .map(opt => opt.value + '|' + opt.text)
                            .join('||');

                        if (parentValue === String(expectedParentValue) && !loading) {
                            stableCount = (signature && signature === lastSignature)
                                ? stableCount + 1
                                : 1;
                            if (stableCount >= 2) {
                                return {ok: true, count: options.length, available: options.slice(0, 20)};
                            }
                        } else {
                            stableCount = 0;
                        }

                        lastSignature = signature;
                        await sleep(150);
                    }

                    const available = getComboOptions(childField);
                    return {ok: false, count: available.length, available: available.slice(0, 20)};
                }

'''

# Gán giá trị combobox (có xác minh lại value/text) và ô nhập thường (bắn change/blur).
JS_FILL_SETTERS = '''                async function setComboField(field, value, label, config, log, errors) {
                    const cfg = config || {};
                    if (value === null || value === undefined || value === '') {
                        log.push(label + ': skipped');
                        return {ok: true, skipped: true};
                    }

                    if (!field || !isComboField(field)) {
                        const msg = label + ': field not found';
                        log.push(msg);
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    const storeInfo = await ensureComboStoreLoaded(field, 2200);
                    if (!storeInfo.ok) {
                        const msg = label + ': store empty (' + (storeInfo.diag || '?') + ')';
                        log.push(msg);
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    const store = getFieldStore(field);
                    const record = findRecordInStore(store, value, cfg.expectedText, field);
                    if (!record) {
                        const options = getComboOptions(field)
                            .slice(0, 10)
                            .map(opt => opt.text)
                            .join(', ');
                        const msg = label + ': option not found value=' + String(value) +
                            (cfg.expectedText ? ' text=' + cfg.expectedText : '') +
                            (options ? ' | available=' + options : '');
                        log.push(msg);
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    const valueField = field.valueField || 'id';
                    const displayField = field.displayField || 'name';
                    const expectedValue = String(record.get(valueField) != null ? record.get(valueField) : '');
                    const expectedDisplay = String(
                        record.get(displayField) != null
                            ? record.get(displayField)
                            : (cfg.expectedText || '')
                    );

                    field.setValue(record.get(valueField));
                    try { field.lastSelection = [record]; } catch (eLast) {}
                    try { field.fireEvent('select', field, [record]); } catch (eSelect) {}
                    try { field.validate(); } catch (eValidate) {}

                    if (cfg.waitAfterMs) {
                        await sleep(cfg.waitAfterMs);
                    }

                    let afterValue = '';
                    try { afterValue = String(field.getValue() || ''); } catch (eAfter) {}
                    const afterText = getComboRawText(field);
                    const valueOk = afterValue === expectedValue;
                    const textOk = !cfg.expectedText ||
                        normalize(afterText) === normalize(cfg.expectedText) ||
                        normalize(afterText) === normalize(expectedDisplay);

                    log.push(
                        label + ': combo ' + expectedValue + ' -> ' + afterValue +
                        ' [' + (afterText || expectedDisplay) + ']'
                    );

                    if (!valueOk || !textOk) {
                        const msg = label + ': verify failed expected=' + expectedValue +
                            (cfg.expectedText ? '/' + cfg.expectedText : '') +
                            ' got=' + afterValue + '/' + afterText;
                        if (cfg.required) errors.push(msg);
                        return {ok: false};
                    }

                    return {
                        ok: true,
                        value: afterValue,
                        text: afterText || expectedDisplay,
                        fieldName: getFieldName(field)
                    };
                }

                function setPlainField(form, fieldName, value, label, required, log, errors) {
                    if (value === null || value === undefined || value === '') {
                        log.push(label + ': skipped');
                        return false;
                    }

                    try {
                        const field = form.findField(fieldName);
                        if (!field) {
                            log.push(label + ': NOT FOUND (' + fieldName + ')');
                            if (required) errors.push('Field not found: ' + fieldName);
                            return false;
                        }

                        let xtype = '?';
                        try { xtype = field.getXType ? field.getXType() : (field.xtype || '?'); } catch (e) {}

                        field.setValue(value);
                        try {
                            field.fireEvent('change', field, value, field.originalValue);
                            field.fireEvent('blur', field);
                        } catch (e2) {}

                        log.push(
                            label + ': OK [' + xtype + '#' + (field.id || '?') + '] = ' +
                            String(value).substring(0, 30)
                        );
                        return true;
                    } catch (e3) {
                        log.push(label + ': ERROR ' + e3.message);
                        if (required) errors.push(label + ': ' + e3.message);
                        return false;
                    }
                }

'''

# Luồng chính: Môn học -> chờ Phân môn -> Phân môn -> Tiết PPCT -> HS nghỉ -> Nội dung -> Nhận xét -> Điểm -> Xếp loại.
JS_FILL_MAIN = '''                if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                    return {ok: false, errors: ['ExtJS not available'], log: []};
                }

                const formPanel = findPopupFormPanel();
                if (!formPanel || !formPanel.getForm) {
                    return {ok: false, errors: ['Khong tim thay ExtJS form popup'], log: []};
                }

                const form = formPanel.getForm();
                const log = [];
                const errors = [];

                const monField = findFieldByCandidates(form, args.monHocCandidates || [], ['mon hoc']);
                const phanField = findFieldByCandidates(form, ['phan_mon_id'], ['phan mon']);
                const xepLoaiField = findFieldByCandidates(form, ['xep_loai'], ['xep loai']);

                if (args.monHocVal !== null && args.monHocVal !== undefined && args.monHocVal !== '') {
                    const monResult = await setComboField(
                        monField,
                        args.monHocVal,
                        'mon_hoc',
                        {
                            required: true,
                            expectedText: args.monHocText || '',
                            waitAfterMs: 150
                        },
                        log,
                        errors
                    );

                    if (monResult.ok && args.phanMonVal !== null && args.phanMonVal !== undefined &&
                            args.phanMonVal !== '' && phanField) {
                        const waitResult = await waitForDependentCombo(
                            monField,
                            phanField,
                            monResult.value,
                            2600
                        );
                        log.push(
                            'phan_mon_store: ' +
                            (waitResult.ok ? 'ready=' + waitResult.count : 'timeout=' + waitResult.count)
                        );
                    }
                } else {
                    log.push('mon_hoc: skipped (no value)');
                }

                if (args.phanMonVal !== null && args.phanMonVal !== undefined && args.phanMonVal !== '') {
                    await setComboField(
                        phanField,
                        args.phanMonVal,
                        'phan_mon',
                        {
                            required: true,
                            expectedText: args.phanMonText || '',
                            waitAfterMs: 100
                        },
                        log,
                        errors
                    );
                } else {
                    log.push('phan_mon: skipped (no value)');
                }

                const needsAutoNoiDung = (
                    !!args.autoNoiDung
                );

                if (needsAutoNoiDung) {
                    log.push('tiet_ppct: deferred_native_typing = ' + String(args.ppct));
                    log.push('noi_dung: deferred_native_autofill');
                } else {
                    setPlainField(form, 'tiet_ppct', args.ppct, 'tiet_ppct', true, log, errors);
                }
                setPlainField(form, 'soluong_nghi', args.hsNghi, 'soluong_nghi', false, log, errors);

                if (needsAutoNoiDung) {
                    // noi_dung đã được xử lý cùng lúc với sự kiện của tiet_ppct.
                } else {
                    setPlainField(form, 'noi_dung', args.noiDung, 'noi_dung', true, log, errors);
                }

                setPlainField(form, 'nhan_xet', args.nhanXet, 'nhan_xet', false, log, errors);
                setPlainField(form, 'diem', args.diem, 'diem', false, log, errors);

                if (args.xepLoaiVal !== null && args.xepLoaiVal !== undefined && args.xepLoaiVal !== '') {
                    await setComboField(
                        xepLoaiField,
                        args.xepLoaiVal,
                        'xep_loai',
                        {required: false, waitAfterMs: 0},
                        log,
                        errors
                    );
                } else {
                    log.push('xep_loai: skipped (no value)');
                }

                return {
                    ok: errors.length === 0,
                    log: log,
                    errors: errors,
                    formId: formPanel.id || ''
                };
            }'''

JS_FILL_POPUP_FORM = (
    JS_FILL_PRELUDE
    + JS_FILL_FIND_POPUP
    + JS_FILL_STORE_HELPERS
    + JS_FILL_LOOKUP
    + JS_FILL_COMBO_LOADING
    + JS_FILL_SETTERS
    + JS_FILL_MAIN
)

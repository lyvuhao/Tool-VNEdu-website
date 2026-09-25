"""Đọc và điền form nhập liệu."""

import time

from ..compat import PlaywrightTimeout
from .config import logger


class FormFillMixin:
    """Đọc và điền form nhập liệu."""

    # -----------------------------------------------------------------
    # 3.5: FORM OPERATIONS (Nhập liệu + Lưu)
    # -----------------------------------------------------------------

    def inspect_form(self):
        """Khám phá cấu trúc form nhập liệu đang mở.

        Gọi SAU KHI click ➕ để xem form có những field gì.
        Kết quả dùng để debug và tinh chỉnh selectors.

        Returns:
            (success, dict) — form structure info
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const formInfo = {
                    selects: [],
                    inputs: [],
                    textareas: [],
                    buttons: [],
                    modals: [],
                };

                // Scan tất cả visible form elements
                for (const sel of document.querySelectorAll('select')) {
                    if (sel.offsetParent === null) continue;  // Skip hidden
                    const opts = Array.from(sel.options).map(o => ({
                        value: o.value, text: o.text.trim()
                    })).slice(0, 20);
                    formInfo.selects.push({
                        id: sel.id,
                        name: sel.name,
                        options: opts,
                        value: sel.value,
                        label: sel.closest('td, tr')?.querySelector('label, span')?.innerText?.trim()?.substring(0, 50) || ''
                    });
                }

                for (const inp of document.querySelectorAll('input[type="text"], input:not([type])')) {
                    if (inp.offsetParent === null) continue;
                    formInfo.inputs.push({
                        id: inp.id,
                        name: inp.name,
                        value: inp.value,
                        placeholder: inp.placeholder || '',
                        label: inp.closest('td, tr')?.querySelector('label, span')?.innerText?.trim()?.substring(0, 50) || ''
                    });
                }

                for (const ta of document.querySelectorAll('textarea')) {
                    if (ta.offsetParent === null) continue;
                    formInfo.textareas.push({
                        id: ta.id,
                        name: ta.name,
                        value: ta.value,
                        label: ta.closest('td, tr')?.querySelector('label, span')?.innerText?.trim()?.substring(0, 50) || ''
                    });
                }

                for (const btn of document.querySelectorAll(
                    'input[type="button"], input[type="submit"], button, a.btn'
                )) {
                    if (btn.offsetParent === null) continue;
                    formInfo.buttons.push({
                        tag: btn.tagName,
                        id: btn.id,
                        text: (btn.value || btn.innerText || '').trim().substring(0, 30),
                        type: btn.type || ''
                    });
                }

                // Detect modals/dialogs
                for (const m of document.querySelectorAll(
                    '.modal, [role="dialog"], .popup, .ui-dialog, div[id*="pnl"], div[id*="Panel"]'
                )) {
                    if (m.offsetParent === null) continue;
                    formInfo.modals.push({
                        tag: m.tagName,
                        id: m.id,
                        className: (m.className || '').substring(0, 60),
                        visible: true
                    });
                }

                return formInfo;
            }''')

            return True, result

        except Exception as e:
            return False, f"Lỗi inspect form: {type(e).__name__}: {str(e)[:80]}"

    def _fetch_ten_bai_by_ppct(self, ppct, mon_hoc_value, phan_mon_value):
        """Gọi service getPPCT của VnEdu trong page context hiện tại."""
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    try {
                        const token = typeof myToken !== 'undefined'
                            ? (typeof myToken === 'function' ? myToken() : myToken)
                            : '';
                        const userId = typeof myUserId !== 'undefined'
                            ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                            : '';
                        const namHoc = String(
                            typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                        );
                        let lopHocId = '';
                        try {
                            const lopCombo = Ext.ComponentQuery.query('combobox').find(function(combo) {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                return comboName === 'cboLopHoc';
                            });
                            if (lopCombo) {
                                lopHocId = String(
                                    lopCombo.getValue ? (lopCombo.getValue() || '') : (lopCombo.value || '')
                                );
                            }
                        } catch (eLop) {}

                        const params = new URLSearchParams();
                        params.set('my_token', String(token || ''));
                        params.set('my_user_id', String(userId || ''));
                        params.set('app_nam_hoc', namHoc);
                        params.set('mon_hoc_id', String(args.monHocValue || ''));
                        params.set('phan_mon_id', String(args.phanMonValue || ''));
                        params.set('lop_hoc_id', String(lopHocId || ''));
                        params.set('tiet_ppct', String(args.ppct || ''));

                        const url = './?call=app.sodaubai.serv.so_dau_bai.getPPCT'
                            + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                            + '&my_token=' + encodeURIComponent(String(token || ''));

                        const resp = await fetch(url, {
                            method: 'POST',
                            headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                            body: params.toString(),
                            credentials: 'same-origin',
                        });
                        const rawText = await resp.text();
                        let data = null;
                        try {
                            data = JSON.parse(rawText);
                        } catch (eJson) {
                            return {ok: false, error: 'json_parse', rawText: rawText.slice(0, 200)};
                        }
                        const tenBai = data && data.data && data.data.ten_bai
                            ? String(data.data.ten_bai).trim()
                            : '';
                        if (tenBai) {
                            return {ok: true, ten_bai: tenBai};
                        }
                        return {ok: false, error: 'empty_data', payload: data};
                    } catch (eFetch) {
                        return {
                            ok: false,
                            error: String(eFetch && eFetch.message ? eFetch.message : eFetch),
                        };
                    }
                }''',
                {
                    "ppct": str(ppct),
                    "monHocValue": "" if mon_hoc_value is None else str(mon_hoc_value),
                    "phanMonValue": "" if phan_mon_value is None else str(phan_mon_value),
                },
            )
            if result.get("ok"):
                return True, result.get("ten_bai", "")
            return False, result.get("error", "empty_data")
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}"

    def _set_popup_plain_field(self, field_name, value):
        """Set 1 field text/textarea/number trên popup lesson hiện tại."""
        try:
            result = self.page.evaluate(
                '''(args) => {
                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, error: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '').toLowerCase();
                            if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1ebft h\\u1ecdc') < 0) continue;
                            const formPanel = w.down ? w.down('form') : null;
                            if (!formPanel || !formPanel.getForm) continue;
                            const form = formPanel.getForm();
                            const field = form.findField(args.fieldName);
                            if (!field) {
                                return {ok: false, error: 'Field not found: ' + args.fieldName};
                            }
                            field.setValue(args.value);
                            try { field.fireEvent('change', field, args.value, field.originalValue); } catch (e1) {}
                            try { field.fireEvent('blur', field); } catch (e2) {}
                            try { field.validate && field.validate(); } catch (e3) {}
                            return {ok: true};
                        } catch (e) {}
                    }
                    return {ok: false, error: 'popup_not_found'};
                }''',
                {"fieldName": str(field_name), "value": str(value)},
            )
            return bool(result.get("ok")), result.get("error", "")
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}"

    def _native_retype_ppct_and_wait_noi_dung(
        self,
        ppct,
        mon_hoc_value=None,
        phan_mon_value=None,
        timeout_s=1.8,
    ):
        """Gõ thật PPCT bằng keyboard để kích hoạt rule auto-fill của VnEdu."""
        try:
            info = self.page.evaluate(
                '''() => {
                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, error: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '').toLowerCase();
                            if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1ebft h\\u1ecdc') < 0) continue;
                            const formPanel = w.down ? w.down('form') : null;
                            if (!formPanel || !formPanel.getForm) continue;
                            const form = formPanel.getForm();
                            const ppct = form.findField('tiet_ppct');
                            const noi = form.findField('noi_dung');
                            return {
                                ok: !!(ppct && ppct.inputEl && ppct.inputEl.dom && noi),
                                ppctInputId: ppct && ppct.inputEl && ppct.inputEl.dom ? ppct.inputEl.dom.id : '',
                                currentPpct: ppct && ppct.getValue ? ppct.getValue() : '',
                                currentNoiDung: noi && noi.getValue ? noi.getValue() : '',
                            };
                        } catch (e) {}
                    }
                    return {ok: false, error: 'popup_not_found'};
                }'''
            )
            if not info.get("ok"):
                return False, f"Không tìm thấy input PPCT thật: {info.get('error', 'unknown')}", []

            selector = f"#{info.get('ppctInputId', '')}"
            target_text = str(ppct)
            locator = self.page.locator(selector)
            locator.click()
            try:
                self.page.keyboard.press("Control+A")
            except Exception:
                pass
            self.page.keyboard.press("Backspace")
            time.sleep(0.18)
            self.page.keyboard.type(target_text, delay=120)
            self.page.keyboard.press("Tab")

            deadline = time.time() + max(timeout_s, 0.5)
            while time.time() < deadline:
                state = self.page.evaluate(
                    '''() => {
                        if (!(window.Ext && Ext.ComponentQuery)) return {ok: false};
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            try {
                                if (!(w.isVisible && w.isVisible())) continue;
                                const title = String(w.title || '').toLowerCase();
                                if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1ebft h\\u1ecdc') < 0) continue;
                                const formPanel = w.down ? w.down('form') : null;
                                if (!formPanel || !formPanel.getForm) continue;
                                const form = formPanel.getForm();
                                const ppct = form.findField('tiet_ppct');
                                const noi = form.findField('noi_dung');
                                return {
                                    ok: true,
                                    ppct: ppct && ppct.getValue ? ppct.getValue() : '',
                                    noi_dung: noi && noi.getValue ? String(noi.getValue() || '') : '',
                                };
                            } catch (e) {}
                        }
                        return {ok: false};
                    }'''
                )
                noi_dung = str(state.get("noi_dung", "")).strip()
                if noi_dung and noi_dung != "---":
                    return True, noi_dung, [
                        f"tiet_ppct: native-typed = {target_text}",
                        f"noi_dung: auto-filled = {noi_dung[:60]}",
                    ]
                time.sleep(0.08)

            ok_fetch, ten_bai = self._fetch_ten_bai_by_ppct(
                ppct,
                mon_hoc_value=mon_hoc_value,
                phan_mon_value=phan_mon_value,
            )
            if ok_fetch and ten_bai:
                ok_set, set_err = self._set_popup_plain_field("noi_dung", ten_bai)
                if ok_set:
                    return True, ten_bai, [
                        f"tiet_ppct: native-typed = {target_text}",
                        f"noi_dung: fetched_by_getPPCT = {ten_bai[:60]}",
                    ]
                return False, f"Không set được tên bài từ getPPCT: {set_err}", []

            return False, (
                "VnEdu không tự hiện tên bài sau khi nhập PPCT "
                "và getPPCT cũng không trả dữ liệu"
            ), [f"tiet_ppct: native-typed = {target_text}"]
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}", []

    def fill_form(self, ppct, hs_nghi, nhan_xet, diem, phan_mon_index=None,
                 xep_loai=None, noi_dung=None, mon_hoc_index=None,
                 phan_mon_text=None, mon_hoc_text=None, mon_hoc_field=None):
        """Nhập liệu vào ExtJS form popup đang mở.

        VnEdu v5 dùng ExtJS 4.x form panel — tất cả field là ExtJS component.
        Dùng Ext form API: form.getForm().findField(name).setValue(value).

        Thứ tự fields trên VnEdu form (chỉ điền các field cần thiết):
        0. mon_hoc_id (combobox) — Môn học (optional, set trước Phân môn)
        1. phan_mon_id (combobox) — chọn theo value nếu cung cấp
        2. tiet_ppct (numberfield) — tiết PPCT
        3. soluong_nghi (numberfield) — số HS nghỉ
        4. noi_dung (textareafield) — tên bài / nội dung (BẮT BUỘC)
        5. nhan_xet (textareafield) — nhận xét giáo viên
        6. diem (numberfield) — điểm tiết học
        7. xep_loai (combobox) — xếp loại (optional)

        Args:
            ppct: str — giá trị tiết PPCT
            hs_nghi: str — số HS nghỉ
            nhan_xet: str — nhận xét giáo viên
            diem: str — điểm tiết học
            phan_mon_index: str|int|None — value Phân môn combobox (None = giữ nguyên)
            xep_loai: str|None — value Xếp loại combobox (None = giữ nguyên)
            noi_dung: str|None — tên bài / nội dung (None = auto-fill check)
            mon_hoc_index: str|int|None — value Môn học combobox (None = giữ nguyên)
            phan_mon_text: str|None — text Phân môn để verify sau khi set
            mon_hoc_text: str|None — text Môn học để verify sau khi set
            mon_hoc_field: str|None — tên field Môn học đã discover khi scan

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            mon_hoc_candidates = []
            if mon_hoc_field:
                mon_hoc_candidates.append(str(mon_hoc_field))
            for name in (
                "mon_hoc_id", "mon_hoc", "monhoc_id", "monhoc",
                "subject_id", "ma_mon_hoc"
            ):
                if name not in mon_hoc_candidates:
                    mon_hoc_candidates.append(name)

            # === Pre-fill: EXPAND combobox dropdowns để trigger store load ===
            # VnEdu popup có lazy-loaded combobox stores — store chỉ load khi
            # user mở dropdown lần đầu. Phải expand() trước → chờ store load
            # → rồi mới setValue() được.
            _expand_combos_js = '''() => {
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

            # Bước 1: Expand/bind combos
            # Khởi tạo trước để tránh NameError nếu evaluate ném exception
            # (popup đóng sớm / "Execution context was destroyed").
            expand_result = None
            try:
                expand_result = self.page.evaluate(_expand_combos_js)
                logger.info(f"Pre-fill expand: {expand_result}")
            except Exception as e_exp:
                logger.debug(f"Pre-fill expand error: {e_exp}")

            # Bước 2: Chờ store load nếu combo thật sự còn lazy-load.
            combo_states = {}
            if isinstance(expand_result, dict):
                combo_states = expand_result.get("combos", {}) or {}
            expanded_combo_names = [
                name for name, state in combo_states.items()
                if str(state).startswith("expanded")
            ]
            needs_combo_wait = any(
                str(state).startswith("expanded")
                for state in combo_states.values()
            )

            # Bước 3: Collapse any expanded combos + verify store counts
            _collapse_verify_js = '''() => {
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

            try:
                verify_result = None
                deadline = time.time() + (0.9 if needs_combo_wait else 0.2)
                while time.time() < deadline:
                    verify_result = self.page.evaluate(_collapse_verify_js)
                    stores = {}
                    if isinstance(verify_result, dict):
                        stores = verify_result.get("stores", {}) or {}
                    if not expanded_combo_names:
                        break
                    all_ready = True
                    for combo_name in expanded_combo_names:
                        count = int(stores.get(combo_name, 0) or 0)
                        if count <= 0:
                            all_ready = False
                            break
                    if all_ready:
                        break
                    time.sleep(0.06)
                logger.info(f"Pre-fill verify: {verify_result}")
            except Exception as e_ver:
                logger.debug(f"Pre-fill verify error: {e_ver}")

            payload = {
                "ppct": str(ppct),
                "hsNghi": str(hs_nghi),
                "nhanXet": str(nhan_xet),
                "diem": str(diem),
                "phanMonVal": (
                    str(phan_mon_index) if phan_mon_index is not None else None
                ),
                "phanMonText": (
                    str(phan_mon_text) if phan_mon_text is not None else None
                ),
                "xepLoaiVal": str(xep_loai) if xep_loai is not None else None,
                "noiDung": str(noi_dung) if noi_dung is not None else None,
                "monHocVal": (
                    str(mon_hoc_index) if mon_hoc_index is not None else None
                ),
                "monHocText": (
                    str(mon_hoc_text) if mon_hoc_text is not None else None
                ),
                "monHocCandidates": mon_hoc_candidates,
                "autoNoiDung": (
                    noi_dung == "---" or
                    noi_dung is None or
                    noi_dung == ""
                ),
            }

            verified_result = self.page.evaluate('''async (args) => {
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

                async function ensureComboStoreLoaded(field, timeoutMs) {
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

                async function setComboField(field, value, label, config, log, errors) {
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

                if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
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
            }''', payload)

            if verified_result.get("ok"):
                log_entries = list(verified_result.get("log", []))
                auto_noi_dung = bool(payload.get("autoNoiDung"))
                if auto_noi_dung:
                    native_ok, native_msg, native_logs = self._native_retype_ppct_and_wait_noi_dung(
                        ppct=str(ppct),
                        mon_hoc_value=mon_hoc_index,
                        phan_mon_value=phan_mon_index,
                        timeout_s=1.8,
                    )
                    log_entries = [
                        entry for entry in log_entries
                        if "deferred_native_" not in entry
                    ]
                    log_entries.extend(native_logs)
                    if not native_ok:
                        logger.warning(
                            "Native PPCT autofill failed: %s | log=%s",
                            native_msg,
                            log_entries,
                        )
                        return False, f"Lỗi nhập form: {native_msg}"

                logger.info(f"Form filled: {', '.join(log_entries)}")
                return True, f"Đã nhập: {', '.join(log_entries)}"

            verify_errors = verified_result.get("errors", [])
            verify_logs = verified_result.get("log", [])
            logger.warning(
                f"Verified form fill failed: {verify_errors}, log: {verify_logs}"
            )
            return False, f"Lỗi nhập form: {'; '.join(verify_errors)}"

        except PlaywrightTimeout:
            return False, "Timeout nhập form"
        except Exception as e:
            return False, f"Lỗi nhập form: {type(e).__name__}: {str(e)[:80]}"

"""Đọc và điền form nhập liệu."""

import time

from ..compat import PlaywrightTimeout
from .config import logger
from .form_fill_js import (
    JS_COLLAPSE_VERIFY_POPUP_COMBOS,
    JS_EXPAND_POPUP_COMBOS,
    JS_FILL_POPUP_FORM,
)


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

        Các bước (mỗi bước là một hàm riêng để dễ debug):
            1. `_mon_hoc_field_candidates` — tên field Môn học có thể có.
            2. `_prime_popup_combo_stores` — expand/bindStore combobox lazy-load rồi chờ store nạp.
            3. `_build_fill_form_payload` — gom tham số gửi xuống JS.
            4. `JS_FILL_POPUP_FORM` (form_fill_js.py) — điền + xác minh từng field trong trình duyệt.
            5. `_finish_fill_form` — tên bài tự điền theo PPCT (gõ lại PPCT) và dựng thông điệp kết quả.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            mon_hoc_candidates = self._mon_hoc_field_candidates(mon_hoc_field)
            self._prime_popup_combo_stores()
            payload = self._build_fill_form_payload(
                ppct, hs_nghi, nhan_xet, diem,
                phan_mon_index=phan_mon_index,
                xep_loai=xep_loai,
                noi_dung=noi_dung,
                mon_hoc_index=mon_hoc_index,
                phan_mon_text=phan_mon_text,
                mon_hoc_text=mon_hoc_text,
                mon_hoc_candidates=mon_hoc_candidates,
            )
            verified_result = self.page.evaluate(JS_FILL_POPUP_FORM, payload)

            if verified_result.get("ok"):
                return self._finish_fill_form(
                    verified_result, payload, ppct, mon_hoc_index, phan_mon_index
                )

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

    # ---- Các bước của fill_form ----------------------------------------

    @staticmethod
    def _mon_hoc_field_candidates(mon_hoc_field=None):
        """Bước 1: tên field Môn học (field đã dò khi quét form được thử trước)."""
        mon_hoc_candidates = []
        if mon_hoc_field:
            mon_hoc_candidates.append(str(mon_hoc_field))
        for name in (
            "mon_hoc_id", "mon_hoc", "monhoc_id", "monhoc",
            "subject_id", "ma_mon_hoc"
        ):
            if name not in mon_hoc_candidates:
                mon_hoc_candidates.append(name)
        return mon_hoc_candidates

    def _prime_popup_combo_stores(self):
        """Bước 2: nạp store các combobox lazy-load của popup trước khi setValue().

        VnEdu popup có lazy-loaded combobox stores — store chỉ load khi user mở
        dropdown lần đầu. Phải expand()/bindStore trước → chờ store load → rồi mới
        setValue() được. Lỗi ở bước này chỉ ghi log, không làm hỏng lần nhập.

        Returns:
            (expand_result, verify_result) — để debug / ghi log.
        """
        # Khởi tạo trước để tránh NameError nếu evaluate ném exception
        # (popup đóng sớm / "Execution context was destroyed").
        expand_result = None
        try:
            expand_result = self.page.evaluate(JS_EXPAND_POPUP_COMBOS)
            logger.info(f"Pre-fill expand: {expand_result}")
        except Exception as e_exp:
            logger.debug(f"Pre-fill expand error: {e_exp}")

        # Chờ store load nếu combo thật sự còn lazy-load.
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
        verify_result = self._wait_popup_combo_stores(expanded_combo_names, needs_combo_wait)
        return expand_result, verify_result

    def _wait_popup_combo_stores(self, expanded_combo_names, needs_combo_wait):
        """Thu gọn combobox đang mở và chờ (tối đa 0.9s) tới khi store của các combo vừa expand có dữ liệu."""
        verify_result = None
        try:
            deadline = time.time() + (0.9 if needs_combo_wait else 0.2)
            while time.time() < deadline:
                verify_result = self.page.evaluate(JS_COLLAPSE_VERIFY_POPUP_COMBOS)
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
        return verify_result

    @staticmethod
    def _build_fill_form_payload(ppct, hs_nghi, nhan_xet, diem, *, phan_mon_index=None,
                                 xep_loai=None, noi_dung=None, mon_hoc_index=None,
                                 phan_mon_text=None, mon_hoc_text=None, mon_hoc_candidates=()):
        """Bước 3: tham số cho JS_FILL_POPUP_FORM (None = giữ nguyên field trên web).

        `autoNoiDung`: nội dung trống / "---" → để VnEdu tự điền tên bài theo PPCT (xem bước 5).
        """
        return {
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
            "monHocCandidates": list(mon_hoc_candidates),
            "autoNoiDung": (
                noi_dung == "---" or
                noi_dung is None or
                noi_dung == ""
            ),
        }

    def _finish_fill_form(self, verified_result, payload, ppct, mon_hoc_index, phan_mon_index):
        """Bước 5: nếu cần tên bài tự điền thì gõ lại PPCT kiểu người dùng và chờ ô Nội dung; dựng kết quả."""
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

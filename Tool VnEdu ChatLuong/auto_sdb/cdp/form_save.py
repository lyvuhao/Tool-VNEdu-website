"""Lưu form (qua giao diện hoặc API)."""

import unicodedata

from ..compat import PlaywrightTimeout
from .config import logger


class FormSaveMixin:
    """Lưu form (qua giao diện hoặc API)."""

    def save_form(self):
        """Click nút Lưu trên ExtJS form popup + xử lý dialog xác nhận.

        VnEdu v5 dùng ExtJS button — tìm qua Ext.ComponentQuery hoặc DOM.
        Sau click: chờ dialog confirm → auto accept → chờ form đóng.

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            # Đăng ký dialog handler TRƯỚC khi click Lưu
            self._handle_dialogs()

            result = self.page.evaluate('''() => {
                // === Pre-save: Kiểm tra form validation trước khi click Lưu ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        var chkWins = Ext.ComponentQuery.query('window');
                        for (var ci = 0; ci < chkWins.length; ci++) {
                            var cw = chkWins[ci];
                            if (!cw.isVisible || !cw.isVisible()) continue;
                            var ct = (cw.title || '').toLowerCase();
                            if (ct.indexOf('chi ti') < 0 && ct.indexOf('ti\\u1EBFt h\\u1ECDc') < 0) continue;
                            var cfp = cw.down('form');
                            if (cfp && cfp.getForm) {
                                var cForm = cfp.getForm();
                                if (!cForm.isValid()) {
                                    var invalids = [];
                                    try {
                                        cForm.getFields().each(function(ff) {
                                            if (ff.isValid && !ff.isValid()) {
                                                var errLabel = ff.fieldLabel || ff.getName() || '?';
                                                var errMsgs = ff.getErrors ? ff.getErrors().join(', ') : 'invalid';
                                                invalids.push(errLabel + ': ' + errMsgs);
                                            }
                                        });
                                    } catch(efv) {}
                                    return {
                                        ok: false,
                                        error: 'Validation: ' + invalids.join('; '),
                                        validation: true,
                                        invalidFields: invalids
                                    };
                                }
                            }
                            break;
                        }
                    } catch(evc) {}
                }

                // === Chiến lược 1: Tìm ExtJS button "Lưu" trong window popup ===
                if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                    try {
                        // Tìm window popup "chi tiết tiết học"
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            if (!w.isVisible || !w.isVisible()) continue;
                            const title = w.title || '';
                            if (title.indexOf('chi ti') < 0 && title.indexOf('ti\\u1EBFt h\\u1ECDc') < 0) continue;

                            // Tìm button "Lưu" trong window
                            const btns = w.query('button');
                            for (const btn of btns) {
                                const text = (btn.text || '').trim();
                                if (text === 'L\\u01B0u' || text === 'Luu' || text.toLowerCase() === 'save') {
                                    // Click qua ExtJS handler
                                    if (btn.handler) {
                                        btn.handler.call(btn.scope || btn, btn);
                                    } else if (btn.el && btn.el.dom) {
                                        btn.el.dom.click();
                                    }
                                    return {
                                        ok: true,
                                        method: 'extjs_handler',
                                        btnText: text,
                                        btnId: btn.id || ''
                                    };
                                }
                            }
                        }
                    } catch(e) {
                        // ExtJS error → fallback
                    }
                }

                // === Chiến lược 2: Fallback — tìm DOM button "Lưu" ===
                const saveKeywords = ['l\\u01B0u', 'luu', 'save'];
                let saveBtn = null;

                for (const btn of document.querySelectorAll('button, input[type="button"], input[type="submit"]')) {
                    if (btn.offsetParent === null) continue;
                    const text = (btn.value || btn.innerText || '').toLowerCase().trim();
                    for (const kw of saveKeywords) {
                        if (text.includes(kw)) {
                            saveBtn = btn;
                            break;
                        }
                    }
                    if (saveBtn) break;
                }

                if (!saveBtn) {
                    return {ok: false, error: 'Khong tim thay nut Luu'};
                }

                saveBtn.click();
                return {
                    ok: true,
                    method: 'dom_click',
                    btnText: (saveBtn.value || saveBtn.innerText || '').trim().substring(0, 20),
                    btnId: saveBtn.id || ''
                };
            }''')

            if not result.get("ok"):
                err_msg = result.get("error", "Unknown")
                # Validation error: trả về chi tiết fields lỗi
                if result.get("validation"):
                    invalid_fields = result.get("invalidFields", [])
                    logger.warning(f"Save blocked — form validation failed: {invalid_fields}")
                    return False, f"Validation lỗi: {err_msg}"
                return False, err_msg

            method = result.get("method", "?")
            btn_text = result.get("btnText", "?")
            logger.info(f"Save clicked: method={method}, btn={btn_text}")

            wait_result = self.page.evaluate('''async (args) => {
                const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                function isLessonTitle(title) {
                    const t = String(title || '').toLowerCase();
                    return (
                        t.indexOf('chi ti') >= 0 ||
                        t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                        t.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                        t.indexOf('so dau bai') >= 0
                    );
                }

                function collectWindows() {
                    const wins = [];
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return wins;
                    }
                    const extWins = Ext.ComponentQuery.query('window');
                    for (const w of extWins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            wins.push({
                                title: String(w.title || ''),
                                lesson: isLessonTitle(w.title || ''),
                                xtype: String(w.xtype || ''),
                            });
                        } catch (e) {}
                    }
                    return wins;
                }

                function clickMessageOk() {
                    const actions = [];
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return actions;
                    }

                    try {
                        if (Ext.Msg && Ext.Msg.isVisible && Ext.Msg.isVisible()) {
                            const btns = Ext.Msg.query('button');
                            for (const btn of btns) {
                                const txt = String(btn.text || '').trim();
                                if (txt === 'OK' || txt === 'Yes') {
                                    if (btn.el && btn.el.dom) btn.el.dom.click();
                                    actions.push('ExtMsg:' + txt);
                                    return actions;
                                }
                            }
                            if (btns.length > 0 && btns[0].el && btns[0].el.dom) {
                                btns[0].el.dom.click();
                                actions.push('ExtMsg:first');
                                return actions;
                            }
                        }
                    } catch (e1) {}

                    try {
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '');
                            const tLow = title.toLowerCase();
                            if (isLessonTitle(title)) continue;
                            if (
                                tLow.indexOf('th\\u00f4ng b\\u00e1o') >= 0 ||
                                tLow.indexOf('thong bao') >= 0 ||
                                tLow.indexOf('notification') >= 0 ||
                                tLow.indexOf('success') >= 0 ||
                                tLow.indexOf('x\\u00e1c nh\\u1EADn') >= 0 ||
                                tLow.indexOf('confirm') >= 0
                            ) {
                                const btns = w.query('button');
                                for (const btn of btns) {
                                    if (btn.el && btn.el.dom) {
                                        btn.el.dom.click();
                                        actions.push('Window:' + (btn.text || 'button'));
                                        return actions;
                                    }
                                }
                                try {
                                    w.close();
                                    actions.push('Window:close');
                                    return actions;
                                } catch (e2) {}
                            }
                        }
                    } catch (e3) {}

                    return actions;
                }

                let actionLog = [];
                let lastWindows = [];
                const deadline = Date.now() + Math.max(args.timeoutMs || 0, 1800);
                while (Date.now() < deadline) {
                    const clicked = clickMessageOk();
                    if (clicked.length) {
                        actionLog = actionLog.concat(clicked);
                    }

                    const wins = collectWindows();
                    lastWindows = wins;
                    const lessonOpen = wins.some(item => item.lesson);
                    const loadMaskVisible = !!document.querySelector('.x-mask-msg[style*="visible"], .x-mask-loading[style*="visible"]');
                    if (!lessonOpen && !loadMaskVisible) {
                        return {
                            ok: true,
                            actions: actionLog,
                            windows: wins.map(item => item.title),
                        };
                    }
                    await sleep(args.pollMs || 120);
                }

                return {
                    ok: false,
                    actions: actionLog,
                    windows: lastWindows.map(item => item.title),
                };
            }''', {"timeoutMs": 3200, "pollMs": 110})

            if wait_result.get("ok"):
                logger.info(
                    "Form saved + closed successfully "
                    f"(actions={wait_result.get('actions', [])})"
                )
                return True, "Đã lưu thành công"

            closed_ok, close_msg = self.wait_for_lesson_form_closed(
                timeout_s=0.8,
                poll_interval=0.06,
            )
            if closed_ok:
                logger.info(
                    "Form closed shortly after save wait timeout "
                    f"(actions={wait_result.get('actions', [])})"
                )
                return True, "Đã lưu thành công"

            logger.warning(
                "Form vẫn mở sau save wait: "
                f"windows={wait_result.get('windows', [])}, close={close_msg}"
            )
            return False, "Form vẫn mở sau khi lưu (có thể lỗi validation)"

        except PlaywrightTimeout:
            return False, "Timeout lưu form"
        except Exception as e:
            return False, f"Lỗi lưu form: {type(e).__name__}: {str(e)[:80]}"

    def save_form_via_api(self, buoi_hoc=None):
        """Lưu trực tiếp qua service nội bộ của VnEdu trong page context hiện tại.

        Ưu tiên đường API-first để giảm độ trễ popup/dialog. Method này chỉ dùng
        session/cookie đang có trong Chrome, không dùng requests ngoài trình duyệt.

        Args:
            buoi_hoc: str|None — giá trị buổi hiện tại của slot ("Sáng"/"Chiều")

        Returns:
            (success: bool, message: str, meta: dict)
            meta chứa:
                - method: str
                - request_sent: bool
                - server_msg: str
                - used_api: bool
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP", {
                "method": "api_precheck",
                "request_sent": False,
                "used_api": False,
            }

        buoi_text = str(buoi_hoc or "").strip()
        if not buoi_text:
            return False, "Thiếu buổi học cho API save", {
                "method": "api_precheck",
                "request_sent": False,
                "used_api": False,
            }

        buoi_norm = unicodedata.normalize("NFD", buoi_text)
        buoi_norm = "".join(ch for ch in buoi_norm if unicodedata.category(ch) != "Mn")
        buoi_norm = buoi_norm.strip().lower()
        if buoi_norm in {"sang", "1"}:
            buoi_payload = "1"
        elif buoi_norm in {"chieu", "2"}:
            buoi_payload = "2"
        else:
            buoi_payload = buoi_text

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

                    function isLessonTitle(title) {
                        const t = String(title || '').toLowerCase();
                        return (
                            t.indexOf('chi ti') >= 0 ||
                            t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0
                        );
                    }

                    function getCombo(name) {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    }

                    function getVisibleLessonWindow() {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        const wins = Ext.ComponentQuery.query('window');
                        for (const w of wins) {
                            try {
                                if (!(w.isVisible && w.isVisible())) continue;
                                if (!isLessonTitle(w.title || '')) continue;
                                const formPanel = w.down ? w.down('form') : null;
                                if (formPanel && formPanel.getForm) return w;
                            } catch (e) {}
                        }
                        return null;
                    }

                    const lessonWin = getVisibleLessonWindow();
                    if (!lessonWin) {
                        return {
                            ok: false,
                            error: 'Không tìm thấy popup chi tiết tiết học',
                            request_sent: false,
                            method: 'api_prepare'
                        };
                    }

                    const formPanel = lessonWin.down ? lessonWin.down('form') : null;
                    if (!formPanel || !formPanel.getForm) {
                        return {
                            ok: false,
                            error: 'Popup không có form hợp lệ',
                            request_sent: false,
                            method: 'api_prepare'
                        };
                    }

                    const form = formPanel.getForm();
                    let stableState = {
                        valid: false,
                        invalidFields: [],
                        loadMaskVisible: false,
                        comboLoading: false,
                    };
                    const stableDeadline = Date.now() + Math.max(parseInt(args.stableWaitMs || 0, 10), 600);
                    while (Date.now() < stableDeadline) {
                        let invalids = [];
                        let loadMaskVisible = false;
                        let comboLoading = false;
                        let valid = false;
                        try {
                            const masks = document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask');
                            for (const mask of masks) {
                                try {
                                    if (!mask) continue;
                                    const style = window.getComputedStyle ? window.getComputedStyle(mask) : null;
                                    if (
                                        style &&
                                        style.display !== 'none' &&
                                        style.visibility !== 'hidden' &&
                                        parseFloat(style.opacity || '1') > 0
                                    ) {
                                        loadMaskVisible = true;
                                        break;
                                    }
                                } catch (eMask) {}
                            }
                        } catch (eMaskAll) {}

                        try {
                            const comboFields = formPanel.query ? formPanel.query('combobox') : [];
                            for (const field of comboFields) {
                                try {
                                    const store = field.getStore ? field.getStore() : field.store;
                                    if (store && store.isLoading && store.isLoading()) {
                                        comboLoading = true;
                                        break;
                                    }
                                } catch (eStore) {}
                            }
                        } catch (eCombo) {}

                        try {
                            valid = !!form.isValid();
                        } catch (eValid) {
                            valid = false;
                        }

                        if (!valid) {
                            try {
                                form.getFields().each(function(field) {
                                    if (field.isValid && !field.isValid()) {
                                        const label = field.fieldLabel || field.getName() || '?';
                                        const errs = field.getErrors ? field.getErrors().join(', ') : 'invalid';
                                        invalids.push(label + ': ' + errs);
                                    }
                                });
                            } catch (eFields) {}
                        }

                        stableState = {
                            valid: valid,
                            invalidFields: invalids,
                            loadMaskVisible: loadMaskVisible,
                            comboLoading: comboLoading,
                        };

                        if (valid && !comboLoading && !loadMaskVisible) {
                            break;
                        }

                        await sleep(args.stablePollMs || 120);
                    }

                    if (!stableState.valid) {
                        const invalids = [];
                        invalids.push.apply(invalids, stableState.invalidFields || []);
                        return {
                            ok: false,
                            error: 'Validation: ' + invalids.join('; '),
                            validation: true,
                            invalidFields: invalids,
                            request_sent: false,
                            method: 'api_prepare',
                            readiness: {
                                loadmask: !!stableState.loadMaskVisible,
                                combo_loading: !!stableState.comboLoading,
                            }
                        };
                    }

                    if (!(window.$ && $.ajax)) {
                        return {
                            ok: false,
                            error: 'jQuery.ajax không sẵn sàng',
                            request_sent: false,
                            method: 'api_prepare'
                        };
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const weekCombo = getCombo('cboTuanHoc');
                    const classCombo = getCombo('cboLopHoc');
                    const capHoc = capCombo && capCombo.getValue ? capCombo.getValue() : '';
                    const tuanHoc = weekCombo && weekCombo.getValue ? weekCombo.getValue() : '';
                    const lopHocId = classCombo && classCombo.getValue ? classCombo.getValue() : '';

                    if (capHoc === '' || tuanHoc === '' || lopHocId === '') {
                        return {
                            ok: false,
                            error: 'Thiếu context lớp/tuần/cấp học cho API save',
                            request_sent: false,
                            method: 'api_prepare',
                            context: {
                                cap_hoc: capHoc,
                                tuan_hoc: tuanHoc,
                                lop_hoc_id: lopHocId,
                            }
                        };
                    }

                    const payload = form.getValues();
                    payload.cap_hoc = capHoc;
                    payload.lop_hoc_id = lopHocId;
                    payload.tuan_hoc = tuanHoc;
                    payload.buoi_hoc = args.buoiHoc;

                    const basePath = (
                        typeof applicationPath !== 'undefined' && applicationPath
                    ) ? String(applicationPath) : './';
                    const url = basePath + '?call=app.sodaubai.serv.so_dau_bai.save';
                    let requestSent = false;
                    let ajaxResult = null;
                    const winId = lessonWin.id || '';

                    try {
                        if (window.Ext && Ext.util && Ext.util.Mask && typeof Ext.util.Mask.show === 'function' && winId) {
                            Ext.util.Mask.show(winId);
                        }
                    } catch (eMaskShow) {}

                    try {
                        ajaxResult = await new Promise((resolve) => {
                            requestSent = true;
                            $.ajax({
                                url: url,
                                type: 'post',
                                data: payload,
                                success: function (rs) {
                                    resolve({
                                        ok: true,
                                        raw: rs,
                                    });
                                },
                                error: function (xhr, ajaxOptions, thrownError) {
                                    resolve({
                                        ok: false,
                                        status: xhr && typeof xhr.status !== 'undefined' ? xhr.status : null,
                                        error: String(thrownError || (xhr && xhr.statusText) || 'ajax_error'),
                                        responseText: xhr && xhr.responseText ? String(xhr.responseText).slice(0, 200) : '',
                                    });
                                }
                            });
                        });
                    } finally {
                        try {
                            if (window.Ext && Ext.util && Ext.util.Mask && typeof Ext.util.Mask.hide === 'function' && winId) {
                                Ext.util.Mask.hide(winId);
                            }
                        } catch (eMaskHide) {}
                    }

                    if (!ajaxResult || !ajaxResult.ok) {
                        return {
                            ok: false,
                            error: ajaxResult && ajaxResult.error ? ajaxResult.error : 'ajax_error',
                            status_code: ajaxResult && ajaxResult.status,
                            response_text: ajaxResult && ajaxResult.responseText,
                            request_sent: requestSent,
                            method: 'api_ajax'
                        };
                    }

                    let data = ajaxResult.raw;
                    if (typeof data === 'string') {
                        try {
                            data = JSON.parse(data);
                        } catch (eJson) {
                            return {
                                ok: false,
                                error: 'JSON parse lỗi: ' + String(eJson && eJson.message ? eJson.message : eJson),
                                raw: String(ajaxResult.raw).slice(0, 200),
                                request_sent: requestSent,
                                method: 'api_parse'
                            };
                        }
                    }

                    if (!data || !data.success) {
                        return {
                            ok: false,
                            error: data && data.msg ? String(data.msg) : 'Server báo lưu thất bại',
                            payload: data || null,
                            request_sent: requestSent,
                            method: 'api_response'
                        };
                    }

                    // Không tự click Refresh sau từng tiết. Worker xác minh bằng
                    // service fetch; refresh UI liên tục làm trang load lặp và có
                    // thể để lại mask/combobox ở trạng thái khó thao tác.
                    let refreshClicked = false;

                    let popupClosed = false;
                    try {
                        lessonWin.close();
                        popupClosed = true;
                    } catch (eClose) {
                        try {
                            if (lessonWin.hide) {
                                lessonWin.hide();
                                popupClosed = true;
                            }
                        } catch (eHide) {}
                    }

                    return {
                        ok: true,
                        method: 'api_ajax',
                        request_sent: requestSent,
                        refresh_clicked: refreshClicked,
                        popup_closed: popupClosed,
                        server_msg: data && data.msg ? String(data.msg) : '',
                    };
                }''',
                {"buoiHoc": buoi_payload, "stableWaitMs": 1800, "stablePollMs": 120},
            )

            if result.get("ok"):
                closed_ok, close_msg = self.wait_for_lesson_form_closed(
                    timeout_s=0.8,
                    poll_interval=0.06,
                )
                logger.info(
                    "API save success: "
                    f"refresh={result.get('refresh_clicked')}, "
                    f"popup_closed={result.get('popup_closed')}, "
                    f"close_wait={closed_ok}:{close_msg}"
                )
                return True, "Đã lưu thành công", {
                    "method": result.get("method", "api_ajax"),
                    "request_sent": bool(result.get("request_sent", False)),
                    "server_msg": result.get("server_msg", ""),
                    "used_api": True,
                }

            err_msg = result.get("error", "API save thất bại")
            if result.get("validation"):
                invalid_fields = result.get("invalidFields", [])
                logger.warning(f"API save blocked — form validation failed: {invalid_fields}")
                return False, f"Validation lỗi: {err_msg}", {
                    "method": result.get("method", "api_prepare"),
                    "request_sent": bool(result.get("request_sent", False)),
                    "server_msg": "",
                    "used_api": True,
                    "validation": True,
                }

            logger.warning(
                "API save failed: "
                f"method={result.get('method')}, "
                f"request_sent={result.get('request_sent')}, "
                f"error={err_msg}"
            )
            return False, err_msg, {
                "method": result.get("method", "api_unknown"),
                "request_sent": bool(result.get("request_sent", False)),
                "server_msg": result.get("error", ""),
                "used_api": True,
            }

        except PlaywrightTimeout:
            return False, "Timeout lưu form qua API", {
                "method": "api_timeout",
                "request_sent": False,
                "used_api": True,
            }
        except Exception as e:
            return False, f"Lỗi lưu form qua API: {type(e).__name__}: {str(e)[:80]}", {
                "method": "api_exception",
                "request_sent": False,
                "used_api": True,
            }

    def save_form_auto(self, buoi_hoc=None):
        """Ưu tiên API-first save; chỉ fallback click-save nếu request chưa hề gửi."""
        api_ok, api_msg, api_meta = self.save_form_via_api(buoi_hoc=buoi_hoc)
        if api_ok:
            return True, api_msg, {
                "method": api_meta.get("method", "api_ajax"),
                "used_api": True,
                "request_sent": bool(api_meta.get("request_sent", False)),
                "fallback_used": False,
            }

        if api_meta.get("validation"):
            return False, api_msg, {
                "method": api_meta.get("method", "api_validation"),
                "used_api": True,
                "request_sent": False,
                "fallback_used": False,
            }

        request_sent = bool(api_meta.get("request_sent", False))
        if request_sent:
            return False, api_msg, {
                "method": api_meta.get("method", "api_failed"),
                "used_api": True,
                "request_sent": True,
                "fallback_used": False,
            }

        logger.info(
            "API save skipped/fallback to legacy click-save: "
            f"{api_msg} ({api_meta.get('method', 'api_precheck')})"
        )
        legacy_ok, legacy_msg = self.save_form()
        return legacy_ok, legacy_msg, {
            "method": "legacy_click_save",
            "used_api": False,
            "request_sent": False,
            "fallback_used": True,
            "api_message": api_msg,
        }

    def _handle_dialogs(self):
        """Đảm bảo dialog auto-accept đã được đăng ký (idempotent).

        VnEdu thường hiện confirm "Bạn có chắc chắn muốn lưu?" hoặc alert
        sau khi click Lưu. Phải đăng ký handler TRƯỚC khi click.
        Gọi method này trước mỗi action có thể trigger dialog — nó ủy quyền
        cho setup_dialog_auto_accept để KHÔNG tạo listener chồng nhau.
        """
        self.setup_dialog_auto_accept()

"""Các đoạn JavaScript chạy trên trang KHDH (đọc context, fetch, lưu...)."""

from __future__ import annotations


# ---------------------------------------------------------------
# JS snippets
# ---------------------------------------------------------------

# Đọc context từ ExtJS components
_JS_GET_CONTEXT = r"""
() => {
    const out = {
        my_token: window.my_token || '',
        my_user_id: (window.phpviet_user && phpviet_user.id) || '',
        nam_hoc: window.phpviet_nam_hoc_v5 || window.phpviet_nam_hoc || 0,
        win_id: '',
        cap_hoc: 0,
        cap_hoc_text: '',
        giao_vien_id: 0,
        giao_vien_name: '',
        tuan_hoc: 0,
        ngay_tac_dung_tkb: '',
        site_id: (window.phpviet_user && phpviet_user.site_id) || '',
    };
    try {
        const wins = Ext.ComponentQuery.query('window').filter(w => /Kế hoạch dạy học/.test(w.title || ''));
        if (wins.length) out.win_id = wins[0].id;
    } catch(e) {}
    try {
        const cbs = Ext.ComponentQuery.query('combobox');
        for (const c of cbs) {
            const name = (c.getName ? c.getName() : c.name) || '';
            const label = c.fieldLabel || '';
            if (name === 'cboCapHoc') {
                out.cap_hoc = c.getValue();
                out.cap_hoc_text = c.getRawValue();
            } else if (name === 'cboTuanHoc') {
                out.tuan_hoc = c.getValue();
            } else if (name === 'cboNgayTacDung') {
                out.ngay_tac_dung_tkb = c.getValue();
            } else if (label === 'G.viên') {
                out.giao_vien_id = c.getValue();
                out.giao_vien_name = c.getRawValue();
            }
        }
    } catch(e) { out.err = String(e); }

    // Khôi phục my_token nếu chưa có — đọc từ jQuery ajaxSetup hoặc cookie
    if (!out.my_token) {
        try {
            // jQuery ajaxSetup không expose my_token trực tiếp, nhưng VnEdu nhúng
            // my_token vào URL của mọi XHR. Bắt 1 token từ XHR log nếu có.
            if (window.__xhrLog && window.__xhrLog.length) {
                for (const x of window.__xhrLog.slice().reverse()) {
                    const m = String(x.url || '').match(/my_token=([a-f0-9]+)/);
                    if (m) { out.my_token = m[1]; break; }
                }
            }
        } catch(e) {}
    }
    return out;
}
"""


# Fetch endpoint qua page.evaluate (giữ cookie session)
_JS_FETCH = r"""
async (req) => {
    const t0 = Date.now();
    try {
        const url = req.url;
        const init = {
            method: req.method || 'POST',
            credentials: 'include',
            __khdhToolRequest: true,
            headers: req.headers || {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
        };
        if (req.body != null) init.body = req.body;
        const r = await fetch(url, init);
        const text = await r.text();
        return {ok: true, status: r.status, text: text, duration_ms: Date.now() - t0};
    } catch(e) {
        return {ok: false, error: String(e), duration_ms: Date.now() - t0};
    }
}
"""


# Tiết TKB từ form trong DOM (dùng cho save: tuNgay/denNgay)
_JS_GET_DATE_RANGE = r"""
() => {
    try {
        const winId = (() => {
            const wins = Ext.ComponentQuery.query('window').filter(w => /Kế hoạch dạy học/.test(w.title || ''));
            return wins.length ? wins[0].id : '';
        })();
        if (!winId) return {ok: false, err: 'no_winId'};
        const tuNgay = Ext.getCmp(winId + '_tuNgay');
        const denNgayInputs = document.querySelectorAll(`#${winId}_frm input[name="denNgay"]`);
        let denNgayVal = '';
        denNgayInputs.forEach(inp => { if (inp.value) denNgayVal = inp.value; });
        return {
            ok: true,
            tuNgay: tuNgay ? (tuNgay.getRawValue ? tuNgay.getRawValue() : '') : '',
            denNgay: denNgayVal,
        };
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Click nút "Lưu" qua handler để giữ behavior (log, mask, etc.)
_JS_CLICK_SAVE = r"""
() => {
    try {
        const btn = Ext.ComponentQuery.query('button').find(b => /^Lưu$/i.test(String(b.text || '').trim()) && b.handler);
        if (!btn) return {ok: false, err: 'no_save_btn'};
        btn.handler.call(btn.scope || btn);
        return {ok: true};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Đóng tất cả Ext.MessageBox / Ext.Window có liên quan để pipeline không bị
# kẹt bởi dialog "Chưa chọn lớp/môn báo giảng?" (handler change của
# cboTrangThai), "Đã lưu thành công!" (sau save), "Còn tồn tại các ô chưa
# nhập đủ" (validation), v.v.
# Trả số dialog đã đóng + danh sách title/msg để caller biết loại nào.
_JS_DISMISS_DIALOGS = r"""
() => {
    try {
        const wins = Ext.ComponentQuery.query('messagebox, window');
        const closed = [];
        for (const w of wins) {
            try {
                if (!(w.isVisible && w.isVisible())) continue;
                /* Bỏ qua các window chính (KHDH, popup chính). Chỉ đóng
                   messagebox + dialog nhỏ (200-450 px width). */
                const W = (w.getWidth ? w.getWidth() : 0);
                const isMsgBox = String(w.xtype || '').indexOf('messagebox') >= 0;
                const isDialog = isMsgBox || (W > 0 && W <= 500);
                if (!isDialog) continue;
                const title = String(w.title || '').slice(0, 80);
                const msg = String(
                    (w.msg || '') ||
                    (w.body && w.body.dom ? w.body.dom.innerText : '')
                ).slice(0, 200);
                if (w.close) w.close();
                else if (w.hide) w.hide();
                closed.push({title: title, msg: msg});
            } catch(e) {}
        }
        return {ok: true, closed: closed};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Reload bảng KHDH (= click Refresh)
_JS_RELOAD_TABLE = r"""
() => {
    try {
        const winId = (() => {
            const wins = Ext.ComponentQuery.query('window').filter(w => /Kế hoạch dạy học/.test(w.title || ''));
            return wins.length ? wins[0].id : '';
        })();
        if (!winId) return {ok: false, err: 'no_winId'};
        const btn = Ext.getCmp(winId + '_btnRefresh');
        if (btn && btn.handler) { btn.handler.call(btn.scope || btn); return {ok: true}; }
        // Fallback: gọi loadData nếu có
        if (typeof loadData === 'function') { loadData(); return {ok: true, msg: 'loadData'}; }
        return {ok: false, err: 'no_refresh'};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Bật chế độ Sửa (Gợi ý theo TKB)
_JS_SET_GOI_Y_TKB = r"""
() => {
    try {
        const r = Ext.getCmp('rdoEdit2');
        if (!r) return {ok: false, err: 'rdoEdit2_not_found'};
        // C6 fix: check visibility & disabled trước khi setValue
        if (r.isVisible && !r.isVisible()) return {ok: false, err: 'rdoEdit2_hidden'};
        if (r.isDisabled && r.isDisabled()) return {ok: false, err: 'rdoEdit2_disabled'};
        if (r.getValue && r.getValue()) return {ok: true, msg: 'already'};
        if (r.setValue) { r.setValue(true); return {ok: true, msg: 'set'}; }
        return {ok: false, err: 'no_setValue'};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Bật chế độ Sửa (không có gợi ý TKB) — mode mặc định cho khi save
_JS_SET_EDIT = r"""
() => {
    try {
        const r = Ext.getCmp('rdoEdit');
        if (!r) return {ok: false, err: 'rdoEdit_not_found'};
        // C6 fix: check visibility & disabled trước khi setValue
        if (r.isVisible && !r.isVisible()) return {ok: false, err: 'rdoEdit_hidden'};
        if (r.isDisabled && r.isDisabled()) return {ok: false, err: 'rdoEdit_disabled'};
        if (r.getValue && r.getValue()) return {ok: true, msg: 'already'};
        if (r.setValue) { r.setValue(true); return {ok: true, msg: 'set'}; }
        return {ok: false, err: 'no_setValue'};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Đổi tuần đang chọn trên combobox
# Đổi tuần đang chọn trên combobox + click Refresh để force reload table.
# cboTuanHoc là ExtJS combobox không có store (load từ HTML render server-side).
# Cách đúng: setValue + fireEvent('select', ...) + click Refresh.
_JS_SELECT_WEEK = r"""
(weekNum) => {
    try {
        const w = Ext.ComponentQuery.query('combobox').find(c =>
            (c.getName ? c.getName() : c.name) === 'cboTuanHoc');
        if (!w) return {ok: false, err: 'no_combo'};
        const tuan = parseInt(weekNum, 10);
        if (!w.setValue) return {ok: false, err: 'no_setValue'};
        const before = w.getValue ? w.getValue() : null;
        w.setValue(tuan);
        // Fire select event để các listener (bao gồm reload table) chạy
        try { w.fireEvent('select', w); } catch(e) {}
        try { w.fireEvent('change', w, tuan, before); } catch(e) {}
        // Click Refresh button để chắc chắn server reload đúng tuần
        const wins = Ext.ComponentQuery.query('window').filter(win =>
            /Kế hoạch dạy học/.test(win.title || ''));
        if (wins.length) {
            const winId = wins[0].id;
            const btn = Ext.getCmp(winId + '_btnRefresh');
            if (btn && btn.handler) {
                btn.handler.call(btn.scope || btn);
            }
        }
        return {ok: true, before: before, after: w.getValue()};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Install XHR sniffer (idempotent) — capture method, url, status, body, response.
# Force reinstall mỗi lần KHDHClient init: dù sniffer v1 cũ đang chạy, mình
# vẫn restore prototype về native trước, rồi patch lại với v2.
_JS_INSTALL_SNIFFER = r"""
() => {
    /* Lưu native prototype 1 lần duy nhất (lần đầu page load).
       Sau đó mỗi lần install, restore từ native rồi patch v2 lên. */
    if (!window.__xhrNativeOpen) {
        /* Lần đầu — backup native (giả định chưa bị patch).
           Nếu trang đã có sniffer khác patch trước đó, native = sniffer đó.
           Đây là tradeoff chấp nhận được: ít nhất ta không double-patch
           với chính sniffer của mình. */
        window.__xhrNativeOpen = XMLHttpRequest.prototype.open;
        window.__xhrNativeSend = XMLHttpRequest.prototype.send;
    } else {
        /* Restore về native trước khi patch lại — tránh chain v1→v2→v3… */
        XMLHttpRequest.prototype.open = window.__xhrNativeOpen;
        XMLHttpRequest.prototype.send = window.__xhrNativeSend;
    }
    window.__xhrLog = [];
    window.__xhrLogV2 = true;
    const origOpen = window.__xhrNativeOpen;
    const origSend = window.__xhrNativeSend;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.__method = method; this.__url = url;
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        const ts = Date.now();
        this.addEventListener('loadend', () => {
            try {
                window.__xhrLog.push({
                    method: this.__method,
                    url: String(this.__url || '').slice(0, 400),
                    status: this.status,
                    duration_ms: Date.now() - ts,
                    body_excerpt: typeof body === 'string' ? body.slice(0, 4000) : '',
                    response_excerpt: (() => {
                        try {
                            const t = (this.responseType === '' || this.responseType === 'text')
                                ? this.responseText : '';
                            return t.slice(0, 4000);
                        } catch(e) { return ''; }
                    })(),
                });
                if (window.__xhrLog.length > 200) window.__xhrLog.shift();
            } catch(e) {}
        });
        return origSend.apply(this, arguments);
    };
    return {installed: true};
}
"""


# v3.3: Block autofill của web trong lúc tool nhập.
#
# Vấn đề: web KHDH bind handler `change` trên cboMonHoc/cboPhanMon gọi
# `getSuggestTietPpct(this)` + `getTenBaiHoc(this)` async qua jQuery AJAX.
# `getSuggestTietPpct` gọi `edu.phan_phoi_chuong_trinh.getSuggestTietPpct`;
# nếu response không có `data.tiet`, web success handler sẽ set rỗng
# txtTietPPCT/txtTenBai/txtGhiChu. Response về sau Phase D/D2 có thể xóa
# PPCT tool vừa nhập.
#
# Fix: patch tầng network trong thời gian fill để block đúng endpoint
# autofill. Patch phải re-arm được sau mỗi lần sniffer reinstall: sniffer
# restore XMLHttpRequest về native trước khi patch lại, nên blocker cũ có
# thể bị tháo dù flag `__khdhAutofillBlockerInstalled` vẫn còn true.
# Tool fetch có `__khdhToolRequest` để bypass blocker.
_JS_INSTALL_AUTOFILL_BLOCKER = r"""
() => {
    window.__khdhBlockAutofill = !!window.__khdhBlockAutofill;

    const isAutofillRequest = (url, body) => {
        const s = String(url || '') + '\n' + String(body || '');
        // v3.3: Auto-detect endpoint autofill mở rộng:
        //  1. Endpoint cũ đã biết: getSuggestTietPpct, getByTiet, getTenBaiHoc
        //  2. Mọi method mới trong namespace `phan_phoi_chuong_trinh.*` có
        //     prefix get/find/suggest/lookup/auto/load/fetch + tham chiếu
        //     đến tiet/ppct/ten_bai/lich_bao_giang.
        //  3. KHÔNG match endpoint save/delete/update của KHDH.
        if (/(?:getSuggestTietPpct|getByTiet|getTenBaiHoc)/i.test(s)) {
            return true;
        }
        // Heuristic: namespace phan_phoi_chuong_trinh + verb get/find/...
        if (/phan_phoi_chuong_trinh\.[\w]*?(get|find|suggest|lookup|auto|load|fetch)[\w]*/i.test(s)) {
            // Loại trừ các endpoint save/update/delete (case web đặt tên lạ)
            if (!/(?:save|update|delete|remove|set|insert|create)/i.test(s)) {
                return true;
            }
        }
        return false;
    };

    const rememberBlocked = (transport, url, body) => {
        try {
            if (!Array.isArray(window.__khdhBlockedAutofillLog)) {
                window.__khdhBlockedAutofillLog = [];
            }
            window.__khdhBlockedAutofillLog.push({
                ts: Date.now(),
                transport: transport,
                url: String(url || '').slice(0, 500),
                body_excerpt: typeof body === 'string' ? body.slice(0, 1000) : '',
            });
            if (window.__khdhBlockedAutofillLog.length > 100) {
                window.__khdhBlockedAutofillLog.shift();
            }
        } catch(e) {}
    };

    window.__khdhIsAutofillRequest = isAutofillRequest;
    window.__khdhRememberBlockedAutofill = rememberBlocked;

    window.__khdhInstallAutofillBlocker = function() {
        const xhrAlreadyArmed =
            XMLHttpRequest.prototype.open === window.__khdhBlockerOpen &&
            XMLHttpRequest.prototype.send === window.__khdhBlockerSend;
        const fetchAlreadyArmed =
            !window.fetch || window.fetch === window.__khdhBlockerFetch;
        const scriptAlreadyArmed =
            Element.prototype.appendChild === window.__khdhBlockerAppendChild &&
            Element.prototype.insertBefore === window.__khdhBlockerInsertBefore;

        if (xhrAlreadyArmed && fetchAlreadyArmed && scriptAlreadyArmed) {
            window.__khdhAutofillBlockerInstalled = true;
            return {ok: true, msg: 'already', armed: true};
        }

        const origOpen = xhrAlreadyArmed && window.__khdhBlockerOrigOpen
            ? window.__khdhBlockerOrigOpen
            : XMLHttpRequest.prototype.open;
        const origSend = xhrAlreadyArmed && window.__khdhBlockerOrigSend
            ? window.__khdhBlockerOrigSend
            : XMLHttpRequest.prototype.send;

        const blockerOpen = function(method, url) {
            this.__khdhUrl = String(url || '');
            return origOpen.apply(this, arguments);
        };
        const blockerSend = function(body) {
            const u = String(this.__khdhUrl || this.__url || '');
            if (window.__khdhBlockAutofill && isAutofillRequest(u, body)) {
                rememberBlocked('xhr', u, body);
                try { this.abort(); } catch(e) {}
                return;
            }
            return origSend.apply(this, arguments);
        };
        blockerOpen.__khdhAutofillBlocker = true;
        blockerSend.__khdhAutofillBlocker = true;
        window.__khdhBlockerOrigOpen = origOpen;
        window.__khdhBlockerOrigSend = origSend;
        window.__khdhBlockerOpen = blockerOpen;
        window.__khdhBlockerSend = blockerSend;
        XMLHttpRequest.prototype.open = blockerOpen;
        XMLHttpRequest.prototype.send = blockerSend;

        if (window.fetch) {
            const origFetch = fetchAlreadyArmed && window.__khdhBlockerOrigFetch
                ? window.__khdhBlockerOrigFetch
                : window.fetch;
            const blockerFetch = function(input, init) {
                const url = typeof input === 'string'
                    ? input
                    : (input && input.url) || '';
                const body = init && init.body;
                const isToolRequest = !!(init && init.__khdhToolRequest);
                if (
                    window.__khdhBlockAutofill &&
                    !isToolRequest &&
                    isAutofillRequest(url, body)
                ) {
                    rememberBlocked('fetch', url, body);
                    const err = new Error('KHDH autofill request blocked');
                    err.name = 'AbortError';
                    return Promise.reject(err);
                }
                return origFetch.apply(this, arguments);
            };
            blockerFetch.__khdhAutofillBlocker = true;
            window.__khdhBlockerOrigFetch = origFetch;
            window.__khdhBlockerFetch = blockerFetch;
            window.fetch = blockerFetch;
        }

        const shouldBlockScript = (node) => {
            try {
                return !!(
                    node &&
                    String(node.tagName || '').toUpperCase() === 'SCRIPT' &&
                    isAutofillRequest(node.src || node.getAttribute('src') || '', '')
                );
            } catch(e) {
                return false;
            }
        };
        const origAppendChild = scriptAlreadyArmed && window.__khdhBlockerOrigAppendChild
            ? window.__khdhBlockerOrigAppendChild
            : Element.prototype.appendChild;
        const origInsertBefore = scriptAlreadyArmed && window.__khdhBlockerOrigInsertBefore
            ? window.__khdhBlockerOrigInsertBefore
            : Element.prototype.insertBefore;
        const blockerAppendChild = function(child) {
            if (window.__khdhBlockAutofill && shouldBlockScript(child)) {
                rememberBlocked('script', child.src || child.getAttribute('src') || '', '');
                return child;
            }
            return origAppendChild.apply(this, arguments);
        };
        const blockerInsertBefore = function(newNode, referenceNode) {
            if (window.__khdhBlockAutofill && shouldBlockScript(newNode)) {
                rememberBlocked('script', newNode.src || newNode.getAttribute('src') || '', '');
                return newNode;
            }
            return origInsertBefore.apply(this, arguments);
        };
        blockerAppendChild.__khdhAutofillBlocker = true;
        blockerInsertBefore.__khdhAutofillBlocker = true;
        window.__khdhBlockerOrigAppendChild = origAppendChild;
        window.__khdhBlockerOrigInsertBefore = origInsertBefore;
        window.__khdhBlockerAppendChild = blockerAppendChild;
        window.__khdhBlockerInsertBefore = blockerInsertBefore;
        Element.prototype.appendChild = blockerAppendChild;
        Element.prototype.insertBefore = blockerInsertBefore;

        window.__khdhAutofillBlockerInstalled = true;
        return {ok: true, msg: 'installed', armed: true};
    };

    return window.__khdhInstallAutofillBlocker();
}
"""


_JS_SET_BLOCK_AUTOFILL = r"""
(blocked) => {
    try {
        if (typeof window.__khdhInstallAutofillBlocker === 'function') {
            window.__khdhInstallAutofillBlocker();
        }
    } catch(e) {}
    window.__khdhBlockAutofill = !!blocked;
    return {
        ok: true,
        blocked: !!blocked,
        xhr_armed: XMLHttpRequest.prototype.send === window.__khdhBlockerSend,
        fetch_armed: !window.fetch || window.fetch === window.__khdhBlockerFetch,
        blocked_count: Array.isArray(window.__khdhBlockedAutofillLog)
            ? window.__khdhBlockedAutofillLog.length
            : 0,
    };
}
"""


# Fetch nhiều tuần trong 1 lần page.evaluate qua Promise.all.
# Tăng tốc 5-8x so với fetch tuần tự (test thực tế: 3 tuần ~1.2s thay vì 3.6s).
# Mỗi tuần gọi cùng endpoint `lich_bao_giang`, server xử lý song song được.
_JS_FETCH_WEEKS_BATCH = r"""
async (args) => {
    const ctx = args.ctx || {};
    const weeks = Array.isArray(args.weeks) ? args.weeks : [];
    const isEdit = args.is_edit || 1;
    if (!ctx.my_token || !ctx.nam_hoc) {
        return {ok: false, err: 'no_context'};
    }
    const fetchOne = async (w) => {
        const params = new URLSearchParams();
        params.set('my_token', String(ctx.my_token));
        params.set('my_user_id', String(ctx.my_user_id || ''));
        params.set('app_nam_hoc', String(ctx.nam_hoc));
        params.set('winId', String(ctx.win_id || ''));
        params.set('namHoc', String(ctx.nam_hoc));
        params.set('capHoc', String(ctx.cap_hoc || 2));
        params.set('giaoVienId', String(ctx.giao_vien_id || ''));
        params.set('tuanHoc', String(w));
        params.set('iNgayTacDung', String(ctx.ngay_tac_dung || ''));
        params.set('isEdit', String(isEdit));
        const url = '?load=edu.lich_bao_giang.lich_bao_giang'
            + '&app_nam_hoc=' + encodeURIComponent(ctx.nam_hoc)
            + '&my_token=' + encodeURIComponent(ctx.my_token);
        const t0 = performance.now();
        try {
            const resp = await fetch(url, {
                method: 'POST', credentials: 'include',
                __khdhToolRequest: true,
                headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                body: params.toString(),
            });
            const text = await resp.text();
            return {
                week: w, ok: resp.status === 200, status: resp.status,
                duration_ms: Math.round(performance.now() - t0),
                text: text,
            };
        } catch (e) {
            return {
                week: w, ok: false, status: 0,
                duration_ms: Math.round(performance.now() - t0),
                err: String(e),
            };
        }
    };
    const t0 = performance.now();
    const results = await Promise.all(weeks.map(fetchOne));
    return {
        ok: true, total_ms: Math.round(performance.now() - t0),
        results: results,
    };
}
"""


# Đọc tổng số tuần từ store của cboTuanHoc — dùng để auto-detect tuan_to.
_JS_GET_TOTAL_WEEKS = r"""
() => {
    try {
        const cb = Ext.ComponentQuery.query('combobox').find(c =>
            (c.getName ? c.getName() : c.name) === 'cboTuanHoc');
        if (!cb || !cb.store || !cb.store.data) return {ok: false, err: 'no_combo'};
        const items = cb.store.data.items || [];
        if (!items.length) return {ok: false, err: 'empty_store'};
        const weeks = items.map(r => {
            try { return parseInt(r.get ? r.get('id') : r.data.id, 10); }
            catch (e) { return 0; }
        }).filter(n => n > 0);
        return {
            ok: true,
            total: weeks.length,
            min: Math.min.apply(null, weeks),
            max: Math.max.apply(null, weeks),
            current: cb.getValue ? parseInt(cb.getValue(), 10) : 0,
        };
    } catch (e) {
        return {ok: false, err: String(e)};
    }
}
"""

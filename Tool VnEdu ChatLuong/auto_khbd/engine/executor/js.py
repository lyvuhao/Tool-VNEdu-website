"""JavaScript thao tác DOM form tiết học."""

from __future__ import annotations


# ---------------------------------------------------------------
# JS snippets — thao tác DOM trên page
# ---------------------------------------------------------------

# Set lop/mon/pm cho 1 ô — fire change để web render phan_mon options
_JS_SET_SLOT_DROPDOWNS = r"""
(args) => {
    const out = {ok: true, missing: []};
    const setSelect = (id, value) => {
        const el = document.getElementById(id);
        if (!el) { out.missing.push(id); return false; }
        let found = false;
        for (let i = 0; i < el.options.length; i++) {
            if (String(el.options[i].value) === String(value)) {
                el.selectedIndex = i;
                found = true;
                break;
            }
        }
        // Cần fire native change + jQuery change (web bind cả 2)
        el.dispatchEvent(new Event('change', {bubbles: true}));
        try { if (window.$) window.$(el).trigger('change'); } catch(e) {}
        return found;
    };
    out.lop = setSelect(`cboLopHoc_${args.rk}`, args.lop_id);
    return out;
}
"""


_JS_SET_MON = r"""
(args) => {
    const el = document.getElementById(`cboMonHoc_${args.rk}`);
    if (!el) return {ok: false, err: 'no_el', missing: [`cboMonHoc_${args.rk}`]};
    let found = false;
    for (let i = 0; i < el.options.length; i++) {
        if (String(el.options[i].value) === String(args.mon_id)) {
            el.selectedIndex = i;
            found = true;
            break;
        }
    }
    if (!found && args.mon_id && String(args.mon_id) !== '0') {
        // Append option (web có thể chưa render option này)
        try {
            const opt = document.createElement('option');
            opt.value = String(args.mon_id);
            // Dùng tên thật từ caller, fallback ID
            opt.text = String(args.mon_text || args.mon_id);
            opt.selected = true;
            el.appendChild(opt);
            found = true;
        } catch(e) {}
    }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    try { if (window.$) window.$(el).trigger('change'); } catch(e) {}
    return {ok: found, options_count: el.options.length};
}
"""


_JS_SET_PHAN_MON = r"""
(args) => {
    const el = document.getElementById(`cboPhanMon_${args.rk}`);
    if (!el) return {ok: false, err: 'no_el', missing: [`cboPhanMon_${args.rk}`]};
    let found = false;
    for (let i = 0; i < el.options.length; i++) {
        if (String(el.options[i].value) === String(args.pm_id)) {
            el.selectedIndex = i;
            found = true;
            break;
        }
    }
    if (!found && args.pm_id && String(args.pm_id) !== '0') {
        // Web chưa render option (race condition) — append với tên thật
        try {
            const opt = document.createElement('option');
            opt.value = String(args.pm_id);
            opt.text = String(args.pm_text || args.pm_id);
            opt.selected = true;
            el.appendChild(opt);
            found = true;
        } catch(e) {}
    }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    try { if (window.$) window.$(el).trigger('change'); } catch(e) {}
    return {ok: found, appended: !found, options_count: el.options.length};
}
"""


# Đợi đến khi cboPhanMon có option khớp pm_id (do web render lazy sau khi
# set mon). Trả ra true nếu thấy option, false nếu hết timeout.
# Polling từng 50ms, max ~1.5s.
_JS_WAIT_PHAN_MON_OPTION = r"""
(args) => {
    const el = document.getElementById(`cboPhanMon_${args.rk}`);
    if (!el) return {ok: false, err: 'no_el'};
    for (let i = 0; i < el.options.length; i++) {
        if (String(el.options[i].value) === String(args.pm_id)) {
            return {ok: true, options_count: el.options.length};
        }
    }
    return {ok: false, options_count: el.options.length};
}
"""


# Giữ lại _JS_SET_MON_PM cho compat cũ (test có thể dùng), nhưng
# executor không gọi trực tiếp nữa.
_JS_SET_MON_PM = r"""
(args) => {
    const out = {ok: true, missing: []};
    const setSelect = (id, value) => {
        const el = document.getElementById(id);
        if (!el) { out.missing.push(id); return false; }
        let found = false;
        for (let i = 0; i < el.options.length; i++) {
            if (String(el.options[i].value) === String(value)) {
                el.selectedIndex = i;
                found = true;
                break;
            }
        }
        if (!found && value && String(value) !== '0') {
            try {
                const opt = document.createElement('option');
                opt.value = String(value);
                opt.text = String(value);
                opt.selected = true;
                el.appendChild(opt);
                found = true;
            } catch(e) {}
        }
        el.dispatchEvent(new Event('change', {bubbles: true}));
        try { if (window.$) window.$(el).trigger('change'); } catch(e) {}
        return found;
    };
    if (args.mon_id) out.mon = setSelect(`cboMonHoc_${args.rk}`, args.mon_id);
    if (args.pm_id) out.pm = setSelect(`cboPhanMon_${args.rk}`, args.pm_id);
    return out;
}
"""


# Reset 1 ô về rỗng — dùng khi op bị skip do không có tên bài.
# QUAN TRỌNG: KHÔNG fire `change` event trên cboTrangThai, vì web bind handler
# `change` trên cboTrangThai mà handler đó check lopId/monId/tiet — nếu thấy
# lopId=0 sẽ show dialog "Chưa chọn lớp báo giảng?" làm gián đoạn flow.
# Mình set value silent (selectedIndex / value gán trực tiếp).
_JS_RESET_SLOT = r"""
(args) => {
    const setSilent = (el, idx) => {
        if (!el) return;
        el.selectedIndex = idx;
    };
    const lop = document.getElementById(`cboLopHoc_${args.rk}`);
    const mon = document.getElementById(`cboMonHoc_${args.rk}`);
    const pm = document.getElementById(`cboPhanMon_${args.rk}`);
    const ppct = document.getElementById(`txtTietPPCT_${args.rk}`);
    const tb = document.getElementById(`txtTenBai_${args.rk}`);
    const gc = document.getElementById(`txtGhiChu_${args.rk}`);
    const tt = document.getElementById(`cboTrangThai_${args.rk}`);

    // Set cboTrangThai về '-1' TRƯỚC, silent (không fire change)
    if (tt) {
        for (let i = 0; i < tt.options.length; i++) {
            if (tt.options[i].value === '-1') {
                tt.selectedIndex = i;
                break;
            }
        }
    }
    // Reset PPCT + tên bài + ghi chú silent
    if (ppct) ppct.value = '';
    if (tb) tb.value = '';
    if (gc) gc.value = '';
    // Reset lop/mon/pm — fire change vì web cần re-render dependent fields,
    // nhưng cboTrangThai đã = '-1' rồi nên handler trên trang thái không bị
    // trigger. (Handler trên cboLopHoc thay đổi cboMonHoc, không show dialog.)
    setSilent(pm, 0);
    if (pm) {
        pm.dispatchEvent(new Event('change', {bubbles: true}));
        try { if (window.$) window.$(pm).trigger('change'); } catch(e) {}
    }
    setSilent(mon, 0);
    if (mon) {
        mon.dispatchEvent(new Event('change', {bubbles: true}));
        try { if (window.$) window.$(mon).trigger('change'); } catch(e) {}
    }
    setSilent(lop, 0);
    if (lop) {
        lop.dispatchEvent(new Event('change', {bubbles: true}));
        try { if (window.$) window.$(lop).trigger('change'); } catch(e) {}
    }
    return {ok: true};
}
"""


# Set PPCT + tên bài + ghi chú + cboTrangThai vào DOM.
# QUAN TRỌNG: cboTrangThai phải = '0' (Bình thường) cho slot có lop để
# server hiểu đây là tiết dạy thật (không phải '-1'='---'). NHƯNG đặt silent
# để không trigger handler `change` (handler đó show dialog "Chưa chọn lớp"
# nếu lúc đó lopId vẫn = 0 do race condition).
_JS_SET_PPCT_TEN_BAI = r"""
(args) => {
    const setVal = (id, value) => {
        const el = document.getElementById(id);
        if (!el) return false;
        el.value = String(value || '');
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    };
    const out = {};
    if (args.ppct != null) out.ppct = setVal(`txtTietPPCT_${args.rk}`, args.ppct);
    if (args.ten_bai != null) out.ten_bai = setVal(`txtTenBai_${args.rk}`, args.ten_bai);
    if (args.ghi_chu != null) out.ghi_chu = setVal(`txtGhiChu_${args.rk}`, args.ghi_chu);
    // Set cboTrangThai SILENT — không fire change để tránh handler show dialog
    const tt = document.getElementById(`cboTrangThai_${args.rk}`);
    if (tt && args.trang_thai != null) {
        const target = String(args.trang_thai);
        for (let i = 0; i < tt.options.length; i++) {
            if (String(tt.options[i].value) === target) {
                tt.selectedIndex = i;
                // Update attr 'val' cho khớp với cách web tracking giá trị cũ
                // (handler check `oldV = obj.attr('val')` để rollback).
                try { tt.setAttribute('val', target); } catch(e) {}
                out.trang_thai = true;
                break;
            }
        }
    }
    return out;
}
"""


# Đóng dialog Ext.MessageBox "Chưa chọn lớp/môn báo giảng" nếu nó xuất hiện.
# Dialog này đến từ handler `change` của cboTrangThai khi lop/mon = 0. Mình đã
# tránh trigger nó nhưng đề phòng race khác, vẫn cần biện pháp xử lý.
_JS_CLOSE_BENIGN_DIALOG = r"""
() => {
    try {
        const wins = Ext.ComponentQuery.query('messagebox, window');
        let closed = 0;
        for (const w of wins) {
            try {
                if (!(w.isVisible && w.isVisible())) continue;
                const title = String(w.title || '').toLowerCase();
                const msg = String(
                    (w.msg || '') ||
                    (w.body && w.body.dom ? w.body.dom.innerText : '')
                ).toLowerCase();
                if (title.indexOf('thay đổi trạng thái') >= 0
                    || msg.indexOf('chưa chọn lớp') >= 0
                    || msg.indexOf('chưa chọn môn') >= 0
                    || msg.indexOf('chua chon lop') >= 0
                    || msg.indexOf('chua chon mon') >= 0) {
                    if (w.close) w.close();
                    else if (w.hide) w.hide();
                    closed++;
                }
            } catch(e) {}
        }
        return {ok: true, closed: closed};
    } catch(e) { return {ok: false, err: String(e)}; }
}
"""


# Đọc khoi của 1 lop từ option attribute (đã được parser preserve)
_JS_GET_KHOI_FOR_LOP = r"""
(args) => {
    const sel = document.getElementById(`cboLopHoc_${args.rk}`);
    if (!sel) return '';
    for (const opt of sel.options) {
        if (String(opt.value) === String(args.lop_id)) {
            return opt.getAttribute('khoi') || '';
        }
    }
    return '';
}
"""


# Đọc tuần đang chọn trên combobox UI — dùng để xác minh ô đúng tuần
# trước khi nhập dữ liệu (an toàn, tránh ghi đè nhầm tuần).
_JS_GET_CURRENT_WEEK = r"""
() => {
    try {
        const w = Ext.ComponentQuery.query('combobox').find(c =>
            (c.getName ? c.getName() : c.name) === 'cboTuanHoc');
        if (!w) return {ok: false, err: 'no_combo', tuan: 0};
        const v = w.getValue ? w.getValue() : null;
        if (v == null) return {ok: false, err: 'no_value', tuan: 0};
        return {ok: true, tuan: parseInt(v, 10)};
    } catch(e) { return {ok: false, err: String(e), tuan: 0}; }
}
"""


# Gọi trực tiếp API getByTiet để lấy ten_bai theo (lop, mon, pm, ppct)
# Nhanh hơn nhiều so với simulate keyboard typing (vốn cần debounce 1s).
_JS_FETCH_TEN_BAI = r"""
async (args) => {
    try {
        const token = String(args.token || '');
        const userId = String(args.user_id || '');
        const namHoc = String(args.nam_hoc || '');
        if (!token || !namHoc) return {ok: false, err: 'no_token_or_nam_hoc'};
        const params = new URLSearchParams();
        params.set('my_token', token);
        params.set('my_user_id', userId);
        params.set('app_nam_hoc', namHoc);
        params.set('khoi_hoc', String(args.khoi || ''));
        params.set('lop_hoc_id', String(args.lop_id || ''));
        params.set('mon_hoc_id', String(args.mon_id || ''));
        params.set('phan_mon_id', String(args.pm_id || ''));
        params.set('nam_hoc', namHoc);
        params.set('tiet', String(args.ppct));
        const url = './?call=edu.phan_phoi_chuong_trinh.getByTiet'
            + '&app_nam_hoc=' + encodeURIComponent(namHoc)
            + '&my_token=' + encodeURIComponent(token);
        const resp = await fetch(url, {
            method: 'POST',
            credentials: 'include',
            __khdhToolRequest: true,
            headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
            body: params.toString(),
        });
        const text = await resp.text();
        let data = null;
        try { data = JSON.parse(text); } catch(e) {
            return {ok: false, err: 'json_parse', raw: text.slice(0, 200)};
        }
        // Empty array = phân môn không có CSDL PPCT
        if (Array.isArray(data) && data.length === 0) {
            return {ok: true, ten_bai: '', msg: 'no_data'};
        }
        const tb = (data && data.ten_bai) ? String(data.ten_bai).trim() : '';
        return {ok: true, ten_bai: tb, raw_id: data && data.id};
    } catch(e) {
        return {ok: false, err: String(e)};
    }
}
"""


# Đọc state hiện tại của các field (verify)
_JS_READ_FIELDS = r"""
(ids) => {
    const out = {};
    for (const id of ids) {
        const el = document.getElementById(id);
        if (!el) { out[id] = null; continue; }
        const tag = el.tagName.toLowerCase();
        if (tag === 'select') {
            const sel = el.options[el.selectedIndex];
            out[id] = {value: el.value, text: sel ? sel.text.trim() : ''};
        } else {
            out[id] = {value: el.value || ''};
        }
    }
    return out;
}
"""


# Sau khi phải tự append option Phân môn vào DOM: gọi Ext.getCmp().setValue() để data binding của
# ExtJS nhận giá trị mới. (Trước đây viết thẳng trong `_execute_week`.)
_JS_EXT_SET_PHAN_MON_VALUE = """({rk, pm_id}) => {
                                    try {
                                        const cmp = Ext.getCmp('cboPhanMon_' + rk);
                                        if (cmp && typeof cmp.setValue === 'function') {
                                            cmp.setValue(pm_id);
                                            return true;
                                        }
                                    } catch(e) {}
                                    return false;
                                }"""

"""Tìm và xoá tiết đã nhập."""

from ..compat import PlaywrightTimeout


class DeleteMixin:
    """Tìm và xoá tiết đã nhập."""

    def discover_delete_controls(self, max_rows=8):
        """[READ-ONLY] Dump cấu trúc nút action trên các dòng đã có dữ liệu.

        Mục đích: tìm chính xác selector/onclick/href của nút XÓA (icon tròn
        đỏ dấu trừ) trước khi implement chức năng xóa. KHÔNG click, KHÔNG xóa.

        Trả về thông tin từng dòng có dữ liệu:
            - các <a>/<button>/<i>/<span> trong cột hành động (2 cột đầu)
            - tagName, class, onclick (rút gọn), href, title, màu chữ
        Dùng để con người + AI xác định selector nút xóa thật.

        Returns:
            (success, dict|message)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate(
                '''(maxRows) => {
                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function describeControl(el) {
                        if (!el) return null;
                        let onclickAttr = '';
                        try {
                            onclickAttr = String(el.getAttribute('onclick') || '');
                        } catch (e) {}
                        let color = '';
                        try {
                            color = String(window.getComputedStyle(el).color || '');
                        } catch (e) {}
                        let bgColor = '';
                        try {
                            bgColor = String(window.getComputedStyle(el).backgroundColor || '');
                        } catch (e) {}
                        const dataAttrs = {};
                        try {
                            for (const attr of Array.from(el.attributes || [])) {
                                const name = String(attr.name || '');
                                if (
                                    name.startsWith('data-') ||
                                    ['thu', 'buoi', 'tiet', 'mon_hoc_id', 'phan_mon_id',
                                     'tiet_ppct', 'id', 'so_dau_bai_id', 'chi_tiet_id'].includes(name)
                                ) {
                                    dataAttrs[name] = String(attr.value || '');
                                }
                            }
                        } catch (e) {}
                        return {
                            tag: String(el.tagName || ''),
                            className: String(el.className || ''),
                            id: String(el.id || ''),
                            title: String(el.getAttribute && el.getAttribute('title') || ''),
                            onclick: onclickAttr.slice(0, 200),
                            href: String(el.getAttribute && el.getAttribute('href') || '').slice(0, 120),
                            text: textOf(el).slice(0, 40),
                            color: color,
                            backgroundColor: bgColor,
                            dataAttrs: dataAttrs,
                            innerHTML: String(el.innerHTML || '').slice(0, 160),
                        };
                    }

                    let mainTable = document.querySelector('table.table');
                    if (!mainTable) {
                        const tables = document.querySelectorAll('table');
                        let maxRowspan = 0;
                        for (const t of tables) {
                            const rowspans = t.querySelectorAll('td[rowspan]');
                            if (rowspans.length > maxRowspan) {
                                maxRowspan = rowspans.length;
                                mainTable = t;
                            }
                        }
                    }
                    if (!mainTable) {
                        return {ok: false, error: 'Không tìm thấy bảng dữ liệu'};
                    }

                    const rows = Array.from(mainTable.querySelectorAll('tr'));
                    const out = [];
                    const globalSelectors = {};

                    // Đếm class phổ biến của <a>/<i> trong toàn bảng để tìm pattern xóa
                    const allActionEls = Array.from(
                        mainTable.querySelectorAll('a, button, i, span[onclick], img')
                    );
                    for (const el of allActionEls) {
                        const cls = String(el.className || '').trim();
                        let onclickAttr = '';
                        try { onclickAttr = String(el.getAttribute('onclick') || ''); } catch (e) {}
                        const keyParts = [];
                        if (cls) keyParts.push('class=' + cls);
                        const onclickFn = onclickAttr.match(/([a-zA-Z_$][\\w$]*)\\s*\\(/);
                        if (onclickFn) keyParts.push('fn=' + onclickFn[1]);
                        const key = keyParts.join(' | ');
                        if (!key) continue;
                        globalSelectors[key] = (globalSelectors[key] || 0) + 1;
                    }

                    for (let i = 0; i < rows.length && out.length < maxRows; i++) {
                        const tr = rows[i];
                        const cells = tr.querySelectorAll('td');
                        if (cells.length < 4) continue;

                        // Chỉ quan tâm dòng đã có dữ liệu (có action cell với attr thu/tiet
                        // hoặc có nút action bất kỳ)
                        const actionEls = Array.from(
                            tr.querySelectorAll('a, button, i, span[onclick], img')
                        );
                        if (actionEls.length === 0) continue;

                        const rowTexts = Array.from(cells).map((c) => textOf(c)).filter(Boolean);
                        const controls = actionEls
                            .map(describeControl)
                            .filter(Boolean);

                        // Lấy attr từ action cell đầu tiên có thu/tiet
                        let actionCellAttrs = {};
                        const actionCell = tr.querySelector('td[thu][tiet], td[tiet]');
                        if (actionCell) {
                            try {
                                for (const attr of Array.from(actionCell.attributes || [])) {
                                    actionCellAttrs[String(attr.name)] = String(attr.value || '');
                                }
                            } catch (e) {}
                        }

                        out.push({
                            rowIdx: i,
                            row_texts: rowTexts.slice(0, 12),
                            action_cell_attrs: actionCellAttrs,
                            controls: controls,
                        });
                    }

                    return {
                        ok: true,
                        table_id: mainTable.id || '',
                        table_class: String(mainTable.className || '').slice(0, 60),
                        global_selectors: globalSelectors,
                        rows: out,
                    };
                }''',
                int(max_rows),
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Không dump được control xóa")
        except Exception as e:
            return False, f"Lỗi discover delete controls: {type(e).__name__}: {str(e)[:120]}"

    def fetch_deletable_entries(self, lop_text, tuan_num, timeout_s=12.0):
        """Fetch HTML sổ đầu bài và trích các tiết ĐÃ CÓ dữ liệu để xóa.

        Mỗi entry chứa chitiet_id thật (lấy từ <td chitiet_id="...">) cùng
        metadata thu/buổi/tiết/môn/PPCT để preview và verify. Chỉ lấy ô có
        chitiet_id non-empty (tức là tiết đã ghi dữ liệu).

        Returns:
            (success, payload|message)
            payload = {
                "entries": [
                    {chitiet_id, thu, thu_full, buoi, tiet, mon_hoc, ppct,
                     noi_dung, nhan_xet, ky_ten},
                    ...
                ],
                "week": int|None,
                "lop": str,
                "fetch_ms": int,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        timeout_ms = max(int(timeout_s * 1000), 3000)
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    function normalize(value) {
                        try {
                            return String(value == null ? '' : value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        } catch (e) {
                            return String(value == null ? '' : value)
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();
                        }
                    }

                    function getCombo(name) {
                        if (typeof Ext === 'undefined' || !Ext.ComponentQuery) return null;
                        for (const combo of Ext.ComponentQuery.query('combobox')) {
                            try {
                                const comboName = combo.getName ? combo.getName() : (combo.name || '');
                                if (comboName === name) return combo;
                            } catch (e) {}
                        }
                        return null;
                    }

                    function getStoreItems(store) {
                        if (!store) return [];
                        if (store.data && Array.isArray(store.data.items)) {
                            return store.data.items;
                        }
                        return [];
                    }

                    function getRecordValue(record, fieldName) {
                        if (!record) return '';
                        try {
                            if (record.get && typeof record.get === 'function') {
                                return record.get(fieldName);
                            }
                        } catch (e) {}
                        try {
                            return record.data ? record.data[fieldName] : '';
                        } catch (e2) {
                            return '';
                        }
                    }

                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function normalizeBuoi(raw) {
                        const value = String(raw || '').trim();
                        if (value === '1') return 'Sáng';
                        if (value === '2') return 'Chiều';
                        return value;
                    }

                    function normalizeThu(raw) {
                        const value = String(raw || '').trim().toUpperCase();
                        if (value === 'CN' || value === '8') return 'CN';
                        return value;
                    }

                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const lopCombo = getCombo('cboLopHoc');
                    if (!lopCombo || !lopCombo.store) {
                        return {ok: false, error: 'Không tìm thấy combobox lớp hoặc store lớp'};
                    }

                    const lopDisplayField = lopCombo.displayField || 'ten';
                    const lopValueField = lopCombo.valueField || 'id';
                    const lopItems = getStoreItems(lopCombo.store);
                    const targetLop = normalize(args.lopText);
                    let classRec = null;
                    for (const rec of lopItems) {
                        if (normalize(getRecordValue(rec, lopDisplayField)) === targetLop) {
                            classRec = rec;
                            break;
                        }
                    }
                    if (!classRec) {
                        return {ok: false, error: 'Không tìm thấy lớp ' + args.lopText + ' trong store hiện tại'};
                    }

                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken) : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId) : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );
                    const capHoc = capCombo
                        ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                        : (getRecordValue(classRec, 'cap') || 2);
                    const khoiHoc = getRecordValue(classRec, 'khoi') || '';
                    const classId = String(getRecordValue(classRec, lopValueField) || '');

                    const params = new URLSearchParams();
                    params.set('my_token', String(token || ''));
                    params.set('my_user_id', String(userId || ''));
                    params.set('app_nam_hoc', namHoc);
                    params.set('capHoc', String(capHoc || ''));
                    params.set('lopHoc', classId);
                    params.set('khoiHoc', String(khoiHoc || ''));
                    params.set('tuanHoc', String(args.tuanNum));
                    params.set('show_goi_y', '0');

                    const url = './?load=app.sodaubai.serv.so_dau_bai'
                        + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                        + '&my_token=' + encodeURIComponent(String(token || ''));

                    const t0 = performance.now();
                    const timeoutMs = Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000);
                    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
                    let timeoutHandle = null;
                    if (controller) {
                        timeoutHandle = setTimeout(() => {
                            try { controller.abort(); } catch (e) {}
                        }, timeoutMs);
                    }

                    let resp = null;
                    let html = '';
                    try {
                        resp = await fetch(url, {
                            method: 'POST',
                            headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                            body: params.toString(),
                            credentials: 'same-origin',
                            signal: controller ? controller.signal : undefined,
                        });
                        html = await resp.text();
                    } catch (error) {
                        const errorName = String(error && error.name ? error.name : '');
                        return {
                            ok: false,
                            error: errorName === 'AbortError'
                                ? ('Timeout fetch service sau ' + timeoutMs + 'ms')
                                : ('Lỗi fetch service: ' + String(error && error.message ? error.message : error)),
                        };
                    } finally {
                        if (timeoutHandle) clearTimeout(timeoutHandle);
                    }
                    const fetchMs = Math.round(performance.now() - t0);

                    if (!resp.ok) {
                        return {ok: false, error: 'Fetch service thất bại: HTTP ' + resp.status, fetch_ms: fetchMs};
                    }

                    const parsed = new DOMParser().parseFromString(html, 'text/html');

                    // Lấy ngày theo thứ (div.thu2, div.thu3, ... hoặc div.thuCN)
                    const dateByThu = {};
                    for (const div of Array.from(parsed.querySelectorAll('div[class^="thu"]'))) {
                        const cls = String(div.className || '');
                        const m = cls.match(/^thu([0-9A-Za-z]+)/);
                        if (m) dateByThu[normalizeThu(m[1])] = textOf(div);
                    }

                    const entries = [];
                    const seenIds = new Set();
                    const cells = Array.from(parsed.querySelectorAll('td[chitiet_id]'));
                    for (const td of cells) {
                        const chitietId = String(td.getAttribute('chitiet_id') || '').trim();
                        if (!chitietId) continue;            // ô trống -> bỏ qua
                        if (seenIds.has(chitietId)) continue; // tránh trùng id
                        seenIds.add(chitietId);

                        const thu = normalizeThu(td.getAttribute('thu'));
                        const buoi = normalizeBuoi(td.getAttribute('buoi'));
                        const tiet = String(td.getAttribute('tiet') || '').trim();
                        const ppct = String(td.getAttribute('tiet_ppct') || '').trim();
                        const noiDung = String(td.getAttribute('noi_dung') || '').trim();
                        const nhanXet = String(td.getAttribute('nhan_xet') || '').trim();

                        // Đọc môn học hiển thị + ký tên từ các cell cùng dòng (nếu có)
                        let monHoc = '';
                        let kyTen = '';
                        const tr = td.closest('tr');
                        if (tr) {
                            const rowCells = Array.from(tr.querySelectorAll('td'));
                            const tdIndex = rowCells.indexOf(td);
                            // Cột Môn học đứng ngay sau cột Tiết (action, tiet, mon_hoc, ppct...)
                            if (tdIndex >= 0 && rowCells[tdIndex + 2]) {
                                monHoc = textOf(rowCells[tdIndex + 2]);
                            }
                            // Cột Ký tên là cell cuối cùng
                            if (rowCells.length) {
                                kyTen = textOf(rowCells[rowCells.length - 1]);
                            }
                        }

                        entries.push({
                            chitiet_id: chitietId,
                            thu: thu,
                            thu_full: dateByThu[thu] || '',
                            buoi: buoi,
                            tiet: tiet,
                            mon_hoc: monHoc,
                            ppct: ppct,
                            noi_dung: noiDung,
                            nhan_xet: nhanXet,
                            ky_ten: kyTen,
                        });
                    }

                    let weekValue = null;
                    const weekHidden = parsed.querySelector('input[name="iTuanHoc"]');
                    if (weekHidden && weekHidden.getAttribute('value')) {
                        const weekNum = parseInt(weekHidden.getAttribute('value'), 10);
                        if (!Number.isNaN(weekNum)) weekValue = weekNum;
                    }
                    if (weekValue === null) {
                        const weekMatch = html.match(/TUẦN\\s+(\\d+)/i);
                        if (weekMatch) weekValue = parseInt(weekMatch[1], 10);
                    }

                    return {
                        ok: true,
                        entries: entries,
                        week: weekValue,
                        fetch_ms: fetchMs,
                        lop: String(getRecordValue(classRec, lopDisplayField) || args.lopText),
                        class_id: classId,
                    };
                }''',
                {"lopText": str(lop_text), "tuanNum": int(tuan_num), "timeoutMs": int(timeout_ms)},
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Fetch tiết cần xóa thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout fetch tiết cần xóa cho lớp {lop_text}, tuần {tuan_num}"
        except Exception as e:
            return False, f"Lỗi fetch tiết cần xóa: {type(e).__name__}: {str(e)[:120]}"

    def delete_entry_by_id(self, chitiet_id, timeout_s=12.0):
        """Xóa 1 tiết sổ đầu bài qua API nội bộ VnEdu (theo chitiet_id).

        Dùng đúng endpoint mà nút Xóa đỏ gọi:
            POST ?call=app.sodaubai.serv.so_dau_bai.delete  data={id: chitiet_id}
        Bỏ qua popup confirm — dùng session/cookie hiện tại của Chrome.

        Args:
            chitiet_id: str|int — id bản ghi chi tiết sổ đầu bài

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        target_id = str(chitiet_id or "").strip()
        if not target_id:
            return False, "Thiếu chitiet_id để xóa"

        timeout_ms = max(int(timeout_s * 1000), 3000)
        try:
            result = self.page.evaluate(
                '''async (args) => {
                    if (!(window.$ && $.ajax)) {
                        return {ok: false, error: 'jQuery.ajax không sẵn sàng', request_sent: false};
                    }
                    const basePath = (typeof applicationPath !== 'undefined' && applicationPath)
                        ? String(applicationPath) : '';
                    const url = (basePath || '') + '?call=app.sodaubai.serv.so_dau_bai.delete';

                    let requestSent = false;
                    let ajaxResult = null;
                    try {
                        ajaxResult = await new Promise((resolve) => {
                            requestSent = true;
                            const timeoutMs = Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000);
                            const timer = setTimeout(() => {
                                resolve({ok: false, error: 'Timeout xóa sau ' + timeoutMs + 'ms', timeout: true});
                            }, timeoutMs);
                            $.ajax({
                                url: url,
                                type: 'post',
                                data: {id: args.id},
                                dataType: 'json',
                                success: function (rs) {
                                    clearTimeout(timer);
                                    resolve({ok: true, raw: rs});
                                },
                                error: function (xhr, status, thrown) {
                                    clearTimeout(timer);
                                    resolve({
                                        ok: false,
                                        error: String(thrown || (xhr && xhr.statusText) || 'ajax_error'),
                                        status: xhr && typeof xhr.status !== 'undefined' ? xhr.status : null,
                                        responseText: xhr && xhr.responseText ? String(xhr.responseText).slice(0, 200) : '',
                                    });
                                }
                            });
                        });
                    } catch (e) {
                        return {ok: false, error: String(e && e.message ? e.message : e), request_sent: requestSent};
                    }

                    if (!ajaxResult || !ajaxResult.ok) {
                        return {
                            ok: false,
                            error: ajaxResult && ajaxResult.error ? ajaxResult.error : 'ajax_error',
                            status_code: ajaxResult && ajaxResult.status,
                            response_text: ajaxResult && ajaxResult.responseText,
                            request_sent: requestSent,
                            timeout: !!(ajaxResult && ajaxResult.timeout),
                        };
                    }

                    let data = ajaxResult.raw;
                    if (typeof data === 'string') {
                        try { data = JSON.parse(data); }
                        catch (e) {
                            return {ok: false, error: 'JSON parse lỗi', raw: String(ajaxResult.raw).slice(0, 200), request_sent: requestSent};
                        }
                    }

                    // VnEdu trả success===false khi lỗi; thiếu field success coi như OK
                    if (data && data.success === false) {
                        return {ok: false, error: data.msg ? String(data.msg) : 'Server báo xóa thất bại', request_sent: requestSent};
                    }

                    let refreshClicked = false;
                    try {
                        const refreshBtn = document.querySelector('#sodaubai_refresh');
                        if (refreshBtn) { refreshBtn.click(); refreshClicked = true; }
                    } catch (e) {}

                    return {ok: true, server_msg: data && data.msg ? String(data.msg) : '', refresh_clicked: refreshClicked, request_sent: requestSent};
                }''',
                {"id": target_id, "timeoutMs": int(timeout_ms)},
            )
            if result.get("ok"):
                return True, result.get("server_msg", "") or "Đã xóa"
            return False, result.get("error", "Xóa thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout xóa chitiet_id={target_id}"
        except Exception as e:
            return False, f"Lỗi xóa: {type(e).__name__}: {str(e)[:120]}"

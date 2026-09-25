"""Lấy dữ liệu sổ đầu bài (từng tuần và hàng loạt)."""

import copy

from ..compat import PlaywrightTimeout


class SoDauBaiFetchMixin:
    """Lấy dữ liệu sổ đầu bài (từng tuần và hàng loạt)."""

    def fetch_sodaubai_rows(self, lop_text, tuan_num, timeout_s=12.0, class_meta=None,
                            show_goi_y=False):
        """Fetch trực tiếp HTML sổ đầu bài theo lớp/tuần rồi parse ra rows.

        Đi đường service nội bộ của VnEdu để tránh tình trạng UI dropdown đã đổi
        nhưng grid chưa refresh kịp.

        Returns:
            (success, payload|message)
            payload = {
                "rows": list[dict],
                "week": int|None,
                "fetch_ms": int,
                "html_length": int,
                "lop": str,
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
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
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

                    function parseRowsFromTable(rootDoc) {
                        let mainTable = rootDoc.querySelector('table.table');
                        if (!mainTable) {
                            const tables = rootDoc.querySelectorAll('table');
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
                            return {ok: false, error: 'Không tìm thấy bảng dữ liệu trong HTML fetch'};
                        }

                        const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                        const allRows = mainTable.querySelectorAll('tr');
                        const dataRows = [];

                        function isHeaderMarkerRow(row) {
                            const cells = Array.from(row.querySelectorAll('td'));
                            if (cells.length < 8) return false;
                            const texts = cells.map((cell) => textOf(cell));
                            const prefix = ['1', '2', '3', '4', '5'];
                            return prefix.every((value, index) => texts[index] === value);
                        }

                        function findDataStartIndex(rows) {
                            for (let i = 0; i < rows.length; i++) {
                                if (isHeaderMarkerRow(rows[i])) {
                                    return i + 1;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                const cells = rows[i].querySelectorAll('td');
                                if (cells.length < 4) continue;
                                const firstText = textOf(cells[0]);
                                const secondText = textOf(cells[1]);
                                const firstRs = parseInt(cells[0].getAttribute('rowspan') || '0', 10);
                                const secondRs = parseInt(cells[1].getAttribute('rowspan') || '0', 10);
                                const looksLikeThu = (
                                    firstRs >= 5 &&
                                    (
                                        /^(CN|[2-7])(\\s|$|\\n|\\/|-|\\d)/i.test(firstText) ||
                                        /\\d{2}\\/\\d{2}\\/\\d{4}/.test(firstText)
                                    )
                                );
                                const looksLikeBuoi = (
                                    normalize(secondText).indexOf('sang') >= 0 ||
                                    normalize(secondText).indexOf('chieu') >= 0 ||
                                    secondRs >= 4
                                );
                                if (looksLikeThu || looksLikeBuoi) {
                                    return i;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                if (rows[i].querySelector(addSelector)) {
                                    return i;
                                }
                            }

                            return 0;
                        }

                        const dataStartIdx = findDataStartIndex(allRows);

                        let currentThu = '';
                        let currentThuFull = '';
                        let currentBuoi = '';
                        let thuRemaining = 0;
                        let buoiRemaining = 0;
                        let addBtnCounter = 0;

                        for (let i = dataStartIdx; i < allRows.length; i++) {
                            const cells = allRows[i].querySelectorAll('td');
                            if (cells.length < 2) continue;

                            let cellIdx = 0;
                            let thu = currentThu;
                            let thuFull = currentThuFull;
                            let buoi = currentBuoi;
                            let tiet = '';
                            let monHoc = '';
                            let ppct = '';
                            let hasData = false;
                            let hasAddBtn = false;
                            let actionCell = null;
                            let chiTietId = '';
                            let monHocId = '';
                            let phanMonId = '';
                            let tietPpctAttr = '';
                            let giaoVienIdDayCung = '';
                            let thongBao = '';
                            let redTexts = [];

                            if (thuRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                if (rs > 1) {
                                    const rawThuText = String(cells[cellIdx].textContent || '')
                                        .replace(/\\u00a0/g, ' ')
                                        .trim();
                                    thuFull = textOf(cells[cellIdx]);
                                    const upperRaw = rawThuText.toUpperCase();
                                    if (upperRaw.startsWith('CN')) {
                                        thu = 'CN';
                                        const tail = rawThuText.slice(2).trim();
                                        if (tail) thuFull = 'CN\\n' + tail;
                                    } else {
                                        const match = rawThuText.match(/[2-7]/);
                                        thu = match ? match[0] : thuFull;
                                        if (match && rawThuText.startsWith(match[0])) {
                                            const tail = rawThuText.slice(match[0].length).trim();
                                            if (tail) thuFull = match[0] + '\\n' + tail;
                                        }
                                    }
                                    currentThu = thu;
                                    currentThuFull = thuFull;
                                    thuRemaining = rs;
                                    cellIdx++;
                                }
                            }

                            if (buoiRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                const text = textOf(cells[cellIdx]);
                                const lower = normalize(text);
                                const isBuoi = (
                                    lower.indexOf('sang') >= 0 ||
                                    lower.indexOf('chieu') >= 0 ||
                                    rs >= 4
                                );
                                if (isBuoi || rs > 1) {
                                    buoi = text;
                                    currentBuoi = buoi;
                                    buoiRemaining = rs > 0 ? rs : 5;
                                    cellIdx++;
                                }
                            }

                            if (cells[cellIdx]) {
                                actionCell = cells[cellIdx];
                                const btn = actionCell.querySelector(addSelector);
                                hasAddBtn = !!btn;
                                chiTietId = String(actionCell.getAttribute('chitiet_id') || '').trim();
                                monHocId = String(actionCell.getAttribute('mon_hoc_id') || '').trim();
                                phanMonId = String(actionCell.getAttribute('phan_mon_id') || '').trim();
                                tietPpctAttr = String(actionCell.getAttribute('tiet_ppct') || '').trim();
                                giaoVienIdDayCung = String(
                                    actionCell.getAttribute('giaovien_id_daycung') || ''
                                ).trim();
                                thongBao = String(actionCell.getAttribute('thong_bao') || '').trim();
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                tiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                monHoc = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                ppct = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            let tenHsNghiTiet = '';
                            let noiDungCongViec = '';
                            let diemKiemTra = '';
                            let nhanXetGiaoVien = '';
                            let diemXepLoai = '';
                            let kyTen = '';

                            if (cells[cellIdx]) {
                                tenHsNghiTiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                noiDungCongViec = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                diemKiemTra = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                nhanXetGiaoVien = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                diemXepLoai = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                kyTen = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            redTexts = Array.from(allRows[i].querySelectorAll('span'))
                                .filter((span) => {
                                    const style = String(span.getAttribute('style') || '').toLowerCase();
                                    let computed = '';
                                    try {
                                        computed = String(window.getComputedStyle(span).color || '').toLowerCase();
                                    } catch (e) {}
                                    return style.includes('red')
                                        || style.includes('#f00')
                                        || style.includes('255, 0, 0')
                                        || computed.includes('red')
                                        || computed.includes('255, 0, 0');
                                })
                                .map((span) => textOf(span))
                                .filter(Boolean);
                            const hasMeaningfulValue = (value) => {
                                const normalized = normalize(value);
                                return Boolean(normalized)
                                    && !['0', 'false', 'null', 'undefined', 'none'].includes(normalized);
                            };
                            const hasSuggestionMetadata = Boolean(
                                hasMeaningfulValue(monHocId)
                                || hasMeaningfulValue(phanMonId)
                                || hasMeaningfulValue(tietPpctAttr)
                                || hasMeaningfulValue(giaoVienIdDayCung)
                                || redTexts.length
                            );
                            hasData = Boolean(chiTietId)
                                || (monHoc.length > 0 && !hasAddBtn && !hasSuggestionMetadata);
                            const isScheduled = !hasData && Boolean(
                                hasSuggestionMetadata || monHoc || ppct || hasMeaningfulValue(thongBao)
                            );
                            const isUnplanned = !hasData && !isScheduled;
                            thuRemaining--;
                            buoiRemaining--;

                            if (tiet && /^\\d+$/.test(tiet)) {
                                const addBtnIndex = hasAddBtn ? addBtnCounter : -1;
                                if (hasAddBtn) addBtnCounter++;
                                dataRows.push({
                                    index: dataRows.length,
                                    rowIdx: i,
                                    thu: thu,
                                    thu_full: thuFull,
                                    buoi: buoi,
                                    tiet: tiet,
                                    mon_hoc: monHoc,
                                    ppct: ppct,
                                    ten_hs_nghi_tiet: tenHsNghiTiet,
                                    noi_dung_cong_viec: noiDungCongViec,
                                    diem_kiem_tra: diemKiemTra,
                                    nhan_xet_giao_vien: nhanXetGiaoVien,
                                    diem_xep_loai: diemXepLoai,
                                    ky_ten: kyTen,
                                    has_data: hasData,
                                    is_scheduled: isScheduled,
                                    is_unplanned: isUnplanned,
                                    has_add_btn: hasAddBtn,
                                    add_btn_index: addBtnIndex,
                                    chitiet_id: chiTietId,
                                    mon_hoc_id: monHocId,
                                    phan_mon_id: phanMonId,
                                    tiet_ppct_attr: tietPpctAttr,
                                    giaovien_id_daycung: giaoVienIdDayCung,
                                    thong_bao: thongBao,
                                    ppct_hint: tietPpctAttr || ppct,
                                    mon_hoc_hint: monHoc,
                                    noi_dung_hint: noiDungCongViec,
                                    red_texts: redTexts,
                                });
                            }
                        }

                        return {ok: true, rows: dataRows};
                    }

                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const classMeta = args.classMeta || {};
                    const classMetaText = String(classMeta.text || '').trim();
                    const classMetaValue = String(classMeta.value || '').trim();
                    const classMetaKhoi = String(classMeta.khoi || '').trim();
                    const classMetaCap = String(classMeta.cap || '').trim();
                    let classDisplayText = classMetaText;
                    let classId = classMetaValue;
                    let khoiHoc = classMetaKhoi;
                    let capHoc = classMetaCap;

                    if (!classId) {
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
                            const label = normalize(getRecordValue(rec, lopDisplayField));
                            if (label === targetLop) {
                                classRec = rec;
                                break;
                            }
                        }
                        if (!classRec) {
                            return {ok: false, error: 'Không tìm thấy lớp ' + args.lopText + ' trong store hiện tại'};
                        }
                        classDisplayText = String(getRecordValue(classRec, lopDisplayField) || args.lopText || '').trim();
                        classId = String(getRecordValue(classRec, lopValueField) || '').trim();
                        khoiHoc = String(getRecordValue(classRec, 'khoi') || '').trim();
                        capHoc = String(
                            getRecordValue(classRec, 'cap')
                            || getRecordValue(classRec, 'cap_hoc')
                            || ''
                        ).trim();
                    }

                    if (!classId) {
                        return {ok: false, error: 'Thiếu class_id để fetch sổ đầu bài'};
                    }

                    if (!classDisplayText) {
                        classDisplayText = String(args.lopText || '').trim();
                    }
                    if (!khoiHoc) {
                        const khoiMatch = classDisplayText.match(/^(\\d{1,2})/);
                        khoiHoc = khoiMatch ? String(khoiMatch[1]) : '';
                    }
                    if (!capHoc) {
                        capHoc = capCombo
                            ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                            : '';
                    }

                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken)
                        : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                        : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );
                    const params = new URLSearchParams();
                    params.set('my_token', String(token || ''));
                    params.set('my_user_id', String(userId || ''));
                    params.set('app_nam_hoc', namHoc);
                    params.set('capHoc', String(capHoc || ''));
                    params.set('lopHoc', String(classId || ''));
                    params.set('khoiHoc', String(khoiHoc || ''));
                    params.set('tuanHoc', String(args.tuanNum));
                    params.set('show_goi_y', args.showGoiY ? '1' : '0');

                    const url = './?load=app.sodaubai.serv.so_dau_bai'
                        + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                        + '&my_token=' + encodeURIComponent(String(token || ''));

                    const t0 = performance.now();
                    const timeoutMs = Math.max(parseInt(args.timeoutMs || 0, 10) || 0, 3000);
                    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
                    let timeoutHandle = null;
                    if (controller) {
                        timeoutHandle = setTimeout(() => {
                            try {
                                controller.abort();
                            } catch (e) {}
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
                        const fetchMs = Math.round(performance.now() - t0);
                        const errorName = String(error && error.name ? error.name : '');
                        const isTimeout = errorName === 'AbortError';
                        return {
                            ok: false,
                            error: isTimeout
                                ? ('Timeout fetch service sau ' + timeoutMs + 'ms')
                                : ('Lỗi fetch service: ' + String(error && error.message ? error.message : error)),
                            error_code: isTimeout ? 'timeout' : 'fetch_error',
                            fetch_ms: fetchMs,
                        };
                    } finally {
                        if (timeoutHandle) {
                            clearTimeout(timeoutHandle);
                        }
                    }
                    const fetchMs = Math.round(performance.now() - t0);

                    if (!resp.ok) {
                        return {
                            ok: false,
                            error: 'Fetch service thất bại: HTTP ' + resp.status,
                            error_code: 'http_error',
                            status: resp.status,
                            fetch_ms: fetchMs,
                        };
                    }

                    const parser = new DOMParser();
                    const parsed = parser.parseFromString(html, 'text/html');
                    const parseResult = parseRowsFromTable(parsed);
                    if (!parseResult.ok) {
                        return {
                            ok: false,
                            error: parseResult.error,
                            error_code: 'parse_error',
                            fetch_ms: fetchMs,
                            html_length: html.length,
                        };
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
                        rows: parseResult.rows,
                        week: weekValue,
                        fetch_ms: fetchMs,
                        html_length: html.length,
                        lop: classDisplayText,
                        class_id: String(classId || ''),
                        khoi_hoc: String(khoiHoc || ''),
                        show_goi_y: !!args.showGoiY,
                    };
                }''',
                {
                    "lopText": str(lop_text),
                    "tuanNum": int(tuan_num),
                    "timeoutMs": int(timeout_ms),
                    "classMeta": copy.deepcopy(class_meta) if class_meta else {},
                    "showGoiY": bool(show_goi_y),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Fetch sổ đầu bài thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout fetch sổ đầu bài cho lớp {lop_text}, tuần {tuan_num}"
        except Exception as e:
            return False, f"Lỗi fetch sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

    def fetch_sodaubai_rows_bulk(self, lop_text, tuan_nums, timeout_s=24.0, concurrency=6,
                                 class_meta=None, show_goi_y=False):
        """Fetch nhiều tuần sổ đầu bài trong một lần evaluate để giảm roundtrip.

        Returns:
            (success, payload|message)
            payload = {
                "results": [
                    {
                        "requested_week": int,
                        "ok": bool,
                        "payload": dict,   # nếu ok
                        "error": str,      # nếu fail
                    }
                ],
                "concurrency": int,
                "requested_count": int,
            }
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        week_numbers = []
        seen = set()
        for item in list(tuan_nums or []):
            try:
                week_num = int(item)
            except Exception:
                continue
            if week_num < 1 or week_num in seen:
                continue
            seen.add(week_num)
            week_numbers.append(week_num)

        if not week_numbers:
            return True, {"results": [], "concurrency": 0, "requested_count": 0}

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
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
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

                    function parseRowsFromTable(rootDoc) {
                        let mainTable = rootDoc.querySelector('table.table');
                        if (!mainTable) {
                            const tables = rootDoc.querySelectorAll('table');
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
                            return {ok: false, error: 'Không tìm thấy bảng dữ liệu trong HTML fetch'};
                        }

                        const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                        const allRows = mainTable.querySelectorAll('tr');
                        const dataRows = [];

                        function isHeaderMarkerRow(row) {
                            const cells = Array.from(row.querySelectorAll('td'));
                            if (cells.length < 8) return false;
                            const texts = cells.map((cell) => textOf(cell));
                            const prefix = ['1', '2', '3', '4', '5'];
                            return prefix.every((value, index) => texts[index] === value);
                        }

                        function findDataStartIndex(rows) {
                            for (let i = 0; i < rows.length; i++) {
                                if (isHeaderMarkerRow(rows[i])) {
                                    return i + 1;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                const cells = rows[i].querySelectorAll('td');
                                if (cells.length < 4) continue;
                                const firstText = textOf(cells[0]);
                                const secondText = textOf(cells[1]);
                                const firstRs = parseInt(cells[0].getAttribute('rowspan') || '0', 10);
                                const secondRs = parseInt(cells[1].getAttribute('rowspan') || '0', 10);
                                const looksLikeThu = (
                                    firstRs >= 5 &&
                                    (
                                        /^(CN|[2-7])(\\s|$|\\n|\\/|-|\\d)/i.test(firstText) ||
                                        /\\d{2}\\/\\d{2}\\/\\d{4}/.test(firstText)
                                    )
                                );
                                const looksLikeBuoi = (
                                    normalize(secondText).indexOf('sang') >= 0 ||
                                    normalize(secondText).indexOf('chieu') >= 0 ||
                                    secondRs >= 4
                                );
                                if (looksLikeThu || looksLikeBuoi) {
                                    return i;
                                }
                            }

                            for (let i = 0; i < rows.length; i++) {
                                if (rows[i].querySelector(addSelector)) {
                                    return i;
                                }
                            }

                            return 0;
                        }

                        const dataStartIdx = findDataStartIndex(allRows);

                        let currentThu = '';
                        let currentThuFull = '';
                        let currentBuoi = '';
                        let thuRemaining = 0;
                        let buoiRemaining = 0;
                        let addBtnCounter = 0;

                        for (let i = dataStartIdx; i < allRows.length; i++) {
                            const cells = allRows[i].querySelectorAll('td');
                            if (cells.length < 2) continue;

                            let cellIdx = 0;
                            let thu = currentThu;
                            let thuFull = currentThuFull;
                            let buoi = currentBuoi;
                            let tiet = '';
                            let monHoc = '';
                            let ppct = '';
                            let hasData = false;
                            let hasAddBtn = false;
                            let actionCell = null;
                            let chiTietId = '';
                            let monHocId = '';
                            let phanMonId = '';
                            let tietPpctAttr = '';
                            let giaoVienIdDayCung = '';
                            let thongBao = '';
                            let redTexts = [];

                            if (thuRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                if (rs > 1) {
                                    const rawThuText = String(cells[cellIdx].textContent || '')
                                        .replace(/\\u00a0/g, ' ')
                                        .trim();
                                    thuFull = textOf(cells[cellIdx]);
                                    const upperRaw = rawThuText.toUpperCase();
                                    if (upperRaw.startsWith('CN')) {
                                        thu = 'CN';
                                        const tail = rawThuText.slice(2).trim();
                                        if (tail) thuFull = 'CN\\n' + tail;
                                    } else {
                                        const match = rawThuText.match(/[2-7]/);
                                        thu = match ? match[0] : thuFull;
                                        if (match && rawThuText.startsWith(match[0])) {
                                            const tail = rawThuText.slice(match[0].length).trim();
                                            if (tail) thuFull = match[0] + '\\n' + tail;
                                        }
                                    }
                                    currentThu = thu;
                                    currentThuFull = thuFull;
                                    thuRemaining = rs;
                                    cellIdx++;
                                }
                            }

                            if (buoiRemaining <= 0 && cells[cellIdx]) {
                                const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0', 10);
                                const text = textOf(cells[cellIdx]);
                                const lower = normalize(text);
                                const isBuoi = (
                                    lower.indexOf('sang') >= 0 ||
                                    lower.indexOf('chieu') >= 0 ||
                                    rs >= 4
                                );
                                if (isBuoi || rs > 1) {
                                    buoi = text;
                                    currentBuoi = buoi;
                                    buoiRemaining = rs > 0 ? rs : 5;
                                    cellIdx++;
                                }
                            }

                            if (cells[cellIdx]) {
                                actionCell = cells[cellIdx];
                                const btn = actionCell.querySelector(addSelector);
                                hasAddBtn = !!btn;
                                chiTietId = String(actionCell.getAttribute('chitiet_id') || '').trim();
                                monHocId = String(actionCell.getAttribute('mon_hoc_id') || '').trim();
                                phanMonId = String(actionCell.getAttribute('phan_mon_id') || '').trim();
                                tietPpctAttr = String(actionCell.getAttribute('tiet_ppct') || '').trim();
                                giaoVienIdDayCung = String(
                                    actionCell.getAttribute('giaovien_id_daycung') || ''
                                ).trim();
                                thongBao = String(actionCell.getAttribute('thong_bao') || '').trim();
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                tiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                monHoc = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            if (cells[cellIdx]) {
                                ppct = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            let tenHsNghiTiet = '';
                            let noiDungCongViec = '';
                            let diemKiemTra = '';
                            let nhanXetGiaoVien = '';
                            let diemXepLoai = '';
                            let kyTen = '';

                            if (cells[cellIdx]) {
                                tenHsNghiTiet = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                noiDungCongViec = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                diemKiemTra = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                nhanXetGiaoVien = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                diemXepLoai = textOf(cells[cellIdx]);
                                cellIdx++;
                            }
                            if (cells[cellIdx]) {
                                kyTen = textOf(cells[cellIdx]);
                                cellIdx++;
                            }

                            redTexts = Array.from(allRows[i].querySelectorAll('span'))
                                .filter((span) => {
                                    const style = String(span.getAttribute('style') || '').toLowerCase();
                                    let computed = '';
                                    try {
                                        computed = String(window.getComputedStyle(span).color || '').toLowerCase();
                                    } catch (e) {}
                                    return style.includes('red')
                                        || style.includes('#f00')
                                        || style.includes('255, 0, 0')
                                        || computed.includes('red')
                                        || computed.includes('255, 0, 0');
                                })
                                .map((span) => textOf(span))
                                .filter(Boolean);
                            const hasMeaningfulValue = (value) => {
                                const normalized = normalize(value);
                                return Boolean(normalized)
                                    && !['0', 'false', 'null', 'undefined', 'none'].includes(normalized);
                            };
                            const hasSuggestionMetadata = Boolean(
                                hasMeaningfulValue(monHocId)
                                || hasMeaningfulValue(phanMonId)
                                || hasMeaningfulValue(tietPpctAttr)
                                || hasMeaningfulValue(giaoVienIdDayCung)
                                || redTexts.length
                            );
                            hasData = Boolean(chiTietId)
                                || (monHoc.length > 0 && !hasAddBtn && !hasSuggestionMetadata);
                            const isScheduled = !hasData && Boolean(
                                hasSuggestionMetadata || monHoc || ppct || hasMeaningfulValue(thongBao)
                            );
                            const isUnplanned = !hasData && !isScheduled;
                            thuRemaining--;
                            buoiRemaining--;

                            if (tiet && /^\\d+$/.test(tiet)) {
                                const addBtnIndex = hasAddBtn ? addBtnCounter : -1;
                                if (hasAddBtn) addBtnCounter++;
                                dataRows.push({
                                    index: dataRows.length,
                                    rowIdx: i,
                                    thu: thu,
                                    thu_full: thuFull,
                                    buoi: buoi,
                                    tiet: tiet,
                                    mon_hoc: monHoc,
                                    ppct: ppct,
                                    ten_hs_nghi_tiet: tenHsNghiTiet,
                                    noi_dung_cong_viec: noiDungCongViec,
                                    diem_kiem_tra: diemKiemTra,
                                    nhan_xet_giao_vien: nhanXetGiaoVien,
                                    diem_xep_loai: diemXepLoai,
                                    ky_ten: kyTen,
                                    has_data: hasData,
                                    is_scheduled: isScheduled,
                                    is_unplanned: isUnplanned,
                                    has_add_btn: hasAddBtn,
                                    add_btn_index: addBtnIndex,
                                    chitiet_id: chiTietId,
                                    mon_hoc_id: monHocId,
                                    phan_mon_id: phanMonId,
                                    tiet_ppct_attr: tietPpctAttr,
                                    giaovien_id_daycung: giaoVienIdDayCung,
                                    thong_bao: thongBao,
                                    ppct_hint: tietPpctAttr || ppct,
                                    mon_hoc_hint: monHoc,
                                    noi_dung_hint: noiDungCongViec,
                                    red_texts: redTexts,
                                });
                            }
                        }

                        return {ok: true, rows: dataRows};
                    }

                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    const capCombo = getCombo('cboCapHoc');
                    const classMeta = args.classMeta || {};
                    const classMetaText = String(classMeta.text || '').trim();
                    const classMetaValue = String(classMeta.value || '').trim();
                    const classMetaKhoi = String(classMeta.khoi || '').trim();
                    const classMetaCap = String(classMeta.cap || '').trim();
                    let lopDisplay = classMetaText;
                    let classId = classMetaValue;
                    let khoiHoc = classMetaKhoi;
                    let capHoc = classMetaCap;

                    if (!classId) {
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
                            const label = normalize(getRecordValue(rec, lopDisplayField));
                            if (label === targetLop) {
                                classRec = rec;
                                break;
                            }
                        }
                        if (!classRec) {
                            return {ok: false, error: 'Không tìm thấy lớp ' + args.lopText + ' trong store hiện tại'};
                        }
                        lopDisplay = String(getRecordValue(classRec, lopDisplayField) || args.lopText);
                        classId = String(getRecordValue(classRec, lopValueField) || '').trim();
                        khoiHoc = String(getRecordValue(classRec, 'khoi') || '').trim();
                        capHoc = String(
                            getRecordValue(classRec, 'cap')
                            || getRecordValue(classRec, 'cap_hoc')
                            || ''
                        ).trim();
                    }
                    if (!classId) {
                        return {ok: false, error: 'Thiếu class_id để bulk fetch sổ đầu bài'};
                    }
                    if (!lopDisplay) lopDisplay = String(args.lopText || '').trim();
                    if (!khoiHoc) {
                        const khoiMatch = lopDisplay.match(/^(\\d{1,2})/);
                        khoiHoc = khoiMatch ? String(khoiMatch[1]) : '';
                    }
                    if (!capHoc) {
                        capHoc = capCombo
                            ? (capCombo.getValue ? capCombo.getValue() : capCombo.value)
                            : 2;
                    }

                    const token = typeof myToken !== 'undefined'
                        ? (typeof myToken === 'function' ? myToken() : myToken)
                        : '';
                    const userId = typeof myUserId !== 'undefined'
                        ? (typeof myUserId === 'function' ? myUserId() : myUserId)
                        : '';
                    const namHoc = String(
                        typeof phpviet_nam_hoc_v5 !== 'undefined' ? phpviet_nam_hoc_v5 : 2025
                    );
                    const url = './?load=app.sodaubai.serv.so_dau_bai'
                        + '&app_nam_hoc=' + encodeURIComponent(namHoc)
                        + '&my_token=' + encodeURIComponent(String(token || ''));

                    async function fetchOne(weekNum) {
                        try {
                            const params = new URLSearchParams();
                            params.set('my_token', String(token || ''));
                            params.set('my_user_id', String(userId || ''));
                            params.set('app_nam_hoc', namHoc);
                            params.set('capHoc', String(capHoc || ''));
                            params.set('lopHoc', classId);
                            params.set('khoiHoc', String(khoiHoc || ''));
                            params.set('tuanHoc', String(weekNum));
                            params.set('show_goi_y', args.showGoiY ? '1' : '0');

                            const controller = new AbortController();
                            const timeoutId = setTimeout(() => controller.abort(), Math.max(3000, args.timeoutMs || 12000));
                            const t0 = performance.now();
                            let resp;
                            let html = '';
                            try {
                                resp = await fetch(url, {
                                    method: 'POST',
                                    headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                                    body: params.toString(),
                                    credentials: 'same-origin',
                                    signal: controller.signal,
                                });
                                html = await resp.text();
                            } finally {
                                clearTimeout(timeoutId);
                            }
                            const fetchMs = Math.round(performance.now() - t0);

                            if (!resp.ok) {
                                return {
                                    requested_week: weekNum,
                                    ok: false,
                                    error: 'Fetch service thất bại: HTTP ' + resp.status,
                                };
                            }

                            const parser = new DOMParser();
                            const parsed = parser.parseFromString(html, 'text/html');
                            const parseResult = parseRowsFromTable(parsed);
                            if (!parseResult.ok) {
                                return {
                                    requested_week: weekNum,
                                    ok: false,
                                    error: parseResult.error,
                                };
                            }

                            let weekValue = null;
                            const weekHidden = parsed.querySelector('input[name="iTuanHoc"]');
                            if (weekHidden && weekHidden.getAttribute('value')) {
                                const weekNumHidden = parseInt(weekHidden.getAttribute('value'), 10);
                                if (!Number.isNaN(weekNumHidden)) weekValue = weekNumHidden;
                            }
                            if (weekValue === null) {
                                const weekMatch = html.match(/TUẦN\\s+(\\d+)/i);
                                if (weekMatch) weekValue = parseInt(weekMatch[1], 10);
                            }
                            if (weekValue !== null && parseInt(weekValue, 10) !== parseInt(weekNum, 10)) {
                                return {
                                    requested_week: weekNum,
                                    ok: false,
                                    error: 'Service trả về tuần ' + weekValue + ', không khớp tuần yêu cầu ' + weekNum,
                                };
                            }

                            return {
                                requested_week: weekNum,
                                ok: true,
                                payload: {
                                    rows: parseResult.rows,
                                    week: weekValue,
                                    fetch_ms: fetchMs,
                                    html_length: html.length,
                                    lop: lopDisplay,
                                    class_id: classId,
                                    khoi_hoc: String(khoiHoc || ''),
                                    show_goi_y: !!args.showGoiY,
                                },
                            };
                        } catch (e) {
                            return {
                                requested_week: weekNum,
                                ok: false,
                                error: 'Lỗi fetch tuần ' + weekNum + ': ' + String(e && e.message ? e.message : e),
                            };
                        }
                    }

                    const queue = Array.from(new Set((args.tuanNums || []).map((x) => parseInt(x, 10)).filter((x) => !Number.isNaN(x) && x > 0)));
                    const requestedCount = queue.length;
                    const results = [];
                    const workerCount = Math.max(1, Math.min(parseInt(args.concurrency || 6, 10) || 6, queue.length));

                    async function worker() {
                        while (queue.length) {
                            const weekNum = queue.shift();
                            if (!weekNum) break;
                            const item = await fetchOne(weekNum);
                            results.push(item);
                        }
                    }

                    await Promise.all(Array.from({length: workerCount}, () => worker()));
                    results.sort((a, b) => {
                        return parseInt(a.requested_week || 0, 10) - parseInt(b.requested_week || 0, 10);
                    });

                    return {
                        ok: true,
                        results: results,
                        concurrency: workerCount,
                        requested_count: requestedCount,
                    };
                }''',
                {
                    "lopText": str(lop_text),
                    "tuanNums": week_numbers,
                    "timeoutMs": max(int(timeout_s * 1000), 3000),
                    "concurrency": max(1, min(int(concurrency or 6), 8)),
                    "classMeta": copy.deepcopy(class_meta) if class_meta else {},
                    "showGoiY": bool(show_goi_y),
                },
            )
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Bulk fetch sổ đầu bài thất bại")
        except PlaywrightTimeout:
            return False, f"Timeout bulk fetch sổ đầu bài cho lớp {lop_text}"
        except Exception as e:
            return False, f"Lỗi bulk fetch sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

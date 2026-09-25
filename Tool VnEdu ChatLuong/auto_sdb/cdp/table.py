"""Đọc bảng sổ đầu bài, chế độ gợi ý KHDH, dọn dẹp và chẩn đoán UI."""

from ..compat import PlaywrightTimeout
from .config import logger


class TableMixin:
    """Đọc bảng sổ đầu bài, chế độ gợi ý KHDH, dọn dẹp và chẩn đoán UI."""

    # -----------------------------------------------------------------
    # 3.3: TABLE READING (Bảng sổ đầu bài)
    # -----------------------------------------------------------------

    def read_table(self):
        """Đọc bảng sổ đầu bài → danh sách structured rows.

        Parse HTML table với xử lý rowspan cho cột Thứ và Buổi.
        VnEdu dùng <td> cho cả header lẫn data (không có <th>).
        Nút ➕ là <a class="add add_tiet_so_dau_bai">.
        Mỗi row đại diện cho 1 tiết học (1 dòng trong bảng).

        Returns:
            (success, data) — data = list[dict] nếu success:
                [{
                    index: int,          — vị trí row trong table (0-based)
                    thu: str,            — "2", "3", ... "7" (thứ trong tuần)
                    thu_full: str,       — "2\n09/03/2026" (text gốc)
                    buoi: str,           — "Sáng" | "Chiều"
                    tiet: str,           — "1", "2", "3", "4", "5"
                    mon_hoc: str,        — tên môn học (nếu đã nhập)
                    ppct: str,           — số PPCT hiển thị trên bảng (nếu có)
                    has_data: bool,      — true nếu tiết đã có dữ liệu
                    has_add_btn: bool,   — true nếu có nút ➕
                    add_btn_index: int,  — vị trí trong danh sách nút ➕ toàn bảng
                }]
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                // === Tìm bảng dữ liệu chính ===
                // Ưu tiên table.table (VnEdu v5), fallback tìm table nhiều rowspan nhất
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

                const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                const textOf = (node) => String(node ? (node.textContent || '') : '')
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();

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
                            /sáng|chiều|sang|chieu/i.test(secondText) ||
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

                // === Parse rows với xử lý rowspan ===
                const allRows = mainTable.querySelectorAll('tr');
                const dataRows = [];

                // Tìm data start thật sự: ưu tiên dòng marker "1..11", fallback sang
                // dòng dữ liệu đầu tiên có cột Thứ/Buổi, cuối cùng mới fallback theo dấu +.
                const dataStartIdx = findDataStartIndex(allRows);

                // State cho rowspan tracking
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

                    // --- Xử lý cột Thứ (có rowspan) ---
                    if (thuRemaining <= 0 && cells[cellIdx]) {
                        const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0');
                        if (rs > 1) {
                            thuFull = cells[cellIdx].innerText.trim();
                            // Trích xuất số thứ từ text (VD: "2" + ngày tháng)
                            const match = thuFull.match(/^(\\d+)/);
                            thu = match ? match[1] : thuFull;
                            currentThu = thu;
                            currentThuFull = thuFull;
                            thuRemaining = rs;
                            cellIdx++;
                        }
                    }

                    // --- Xử lý cột Buổi (có rowspan) ---
                    if (buoiRemaining <= 0 && cells[cellIdx]) {
                        const rs = parseInt(cells[cellIdx].getAttribute('rowspan') || '0');
                        const text = cells[cellIdx].innerText.trim();
                        const isBuoi = (
                            text.toLowerCase().includes('sáng') ||
                            text.toLowerCase().includes('chiều') ||
                            text.toLowerCase().includes('sang') ||
                            text.toLowerCase().includes('chieu') ||
                            rs >= 4
                        );
                        if (isBuoi || rs > 1) {
                            buoi = text;
                            currentBuoi = buoi;
                            buoiRemaining = rs > 0 ? rs : 5;
                            cellIdx++;
                        }
                    }

                    // --- Cột Hành động (nút ➕) ---
                    // VnEdu: <a class="add add_tiet_so_dau_bai">
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

                    // --- Cột Tiết ---
                    if (cells[cellIdx]) {
                        tiet = cells[cellIdx].innerText.trim();
                        cellIdx++;
                    }

                    // --- Cột Môn học ---
                    if (cells[cellIdx]) {
                        monHoc = cells[cellIdx].innerText.trim();
                        cellIdx++;
                    }

                    // --- Cột PPCT ---
                    if (cells[cellIdx]) {
                        ppct = cells[cellIdx].innerText.trim();
                        cellIdx++;
                    }

                    redTexts = Array.from(allRows[i].querySelectorAll('span'))
                        .filter((span) => {
                            const style = String(span.getAttribute('style') || '').toLowerCase();
                            const computed = String(window.getComputedStyle(span).color || '').toLowerCase();
                            return style.includes('red')
                                || style.includes('#f00')
                                || style.includes('255, 0, 0')
                                || computed.includes('red')
                                || computed.includes('255, 0, 0');
                        })
                        .map((span) => textOf(span))
                        .filter(Boolean);
                    const hasMeaningfulValue = (value) => {
                        const normalized = String(value == null ? '' : value).trim().toLowerCase();
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

                    // Giảm counter rowspan
                    thuRemaining--;
                    buoiRemaining--;

                    // Chỉ thêm row nếu có tiết (bỏ qua header/footer/summary rows)
                    if (tiet && /^\\d+$/.test(tiet)) {
                        const addBtnIndex = hasAddBtn ? addBtnCounter : -1;
                        if (hasAddBtn) {
                            addBtnCounter++;
                        }
                        dataRows.push({
                            index: dataRows.length,
                            rowIdx: i,
                            thu: thu,
                            thu_full: thuFull,
                            buoi: buoi,
                            tiet: tiet,
                            mon_hoc: monHoc,
                            ppct: ppct,
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
                            red_texts: redTexts,
                        });
                    }
                }

                return {
                    ok: true,
                    rows: dataRows,
                    total: dataRows.length,
                    tableId: mainTable.id || '',
                    tableClass: (mainTable.className || '').substring(0, 50)
                };
            }''')

            if result.get("ok"):
                rows = result.get("rows", [])
                logger.info(f"Read table: {len(rows)} rows")
                return True, rows
            else:
                return False, result.get("error", "Unknown error")

        except PlaywrightTimeout:
            return False, "Timeout đọc bảng (page chưa load xong?)"
        except Exception as e:
            return False, f"Lỗi đọc bảng: {type(e).__name__}: {str(e)[:80]}"

    def get_empty_rows(self):
        """Lấy danh sách tiết chưa nhập (có nút ➕, chưa có dữ liệu).

        Returns:
            (success, list[dict]) — filtered rows chưa có dữ liệu
        """
        ok, data = self.read_table()
        if not ok:
            return False, data
        empty = [r for r in data if r.get("has_add_btn") and not r.get("has_data")]
        return True, empty

    def set_goi_y_khdh_mode(self, enabled=True):
        """Bật/tắt chế độ Gợi ý theo KHDH trên toolbar Sổ đầu bài.

        Ưu tiên thao tác đúng control ExtJS/radio hiện có trên trang. Fallback
        sang click DOM label chứa text "Gợi ý theo KHDH" nếu component query
        không ổn định.

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        target_enabled = bool(enabled)
        try:
            result = self.page.evaluate(
                '''(targetEnabled) => {
                    const normalize = (value) => {
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
                    };

                    const targetRootId = targetEnabled ? 'rdoEdit' : 'rdoView';
                    const targetTextToken = targetEnabled ? 'goi y theo khdh' : 'xem';

                    const readCheckedFromDom = (rootId) => {
                        const root = document.querySelector('#' + rootId);
                        if (!root) return false;
                        const cls = String(root.className || '');
                        if (cls.indexOf('x-form-cb-checked') >= 0) return true;
                        const radio = root.querySelector('input[type="radio"], input[type="checkbox"]');
                        return !!(radio && radio.checked);
                    };

                    const clickDomControl = () => {
                        const root = document.querySelector('#' + targetRootId);
                        if (root) {
                            const label = root.querySelector('label');
                            if (label) {
                                label.click();
                                return true;
                            }
                            const input = root.querySelector('input[type="radio"], input[type="checkbox"]');
                            if (input) {
                                input.click();
                                return true;
                            }
                            root.click();
                            return true;
                        }

                        const nodes = Array.from(document.querySelectorAll('label, span, div'));
                        for (const node of nodes) {
                            const text = normalize(node.innerText || node.textContent || '');
                            if (text === targetTextToken || text.includes(targetTextToken)) {
                                node.click();
                                return true;
                            }
                        }
                        return false;
                    };

                    const readCheckedFromExt = () => {
                        if (!(window.Ext && Ext.ComponentQuery)) return null;
                        try {
                            const candidates = [];
                            const radios = Ext.ComponentQuery.query('radiofield');
                            const checks = Ext.ComponentQuery.query('checkboxfield');
                            candidates.push.apply(candidates, radios);
                            candidates.push.apply(candidates, checks);
                            for (const field of candidates) {
                                let fieldId = '';
                                let fieldLabel = '';
                                let boxLabel = '';
                                let name = '';
                                try { fieldId = String(field.id || ''); } catch (e) {}
                                try { fieldLabel = String(field.fieldLabel || ''); } catch (e) {}
                                try { boxLabel = String(field.boxLabel || ''); } catch (e) {}
                                try { name = String(field.getName ? field.getName() : (field.name || '')); } catch (e) {}
                                const haystack = normalize([fieldId, fieldLabel, boxLabel, name].join(' '));
                                if (!haystack) continue;
                                const isTarget = targetEnabled
                                    ? (haystack.includes('rdoedit') || haystack.includes('goi y theo khdh'))
                                    : (haystack.includes('rdoview') || haystack === 'xem' || haystack.includes(' xem '));
                                if (isTarget) {
                                    let checked = false;
                                    try { checked = !!(field.getValue ? field.getValue() : field.checked); } catch (e2) {}
                                    return {field, checked};
                                }
                            }
                        } catch (e3) {}
                        return null;
                    };

                    const extMatch = readCheckedFromExt();
                    if (extMatch && extMatch.checked) {
                        return {ok: true, checked: true, method: 'ext_state'};
                    }
                    if (!extMatch && readCheckedFromDom(targetRootId)) {
                        return {ok: true, checked: true, method: 'dom_state'};
                    }

                    if (extMatch) {
                        try {
                            if (extMatch.field.setValue) extMatch.field.setValue(true);
                            if (extMatch.field.fireEvent) {
                                extMatch.field.fireEvent('change', extMatch.field, true);
                                extMatch.field.fireEvent('select', extMatch.field, true);
                            }
                        } catch (extErr) {}
                    } else {
                        const clicked = clickDomControl();
                        if (!clicked) {
                            return {ok: false, error: 'Không tìm thấy control ' + (targetEnabled ? 'Gợi ý theo KHDH' : 'Xem')};
                        }
                    }

                    const checked = readCheckedFromExt()
                        ? !!readCheckedFromExt().checked
                        : readCheckedFromDom(targetRootId);
                    if (!checked) {
                        return {
                            ok: false,
                            error: 'Không đổi được trạng thái ' + (targetEnabled ? 'Gợi ý theo KHDH' : 'Xem'),
                            checked: checked
                        };
                    }

                    return {ok: true, checked: checked, method: extMatch ? 'ext_set' : 'dom_click'};
                }''',
                target_enabled,
            )
            if result.get("ok"):
                return True, "Đã bật Gợi ý theo KHDH" if target_enabled else "Đã chuyển về Xem"
            return False, result.get("error", "Không đổi được trạng thái Gợi ý theo KHDH")
        except Exception as e:
            return False, f"Lỗi đổi mode Gợi ý theo KHDH: {type(e).__name__}: {str(e)[:120]}"

    def cleanup_after_automation(self, restore_view_mode=False):
        """Đưa trang Sổ đầu bài về trạng thái có thể thao tác sau automation.

        Cleanup này chỉ chạy trong tab VnEdu hiện tại: đóng popup nhập liệu còn sót,
        collapse combobox/boundlist, chờ AJAX/loadmask lắng xuống và trả về mode Xem
        nếu worker trước đó bật Gợi ý theo KHDH.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        notes = []
        try:
            try:
                ok_close, msg_close = self.close_form()
                notes.append(f"close_form={ok_close}:{msg_close}")
                self.wait_for_lesson_form_closed(timeout_s=1.2, poll_interval=0.08)
            except Exception as e_close:
                notes.append(f"close_form_error={type(e_close).__name__}")

            if restore_view_mode:
                ok_view, msg_view = self.set_goi_y_khdh_mode(False)
                notes.append(f"view_mode={ok_view}:{msg_view}")

            self._wait_page_update(timeout_s=3.0)

            result = self.page.evaluate('''() => {
                const out = {
                    combo_collapsed: 0,
                    masks_hidden: 0,
                    visible_masks_before: 0,
                    visible_masks_after: 0,
                    ajax_loading: false,
                    visible_windows: 0,
                    blocking_windows: 0,
                };
                const normalize = (value) => {
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
                };
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {width: 0, height: 0};
                    return !!(
                        (!style || (style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0)) &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                };
                const getWindowTitle = (win) => {
                    try {
                        if (win && win.title) return String(win.title || '');
                    } catch (e) {}
                    try {
                        if (win && win.header && win.header.titleCmp && win.header.titleCmp.text) {
                            return String(win.header.titleCmp.text || '');
                        }
                    } catch (e2) {}
                    try {
                        const dom = win && win.el && win.el.dom ? win.el.dom : null;
                        if (dom) {
                            const titleNode = dom.querySelector('.x-window-header-text, .x-title-text, .x-window-header');
                            if (titleNode) return String(titleNode.innerText || titleNode.textContent || '');
                        }
                    } catch (e3) {}
                    return '';
                };

                try {
                    if (window.Ext && Ext.ComponentQuery) {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            try {
                                if (combo.collapse) {
                                    combo.collapse();
                                    out.combo_collapsed += 1;
                                }
                            } catch (eCombo) {}
                        }
                        const wins = Ext.ComponentQuery.query('window');
                        for (const win of wins) {
                            try {
                                if (!(win.isVisible && win.isVisible())) continue;
                                out.visible_windows += 1;
                                const title = normalize(getWindowTitle(win));
                                const isMainSdbWindow = title.includes('quan ly so dau bai');
                                if (!isMainSdbWindow) out.blocking_windows += 1;
                            } catch (eWin) {}
                        }
                    }
                } catch (eExt) {}

                try {
                    out.ajax_loading = !!(window.Ext && Ext.Ajax && Ext.Ajax.isLoading && Ext.Ajax.isLoading());
                } catch (eAjax) {}

                const masks = Array.from(document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask'));
                out.visible_masks_before = masks.filter(isVisible).length;

                // Nếu không còn AJAX/window nào mà mask vẫn hiện, đó thường là mask mồ côi
                // sau reload/close popup; ẩn nó để trả lại khả năng click cho người dùng.
                // Bỏ qua cửa sổ chính "Quản lý sổ đầu bài"; nó luôn tồn tại và không phải
                // popup chặn thao tác.
                if (!out.ajax_loading && out.blocking_windows === 0 && out.visible_masks_before > 0) {
                    for (const mask of masks) {
                        try {
                            if (!isVisible(mask)) continue;
                            mask.setAttribute('data-auto-sdb-hidden-orphan-mask', '1');
                            mask.style.display = 'none';
                            mask.style.visibility = 'hidden';
                            mask.style.pointerEvents = 'none';
                            out.masks_hidden += 1;
                        } catch (eMask) {}
                    }
                }
                out.visible_masks_after = masks.filter(isVisible).length;
                return out;
            }''')
            notes.append(f"dom_cleanup={result}")
            self._wait_page_update(timeout_s=2.0)
            return True, " | ".join(notes)
        except Exception as e:
            return False, f"Lỗi cleanup sau automation: {type(e).__name__}: {str(e)[:120]}"

    def diagnose_ui_state(self):
        """Đọc trạng thái thao tác chính của trang VNEDU sau automation.

        Dùng cho log và nút khôi phục: radio Xem/KHDH, dropdown, AJAX, mask,
        popup đang mở. Không sửa dữ liệu trên trang.
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                const normalize = (value) => {
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
                };
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {width: 0, height: 0};
                    return !!(
                        (!style || (style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0)) &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                };
                const readRadioChecked = (rootId) => {
                    const root = document.querySelector('#' + rootId);
                    if (!root) return null;
                    const cls = String(root.className || '');
                    if (cls.includes('x-form-cb-checked')) return true;
                    const input = root.querySelector('input[type="radio"], input[type="checkbox"]');
                    if (input) return !!input.checked;
                    return false;
                };
                const getWindowTitle = (win) => {
                    try {
                        if (win && win.title) return String(win.title || '');
                    } catch (e) {}
                    try {
                        if (win && win.header && win.header.titleCmp && win.header.titleCmp.text) {
                            return String(win.header.titleCmp.text || '');
                        }
                    } catch (e2) {}
                    try {
                        const dom = win && win.el && win.el.dom ? win.el.dom : null;
                        if (dom) {
                            const titleNode = dom.querySelector('.x-window-header-text, .x-title-text, .x-window-header');
                            if (titleNode) return String(titleNode.innerText || titleNode.textContent || '');
                        }
                    } catch (e3) {}
                    return '';
                };

                const out = {
                    url: location.href,
                    title: document.title || '',
                    has_ext: !!(window.Ext && Ext.ComponentQuery),
                    ajax_loading: false,
                    view_checked: readRadioChecked('rdoView'),
                    khdh_checked: readRadioChecked('rdoEdit'),
                    visible_masks: 0,
                    visible_boundlists: 0,
                    visible_windows: 0,
                    blocking_windows: 0,
                    combos: [],
                    has_sdb_table: !!document.querySelector('table.table, a.add_tiet_so_dau_bai'),
                };

                try {
                    out.ajax_loading = !!(window.Ext && Ext.Ajax && Ext.Ajax.isLoading && Ext.Ajax.isLoading());
                } catch (eAjax) {}

                try {
                    const masks = Array.from(document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask'));
                    out.visible_masks = masks.filter(isVisible).length;
                } catch (eMask) {}

                try {
                    out.visible_boundlists = Array.from(document.querySelectorAll('.x-boundlist, .x-combo-list'))
                        .filter(isVisible).length;
                } catch (eList) {}

                if (window.Ext && Ext.ComponentQuery) {
                    try {
                        const combos = Ext.ComponentQuery.query('combobox');
                        for (const combo of combos) {
                            let name = '';
                            let raw = '';
                            let storeCount = null;
                            try { name = combo.getName ? combo.getName() : (combo.name || ''); } catch (e) {}
                            try { raw = combo.getRawValue ? combo.getRawValue() : (combo.rawValue || ''); } catch (e2) {}
                            try { storeCount = combo.store && combo.store.getCount ? combo.store.getCount() : null; } catch (e3) {}
                            if (['cboCapHoc', 'cboTuanHoc', 'cboLopHoc'].includes(name)) {
                                out.combos.push({
                                    id: combo.id || '',
                                    name,
                                    raw: String(raw || ''),
                                    disabled: !!combo.disabled,
                                    readOnly: !!combo.readOnly,
                                    expanded: !!combo.isExpanded,
                                    storeCount,
                                });
                            }
                        }
                    } catch (eCombo) {}

                    try {
                        const wins = Ext.ComponentQuery.query('window');
                        for (const win of wins) {
                            try {
                                if (!(win.isVisible && win.isVisible())) continue;
                                out.visible_windows += 1;
                                const title = normalize(getWindowTitle(win));
                                if (!title.includes('quan ly so dau bai')) out.blocking_windows += 1;
                            } catch (eWin) {}
                        }
                    } catch (eWinOuter) {}
                }
                return out;
            }''')
            return True, result
        except Exception as e:
            return False, f"Lỗi đọc trạng thái UI: {type(e).__name__}: {str(e)[:120]}"

"""Màn Thống kê nhập sổ đầu bài."""

import json

from ..compat import PlaywrightTimeout


class StatsMixin:
    """Màn Thống kê nhập sổ đầu bài."""

    def _is_v5_stats_ready(self):
        """Kiểm tra đã vào đúng màn Thống kê nhập sổ đầu bài hay chưa."""
        if not self.page:
            return False
        try:
            if self._is_session_expired_page():
                return False
            return bool(self.page.evaluate('''() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const weekInput = document.querySelector('input[name="cboTuanHoc"]');
                const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                    .filter(visible);
                const bodyText = String(document.body ? document.body.innerText || '' : '');
                return !!weekInput &&
                    classInputs.length >= 2 &&
                    /GV chưa nhập lịch/i.test(bodyText) &&
                    /Thống kê nhập sổ đầu bài/i.test(bodyText);
            }'''))
        except Exception:
            return False

    def ensure_thong_ke_nhap_sodau_bai(self, username="", password=""):
        """Đảm bảo tab hiện tại đang ở màn Thống kê nhập sổ đầu bài."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            if self._is_session_expired_page():
                ok_ready, ready_payload = self.ensure_chi_tiet_sodau_bai(username, password)
                if not ok_ready:
                    return False, ready_payload
            if self._is_v5_stats_ready():
                return True, {
                    "stage": "already_ready",
                    "url": self.page.url,
                    "title": self.page.title(),
                }

            ok_ready, ready_payload = self.ensure_chi_tiet_sodau_bai(username, password)
            if not ok_ready:
                return False, ready_payload

            clicked = False
            for selector in [
                'text="Thống kê nhập sổ đầu bài"',
                'div.x-grid-cell-inner:has-text("Thống kê nhập sổ đầu bài")',
            ]:
                try:
                    loc = self.page.locator(selector).first
                    if loc.count() > 0:
                        loc.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                clicked = self._click_text_fallback("Thống kê nhập sổ đầu bài")
            if not clicked:
                return False, "Không tìm thấy node 'Thống kê nhập sổ đầu bài'"

            ok_wait, wait_err = self._wait_until(
                '''() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const weekInput = document.querySelector('input[name="cboTuanHoc"]');
                    const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                        .filter(visible);
                    const bodyText = String(document.body ? document.body.innerText || '' : '');
                    return !!weekInput &&
                        classInputs.length >= 2 &&
                        /GV chưa nhập lịch/i.test(bodyText);
                }''',
                timeout_s=20,
            )
            if not ok_wait:
                return False, f"Không vào được màn Thống kê nhập sổ đầu bài: {wait_err}"

            return True, {
                "stage": "stats_ready",
                "url": self.page.url,
                "title": self.page.title(),
            }
        except Exception as e:
            return False, f"Lỗi mở màn Thống kê nhập sổ đầu bài: {type(e).__name__}: {str(e)[:120]}"

    def _stats_select_field(self, field_kind, target_text):
        """Chọn filter trên màn thống kê theo kind: week | grade | class."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        field_kind = str(field_kind or "").strip().lower()
        target_text = str(target_text or "").strip()
        if field_kind not in {"week", "grade", "class"}:
            return False, f"field_kind không hợp lệ: {field_kind}"
        if not target_text:
            return False, "Thiếu target_text"

        try:
            result = self.page.evaluate(
                '''async (args) => {
                    const cleanText = (text) => String(text == null ? '' : text)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const norm = (text) => cleanText(text).toLowerCase();
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                    const fireClick = (el) => {
                        if (!el) return false;
                        const evtOpts = {bubbles: true, cancelable: true, view: window};
                        el.dispatchEvent(new MouseEvent('mouseover', evtOpts));
                        el.dispatchEvent(new MouseEvent('mousedown', evtOpts));
                        el.dispatchEvent(new MouseEvent('mouseup', evtOpts));
                        el.click();
                        return true;
                    };
                    const getFieldInput = (kind) => {
                        if (kind === 'week') {
                            return Array.from(document.querySelectorAll('input[name="cboTuanHoc"]'))
                                .find(visible) || null;
                        }
                        const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                            .filter(visible);
                        if (kind === 'grade') return classInputs[0] || null;
                        if (kind === 'class') return classInputs[classInputs.length - 1] || null;
                        return null;
                    };
                    const input = getFieldInput(args.kind);
                    if (!input) {
                        return {ok: false, error: 'Không tìm thấy input filter ' + args.kind};
                    }
                    const currentText = cleanText(input.value || input.getAttribute('value') || '');
                    if (norm(currentText) === norm(args.targetText)) {
                        return {ok: true, selectedText: currentText, unchanged: true};
                    }

                    const wrapper = input.closest('.x-form-trigger-wrap') || input.parentElement;
                    const trigger = wrapper
                        ? wrapper.querySelector('.x-form-trigger, .x-trigger-index-0')
                        : null;
                    if (!trigger) {
                        return {ok: false, error: 'Không tìm thấy trigger của field ' + args.kind};
                    }
                    fireClick(trigger);
                    await sleep(180);

                    const items = Array.from(document.querySelectorAll('.x-boundlist-item, .x-combo-list-item, li'))
                        .filter(visible);
                    const exact = items.find((node) => norm(node.textContent || node.innerText || '') === norm(args.targetText));
                    const partial = items.find((node) => norm(node.textContent || node.innerText || '').includes(norm(args.targetText)));
                    const targetNode = exact || partial;
                    if (!targetNode) {
                        return {
                            ok: false,
                            error: 'Không thấy option ' + args.targetText,
                            available: items.slice(0, 40).map(node => cleanText(node.textContent || node.innerText || '')).filter(Boolean),
                        };
                    }
                    fireClick(targetNode);
                    await sleep(280);

                    const selectedText = cleanText(input.value || input.getAttribute('value') || '');
                    return {
                        ok: norm(selectedText) === norm(args.targetText) || norm(selectedText).includes(norm(args.targetText)),
                        selectedText,
                    };
                }''',
                {"kind": field_kind, "targetText": target_text},
            )
            if result.get("ok"):
                return True, str(result.get("selectedText") or target_text).strip()
            available = list(result.get("available") or [])
            message = str(result.get("error", f"Không chọn được {target_text}")).strip()
            if available:
                message += f" | Có sẵn: {', '.join(available[:12])}"
            return False, message
        except PlaywrightTimeout:
            return False, f"Timeout chọn filter thống kê {field_kind}"
        except Exception as e:
            return False, f"Lỗi chọn filter thống kê {field_kind}: {type(e).__name__}: {str(e)[:120]}"

    def stats_select_week(self, tuan_text):
        """Chọn tuần trên màn thống kê."""
        text = str(tuan_text or "").strip()
        if text.isdigit():
            text = f"Tuần {text}"
        return self._stats_select_field("week", text)

    def stats_select_grade(self, grade_text):
        """Chọn khối trên màn thống kê."""
        return self._stats_select_field("grade", grade_text)

    def stats_select_class(self, lop_text):
        """Chọn lớp trên màn thống kê."""
        return self._stats_select_field("class", lop_text)

    def stats_get_filter_options(self, field_kind):
        """Đọc option thật đang được cấp quyền trên màn thống kê."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        if field_kind not in {"week", "grade", "class"}:
            return False, f"Filter thống kê không hợp lệ: {field_kind}"
        try:
            result = self.page.evaluate(
                '''async (kind) => {
                    const cleanText = (value) => String(value == null ? '' : value)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1
                            && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const fireClick = (el) => {
                        if (!el) return false;
                        const opts = {bubbles: true, cancelable: true, view: window};
                        el.dispatchEvent(new MouseEvent('mousedown', opts));
                        el.dispatchEvent(new MouseEvent('mouseup', opts));
                        el.click();
                        return true;
                    };
                    const classInputs = Array.from(document.querySelectorAll('input[name="cboLopHoc"]'))
                        .filter(visible);
                    let input = null;
                    if (kind === 'week') {
                        input = Array.from(document.querySelectorAll('input[name="cboTuanHoc"]'))
                            .find(visible) || null;
                    } else if (kind === 'grade') {
                        input = classInputs[0] || null;
                    } else {
                        input = classInputs[classInputs.length - 1] || null;
                    }
                    if (!input) return {ok: false, error: 'Không tìm thấy input ' + kind};

                    let combo = null;
                    if (window.Ext && Ext.ComponentQuery) {
                        for (const item of Ext.ComponentQuery.query('combobox')) {
                            try {
                                const itemInput = item.inputEl && item.inputEl.dom ? item.inputEl.dom : null;
                                if (itemInput === input || (itemInput && itemInput.id === input.id)) {
                                    combo = item;
                                    break;
                                }
                            } catch (e) {}
                        }
                    }
                    if (combo) {
                        try {
                            if (combo.expand) combo.expand();
                        } catch (e) {}
                    } else {
                        const wrapper = input.closest('.x-form-trigger-wrap') || input.parentElement;
                        const trigger = wrapper
                            ? wrapper.querySelector('.x-form-trigger, .x-trigger-index-0')
                            : null;
                        if (!fireClick(trigger)) {
                            return {ok: false, error: 'Không mở được dropdown ' + kind};
                        }
                    }
                    let store = combo ? (combo.getStore ? combo.getStore() : combo.store) : null;
                    const displayField = combo ? (combo.displayField || 'ten') : 'ten';
                    let items = [];
                    for (let attempt = 0; attempt < 10; attempt++) {
                        store = combo ? (combo.getStore ? combo.getStore() : combo.store) : null;
                        items = store && store.getRange
                            ? store.getRange()
                            : (store && store.data && store.data.items) || [];
                        if (items.length) break;
                        await new Promise((resolve) => setTimeout(resolve, 120));
                    }
                    const options = [];
                    const seen = new Set();
                    for (const record of Array.from(items || [])) {
                        let value = '';
                        try {
                            value = record.get ? record.get(displayField) : '';
                        } catch (e) {}
                        if (!value && record && record.data) {
                            value = record.data[displayField]
                                || record.data.ten
                                || record.data.name
                                || record.data.text
                                || '';
                        }
                        const text = cleanText(value);
                        const key = text.toLocaleLowerCase('vi');
                        if (!text || seen.has(key)) continue;
                        seen.add(key);
                        options.push(text);
                    }
                    if (!options.length) {
                        const domItems = Array.from(
                            document.querySelectorAll('.x-boundlist-item, .x-combo-list-item')
                        ).filter(visible);
                        for (const node of domItems) {
                            const text = cleanText(node.textContent || node.innerText || '');
                            const key = text.toLocaleLowerCase('vi');
                            if (!text || seen.has(key)) continue;
                            seen.add(key);
                            options.push(text);
                        }
                    }
                    try {
                        if (combo && combo.collapse) combo.collapse();
                    } catch (e) {}
                    return options.length
                        ? {ok: true, options}
                        : {ok: false, error: 'Dropdown ' + kind + ' không có option'};
                }''',
                field_kind,
            )
            if result.get("ok"):
                return True, list(result.get("options") or [])
            return False, result.get("error", f"Không đọc được option {field_kind}")
        except Exception as e:
            return False, f"Lỗi đọc option thống kê {field_kind}: {type(e).__name__}: {str(e)[:120]}"

    def stats_set_missing_only(self, enabled=True):
        """Bật/tắt checkbox 'GV chưa nhập lịch' trên màn thống kê."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        target_enabled = bool(enabled)
        try:
            result = self.page.evaluate(
                '''async (targetEnabled) => {
                    const cleanText = (text) => String(text == null ? '' : text)
                        .replace(/\\u00a0/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 1 && rect.height > 1 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                    const fireClick = (el) => {
                        if (!el) return false;
                        const evtOpts = {bubbles: true, cancelable: true, view: window};
                        el.dispatchEvent(new MouseEvent('mouseover', evtOpts));
                        el.dispatchEvent(new MouseEvent('mousedown', evtOpts));
                        el.dispatchEvent(new MouseEvent('mouseup', evtOpts));
                        el.click();
                        return true;
                    };
                    const fieldCandidates = Array.from(document.querySelectorAll('.x-form-item, .x-field'))
                        .filter((node) => /GV chưa nhập lịch/i.test(cleanText(node.textContent || '')) && visible(node))
                        .sort((a, b) => cleanText(a.textContent || '').length - cleanText(b.textContent || '').length);
                    const field = fieldCandidates[0] || null;
                    if (!field) {
                        return {ok: false, error: 'Không tìm thấy checkbox GV chưa nhập lịch'};
                    }
                    const isChecked = field.classList.contains('x-form-cb-checked');
                    if (isChecked === targetEnabled) {
                        return {ok: true, enabled: isChecked, unchanged: true};
                    }
                    const target = field.querySelector('input, .x-form-cb, .x-form-checkbox') || field;
                    fireClick(target);
                    await sleep(250);
                    return {
                        ok: field.classList.contains('x-form-cb-checked') === targetEnabled,
                        enabled: field.classList.contains('x-form-cb-checked'),
                    };
                }''',
                target_enabled,
            )
            if result.get("ok"):
                return True, "Đã bật lọc GV chưa nhập" if result.get("enabled") else "Đã tắt lọc GV chưa nhập"
            return False, result.get("error", "Không đổi được checkbox GV chưa nhập lịch")
        except Exception as e:
            return False, f"Lỗi đổi checkbox GV chưa nhập lịch: {type(e).__name__}: {str(e)[:120]}"

    def stats_read_missing_teacher_rows(self, expected_class="", timeout_s=6.0):
        """Đọc bảng thống kê GV chưa nhập lịch ở màn thống kê nhập sổ đầu bài."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"
        try:
            if self._is_session_expired_page():
                return False, "Phiên làm việc VnEdu đã hết hiệu lực. Hãy đăng nhập lại rồi chạy thống kê."
            expected_class = str(expected_class or "").strip()
            if expected_class:
                expected_json = json.dumps(expected_class)
                predicate_js = (
                    "() => {"
                    f"const expectedClass = {expected_json};"
                    "const cleanText = (text) => String(text == null ? '' : text)"
                    ".replace(/\\u00a0/g, ' ')"
                    ".replace(/\\s+/g, ' ')"
                    ".trim()"
                    ".toLowerCase();"
                    "const target = cleanText(expectedClass);"
                    "if (!target) return true;"
                    "for (const table of Array.from(document.querySelectorAll('table'))) {"
                    "const text = cleanText(table.innerText || '');"
                    "if (!text.includes('họ tên') || !text.includes('tổng tiết')) continue;"
                    "const rows = Array.from(table.querySelectorAll('tr')).map((tr) => cleanText(tr.textContent || ''));"
                    "const classLabel = rows.find((row) => row.startsWith('lớp:')) || '';"
                    "if (classLabel === ('lớp: ' + target) || classLabel.endsWith(target)) return true;"
                    "}"
                    "return false;"
                    "}"
                )
                wait_ok, wait_err = self._wait_until(
                    predicate_js,
                    timeout_s=max(float(timeout_s or 0), 0.5),
                )
                if not wait_ok:
                    return False, f"Timeout chờ bảng thống kê cập nhật cho lớp {expected_class}: {wait_err}"
            result = self.page.evaluate('''() => {
                const cleanText = (text) => String(text == null ? '' : text)
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const dayMap = [2, 3, 4, 5, 6, 7, 8];
                const headers = {
                    morning: dayMap.map((thu, idx) => ({thu, buoi: 'Sáng', columnIndex: 5 + idx})),
                    afternoon: dayMap.map((thu, idx) => ({thu, buoi: 'Chiều', columnIndex: 12 + idx})),
                };
                const table = Array.from(document.querySelectorAll('table'))
                    .find((node) => /Họ tên/.test(cleanText(node.innerText || '')) && /Tổng tiết/.test(cleanText(node.innerText || '')));
                if (!table) {
                    return {ok: false, error: 'Không tìm thấy bảng thống kê giáo viên'};
                }

                const classLabel = Array.from(table.querySelectorAll('tr'))
                    .map((tr) => cleanText(tr.textContent || ''))
                    .find((text) => /^Lớp:/i.test(text)) || '';
                const classText = cleanText(classLabel.replace(/^Lớp:\\s*/i, ''));

                const rows = [];
                for (const tr of Array.from(table.querySelectorAll('tr'))) {
                    const cells = Array.from(tr.querySelectorAll('td'));
                    if (cells.length < 19) continue;
                    const stt = cleanText(cells[0].textContent || '');
                    const teacher = cleanText(cells[1].textContent || '');
                    const subject = cleanText(cells[2].textContent || '');
                    const className = cleanText(cells[3].textContent || '');
                    const totalText = cleanText(cells[4].textContent || '');
                    if (!teacher || /^tổng$/i.test(teacher) || /^tổng$/i.test(stt)) continue;
                    const matchTotal = totalText.match(/\\d+/);
                    const totalMissing = matchTotal ? parseInt(matchTotal[0], 10) : 0;
                    const counts = [];
                    for (const item of [...headers.morning, ...headers.afternoon]) {
                        const text = cleanText((cells[item.columnIndex] || {}).textContent || '');
                        const match = text.match(/\\d+/);
                        if (!match) continue;
                        counts.push({
                            thu: item.thu,
                            buoi: item.buoi,
                            count: parseInt(match[0], 10),
                            column_index: item.columnIndex,
                        });
                    }
                    rows.push({
                        stt,
                        teacher_name: teacher,
                        mon_hoc: subject,
                        lop: className || classText,
                        total_missing: totalMissing,
                        counts,
                    });
                }

                return {
                    ok: true,
                    lop: classText,
                    rows,
                };
            }''')
            if result.get("ok"):
                return True, result
            return False, result.get("error", "Không đọc được bảng thống kê GV chưa nhập")
        except PlaywrightTimeout:
            return False, "Timeout đọc bảng thống kê GV chưa nhập"
        except Exception as e:
            return False, f"Lỗi đọc bảng thống kê GV chưa nhập: {type(e).__name__}: {str(e)[:120]}"

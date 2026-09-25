"""Đọc popup tiết học và chờ dữ liệu slot."""

import re
import time
import unicodedata


class LessonFormMixin:
    """Đọc popup tiết học và chờ dữ liệu slot."""

    def get_open_lesson_form_snapshot(self):
        """Đọc nhanh dữ liệu hiện có trong popup tiết học đang mở."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate(
                '''() => {
                    function isLessonTitle(title) {
                        const t = String(title || '').toLowerCase();
                        return (
                            t.indexOf('chi ti') >= 0 ||
                            t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                            t.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                            t.indexOf('so dau bai') >= 0
                        );
                    }

                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, error: 'ExtJS not available'};
                    }

                    let formPanel = null;
                    let title = '';
                    const wins = Ext.ComponentQuery.query('window');
                    for (const win of wins) {
                        try {
                            if (!(win.isVisible && win.isVisible())) continue;
                            if (!isLessonTitle(win.title || '')) continue;
                            const candidate = win.down ? win.down('form') : null;
                            if (candidate && candidate.getForm) {
                                formPanel = candidate;
                                title = String(win.title || '');
                                break;
                            }
                        } catch (e) {}
                    }

                    if (!formPanel || !formPanel.getForm) {
                        return {ok: false, error: 'Không tìm thấy popup chi tiết tiết học'};
                    }

                    const form = formPanel.getForm();
                    const fields = form.getFields().items || [];
                    const payload = {title: title, fields: {}, labels: {}};
                    for (const field of fields) {
                        let name = '';
                        if (!field) continue;
                        try { name = field.getName ? field.getName() : (field.name || ''); } catch (e) {}
                        if (!name) continue;
                        let value = '';
                        let raw = '';
                        let label = '';
                        let xtype = '';
                        try { value = field.getValue ? field.getValue() : ''; } catch (e) {}
                        try { raw = field.getRawValue ? field.getRawValue() : ''; } catch (e) {}
                        try { label = field.fieldLabel || ''; } catch (e) {}
                        try { xtype = field.getXType ? field.getXType() : (field.xtype || ''); } catch (e) {}
                        payload.fields[name] = {
                            value: String(value == null ? '' : value),
                            raw: String(raw == null ? '' : raw),
                            label: String(label || ''),
                            xtype: String(xtype || ''),
                        };
                    }
                    return {ok: true, payload: payload};
                }'''
            )
            if result.get("ok"):
                return True, result.get("payload", {})
            return False, result.get("error", "Không đọc được popup tiết học")
        except Exception as e:
            return False, f"Lỗi đọc popup tiết học: {type(e).__name__}: {str(e)[:120]}"

    def fill_form_minimal(self, hs_nghi, nhan_xet, diem):
        """Chỉ điền các field an toàn cho mode KHDH: nghỉ, nhận xét, điểm."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        payload = {
            "hsNghi": str(hs_nghi),
            "nhanXet": str(nhan_xet),
            "diem": str(diem),
        }
        try:
            result = self.page.evaluate(
                '''(args) => {
                    function isLessonTitle(title) {
                        const t = String(title || '').toLowerCase();
                        return (
                            t.indexOf('chi ti') >= 0 ||
                            t.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                            t.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                            t.indexOf('so dau bai') >= 0
                        );
                    }

                    function setPlainField(form, fieldName, value, label, log, errors) {
                        if (value === null || value === undefined || value === '') {
                            log.push(label + ': skipped');
                            return true;
                        }
                        try {
                            const field = form.findField(fieldName);
                            if (!field) {
                                errors.push('Field not found: ' + fieldName);
                                return false;
                            }
                            field.setValue(value);
                            try {
                                field.fireEvent('change', field, value, field.originalValue);
                                field.fireEvent('blur', field);
                            } catch (e2) {}
                            log.push(label + '=' + String(value).substring(0, 40));
                            return true;
                        } catch (e3) {
                            errors.push(label + ': ' + e3.message);
                            return false;
                        }
                    }

                    if (!(window.Ext && Ext.ComponentQuery)) {
                        return {ok: false, errors: ['ExtJS not available'], log: []};
                    }

                    let formPanel = null;
                    const wins = Ext.ComponentQuery.query('window');
                    for (const win of wins) {
                        try {
                            if (!(win.isVisible && win.isVisible())) continue;
                            if (!isLessonTitle(win.title || '')) continue;
                            const candidate = win.down ? win.down('form') : null;
                            if (candidate && candidate.getForm) {
                                formPanel = candidate;
                                break;
                            }
                        } catch (e) {}
                    }
                    if (!formPanel || !formPanel.getForm) {
                        return {ok: false, errors: ['Không tìm thấy popup chi tiết tiết học'], log: []};
                    }

                    const form = formPanel.getForm();
                    const log = [];
                    const errors = [];
                    setPlainField(form, 'soluong_nghi', args.hsNghi, 'soluong_nghi', log, errors);
                    setPlainField(form, 'nhan_xet', args.nhanXet, 'nhan_xet', log, errors);
                    setPlainField(form, 'diem', args.diem, 'diem', log, errors);
                    return {ok: errors.length === 0, errors: errors, log: log};
                }''',
                payload,
            )

            if result.get("ok"):
                return True, "Đã nhập tối thiểu: " + ", ".join(result.get("log", []))
            errors = result.get("errors", [])
            return False, "Lỗi nhập tối thiểu: " + "; ".join(errors)
        except Exception as e:
            return False, f"Lỗi nhập tối thiểu: {type(e).__name__}: {str(e)[:120]}"

    @staticmethod
    def _normalize_thu_token(value):
        """Quy mọi biểu diễn 'thứ' về một token chuẩn: '2'..'7' hoặc 'CN'.

        Xử lý nhất quán mọi nguồn dữ liệu khác nhau:
          - UI slot: 8 / '8'        → 'CN'
          - read_table: 'CN\\n14/03' → 'CN'  | '2\\n09/03' → '2'
          - fetch_sodaubai_rows: 'CN' → 'CN'
          - read_khdh_suggested_rows: '8' → 'CN'

        Lưu ý: phải kiểm tra CN TRƯỚC khi regex [2-7] để tránh bắt nhầm
        chữ số trong phần ngày tháng (vd 'CN\\n14/03/2026' chứa '4','3','2').
        """
        text = str(value if value is not None else "").strip()
        if not text:
            return ""
        upper = text.upper()
        if upper.startswith("CN") or text == "8":
            return "CN"
        match = re.search(r"[2-7]", text)
        if match:
            return match.group()
        if "8" in text:
            return "CN"
        return upper

    @staticmethod
    def _normalize_buoi_token(value):
        """Quy 'buổi' về token chuẩn 'sang'/'chieu' (bỏ dấu, không phân biệt hoa thường)."""
        text = unicodedata.normalize("NFD", str(value if value is not None else ""))
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = text.strip().lower()
        if "sang" in text or text == "1":
            return "sang"
        if "chieu" in text or text == "2":
            return "chieu"
        return text

    @staticmethod
    def _slot_row_matches(row, thu, buoi, tiet):
        """So khớp một row của bảng với slot mục tiêu.

        Dùng token chuẩn hoá cho thu/buoi để khớp được Chủ nhật (CN/8) và
        các biến thể dấu/hoa-thường giữa các nguồn dữ liệu khác nhau.
        """
        row_thu = LessonFormMixin._normalize_thu_token(row.get("thu", ""))
        row_buoi = LessonFormMixin._normalize_buoi_token(row.get("buoi", ""))
        row_tiet = str(row.get("tiet", "")).strip()
        return (
            row_thu == LessonFormMixin._normalize_thu_token(thu)
            and row_buoi == LessonFormMixin._normalize_buoi_token(buoi)
            and row_tiet == str(tiet).strip()
        )

    def wait_for_slot_data(self, thu, buoi, tiet, timeout_s=6.0, poll_interval=0.5):
        """Chờ đến khi 1 slot trên bảng xuất hiện dữ liệu sau khi lưu.

        Args:
            thu: int|str — thứ trong tuần
            buoi: str — "Sáng" | "Chiều"
            tiet: int|str — tiết học
            timeout_s: float — thời gian chờ tối đa
            poll_interval: float — chu kỳ poll

        Returns:
            (success: bool, message: str, row: dict|None)
        """
        deadline = time.time() + max(timeout_s, 0.5)
        target_thu = str(thu).strip()
        target_buoi = str(buoi).strip()
        target_tiet = str(tiet).strip()
        last_msg = "Không tìm thấy row mục tiêu"
        last_row = None

        while time.time() < deadline:
            ok, table_data = self.read_table()
            if not ok:
                last_msg = f"Lỗi đọc bảng: {table_data}"
                time.sleep(poll_interval)
                continue

            for row in table_data:
                if self._slot_row_matches(row, target_thu, target_buoi, target_tiet):
                    last_row = row
                    if row.get("has_data", False):
                        mon_hoc = str(row.get("mon_hoc", "")).strip()
                        return True, f"Row đã có dữ liệu ({mon_hoc or 'không rõ môn'})", row
                    last_msg = "Row đã tìm thấy nhưng chưa có dữ liệu"
                    break

            time.sleep(poll_interval)

        return False, last_msg, last_row

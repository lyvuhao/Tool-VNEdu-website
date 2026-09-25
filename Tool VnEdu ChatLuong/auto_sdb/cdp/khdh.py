"""Đọc context và các dòng gợi ý theo KHDH."""

from .config import logger


class KHDHScheduleMixin:
    """Đọc context và các dòng gợi ý theo KHDH."""

    def probe_khdh_schedule_context(self):
        """Kiểm tra nhanh trang live hiện tại có đủ control cho mode KHDH hay không."""
        if not self.is_connected:
            return False, "Chưa kết nối CDP", {}

        try:
            result = self.page.evaluate(
                '''() => {
                    const hasExt = !!(window.Ext && Ext.ComponentQuery);
                    const hasKhdhToggle = !!document.querySelector('#rdoEdit');
                    const hasWeek = !!document.querySelector('#cboTuanHoc, input[name="cboTuanHoc"]');
                    const hasClass = !!document.querySelector('#cboLopHoc, input[name="cboLopHoc"]');
                    const hasTable = !!document.querySelector('table.table, table');
                    let title = '';
                    try { title = String(document.title || ''); } catch (e) {}
                    return {
                        ok: hasExt && hasKhdhToggle && hasWeek && hasClass && hasTable,
                        title,
                        hasExt,
                        hasKhdhToggle,
                        hasWeek,
                        hasClass,
                        hasTable,
                    };
                }'''
            )
            if result.get("ok"):
                return True, "Trang live đã sẵn sàng cho mode KHDH", result
            missing = []
            if not result.get("hasExt"):
                missing.append("ExtJS")
            if not result.get("hasKhdhToggle"):
                missing.append("nút Gợi ý theo KHDH")
            if not result.get("hasWeek"):
                missing.append("dropdown Tuần")
            if not result.get("hasClass"):
                missing.append("dropdown Lớp")
            if not result.get("hasTable"):
                missing.append("bảng Sổ đầu bài")
            detail = ", ".join(missing) if missing else "context cần thiết"
            return False, f"Trang live chưa sẵn sàng cho KHDH: thiếu {detail}", result
        except Exception as e:
            return False, f"Lỗi kiểm tra context KHDH: {type(e).__name__}: {str(e)[:120]}", {}

    def read_khdh_suggested_rows(self):
        """Đọc các row gợi ý theo KHDH đang hiển thị bằng chữ đỏ trên bảng live.

        Mỗi row trả về đủ metadata để worker có thể click đúng nút `+` của
        chính row đó và verify popup sau khi mở.

        Returns:
            (success, rows|message)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate(
                '''() => {
                    function textOf(node) {
                        return String(node ? (node.textContent || '') : '')
                            .replace(/\\u00a0/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    }

                    function normalizeBuoi(raw) {
                        const value = String(raw || '').trim();
                        if (!value) return '';
                        if (value === '1') return 'Sáng';
                        if (value === '2') return 'Chiều';
                        return value;
                    }

                    let mainTable = document.querySelector('table.table');
                    if (!mainTable) {
                        const tables = document.querySelectorAll('table');
                        let maxRowspan = 0;
                        for (const table of tables) {
                            const rowspans = table.querySelectorAll('td[rowspan]');
                            if (rowspans.length > maxRowspan) {
                                maxRowspan = rowspans.length;
                                mainTable = table;
                            }
                        }
                    }
                    if (!mainTable) {
                        return {ok: false, error: 'Không tìm thấy bảng Sổ đầu bài'};
                    }

                    const rows = Array.from(mainTable.querySelectorAll('tr'));
                    const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';
                    const items = [];
                    let currentThu = '';
                    let currentBuoi = '';
                    let currentDate = '';
                    let addBtnIndex = 0;

                    for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
                        const tr = rows[rowIdx];
                        const firstActionCell = tr.querySelector('td[thu][tiet]');
                        if (firstActionCell) {
                            const attrThu = String(firstActionCell.getAttribute('thu') || '').trim();
                            if (attrThu) currentThu = attrThu === 'CN' ? '8' : attrThu;
                            const attrBuoi = String(firstActionCell.getAttribute('buoi') || '').trim();
                            if (attrBuoi) currentBuoi = normalizeBuoi(attrBuoi);
                        }

                        const dateNode = tr.querySelector('div[class^="thu"]');
                        if (dateNode) {
                            const dateText = textOf(dateNode);
                            if (dateText) currentDate = dateText;
                        }

                        const buoiCell = Array.from(tr.querySelectorAll('td[rowspan]')).find(
                            (td) => /sáng|chiều/i.test(textOf(td))
                        );
                        if (buoiCell) {
                            const buoiText = textOf(buoiCell);
                            if (buoiText) currentBuoi = buoiText;
                        }

                        const addLink = tr.querySelector(addSelector);
                        const currentAddBtnIndex = addLink ? addBtnIndex : null;
                        if (addLink) addBtnIndex += 1;

                        const redSpans = Array.from(tr.querySelectorAll('span')).filter((el) => {
                            const color = String(window.getComputedStyle(el).color || '').toLowerCase();
                            return color.includes('255, 0, 0') || color.includes('red');
                        });
                        if (!redSpans.length) continue;

                        const actionCell = firstActionCell || tr.querySelector('td[thu][tiet], td[tiet]');
                        const tietAttr = actionCell ? String(actionCell.getAttribute('tiet') || '').trim() : '';
                        const thuAttrRaw = actionCell ? String(actionCell.getAttribute('thu') || '').trim() : '';
                        const thuAttr = thuAttrRaw === 'CN' ? '8' : thuAttrRaw;
                        const buoiAttr = actionCell ? String(actionCell.getAttribute('buoi') || '').trim() : '';
                        const monHocId = actionCell ? String(actionCell.getAttribute('mon_hoc_id') || '').trim() : '';
                        const phanMonId = actionCell ? String(actionCell.getAttribute('phan_mon_id') || '').trim() : '';
                        const tietPpctAttr = actionCell ? String(actionCell.getAttribute('tiet_ppct') || '').trim() : '';
                        const monHocTextAttr = actionCell
                            ? String(
                                actionCell.getAttribute('ten_mon_hoc') ||
                                actionCell.getAttribute('mon_hoc') ||
                                actionCell.getAttribute('mon_hoc_text') ||
                                ''
                            ).trim()
                            : '';
                        const phanMonTextAttr = actionCell
                            ? String(
                                actionCell.getAttribute('ten_phan_mon') ||
                                actionCell.getAttribute('phan_mon') ||
                                actionCell.getAttribute('phan_mon_text') ||
                                ''
                            ).trim()
                            : '';

                        const cells = Array.from(tr.querySelectorAll('td'));
                        const texts = cells.map((td) => textOf(td)).filter(Boolean);
                        const tietText = texts.find((value) => /^\\d+$/.test(value)) || tietAttr;
                        const redTexts = redSpans.map((span) => textOf(span)).filter(Boolean);
                        const monHocHint = monHocTextAttr || redTexts[0] || '';
                        const ppctHint = tietPpctAttr || redTexts.find((value) => /^\\d+$/.test(value)) || '';
                        const noiDungHint = redTexts.length >= 2 ? redTexts[redTexts.length - 1] : '';

                        items.push({
                            rowIdx: rowIdx,
                            add_btn_index: currentAddBtnIndex,
                            thu: thuAttr || currentThu,
                            buoi: normalizeBuoi(buoiAttr || currentBuoi),
                            tiet: tietAttr || tietText,
                            ngay: currentDate,
                            mon_hoc_id: monHocId,
                            phan_mon_id: phanMonId,
                            ppct_hint: ppctHint,
                            mon_hoc_hint: monHocHint,
                            mon_hoc_text_hint: monHocTextAttr || monHocHint,
                            phan_mon_text_hint: phanMonTextAttr,
                            noi_dung_hint: noiDungHint,
                            red_texts: redTexts,
                            row_texts: texts,
                            has_add_btn: !!addLink,
                        });
                    }

                    return {ok: true, rows: items};
                }'''
            )
            if result.get("ok"):
                rows = result.get("rows", [])
                logger.info(f"KHDH rows: {len(rows)} suggested rows")
                return True, rows
            return False, result.get("error", "Không đọc được các row KHDH")
        except Exception as e:
            return False, f"Lỗi đọc row KHDH: {type(e).__name__}: {str(e)[:120]}"

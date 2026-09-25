"""Chờ form sẵn sàng và bấm nút ➕ mở form."""

import time

from ..compat import PlaywrightTimeout
from .config import logger


class FormWaitMixin:
    """Chờ form sẵn sàng và bấm nút ➕ mở form."""

    def wait_for_slot_data_fetch(self, lop_text, tuan_num, thu, buoi, tiet,
                                 timeout_s=5.0, poll_interval=0.35):
        """Xác minh slot đã commit bằng cách refetch HTML sổ đầu bài từ service."""
        deadline = time.time() + max(timeout_s, 0.5)
        last_msg = "Không tìm thấy row mục tiêu trong HTML fetch"
        last_row = None
        target_thu = str(thu).strip()
        target_buoi = str(buoi).strip()
        target_tiet = str(tiet).strip()
        attempt = 0
        sleep_steps = [0.18, 0.25, 0.35, 0.45, 0.6]

        while time.time() < deadline:
            fetch_timeout = max(min(poll_interval * 4, 4.5), 2.5)
            ok, payload = self.fetch_sodaubai_rows(
                lop_text,
                tuan_num,
                timeout_s=fetch_timeout,
            )
            if not ok:
                last_msg = str(payload)
                sleep_s = min(
                    sleep_steps[min(attempt, len(sleep_steps) - 1)],
                    max(deadline - time.time(), 0),
                )
                attempt += 1
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

            server_week = payload.get("week")
            if server_week is not None and int(server_week) != int(tuan_num):
                last_msg = (
                    f"Service trả về tuần {server_week}, không khớp tuần yêu cầu {tuan_num}"
                )
                sleep_s = min(
                    sleep_steps[min(attempt, len(sleep_steps) - 1)],
                    max(deadline - time.time(), 0),
                )
                attempt += 1
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

            for row in payload.get("rows", []):
                if self._slot_row_matches(row, target_thu, target_buoi, target_tiet):
                    last_row = row
                    if row.get("has_data", False):
                        mon_hoc = str(row.get("mon_hoc", "")).strip()
                        return True, (
                            f"HTML fetch đã có dữ liệu ({mon_hoc or 'không rõ môn'})"
                        ), row
                    last_msg = "HTML fetch đã thấy row nhưng slot vẫn chưa có dữ liệu"
                    break

            sleep_s = min(
                sleep_steps[min(attempt, len(sleep_steps) - 1)],
                max(deadline - time.time(), 0),
            )
            attempt += 1
            if sleep_s > 0:
                time.sleep(sleep_s)

        return False, last_msg, last_row

    def wait_for_lesson_form(self, timeout_s=4.0, poll_interval=0.15):
        """Chờ popup chi tiết tiết học mở thật sự trước khi fill form."""
        deadline = time.time() + max(timeout_s, 0.5)
        last_msg = "Popup chi tiết tiết học chưa xuất hiện"

        while time.time() < deadline:
            try:
                result = self.page.evaluate('''() => {
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, msg: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = (w.title || '').toLowerCase();
                            const isLessonPopup = (
                                title.indexOf('chi ti') >= 0 ||
                                title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                                title.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                                title.indexOf('so dau bai') >= 0
                            );
                            if (!isLessonPopup) continue;
                            const form = w.down ? w.down('form') : null;
                            if (!form || !form.getForm) continue;
                            return {
                                ok: true,
                                title: w.title || '',
                                formId: form.id || '',
                            };
                        } catch (e) {}
                    }
                    return {ok: false, msg: 'popup_not_ready'};
                }''')
            except Exception as e:
                last_msg = f"Lỗi đọc popup: {type(e).__name__}: {str(e)[:80]}"
                time.sleep(poll_interval)
                continue

            if result.get("ok"):
                return True, result.get("title", "Popup chi tiết tiết học đã mở")

            last_msg = result.get("msg", last_msg)
            time.sleep(poll_interval)

        return False, last_msg

    def wait_for_lesson_form_closed(self, timeout_s=2.5, poll_interval=0.08):
        """Chờ popup chi tiết tiết học đóng hẳn sau save hoặc cleanup."""
        deadline = time.time() + max(timeout_s, 0.3)
        last_state = {"lessonOpen": True, "titles": []}

        while time.time() < deadline:
            try:
                state = self.page.evaluate('''() => {
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {lessonOpen: false, titles: []};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    const titles = [];
                    let lessonOpen = false;
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = String(w.title || '');
                            titles.push(title);
                            const tLow = title.toLowerCase();
                            if (
                                tLow.indexOf('chi ti') >= 0 ||
                                tLow.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                                tLow.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                                tLow.indexOf('so dau bai') >= 0
                            ) {
                                lessonOpen = true;
                            }
                        } catch (e) {}
                    }
                    return {lessonOpen: lessonOpen, titles: titles};
                }''')
            except Exception as e:
                last_state = {"lessonOpen": True, "titles": [], "error": str(e)}
                time.sleep(poll_interval)
                continue

            last_state = state or last_state
            if not last_state.get("lessonOpen", False):
                return True, "Popup đã đóng"
            time.sleep(poll_interval)

        return False, f"Popup vẫn còn mở: {last_state.get('titles', [])}"

    def wait_for_form_ready_to_save(self, timeout_s=1.2, poll_interval=0.08):
        """Chờ form ổn định và valid trước khi bấm Lưu."""
        deadline = time.time() + max(timeout_s, 0.3)
        last_msg = "Form chưa ổn định để lưu"

        while time.time() < deadline:
            try:
                state = self.page.evaluate('''() => {
                    if (typeof Ext === 'undefined' || !Ext.ComponentQuery) {
                        return {ok: false, msg: 'ExtJS not available'};
                    }
                    const wins = Ext.ComponentQuery.query('window');
                    let formPanel = null;
                    for (const w of wins) {
                        try {
                            if (!(w.isVisible && w.isVisible())) continue;
                            const title = (w.title || '').toLowerCase();
                            if (
                                title.indexOf('chi ti') >= 0 ||
                                title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                                title.indexOf('s\\u1ED5 \\u0111\\u1EA7u b\\u00E0i') >= 0 ||
                                title.indexOf('so dau bai') >= 0
                            ) {
                                const f = w.down ? w.down('form') : null;
                                if (f && f.getForm) {
                                    formPanel = f;
                                    break;
                                }
                            }
                        } catch (e) {}
                    }
                    if (!formPanel || !formPanel.getForm) {
                        return {ok: false, msg: 'popup_form_missing'};
                    }

                    let loadMaskVisible = false;
                    try {
                        const masks = document.querySelectorAll('.x-mask-msg, .x-mask-loading, .x-mask');
                        for (const mask of masks) {
                            if (mask.offsetParent !== null) {
                                loadMaskVisible = true;
                                break;
                            }
                        }
                    } catch (e2) {}

                    let comboLoading = false;
                    try {
                        const comboFields = formPanel.query('combobox');
                        for (const field of comboFields) {
                            const store = field.getStore ? field.getStore() : field.store;
                            if (store && store.isLoading && store.isLoading()) {
                                comboLoading = true;
                                break;
                            }
                        }
                    } catch (e3) {}

                    const form = formPanel.getForm();
                    let valid = false;
                    try { valid = !!form.isValid(); } catch (e4) {}

                    if (loadMaskVisible) {
                        return {ok: false, msg: 'loadmask_visible'};
                    }
                    if (comboLoading) {
                        return {ok: false, msg: 'combo_store_loading'};
                    }
                    if (!valid) {
                        return {ok: false, msg: 'form_invalid'};
                    }
                    return {ok: true, msg: 'ready'};
                }''')
            except Exception as e:
                last_msg = f"{type(e).__name__}: {str(e)[:80]}"
                time.sleep(poll_interval)
                continue

            if state.get("ok"):
                return True, state.get("msg", "ready")
            last_msg = state.get("msg", last_msg)
            time.sleep(poll_interval)

        return False, last_msg

    # -----------------------------------------------------------------
    # 3.4: CLICK NÚT ➕ (Mở form nhập liệu)
    # -----------------------------------------------------------------

    def click_add_button(self, row_index, row_dom_index=None):
        """Click đúng nút ➕ của row mục tiêu trên bảng.

        VnEdu dùng <a class="add add_tiet_so_dau_bai" onclick="themChiTietSoDauBai(this)">.
        Cách nhanh: lấy tất cả a.add_tiet_so_dau_bai → click theo index.

        Args:
            row_index: add_btn_index từ read_table() (0-based)
            row_dom_index: rowIdx từ read_table() nếu muốn click trực tiếp theo DOM row

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''(args) => {
                const targetIdx = args.targetIdx;
                const targetRowIdx = args.targetRowIdx;
                const addSelector = 'a.add_tiet_so_dau_bai, a[onclick*="themChiTietSoDauBai"]';

                function extractTietFromRow(tr) {
                    let tiet = '?';
                    if (!tr) return tiet;
                    const tds = tr.querySelectorAll('td');
                    for (const td of tds) {
                        const t = td.innerText.trim();
                        if (/^\\d+$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 5) {
                            tiet = t;
                            break;
                        }
                    }
                    return tiet;
                }

                // === Ưu tiên: click theo DOM row index đã parse ===
                let mainTable = document.querySelector('table.table');
                if (!mainTable) {
                    const tables = document.querySelectorAll('table');
                    let maxRowspan = 0;
                    for (const t of tables) {
                        const rs = t.querySelectorAll('td[rowspan]');
                        if (rs.length > maxRowspan) {
                            maxRowspan = rs.length;
                            mainTable = t;
                        }
                    }
                }

                if (mainTable && targetRowIdx !== null && targetRowIdx !== undefined && targetRowIdx >= 0) {
                    const allRows = mainTable.querySelectorAll('tr');
                    if (targetRowIdx >= 0 && targetRowIdx < allRows.length) {
                        const targetRow = allRows[targetRowIdx];
                        const rowBtn = targetRow.querySelector(addSelector);
                        if (rowBtn) {
                            const tiet = extractTietFromRow(targetRow);
                            rowBtn.click();
                            return {
                                ok: true,
                                tiet: tiet,
                                method: 'row_dom_index',
                                btnIndex: targetIdx,
                                rowIdx: targetRowIdx
                            };
                        }
                    }
                }

                // === Chiến lược chính: Dùng selector a.add_tiet_so_dau_bai ===
                const addLinks = document.querySelectorAll(addSelector);
                if (addLinks.length > 0) {
                    if (targetIdx < 0 || targetIdx >= addLinks.length) {
                        return {
                            ok: false,
                            error: 'Row index ' + targetIdx + ' ngoài phạm vi (total add buttons: ' + addLinks.length + ')'
                        };
                    }
                    const btn = addLinks[targetIdx];
                    const tr = btn.closest('tr');
                    const tiet = extractTietFromRow(tr);
                    btn.click();
                    return {
                        ok: true,
                        tiet: tiet,
                        method: 'add_btn_index',
                        btnIndex: targetIdx,
                        totalBtns: addLinks.length
                    };
                }

                // === Fallback: tìm bảng chính → duyệt rows ===
                if (!mainTable) return {ok: false, error: 'Không tìm thấy bảng'};

                const allBtns = mainTable.querySelectorAll(addSelector);
                if (targetIdx < 0 || targetIdx >= allBtns.length) {
                    return {
                        ok: false,
                        error: 'Row index ' + targetIdx + ' ngoài phạm vi (total: ' + allBtns.length + ')'
                    };
                }
                allBtns[targetIdx].click();
                return {ok: true, tiet: '?', btnIndex: targetIdx, totalBtns: allBtns.length};
            }''', {"targetIdx": row_index, "targetRowIdx": row_dom_index})

            if result.get("ok"):
                tiet = result.get("tiet", "?")
                total = result.get("totalBtns", "?")
                method = result.get("method", "?")
                logger.info(
                    f"Clicked add button #{row_index} (rowIdx={row_dom_index}, "
                    f"tiết {tiet}, total={total}, method={method})"
                )

                return True, f"Đã click tiết {tiet}"
            else:
                return False, result.get("error", "Unknown")

        except PlaywrightTimeout:
            return False, "Timeout click nút ➕"
        except Exception as e:
            return False, f"Lỗi click nút: {type(e).__name__}: {str(e)[:80]}"

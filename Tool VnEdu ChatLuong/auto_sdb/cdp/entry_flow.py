"""Luồng nhập hoàn chỉnh: ➕ → điền → lưu → đóng."""

import re
import time

from .config import logger


class EntryFlowMixin:
    """Luồng nhập hoàn chỉnh: ➕ → điền → lưu → đóng."""

    # -----------------------------------------------------------------
    # 3.6: COMPLETE ENTRY FLOW (Click ➕ → Fill → Save)
    # -----------------------------------------------------------------

    def close_form(self):
        """Đóng ExtJS form popup (click nút Đóng / X) — dùng cho error recovery.

        Đóng BẤT KỲ ExtJS window popup visible nào (chi tiết tiết học,
        ý kiến GVCN, thông báo, v.v.).

        Returns:
            (success: bool, message: str)
        """
        if not self.is_connected:
            return False, "Chưa kết nối CDP"

        try:
            result = self.page.evaluate('''() => {
                if (typeof Ext === 'undefined') return {ok: false, error: 'No ExtJS'};

                var closed = [];
                var skipped = [];
                var wins = Ext.ComponentQuery.query('window');
                for (var i = 0; i < wins.length; i++) {
                    var w = wins[i];
                    if (!w.isVisible || !w.isVisible()) continue;

                    // Safety filter: chỉ đóng popup/dialog, KHÔNG đóng content panels
                    var title = (w.title || '').toLowerCase();
                    var isPopup = false;
                    // Known popup patterns
                    if (title.indexOf('chi ti') >= 0 ||
                        title.indexOf('ti\\u1EBFt h\\u1ECDc') >= 0 ||
                        title.indexOf('\\u00fd ki\\u1EBFn') >= 0 ||
                        title.indexOf('y kien') >= 0 ||
                        title.indexOf('gvcn') >= 0 ||
                        title.indexOf('th\\u00f4ng b\\u00e1o') >= 0 ||
                        title.indexOf('thong bao') >= 0 ||
                        title.indexOf('x\\u00e1c nh\\u1EADn') >= 0 ||
                        title.indexOf('notification') >= 0 ||
                        title.indexOf('confirm') >= 0 ||
                        title.indexOf('th\\u00eam') >= 0 ||
                        title.indexOf('s\\u1EEDa') >= 0) {
                        isPopup = true;
                    }
                    // Has a form inside → likely a data entry popup
                    if (!isPopup && w.down && w.down('form')) isPopup = true;
                    // Is a messagebox
                    if (!isPopup && (w.xtype === 'messagebox' ||
                        w.$className === 'Ext.window.MessageBox')) isPopup = true;
                    // Is explicitly modal
                    if (!isPopup && w.modal) isPopup = true;

                    if (!isPopup) {
                        skipped.push(w.title || w.id);
                        continue;
                    }

                    // Thử click nút Đóng/Close
                    var btns = w.query('button');
                    var found = false;
                    for (var j = 0; j < btns.length; j++) {
                        var btn = btns[j];
                        var text = (btn.text || '').trim().toLowerCase();
                        if (text === '\\u0111\\u00f3ng' || text === 'close' ||
                            text === 'dong' || text === 'cancel' ||
                            text === 'h\\u1ee7y' || text === 'ok') {
                            if (btn.handler) {
                                btn.handler.call(btn.scope || btn, btn);
                            } else if (btn.el && btn.el.dom) {
                                btn.el.dom.click();
                            }
                            closed.push(w.title || w.id);
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        // Fallback: w.close() trực tiếp
                        try {
                            w.close();
                            closed.push((w.title || w.id) + ' (w.close)');
                        } catch(e) {}
                    }
                }
                return {ok: true, closed: closed, skipped: skipped,
                        method: closed.length > 0 ? 'closed' : 'no_popup'};
            }''')

            closed = result.get("closed", [])
            skipped = result.get("skipped", [])
            if closed:
                logger.info(f"Close form: closed {len(closed)} popup(s): {closed}")
            if skipped:
                logger.debug(f"Close form: skipped {len(skipped)} window(s): {skipped}")
            return True, result.get("method", "ok")

        except Exception as e:
            return False, f"Lỗi đóng form: {type(e).__name__}: {str(e)[:80]}"

    def type_one_entry(self, row_index, ppct, hs_nghi, nhan_xet, diem,
                       phan_mon_index=None, xep_loai=None, noi_dung=None,
                       pre_click_delay=0.3, row_dom_index=None):
        """Nhập liệu hoàn chỉnh cho 1 tiết: click ➕ → fill form → save.

        Args:
            row_index: add_btn_index của row mục tiêu (từ read_table)
            ppct: str — tiết PPCT
            hs_nghi: str — số HS nghỉ
            nhan_xet: str — nhận xét
            diem: str — điểm tiết học
            phan_mon_index: str|int|None — value Phân môn (None = giữ nguyên)
            xep_loai: str|None — value Xếp loại (None = giữ nguyên)
            noi_dung: str|None — tên bài / nội dung (None = "---")
            pre_click_delay: float — delay trước khi click (s)
            row_dom_index: int|None — rowIdx DOM từ read_table(), ưu tiên click
                đúng dấu ➕ nằm trên hàng đã match

        Returns:
            (success: bool, message: str)
        """
        if self.should_stop:
            return False, "Đã yêu cầu dừng"

        row_meta = None
        current_context = {"tuan": "", "lop": ""}
        ok_rows, rows_data = self.read_table()
        if ok_rows:
            for row in rows_data:
                if row_dom_index is not None and row.get("rowIdx") == row_dom_index:
                    row_meta = row
                    break
                if row.get("add_btn_index") == row_index:
                    row_meta = row
                    break
        ok_ctx, ctx_data = self.get_current_selection()
        if ok_ctx and isinstance(ctx_data, dict):
            current_context = ctx_data

        # Bước 0: Delay trước click (nếu caller yêu cầu)
        if pre_click_delay and pre_click_delay > 0:
            time.sleep(pre_click_delay)

        # Bước 1: Click nút ➕
        ok, msg = self.click_add_button(row_index, row_dom_index=row_dom_index)
        if not ok:
            return False, f"Click ➕ thất bại: {msg}"

        if self.should_stop:
            self.close_form()
            return False, "Đã yêu cầu dừng"

        # Bước 2: Chờ form sẵn sàng
        ok, msg = self.wait_for_lesson_form(timeout_s=4.0, poll_interval=0.12)
        if not ok:
            try:
                self.close_form()
            except Exception:
                pass
            return False, f"Form chưa mở sẵn sàng: {msg}"

        # Bước 3: Nhập liệu
        ok, msg = self.fill_form(ppct, hs_nghi, nhan_xet, diem,
                                 phan_mon_index, xep_loai, noi_dung)
        if not ok:
            # Đóng form để recovery
            self.close_form()
            return False, f"Nhập form thất bại: {msg}"

        if self.should_stop:
            self.close_form()
            return False, "Đã yêu cầu dừng"

        # Bước 4: Chờ form ổn định rồi lưu
        self.wait_for_form_ready_to_save(timeout_s=0.8, poll_interval=0.06)
        api_buoi = ""
        if row_meta is not None:
            api_buoi = str(row_meta.get("buoi", "")).strip()
        ok, msg, save_meta = self.save_form_auto(buoi_hoc=api_buoi)
        if not ok:
            if row_meta is not None:
                ok_verify = False
                msg_verify = ""
                _row = None
                match_tuan = re.search(r"\d+", str(current_context.get("tuan", "")))
                tuan_num = int(match_tuan.group()) if match_tuan else None
                lop_text = str(current_context.get("lop", "")).strip()
                if tuan_num and lop_text:
                    ok_verify, msg_verify, _row = self.wait_for_slot_data_fetch(
                        lop_text,
                        tuan_num,
                        row_meta.get("thu", ""),
                        row_meta.get("buoi", ""),
                        row_meta.get("tiet", ""),
                        timeout_s=4.5,
                        poll_interval=0.3,
                    )
                if not ok_verify:
                    ok_verify, msg_verify, _row = self.wait_for_slot_data(
                        row_meta.get("thu", ""),
                        row_meta.get("buoi", ""),
                        row_meta.get("tiet", ""),
                        timeout_s=3.0,
                        poll_interval=0.25,
                    )
                if ok_verify:
                    logger.warning(
                        "Save reported failure nhưng bảng đã cập nhật: "
                        f"{msg} | {msg_verify}"
                    )
                    try:
                        self.close_form()
                        self.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
                    except Exception:
                        pass
                    return True, f"Lưu xác minh từ bảng: {msg_verify}"
            try:
                self.close_form()
                self.wait_for_lesson_form_closed(timeout_s=1.0, poll_interval=0.06)
            except Exception:
                pass
            return False, f"Lưu thất bại: {msg}"

        logger.info(
            "Entry saved via %s%s",
            save_meta.get("method", "?"),
            " (fallback)" if save_meta.get("fallback_used") else "",
        )
        return True, f"Tiết {ppct}: OK"

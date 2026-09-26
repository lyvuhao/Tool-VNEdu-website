"""Điền các ô của một tuần KHDH lên DOM — gọi từ `_execute_week` (week_execution.py).

Mô phỏng đúng thứ tự giáo viên nhập trên web (web chỉ render lựa chọn kế tiếp sau khi chọn bước trước):

    Phase A   set Lớp cho từng ô           -> web render danh sách Môn
    Phase B   set Môn học                  -> web render Phân môn
    Phase B2  set Phân môn (chờ option; không có thì append với tên thật)
    Phase C   lấy tên bài qua API getByTiet (hoặc catalog Word HĐTN)
    Phase D   set PPCT + tên bài + ghi chú (+ fallback dấu cách nếu bật)
    Phase D2  set lại PPCT + tên bài ngay trước Lưu; chế độ nhanh thì đọc lại DOM, lệch -> không lưu

Ô lỗi ở phase trước (`run.failed_ops`) được bỏ qua ở các phase sau để tránh trạng thái nửa vời
(lớp chưa set mà môn đã set -> lỗi khi lưu).
"""

from __future__ import annotations

import time
from datetime import datetime

from ...log import logger
from ..client.js import _JS_SET_BLOCK_AUTOFILL
from ..fallback_log import FallbackEntry
from .js import (
    _JS_CLOSE_BENIGN_DIALOG,
    _JS_EXT_SET_PHAN_MON_VALUE,
    _JS_FETCH_TEN_BAI,
    _JS_GET_KHOI_FOR_LOP,
    _JS_SET_MON,
    _JS_SET_PHAN_MON,
    _JS_SET_PPCT_TEN_BAI,
    _JS_SET_SLOT_DROPDOWNS,
    _JS_WAIT_PHAN_MON_OPTION,
)


class WeekFillMixin:
    """Điền các ô của một tuần KHDH lên DOM (Phase A → D2)."""

    def _fill_week_ops(self, run) -> bool:
        """Chạy Phase A → D2 cho `run.active_ops`.

        Returns:
            False nếu chế độ nhanh phát hiện DOM vẫn lệch trước khi Lưu (tuần đã kết thúc, không lưu).
        """
        wp = run.wp
        self._emit("fill_ops", tuan=wp.tuan,
                   message=f"Apply {len(run.active_ops)} ô")

        # v3.2: BẬT XHR autofill blocker NGAY trước khi set DOM —
        # web bind handler change trên cboMonHoc/cboPhanMon sẽ async
        # gọi getByTiet/getSuggestTietPpct sau 0-1s. Blocker abort
        # các XHR này để web KHÔNG override PPCT tool đã set.
        # v3.3: Reset log mỗi tuần để đếm chuẩn cho UI real-time.
        try:
            self.client.reset_blocked_autofill_log()
            self.client.page.evaluate(_JS_SET_BLOCK_AUTOFILL, True)
        except Exception:
            pass

        self._fill_phase_set_lop(run)
        time.sleep(0.4)  # chờ web render mon options
        self._fill_phase_set_mon(run)
        time.sleep(0.5)  # chờ web render phan_mon options
        self._fill_phase_set_phan_mon(run)
        time.sleep(0.4)
        self._fill_phase_fetch_ten_bai(run)
        self._fill_phase_write_ppct(run)
        self._interruptible_sleep(self.wait_after_set_fields_s)

        # Đóng dialog "Chưa chọn lớp/môn báo giảng" nếu (do race) nó vẫn
        # bật lên — dialog này không ảnh hưởng save endpoint nhưng nếu
        # còn mở sẽ block click Lưu trên UI button.
        try:
            self.client.page.evaluate(_JS_CLOSE_BENIGN_DIALOG)
        except Exception:
            pass

        return self._fill_phase_reconfirm_before_save(run)

    # ------------------------------------------------------------------
    # Phase A / B / B2: Lớp -> Môn -> Phân môn
    # ------------------------------------------------------------------

    def _fill_phase_set_lop(self, run):
        """Phase A: set lop trước (web cần để render mon)."""
        for op in run.active_ops:
            if self.stop_event.is_set():
                break
            try:
                self.client.page.evaluate(
                    _JS_SET_SLOT_DROPDOWNS,
                    {"rk": op.row_key, "lop_id": op.lop_id},
                )
            except Exception as e:
                run.failed_ops.add(op.row_key)
                self._emit("error", tuan=run.wp.tuan,
                           message=f"set lop {op.row_key}: {e}")

    def _fill_phase_set_mon(self, run):
        """Phase B: set MÔN HỌC trước (web cần để render Phân môn)."""
        for op in run.active_ops:
            if self.stop_event.is_set():
                break
            # Skip nếu Phase A đã fail cho op này
            if op.row_key in run.failed_ops:
                continue
            try:
                self.client.page.evaluate(
                    _JS_SET_MON,
                    {"rk": op.row_key,
                     "mon_id": op.mon_id,
                     "mon_text": op.mon_text or ""},
                )
            except Exception as e:
                run.failed_ops.add(op.row_key)
                self._emit("error", tuan=run.wp.tuan,
                           message=f"set môn ô {op.row_key}: {e}")

    def _fill_phase_set_phan_mon(self, run):
        """Phase B2: set PHÂN MÔN sau khi options đã render.

        Mỗi ô poll cboPhanMon đến khi thấy option khớp (max 1.5s), nếu vẫn không thấy thì append option
        với tên thật từ profile (KHÔNG bao giờ append với raw ID làm text).
        """
        pm_appended = 0
        for op in run.active_ops:
            if self.stop_event.is_set():
                break
            # Skip nếu Phase A/B đã fail cho op này
            if op.row_key in run.failed_ops:
                continue
            if not op.phan_mon_id or op.phan_mon_id == "0":
                continue
            if self._set_phan_mon_for_op(run, op):
                pm_appended += 1
        if pm_appended:
            self._emit("warning", tuan=run.wp.tuan,
                       message=f"Phải tự thêm {pm_appended} option phân môn "
                               f"(web render chậm) — đã dùng tên đầy đủ.")

    def _wait_phan_mon_option(self, op, timeout_s=1.5) -> bool:
        """Poll tới khi cboPhanMon của ô có option khớp phân môn cần chọn."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                chk = self.client.page.evaluate(
                    _JS_WAIT_PHAN_MON_OPTION,
                    {"rk": op.row_key, "pm_id": op.phan_mon_id},
                )
                if chk and chk.get("ok"):
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    def _set_phan_mon_for_op(self, run, op) -> bool:
        """Set Phân môn cho một ô. Trả về True nếu phải tự append option vào DOM."""
        option_present = self._wait_phan_mon_option(op)
        appended = False
        # Set value (nếu vẫn chưa thấy option thì append với tên thật)
        res = None
        try:
            res = self.client.page.evaluate(
                _JS_SET_PHAN_MON,
                {"rk": op.row_key,
                 "pm_id": op.phan_mon_id,
                 "pm_text": op.phan_mon_text or ""},
            )
            if res and res.get("appended"):
                appended = True
                # Sau khi DOM append, gọi Ext.getCmp().setValue() để
                # ExtJS data binding nhận được giá trị mới (X2 fix).
                try:
                    self.client.page.evaluate(
                        _JS_EXT_SET_PHAN_MON_VALUE,
                        {"rk": op.row_key, "pm_id": op.phan_mon_id},
                    )
                except Exception:
                    pass
        except Exception as e:
            run.failed_ops.add(op.row_key)
            self._emit("error", tuan=run.wp.tuan,
                       message=f"set phân môn ô {op.row_key}: {e}")
        if not option_present and not (res and res.get("ok")):
            self._emit("warning", tuan=run.wp.tuan,
                       message=f"Ô {op.row_key}: web chưa render option "
                               f"phân môn {op.phan_mon_text or op.phan_mon_id} "
                               f"sau 1.5s")
        return appended

    # ------------------------------------------------------------------
    # Phase C: tên bài
    # ------------------------------------------------------------------

    def _fill_phase_fetch_ten_bai(self, run):
        """Phase C: fetch tên bài qua API getByTiet.

        Đây là API web KHDH dùng nội bộ. Giáo viên gõ PPCT → debounce 1s → web gọi API này → fill
        txtTenBai. Mình gọi trực tiếp để bypass debounce + giảm rủi ro race condition.
        """
        ten_bai_fetched = 0
        ten_bai_empty = 0
        ten_bai_skipped = 0
        ten_bai_from_word = 0
        for op in run.active_ops:
            if self.stop_event.is_set():
                break
            # Skip ops đã fail Phase A/B/B2 — gọi API với lop_id rỗng
            # sẽ chỉ tốn round-trip vô ích (X1 fix).
            if op.row_key in run.failed_ops:
                ten_bai_skipped += 1
                continue
            if not op.ppct or op.ppct <= 0:
                ten_bai_skipped += 1
                continue
            # Đọc khoi từ option của lop
            try:
                khoi = self.client.page.evaluate(
                    _JS_GET_KHOI_FOR_LOP,
                    {"rk": op.row_key, "lop_id": op.lop_id},
                ) or ""
            except Exception:
                khoi = ""
            # Nếu profile/catalog đã có sẵn ten_bai thì dùng (catalog ưu tiên)
            if op.ten_bai:
                ten_bai_skipped += 1
                continue
            outcome = self._fetch_ten_bai_from_web(run, op, khoi)
            if outcome == "fetched":
                ten_bai_fetched += 1
            elif outcome == "empty":
                ten_bai_empty += 1
            if not op.ten_bai and self._fill_hdtn_title_from_word_catalog(op, run.wp.tuan):
                ten_bai_from_word += 1
        self._emit("fill_done", tuan=run.wp.tuan,
                   message=f"Tên bài: web cấp {ten_bai_fetched}, "
                           f"Word HĐTN cấp {ten_bai_from_word}, "
                           f"phân môn không có CSDL {ten_bai_empty}, "
                           f"đã có sẵn {ten_bai_skipped}")

    def _fetch_ten_bai_from_web(self, run, op, khoi):
        """Gọi API getByTiet cho một ô; có tên bài thì gán vào `op.ten_bai`.

        Returns:
            "fetched" (có tên bài), "empty" (web trả rỗng — phân môn không có CSDL) hoặc None (lỗi).
        """
        try:
            res = self.client.page.evaluate(
                _JS_FETCH_TEN_BAI,
                {
                    "rk": op.row_key,
                    "khoi": khoi,
                    "lop_id": op.lop_id,
                    "mon_id": op.mon_id,
                    "pm_id": op.phan_mon_id,
                    "ppct": op.ppct,
                    "token": self._ctx.my_token if self._ctx else "",
                    "user_id": self._ctx.my_user_id if self._ctx else "",
                    "nam_hoc": str(self._ctx.nam_hoc) if self._ctx else "",
                },
            )
            logger.debug(f"fetch_ten_bai rk={op.row_key} ppct={op.ppct} → {res}")
            if res and res.get("ok"):
                tb = (res.get("ten_bai") or "").strip()
                if tb:
                    op.ten_bai = tb
                    return "fetched"
                return "empty"
            err = res.get("err") if res else "no_response"
            self._emit("warning", tuan=run.wp.tuan,
                       message=f"fetch ô {op.row_key}: {err}")
        except Exception as e:
            self._emit("warning", tuan=run.wp.tuan,
                       message=f"fetch ten_bai ô {op.row_key}: {e}")
        return None

    # ------------------------------------------------------------------
    # Phase D: PPCT + tên bài + ghi chú
    # ------------------------------------------------------------------

    def _fill_phase_write_ppct(self, run):
        """Phase D: set PPCT + tên bài + ghi chú.

        Ô không có tên bài (phân môn không có CSDL PPCT cho số tiết này) VẪN set lop/mon/pm/ppct và để
        tên bài trống — giáo viên tự bổ sung sau. KHÔNG reset ô về '---' (sẽ mất lop/mon/pm đã set ở
        Phase A-B, gây lỗi "không điền lớp").

        Cờ `ten_bai_fallback` bật: ô PPCT > 0 mà tên bài vẫn trống được chèn một dấu cách " " để qua
        bước validate phía web. Không đụng ô đã có tên bài thật, không fallback cho ô đã fail
        Phase A/B/B2.
        """
        wp = run.wp
        ops_no_ten_bai = 0
        ops_fallback_week = 0
        for op in run.active_ops:
            if self.stop_event.is_set():
                break
            # Skip ops đã fail phases A/B/B2 — không set PPCT vì lop/mon
            # chưa được set, sẽ gây lỗi save sau đó.
            if op.row_key in run.failed_ops:
                continue

            # Quyết định giá trị ten_bai sẽ ghi xuống DOM
            applied_fallback = False
            ten_bai_to_write = op.ten_bai or ""
            if (
                self.ten_bai_fallback
                and not ten_bai_to_write
                and op.ppct
                and op.ppct > 0
            ):
                ten_bai_to_write = " "
                applied_fallback = True

            try:
                self.client.page.evaluate(
                    _JS_SET_PPCT_TEN_BAI,
                    {
                        "rk": op.row_key,
                        "ppct": op.ppct if op.ppct else "",
                        "ten_bai": ten_bai_to_write,
                        # v3 (backup/restore): honor op.ghi_chu + op.trang_thai
                        # nếu có. Default rỗng/"0" giữ nguyên hành vi cũ.
                        "ghi_chu": getattr(op, "ghi_chu", "") or "",
                        "trang_thai": getattr(op, "trang_thai", "0") or "0",
                    },
                )
                if applied_fallback:
                    ops_fallback_week += 1
                    self._record_ten_bai_fallback(run, op)
                elif op.ppct and op.ppct > 0 and not op.ten_bai:
                    ops_no_ten_bai += 1
            except Exception as e:
                self._emit("error", tuan=wp.tuan,
                           message=f"set ppct/ten_bai {op.row_key}: {e}")
        if ops_no_ten_bai:
            self._emit("warning", tuan=wp.tuan,
                       message=f"{ops_no_ten_bai} ô chưa có tên bài "
                               f"(phân môn chưa có CSDL trên web cho số "
                               f"PPCT này) — bạn cần tự bổ sung tên bài "
                               f"trên web sau khi tool chạy xong.")
        if ops_fallback_week:
            self._emit("warning", tuan=wp.tuan,
                       message=f"Đã dùng fallback dấu cách cho "
                               f"{ops_fallback_week} ô trong tuần này. "
                               "Hãy kiểm tra lại sau khi lưu xong.")

    def _record_ten_bai_fallback(self, run, op):
        """Đếm + ghi fallback log cho ô vừa chèn dấu cách vào Tên bài.

        Entry log để sau này tự cập nhật tên bài thật (vd admin bổ sung CSDL phân phối chương trình);
        khoá (tuan, row_key) để upsert. Entry nằm trong `_week_pending_log_entries` cho tới khi biết
        tuần có lưu được hay không (xem `_commit_week_fallback_log`).
        """
        wp = run.wp
        self._fallback_count += 1
        if self.fallback_log is not None:
            try:
                entry = FallbackEntry(
                    tuan=int(wp.tuan),
                    row_key=str(op.row_key or ""),
                    lop_id=str(op.lop_id or ""),
                    lop_text=str(op.lop_text or ""),
                    mon_id=str(op.mon_id or ""),
                    mon_text=str(op.mon_text or ""),
                    phan_mon_id=str(op.phan_mon_id or ""),
                    phan_mon_text=str(op.phan_mon_text or ""),
                    ppct=int(op.ppct or 0),
                    fallback_at=datetime.now().isoformat(timespec="seconds"),
                    session_id=self._session_id,
                )
                self.fallback_log.add(entry)
                self._week_pending_log_entries.append(
                    (int(wp.tuan), str(op.row_key or ""))
                )
            except Exception as log_e:
                self._emit(
                    "warning", tuan=wp.tuan,
                    message=f"Không ghi được fallback log: {log_e}",
                )
        self._emit(
            "warning", tuan=wp.tuan,
            message=(
                f"🛟 Ô {op.row_key} (PPCT {op.ppct}, "
                f"{op.lop_text or op.lop_id}/"
                f"{op.phan_mon_text or op.mon_text or op.mon_id}): "
                "web không có tên bài → đã chèn dấu cách fallback "
                "để qua bước validate."
            ),
        )

    # ------------------------------------------------------------------
    # Phase D2: xác nhận lại ngay trước Lưu
    # ------------------------------------------------------------------

    def _fill_phase_reconfirm_before_save(self, run) -> bool:
        """Phase D2: RE-CONFIRM PPCT + tên bài ngay TRƯỚC save.

        Web KHDH bind handler `change` trên cboMonHoc/cboPhanMon gọi `getSuggestTietPpct` +
        `getTenBaiHoc` async; response về muộn có thể OVERRIDE PPCT tool đã set ở Phase D → lưu PPCT
        sai → mất tiết. Set lại lần 2 ngay trước Lưu (cách <500ms) để web không kịp override.

        Chế độ nhanh an toàn: đọc lại DOM thay vì ngủ cố định; lệch thì chậm lại và set lại một lần,
        vẫn lệch thì KHÔNG lưu tuần này.

        Returns:
            False nếu hủy lưu (tuần đã kết thúc với lỗi).
        """
        wp = run.wp
        wr = run.wr
        active_ops = run.active_ops
        failed_ops = run.failed_ops
        self._write_ppct_fields_for_ops(active_ops, failed_ops)
        # Wait ngắn (300ms) để DOM stabilize trước save
        time.sleep(0.3)

        if not self.fast_safe_mode or self.stop_event.is_set():
            return True
        dom_ok, dom_diffs = self._verify_fill_ops_dom(
            active_ops, failed_ops,
        )
        if not dom_ok:
            detail = "; ".join(dom_diffs[:5])
            self._emit(
                "warning", tuan=wp.tuan,
                message=(
                    "Chế độ nhanh: DOM chưa khớp trước khi lưu "
                    f"({detail}). Fallback nhịp chậm cho tuần này."
                ),
            )
            self._interruptible_sleep(2.5)
            self._write_ppct_fields_for_ops(active_ops, failed_ops)
            time.sleep(0.3)
            dom_ok, dom_diffs = self._verify_fill_ops_dom(
                active_ops, failed_ops,
            )
        if dom_ok:
            return True

        detail = "\n".join(f"  • {d}" for d in dom_diffs[:12])
        wr.save_ok = False
        wr.save_msg = (
            "Chế độ nhanh hủy lưu vì DOM vẫn lệch trước "
            "khi nhấn Lưu."
        )
        self._emit(
            "error", tuan=wp.tuan,
            message=(
                f"{wr.save_msg}\n{detail}\n"
                "Tool chưa bấm Lưu tuần này để tránh sót tiết. "
                "Hãy chạy lại tuần này bằng chế độ thường."
            ),
        )
        self._disable_autofill_blocker_best_effort()
        self._finish_week(run, "ok=False errors=1")
        return False

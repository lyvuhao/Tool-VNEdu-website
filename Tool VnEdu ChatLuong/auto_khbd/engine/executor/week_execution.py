"""Thực thi một tuần KHDH."""

from __future__ import annotations

import time
from datetime import datetime

from ...log import logger
from ..client.js import _JS_SET_BLOCK_AUTOFILL
from ..fallback_log import FallbackEntry
from ..planner import WeekPlan
from .errors import classify_save_error_msg, ERROR_HINTS, format_save_errors_for_user
from .js import (
    _JS_CLOSE_BENIGN_DIALOG,
    _JS_FETCH_TEN_BAI,
    _JS_GET_KHOI_FOR_LOP,
    _JS_SET_MON,
    _JS_SET_PHAN_MON,
    _JS_SET_PPCT_TEN_BAI,
    _JS_SET_SLOT_DROPDOWNS,
    _JS_WAIT_PHAN_MON_OPTION,
)
from .models import WeekResult


class WeekExecutionMixin:
    """Thực thi một tuần KHDH."""

    def _execute_week(self, wp: WeekPlan, dry_run: bool) -> WeekResult:
        wr = WeekResult(tuan=wp.tuan)
        if wp.skip_reason:
            wr.skipped = True
            wr.skip_reason = wp.skip_reason
            self._emit("week_skip", tuan=wp.tuan, message=wp.skip_reason)
            return wr

        # Reset danh sách entry log pending cho tuần này — nếu save tuần
        # thất bại, mình sẽ rollback các entry đã ghi để tránh log bị "phình"
        # bởi các phiên không thật sự lưu được.
        self._week_pending_log_entries = []

        t0 = time.time()
        self._emit("week_start", tuan=wp.tuan,
                   message=f"strategy={wp.strategy.value} pre_action={wp.pre_action} ops={len(wp.fill_ops)}")

        # 1. Đổi tuần trên UI + xác minh đã đúng tuần (rất quan trọng:
        #    tránh ghi nhầm dữ liệu vào tuần khác). Polling tối đa 8s,
        #    nếu sau 8s vẫn không đúng tuần → fail luôn tuần đó.
        if not dry_run:
            verified_tuan = self._switch_and_verify_week(wp.tuan)
            if verified_tuan != wp.tuan:
                wr.save_ok = False
                wr.save_msg = (
                    f"Không chuyển được sang tuần {wp.tuan} (web vẫn ở "
                    f"tuần {verified_tuan} sau 3 lần thử). "
                    "Có thể bạn đã click sang tuần khác trên web cùng lúc, "
                    "hoặc combobox bị treo. Hãy reload tab VnEdu rồi chạy lại."
                )
                self._emit("error", tuan=wp.tuan, message=wr.save_msg)
                self._disable_autofill_blocker_best_effort()
                wr.duration_ms = int((time.time() - t0) * 1000)
                self._emit("week_done", tuan=wp.tuan,
                          message=f"ok=False errors=1 took={wr.duration_ms}ms")
                return wr

            # 1b. BẮT BUỘC bật mode "Sửa" (rdoEdit) TRƯỚC KHI set fields.
            # Nếu web đang ở rdoEdit2 (Gợi ý theo TKB), các ô sẽ hiển thị
            # data suggested từ TKB — khi set lop/mon/pm, web có thể override
            # hoặc reject vì mode gợi ý chỉ cho xem, không cho sửa tự do.
            # Phải bật rdoEdit + reload table để có form trống sạch.
            try:
                self.client.enable_edit_mode()
                time.sleep(0.3)
                # Reload table sau khi đổi mode để DOM render đúng mode mới.
                # (#3) Chờ DOM render thật thay vì sleep cứng 2s — mạng chậm
                # 2s có thể không đủ → scan-and-diff đọc DOM trống → sai.
                self.client.reload_table_and_wait(
                    floor_s=2.0, max_extra_s=6.0, stop_event=self.stop_event,
                )
            except Exception as e:
                self._emit("warning", tuan=wp.tuan,
                          message=f"enable_edit_mode: {e}")

            # 1c. v2: Scan-and-diff tuần này TRƯỚC KHI fill ops.
            # - Nếu mọi op đã có sẵn trên web → SKIP toàn tuần (idempotent).
            # - Nếu thiếu một phần → chỉ giữ ops cần fill (filter wp.fill_ops).
            # - Nếu phát hiện tiết bù (cboTrangThai = "Dạy bù"/"Chèn lịch"...)
            #   → emit info để UI biết PPCT đã shift, không gián đoạn flow.
            try:
                skip_full, ops_to_keep, extras, scan_progress = \
                    self._scan_and_diff_week(wp)
                # Cache scan_progress để UI có thể đọc PPCT next real-time
                if not hasattr(self, "_last_scan_progress"):
                    self._last_scan_progress = {}
                self._last_scan_progress[wp.tuan] = scan_progress

                if extras:
                    extras_summary = ", ".join(
                        f"({e['thu']}/{e['buoi']}/{e['tiet']}:PPCT={e['ppct']})"
                        for e in extras[:5]
                    )
                    # v2.2: Group extras theo (lop, mon, pm) để biết mỗi
                    # group có bao nhiêu tiết bù trong tuần này — shift PPCT
                    # cho các tuần KẾ TIẾP (không động tuần này và trước nó).
                    extras_by_group: dict[str, int] = {}
                    for e in extras:
                        gk = f"{e['lop_id']}|{e['mon_id']}|{e['phan_mon_id']}"
                        extras_by_group[gk] = extras_by_group.get(gk, 0) + 1

                    shifted = self._apply_inflight_ppct_shift(
                        current_tuan=wp.tuan,
                        extras_by_group=extras_by_group,
                    )

                    extra_msg = (
                        f"Phát hiện {len(extras)} tiết dạy bù/chèn lịch trên web "
                        f"tuần {wp.tuan}: {extras_summary}"
                        + ("…" if len(extras) > 5 else "")
                        + (f". Đã shift +PPCT cho {shifted} ô ở các tuần kế tiếp."
                           if shifted > 0 else "")
                    )
                    self._emit("extra_detected", tuan=wp.tuan,
                              message=extra_msg, extras=extras,
                              shifted=shifted)
                    # Bump counter trong report (nếu đang được tracked)
                    if self._report is not None:
                        self._report.extras_detected_count += len(extras)

                if skip_full:
                    wr.skipped = True
                    wr.skip_reason = (
                        f"Tuần {wp.tuan} đã đầy đủ và đúng trên web "
                        f"({len([o for o in wp.fill_ops if not o.skip])} ô khớp). "
                        "Bỏ qua tuần này."
                    )
                    wr.save_ok = True
                    self._emit("week_already_complete", tuan=wp.tuan,
                              message=wr.skip_reason)
                    if self._report is not None:
                        self._report.weeks_already_complete += 1
                    wr.duration_ms = int((time.time() - t0) * 1000)
                    self._emit("week_done", tuan=wp.tuan,
                              message=f"ok=True skipped=True took={wr.duration_ms}ms")
                    return wr

                if len(ops_to_keep) < sum(1 for o in wp.fill_ops if not o.skip):
                    # Partial supplement: web đã có 1 phần, chỉ fill ô thiếu
                    skipped_count = sum(1 for o in wp.fill_ops if not o.skip) - len(ops_to_keep)
                    self._emit("week_partial_supplement", tuan=wp.tuan,
                              message=f"Bổ sung {len(ops_to_keep)} ô thiếu "
                                      f"(đã có sẵn {skipped_count} ô khớp web).")
                    # Mark ops không cần fill bằng skip
                    keep_keys = {op.row_key for op in ops_to_keep}
                    for op in wp.fill_ops:
                        if op.row_key not in keep_keys:
                            op.skip = True
            except Exception as e:
                # Scan thất bại → fallback: fill như bình thường, không block
                self._emit("warning", tuan=wp.tuan,
                          message=f"scan_and_diff exception: {e} — fallback fill all")

        # 2. Pre-action (gọi API gen)
        # Stop check trước pre_action — tránh chạy 1 tuần thừa khi user dừng
        # ngay sau khi top-of-loop check.
        if (self.stop_event is not None and self.stop_event.is_set()
                and not dry_run and wp.pre_action):
            self._emit("stop", tuan=wp.tuan,
                      message="Bỏ qua pre_action vì stop_event")
            wr.duration_ms = int((time.time() - t0) * 1000)
            self._emit("week_done", tuan=wp.tuan,
                      message=f"ok=False errors=1 took={wr.duration_ms}ms")
            return wr
        if wp.pre_action == "gen_prev" and not dry_run:
            self._emit("pre_action", tuan=wp.tuan, message="genLichBaoGiangTuan từ tuần X-1")
            try:
                res = self.client.gen_from_prev_week(wp.tuan)
                wr.pre_action = "gen_prev"
                wr.pre_action_ok = bool(res.ok and res.data and res.data.get("success", True))
                wr.pre_action_msg = (res.data and res.data.get("msg")) or ""
                if not wr.pre_action_ok:
                    self._emit("error", tuan=wp.tuan, message=f"gen_prev failed: {wr.pre_action_msg}")
            except Exception as e:
                wr.pre_action_ok = False
                wr.pre_action_msg = f"{type(e).__name__}: {e}"
                self._emit("error", tuan=wp.tuan, message=f"gen_prev exception: {e}")
            self._interruptible_sleep(self.wait_after_pre_action_s)
            # Reload sau gen
            try:
                self.client.reload_table_and_wait(
                    floor_s=1.5, max_extra_s=6.0, stop_event=self.stop_event,
                )
            except Exception:
                pass

        elif wp.pre_action == "gen_tkb" and not dry_run:
            self._emit("pre_action", tuan=wp.tuan, message="genLichBaoGiangTuanTheoTKB")
            try:
                res = self.client.gen_from_tkb(wp.tuan)
                wr.pre_action = "gen_tkb"
                wr.pre_action_ok = bool(res.ok and res.data and res.data.get("success", True))
                wr.pre_action_msg = (res.data and res.data.get("msg")) or ""
            except Exception as e:
                wr.pre_action_ok = False
                wr.pre_action_msg = f"{type(e).__name__}: {e}"
            self._interruptible_sleep(self.wait_after_pre_action_s)
            try:
                self.client.reload_table_and_wait(
                    floor_s=1.5, max_extra_s=6.0, stop_event=self.stop_event,
                )
            except Exception:
                pass

        # 3. Apply fill ops — qua nhiều phase để mô phỏng đúng cách user nhập:
        #   Phase A: set lop cho mỗi ô → web tự render danh sách môn
        #   Phase B: set mon + phan_mon → web tự render thêm
        #   Phase C: với mỗi (lop, mon, pm, ppct) → gọi API getByTiet để
        #           lấy tên bài CHUẨN (web KHDH dùng cùng API này)
        #   Phase D: set PPCT + tên bài + ghi chú vào DOM
        active_ops = [op for op in wp.fill_ops if not op.skip]
        # Track per-op failure: nếu Phase A fail cho slot X → skip B/C/D cho X
        # để tránh state malformed (lop chưa set nhưng mon đã set → lỗi save).
        # Set chứa row_key của các op đã fail ở phase trước đó.
        failed_ops: set[str] = set()
        if active_ops and not dry_run:
            self._emit("fill_ops", tuan=wp.tuan,
                       message=f"Apply {len(active_ops)} ô")

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

            # --- Phase A: set lop trước (web cần để render mon) ---
            for op in active_ops:
                if self.stop_event.is_set():
                    break
                try:
                    self.client.page.evaluate(
                        _JS_SET_SLOT_DROPDOWNS,
                        {"rk": op.row_key, "lop_id": op.lop_id},
                    )
                except Exception as e:
                    failed_ops.add(op.row_key)
                    self._emit("error", tuan=wp.tuan,
                               message=f"set lop {op.row_key}: {e}")
            time.sleep(0.4)  # chờ web render mon options

            # --- Phase B: set MÔN HỌC trước (web cần để render Phân môn) ---
            for op in active_ops:
                if self.stop_event.is_set():
                    break
                # Skip nếu Phase A đã fail cho op này
                if op.row_key in failed_ops:
                    continue
                try:
                    self.client.page.evaluate(
                        _JS_SET_MON,
                        {"rk": op.row_key,
                         "mon_id": op.mon_id,
                         "mon_text": op.mon_text or ""},
                    )
                except Exception as e:
                    failed_ops.add(op.row_key)
                    self._emit("error", tuan=wp.tuan,
                               message=f"set môn ô {op.row_key}: {e}")
            time.sleep(0.5)  # chờ web render phan_mon options

            # --- Phase B2: set PHÂN MÔN sau khi options đã render ---
            # Mỗi ô poll cboPhanMon đến khi thấy option khớp (max 1.5s),
            # nếu vẫn không thấy thì append option với tên thật từ profile
            # (KHÔNG bao giờ append với raw ID làm text).
            pm_appended = 0
            for op in active_ops:
                if self.stop_event.is_set():
                    break
                # Skip nếu Phase A/B đã fail cho op này
                if op.row_key in failed_ops:
                    continue
                if not op.phan_mon_id or op.phan_mon_id == "0":
                    continue
                # Poll option khớp
                deadline = time.monotonic() + 1.5
                option_present = False
                while time.monotonic() < deadline:
                    try:
                        chk = self.client.page.evaluate(
                            _JS_WAIT_PHAN_MON_OPTION,
                            {"rk": op.row_key, "pm_id": op.phan_mon_id},
                        )
                        if chk and chk.get("ok"):
                            option_present = True
                            break
                    except Exception:
                        pass
                    time.sleep(0.1)
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
                        pm_appended += 1
                        # Sau khi DOM append, gọi Ext.getCmp().setValue() để
                        # ExtJS data binding nhận được giá trị mới (X2 fix).
                        try:
                            self.client.page.evaluate(
                                """({rk, pm_id}) => {
                                    try {
                                        const cmp = Ext.getCmp('cboPhanMon_' + rk);
                                        if (cmp && typeof cmp.setValue === 'function') {
                                            cmp.setValue(pm_id);
                                            return true;
                                        }
                                    } catch(e) {}
                                    return false;
                                }""",
                                {"rk": op.row_key, "pm_id": op.phan_mon_id},
                            )
                        except Exception:
                            pass
                except Exception as e:
                    failed_ops.add(op.row_key)
                    self._emit("error", tuan=wp.tuan,
                               message=f"set phân môn ô {op.row_key}: {e}")
                if not option_present and not (res and res.get("ok")):
                    self._emit("warning", tuan=wp.tuan,
                              message=f"Ô {op.row_key}: web chưa render option "
                                      f"phân môn {op.phan_mon_text or op.phan_mon_id} "
                                      f"sau 1.5s")
            if pm_appended:
                self._emit("warning", tuan=wp.tuan,
                          message=f"Phải tự thêm {pm_appended} option phân môn "
                                  f"(web render chậm) — đã dùng tên đầy đủ.")
            time.sleep(0.4)

            # --- Phase C: fetch tên bài qua API getByTiet ---
            # Đây là API web KHDH dùng nội bộ. Giáo viên gõ PPCT → debounce 1s
            # → web gọi API này → fill txtTenBai. Mình gọi trực tiếp để
            # bypass debounce + giảm rủi ro race condition.
            ten_bai_fetched = 0
            ten_bai_empty = 0
            ten_bai_skipped = 0
            ten_bai_from_word = 0
            for op in active_ops:
                if self.stop_event.is_set():
                    break
                # Skip ops đã fail Phase A/B/B2 — gọi API với lop_id rỗng
                # sẽ chỉ tốn round-trip vô ích (X1 fix).
                if op.row_key in failed_ops:
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
                            ten_bai_fetched += 1
                        else:
                            ten_bai_empty += 1
                    else:
                        err = res.get("err") if res else "no_response"
                        self._emit("warning", tuan=wp.tuan,
                                  message=f"fetch ô {op.row_key}: {err}")
                except Exception as e:
                    self._emit("warning", tuan=wp.tuan,
                              message=f"fetch ten_bai ô {op.row_key}: {e}")
                if not op.ten_bai and self._fill_hdtn_title_from_word_catalog(op, wp.tuan):
                    ten_bai_from_word += 1
            self._emit("fill_done", tuan=wp.tuan,
                       message=f"Tên bài: web cấp {ten_bai_fetched}, "
                              f"Word HĐTN cấp {ten_bai_from_word}, "
                              f"phân môn không có CSDL {ten_bai_empty}, "
                              f"đã có sẵn {ten_bai_skipped}")

            # --- Phase D: set PPCT + tên bài + ghi chú ---
            # Quy tắc MỚI: nếu ô không có tên bài (vì phân môn không có CSDL
            # PPCT cho số tiết này), VẪN set lop/mon/pm/ppct + để ten_bai
            # trống. Giáo viên sẽ tự bổ sung tên bài sau trên web.
            # KHÔNG reset ô về '---' nữa — vì reset sẽ mất toàn bộ data
            # (lop/mon/pm) đã set ở Phase A-B, gây lỗi "không điền lớp".
            #
            # Nếu cờ ten_bai_fallback bật: với các ô PPCT hợp lệ nhưng
            # ten_bai vẫn trống (web không có CSDL hoặc PPCT vượt phân phối),
            # chèn 1 dấu cách " " vào ô Tên bài để qua bước validate phía
            # web. Logic siết: KHÔNG đụng nếu ten_bai đã có giá trị thật;
            # KHÔNG fallback cho ô đã fail Phase A/B/B2; chỉ fallback khi
            # PPCT > 0 (ô có ý nghĩa).
            ops_no_ten_bai = 0
            ops_fallback_week = 0
            for op in active_ops:
                if self.stop_event.is_set():
                    break
                # Skip ops đã fail phases A/B/B2 — không set PPCT vì lop/mon
                # chưa được set, sẽ gây lỗi save sau đó.
                if op.row_key in failed_ops:
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
                        self._fallback_count += 1
                        # Ghi entry vào fallback log để có thể tự cập nhật
                        # tên bài thật sau này (vd admin update CSDL phân
                        # phối chương trình). Luôn dùng tuple (tuan, row_key)
                        # làm khóa nhằm upsert nếu đã có entry cũ.
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
            self._interruptible_sleep(self.wait_after_set_fields_s)

            # Đóng dialog "Chưa chọn lớp/môn báo giảng" nếu (do race) nó vẫn
            # bật lên — dialog này không ảnh hưởng save endpoint nhưng nếu
            # còn mở sẽ block click Lưu trên UI button.
            try:
                self.client.page.evaluate(_JS_CLOSE_BENIGN_DIALOG)
            except Exception:
                pass

            # --- Phase D2: RE-CONFIRM PPCT + tên bài ngay TRƯỚC save ---
            # CRITICAL FIX: Web KHDH bind handler `change` trên cboMonHoc và
            # cboPhanMon gọi `getSuggestTietPpct(this)` + `getTenBaiHoc(this)`
            # async. Sau khi tool set ở Phase D rồi đợi 2.5s, response server
            # về có thể OVERRIDE PPCT đã set thành PPCT gợi ý (thường khác
            # với PPCT tool muốn lưu) → save lưu PPCT sai → mất tiết.
            #
            # Fix: re-set PPCT + tên bài lần 2 ngay trước Save. Giữa lần
            # set lần 2 và click Save chỉ cách <500ms → web không kịp gọi
            # async lại để override.
            if active_ops:
                self._write_ppct_fields_for_ops(active_ops, failed_ops)
                # Wait ngắn (300ms) để DOM stabilize trước save
                time.sleep(0.3)

                # Chế độ nhanh an toàn: thay vì ngủ 2.5s cố định, đọc lại
                # DOM ngay trước Save. Nếu chưa khớp 100%, fallback tuần đó
                # sang nhịp chậm rồi re-set một lần. Vẫn lệch thì KHÔNG lưu.
                if self.fast_safe_mode and not self.stop_event.is_set():
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
                    if not dom_ok:
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
                        wr.duration_ms = int((time.time() - t0) * 1000)
                        self._emit(
                            "week_done", tuan=wp.tuan,
                            message=(
                                f"ok=False errors=1 took={wr.duration_ms}ms"
                            ),
                        )
                        return wr

        # 4. Save — đảm bảo đang ở chế độ "Sửa" (rdoEdit), KHÔNG phải
        # "Sửa (Gợi ý theo TKB)" (rdoEdit2). Mode rdoEdit2 thường bị web
        # khoá save vì hiển thị slot suggested chưa thực sự được chọn.
        if (active_ops or wp.pre_action) and not dry_run:
            # Kiểm tra lần cuối: combobox vẫn đúng tuần? Nếu UI bị nhảy tuần
            # giữa chừng (do user click hoặc reload), KHÔNG được save.
            cur = self._read_current_week()
            if cur != wp.tuan:
                wr.save_ok = False
                wr.save_msg = (
                    f"Ngay trước khi nhấn Lưu, web đang ở tuần {cur} thay "
                    f"vì tuần {wp.tuan}. Có vẻ bạn vừa click sang tuần "
                    "khác trên Chrome trong lúc tool đang điền. Tool hủy "
                    "thao tác Lưu để KHÔNG ghi nhầm dữ liệu vào tuần khác. "
                    "Hãy giữ tab VnEdu yên trong lúc tool chạy, rồi chạy "
                    f"lại từ tuần {wp.tuan}."
                )
                self._emit("error", tuan=wp.tuan, message=wr.save_msg)
                wr.duration_ms = int((time.time() - t0) * 1000)
                self._emit("week_done", tuan=wp.tuan,
                          message=f"ok=False errors=1 took={wr.duration_ms}ms")
                return wr
            try:
                self.client.enable_edit_mode()
                time.sleep(0.3)
            except Exception as e:
                self._emit("warning", tuan=wp.tuan,
                          message=f"enable_edit_mode: {e}")
            # Stop check ngay TRƯỚC save — tránh ghi 1 tuần thừa khi user
            # đã bấm Dừng trong khi đang fill ops.
            if self.stop_event is not None and self.stop_event.is_set():
                wr.save_ok = False
                wr.save_msg = "Đã dừng trước khi lưu"
                self._emit("stop", tuan=wp.tuan,
                          message="Bỏ qua save vì stop_event")
                self._disable_autofill_blocker_best_effort()
                wr.duration_ms = int((time.time() - t0) * 1000)
                self._emit("week_done", tuan=wp.tuan,
                          message=f"ok=False errors=1 took={wr.duration_ms}ms")
                return wr
            self._emit("save", tuan=wp.tuan, message="Click Lưu")
            try:
                save_res = self.client.save_week_via_button(wait_s=self.wait_after_save_s)
                # Yêu cầu của user: hễ server báo fail HOẶC có field error
                # nào → save_ok = False (không tính là warning nữa).
                wr.save_ok = save_res.ok and save_res.success and not save_res.errors
                wr.save_msg = save_res.msg
                wr.save_errors = list(save_res.errors)
                if save_res.errors:
                    # Render TẤT CẢ ô lỗi với label tiếng Việt + context lớp/môn
                    detail = format_save_errors_for_user(
                        save_res.errors, wp.fill_ops, max_lines=20,
                    )
                    self._emit(
                        "error", tuan=wp.tuan,
                        message=(
                            f"Web báo {len(save_res.errors)} ô lỗi:\n"
                            f"{detail}"
                        ),
                    )
                if not save_res.success and save_res.msg:
                    # Server trả msg rõ — phân loại để có hint cụ thể.
                    err_kind = classify_save_error_msg(save_res.msg)
                    hint = ERROR_HINTS.get(err_kind, ERROR_HINTS["unknown"])
                    self._emit(
                        "error", tuan=wp.tuan,
                        message=(
                            f"Web từ chối lưu cả tuần.\n"
                            f"Server báo: \"{save_res.msg}\"\n{hint}"
                        ),
                    )
                    self._emit(
                        "warning", tuan=wp.tuan,
                        message=(
                            "Lưu ý: web KHDH lưu all-or-nothing — chỉ cần "
                            "1 ô lỗi là CẢ TUẦN không lưu được. Sửa các ô "
                            "vàng rồi chạy lại từ tuần này."
                        ),
                    )
            except Exception as e:
                wr.save_ok = False
                wr.save_msg = f"{type(e).__name__}: {e}"
                self._emit("error", tuan=wp.tuan, message=f"save exception: {e}")
            time.sleep(0.5)
            # v3.2: TẮT XHR autofill blocker sau khi save xong — để bước
            # verify-after-save fetch_week và scan tuần kế không bị block.
            # Save endpoint không khớp pattern blocker nên không bị ảnh
            # hưởng, nhưng tắt sớm cho an toàn.
            # v3.3: Emit số XHR đã chặn để UI hiện real-time.
            try:
                self._emit_blocker_stats(wp.tuan, label="after_save")
                self.client.page.evaluate(_JS_SET_BLOCK_AUTOFILL, False)
            except Exception:
                pass

            # (#1/#4/#6) VERIFY-AFTER-SAVE: đọc lại web kiểm chứng dữ liệu
            # thật khớp ý định. Chỉ chạy khi save báo OK — nếu save đã fail
            # thì không cần verify (đã biết lỗi). Mismatch ở đây bắt được:
            #   - web autofill ghi đè tên bài sau debounce (mất tiết)
            #   - autofill-blocker over/under-block gây ghi sai
            #   - server trả success=true nhưng không lưu trọn vẹn
            # Mismatch → coi như tuần FAIL → halt-on-error trigger.
            if (wr.save_ok and self.verify_after_save
                    and not self.stop_event.is_set()):
                self._emit("save", tuan=wp.tuan,
                          message="Đọc lại web để kiểm chứng…")
                verified, diffs = self._verify_week_after_save(
                    wp.tuan, active_ops,
                )
                if not verified:
                    wr.save_ok = False
                    detail = "\n".join(diffs[:12])
                    if len(diffs) > 12:
                        detail += f"\n  • … và {len(diffs) - 12} ô khác"
                    wr.save_msg = (
                        f"Web báo lưu thành công nhưng kiểm chứng lại thấy "
                        f"{len(diffs)} ô lệch."
                    )
                    self._emit(
                        "error", tuan=wp.tuan,
                        message=(
                            f"⚠ Tuần {wp.tuan}: web nói đã lưu nhưng dữ liệu "
                            f"thực tế KHÔNG khớp:\n{detail}\n\n"
                            "Nguyên nhân thường gặp: web tự điền lại tên bài "
                            "sau khi tool lưu, hoặc mạng chậm khiến lưu chưa "
                            "trọn. Hãy mở web tuần này kiểm tra rồi chạy lại."
                        ),
                    )
                else:
                    self._emit("save", tuan=wp.tuan,
                              message="✓ Đã kiểm chứng: web khớp đúng ý định.")

        # Persist hoặc rollback fallback log dựa vào kết quả save tuần.
        # - Save OK + có entry pending → save log xuống đĩa (commit).
        # - Save FAIL + có entry pending → bỏ entry khỏi log (rollback,
        #   tránh log "phình" bởi các phiên không thực sự lưu được).
        if self._week_pending_log_entries and self.fallback_log is not None and not dry_run:
            try:
                if wr.save_ok:
                    self.fallback_log.save()
                else:
                    for tuan_k, row_k in self._week_pending_log_entries:
                        self.fallback_log.remove(tuan_k, row_k)
                    # Sau rollback vẫn save xuống đĩa để file consistent
                    # (vd phiên trước đã có entry hợp lệ thì giữ nguyên).
                    self.fallback_log.save()
            except Exception as log_e:
                self._emit(
                    "warning", tuan=wp.tuan,
                    message=f"Không lưu được fallback log: {log_e}",
                )
        # Cleanup tự động các entry stale ở tuần này: nếu ô đã có Tên bài
        # thật (raw value khác " " và không rỗng) thì bỏ entry khỏi log.
        # An toàn: chỉ chạy khi save tuần thành công và log non-empty.
        if (
            wr.save_ok
            and self.fallback_log is not None
            and not dry_run
            and any(int(e.tuan) == int(wp.tuan)
                    for e in self.fallback_log.all_entries())
        ):
            try:
                self._cleanup_stale_log_entries_for_week(wp.tuan)
            except Exception as cleanup_e:
                self._emit(
                    "warning", tuan=wp.tuan,
                    message=f"Cleanup log: {cleanup_e}",
                )
        self._week_pending_log_entries = []

        wr.duration_ms = int((time.time() - t0) * 1000)
        self._emit("week_done", tuan=wp.tuan,
                   message=f"ok={wr.save_ok} errors={len(wr.save_errors)} took={wr.duration_ms}ms")
        return wr

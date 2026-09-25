"""Worker cập nhật lại tên bài đã fallback."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    classify_save_error_msg,
    ERROR_HINTS,
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
    format_save_errors_for_user,
)
from ...engine.executor.js import (
    _JS_FETCH_TEN_BAI,
    _JS_GET_CURRENT_WEEK,
    _JS_GET_KHOI_FOR_LOP,
    _JS_READ_FIELDS,
    _JS_SET_PPCT_TEN_BAI,
)
from ...engine.executor.models import ExecutorEvent
from ...engine.fallback_log import FallbackEntry, FallbackLog
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — RefreshFallbackWorker
# =====================================================================

class RefreshFallbackWorker(threading.Thread):
    """Đọc fallback log + cập nhật Tên bài thật cho các ô đã chèn dấu cách.

    Flow:
        1. Đọc `<excel_path>.fallback.json`.
        2. Connect CDP, vào màn KHDH.
        3. Cho mỗi tuần có entry:
            a. Switch tuần + verify combobox.
            b. Bật mode "Sửa" (rdoEdit) + reload table.
            c. Đọc raw value của các ô txtTenBai trong tuần.
            d. Phân loại từng entry:
                - raw != " "  → user đã sửa thủ công / RUN khác đã ghi đè
                  → xóa entry khỏi log (đã xử lý).
                - raw == " "  → gọi getByTiet để fetch tên bài hiện tại:
                    - Có tên bài → set DOM, mark cần save.
                    - Vẫn rỗng → giữ entry, log "still_no_data".
            e. Nếu có ô updated → click Lưu, verify save:
                - OK → xóa entry các ô updated khỏi log (commit).
                - Fail → giữ entry, halt.
        4. Emit summary qua queue.

    Halt-on-error giống PlanExecutor để tránh ghi lung tung khi web/mạng lỗi.

    Events emit qua queue (giống ExecutorWorker):
        ("status", text)
        ("event", ExecutorEvent)
        ("done", RefreshFallbackReport)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        excel_path: str | Path | None,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        wait_after_save_s: float = 10.0,
    ):
        super().__init__(daemon=True, name="KHDH-RefreshFallback")
        self.port = port
        self.excel_path = Path(excel_path) if excel_path else None
        self.q = event_queue
        self.stop_event = stop_event
        self.wait_after_save_s = wait_after_save_s

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi không lường trước: {e}\n\n{tb}"))

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep with stop_event polling (giống PlanExecutor).

        Returns:
            True nếu sleep hoàn tất bình thường, False nếu stop_event được set.
        """
        if seconds <= 0:
            return True
        ticks = max(1, int(seconds * 10))
        remaining = float(seconds)
        for _ in range(ticks):
            if self.stop_event.is_set():
                return False
            step = min(0.1, remaining)
            time.sleep(step)
            remaining -= step
            if remaining <= 0:
                break
        return True

    def _run_inner(self):
        # 0. Validate
        log = FallbackLog(self.excel_path).load()
        weeks = log.by_week()
        if not weeks:
            self.q.put(("done", RefreshFallbackReport(
                completed=True,
                weeks_processed=0,
                updated_count=0,
                user_modified_count=0,
                still_no_data_count=0,
                error_count=0,
            )))
            return
        total_entries = sum(len(v) for v in weeks.values())
        self.q.put(("status",
                   f"Đang chuẩn bị cập nhật {total_entries} ô fallback "
                   f"trên {len(weeks)} tuần…"))

        # 1. Connect CDP
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(
                    f"http://localhost:{self.port}",
                    timeout=5000,
                )
            except Exception as e:
                self.q.put(("error", format_chrome_connect_error(self.port, e)))
                return

            page = BootstrapWorker._find_vnedu_page(browser)
            if not page:
                self.q.put(("error", format_no_vnedu_tab_error()))
                return
            try:
                page.bring_to_front()
            except Exception:
                pass

            client = KHDHClient(page)
            try:
                ctx = client.fetch_context()
            except Exception as e:
                self.q.put(("error", format_context_read_error(e)))
                return

            try:
                client.enable_edit_mode()
                self._interruptible_sleep(0.4)
            except Exception:
                pass

            # 2. Tổng hợp counter
            report = RefreshFallbackReport()
            self._emit_event("plan_start",
                            message=f"Bắt đầu cập nhật {total_entries} ô fallback")

            sorted_weeks = sorted(weeks.keys())
            for tuan in sorted_weeks:
                if self.stop_event.is_set():
                    self._emit_event("stop", message="user_stop")
                    break
                entries = weeks[tuan]
                self._emit_event("week_start", tuan=tuan,
                                message=f"{len(entries)} ô cần kiểm tra")

                # 2a. Switch tuần + verify
                ok_switch = self._switch_and_verify_week(client, tuan)
                if not ok_switch:
                    report.error_count += len(entries)
                    self._emit_event(
                        "error", tuan=tuan,
                        message=(
                            f"Không chuyển được sang tuần {tuan} (combobox "
                            "trên web không phản hồi sau 3 lần thử). Bỏ qua "
                            f"các ô tuần này. Hãy reload tab VnEdu rồi "
                            "chạy lại nếu muốn cập nhật tuần này."
                        ),
                    )
                    continue

                # 2b. Bật mode Sửa + reload table
                try:
                    client.enable_edit_mode()
                    self._interruptible_sleep(0.3)
                    client.reload_table()
                    self._interruptible_sleep(2.0)
                except Exception as e:
                    self._emit_event("warning", tuan=tuan,
                                    message=f"reload tuần: {e}")

                # 2c. Đọc trạng thái đầy đủ các ô — không chỉ txtTenBai mà
                # cả lop/mon/pm/ppct để verify "context drift" (vd user đã
                # đổi lớp/môn/phân môn của row đó). Nếu context đã thay đổi,
                # entry cũ trong log không còn ý nghĩa và phải được cleanup
                # để tránh ghi nhầm tên bài cho row context mới.
                ids: list[str] = []
                for e in entries:
                    ids.extend([
                        f"txtTenBai_{e.row_key}",
                        f"cboLopHoc_{e.row_key}",
                        f"cboMonHoc_{e.row_key}",
                        f"cboPhanMon_{e.row_key}",
                        f"txtTietPPCT_{e.row_key}",
                    ])
                try:
                    raw_state = client.page.evaluate(_JS_READ_FIELDS, ids) or {}
                except Exception as e:
                    self._emit_event(
                        "error", tuan=tuan,
                        message=f"Không đọc được state DOM tuần {tuan}: {e}",
                    )
                    report.error_count += len(entries)
                    if self.stop_event:
                        self.stop_event.set()
                    break

                # 2c-2. Retry DOM read 1 lần nếu nhiều entry bị missing —
                # khả năng cao là table reload chưa hoàn tất render khi
                # tool đọc. KHÔNG cleanup entry chỉ vì DOM null ở lần đọc
                # đầu — đó là race condition, không phải bằng chứng row đã
                # bị xóa. Chỉ classify "missing_dom" sau khi đã retry +
                # vẫn null, và giữ nguyên trong log để user kiểm tra.
                missing_count = 0
                for e in entries:
                    state_check = raw_state.get(f"txtTenBai_{e.row_key}")
                    if state_check is None:
                        missing_count += 1
                if missing_count > 0:
                    self._emit_event(
                        "info", tuan=tuan,
                        message=(
                            f"  {missing_count}/{len(entries)} ô chưa thấy "
                            "DOM — chờ thêm 1.5s rồi đọc lại."
                        ),
                    )
                    self._interruptible_sleep(1.5)
                    if self.stop_event.is_set():
                        break
                    try:
                        raw_state2 = client.page.evaluate(
                            _JS_READ_FIELDS, ids,
                        ) or {}
                        # Merge: ưu tiên giá trị mới nếu cũ là None
                        for k, v in raw_state2.items():
                            if raw_state.get(k) is None and v is not None:
                                raw_state[k] = v
                    except Exception as e:
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"retry đọc DOM tuần {tuan}: {e}",
                        )

                # 2d. Phân loại entries
                # Logic phân loại theo `raw_ten_bai` (giá trị raw, chưa trim)
                # khi context vẫn khớp với entry log:
                #   - == ""    → user_modified (web/user đã reset, cleanup)
                #   - == " "   → fetch API:
                #                · có tên bài → update DOM + save → cleanup
                #                · vẫn rỗng → still_no_data, giữ log
                #   - khác " " và "" → DOM đã có content thật (web auto-fill
                #     từ getByTiet hoặc user đã gõ nhưng chưa save). Phải
                #     SAVE để commit về server (nếu không, server vẫn còn
                #     " "). Giữ nguyên DOM, ten_bai_to_write = raw_ten_bai.
                week_pending_updates: list[tuple[FallbackEntry, str, str]] = []
                # Tuple: (entry, ten_bai_to_write, source)
                #   source: "fetched" (từ API) | "auto_filled" (DOM đã có)
                week_user_modified: list[FallbackEntry] = []
                week_still_no_data: list[FallbackEntry] = []
                week_skip_missing_dom: list[FallbackEntry] = []
                week_context_changed: list[FallbackEntry] = []

                for entry in entries:
                    if self.stop_event.is_set():
                        break
                    state_ten_bai = raw_state.get(f"txtTenBai_{entry.row_key}")
                    state_lop = raw_state.get(f"cboLopHoc_{entry.row_key}")
                    state_mon = raw_state.get(f"cboMonHoc_{entry.row_key}")
                    state_pm = raw_state.get(f"cboPhanMon_{entry.row_key}")
                    state_ppct = raw_state.get(f"txtTietPPCT_{entry.row_key}")
                    if any(s is None for s in (
                        state_ten_bai, state_lop, state_mon, state_pm, state_ppct
                    )):
                        # Một trong các control không tồn tại trên DOM → row
                        # đã bị remove khỏi giao diện; coi entry là vô hiệu.
                        week_skip_missing_dom.append(entry)
                        continue

                    raw_ten_bai = str((state_ten_bai or {}).get("value") or "")
                    raw_lop = str((state_lop or {}).get("value") or "")
                    raw_mon = str((state_mon or {}).get("value") or "")
                    raw_pm = str((state_pm or {}).get("value") or "")
                    raw_ppct_text = str((state_ppct or {}).get("value") or "").strip()
                    try:
                        raw_ppct = int(raw_ppct_text) if raw_ppct_text else 0
                    except ValueError:
                        raw_ppct = 0

                    # CRITICAL: kiểm context khớp 100% trước khi xét tên bài.
                    # Nếu user đã đổi lop/mon/pm/ppct của row → entry cũ
                    # KHÔNG còn ý nghĩa cho row hiện tại; cleanup an toàn,
                    # KHÔNG fetch tên bài (sẽ ghi sai nếu fetch).
                    context_ok = (
                        raw_lop == str(entry.lop_id or "")
                        and raw_mon == str(entry.mon_id or "")
                        and raw_pm == str(entry.phan_mon_id or "")
                        and raw_ppct == int(entry.ppct or 0)
                    )
                    if not context_ok:
                        week_context_changed.append(entry)
                        continue

                    # Phân loại theo raw_ten_bai
                    if raw_ten_bai == "":
                        # Web/user đã reset ô về rỗng — log entry không còn
                        # áp dụng. Cleanup an toàn, không save.
                        week_user_modified.append(entry)
                        continue

                    if raw_ten_bai != " ":
                        # DOM đã có content thật (vd web auto-fill từ
                        # getByTiet sau reload, hoặc user đã gõ nhưng chưa
                        # save). Cần SAVE tuần để commit về server. Giữ
                        # nguyên DOM, không gọi API.
                        week_pending_updates.append(
                            (entry, raw_ten_bai, "auto_filled")
                        )
                        continue

                    # raw_ten_bai == " " → fetch lại tên bài từ API
                    try:
                        khoi = client.page.evaluate(
                            _JS_GET_KHOI_FOR_LOP,
                            {"rk": entry.row_key, "lop_id": entry.lop_id},
                        ) or ""
                    except Exception:
                        khoi = ""
                    try:
                        res = client.page.evaluate(
                            _JS_FETCH_TEN_BAI,
                            {
                                "rk": entry.row_key,
                                "khoi": khoi,
                                "lop_id": entry.lop_id,
                                "mon_id": entry.mon_id,
                                "pm_id": entry.phan_mon_id,
                                "ppct": entry.ppct,
                                "token": ctx.my_token,
                                "user_id": ctx.my_user_id,
                                "nam_hoc": str(ctx.nam_hoc),
                            },
                        )
                    except Exception as e:
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"fetch ô {entry.row_key}: {e}",
                        )
                        week_still_no_data.append(entry)
                        continue
                    if res and res.get("ok"):
                        tb = (res.get("ten_bai") or "").strip()
                        if tb:
                            week_pending_updates.append((entry, tb, "fetched"))
                        else:
                            week_still_no_data.append(entry)
                    else:
                        err = res.get("err") if res else "no_response"
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"Web phản hồi lỗi cho ô {entry.row_key}: {err}",
                        )
                        week_still_no_data.append(entry)

                # 2e. Áp dụng các update vào DOM
                # Với entry source="auto_filled", DOM đã có nội dung đúng
                # rồi → KHÔNG đụng vào DOM, chỉ trigger save tuần ở Phase
                # 2f để commit về server.
                if week_pending_updates and not self.stop_event.is_set():
                    for entry, tb, source in week_pending_updates:
                        if source == "auto_filled":
                            self._emit_event(
                                "info", tuan=tuan,
                                message=(
                                    f"  Ô {entry.row_key} (PPCT {entry.ppct}, "
                                    f"{entry.lop_text or entry.lop_id}/"
                                    f"{entry.phan_mon_text or entry.mon_text}): "
                                    f"DOM đã có Tên bài '{tb[:50]}{'…' if len(tb)>50 else ''}' "
                                    "(web auto-fill) — sẽ commit vào server."
                                ),
                            )
                            continue
                        # source == "fetched": ghi DOM với tên bài lấy từ API
                        try:
                            client.page.evaluate(
                                _JS_SET_PPCT_TEN_BAI,
                                {
                                    "rk": entry.row_key,
                                    # Chỉ set txtTenBai. Pass None cho các
                                    # field khác để KHÔNG ghi đè giá trị
                                    # hiện tại (PPCT, ghi chú, trạng thái).
                                    "ppct": None,
                                    "ten_bai": tb,
                                    "ghi_chu": None,
                                    "trang_thai": None,
                                },
                            )
                            self._emit_event(
                                "info", tuan=tuan,
                                message=(
                                    f"  Ô {entry.row_key} (PPCT {entry.ppct}, "
                                    f"{entry.lop_text or entry.lop_id}/"
                                    f"{entry.phan_mon_text or entry.mon_text}): "
                                    f"đã có Tên bài '{tb[:50]}{'…' if len(tb)>50 else ''}'"
                                ),
                            )
                        except Exception as e:
                            self._emit_event(
                                "error", tuan=tuan,
                                message=f"set DOM ô {entry.row_key}: {e}",
                            )
                            report.error_count += 1
                            if self.stop_event:
                                self.stop_event.set()
                            break

                # 2f. Nếu DOM đã update → click Lưu
                if week_pending_updates and not self.stop_event.is_set():
                    # Verify lại tuần ngay trước save — race condition: user
                    # có thể đã click combobox tuần khác trong lúc tool xử lý
                    # Phase D. Nếu lệch tuần → halt, tuyệt đối không save.
                    cur_tuan = self._read_current_week(client)
                    if cur_tuan != tuan:
                        self._emit_event(
                            "error", tuan=tuan,
                            message=(
                                f"Ngay trước khi nhấn Lưu, web đang ở tuần "
                                f"{cur_tuan} thay vì tuần {tuan}. Có vẻ "
                                "bạn vừa click sang tuần khác trên Chrome. "
                                "Tool hủy thao tác Lưu để KHÔNG ghi nhầm "
                                "dữ liệu. Hãy giữ tab VnEdu yên rồi chạy "
                                "lại nút [🔄 Cập nhật Tên bài đã fallback]."
                            ),
                        )
                        report.error_count += len(week_pending_updates)
                        if self.stop_event:
                            self.stop_event.set()
                        break

                    # Bật lại mode "Sửa" trước save (PlanExecutor cũng làm
                    # vậy — đảm bảo web không reject save vì còn ở mode
                    # gợi ý TKB do trace nào đó).
                    try:
                        client.enable_edit_mode()
                        self._interruptible_sleep(0.3)
                    except Exception as e:
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"enable_edit_mode trước save: {e}",
                        )

                    self._emit_event("save", tuan=tuan,
                                    message="Click Lưu để ghi xuống server")
                    try:
                        save_res = client.save_week_via_button(
                            wait_s=self.wait_after_save_s,
                        )
                    except Exception as e:
                        save_res = None
                        self._emit_event(
                            "error", tuan=tuan,
                            message=f"save exception: {e}",
                        )

                    save_ok = (
                        save_res is not None
                        and save_res.ok
                        and save_res.success
                        and not save_res.errors
                    )
                    if save_ok:
                        # Commit: xóa các entry đã update khỏi log
                        fetched_n = 0
                        autofill_n = 0
                        for entry, _tb, source in week_pending_updates:
                            log.remove(entry.tuan, entry.row_key)
                            report.updated_count += 1
                            if source == "auto_filled":
                                autofill_n += 1
                            else:
                                fetched_n += 1
                        try:
                            log.save()
                        except Exception as e:
                            self._emit_event(
                                "warning", tuan=tuan,
                                message=f"Lưu log thất bại: {e}",
                            )
                        breakdown = []
                        if fetched_n:
                            breakdown.append(f"{fetched_n} từ API")
                        if autofill_n:
                            breakdown.append(f"{autofill_n} commit DOM auto-fill")
                        breakdown_text = (
                            f" ({', '.join(breakdown)})" if breakdown else ""
                        )
                        self._emit_event(
                            "info", tuan=tuan,
                            message=(
                                f"Đã lưu {len(week_pending_updates)} ô "
                                f"có Tên bài mới vào tuần {tuan}{breakdown_text}."
                            ),
                        )
                    else:
                        # Save fail → halt; không xóa entry vì chúng chưa
                        # thực sự lên server.
                        msg = (save_res.msg if save_res else "save_failed")
                        # Build detail từ pending_updates + save_errors —
                        # phải nói rõ ô nào, lỗi gì.
                        ops_for_detail: list = []
                        for entry, _tb, _src in week_pending_updates:
                            class _Op:
                                pass
                            o = _Op()
                            o.row_key = entry.row_key
                            o.lop_text = entry.lop_text
                            o.mon_text = entry.mon_text
                            o.phan_mon_text = entry.phan_mon_text
                            ops_for_detail.append(o)
                        detail = ""
                        if save_res and save_res.errors:
                            detail = format_save_errors_for_user(
                                save_res.errors, ops_for_detail, max_lines=15,
                            )
                        err_kind = classify_save_error_msg(msg)
                        hint = ERROR_HINTS.get(err_kind, ERROR_HINTS["unknown"])
                        full_msg_parts = [
                            f"Web từ chối lưu tuần {tuan}.",
                            f'Server báo: "{msg}"',
                        ]
                        if detail:
                            full_msg_parts.append(
                                f"Có {len(save_res.errors)} ô bị flag:\n{detail}"
                            )
                        full_msg_parts.append(hint)
                        full_msg = "\n\n".join(full_msg_parts)
                        self._emit_event(
                            "error", tuan=tuan, message=full_msg,
                        )
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=(
                                "⛔ DỪNG cập nhật fallback. Sau khi sửa "
                                "các ô vàng trên web, hãy chạy lại nút "
                                "[🔄 Cập nhật Tên bài đã fallback]."
                            ),
                        )
                        report.error_count += len(week_pending_updates)
                        if self.stop_event:
                            self.stop_event.set()
                        break

                # 2g. Cleanup entry user_modified + context_changed.
                # CHÚ Ý: missing_dom được xử lý RIÊNG ở dưới — KHÔNG cleanup
                # vì DOM null sau retry vẫn có thể là race condition (extjs
                # paging / bug ngầm) thay vì bằng chứng row đã bị xóa.
                stale_groups: list[tuple[list[FallbackEntry], str, str]] = [
                    (week_user_modified, "user_modified", "đã có nội dung khác dấu cách (user đã sửa hoặc RUN khác đã ghi đè)"),
                    (week_context_changed, "context_changed", "đã đổi lớp/môn/phân môn/PPCT (context drift)"),
                ]
                cleanup_any = False
                for group_list, _reason, label in stale_groups:
                    if not group_list:
                        continue
                    for entry in group_list:
                        log.remove(entry.tuan, entry.row_key)
                    cleanup_any = True
                    if group_list is week_user_modified:
                        report.user_modified_count += len(group_list)
                    else:
                        report.context_changed_count += len(group_list)
                    self._emit_event(
                        "info", tuan=tuan,
                        message=(
                            f"  {len(group_list)} ô {label} → bỏ khỏi log."
                        ),
                    )

                # 2g-2. Missing DOM — GIỮ trong log, chỉ cảnh báo.
                # Lý do: DOM null không phân biệt được race condition với
                # row đã thực sự xóa. Giữ entry để user có thể inspect ô,
                # và nếu là race → lần chạy sau sẽ tự xử lý đúng.
                if week_skip_missing_dom:
                    report.skipped_missing_dom_count += len(week_skip_missing_dom)
                    # Tăng retry_count để user biết entry đã thử bao nhiêu lần
                    for entry in week_skip_missing_dom:
                        log.update_check(
                            entry.tuan, entry.row_key, "missing_dom",
                        )
                    cleanup_any = True  # cần save log vì update_check đã mutate
                    self._emit_event(
                        "warning", tuan=tuan,
                        message=(
                            f"  {len(week_skip_missing_dom)} ô không thấy "
                            "DOM (sau retry) — GIỮ trong log để chạy lại "
                            "sau, KHÔNG tự xóa. Nếu chắc chắn các ô này "
                            "đã không còn cần thiết, xóa thủ công file "
                            "<excel>.fallback.json hoặc đợi lần chạy kế."
                        ),
                    )
                if cleanup_any:
                    try:
                        log.save()
                    except Exception as e:
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"Không lưu được log sau cleanup: {e}",
                        )

                # 2h. Update last_check cho các entry vẫn no_data (giữ lại)
                if week_still_no_data:
                    for entry in week_still_no_data:
                        log.update_check(
                            entry.tuan, entry.row_key, "still_no_data",
                        )
                        report.still_no_data_count += 1
                    try:
                        log.save()
                    except Exception as e:
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"Không lưu được log still_no_data: {e}",
                        )
                    self._emit_event(
                        "warning", tuan=tuan,
                        message=(
                            f"  {len(week_still_no_data)} ô vẫn chưa có "
                            f"CSDL trên web — giữ trong log để chạy lại sau."
                        ),
                    )

                report.weeks_processed += 1
                self._emit_event(
                    "week_done", tuan=tuan,
                    message=(
                        f"updated={len(week_pending_updates)} "
                        f"user_modified={len(week_user_modified)} "
                        f"still_no_data={len(week_still_no_data)}"
                    ),
                )

            report.completed = not self.stop_event.is_set()
            report.stopped = self.stop_event.is_set()
            self._emit_event(
                "plan_done",
                message=(
                    f"Cập nhật xong: updated={report.updated_count} "
                    f"user_modified={report.user_modified_count} "
                    f"still_no_data={report.still_no_data_count} "
                    f"errors={report.error_count}"
                ),
            )
            self.q.put(("done", report))

    def _switch_and_verify_week(self, client: KHDHClient, target_tuan: int,
                                  max_attempts: int = 3,
                                  timeout_per_attempt_s: float = 4.0) -> bool:
        """Logic switch giống PlanExecutor — tránh ghi nhầm tuần."""
        if self._read_current_week(client) == target_tuan:
            try:
                client.reload_table()
                self._interruptible_sleep(1.5)
            except Exception:
                pass
            return self._read_current_week(client) == target_tuan

        for _attempt in range(max_attempts):
            try:
                client.select_week_in_ui(target_tuan)
            except Exception:
                pass
            deadline = time.monotonic() + timeout_per_attempt_s
            while time.monotonic() < deadline:
                if self.stop_event.is_set():
                    return False
                time.sleep(0.3)
                if self._read_current_week(client) == target_tuan:
                    self._interruptible_sleep(0.8)
                    return True
        return False

    def _read_current_week(self, client: KHDHClient) -> int:
        """Đọc tuần đang chọn trên combobox UI. 0 nếu không xác định."""
        try:
            r = client.page.evaluate(_JS_GET_CURRENT_WEEK)
            if r and r.get("ok"):
                return int(r.get("tuan") or 0)
        except Exception:
            pass
        return 0

    def _emit_event(self, event_type: str, tuan: int = 0, message: str = ""):
        try:
            self.q.put((
                "event",
                ExecutorEvent(
                    event_type=event_type,
                    tuan=tuan,
                    message=message,
                ),
            ))
        except Exception:
            pass


@dataclass
class RefreshFallbackReport:
    """Tổng kết phiên cập nhật fallback."""
    completed: bool = False
    stopped: bool = False
    weeks_processed: int = 0
    updated_count: int = 0           # Đã fetch + lưu tên bài mới
    user_modified_count: int = 0     # User/RUN khác đã sửa, cleanup
    still_no_data_count: int = 0     # Vẫn chưa có CSDL — giữ log
    skipped_missing_dom_count: int = 0
    context_changed_count: int = 0   # Lop/mon/pm/ppct đã đổi → cleanup
    error_count: int = 0

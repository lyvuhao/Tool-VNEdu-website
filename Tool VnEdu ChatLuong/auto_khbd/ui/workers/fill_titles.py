"""Worker điền tên bài HĐTN còn thiếu."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from ...engine.analyzer.hdtn import _classify_hdtn_part, _extract_grade_from_lop_text
from ...engine.catalog import get_hdtn_lesson_title_catalog
from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
    format_row_with_context,
)
from ...engine.executor.js import _JS_GET_CURRENT_WEEK, _JS_READ_FIELDS, _JS_SET_PPCT_TEN_BAI
from ...engine.executor.models import ExecutorEvent
from ...engine.parser import SlotData
from ...engine.planner import FillOp
from .bootstrap import BootstrapWorker


@dataclass
class FillMissingHDTNTitlesReport:
    """Tổng kết phiên điền tên bài HĐTN thiếu từ kế hoạch Word."""
    completed: bool = False
    stopped: bool = False
    weeks_processed: int = 0
    updated_count: int = 0
    skipped_has_title_count: int = 0
    no_word_title_count: int = 0
    context_changed_count: int = 0
    error_count: int = 0
    updated_details: list[str] = field(default_factory=list)
    no_word_details: list[str] = field(default_factory=list)
    skipped_details: list[str] = field(default_factory=list)


def _trim_report_details(items: list[str], max_items: int = 12) -> str:
    """Rút gọn danh sách chi tiết để vừa hộp thoại Tk."""
    if not items:
        return ""
    shown = items[:max_items]
    lines = [f"  - {x}" for x in shown]
    rest = len(items) - len(shown)
    if rest > 0:
        lines.append(f"  - ... còn {rest} dòng khác, xem Nhật ký để kiểm tra tiếp.")
    return "\n".join(lines)


def _fill_title_detail(tuan: int, slot: SlotData, title: str = "") -> str:
    """Format 1 dòng chi tiết cho report điền tên bài."""
    ppct_text = str(slot.ppct or "").strip() or "?"
    label = format_row_with_context(
        slot.row_key,
        lop_text=slot.lop_text,
        mon_text=slot.mon_text,
        phan_mon_text=slot.phan_mon_text,
    )
    tail = f" -> {title}" if title else ""
    return f"Tuần {tuan}: {label}, PPCT {ppct_text}{tail}"


class FillMissingHDTNTitlesWorker(threading.Thread):
    """Quét dải tuần đang có trên web và chỉ điền Tên bài HĐTN còn thiếu.

    Worker này dành cho trường hợp giáo viên đã nhập đúng KHBD rồi nhưng một
    vài ô Tên bài dạy bị trống/dấu cách. Không nhập lại lớp/môn/PPCT/trạng
    thái, chỉ set `txtTenBai_*` khi context DOM vẫn khớp dữ liệu server.
    """

    def __init__(
        self,
        port: int,
        tuan_from: int,
        tuan_to: int,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        wait_after_save_s: float = 10.0,
    ):
        super().__init__(daemon=True, name="KHDH-FillMissingHDTNTitles")
        self.port = int(port)
        self.tuan_from = int(tuan_from)
        self.tuan_to = int(tuan_to)
        self.q = event_queue
        self.stop_event = stop_event
        self.wait_after_save_s = wait_after_save_s
        self.catalog = get_hdtn_lesson_title_catalog()

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi không lường trước: {e}\n\n{tb}"))

    def _interruptible_sleep(self, seconds: float) -> bool:
        if seconds <= 0:
            return True
        deadline = time.monotonic() + float(seconds)
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                return False
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return True

    def _run_inner(self):
        report = FillMissingHDTNTitlesReport()
        weeks = list(range(self.tuan_from, self.tuan_to + 1))
        self.q.put(("status", f"Đang chuẩn bị quét tên bài HĐTN tuần {self.tuan_from}-{self.tuan_to}…"))

        if self.catalog.load_errors:
            self._emit_event(
                "warning",
                message=(
                    "Kho tên bài HĐTN Word chưa sẵn sàng: "
                    + "; ".join(self.catalog.load_errors[:3])
                ),
            )

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
                client.fetch_context()
            except Exception as e:
                self.q.put(("error", format_context_read_error(e)))
                return

            try:
                client.enable_edit_mode()
                self._interruptible_sleep(0.4)
            except Exception:
                pass

            self._emit_event("plan_start", message=f"Quét {len(weeks)} tuần để điền tên bài HĐTN thiếu")
            for tuan in weeks:
                if self.stop_event.is_set():
                    self._emit_event("stop", message="user_stop")
                    break

                self._emit_event("week_start", tuan=tuan, message="đang đọc dữ liệu web")
                if not self._switch_and_verify_week(client, tuan):
                    report.error_count += 1
                    self._emit_event("error", tuan=tuan, message=f"Không chuyển được sang tuần {tuan}")
                    break

                try:
                    wd = client.fetch_week(tuan, is_edit=1)
                except Exception as e:
                    report.error_count += 1
                    self._emit_event("error", tuan=tuan, message=f"Không đọc được tuần {tuan}: {e}")
                    break

                candidates: list[tuple[SlotData, str]] = []
                for slot in wd.slots:
                    if not slot.has_lop:
                        continue
                    try:
                        ppct = int(str(slot.ppct or "").strip() or "0")
                    except ValueError:
                        continue
                    if ppct <= 0:
                        continue
                    if str(slot.ten_bai or "").strip():
                        report.skipped_has_title_count += 1
                        continue
                    op = FillOp(
                        tuan=tuan,
                        row_key=slot.row_key,
                        lop_id=slot.lop_id,
                        lop_text=slot.lop_text,
                        mon_id=slot.mon_id,
                        mon_text=slot.mon_text,
                        phan_mon_id=slot.phan_mon_id,
                        phan_mon_text=slot.phan_mon_text,
                        ppct=ppct,
                    )
                    title = self.catalog.lookup_for_op(op)
                    if title:
                        candidates.append((slot, title))
                    elif _extract_grade_from_lop_text(slot.lop_text) in (6, 8) and _classify_hdtn_part(slot.mon_text, slot.phan_mon_text):
                        report.no_word_title_count += 1
                        report.no_word_details.append(_fill_title_detail(tuan, slot))
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=(
                                f"Không tìm thấy tên bài trong Word cho ô "
                                f"{slot.row_key}, PPCT {slot.ppct}, "
                                f"{slot.lop_text}/{slot.phan_mon_text or slot.mon_text}."
                            ),
                        )

                if not candidates:
                    report.weeks_processed += 1
                    self._emit_event("week_done", tuan=tuan, message="không có ô HĐTN thiếu tên bài")
                    continue

                ids: list[str] = []
                for slot, _title in candidates:
                    ids.extend([
                        f"txtTenBai_{slot.row_key}",
                        f"cboLopHoc_{slot.row_key}",
                        f"cboMonHoc_{slot.row_key}",
                        f"cboPhanMon_{slot.row_key}",
                        f"txtTietPPCT_{slot.row_key}",
                    ])
                try:
                    raw_state = client.page.evaluate(_JS_READ_FIELDS, ids) or {}
                except Exception as e:
                    report.error_count += len(candidates)
                    self._emit_event("error", tuan=tuan, message=f"Không đọc được DOM tuần {tuan}: {e}")
                    break

                updated_this_week = 0
                for slot, title in candidates:
                    if self.stop_event.is_set():
                        break
                    row_key = slot.row_key
                    state_tb = raw_state.get(f"txtTenBai_{row_key}")
                    state_lop = raw_state.get(f"cboLopHoc_{row_key}")
                    state_mon = raw_state.get(f"cboMonHoc_{row_key}")
                    state_pm = raw_state.get(f"cboPhanMon_{row_key}")
                    state_ppct = raw_state.get(f"txtTietPPCT_{row_key}")
                    if any(s is None for s in (state_tb, state_lop, state_mon, state_pm, state_ppct)):
                        report.context_changed_count += 1
                        report.skipped_details.append(_fill_title_detail(tuan, slot, "không thấy đủ control trên web"))
                        continue
                    raw_title = str((state_tb or {}).get("value") or "")
                    if raw_title.strip():
                        report.skipped_has_title_count += 1
                        continue
                    raw_lop = str((state_lop or {}).get("value") or "")
                    raw_mon = str((state_mon or {}).get("value") or "")
                    raw_pm = str((state_pm or {}).get("value") or "")
                    raw_ppct_text = str((state_ppct or {}).get("value") or "").strip()
                    try:
                        raw_ppct = int(raw_ppct_text) if raw_ppct_text else 0
                    except ValueError:
                        raw_ppct = 0
                    if (
                        raw_lop != str(slot.lop_id or "")
                        or raw_mon != str(slot.mon_id or "")
                        or raw_pm != str(slot.phan_mon_id or "")
                        or raw_ppct != int(slot.ppct or 0)
                    ):
                        report.context_changed_count += 1
                        report.skipped_details.append(_fill_title_detail(tuan, slot, "dữ liệu trên màn hình đã đổi"))
                        self._emit_event(
                            "warning", tuan=tuan,
                            message=f"Ô {row_key}: dữ liệu trên màn hình đã đổi, bỏ qua để tránh ghi nhầm.",
                        )
                        continue
                    try:
                        client.page.evaluate(
                            _JS_SET_PPCT_TEN_BAI,
                            {
                                "rk": row_key,
                                "ppct": None,
                                "ten_bai": title,
                                "ghi_chu": None,
                                "trang_thai": None,
                            },
                        )
                        updated_this_week += 1
                        report.updated_details.append(_fill_title_detail(tuan, slot, title))
                        self._emit_event(
                            "info", tuan=tuan,
                            message=f"Ô {row_key} PPCT {slot.ppct}: điền '{title[:70]}{'…' if len(title) > 70 else ''}'",
                        )
                    except Exception as e:
                        report.error_count += 1
                        self._emit_event("error", tuan=tuan, message=f"Không điền được ô {row_key}: {e}")
                        break

                if updated_this_week and self.stop_event.is_set():
                    try:
                        client.reload_table()
                    except Exception:
                        pass
                    self._emit_event(
                        "warning", tuan=tuan,
                        message=(
                            "Đã dừng giữa lúc điền tên bài; tool reload lại "
                            "bảng để bỏ các thay đổi chưa lưu, tránh bạn vô "
                            "tình bấm Lưu thủ công sau đó."
                        ),
                    )
                    break

                if updated_this_week and not self.stop_event.is_set():
                    cur_tuan = self._read_current_week(client)
                    if cur_tuan != tuan:
                        report.error_count += updated_this_week
                        self._emit_event(
                            "error", tuan=tuan,
                            message=f"Trước khi lưu, web đang ở tuần {cur_tuan} thay vì tuần {tuan}; hủy để tránh ghi nhầm.",
                        )
                        break
                    try:
                        client.enable_edit_mode()
                        self._interruptible_sleep(0.3)
                    except Exception:
                        pass
                    self._emit_event("save", tuan=tuan, message=f"Lưu {updated_this_week} ô tên bài")
                    try:
                        save_res = client.save_week_via_button(wait_s=self.wait_after_save_s)
                    except Exception as e:
                        save_res = None
                        self._emit_event("error", tuan=tuan, message=f"Lỗi lưu tuần: {e}")
                    save_ok = (
                        save_res is not None
                        and save_res.ok
                        and save_res.success
                        and not save_res.errors
                    )
                    if save_ok:
                        report.updated_count += updated_this_week
                    else:
                        msg = save_res.msg if save_res else "save_failed"
                        report.error_count += updated_this_week
                        self._emit_event("error", tuan=tuan, message=f"Web từ chối lưu tuần {tuan}: {msg}")
                        break

                report.weeks_processed += 1
                self._emit_event("week_done", tuan=tuan, message=f"đã điền {updated_this_week} ô")

            report.completed = not self.stop_event.is_set() and report.error_count == 0
            report.stopped = self.stop_event.is_set()
            self._emit_event(
                "plan_done",
                message=(
                    f"Điền tên bài xong: {report.updated_count} ô cập nhật, "
                    f"{report.no_word_title_count} ô chưa có trong Word, "
                    f"{report.error_count} lỗi"
                ),
            )
            self.q.put(("done", report))

    def _switch_and_verify_week(self, client: KHDHClient, target_tuan: int,
                                  max_attempts: int = 3) -> bool:
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
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if self.stop_event.is_set():
                    return False
                time.sleep(0.3)
                if self._read_current_week(client) == target_tuan:
                    self._interruptible_sleep(0.8)
                    return True
        return False

    def _read_current_week(self, client: KHDHClient) -> int:
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

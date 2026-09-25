"""Worker quét sức khoẻ và quét TKB toàn năm."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections import defaultdict
from typing import TYPE_CHECKING

from playwright.sync_api import sync_playwright

from ...engine.analyzer.pattern_analyzer import PatternAnalyzer
from ...engine.analyzer.reports import HealthScanResult
from ...engine.client.client import KHDHClient
from ...engine.client.js import _JS_GET_TOTAL_WEEKS
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from .bootstrap import BootstrapWorker
from .tkb_import import group_tkb_patterns, ImportTKBReport

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ...engine.profile.profile import KHDHProfile


class HealthScanWorker(threading.Thread):
    """Quét tình trạng KHDH chỉ trong scope group user có quyền nhập."""

    def __init__(
        self,
        port: int,
        profile: "KHDHProfile",
        tuan_to: int,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        batch_size: int = 6,
    ):
        super().__init__(daemon=True, name="KHDH-HealthScan")
        self.port = port
        self.profile = profile
        self.tuan_to = int(tuan_to)
        self.q = event_queue
        self.stop_event = stop_event
        self.batch_size = max(1, int(batch_size))

    def _build_allowed_groups(self) -> set[str]:
        allowed = {entry.group_key for entry in (self.profile.ppct_starts or [])}
        for tmpl in self.profile.all_templates():
            for slot in tmpl.slots:
                allowed.add(slot.group_key)
        return allowed

    def _build_expected_slot_count_by_week(self, tuan_to: int, allowed_groups: set[str]) -> dict[int, dict[str, int]]:
        expected: dict[int, dict[str, int]] = {}
        for tuan in range(1, tuan_to + 1):
            tmpl = self.profile.get_active_template(tuan)
            counts: dict[str, int] = defaultdict(int)
            for slot in tmpl.slots:
                if slot.group_key in allowed_groups:
                    counts[slot.group_key] += 1
            expected[tuan] = dict(counts)
        return expected

    def run(self):
        try:
            allowed_groups = self._build_allowed_groups()
            expected = self._build_expected_slot_count_by_week(self.tuan_to, allowed_groups)
            with sync_playwright() as pw:
                self.q.put(("status", "Kết nối trình duyệt…"))
                try:
                    browser = pw.chromium.connect_over_cdp(
                        f"http://localhost:{self.port}", timeout=5000,
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
                    time.sleep(0.5)
                except Exception:
                    pass

                tw = client.get_total_weeks()
                if tw.get("ok") and tw.get("max"):
                    self.tuan_to = min(self.tuan_to, int(tw["max"]))

                weeks = list(range(1, self.tuan_to + 1))
                if not weeks:
                    self.q.put(("done", HealthScanResult()))
                    return

                def _on_batch(done: int, tot: int, batch_weeks: list[int]):
                    label = f"tuần {batch_weeks[0]}–{batch_weeks[-1]}"
                    self.q.put(("progress", done, tot, batch_weeks, label))

                self.q.put(("status", f"Quét tình trạng {len(weeks)} tuần…"))
                weeks_data = client.fetch_weeks_parallel(
                    weeks,
                    is_edit=1,
                    batch_size=self.batch_size,
                    on_batch=_on_batch,
                    stop_event=self.stop_event,
                )
                result = PatternAnalyzer.analyze_health_in_scope(
                    weeks_data=weeks_data,
                    allowed_groups=allowed_groups,
                    expected_slot_count_by_week=expected,
                )
                self.q.put(("done", result))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"HealthScan failed: {e}\n\n{tb}"))


class TKBScanWorker(threading.Thread):
    """Quét TKB toàn năm + group theo pattern.

    SIẾT LOGIC quan trọng:
    1. CHỈ READ qua endpoint `lich_bao_giang.lich_bao_giang` (đã có).
       KHÔNG ghi gì lên server.
    2. Halt-on-fetch-fail: nếu >50% tuần fail → emit error, dừng.
    3. Stop-event support: user có thể bấm Esc / đóng dialog.
    4. Halt KHÔNG có nghĩa fail: vẫn emit `done` với patterns rỗng nếu
       toàn bộ tuần lỗi → UI hiển thị "Không quét được tuần nào".

    Events qua queue:
        ("status", text)
        ("progress", done_count, total)
        ("done", ImportTKBReport)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        is_edit: int = 2,
        batch_size: int = 6,
    ):
        super().__init__(daemon=True, name="KHDH-TKBScan")
        self.port = port
        self.q = event_queue
        self.stop_event = stop_event
        self.is_edit = is_edit
        self.batch_size = batch_size

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi quét TKB: {e}\n\n{tb}"))

    def _run_inner(self):
        t0 = time.time()
        with sync_playwright() as pw:
            self.q.put(("status", "Đang kết nối Chrome…"))
            try:
                browser = pw.chromium.connect_over_cdp(
                    f"http://localhost:{self.port}",
                    timeout=5000,
                )
            except Exception as e:
                self.q.put((
                    "error",
                    format_chrome_connect_error(self.port, e),
                ))
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

            # Đếm tổng tuần qua _JS_GET_TOTAL_WEEKS
            self.q.put(("status", "Đang đọc danh sách tuần học…"))
            try:
                info = page.evaluate(_JS_GET_TOTAL_WEEKS)
            except Exception as e:
                self.q.put((
                    "error",
                    f"Không đọc được danh sách tuần học: {e}\n\n"
                    "Hãy reload tab VnEdu (F5) rồi thử lại.",
                ))
                return
            if not info or not info.get("ok"):
                err = info.get("err", "no_response") if info else "no_response"
                self.q.put((
                    "error",
                    f"VnEdu chưa load xong combobox tuần ({err}).\n\n"
                    "Hãy mở module Kế hoạch dạy học, chờ trang tải xong, "
                    "rồi thử lại.",
                ))
                return
            min_w = int(info.get("min") or 1)
            max_w = int(info.get("max") or 35)
            tuans = list(range(min_w, max_w + 1))
            total = len(tuans)
            self.q.put(("status",
                       f"Đang quét {total} tuần ({min_w}–{max_w})…"))
            self.q.put(("progress", 0, total))

            # Fetch parallel + emit progress per batch
            fetch_errors: list[tuple[int, str]] = []
            done_count = 0

            def on_batch(done, total_, batch):
                nonlocal done_count
                done_count = done
                self.q.put(("progress", done, total_))

            try:
                weeks_data = client.fetch_weeks_parallel(
                    tuans,
                    is_edit=self.is_edit,
                    batch_size=self.batch_size,
                    on_batch=on_batch,
                    stop_event=self.stop_event,
                )
            except Exception as e:
                self.q.put((
                    "error",
                    f"Lỗi khi fetch TKB: {e}\n\n"
                    "Có thể server VnEdu quá tải hoặc mạng không ổn định. "
                    "Hãy thử lại sau ít phút.",
                ))
                return

            # Build fetch_errors từ các tuần không trong weeks_data
            for w in tuans:
                if w not in weeks_data:
                    fetch_errors.append((w, "fetch_failed"))

            # Halt-on-fetch-fail: nếu > 50% fail → fail toàn bộ
            if len(fetch_errors) > total // 2:
                self.q.put((
                    "error",
                    f"Chỉ fetch được {len(weeks_data)}/{total} tuần — "
                    "quá nhiều tuần lỗi. Có thể session VnEdu hết hạn "
                    "hoặc mạng không ổn định.\n\n"
                    "Hãy reload tab VnEdu, đăng nhập lại nếu cần, rồi "
                    "thử lại.",
                ))
                return

            if self.stop_event.is_set():
                # User huỷ — vẫn emit done với data đã có để dialog hiển thị
                # phần đã quét. Không coi là error.
                self.q.put(("status", "Đã dừng theo yêu cầu"))

            patterns = group_tkb_patterns(weeks_data, fetch_errors)
            duration_ms = int((time.time() - t0) * 1000)
            report = ImportTKBReport(
                patterns=patterns,
                duration_ms=duration_ms,
                completed=not self.stop_event.is_set(),
                stopped=self.stop_event.is_set(),
            )
            self.q.put(("done", report))

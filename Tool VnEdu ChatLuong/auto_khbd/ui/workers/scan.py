"""Worker quét KHDH cho tab Nâng cao."""

from __future__ import annotations

import queue
import threading
import time
import traceback

from playwright.sync_api import sync_playwright

from ...engine.analyzer.pattern_analyzer import PatternAnalyzer
from ...engine.analyzer.reports import YearScanReport
from ...engine.catalog import LessonCatalog
from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — ScanWorker (cho Tab Nâng cao)
# =====================================================================

class ScanWorker(threading.Thread):
    """Quét toàn năm để build YearScanReport + LessonCatalog (cho Nâng cao).

    Cải tiến (2026-05):
    - Fetch song song 6 tuần/batch qua `fetch_weeks_parallel` → 5-8x nhanh
      hơn tuần tự (~35 tuần xong trong 17-21s thay vì ~30-40s).
    - Auto-detect `tuan_to` từ store `cboTuanHoc` để không quét quá web max.
    - Emit progress theo batch thay vì per-week (giảm UI thrashing).
    """

    def __init__(
        self,
        port: int,
        tuan_from: int,
        tuan_to: int,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        batch_size: int = 6,
    ):
        super().__init__(daemon=True, name="KHDH-Scan")
        self.port = port
        self.tuan_from = tuan_from
        self.tuan_to = tuan_to
        self.q = event_queue
        self.stop_event = stop_event
        self.batch_size = max(1, int(batch_size))

    def run(self):
        try:
            with sync_playwright() as pw:
                self.q.put(("status", "Kết nối trình duyệt…"))
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
                client.enable_goi_y_tkb_mode()
                time.sleep(0.5)

                # Auto-detect tuan_to nếu user chọn vượt web max
                tw = client.get_total_weeks()
                if tw.get("ok") and tw.get("max"):
                    web_max = int(tw["max"])
                    if self.tuan_to > web_max:
                        self.q.put((
                            "status",
                            f"Web có {web_max} tuần — giảm tuan_to "
                            f"từ {self.tuan_to} xuống {web_max}",
                        ))
                        self.tuan_to = web_max

                weeks = list(range(self.tuan_from, self.tuan_to + 1))
                total = len(weeks)
                if not weeks:
                    self.q.put(("done", YearScanReport(), LessonCatalog(), {}))
                    return

                # Fetch song song theo batch
                done_counter = [0]

                def _on_batch(done: int, tot: int, batch_weeks: list[int]):
                    done_counter[0] = done
                    label = f"tuần {batch_weeks[0]}–{batch_weeks[-1]}"
                    self.q.put(("progress", done, tot, batch_weeks[-1], label))

                self.q.put((
                    "status",
                    f"Bắt đầu quét {total} tuần (song song {self.batch_size}/batch)…",
                ))
                t0 = time.time()
                weeks_data = client.fetch_weeks_parallel(
                    weeks,
                    is_edit=2,  # Sửa (Gợi ý theo TKB) — load đủ slot từ TKB
                    batch_size=self.batch_size,
                    on_batch=_on_batch,
                    stop_event=self.stop_event,
                )
                fetch_ms = int((time.time() - t0) * 1000)

                self.q.put((
                    "status",
                    f"Đã fetch {len(weeks_data)} tuần trong "
                    f"{fetch_ms/1000:.1f}s. Đang phân tích…",
                ))
                report = PatternAnalyzer.build_report(weeks_data)
                catalog = LessonCatalog()
                catalog.ingest_weeks(weeks_data)
                self.q.put(("done", report, catalog, weeks_data))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi quét: {e}\n\n{tb}"))

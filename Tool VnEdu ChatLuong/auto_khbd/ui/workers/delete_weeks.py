"""Worker xoá KHDH theo tuần."""

from __future__ import annotations

import queue
import threading
import traceback

from playwright.sync_api import sync_playwright

from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — DeleteWeeksWorker
# =====================================================================

class DeleteWeeksWorker(threading.Thread):
    """Xóa nhiều tuần KHDH liên tiếp của giáo viên hiện tại.

    SIẾT LOGIC quan trọng (đọc kỹ trước khi sửa):
    1. **CHỈ** dùng `KHDHClient.delete_weeks_range` → endpoint
       `xoaLichBaoGiangGVHienTai` (xóa 1 tuần cho GV).
    2. **KHÔNG BAO GIỜ** gọi `delete_year` / `xoaLichBaoGiangGV`
       (xóa cả cấp).
    3. Halt-on-error: nếu 1 tuần fail → DỪNG (tránh chuỗi lỗi do
       session expired).
    4. Tham số `tuan` truyền explicit qua API body, không phụ thuộc
       combobox web hiện tại.

    Events qua queue:
        ("status", text)
        ("progress", idx, total, tuan, success: bool, msg: str)
        ("done", report_dict)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        tuan_from: int,
        tuan_to: int,
        event_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        super().__init__(daemon=True, name="KHDH-DeleteWeeks")
        self.port = port
        # Re-validate tại init để fail-fast trước khi spawn thread
        if not isinstance(tuan_from, int) or not isinstance(tuan_to, int):
            raise TypeError("tuan_from và tuan_to phải là int")
        if not (1 <= tuan_from <= 52 and 1 <= tuan_to <= 52):
            raise ValueError(
                f"Tuần ngoài khoảng 1..52: from={tuan_from}, to={tuan_to}"
            )
        if tuan_from > tuan_to:
            raise ValueError(
                f"tuan_from ({tuan_from}) > tuan_to ({tuan_to})"
            )
        self.tuan_from = tuan_from
        self.tuan_to = tuan_to
        self.q = event_queue
        self.stop_event = stop_event

    def run(self):
        try:
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
                try:
                    ctx = client.fetch_context()
                except Exception as e:
                    self.q.put(("error", format_context_read_error(e)))
                    return

                weeks = list(range(self.tuan_from, self.tuan_to + 1))
                self.q.put((
                    "status",
                    f"Bắt đầu xóa {len(weeks)} tuần "
                    f"({self.tuan_from}→{self.tuan_to}) cho GV "
                    f"{ctx.giao_vien_name}, năm {ctx.nam_hoc}…",
                ))

                def _on_progress(idx: int, total: int, tuan: int, res):
                    success = bool(
                        res
                        and res.ok
                        and res.data
                        and isinstance(res.data, dict)
                        and res.data.get("success")
                    )
                    msg = ""
                    if res:
                        if res.data and isinstance(res.data, dict):
                            msg = str(res.data.get("msg") or "")
                        if not msg:
                            msg = res.error or f"status={res.status}"
                    self.q.put((
                        "progress", idx, total, tuan, success, msg,
                    ))

                try:
                    result = client.delete_weeks_range(
                        tuan_from=self.tuan_from,
                        tuan_to=self.tuan_to,
                        on_progress=_on_progress,
                        stop_event=self.stop_event,
                    )
                except ValueError as e:
                    # Validation lại lần nữa trong delete_weeks_range
                    self.q.put(("error", f"Tham số không hợp lệ: {e}"))
                    return
                except Exception as e:
                    tb = traceback.format_exc()
                    self.q.put((
                        "error",
                        f"Lỗi xóa tuần: {type(e).__name__}: {e}\n\n{tb}",
                    ))
                    return

                self.q.put(("done", result))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put((
                "error",
                f"Lỗi không lường trước: {e}\n\n{tb}",
            ))

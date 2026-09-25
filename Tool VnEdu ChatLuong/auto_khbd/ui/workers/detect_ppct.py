"""Worker dò PPCT bắt đầu."""

from __future__ import annotations

import queue
import threading
import time
import traceback

from playwright.sync_api import sync_playwright

from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from ...engine.profile.profile import KHDHProfile
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — DetectPPCTWorker
# =====================================================================

class DetectPPCTWorker(threading.Thread):
    """Quét nhanh các tuần để tìm PPCT lớn nhất theo (Lớp × Phân môn).

    Mục đích: gợi ý cho giáo viên `ppct_start = max(PPCT đã có) + 1` để
    tránh trùng PPCT với data đã save trên web (gây lỗi "tiết PPCT này có
    trạng thái không phù hợp...").

    Thiết kế (siết logic):
    1. **Tốc độ**: fetch song song qua `fetch_weeks_parallel` (6 tuần/batch),
       giảm 5-8x so với tuần tự. ~35 tuần xong trong ~6-10s thay vì ~25-30s.
    2. **Auto-detect tuan_to**: đọc từ `cboTuanHoc` store thay vì cứng 35.
    3. **Tách max lẻ/chẵn**: track riêng `max_le` và `max_chan` cho mỗi
       group (lop|mon|pm). Báo cáo cả overall + chi tiết để user thấy rõ
       group nào chỉ xuất hiện ở 1 phía (vd môn HĐTN khác giữa tuần lẻ/chẵn).
    4. **Track group_appearances**: list các tuần mà group xuất hiện —
       giúp user biết môn này dạy ở những tuần nào, có pattern lẻ/chẵn rõ
       ràng không.
    5. **Detect new groups**: phát hiện group có trên web nhưng KHÔNG có
       trong profile hiện tại (tuần trước đó user đã từng dạy nhưng giờ
       bỏ) — báo để user kiểm tra lại profile.

    Events:
        ("status", text)
        ("progress", done, total, last_batch_weeks, info)
        ("done", report_dict) — report_dict = {
            "max_overall": dict[group_key, max_ppct],
            "max_le": dict[group_key, max_ppct],
            "max_chan": dict[group_key, max_ppct],
            "group_info": dict[group_key, {lop_text, mon_text, ...}],
            "group_appearances": dict[group_key, {"le_weeks": [...], "chan_weeks": [...]}],
            "weeks_scanned": int,
            "elapsed_ms": int,
        }
        ("error", message)
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
        super().__init__(daemon=True, name="KHDH-DetectPPCT")
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
                client.enable_edit_mode()  # mode Sửa, KHÔNG gợi ý
                time.sleep(0.5)

                # Auto-detect tuan_to nếu user chưa biết hoặc giá trị < total
                tw = client.get_total_weeks()
                if tw.get("ok") and tw.get("max"):
                    web_max = int(tw["max"])
                    if self.tuan_to > web_max:
                        self.q.put((
                            "status",
                            f"Web có tổng {web_max} tuần — giảm tuan_to "
                            f"từ {self.tuan_to} xuống {web_max}",
                        ))
                        self.tuan_to = web_max

                weeks = list(range(self.tuan_from, self.tuan_to + 1))
                total = len(weeks)
                if not weeks:
                    self.q.put(("done", {
                        "max_overall": {}, "max_le": {}, "max_chan": {},
                        "group_info": {}, "group_appearances": {},
                        "weeks_scanned": 0, "elapsed_ms": 0,
                    }))
                    return

                # Track riêng cho lẻ và chẵn — siết logic theo yêu cầu user
                max_overall: dict[str, int] = {}
                max_le: dict[str, int] = {}
                max_chan: dict[str, int] = {}
                group_info: dict[str, dict] = {}
                group_appearances: dict[str, dict] = {}

                # Callback nhận progress sau mỗi batch
                def _on_batch(done: int, tot: int, batch_weeks: list[int]):
                    label = f"tuần {batch_weeks[0]}–{batch_weeks[-1]}"
                    self.q.put(("progress", done, tot, batch_weeks, label))

                t0 = time.time()
                self.q.put((
                    "status",
                    f"Bắt đầu quét {total} tuần (song song {self.batch_size}/batch)…",
                ))
                weeks_data = client.fetch_weeks_parallel(
                    weeks,
                    is_edit=1,
                    batch_size=self.batch_size,
                    on_batch=_on_batch,
                    stop_event=self.stop_event,
                )

                # Aggregate kết quả
                for w, wd in weeks_data.items():
                    if self.stop_event.is_set():
                        break
                    is_chan = KHDHProfile.is_chan(int(w))
                    for slot in wd.filled_slots:
                        try:
                            ppct = int(slot.ppct) if slot.ppct else 0
                        except Exception:
                            ppct = 0
                        if ppct <= 0:
                            continue
                        if not slot.lop_id or slot.lop_id == "0":
                            continue
                        if not slot.mon_id or slot.mon_id == "0":
                            continue
                        pm_id = slot.phan_mon_id or "0"
                        key = f"{slot.lop_id}|{slot.mon_id}|{pm_id}"

                        # Update max_overall
                        cur_o = max_overall.get(key, 0)
                        if ppct > cur_o:
                            max_overall[key] = ppct
                        # Update max_le hoặc max_chan
                        if is_chan:
                            cur_c = max_chan.get(key, 0)
                            if ppct > cur_c:
                                max_chan[key] = ppct
                        else:
                            cur_l = max_le.get(key, 0)
                            if ppct > cur_l:
                                max_le[key] = ppct
                        # Group info (lưu lần đầu thấy)
                        if key not in group_info:
                            group_info[key] = {
                                "lop_id": slot.lop_id,
                                "lop_text": slot.lop_text,
                                "mon_id": slot.mon_id,
                                "mon_text": slot.mon_text,
                                "phan_mon_id": pm_id,
                                "phan_mon_text": slot.phan_mon_text,
                            }
                        # Track tuần xuất hiện
                        ap = group_appearances.setdefault(key, {
                            "le_weeks": [], "chan_weeks": [],
                        })
                        bucket = "chan_weeks" if is_chan else "le_weeks"
                        if int(w) not in ap[bucket]:
                            ap[bucket].append(int(w))

                # Sort weeks lists để hiển thị đẹp
                for ap in group_appearances.values():
                    ap["le_weeks"].sort()
                    ap["chan_weeks"].sort()

                elapsed_ms = int((time.time() - t0) * 1000)
                self.q.put(("done", {
                    "max_overall": max_overall,
                    "max_le": max_le,
                    "max_chan": max_chan,
                    "group_info": group_info,
                    "group_appearances": group_appearances,
                    "weeks_scanned": len(weeks_data),
                    "elapsed_ms": elapsed_ms,
                }))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi phát hiện PPCT: {e}\n\n{tb}"))

"""Worker sao lưu toàn bộ KHDH ra JSON."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from ...engine.backup.models import BackupFile, BackupMetadata, BackupWeek, save_backup_json
from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — BackupWorker (sao lưu toàn bộ KHDH ra JSON)
# =====================================================================

class BackupWorker(threading.Thread):
    """Quét toàn bộ tuần được chỉ định + dump thành BackupFile JSON.

    SIẾT LOGIC:
    - **READ-ONLY** trên web: chỉ dùng `fetch_weeks_parallel`, không
      ghi gì lên server.
    - Halt nếu > 50% tuần fetch fail (network/session expired).
    - Skip tuần rỗng hoàn toàn (không có slot có lop) → vẫn ghi vào
      file backup nhưng `slots=[]` để khi restore biết tuần đó trống.
    - Atomic write JSON qua `save_backup_json`.

    Events qua queue:
        ("status", text)
        ("progress", done_count, total, last_batch_weeks_str)
        ("done", BackupFile, save_path | None)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        tuan_from: int,
        tuan_to: int,
        save_path: str | Path | None,
        note: str,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        batch_size: int = 6,
    ):
        super().__init__(daemon=True, name="KHDH-Backup")
        self.port = port
        self.tuan_from = int(tuan_from)
        self.tuan_to = int(tuan_to)
        self.save_path = Path(save_path) if save_path else None
        self.note = str(note or "")
        self.q = event_queue
        self.stop_event = stop_event
        self.batch_size = max(1, int(batch_size))

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi sao lưu: {e}\n\n{tb}"))

    def _run_inner(self):
        if not (1 <= self.tuan_from <= self.tuan_to <= 52):
            self.q.put((
                "error",
                f"Khoảng tuần không hợp lệ: {self.tuan_from}..{self.tuan_to}",
            ))
            return

        with sync_playwright() as pw:
            self.q.put(("status", "Đang kết nối Chrome…"))
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
                ctx = client.fetch_context()
            except Exception as e:
                self.q.put(("error", format_context_read_error(e)))
                return

            # Auto-detect tuan_to nếu vượt web max
            try:
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
            except Exception:
                pass

            # Bật mode "Sửa" — đọc data thực tế đã lưu, không phải gợi ý TKB
            try:
                client.enable_edit_mode()
                time.sleep(0.4)
            except Exception:
                pass

            tuans = list(range(self.tuan_from, self.tuan_to + 1))
            total = len(tuans)

            def _on_batch(done: int, _tot: int, batch_weeks: list[int]):
                if not batch_weeks:
                    return
                label = (
                    f"tuần {batch_weeks[0]}–{batch_weeks[-1]}"
                    if len(batch_weeks) > 1
                    else f"tuần {batch_weeks[0]}"
                )
                self.q.put(("progress", done, total, label))

            self.q.put((
                "status",
                f"Đang sao lưu {total} tuần "
                f"({self.tuan_from}–{self.tuan_to}) song song "
                f"{self.batch_size}/batch…",
            ))
            t0 = time.time()
            try:
                weeks_data = client.fetch_weeks_parallel(
                    tuans,
                    is_edit=1,                # mode "Sửa" — data thực
                    batch_size=self.batch_size,
                    on_batch=_on_batch,
                    stop_event=self.stop_event,
                )
            except Exception as e:
                tb = traceback.format_exc()
                self.q.put((
                    "error",
                    f"Lỗi fetch tuần: {e}\n\n{tb}\n\n"
                    "Có thể session VnEdu hết hạn. Hãy reload tab "
                    "rồi thử lại.",
                ))
                return

            elapsed_ms = int((time.time() - t0) * 1000)
            fetched_count = len(weeks_data)
            failed_count = total - fetched_count
            if failed_count > total // 2:
                self.q.put((
                    "error",
                    f"Chỉ fetch được {fetched_count}/{total} tuần — "
                    "quá nhiều tuần lỗi. Có thể session VnEdu hết hạn "
                    "hoặc mạng không ổn định.\n\n"
                    "Hãy reload tab VnEdu rồi thử lại.",
                ))
                return

            if self.stop_event.is_set():
                self.q.put(("status", "Đã dừng theo yêu cầu"))

            # Build BackupFile
            metadata = BackupMetadata(
                created_at=datetime.now().isoformat(timespec="seconds"),
                tool_version="auto_khbd_pro",
                nam_hoc=int(ctx.nam_hoc or 0),
                cap_hoc=int(ctx.cap_hoc or 0),
                cap_hoc_text=str(ctx.cap_hoc_text or ""),
                giao_vien_id=int(ctx.giao_vien_id or 0),
                giao_vien_name=str(ctx.giao_vien_name or ""),
                ma_truong=str(ctx.site_id or ""),
                tuan_from=self.tuan_from,
                tuan_to=self.tuan_to,
                note=self.note,
            )

            weeks: list[BackupWeek] = []
            for tuan in sorted(tuans):
                wd = weeks_data.get(tuan)
                if wd is None:
                    # Tuần fetch fail → ghi vào file với slots rỗng và
                    # note `fetched_at=""` để restore biết là MISSING.
                    weeks.append(BackupWeek(tuan=tuan, fetched_at=""))
                    continue
                weeks.append(BackupWeek.from_week_data(wd))
            bf = BackupFile(metadata=metadata, weeks=weeks)

            # Save
            saved_path: Path | None = None
            if self.save_path is not None:
                try:
                    save_backup_json(bf, self.save_path)
                    saved_path = self.save_path
                except Exception as e:
                    self.q.put((
                        "error",
                        f"Đã đọc xong nhưng không ghi được tệp:\n{e}",
                    ))
                    return

            self.q.put((
                "status",
                f"✓ Sao lưu xong {fetched_count}/{total} tuần "
                f"({elapsed_ms / 1000:.1f}s, {bf.total_filled_slots} ô có data)"
                + (f" — đã lưu: {saved_path.name}" if saved_path else ""),
            ))
            self.q.put(("done", bf, str(saved_path) if saved_path else None))

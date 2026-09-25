"""Worker chạy PlanExecutor."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

from ...engine.catalog import LessonCatalog
from ...engine.client.client import KHDHClient
from ...engine.executor.errors import (
    format_chrome_connect_error,
    format_context_read_error,
    format_no_vnedu_tab_error,
)
from ...engine.executor.executor import PlanExecutor
from ...engine.fallback_log import FallbackLog
from ...engine.planner import PlanMode, PlanRequest, PlanStrategy, WeekPlanner
from ...engine.profile.json_io import load_profile_auto_json, save_profile_auto_json
from ...engine.profile.profile import KHDHProfile
from .bootstrap import BootstrapWorker


# =====================================================================
# Worker — ExecutorWorker
# =====================================================================

class ExecutorWorker(threading.Thread):
    """Chạy plan (sinh từ profile) lên web KHDH.

    Args:
        port: CDP port
        profile: KHDHProfile đã đầy đủ
        tuan_from / tuan_to: dải tuần
        dry_run: True = không thật, False = ghi vào web
        excel_path: đường dẫn file Excel (để ghi checkpoint vào .auto.json)
        event_queue: nhận events
        stop_event: cancel grace fully
        resume_from: nếu set, chỉ chạy từ tuần này (dùng cho resume)
        catalog: optional catalog cho lookup tên bài

    Events emit qua queue:
        ("status", text)
        ("event", ExecutorEvent từ PlanExecutor)
        ("checkpoint", tuan_done)
        ("done", ExecutorReport)
        ("error", message)
    """

    def __init__(
        self,
        port: int,
        profile: KHDHProfile,
        tuan_from: int,
        tuan_to: int,
        dry_run: bool,
        excel_path: str | Path | None,
        event_queue: queue.Queue,
        stop_event: threading.Event,
        resume_from: int | None = None,
        catalog: LessonCatalog | None = None,
        ten_bai_fallback: bool = False,
    ):
        super().__init__(daemon=True, name="KHDH-Executor")
        self.port = port
        self.profile = profile
        self.tuan_from = tuan_from
        self.tuan_to = tuan_to
        self.dry_run = dry_run
        self.excel_path = Path(excel_path) if excel_path else None
        self.q = event_queue
        self.stop_event = stop_event
        self.resume_from = resume_from
        self.catalog = catalog or LessonCatalog()
        self.ten_bai_fallback = bool(ten_bai_fallback)

    def run(self):
        try:
            # 1. Sinh PlanReport tại thread này (không cần CDP)
            self.q.put(("status", "Đang lập kế hoạch chi tiết…"))
            actual_from = self.resume_from or self.tuan_from
            try:
                planner = WeekPlanner(profile=self.profile, catalog=self.catalog)
                plan = planner.generate_plan(PlanRequest(
                    tuan_from=actual_from,
                    tuan_to=self.tuan_to,
                    strategy=PlanStrategy.EVEN_ODD_TEMPLATES,
                    mode=PlanMode.PREVIEW_APPLY,
                ))
            except Exception as e:
                self.q.put(("error", f"Lỗi lập kế hoạch: {e}"))
                return

            total_ops = sum(len(wp.fill_ops) for wp in plan.week_plans)
            total_weeks = len([wp for wp in plan.week_plans if not wp.skip_reason])
            self.q.put((
                "status",
                f"Sẵn sàng nhập: {total_weeks} tuần, {total_ops} tiết",
            ))

            # 2. Connect CDP
            with sync_playwright() as pw:
                self.q.put(("status", "Đang kết nối Chrome…"))
                try:
                    browser = pw.chromium.connect_over_cdp(
                        f"http://localhost:{self.port}",
                        timeout=5000,  # 5s — fail fast nếu Chrome không phản hồi (W3)
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
                    client.fetch_context()
                except Exception as e:
                    self.q.put(("error", format_context_read_error(e)))
                    return

                # Bật mode "Sửa" (KHÔNG dùng "Sửa (Gợi ý theo TKB)")
                # Lý do: web khóa save khi đang ở mode Gợi ý.
                try:
                    client.enable_edit_mode()
                    time.sleep(0.5)
                except Exception:
                    pass

                # 3. Chạy executor
                checkpoint_callback = self._make_checkpoint_callback()

                def _on_event(ev):
                    """Forward executor event sang queue — error-safe."""
                    try:
                        self.q.put(("event", ev))
                        # Capture week_done để ghi checkpoint
                        # CHỈ checkpoint khi week thật sự SUCCESS (ok=True).
                        # Nếu không, resume sẽ skip qua tuần lỗi → mất data.
                        if (ev.event_type == "week_done"
                                and not self.dry_run
                                and "ok=True" in (ev.message or "")):
                            try:
                                checkpoint_callback(ev.tuan)
                                self.q.put(("checkpoint", ev.tuan))
                            except Exception as cp_e:
                                self.q.put((
                                    "status",
                                    f"⚠ Lỗi ghi checkpoint tuần {ev.tuan}: {cp_e}",
                                ))
                    except Exception as cb_e:
                        # Error boundary: callback failure KHÔNG được crash executor
                        try:
                            self.q.put((
                                "status",
                                f"⚠ Lỗi callback event: {type(cb_e).__name__}: {cb_e}",
                            ))
                        except Exception:
                            pass

                executor = PlanExecutor(
                    client,
                    on_event=_on_event,
                    stop_event=self.stop_event,
                    ten_bai_fallback=self.ten_bai_fallback,
                    fallback_log=FallbackLog(self.excel_path).load(),
                )
                # v2: Forward executor reference vào queue cho UI biết để
                # đọc _last_scan_progress khi refresh PPCT table.
                self.q.put(("executor_ready", executor))

                self.q.put(("status",
                          "🚀 Bắt đầu chạy thử…" if self.dry_run else "🚀 Bắt đầu nhập web…"))
                try:
                    report = executor.execute(plan, dry_run=self.dry_run)
                except Exception as e:
                    tb = traceback.format_exc()
                    self.q.put(("error", f"Lỗi khi chạy: {e}\n\n{tb}"))
                    # Emit partial report — UI vẫn cần biết có bao nhiêu tuần
                    # đã hoàn tất trước khi exception (W1 fix). Nếu executor
                    # đã có report nội bộ trước khi raise, dùng nó; ngược lại
                    # emit empty report để UI cập nhật summary.
                    try:
                        partial = getattr(executor, "_report", None)
                        if partial is not None:
                            self.q.put(("done", partial))
                    except Exception:
                        pass
                    return

                # Cleanup checkpoint nếu chạy hoàn tất bình thường
                if (not self.dry_run and report.completed
                        and self.excel_path and self.excel_path.exists()):
                    try:
                        save_profile_auto_json(
                            self.profile, self.excel_path, checkpoint=None
                        )
                    except Exception as cleanup_e:
                        # Log thay vì silent pass — nếu fail, lần chạy sau
                        # sẽ thấy phantom resume prompt (W2 fix).
                        self.q.put((
                            "status",
                            f"⚠ Không xóa được checkpoint cũ: {cleanup_e}. "
                            "Lần chạy sau có thể hiện banner 'Tiếp tục?'.",
                        ))

                self.q.put(("done", report))
        except Exception as e:
            tb = traceback.format_exc()
            self.q.put(("error", f"Lỗi không lường trước: {e}\n\n{tb}"))

    def _make_checkpoint_callback(self) -> Callable[[int], None]:
        """Trả về callback ghi checkpoint vào .auto.json sau mỗi tuần thành công."""
        if not self.excel_path or not self.excel_path.exists():
            return lambda tuan: None

        # Load completed_weeks từ checkpoint cũ (nếu có) để giữ lịch sử resume.
        # Trước đây list này luôn rỗng → resume sẽ overwrite, mất tracking.
        completed_weeks: list[int] = []
        try:
            existing = load_profile_auto_json(self.excel_path)
            old_cp = existing.get("checkpoint") if existing else None
            if old_cp:
                old_list = old_cp.get("completed_weeks") or []
                completed_weeks = [int(w) for w in old_list if isinstance(w, (int, str))]
        except Exception:
            pass

        def cb(tuan: int):
            if tuan in completed_weeks:
                return
            completed_weeks.append(tuan)
            checkpoint = {
                "last_completed_tuan": tuan,
                "tuan_from": self.tuan_from,
                "tuan_to": self.tuan_to,
                "completed_weeks": list(completed_weeks),
            }
            save_profile_auto_json(self.profile, self.excel_path,
                                  checkpoint=checkpoint)

        return cb

"""PlanExecutor: thực thi kế hoạch điền KHDH từng tuần."""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from ...log import logger
from ..analyzer.hdtn import _classify_hdtn_part, _extract_grade_from_lop_text
from ..catalog import get_hdtn_lesson_title_catalog
from ..client.client import KHDHClient
from ..planner import FillOp
from .dom_write import DomWriteMixin
from .models import ExecutorEvent, ExecutorReport
from .resume import ResumeMixin
from .week_execution import WeekExecutionMixin
from .week_fill import WeekFillMixin
from .week_navigation import WeekNavigationMixin

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..fallback_log import FallbackLog


class PlanExecutor(
    WeekNavigationMixin,
    DomWriteMixin,
    WeekExecutionMixin,
    WeekFillMixin,
    ResumeMixin,
):
    """Thực thi PlanReport trên trang web qua KHDHClient.

    Flow cho mỗi tuần:
        1. (Optional) Đổi tuần trên UI: client.select_week_in_ui(tuan)
        2. Reload table: client.reload_table()
        3. Chờ DOM render (~1s)
        4. (Optional) pre_action: gen_prev / gen_tkb
        5. Apply fill_ops: set field qua DOM (JS)
        6. Click Lưu (ưu tiên qua UI button) hoặc save_week_payload
        7. Verify save errors
        8. Tiếp tục tuần kế tiếp

    Stop event: caller set self.stop_event để dừng giữa chừng.
    """

    def __init__(
        self,
        client: KHDHClient,
        on_event: Callable[[ExecutorEvent], None] | None = None,
        stop_event: threading.Event | None = None,
        resume_path: str | Path | None = None,
        wait_after_pre_action_s: float = 4.0,
        # v3.1: 2.5s đảm bảo web KHDH debounce 1s + getByTiet API + autofill
        # tên bài hoàn tất TRƯỚC khi tool save. Wait ngắn (0.3-0.5s) gây mất
        # tiết do web reset PPCT sau khi tool đã set.
        wait_after_set_fields_s: float = 2.5,
        wait_after_save_s: float = 10.0,
        halt_on_error: bool = True,
        ten_bai_fallback: bool = False,
        fallback_log: "FallbackLog | None" = None,
        fast_safe_mode: bool = False,
        verify_after_save: bool = True,
    ):
        self.client = client
        self.on_event = on_event or (lambda e: None)
        self.stop_event = stop_event or threading.Event()
        self.resume_path = Path(resume_path) if resume_path else None
        self.wait_after_pre_action_s = wait_after_pre_action_s
        self.wait_after_set_fields_s = wait_after_set_fields_s
        self.wait_after_save_s = wait_after_save_s
        self.fast_safe_mode = bool(fast_safe_mode)
        # (#1/#4/#6) Đọc lại web sau mỗi save để KIỂM CHỨNG dữ liệu thật khớp
        # ý định — biến lỗi-im-lặng (server trả success nhưng autofill ghi đè
        # tên bài / blocker over-block) thành lỗi-ồn-ào. Mặc định BẬT cho RUN
        # thật; dry_run tự skip (không có save). Tắt được khi cần tốc độ.
        self.verify_after_save = bool(verify_after_save)
        # Yêu cầu của user: nếu 1 tuần fail (save không thành công, hoặc xác
        # minh tuần thất bại), DỪNG TOÀN BỘ ngay. Người dùng phải tự setup
        # lại + chạy lại từ đầu để tránh lỗi liên tiếp.
        self.halt_on_error = halt_on_error
        # Cờ fallback dấu cách cho ô Tên bài dạy. Lock theo phiên — caller
        # (ExecutorWorker) snapshot từ checkbox UI tại thời điểm bấm CHẠY
        # và truyền vào đây; PlanExecutor không đọc lại UI giữa chừng.
        self.ten_bai_fallback = bool(ten_bai_fallback)
        # Đếm số ô đã dùng fallback trong toàn phiên — emit qua plan_done
        # để UI hiển thị trong tổng kết.
        self._fallback_count: int = 0
        # Log persist các ô đã fallback (kèm theo profile Excel) — null-safe:
        # nếu không có path Excel, executor sẽ skip ghi log nhưng fallback
        # chính vẫn hoạt động.
        self.fallback_log = fallback_log
        self.hdtn_title_catalog = get_hdtn_lesson_title_catalog()
        self._hdtn_catalog_warning_emitted = False
        # Session ID cho lần chạy hiện tại — dùng làm metadata trong log
        # giúp truy vết entry thuộc phiên nào. Format:
        # `<YYYYMMDD-HHMMSS>-<random_4hex>` — 4 hex bytes ngẫu nhiên đảm bảo
        # 2 RUN cùng giây không trùng session_id (rất hiếm nhưng possible).
        self._session_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-" + secrets.token_hex(2)
        )
        # Track per-week các entry vừa ghi vào log để có thể rollback nếu
        # save tuần thất bại. Reset đầu mỗi tuần.
        self._week_pending_log_entries: list[tuple[int, str]] = []
        self._ctx = None  # KHDHContext, set tại execute() để fetch ten_bai
        # Partial report — set khi execute() bắt đầu, đọc bởi caller nếu
        # execute() raise giữa chừng (W1 fix).
        self._report: ExecutorReport | None = None

    def _emit(self, event_type: str, tuan: int = 0, message: str = "", **detail):
        try:
            self.on_event(ExecutorEvent(event_type=event_type, tuan=tuan, message=message, detail=detail))
        except Exception as e:
            logger.warning(f"on_event handler raised: {e}")

    def _fill_hdtn_title_from_word_catalog(self, op: FillOp, tuan: int) -> bool:
        """Điền tên bài HĐTN từ file Word khi web trả rỗng."""
        if not op or op.ten_bai or not op.ppct:
            return False
        try:
            title = self.hdtn_title_catalog.lookup_for_op(op)
        except Exception as e:
            if not self._hdtn_catalog_warning_emitted:
                self._hdtn_catalog_warning_emitted = True
                self._emit(
                    "warning", tuan=tuan,
                    message=f"Kho tên bài HĐTN Word bị lỗi, giữ fallback cũ: {e}",
                )
            return False
        if title:
            op.ten_bai = title
            op.source = op.source or "word"
            op.notes = (op.notes + "; " if op.notes else "") + "Tên bài lấy từ Word HĐTN"
            return True
        if (
            self.hdtn_title_catalog.load_errors
            and not self._hdtn_catalog_warning_emitted
            and _extract_grade_from_lop_text(op.lop_text) in (6, 8)
            and _classify_hdtn_part(op.mon_text, op.phan_mon_text)
        ):
            self._hdtn_catalog_warning_emitted = True
            self._emit(
                "warning", tuan=tuan,
                message=(
                    "Kho tên bài HĐTN Word chưa sẵn sàng: "
                    + "; ".join(self.hdtn_title_catalog.load_errors[:3])
                ),
            )
        return False

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep with stop_event polling (100ms tick).

        Returns True nếu sleep hoàn tất bình thường, False nếu stop_event
        được set giữa chừng (caller nên thoát sớm).
        """
        if seconds <= 0:
            return True
        # Tick 100ms
        ticks = max(1, int(seconds * 10))
        remaining = seconds
        for _ in range(ticks):
            if self.stop_event is not None and self.stop_event.is_set():
                return False
            step = min(0.1, remaining)
            time.sleep(step)
            remaining -= step
            if remaining <= 0:
                break
        return True

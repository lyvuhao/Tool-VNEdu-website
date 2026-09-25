"""Sự kiện và báo cáo của PlanExecutor."""

from __future__ import annotations

from dataclasses import dataclass, field


# ######################################################################
# Section: executor
# ######################################################################






# ---------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------

@dataclass
class ExecutorEvent:
    """1 sự kiện trong quá trình chạy."""
    event_type: str    # "week_start" | "pre_action" | "fill_op" | "save" | "week_done" | "stop" | "error"
    tuan: int = 0
    message: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class WeekResult:
    """Kết quả thực thi 1 tuần."""
    tuan: int
    pre_action: str = ""
    pre_action_ok: bool = True
    pre_action_msg: str = ""
    save_ok: bool = True
    save_msg: str = ""
    save_errors: list = field(default_factory=list)  # list[SaveError]
    duration_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class ExecutorReport:
    """Tổng hợp executor."""
    started_at: float = 0
    finished_at: float = 0
    completed: bool = False
    stopped: bool = False
    week_results: list[WeekResult] = field(default_factory=list)
    error_count: int = 0
    # Số ô đã dùng fallback dấu cách trong phiên này (set bởi PlanExecutor.execute).
    ten_bai_fallback_count: int = 0
    # v2: Tổng số tiết "extra" (dạy bù / chèn lịch / dạy chung) phát hiện
    # trên web trong phiên này. Hiển thị ở UI summary để user biết PPCT đã
    # shift bao nhiêu vì có dạy bù.
    extras_detected_count: int = 0
    # v2: Số tuần được skip vì đã đầy đủ và đúng trên web.
    weeks_already_complete: int = 0

    @property
    def total_duration_ms(self) -> int:
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at) * 1000)
        return 0

    @property
    def successful_weeks(self) -> list[int]:
        return [r.tuan for r in self.week_results if r.save_ok and not r.skipped]

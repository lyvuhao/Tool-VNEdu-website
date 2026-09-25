"""Dataclass cho KHDHClient (context, kết quả API, lỗi lưu)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ######################################################################
# Section: client
# ######################################################################






# ---------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------

@dataclass
class KHDHContext:
    """Context của session KHDH — đọc 1 lần khi bắt đầu, dùng cho mọi request."""
    my_token: str
    my_user_id: str
    nam_hoc: int                 # vd 2025
    cap_hoc: int                 # 1=TH, 2=THCS, 3=THPT
    giao_vien_id: int            # id giáo viên đang đăng nhập
    giao_vien_name: str
    win_id: str                  # id của ExtJS window KHDH (md5 hash)
    ngay_tac_dung_tkb: str       # vd "2026-01-19 00:00:00"
    cap_hoc_text: str = ""       # vd "THCS"
    site_id: str = ""

    @property
    def ngay_tac_dung_date(self) -> str:
        """Trả về dạng yyyy-mm-dd (cắt từ ngay_tac_dung_tkb)."""
        return self.ngay_tac_dung_tkb.split(" ")[0] if self.ngay_tac_dung_tkb else ""


@dataclass
class ApiResult:
    """Kết quả 1 API call."""
    ok: bool
    status: int = 0
    data: Any = None             # parsed JSON / dict / WeekData / str
    raw: str = ""
    error: str = ""
    duration_ms: int = 0


@dataclass
class SaveError:
    """Field-level error trả từ save endpoint."""
    field_id: str                # vd "txtTenBai_2_1_1"
    row_key: str                 # vd "2_1_1"
    message: str


@dataclass
class SaveResult:
    """Result của save 1 tuần."""
    ok: bool
    success: bool                # data.success từ server
    msg: str = ""
    errors: list[SaveError] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

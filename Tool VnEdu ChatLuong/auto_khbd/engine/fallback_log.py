"""Log các ô đã chèn dấu cách thay tên bài (fallback) — dùng bởi executor và worker."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..log import logger


# =====================================================================
# Worker — BootstrapWorker
# =====================================================================

# =====================================================================
# Fallback Log — track các ô đã chèn dấu cách thay tên bài
# =====================================================================
#
# Khi tool dùng fallback dấu cách, ghi entry vào `<excel_path>.fallback.json`.
# Sau này (vd admin cập nhật CSDL phân phối chương trình), user bấm nút
# "Cập nhật Tên bài đã fallback" → tool đọc log, fetch lại tên bài từ API
# `getByTiet`, ghi xuống DOM, lưu tuần. Mỗi entry được verify bằng RAW
# value (chưa trim) trước khi update để tôn trọng sửa thủ công của user.

FALLBACK_LOG_VERSION = 1


def fallback_log_path_for(excel_path) -> Path:
    """Đường dẫn file `.fallback.json` đi kèm 1 file Excel."""
    p = Path(excel_path)
    return p.with_suffix(".fallback.json")


def fallback_global_log_path() -> Path:
    """Đường dẫn log fallback dùng khi người dùng chưa lưu hồ sơ Excel."""
    root = Path(os.environ.get("APPDATA") or Path.home())
    return root / "KHDH_Pro" / "fallback-global" / "fallback.json"


# Regex để validate row_key (ràng buộc cùng `_JS_SET_PPCT_TEN_BAI` định dạng).
# Format: `<thu>_<buoi>_<tiet>` với thu ∈ [2..8], buoi ∈ [1..2], tiet ∈ [1..5].
_FB_ROW_KEY_RE = re.compile(r"^([2-8])_([12])_([1-5])$")


def _is_valid_row_key(row_key: str) -> bool:
    return bool(_FB_ROW_KEY_RE.match(str(row_key or "")))


def _row_key_sort_tuple(row_key: str) -> tuple[int, int, int]:
    """Parse row_key thành (thu, buoi, tiet) để sort. Trả tuple inf nếu invalid."""
    m = _FB_ROW_KEY_RE.match(str(row_key or ""))
    if not m:
        return (99, 99, 99)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


@dataclass
class FallbackEntry:
    """Một ô đã được tool chèn dấu cách thay tên bài."""
    tuan: int
    row_key: str
    lop_id: str
    lop_text: str
    mon_id: str
    mon_text: str
    phan_mon_id: str
    phan_mon_text: str
    ppct: int
    fallback_at: str  # ISO timestamp
    session_id: str = ""
    # Tracking các lần thử update bất thành (vd web vẫn chưa có CSDL).
    last_check_at: str = ""
    last_check_result: str = ""  # "still_no_data" | "user_modified" | "updated" | ""
    retry_count: int = 0

    def key(self) -> tuple[int, str]:
        """Khóa duy nhất 1 entry trong log."""
        return (int(self.tuan), str(self.row_key or ""))

    def to_dict(self) -> dict:
        return {
            "tuan": int(self.tuan),
            "row_key": str(self.row_key or ""),
            "lop_id": str(self.lop_id or ""),
            "lop_text": str(self.lop_text or ""),
            "mon_id": str(self.mon_id or ""),
            "mon_text": str(self.mon_text or ""),
            "phan_mon_id": str(self.phan_mon_id or ""),
            "phan_mon_text": str(self.phan_mon_text or ""),
            "ppct": int(self.ppct or 0),
            "fallback_at": str(self.fallback_at or ""),
            "session_id": str(self.session_id or ""),
            "last_check_at": str(self.last_check_at or ""),
            "last_check_result": str(self.last_check_result or ""),
            "retry_count": int(self.retry_count or 0),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FallbackEntry":
        return cls(
            tuan=int(data.get("tuan", 0) or 0),
            row_key=str(data.get("row_key") or ""),
            lop_id=str(data.get("lop_id") or ""),
            lop_text=str(data.get("lop_text") or ""),
            mon_id=str(data.get("mon_id") or ""),
            mon_text=str(data.get("mon_text") or ""),
            phan_mon_id=str(data.get("phan_mon_id") or ""),
            phan_mon_text=str(data.get("phan_mon_text") or ""),
            ppct=int(data.get("ppct", 0) or 0),
            fallback_at=str(data.get("fallback_at") or ""),
            session_id=str(data.get("session_id") or ""),
            last_check_at=str(data.get("last_check_at") or ""),
            last_check_result=str(data.get("last_check_result") or ""),
            retry_count=int(data.get("retry_count", 0) or 0),
        )


class FallbackLog:
    """Quản lý danh sách entry fallback.

    Nếu có hồ sơ Excel, log nằm cạnh hồ sơ: `<excel>.fallback.json`.
    Nếu chưa lưu hồ sơ, log nằm trong `%APPDATA%\\KHDH_Pro\\fallback-global`.

    Thread-safe ở mức file: mỗi method tự load → modify → save (atomic
    qua write-tmp-rename). Không giữ instance dài lâu — tạo/dùng/bỏ.
    """

    def __init__(self, excel_path: str | Path | None):
        self.excel_path = Path(excel_path) if excel_path else None
        self._entries: list[FallbackEntry] = []
        self._loaded = False

    @property
    def file_path(self) -> Path | None:
        if not self.excel_path:
            return fallback_global_log_path()
        return fallback_log_path_for(self.excel_path)

    def load(self) -> "FallbackLog":
        """Load từ file. An toàn nếu file không tồn tại / hỏng / có entry invalid.

        Khi file CORRUPT (đọc/parse JSON fail), backup nguyên trạng sang
        `.fallback.json.corrupted-{ts}.bak` TRƯỚC KHI trả về entries rỗng.
        Lý do: nếu không backup, lần `save()` kế tiếp sẽ `os.replace` đè
        file gốc → mất toàn bộ lịch sử fallback. User có thể tự inspect
        bản .bak để recover entry quan trọng.
        """
        self._entries = []
        self._loaded = True
        path = self.file_path
        if not path or not path.exists():
            return self
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                return self
            data = json.loads(text)
        except Exception as e:
            # File hỏng — backup nguyên trạng để user có thể recover.
            try:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_path = path.with_suffix(
                    f"{path.suffix}.corrupted-{ts}.bak"
                )
                # Tránh đè bản backup nếu trùng giây (rất hiếm) → đếm thêm
                idx = 1
                while backup_path.exists():
                    backup_path = path.with_suffix(
                        f"{path.suffix}.corrupted-{ts}-{idx}.bak"
                    )
                    idx += 1
                # Dùng `replace` thay vì rename để tương thích Windows khi
                # file đích đã tồn tại (mặc dù đã loop check ở trên).
                os.replace(path, backup_path)
                logger.warning(
                    "FallbackLog: file corrupt (%s) — đã backup sang %s",
                    e, backup_path.name,
                )
            except Exception as e2:
                # Không backup được — log nhưng không crash.
                logger.error(
                    "FallbackLog: file corrupt (%s) và không thể backup: %s",
                    e, e2,
                )
            return self
        items = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return self
        seen: set[tuple[int, str]] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                entry = FallbackEntry.from_dict(raw)
            except Exception:
                continue
            # Validate entry — bỏ qua các entry không hợp lệ để tránh
            # crash worker khi switch tuần / fetch tên bài.
            if not (1 <= int(entry.tuan or 0) <= 52):
                logger.warning(
                    "FallbackLog: bỏ entry tuan=%r ngoài 1..52",
                    entry.tuan,
                )
                continue
            if not _is_valid_row_key(entry.row_key):
                logger.warning(
                    "FallbackLog: bỏ entry row_key=%r không hợp lệ",
                    entry.row_key,
                )
                continue
            if int(entry.ppct or 0) <= 0:
                logger.warning(
                    "FallbackLog: bỏ entry ppct=%r ≤ 0", entry.ppct,
                )
                continue
            if not str(entry.lop_id or "").strip():
                continue
            if not str(entry.mon_id or "").strip():
                continue
            k = entry.key()
            if k in seen:
                continue
            seen.add(k)
            self._entries.append(entry)
        return self

    def save(self) -> None:
        """Ghi atomic ra file.

        Raise (sau khi cleanup tmp) nếu ghi thất bại — KHÔNG nuốt lỗi.
        Lý do (#2 data-path): nếu save fail im lặng, user tưởng đã lưu log
        fallback nhưng thực tế mất → lần "Cập nhật Tên bài đã fallback" sau
        sẽ thiếu ô. Mọi caller đã bọc try/except + emit warning, nên việc
        re-raise cho phép caller phản ứng đúng thay vì âm thầm hỏng.
        """
        path = self.file_path
        if not path:
            return
        payload = {
            "version": FALLBACK_LOG_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "entries": [e.to_dict() for e in self._entries],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception:
            # Cleanup tmp (không xóa file gốc) rồi RE-RAISE để caller biết.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def all_entries(self) -> list[FallbackEntry]:
        if not self._loaded:
            self.load()
        return list(self._entries)

    def count(self) -> int:
        return len(self.all_entries())

    def add(self, entry: FallbackEntry) -> None:
        """Upsert 1 entry (theo key tuan+row_key).

        Khi upsert: GIỮ NGUYÊN các trường tracking từ entry cũ
        (`retry_count`, `last_check_at`, `last_check_result`) và OVERRIDE
        các trường business (lop/mon/pm/ppct, fallback_at, session_id).
        Lý do: lịch sử retry không phải property của lần fallback hiện tại.
        """
        if not self._loaded:
            self.load()
        # Skip silently nếu entry đầu vào không hợp lệ — tránh phá file log.
        if (
            not (1 <= int(entry.tuan or 0) <= 52)
            or not _is_valid_row_key(entry.row_key)
            or int(entry.ppct or 0) <= 0
            or not str(entry.lop_id or "").strip()
            or not str(entry.mon_id or "").strip()
        ):
            logger.warning(
                "FallbackLog.add: bỏ qua entry không hợp lệ "
                "tuan=%r row_key=%r ppct=%r",
                entry.tuan, entry.row_key, entry.ppct,
            )
            return
        k = entry.key()
        for i, e in enumerate(self._entries):
            if e.key() == k:
                # Carry-over tracking metadata
                entry.retry_count = e.retry_count
                entry.last_check_at = e.last_check_at
                entry.last_check_result = e.last_check_result
                self._entries[i] = entry
                return
        self._entries.append(entry)

    def remove(self, tuan: int, row_key: str) -> bool:
        if not self._loaded:
            self.load()
        k = (int(tuan), str(row_key or ""))
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.key() != k]
        return len(self._entries) < before

    def update_check(self, tuan: int, row_key: str, result: str) -> None:
        """Cập nhật last_check_* cho entry (không xóa)."""
        if not self._loaded:
            self.load()
        k = (int(tuan), str(row_key or ""))
        for e in self._entries:
            if e.key() == k:
                e.last_check_at = datetime.now().isoformat(timespec="seconds")
                e.last_check_result = result
                e.retry_count += 1
                return

    def by_week(self) -> dict[int, list[FallbackEntry]]:
        out: dict[int, list[FallbackEntry]] = {}
        for e in self.all_entries():
            out.setdefault(int(e.tuan), []).append(e)
        # Sort entries within week theo (thu, buoi, tiet) parsed từ row_key
        # — không dùng string sort để đúng thứ tự thời gian thực.
        for week_list in out.values():
            week_list.sort(key=lambda x: _row_key_sort_tuple(x.row_key))
        return out

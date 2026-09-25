"""Tự động chụp snapshot trước các thao tác nguy hiểm."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ...log import logger
from .models import BACKUP_FILE_EXT, BackupFile, save_backup_json


# ---------------------------------------------------------------
# SnapshotManager — auto-snapshot trước mọi thao tác nguy hiểm
# ---------------------------------------------------------------
#
# Design:
#   - Mỗi profile Excel có 1 folder snapshots cạnh nó:
#     `<excel_stem>.snapshots/`
#   - Tên file: `snapshot-<YYYYMMDD-HHMMSS>-<reason>.khdh-backup.json`
#   - Cleanup: giữ tối đa 10 snapshot mới nhất, xóa snapshot > 30 ngày.
#   - Default ON: tự snapshot trước RUN/restore. User có thể disable
#     toggle trong UI nếu cần (vd dung lượng đĩa hạn chế).

SNAPSHOT_MAX_KEEP = 10


SNAPSHOT_MAX_AGE_DAYS = 30


SNAPSHOT_DIR_SUFFIX = ".snapshots"


# Global fallback folder khi chưa có hồ sơ Excel.
# Dùng cho user mới chưa lưu profile mà muốn backup/restore web ngay.
# Subfolder theo gv_id + năm học để tách biệt.
def _global_snapshot_root() -> Path:
    """Folder global cho snapshot khi không có hồ sơ Excel.

    Windows: %APPDATA%\\KHDH_Pro\\snapshots-global
    Linux/macOS: ~/.config/khdh_pro/snapshots-global
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "KHDH_Pro" / "snapshots-global"
    return Path.home() / ".config" / "khdh_pro" / "snapshots-global"


# Regex parse tên file snapshot — ghi rõ format để cleanup không xóa
# nhầm file user copy vào đó.
_SNAPSHOT_NAME_RE = re.compile(
    r"^snapshot-(\d{8}-\d{6})-([\w\-]+)" + re.escape(BACKUP_FILE_EXT) + r"$"
)


@dataclass
class SnapshotInfo:
    """Metadata 1 snapshot (lazy — không load full BackupFile)."""
    path: Path
    created_at: datetime
    reason: str
    size_bytes: int = 0
    note: str = ""           # Đọc lazy từ metadata khi cần


class SnapshotManager:
    """Quản lý folder snapshot.

    Mặc định lưu cạnh hồ sơ Excel: `<excel_stem>.snapshots/`.
    Nếu chưa có hồ sơ Excel → fallback về folder global
    `%APPDATA%\\KHDH_Pro\\snapshots-global\\<gv_id>-<nam_hoc>\\` để
    user vẫn dùng được backup/restore khi chưa lưu profile.

    Thread-safe ở mức file (atomic write). Không giữ state lâu —
    tạo/dùng/bỏ.
    """

    def __init__(
        self, excel_path: str | Path | None,
        gv_id: int = 0, nam_hoc: int = 0,
    ):
        self.excel_path = Path(excel_path) if excel_path else None
        # Dùng cho fallback global khi excel_path is None.
        # Caller (RestoreWorker / SmartRepairWorker) pass thêm gv_id +
        # nam_hoc từ ctx để folder global không lẫn lộn nhiều GV/năm.
        self.gv_id = int(gv_id or 0)
        self.nam_hoc = int(nam_hoc or 0)

    @property
    def is_global_fallback(self) -> bool:
        """True khi đang dùng folder global thay vì cạnh Excel."""
        return self.excel_path is None

    @property
    def snapshots_dir(self) -> Path | None:
        if self.excel_path is not None:
            return self.excel_path.parent / (
                self.excel_path.stem + SNAPSHOT_DIR_SUFFIX
            )
        # Fallback global
        if self.gv_id <= 0:
            return None  # Không xác định GV → không dám lưu
        sub = f"{self.gv_id}-{self.nam_hoc or 'unknown'}"
        return _global_snapshot_root() / sub

    def ensure_dir(self) -> Path | None:
        d = self.snapshots_dir
        if d is None:
            return None
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"SnapshotManager: cannot mkdir {d}: {e}")
            return None
        return d

    def make_snapshot_path(self, reason: str) -> Path | None:
        """Sinh path mới với timestamp. reason chỉ chứa [a-zA-Z0-9_-]."""
        d = self.ensure_dir()
        if d is None:
            return None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Sanitize reason: chỉ giữ word chars + dash
        safe_reason = re.sub(r"[^\w\-]+", "_", reason or "manual")[:30]
        if not safe_reason:
            safe_reason = "manual"
        return d / f"snapshot-{ts}-{safe_reason}{BACKUP_FILE_EXT}"

    def save_snapshot(self, bf: BackupFile, reason: str) -> Path | None:
        """Save BackupFile thành snapshot. Trả path đã save hoặc None."""
        path = self.make_snapshot_path(reason)
        if path is None:
            return None
        try:
            save_backup_json(bf, path)
        except Exception as e:
            logger.warning(f"SnapshotManager.save_snapshot: {e}")
            return None
        # Cleanup sau khi save thành công
        try:
            self.cleanup()
        except Exception as e:
            logger.warning(f"SnapshotManager.cleanup: {e}")
        return path

    def list_snapshots(self) -> list[SnapshotInfo]:
        """Liệt kê snapshot, mới nhất đầu. Filter theo regex tên file."""
        d = self.snapshots_dir
        if d is None or not d.exists():
            return []
        out: list[SnapshotInfo] = []
        try:
            for p in d.iterdir():
                if not p.is_file():
                    continue
                m = _SNAPSHOT_NAME_RE.match(p.name)
                if not m:
                    continue
                ts_str, reason = m.group(1), m.group(2)
                try:
                    created = datetime.strptime(ts_str, "%Y%m%d-%H%M%S")
                except ValueError:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                out.append(SnapshotInfo(
                    path=p, created_at=created, reason=reason,
                    size_bytes=size,
                ))
        except Exception as e:
            logger.warning(f"SnapshotManager.list_snapshots: {e}")
            return []
        out.sort(key=lambda x: x.created_at, reverse=True)
        return out

    def load_note(self, info: SnapshotInfo) -> str:
        """Lazy load note từ metadata của snapshot (không load full)."""
        try:
            text = info.path.read_text(encoding="utf-8")
            data = json.loads(text)
            md = data.get("metadata") or {}
            return str(md.get("note") or "")
        except Exception:
            return ""

    def cleanup(
        self,
        max_keep: int = SNAPSHOT_MAX_KEEP,
        max_age_days: int = SNAPSHOT_MAX_AGE_DAYS,
    ) -> int:
        """Xóa snapshot cũ. Trả số file đã xóa.

        Logic 2 lớp:
          - Xóa mọi snapshot > max_age_days.
          - Trong số còn lại, giữ max_keep mới nhất, xóa phần dư.
        """
        snaps = self.list_snapshots()
        if not snaps:
            return 0
        cutoff = datetime.now() - timedelta(days=max_age_days)
        deleted = 0
        # 1. Xóa quá hạn
        keep: list[SnapshotInfo] = []
        for s in snaps:
            if s.created_at < cutoff:
                try:
                    s.path.unlink()
                    deleted += 1
                except Exception:
                    keep.append(s)
            else:
                keep.append(s)
        # 2. Trong số còn lại, giữ max_keep mới nhất
        if len(keep) > max_keep:
            for s in keep[max_keep:]:
                try:
                    s.path.unlink()
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def total_size_bytes(self) -> int:
        return sum(s.size_bytes for s in self.list_snapshots())

"""Định dạng file sao lưu KHDH toàn năm."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..parser import SlotData, WeekData


# ######################################################################
# Section: backup (sao lưu / khôi phục KHDH toàn năm)
# ######################################################################
#
# Mục đích: Cho phép giáo viên dump toàn bộ data KHDH (1..36 tuần) ra
# 1 file JSON, sau này có thể import lại để khôi phục đúng nguyên bản —
# kể cả các trạng thái non-BT (Dạy bù / Chèn lịch / Dạy chung...).
#
# Format JSON là canonical: serialize/deserialize round-trip 100% chính
# xác. Mỗi BackupSlot giữ ID gốc (lop_id, mon_id, phan_mon_id) + text
# label (chỉ để user đọc, restore vẫn dùng ID).
#
# Anti-corruption: validate version + kind + schema khi load. File từ
# năm học khác hoặc cấu trúc lạ → reject ngay với message rõ ràng.

BACKUP_FORMAT_VERSION = 1


BACKUP_FILE_KIND = "khdh-backup"


BACKUP_FILE_EXT = ".khdh-backup.json"


# Mapping value → text để cross-check khi load file cũ (tránh corrupt do
# user edit JSON tay đặt trang_thai sai). Phải khớp với enum trong section
# analyzer.
_TT_VALUE_TO_TEXT_CANONICAL = {
    "-1": "---",
    "0": "Bình thường",
    "1": "Nghỉ",
    "2": "Dạy thay",
    "3": "Dạy bù",
    "4": "Chèn lịch",
    "5": "Dạy chung",
    "6": "Phụ đạo",
    "7": "Bồi dưỡng",
    "8": "Dạy thêm",
}


@dataclass
class BackupSlot:
    """1 ô slot trong backup — đầy đủ field cần để restore.

    Chỉ giữ slot có lop (`lop_id != "0"`). Slot trống không cần backup.
    """
    row_key: str           # "thu_buoi_tiet"
    thu: int
    buoi_idx: int
    tiet_idx: int
    tiet_tkb: str = ""

    lop_id: str = ""
    lop_text: str = ""
    khoi: str = ""

    mon_id: str = ""
    mon_text: str = ""
    mon_dmonid: str = ""

    phan_mon_id: str = ""
    phan_mon_text: str = ""

    ppct: str = ""         # giữ string để không bị Excel-format
    ten_bai: str = ""
    ghi_chu: str = ""

    trang_thai: str = ""
    trang_thai_text: str = ""

    id_giao_an: str = ""

    @classmethod
    def from_slot_data(cls, s: SlotData) -> "BackupSlot":
        """Convert SlotData (parser output) → BackupSlot."""
        return cls(
            row_key=s.row_key,
            thu=s.thu,
            buoi_idx=s.buoi_idx,
            tiet_idx=s.tiet_idx,
            tiet_tkb=s.tiet_tkb or "",
            lop_id=s.lop_id or "",
            lop_text=s.lop_text or "",
            khoi=s.khoi or "",
            mon_id=s.mon_id or "",
            mon_text=s.mon_text or "",
            mon_dmonid=s.mon_dmonid or "",
            phan_mon_id=s.phan_mon_id or "",
            phan_mon_text=s.phan_mon_text or "",
            ppct=str(s.ppct or ""),
            ten_bai=s.ten_bai or "",
            ghi_chu=s.ghi_chu or "",
            trang_thai=s.trang_thai or "",
            trang_thai_text=s.trang_thai_text or "",
            id_giao_an=s.id_giao_an or "",
        )

    def to_dict(self) -> dict:
        return {
            "row_key": self.row_key,
            "thu": int(self.thu),
            "buoi_idx": int(self.buoi_idx),
            "tiet_idx": int(self.tiet_idx),
            "tiet_tkb": self.tiet_tkb,
            "lop_id": self.lop_id,
            "lop_text": self.lop_text,
            "khoi": self.khoi,
            "mon_id": self.mon_id,
            "mon_text": self.mon_text,
            "mon_dmonid": self.mon_dmonid,
            "phan_mon_id": self.phan_mon_id,
            "phan_mon_text": self.phan_mon_text,
            "ppct": self.ppct,
            "ten_bai": self.ten_bai,
            "ghi_chu": self.ghi_chu,
            "trang_thai": self.trang_thai,
            "trang_thai_text": self.trang_thai_text,
            "id_giao_an": self.id_giao_an,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BackupSlot":
        # Defensive coerce — file có thể bị edit tay
        return cls(
            row_key=str(d.get("row_key") or ""),
            thu=int(d.get("thu") or 0),
            buoi_idx=int(d.get("buoi_idx") or 0),
            tiet_idx=int(d.get("tiet_idx") or 0),
            tiet_tkb=str(d.get("tiet_tkb") or ""),
            lop_id=str(d.get("lop_id") or ""),
            lop_text=str(d.get("lop_text") or ""),
            khoi=str(d.get("khoi") or ""),
            mon_id=str(d.get("mon_id") or ""),
            mon_text=str(d.get("mon_text") or ""),
            mon_dmonid=str(d.get("mon_dmonid") or ""),
            phan_mon_id=str(d.get("phan_mon_id") or ""),
            phan_mon_text=str(d.get("phan_mon_text") or ""),
            ppct=str(d.get("ppct") or ""),
            ten_bai=str(d.get("ten_bai") or ""),
            ghi_chu=str(d.get("ghi_chu") or ""),
            trang_thai=str(d.get("trang_thai") or ""),
            trang_thai_text=str(d.get("trang_thai_text") or ""),
            id_giao_an=str(d.get("id_giao_an") or ""),
        )

    @property
    def has_lop(self) -> bool:
        return bool(self.lop_id) and self.lop_id != "0"

    @property
    def is_filled(self) -> bool:
        """Có lop + ppct + tên bài (để verify restore thành công)."""
        try:
            ppct_int = int(self.ppct or 0)
        except (TypeError, ValueError):
            ppct_int = 0
        return self.has_lop and ppct_int > 0 and bool(self.ten_bai)


@dataclass
class BackupWeek:
    """Data 1 tuần trong backup. Chỉ chứa slot có lop_id."""
    tuan: int
    fetched_at: str = ""
    slots: list[BackupSlot] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tuan": int(self.tuan),
            "fetched_at": self.fetched_at,
            "slots": [s.to_dict() for s in self.slots],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BackupWeek":
        return cls(
            tuan=int(d.get("tuan") or 0),
            fetched_at=str(d.get("fetched_at") or ""),
            slots=[BackupSlot.from_dict(x) for x in (d.get("slots") or [])],
        )

    @classmethod
    def from_week_data(cls, wd: WeekData) -> "BackupWeek":
        """Convert WeekData (parser) → BackupWeek. Chỉ giữ slot có lop."""
        slots = [
            BackupSlot.from_slot_data(s)
            for s in wd.slots
            if s.has_lop
        ]
        slots.sort(key=lambda s: (s.thu, s.buoi_idx, s.tiet_idx))
        return cls(
            tuan=int(wd.tuan),
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            slots=slots,
        )

    @property
    def filled_count(self) -> int:
        return sum(1 for s in self.slots if s.is_filled)


@dataclass
class BackupMetadata:
    """Metadata header — dùng để validate khi load."""
    created_at: str = ""
    tool_version: str = "auto_khbd_pro"
    nam_hoc: int = 0
    cap_hoc: int = 0
    cap_hoc_text: str = ""
    giao_vien_id: int = 0
    giao_vien_name: str = ""
    ma_truong: str = ""
    tuan_from: int = 1
    tuan_to: int = 36
    note: str = ""           # Ghi chú do user nhập (vd "Trước khi sửa T31")

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at,
            "tool_version": self.tool_version,
            "nam_hoc": int(self.nam_hoc),
            "cap_hoc": int(self.cap_hoc),
            "cap_hoc_text": self.cap_hoc_text,
            "giao_vien_id": int(self.giao_vien_id),
            "giao_vien_name": self.giao_vien_name,
            "ma_truong": self.ma_truong,
            "tuan_from": int(self.tuan_from),
            "tuan_to": int(self.tuan_to),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BackupMetadata":
        return cls(
            created_at=str(d.get("created_at") or ""),
            tool_version=str(d.get("tool_version") or ""),
            nam_hoc=int(d.get("nam_hoc") or 0),
            cap_hoc=int(d.get("cap_hoc") or 0),
            cap_hoc_text=str(d.get("cap_hoc_text") or ""),
            giao_vien_id=int(d.get("giao_vien_id") or 0),
            giao_vien_name=str(d.get("giao_vien_name") or ""),
            ma_truong=str(d.get("ma_truong") or ""),
            tuan_from=int(d.get("tuan_from") or 1),
            tuan_to=int(d.get("tuan_to") or 36),
            note=str(d.get("note") or ""),
        )


@dataclass
class BackupFile:
    """Toàn bộ file backup. Top-level container."""
    metadata: BackupMetadata = field(default_factory=BackupMetadata)
    weeks: list[BackupWeek] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": BACKUP_FORMAT_VERSION,
            "kind": BACKUP_FILE_KIND,
            "metadata": self.metadata.to_dict(),
            "weeks": [w.to_dict() for w in self.weeks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BackupFile":
        return cls(
            metadata=BackupMetadata.from_dict(d.get("metadata") or {}),
            weeks=[BackupWeek.from_dict(w) for w in (d.get("weeks") or [])],
        )

    @property
    def total_filled_slots(self) -> int:
        return sum(w.filled_count for w in self.weeks)

    @property
    def total_slots(self) -> int:
        return sum(len(w.slots) for w in self.weeks)

    def week_by_num(self, tuan: int) -> "BackupWeek | None":
        for w in self.weeks:
            if int(w.tuan) == int(tuan):
                return w
        return None


class BackupSchemaError(Exception):
    """File backup không hợp lệ — schema sai hoặc cấu trúc lạ."""


def save_backup_json(bf: BackupFile, path: str | Path) -> None:
    """Atomic write: tmp → replace. Tránh corrupt nếu crash giữa chừng.

    JSON có indent=2 để diff dễ; file 36 tuần × 25 slots ~ 200KB.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = bf.to_dict()
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        os.replace(tmp, p)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def load_backup_json(path: str | Path) -> BackupFile:
    """Đọc + validate. Raise BackupSchemaError nếu không hợp lệ."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        raise BackupSchemaError(f"Không đọc được file: {e}")
    try:
        data = json.loads(text)
    except Exception as e:
        raise BackupSchemaError(
            f"File không phải JSON hợp lệ: {e}\n"
            "Có thể file đã bị hỏng hoặc bị edit tay sai cú pháp."
        )
    if not isinstance(data, dict):
        raise BackupSchemaError("File không phải dict JSON.")
    kind = str(data.get("kind") or "")
    if kind != BACKUP_FILE_KIND:
        raise BackupSchemaError(
            f"File không phải backup KHDH (kind='{kind}', "
            f"phải là '{BACKUP_FILE_KIND}')."
        )
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise BackupSchemaError(
            f"Version không hợp lệ: {version!r}. Phải là số ≥ 1."
        )
    if version > BACKUP_FORMAT_VERSION:
        # Forward-compat: chấp nhận file v2/v3 nhưng warning ở caller
        pass
    return BackupFile.from_dict(data)

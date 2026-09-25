"""Lưu/nạp cấu hình."""

import json
import os

from ..cdp.config import DEFAULT_CDP_PORT
from ..config import CONFIG_FILE, SCHEDULE_DAYS, SCHEDULE_MODE_MANUAL
from ..paths import TOOL_DIR


class SettingsMixin:
    """Lưu/nạp cấu hình."""

    # -----------------------------------------------------------------
    # CONFIG PERSISTENCE
    # -----------------------------------------------------------------

    def _save_config(self):
        """Lưu toàn bộ config ra JSON."""
        try:
            config = {
                "is_compact": self.is_compact,
                # Schedule (Lịch dạy) settings
                "sched_tuan_from": self.var_sched_tuan_from.get(),
                "sched_tuan_to": self.var_sched_tuan_to.get(),
                "sched_lop": self.var_sched_lop.get(),
                "sched_lop_multi": self.var_sched_lop_multi.get(),
                "sched_mode": self._get_sched_mode(),
                "sched_buoi": {str(k): v.get() for k, v in self._sched_buoi.items()},
                "sched_grid": {
                    f"{thu}_{buoi}": [v.get() for v in vars_list]
                    for (thu, buoi), vars_list in self._sched_grid.items()
                },
                # Schedule CDP fields (Phase 5)
                "sched_phan_mon": self.var_sched_phan_mon.get(),
                "sched_mon_hoc": self.var_sched_mon_hoc.get(),
                "sched_ppct_start": self.var_sched_ppct_start.get(),
                "sched_hs_nghi": self.var_sched_hs_nghi.get(),
                "sched_diem": self.var_sched_diem.get(),
                "sched_nhan_xet": self.var_sched_nhan_xet.get(),
                "sched_teacher_progress_fast_mode": bool(self.var_sched_teacher_progress_fast_mode.get()),
                "vnedu_username": self.var_vnedu_username.get(),
                "stats_tuan_from": self.var_stats_tuan_from.get(),
                "stats_tuan_to": self.var_stats_tuan_to.get(),
                "stats_mon_hoc": self.var_stats_mon_hoc.get(),
            }

            config_path = os.path.join(str(TOOL_DIR), CONFIG_FILE)
            self._write_json_atomic(config_path, config)
            if self._schedule_resume_dirty:
                self._persist_schedule_resume_snapshot()
            self._save_class_stats_cache_to_disk()

        except Exception as e:
            self._log(f"Lỗi save config: {e}", "error")

    def _load_config(self):
        """Đọc config từ JSON, áp dụng vào UI."""
        config_path = os.path.join(str(TOOL_DIR), CONFIG_FILE)
        if not os.path.exists(config_path):
            self._log("Chưa có config — dùng mặc định", "info")
            self._load_class_stats_cache_from_disk()
            self._load_schedule_resume_snapshot()
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            # CDP port được khóa cứng để tránh lệch port giữa các lần chạy.
            self.var_cdp_port.set(DEFAULT_CDP_PORT)

            # Schedule (Lịch dạy) settings
            self.var_sched_tuan_from.set(cfg.get("sched_tuan_from", 1))
            self.var_sched_tuan_to.set(cfg.get("sched_tuan_to", 1))
            saved_sched_lop = cfg.get("sched_lop", "")
            if saved_sched_lop:
                self.var_sched_lop.set(saved_sched_lop)
            self.var_sched_lop_multi.set(cfg.get("sched_lop_multi", ""))
            self.var_sched_mode.set(
                cfg.get("sched_mode", SCHEDULE_MODE_MANUAL) or SCHEDULE_MODE_MANUAL
            )

            # Khôi phục buổi cho mỗi thứ
            saved_buoi = cfg.get("sched_buoi", {})
            for k_str, buoi_val in saved_buoi.items():
                try:
                    thu_key = int(k_str)
                    if thu_key in self._sched_buoi:
                        self._sched_buoi[thu_key].set(buoi_val)
                except (ValueError, KeyError):
                    pass

            # Khôi phục checkbox grid
            saved_grid = cfg.get("sched_grid", {})
            for key_str, bool_list in saved_grid.items():
                try:
                    parts = key_str.split("_")
                    thu_key = int(parts[0])
                    buoi_key = parts[1]
                    grid_key = (thu_key, buoi_key)
                    if grid_key in self._sched_grid:
                        for i, val in enumerate(bool_list):
                            if i < len(self._sched_grid[grid_key]):
                                self._sched_grid[grid_key][i].set(bool(val))
                except (ValueError, IndexError, KeyError):
                    pass

            # Trigger buổi change callback để sync checkboxes
            for thu in SCHEDULE_DAYS:
                self._on_sched_buoi_changed(thu)

            # Schedule CDP fields (Phase 5)
            saved_phan_mon = cfg.get("sched_phan_mon", "")
            if saved_phan_mon:
                self.var_sched_phan_mon.set(saved_phan_mon)
            saved_mon_hoc = cfg.get("sched_mon_hoc", "")
            if saved_mon_hoc:
                self.var_sched_mon_hoc.set(saved_mon_hoc)
            self.var_sched_ppct_start.set(cfg.get("sched_ppct_start", 1))
            self.var_sched_hs_nghi.set(cfg.get("sched_hs_nghi", "0"))
            self.var_sched_diem.set(cfg.get("sched_diem", "10"))
            self.var_sched_nhan_xet.set(
                cfg.get("sched_nhan_xet", "Lớp học chăm ngoan"))
            self.var_sched_teacher_progress_fast_mode.set(
                bool(cfg.get("sched_teacher_progress_fast_mode", False))
            )
            self.var_vnedu_username.set(cfg.get("vnedu_username", ""))
            self.var_stats_tuan_from.set(cfg.get("stats_tuan_from", 1))
            self.var_stats_tuan_to.set(cfg.get("stats_tuan_to", 1))
            self.var_stats_mon_hoc.set(cfg.get("stats_mon_hoc", ""))

            # Compact state
            if cfg.get("is_compact", False):
                self._toggle_compact()

            self._load_class_stats_cache_from_disk()
            self._load_schedule_resume_snapshot()
            self._log("Đã tải config", "success")

        except Exception as e:
            self._log(f"Lỗi load config: {e}", "warning")
            self._load_class_stats_cache_from_disk()
            self._load_schedule_resume_snapshot()

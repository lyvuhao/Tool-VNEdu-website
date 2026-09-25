"""Lưu/nạp cấu hình."""

from __future__ import annotations

from .. import config as app_config
from ..storage import _load_json_object_file, _write_json_atomic_file


class SettingsMixin:
    """Lưu/nạp cấu hình."""

    def _save_config(self) -> None:
        """Lưu config ra file JSON theo atomic write pattern (IMP-A1)."""
        payload = {
            "debug_port": self.port_var.get().strip(),
            "username": self.username_var.get().strip(),
            "show_password": bool(self.show_password_var.get()),
            "feature_name": self.feature_name_var.get().strip(),
            "auto_scan_rows": bool(self.auto_scan_rows_var.get()),
            "auto_save_scores": bool(self.auto_save_scores_var.get()),
            "cdp_url": self.url_var.get().strip(),  # IMP-C6: Lưu CDP URL vào config
        }
        _write_json_atomic_file(app_config.CONFIG_FILE, payload)

    def _load_config(self) -> None:
        if not app_config.CONFIG_FILE.exists():
            return
        payload, backup_path, error = _load_json_object_file(app_config.CONFIG_FILE)
        if error is not None:
            self._log(f"Không tải được cấu hình standalone: {error}")
            if backup_path is not None:
                self._log(f"Đã chuyển file cấu hình lỗi sang {backup_path}")
            return
        self.port_var.set(str(payload.get("debug_port", self.port_var.get())))
        self.username_var.set(str(payload.get("username", self.username_var.get())))
        self.show_password_var.set(bool(payload.get("show_password", self.show_password_var.get())))
        self.feature_name_var.set(str(payload.get("feature_name", self.feature_name_var.get())))
        self.auto_save_scores_var.set(bool(payload.get("auto_save_scores", self.auto_save_scores_var.get())))
        # IMP-C6: Khôi phục CDP URL từ config (nếu có)
        saved_url = str(payload.get("cdp_url", "")).strip()
        # SECURITY: Validate URL scheme khi load từ config
        if saved_url and saved_url.lower().startswith(("http://", "https://")):
            self.url_var.set(saved_url)
        self._apply_password_visibility()

"""Lưu/nạp cấu hình và mở trang web."""

from __future__ import annotations

import json
from tkinter import messagebox
from typing import List

from ..config import CONFIG_FILE
from ..models import CommentRule


class ConfigMixin:
    """Lưu/nạp cấu hình và mở trang web."""

    def _save_config(self) -> None:
        """Persists session fields together with the current rule preferences."""
        payload = {
            "debug_port": self.port_var.get().strip(),
            "target_url": self.url_var.get().strip(),
            "username": self.username_var.get().strip(),
            "num_forms": self.num_forms_var.get().strip(),
            "auto_save": bool(self.auto_save_var.get()),
            "allow_comment_overwrite": bool(self.allow_comment_overwrite_var.get()),
            "show_password": bool(self.show_password_var.get()),
            "preferred_score_source_key": self._selected_option_id(
                self.score_source_var,
                self.score_source_label_to_key,
            ) or self.preferred_score_source_key,
            "rules": [
                {
                    "condition": form["condition"].get().strip(),
                    "template": form["template"].get().strip(),
                }
                for form in self.score_forms
                if form["condition"].get().strip() or form["template"].get().strip()
            ],
        }
        with CONFIG_FILE.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _load_config(self) -> None:
        """Loads session fields and restores the last saved rule form layout."""
        default_count = 7
        default_rules = self._seed_default_rules(default_count)
        if not CONFIG_FILE.exists():
            self.num_forms_var.set(str(default_count))
            self._build_rule_forms(default_count, initial_rules=default_rules)
            return
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as error:  # noqa: BLE001 - config corruption should not stop app startup
            self._log(f"Không tải được cấu hình V2: {error}")
            self.num_forms_var.set(str(default_count))
            self._build_rule_forms(default_count, initial_rules=default_rules)
            return

        self.port_var.set(str(payload.get("debug_port", self.port_var.get())))
        self.url_var.set(str(payload.get("target_url", self.url_var.get())))
        self.username_var.set(str(payload.get("username", self.username_var.get())))
        self.auto_save_var.set(bool(payload.get("auto_save", self.auto_save_var.get())))
        self.allow_comment_overwrite_var.set(
            bool(payload.get("allow_comment_overwrite", self.allow_comment_overwrite_var.get()))
        )
        self.show_password_var.set(bool(payload.get("show_password", self.show_password_var.get())))
        self._apply_password_visibility()
        self.preferred_score_source_key = str(payload.get("preferred_score_source_key", "")).strip()

        try:
            rule_count = int(str(payload.get("num_forms", self.num_forms_var.get())).strip())
        except ValueError:
            rule_count = len(payload.get("rules", payload.get("forms", []))) or default_count
        rule_count = min(max(rule_count, 1), 20)
        self.num_forms_var.set(str(rule_count))

        raw_rules = payload.get("rules", payload.get("forms", []))
        loaded_rules: List[CommentRule] = []
        if isinstance(raw_rules, list):
            for item in raw_rules:
                if not isinstance(item, dict):
                    continue
                loaded_rules.append(
                    CommentRule(
                        condition=str(item.get("condition", "")).strip(),
                        template=str(item.get("template", "")).strip(),
                    )
                )
        self._build_rule_forms(rule_count, initial_rules=(loaded_rules or default_rules[:rule_count]))

    def on_save_config(self) -> None:
        """Saves the current CDP/session shell fields to disk."""
        try:
            self._save_config()
        except Exception as error:  # noqa: BLE001 - user-facing save command
            messagebox.showerror("Lỗi lưu", str(error))
            self._log(f"Lỗi lưu cấu hình: {error}")
            return
        messagebox.showinfo("Đã lưu", f"Đã lưu cấu hình vào {CONFIG_FILE}")
        self._log(f"Đã lưu cấu hình vào {CONFIG_FILE}")

    def on_open_web(self) -> None:
        """Opens the VNEDU target URL in the connected browser session."""
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001 - validation path
            messagebox.showerror("Lỗi mở web", str(error))
            self._log(f"Lỗi mở web: {error}")
            return

        self._run_background_task(
            "Đang mở VNEDU qua live Chrome...",
            worker=lambda progress: automation.open_target_page(progress_callback=progress),
            on_success=lambda result: (
                self._set_status_text(f"Đã mở trang: {result}"),
                self._log(f"Đã mở VNEDU: {result}"),
            ),
            on_error=lambda error: (
                messagebox.showerror("Lỗi mở web", str(error)),
                self._log(f"Lỗi mở web: {error}"),
                self._set_status_text("Lỗi mở VNEDU"),
            ),
        )

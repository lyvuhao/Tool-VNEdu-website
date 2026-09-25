"""Quản lý form rule nhận xét."""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Dict, List

from ..config import CONFIG_FILE, DEFAULT_RULE_EXPORT_DIR
from ..models import CommentRule, CommentWriteRow
from ..rules import compile_comment_rules, describe_condition, parse_condition


class RulesMixin:
    """Quản lý form rule nhận xét."""

    def _seed_default_rules(self, count: int) -> List[CommentRule]:
        """Returns a default rule template set for quick first use."""
        defaults = [
            CommentRule(">=8", "Hoàn thành tốt yêu cầu cần đạt của bộ môn, chủ động, tự giác trong học tập và rèn luyện."),
            CommentRule("6.5-7.9", "Hoàn thành khá tốt nội dung kiến thức đã học, vận dụng được vào bài thực hành, chăm chỉ trong học tập."),
            CommentRule("6-6.4", "Tiếp thu được các kiến thức cơ bản của môn học, có ý thức tự giác, tương đối chủ động trong học tập."),
            CommentRule("5-5.9", "Hoàn thành được các yêu cầu của bộ môn, chủ động hơn trong học tập, tăng cường rèn luyện kỹ năng giải bài tập."),
            CommentRule("<5", "Chưa hoàn thành các yêu cầu cần đạt của bộ môn, còn thụ động, tăng cường luyện tập kỹ năng thực hành."),
            CommentRule("Đ", "Hoàn thành tốt yêu cầu cần đạt của bộ môn, chủ động, tự giác trong học tập và rèn luyện."),
            CommentRule("CĐ", "Chưa hoàn thành tốt nội dung kiến thức môn học."),
        ]
        return defaults[:count]

    def _sanitize_rule_filename_part(self, value: str) -> str:
        """Converts one rule label into a Windows-safe filename fragment."""
        sanitized = value.strip()
        replacements = (
            (">=", "ge_"),
            ("<=", "le_"),
            (">", "gt_"),
            ("<", "lt_"),
            ("=", "eq_"),
        )
        for old, new in replacements:
            sanitized = sanitized.replace(old, new)
        sanitized = re.sub(r'[<>:"/\\\\|?*]+', "_", sanitized)
        sanitized = re.sub(r"\s+", "_", sanitized)
        sanitized = sanitized.strip("._")
        return sanitized or "rule"

    def _write_default_rule_files(self, target_dir: Path | None = None) -> List[Path]:
        """Writes the built-in default rule set into seven plain-text files."""
        export_dir = target_dir or DEFAULT_RULE_EXPORT_DIR
        export_dir.mkdir(parents=True, exist_ok=True)

        rules = self._seed_default_rules(7)
        created_files: List[Path] = []
        for index, rule in enumerate(rules, start=1):
            filename = f"{index:02d}_{self._sanitize_rule_filename_part(rule.condition)}.txt"
            file_path = export_dir / filename
            content = (
                f"STT: {index}\n"
                f"Điều kiện: {rule.condition}\n"
                "Mẫu nhận xét:\n"
                f"{rule.template}\n"
            )
            file_path.write_text(content, encoding="utf-8")
            created_files.append(file_path)
        return created_files

    def _build_rule_forms(self, count: int, initial_rules: List[CommentRule] | None = None) -> None:
        """Rebuilds the rule form list inside the scrollable rule panel."""
        if self._rule_busy_widgets:
            self._busy_widgets = [
                item for item in self._busy_widgets if item[0] not in self._rule_busy_widgets
            ]
            self._rule_busy_widgets = []
        for widget in self.rule_form_container.winfo_children():
            widget.destroy()
        self.score_forms = []

        header = ttk.Frame(self.rule_form_container)
        header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(header, text="STT", width=6).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(header, text="Điều kiện", width=18).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(header, text="Mẫu nhận xét", anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        seeded_rules = list(initial_rules or self._seed_default_rules(count))
        for index in range(count):
            row_frame = ttk.Frame(self.rule_form_container)
            row_frame.pack(fill=tk.X, pady=2)
            ttk.Label(row_frame, text=f"{index + 1}", width=6).pack(side=tk.LEFT, padx=(0, 4))

            condition_var = tk.StringVar()
            template_var = tk.StringVar()
            condition_entry = ttk.Entry(row_frame, textvariable=condition_var, width=18)
            template_entry = ttk.Entry(row_frame, textvariable=template_var)
            condition_entry.pack(side=tk.LEFT, padx=(0, 4))
            template_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self.score_forms.append({"condition": condition_var, "template": template_var})
            self._busy_widgets.append((condition_entry, "normal"))
            self._busy_widgets.append((template_entry, "normal"))
            self._rule_busy_widgets.extend([condition_entry, template_entry])
            condition_var.trace_add("write", self._schedule_config_autosave)
            template_var.trace_add("write", self._schedule_config_autosave)

            if index < len(seeded_rules):
                condition_var.set(seeded_rules[index].condition)
                template_var.set(seeded_rules[index].template)

    def on_build_rule_forms(self) -> None:
        """Rebuilds the rule forms from the requested form count."""
        existing_rules = self._collect_comment_rules()
        try:
            count = int(self.num_forms_var.get().strip())
        except ValueError:
            messagebox.showwarning("Thiếu rule", "Số rule phải là số nguyên hợp lệ.")
            self._log("Bỏ qua tạo form rule vì số lượng rule không hợp lệ.")
            return
        if count < 1 or count > 20:
            messagebox.showwarning("Thiếu rule", "Số rule phải nằm trong khoảng 1-20.")
            self._log("Bỏ qua tạo form rule vì số lượng nằm ngoài khoảng 1-20.")
            return
        self._build_rule_forms(count, initial_rules=existing_rules)
        self._schedule_config_autosave()
        self._log(f"Đã tạo {count} form rule.")

    def on_export_default_rules(self) -> None:
        """Restores the built-in default rule set and clears any saved custom config."""
        default_count = 7
        default_rules = self._seed_default_rules(default_count)
        try:
            if self._config_autosave_after_id is not None:
                self.root.after_cancel(self._config_autosave_after_id)
            self._config_autosave_after_id = None
            self._suspend_config_autosave = True
            self.num_forms_var.set(str(default_count))
            self._build_rule_forms(default_count, initial_rules=default_rules)
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
        except Exception as error:  # noqa: BLE001 - user-facing reset action
            messagebox.showerror("Lỗi nhận xét mặc định", str(error))
            self._log(f"Lỗi khôi phục nhận xét mặc định: {error}")
            return
        finally:
            self._suspend_config_autosave = False

        self._log("Đã khôi phục 7 rule nhận xét mặc định trong GUI.")
        self._log("Đã xóa file cấu hình tùy biến; lần mở sau app sẽ dùng lại rule mặc định cho đến khi bạn chỉnh sửa.")
        messagebox.showinfo(
            "Nhận xét mặc định",
            (
                "Đã khôi phục 7 rule nhận xét mặc định.\n"
                "Config tùy biến cũ đã được xóa. Khi bạn sửa rule sau đó, app sẽ tự tạo lại config."
            ),
        )

    def _collect_comment_rules(self) -> List[CommentRule]:
        """Collects the current rule form values from the GUI."""
        return [
            CommentRule(
                condition=form["condition"].get().strip(),
                template=form["template"].get().strip(),
            )
            for form in self.score_forms
        ]

    def _warn_rule_overlap(self, rules: List[CommentRule]) -> None:
        """Warns when numeric test points match more than one rule."""
        compiled_rules = compile_comment_rules(rules)
        if not compiled_rules:
            return
        overlaps: List[str] = []
        for probe in [0, 1, 2, 3, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10]:
            matched_conditions = []
            for checker, _template, condition in compiled_rules:
                try:
                    if checker(probe):
                        matched_conditions.append(condition)
                except TypeError:
                    continue
            if len(matched_conditions) > 1:
                overlaps.append(f"{probe}: {', '.join(matched_conditions)}")
        if overlaps:
            messagebox.showwarning(
                "Cảnh báo rule chồng lấn",
                "Một số điểm khớp nhiều rule. App sẽ lấy rule đầu tiên khớp từ trên xuống.\n\n"
                + "\n".join(overlaps[:6]),
            )

    def _validate_comment_rules(self) -> List[CommentRule]:
        """Validates the current rule forms and returns typed rules."""
        rules = self._collect_comment_rules()
        if not rules:
            raise RuntimeError("Chưa có form rule nào.")

        for index, rule in enumerate(rules, start=1):
            if not rule.condition.strip():
                raise RuntimeError(f"Rule {index}: chưa nhập điều kiện.")
            if not rule.template.strip():
                raise RuntimeError(f"Rule {index}: chưa nhập mẫu nhận xét.")
            if parse_condition(rule.condition) is None:
                raise RuntimeError(
                    f"Rule {index}: điều kiện `{rule.condition}` không hợp lệ ({describe_condition(rule.condition)})."
                )

        self._warn_rule_overlap(rules)
        return rules

    def _write_status_label(self, status: str) -> str:
        """Maps internal write-queue status codes to short Vietnamese labels."""
        return {
            "ready": "Sẵn sàng",
            "skip_no_score": "Chưa có điểm",
            "skip_comment_score": "Đã có điểm nhận xét",
            "skip_existing_comment": "Đã có nhận xét chữ",
            "skip_unmatched": "Không khớp rule",
            "skip_same": "Đã giống",
        }.get(status, status)

    def _write_status_counts(self, rows: List[CommentWriteRow]) -> Dict[str, int]:
        """Aggregates analyzed write rows by internal status code."""
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def _warn_students_with_numeric_comment_scores(
        self,
        rows: List[CommentWriteRow],
        allow_overwrite_existing_comment: bool,
    ) -> None:
        """Shows a warning when some comment cells already contain numeric score values."""
        if allow_overwrite_existing_comment:
            return
        blocked_rows = [row for row in rows if row.status == "skip_comment_score"]
        if not blocked_rows:
            return

        blocked_lines = [
            f"{row.student_name or '(không rõ tên)'}"
            f"{f' ({row.student_code})' if row.student_code else ''}: {row.current_comment}"
            for row in blocked_rows[:12]
        ]
        more_count = len(blocked_rows) - len(blocked_lines)
        message = "Tên học sinh đã có điểm nhận xét:\n" + "\n".join(blocked_lines)
        if more_count > 0:
            message += f"\n... và thêm {more_count} học sinh khác."
        messagebox.showwarning("Đã có điểm nhận xét", message)

"""Hiển thị combobox và cột điểm được nhận diện."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from ..models import ScorebookContext, ScorebookDetectedColumns, ScoreColumnSchema, ScoreOption


class ColumnsMixin:
    """Hiển thị combobox và cột điểm được nhận diện."""

    def _render_option_combo(
        self,
        combo: ttk.Combobox,
        variable: tk.StringVar,
        label_map: Dict[str, str],
        options: List[ScoreOption],
        selected_id: str,
        empty_label: str,
    ) -> None:
        """Renders one read-only combobox from typed options."""
        label_map.clear()
        labels: List[str] = []
        for option in options:
            label = option.option_text.strip() or option.option_id
            if label in label_map:
                label = f"{label} ({option.option_id})"
            label_map[label] = option.option_id
            labels.append(label)

        combo["values"] = labels
        target_label = next((label for label, option_id in label_map.items() if option_id == selected_id), "")
        variable.set(target_label or (labels[0] if labels else empty_label))

    def _selected_option_id(self, variable: tk.StringVar, label_map: Dict[str, str]) -> str:
        """Maps the current GUI combobox label back to its underlying option id."""
        return label_map.get(variable.get().strip(), "").strip()

    def _schema_by_key(
        self, schemas: List[ScoreColumnSchema], column_key: str
    ) -> ScoreColumnSchema | None:
        """Finds one parsed schema by key inside the UI layer."""
        column_key = column_key.strip()
        if not column_key:
            return None
        return next((schema for schema in schemas if schema.column_key == column_key), None)

    def _score_source_candidate_schemas(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> List[ScoreColumnSchema]:
        """Returns score-like columns that can be used as the source for comment rules."""
        candidate_keys = list(detected.score_candidate_keys)
        candidate_schemas: List[ScoreColumnSchema] = []
        seen_keys = set()

        for column_key in candidate_keys:
            schema = self._schema_by_key(schemas, column_key)
            if schema is None or schema.column_key in seen_keys:
                continue
            seen_keys.add(schema.column_key)
            candidate_schemas.append(schema)

        fallback_schemas = [
            schema
            for schema in sorted(schemas, key=lambda item: item.leaf_index)
            if schema.role_hint in {"score", "average"}
        ]
        for schema in fallback_schemas:
            if schema.column_key in seen_keys:
                continue
            seen_keys.add(schema.column_key)
            candidate_schemas.append(schema)
        return candidate_schemas

    def _schema_source_label(self, schema: ScoreColumnSchema) -> str:
        """Builds one stable combobox label for a selectable score source column."""
        header_text = " / ".join(
            part.strip()
            for part in schema.header_path
            if part.strip()
        )
        display_name = schema.display_name.strip() or schema.column_key
        if header_text and display_name not in header_text:
            return f"{header_text} [{display_name}]"
        return header_text or display_name

    def _render_score_source_combo(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> None:
        """Populates the score-source combobox from the live parsed schema."""
        selectable_schemas = self._score_source_candidate_schemas(schemas, detected)
        previous_selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)
        self.score_source_label_to_key.clear()
        labels: List[str] = []
        for schema in selectable_schemas:
            label = self._schema_source_label(schema)
            if label in self.score_source_label_to_key:
                label = f"{label} ({schema.column_key})"
            self.score_source_label_to_key[label] = schema.column_key
            labels.append(label)

        self.score_source_combo["values"] = labels
        selected_key = ""
        if self.score_source_label_to_key:
            if previous_selected_key in self.score_source_label_to_key.values():
                selected_key = previous_selected_key
            elif self.preferred_score_source_key.strip() in self.score_source_label_to_key.values():
                selected_key = self.preferred_score_source_key.strip()
            elif detected.preferred_score_column_key.strip() in self.score_source_label_to_key.values():
                selected_key = detected.preferred_score_column_key.strip()
            else:
                selected_key = next(iter(self.score_source_label_to_key.values()), "")

        target_label = next(
            (label for label, column_key in self.score_source_label_to_key.items() if column_key == selected_key),
            "",
        )
        self.score_source_var.set(target_label or "(chưa dò)")
        self.preferred_score_source_key = selected_key

    def _bind_config_autosave_traces(self) -> None:
        """Binds lightweight autosave traces for config-backed UI fields."""
        self.num_forms_var.trace_add("write", self._schedule_config_autosave)
        self.auto_save_var.trace_add("write", self._schedule_config_autosave)
        self.allow_comment_overwrite_var.trace_add("write", self._schedule_config_autosave)
        self.show_password_var.trace_add("write", self._schedule_config_autosave)

    def _schedule_config_autosave(self, *_args: object) -> None:
        """Debounces config writes so rule edits persist without spamming disk writes."""
        if self._suspend_config_autosave:
            return
        if self._config_autosave_after_id is not None:
            self.root.after_cancel(self._config_autosave_after_id)
        self._config_autosave_after_id = self.root.after(700, self._flush_config_autosave)

    def _flush_config_autosave(self) -> None:
        """Writes the current config snapshot to disk for autosave-triggered changes."""
        self._config_autosave_after_id = None
        if self._suspend_config_autosave:
            return
        try:
            self._save_config()
        except Exception as error:  # noqa: BLE001 - autosave should not break the UI
            self._log(f"Lỗi auto-save cấu hình: {error}")

    def _render_detected_columns(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> None:
        """Shows the auto-detected score/comment columns in the GUI."""
        score_schema = self._schema_by_key(schemas, detected.preferred_score_column_key)
        comment_schema = self._schema_by_key(schemas, detected.preferred_comment_column_key)

        self.detected_score_var.set(
            self._schema_source_label(score_schema) if score_schema is not None else "(chưa xác định)"
        )
        self.detected_comment_var.set(
            self._schema_source_label(comment_schema) if comment_schema is not None else "(chưa xác định)"
        )

        candidate_labels = []
        for column_key in detected.score_candidate_keys:
            schema = self._schema_by_key(schemas, column_key)
            if schema is None:
                continue
            candidate_labels.append(self._schema_source_label(schema))
        self.detected_candidates_var.set(", ".join(candidate_labels) if candidate_labels else "(không có)")
        self.detected_reason_var.set(detected.preferred_score_reason or "")

    def _selected_score_source_key(self, context: ScorebookContext) -> str:
        """Returns the user-selected source score column key with safe fallbacks."""
        selected_key = self._selected_option_id(self.score_source_var, self.score_source_label_to_key)
        if selected_key and self._schema_by_key(context.column_schemas, selected_key) is not None:
            return selected_key

        detected_key = context.detected_columns.preferred_score_column_key.strip()
        if detected_key and self._schema_by_key(context.column_schemas, detected_key) is not None:
            return detected_key

        candidate_schemas = self._score_source_candidate_schemas(
            context.column_schemas,
            context.detected_columns,
        )
        if candidate_schemas:
            return candidate_schemas[0].column_key
        return ""

    def _log_detected_columns(
        self,
        schemas: List[ScoreColumnSchema],
        detected: ScorebookDetectedColumns,
    ) -> None:
        """Writes the auto-detected score/comment columns to the log panel."""
        score_schema = self._schema_by_key(schemas, detected.preferred_score_column_key)
        comment_schema = self._schema_by_key(schemas, detected.preferred_comment_column_key)
        if score_schema is not None:
            self._log(f"Cột điểm đề xuất: {score_schema.display_name}")
        else:
            self._log("Chưa auto-detect được cột điểm đề xuất.")
        if comment_schema is not None:
            self._log(f"Cột nhận xét đích: {comment_schema.display_name}")
        else:
            self._log("Chưa auto-detect được cột nhận xét.")
        if detected.preferred_score_reason:
            self._log(detected.preferred_score_reason)

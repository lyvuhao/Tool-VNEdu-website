"""Dựng ScorebookContext và danh sách quyền từ snapshot."""

from __future__ import annotations

from typing import Dict, List

from ..access import ensure_selected_score_option
from ..models import ScorebookAccessEntry, ScorebookContext, ScoreOption


class ContextBuildMixin:
    """Dựng ScorebookContext và danh sách quyền từ snapshot."""

    def _build_scorebook_context(self, snapshot: Dict[str, object]) -> ScorebookContext:
        """Builds a typed scorebook context from one raw ExtJS snapshot."""
        column_schemas = self._extract_scorebook_schema_from_snapshot(snapshot)
        selected_grade_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentGradeId",
            hidden_key="hiddenGradeId",
        )
        selected_class_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentClassId",
            hidden_key="hiddenClassId",
        )
        selected_subject_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentSubjectId",
            hidden_key="hiddenSubjectId",
        )
        selected_term_id = self._effective_snapshot_selected_id(
            snapshot,
            current_key="currentTermId",
            hidden_key="hiddenTermId",
        )
        return ScorebookContext(
            grade_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("gradeOptions", []))),
                selected_grade_id,
                str(snapshot.get("currentGradeText", "")).strip(),
            ),
            selected_grade_id=selected_grade_id,
            class_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("classOptions", []))),
                selected_class_id,
                str(snapshot.get("currentClassText", "")).strip(),
            ),
            selected_class_id=selected_class_id,
            subject_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("subjectOptions", []))),
                selected_subject_id,
                str(snapshot.get("currentSubjectText", "")).strip(),
            ),
            selected_subject_id=selected_subject_id,
            term_options=ensure_selected_score_option(
                self._build_options(list(snapshot.get("termOptions", []))),
                selected_term_id,
                str(snapshot.get("currentTermText", "")).strip(),
            ),
            selected_term_id=selected_term_id,
            window_id=str(snapshot.get("windowId", "")).strip(),
            window_title=str(snapshot.get("windowTitle", "")).strip(),
            teacher_text=str(snapshot.get("teacherText", "")).strip(),
            permission_text=str(snapshot.get("permissionText", "")).strip(),
            comment_input_count=int(snapshot.get("commentInputCount", 0) or 0),
            enabled_comment_input_count=int(snapshot.get("enabledCommentInputCount", 0) or 0),
            column_schemas=column_schemas,
            detected_columns=self._detect_scorebook_columns(column_schemas),
        )

    def _effective_snapshot_selected_id(
        self,
        snapshot: Dict[str, object],
        current_key: str,
        hidden_key: str,
    ) -> str:
        """Returns the most reliable selected id from one raw scorebook snapshot."""
        hidden_value = str(snapshot.get(hidden_key, "")).strip()
        if hidden_value:
            return hidden_value
        return str(snapshot.get(current_key, "")).strip()

    def _requested_scorebook_context_differs(
        self,
        snapshot: Dict[str, object],
        *,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
    ) -> bool:
        """Returns whether one requested scorebook context needs live combo synchronization."""
        requested_specs = (
            (grade_id, "currentGradeId", "hiddenGradeId"),
            (class_id, "currentClassId", "hiddenClassId"),
            (subject_id, "currentSubjectId", "hiddenSubjectId"),
            (term_id, "currentTermId", "hiddenTermId"),
        )
        for requested_id, current_key, hidden_key in requested_specs:
            normalized_requested_id = requested_id.strip()
            if not normalized_requested_id:
                continue
            if normalized_requested_id != self._effective_snapshot_selected_id(snapshot, current_key, hidden_key):
                return True
        return False

    def _build_options(self, items: List[Dict[str, object]]) -> List[ScoreOption]:
        """Converts raw ExtJS store items into typed options."""
        options: List[ScoreOption] = []
        seen_ids = set()
        for item in items:
            option_id = str(item.get("id", "")).strip()
            option_text = str(item.get("ten", "")).strip() or option_id
            if not option_id or option_id in seen_ids:
                continue
            seen_ids.add(option_id)
            options.append(ScoreOption(option_id=option_id, option_text=option_text))
        return options

    def _build_access_entries(self, items: List[Dict[str, object]]) -> List[ScorebookAccessEntry]:
        """Converts raw permission scan items into typed access entries."""
        entries: List[ScorebookAccessEntry] = []
        seen_keys = set()
        for item in items:
            class_id = str(item.get("classId", "")).strip()
            subject_id = str(item.get("subjectId", "")).strip()
            if not class_id or not subject_id:
                continue
            key = (class_id, subject_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append(
                ScorebookAccessEntry(
                    grade_id=str(item.get("gradeId", "")).strip(),
                    grade_text=str(item.get("gradeText", "")).strip(),
                    class_id=class_id,
                    class_text=str(item.get("classText", "")).strip(),
                    subject_id=subject_id,
                    subject_text=str(item.get("subjectText", "")).strip(),
                    term_id=str(item.get("termId", "")).strip(),
                    term_text=str(item.get("termText", "")).strip(),
                    teacher_text=str(item.get("teacherText", "")).strip(),
                    permission_text=str(item.get("permissionText", "")).strip(),
                    comment_input_count=int(item.get("commentInputCount", 0) or 0),
                    enabled_comment_input_count=int(item.get("enabledCommentInputCount", 0) or 0),
                )
            )
        return entries

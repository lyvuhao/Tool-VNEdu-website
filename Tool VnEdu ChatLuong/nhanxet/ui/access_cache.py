"""Cache quyền truy cập và áp dụng context lên giao diện."""

from __future__ import annotations

from typing import List, Tuple
from urllib.parse import urlparse

from ..access import (
    class_options_from_access_entries,
    merge_score_options,
    subject_options_from_access_entries,
)
from ..models import ScorebookAccessEntry, ScorebookContext, ScoreOption


class AccessCacheMixin:
    """Cache quyền truy cập và áp dụng context lên giao diện."""

    def _normalize_access_cache_url(self, target_url: str) -> str:
        """Normalizes the VNEDU target URL so equivalent session URLs share one cache namespace."""
        normalized_url = target_url.strip()
        if not normalized_url:
            return ""
        parsed = urlparse(normalized_url)
        if parsed.scheme or parsed.netloc:
            normalized_path = parsed.path.rstrip("/")
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{normalized_path}"
        return normalized_url.rstrip("/").lower()

    def _current_access_cache_identity(self) -> Tuple[str, str]:
        """Returns the current in-memory cache identity derived from URL and username fields."""
        current_url = self._normalize_access_cache_url(self.url_var.get())
        current_username = self.username_var.get().strip()
        return current_url, current_username

    def _sync_access_cache_identity(self) -> Tuple[str, str]:
        """Clears stale permission cache state when the active VNEDU URL or username changes."""
        current_identity = self._current_access_cache_identity()
        previous_identity = self._access_cache_identity
        if previous_identity is None:
            self._access_cache_identity = current_identity
            return current_identity
        if previous_identity != current_identity:
            self.access_entry_cache.clear()
            self._access_scan_inflight.clear()
            self._access_cache_identity = current_identity
        return current_identity

    def _access_cache_key(self, grade_id: str, term_id: str) -> Tuple[str, str, str, str]:
        """Builds the cache key for one scanned grade-term permission matrix within one VNEDU session identity."""
        current_url, current_username = self._access_cache_identity or ("", "")
        return current_url, current_username, grade_id.strip(), term_id.strip()

    def _cache_context_access_entries(self, context: ScorebookContext) -> None:
        """Stores discovered permission entries so grade-term scans can be reused later."""
        if not getattr(self, "_matrix_access_scan_enabled", True):
            return
        self._sync_access_cache_identity()
        if not context.accessible_entries:
            return
        cache_grade_id = context.accessible_grade_id or context.selected_grade_id
        cache_term_id = context.accessible_term_id or context.selected_term_id
        if not cache_grade_id or not cache_term_id:
            return
        cache_key = self._access_cache_key(
            cache_grade_id,
            cache_term_id,
        )
        self.access_entry_cache[cache_key] = list(context.accessible_entries)

    def _with_cached_access_entries(self, context: ScorebookContext) -> ScorebookContext:
        """Reattaches cached permission entries to a plain scorebook context when possible."""
        if not getattr(self, "_matrix_access_scan_enabled", True):
            return context
        self._sync_access_cache_identity()
        if context.accessible_entries:
            return context
        cache_key = self._access_cache_key(context.selected_grade_id, context.selected_term_id)
        if cache_key in self.access_entry_cache:
            cached_entries = self.access_entry_cache.get(cache_key, [])
            if cached_entries:
                context.class_options = merge_score_options(
                    context.class_options,
                    class_options_from_access_entries(cached_entries),
                )
                context.subject_options = merge_score_options(
                    context.subject_options,
                    subject_options_from_access_entries(cached_entries),
                )
                context.accessible_entries = list(cached_entries)
                context.accessible_grade_id = context.selected_grade_id
                context.accessible_term_id = context.selected_term_id
        return context

    def _build_minimal_access_entries_from_context(self, context: ScorebookContext) -> List[ScorebookAccessEntry]:
        """Builds a one-item permission fallback from the current live context when it is clearly writable."""
        if not self._context_has_comment_permission(context):
            return []

        grade_id = context.selected_grade_id.strip()
        class_id = context.selected_class_id.strip()
        subject_id = context.selected_subject_id.strip()
        term_id = context.selected_term_id.strip()
        if not grade_id or not class_id or not subject_id or not term_id:
            return []

        grade_text = next(
            (option.option_text for option in context.grade_options if option.option_id == grade_id),
            grade_id,
        )
        class_text = next(
            (option.option_text for option in context.class_options if option.option_id == class_id),
            class_id,
        )
        subject_text = next(
            (option.option_text for option in context.subject_options if option.option_id == subject_id),
            subject_id,
        )
        term_text = next(
            (option.option_text for option in context.term_options if option.option_id == term_id),
            term_id,
        )

        return [
            ScorebookAccessEntry(
                grade_id=grade_id,
                grade_text=grade_text,
                class_id=class_id,
                class_text=class_text,
                subject_id=subject_id,
                subject_text=subject_text,
                term_id=term_id,
                term_text=term_text,
                teacher_text=context.teacher_text,
                permission_text=context.permission_text,
                comment_input_count=context.comment_input_count,
                enabled_comment_input_count=context.enabled_comment_input_count,
            )
        ]

    def _accessible_class_options(self, context: ScorebookContext) -> List[ScoreOption]:
        """Returns only classes that appear in the discovered permission matrix."""
        if not context.accessible_entries and not (context.accessible_grade_id or context.accessible_term_id):
            return list(context.class_options)
        if not context.accessible_entries:
            return []

        allowed_ids = {
            entry.class_id.strip()
            for entry in context.accessible_entries
            if entry.class_id.strip()
        }
        if not allowed_ids:
            return []

        return merge_score_options(
            [option for option in context.class_options if option.option_id.strip() in allowed_ids],
            class_options_from_access_entries(context.accessible_entries),
        )

    def _accessible_subject_options(self, context: ScorebookContext, class_id: str) -> List[ScoreOption]:
        """Returns only subjects that are permitted for the selected class."""
        if not context.accessible_entries and not (context.accessible_grade_id or context.accessible_term_id):
            return list(context.subject_options)
        if not context.accessible_entries:
            return []

        normalized_class_id = class_id.strip()
        candidate_entries = [
            entry
            for entry in context.accessible_entries
            if not normalized_class_id or entry.class_id.strip() == normalized_class_id
        ]
        if not candidate_entries:
            candidate_entries = list(context.accessible_entries)

        allowed_ids = {
            entry.subject_id.strip()
            for entry in candidate_entries
            if entry.subject_id.strip()
        }
        if not allowed_ids:
            return []

        return merge_score_options(
            [option for option in context.subject_options if option.option_id.strip() in allowed_ids],
            subject_options_from_access_entries(candidate_entries, class_id=normalized_class_id),
        )

    def _normalize_context_for_ui_scope(self, context: ScorebookContext) -> ScorebookContext:
        """Keeps the UI bound to the exact live context instead of any broader permission matrix."""
        if getattr(self, "_matrix_access_scan_enabled", True):
            return context
        context.accessible_entries = []
        context.accessible_grade_id = ""
        context.accessible_term_id = ""
        return context

    def _apply_scorebook_context(self, context: ScorebookContext) -> None:
        """Hydrates the GUI shell from the latest scorebook snapshot."""
        context = self._normalize_context_for_ui_scope(context)
        context = self._with_cached_access_entries(context)
        self._cache_context_access_entries(context)
        permission_matrix_ready = bool(
            context.accessible_entries
            or context.accessible_grade_id
            or context.accessible_term_id
        )
        accessible_class_options = self._accessible_class_options(context)
        selected_class_ids = {option.option_id for option in accessible_class_options}
        if accessible_class_options and context.selected_class_id not in selected_class_ids:
            context.selected_class_id = accessible_class_options[0].option_id

        accessible_subject_options = self._accessible_subject_options(context, context.selected_class_id)
        selected_subject_ids = {option.option_id for option in accessible_subject_options}
        if accessible_subject_options and context.selected_subject_id not in selected_subject_ids:
            context.selected_subject_id = accessible_subject_options[0].option_id

        self.current_context = context
        self._render_detected_columns(context.column_schemas, context.detected_columns)
        self._suspend_context_events = True
        try:
            self._render_score_source_combo(context.column_schemas, context.detected_columns)
            self._render_option_combo(
                self.grade_combo,
                self.grade_var,
                self.grade_label_to_id,
                context.grade_options,
                context.selected_grade_id,
                "(chưa đọc)",
            )
            self._render_option_combo(
                self.class_combo,
                self.class_var,
                self.class_label_to_id,
                accessible_class_options,
                context.selected_class_id,
                "(chưa đọc)",
            )
            self._render_option_combo(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                accessible_subject_options,
                context.selected_subject_id,
                "(chưa đọc)",
            )
            self._render_option_combo(
                self.term_combo,
                self.term_var,
                self.term_label_to_id,
                context.term_options,
                context.selected_term_id,
                "(chưa đọc)",
            )
        finally:
            self._suspend_context_events = False
        class_wording = "lớp có quyền" if permission_matrix_ready else "lớp hiện có"
        subject_wording = "môn có quyền" if permission_matrix_ready else "môn hiện có"
        window_title = context.window_title or "Sổ điểm"
        self._set_status_text(
            f"Đã đọc ngữ cảnh {window_title}: "
            f"{len(context.grade_options)} khối, "
            f"{len(accessible_class_options)} {class_wording}, "
            f"{len(accessible_subject_options)} {subject_wording}, "
            f"{len(context.term_options)} học kỳ, "
            f"{len(context.column_schemas)} cột."
        )

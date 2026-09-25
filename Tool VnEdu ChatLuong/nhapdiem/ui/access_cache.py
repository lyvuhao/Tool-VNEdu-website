"""Cache quyền truy cập và áp dụng context."""

from __future__ import annotations

from ..config import ACCESS_CACHE_FILE, ACCESS_CACHE_SCHEMA_VERSION
from ..models import AccessScopeMode
from ..scorebook_core import (
    class_options_from_access_entries,
    merge_score_options,
    resolve_accessible_selection,
    ScorebookAccessEntry,
    ScorebookContext,
    ScoreOption,
    subject_options_from_access_entries,
)
from ..storage import _load_json_object_file, _write_json_atomic_file


class AccessCacheMixin:
    """Cache quyền truy cập và áp dụng context."""

    def _access_cache_namespace(self) -> str:
        """Builds one persistent cache namespace for the current VNEDU account."""
        target_url = " ".join(self.url_var.get().strip().lower().split()).rstrip("/")
        username = " ".join(self.username_var.get().strip().lower().split())
        if not target_url or not username:
            return ""
        return f"{target_url}|{username}"

    def _clear_access_scope_caches(self) -> None:
        """Drops all in-memory permission caches for the current session namespace."""
        self._full_access_scope_cache.clear()
        self._subject_access_scope_cache.clear()
        self._empty_access_scope_target_cache.clear()

    def _encode_access_matrix_key(self, grade_id: str, term_id: str) -> str:
        """Serializes one grade-term key for JSON persistence."""
        return f"{grade_id.strip()}|{term_id.strip()}"

    def _encode_access_subject_key(self, grade_id: str, term_id: str, subject_id: str) -> str:
        """Serializes one grade-term-subject key for JSON persistence."""
        return f"{grade_id.strip()}|{term_id.strip()}|{subject_id.strip()}"

    def _serialize_access_entry(self, entry: object) -> dict[str, object]:
        """Converts one permission entry into JSON-safe data."""
        return {
            "grade_id": str(getattr(entry, "grade_id", "")).strip(),
            "grade_text": str(getattr(entry, "grade_text", "")).strip(),
            "class_id": str(getattr(entry, "class_id", "")).strip(),
            "class_text": str(getattr(entry, "class_text", "")).strip(),
            "subject_id": str(getattr(entry, "subject_id", "")).strip(),
            "subject_text": str(getattr(entry, "subject_text", "")).strip(),
            "term_id": str(getattr(entry, "term_id", "")).strip(),
            "term_text": str(getattr(entry, "term_text", "")).strip(),
            "teacher_text": str(getattr(entry, "teacher_text", "")).strip(),
            "permission_text": str(getattr(entry, "permission_text", "")).strip(),
            "comment_input_count": int(getattr(entry, "comment_input_count", 0) or 0),
            "enabled_comment_input_count": int(getattr(entry, "enabled_comment_input_count", 0) or 0),
        }

    def _deserialize_access_entries(self, raw_entries: object) -> list[ScorebookAccessEntry]:
        """Restores cached permission entries from disk."""
        restored_entries: list[ScorebookAccessEntry] = []
        if not isinstance(raw_entries, list):
            return restored_entries
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            try:
                restored_entries.append(
                    ScorebookAccessEntry(
                        grade_id=str(raw_entry.get("grade_id", "")).strip(),
                        grade_text=str(raw_entry.get("grade_text", "")).strip(),
                        class_id=str(raw_entry.get("class_id", "")).strip(),
                        class_text=str(raw_entry.get("class_text", "")).strip(),
                        subject_id=str(raw_entry.get("subject_id", "")).strip(),
                        subject_text=str(raw_entry.get("subject_text", "")).strip(),
                        term_id=str(raw_entry.get("term_id", "")).strip(),
                        term_text=str(raw_entry.get("term_text", "")).strip(),
                        teacher_text=str(raw_entry.get("teacher_text", "")).strip(),
                        permission_text=str(raw_entry.get("permission_text", "")).strip(),
                        comment_input_count=int(raw_entry.get("comment_input_count", 0) or 0),
                        enabled_comment_input_count=int(raw_entry.get("enabled_comment_input_count", 0) or 0),
                    )
                )
            except Exception:
                continue
        return restored_entries

    def _ensure_access_cache_loaded(self) -> None:
        """Loads the persisted permission cache for the current URL+username namespace."""
        namespace = self._access_cache_namespace()
        if namespace == self._loaded_access_cache_namespace:
            return
        self._clear_access_scope_caches()
        self._loaded_access_cache_namespace = namespace
        if not namespace or not ACCESS_CACHE_FILE.exists():
            return
        payload, backup_path, error = _load_json_object_file(ACCESS_CACHE_FILE)
        if error is not None:
            self._log(f"Không tải được cache quyền nhập điểm: {error}")
            if backup_path is not None:
                self._log(f"Đã chuyển file cache lỗi sang {backup_path}")
            return
        if int(payload.get("version", 0) or 0) != ACCESS_CACHE_SCHEMA_VERSION:
            return
        namespaces = payload.get("namespaces", {})
        if not isinstance(namespaces, dict):
            return
        namespace_payload = namespaces.get(namespace, {})
        if not isinstance(namespace_payload, dict):
            return
        raw_full = namespace_payload.get("full_matrices", {})
        if isinstance(raw_full, dict):
            for serialized_key, raw_entries in raw_full.items():
                if not isinstance(serialized_key, str):
                    continue
                parts = serialized_key.split("|", 1)
                if len(parts) != 2:
                    continue
                restored_entries = self._deserialize_access_entries(raw_entries)
                if restored_entries:
                    self._full_access_scope_cache[(parts[0].strip(), parts[1].strip())] = restored_entries
        raw_subject = namespace_payload.get("subject_scopes", {})
        if isinstance(raw_subject, dict):
            for serialized_key, raw_entries in raw_subject.items():
                if not isinstance(serialized_key, str):
                    continue
                parts = serialized_key.split("|", 2)
                if len(parts) != 3:
                    continue
                restored_entries = self._deserialize_access_entries(raw_entries)
                if restored_entries:
                    self._subject_access_scope_cache[
                        (parts[0].strip(), parts[1].strip(), parts[2].strip())
                    ] = restored_entries
        raw_empty = namespace_payload.get("empty_targets", {})
        if isinstance(raw_empty, dict):
            for serialized_key, raw_target in raw_empty.items():
                if not isinstance(serialized_key, str) or not isinstance(raw_target, dict):
                    continue
                parts = serialized_key.split("|", 1)
                if len(parts) != 2:
                    continue
                self._empty_access_scope_target_cache[(parts[0].strip(), parts[1].strip())] = (
                    str(raw_target.get("class_id", "")).strip(),
                    str(raw_target.get("subject_id", "")).strip(),
                )

    def _save_access_cache(self) -> None:
        """Persists the current permission caches for faster future grade/term switches."""
        namespace = self._access_cache_namespace()
        if not namespace:
            return
        payload: dict[str, object] = {"version": ACCESS_CACHE_SCHEMA_VERSION, "namespaces": {}}
        if ACCESS_CACHE_FILE.exists():
            loaded_payload, backup_path, error = _load_json_object_file(ACCESS_CACHE_FILE)
            if error is None:
                payload = loaded_payload
            elif backup_path is not None:
                self._log(f"Cache quyền cũ bị lỗi, đã backup sang {backup_path}")
        payload["version"] = ACCESS_CACHE_SCHEMA_VERSION
        namespaces = payload.get("namespaces", {})
        if not isinstance(namespaces, dict):
            namespaces = {}
        namespaces[namespace] = {
            "full_matrices": {
                self._encode_access_matrix_key(grade_id, term_id): [
                    self._serialize_access_entry(entry) for entry in entries
                ]
                for (grade_id, term_id), entries in self._full_access_scope_cache.items()
            },
            "subject_scopes": {
                self._encode_access_subject_key(grade_id, term_id, subject_id): [
                    self._serialize_access_entry(entry) for entry in entries
                ]
                for (grade_id, term_id, subject_id), entries in self._subject_access_scope_cache.items()
            },
            "empty_targets": {
                self._encode_access_matrix_key(grade_id, term_id): {
                    "class_id": class_id,
                    "subject_id": subject_id,
                }
                for (grade_id, term_id), (class_id, subject_id) in self._empty_access_scope_target_cache.items()
            },
        }
        payload["namespaces"] = namespaces
        try:
            _write_json_atomic_file(ACCESS_CACHE_FILE, payload)
        except Exception as error:  # noqa: BLE001
            self._log(f"Không lưu được cache quyền nhập điểm: {error}")

    def _context_has_access_scope(self, context: ScorebookContext) -> bool:
        """Returns whether the current context already carries one score-entry permission matrix."""
        return bool(
            context.accessible_entries
            or context.accessible_grade_id.strip()
            or context.accessible_term_id.strip()
        )

    def _access_matrix_key(self, grade_id: str, term_id: str) -> tuple[str, str]:
        """Builds one cache key for a full permission matrix inside one grade-term scope."""
        return (grade_id.strip(), term_id.strip())

    def _access_subject_key(self, grade_id: str, term_id: str, subject_id: str) -> tuple[str, str, str]:
        """Builds one cache key for a subject-scoped permission probe."""
        return (grade_id.strip(), term_id.strip(), subject_id.strip())

    def _set_context_access_scope(
        self,
        context: ScorebookContext,
        entries: list[object],
        grade_id: str,
        term_id: str,
        *,
        scope_mode: str = "",
        scope_subject_id: str = "",
    ) -> ScorebookContext:
        """Injects one known permission scope back into a context snapshot."""
        context.accessible_entries = list(entries)
        context.accessible_grade_id = grade_id.strip()
        context.accessible_term_id = term_id.strip()
        setattr(context, "_access_scope_mode", scope_mode.strip())
        setattr(context, "_access_scope_subject_id", scope_subject_id.strip())
        return context

    def _lookup_cached_access_scope(
        self,
        grade_id: str,
        term_id: str,
        *,
        subject_id: str = "",
    ) -> tuple[list[object], str, str]:
        """Returns one cached permission scope, preferring exact subject matches before full matrices."""
        normalized_grade_id = grade_id.strip()
        normalized_term_id = term_id.strip()
        normalized_subject_id = subject_id.strip()
        if not normalized_grade_id or not normalized_term_id:
            return [], "", ""
        if normalized_subject_id:
            subject_entries = self._subject_access_scope_cache.get(
                self._access_subject_key(normalized_grade_id, normalized_term_id, normalized_subject_id),
                [],
            )
            if subject_entries:
                return list(subject_entries), AccessScopeMode.SUBJECT_FAST, normalized_subject_id
        full_entries = self._full_access_scope_cache.get(
            self._access_matrix_key(normalized_grade_id, normalized_term_id),
            [],
        )
        if full_entries:
            return list(full_entries), AccessScopeMode.FULL_MATRIX, ""
        return [], "", ""

    def _lookup_cached_empty_access_target(self, grade_id: str, term_id: str) -> tuple[str, str] | None:
        """Returns one cached stable class-subject pair for grade-terms known to have no write permission."""
        normalized_key = self._access_matrix_key(grade_id, term_id)
        if normalized_key not in self._empty_access_scope_target_cache:
            return None
        empty_class_id, empty_subject_id = self._empty_access_scope_target_cache.get(normalized_key, ("", ""))
        return (str(empty_class_id).strip(), str(empty_subject_id).strip())

    def _resolve_cached_lookup_subject_id(self, grade_id: str, term_id: str, requested_subject_id: str) -> str:
        """Chooses one subject id for cache lookup even when the UI temporarily invalidates the subject combobox."""
        normalized_grade_id = grade_id.strip()
        normalized_term_id = term_id.strip()
        normalized_requested_subject_id = requested_subject_id.strip()
        if normalized_requested_subject_id:
            return normalized_requested_subject_id
        current_subject_id = ""
        if self.current_context is not None:
            current_subject_id = self.current_context.selected_subject_id.strip()
        candidate_subject_ids: list[str] = []
        seen_subject_ids: set[str] = set()
        for cached_grade_id, cached_term_id, cached_subject_id in self._subject_access_scope_cache:
            if cached_grade_id != normalized_grade_id or cached_term_id != normalized_term_id:
                continue
            normalized_cached_subject_id = str(cached_subject_id).strip()
            if not normalized_cached_subject_id or normalized_cached_subject_id in seen_subject_ids:
                continue
            seen_subject_ids.add(normalized_cached_subject_id)
            candidate_subject_ids.append(normalized_cached_subject_id)
        if current_subject_id and current_subject_id in seen_subject_ids:
            return current_subject_id
        if len(candidate_subject_ids) == 1:
            return candidate_subject_ids[0]
        return ""

    def _cache_access_scope(self, context: ScorebookContext) -> None:
        """Stores discovered permission scopes so repeated grade/subject switches can reuse them safely."""
        grade_id = context.accessible_grade_id.strip() or context.selected_grade_id.strip()
        term_id = context.accessible_term_id.strip() or context.selected_term_id.strip()
        if not grade_id or not term_id:
            return
        scope_mode = str(getattr(context, "_access_scope_mode", "")).strip()
        scope_subject_id = str(getattr(context, "_access_scope_subject_id", "")).strip()
        entries = list(context.accessible_entries)
        matrix_key = self._access_matrix_key(grade_id, term_id)
        cache_changed = False
        if not entries:
            if self._context_has_access_scope(context):
                next_empty_target = (
                    context.selected_class_id.strip(),
                    context.selected_subject_id.strip(),
                )
                if self._empty_access_scope_target_cache.get(matrix_key) != next_empty_target:
                    self._empty_access_scope_target_cache[matrix_key] = next_empty_target
                    cache_changed = True
                if matrix_key in self._full_access_scope_cache:
                    self._full_access_scope_cache.pop(matrix_key, None)
                    cache_changed = True
                subject_keys_to_remove = [
                    key for key in self._subject_access_scope_cache if key[0] == grade_id and key[1] == term_id
                ]
                for subject_key in subject_keys_to_remove:
                    self._subject_access_scope_cache.pop(subject_key, None)
                    cache_changed = True
            if cache_changed:
                self._save_access_cache()
            return
        if matrix_key in self._empty_access_scope_target_cache:
            self._empty_access_scope_target_cache.pop(matrix_key, None)
            cache_changed = True
        if scope_mode == AccessScopeMode.FULL_MATRIX:
            if self._full_access_scope_cache.get(matrix_key) != entries:
                self._full_access_scope_cache[matrix_key] = entries
                cache_changed = True
            subject_keys_to_remove = [
                key for key in self._subject_access_scope_cache if key[0] == grade_id and key[1] == term_id
            ]
            for subject_key in subject_keys_to_remove:
                self._subject_access_scope_cache.pop(subject_key, None)
                cache_changed = True
            per_subject: dict[str, list[object]] = {}
            for entry in entries:
                entry_subject_id = str(getattr(entry, "subject_id", "")).strip()
                if not entry_subject_id:
                    continue
                per_subject.setdefault(entry_subject_id, []).append(entry)
            for entry_subject_id, subject_entries in per_subject.items():
                subject_key = self._access_subject_key(grade_id, term_id, entry_subject_id)
                if self._subject_access_scope_cache.get(subject_key) != list(subject_entries):
                    self._subject_access_scope_cache[subject_key] = list(subject_entries)
                    cache_changed = True
            if cache_changed:
                self._save_access_cache()
            return
        if scope_mode == AccessScopeMode.SUBJECT_FAST and scope_subject_id:
            subject_key = self._access_subject_key(grade_id, term_id, scope_subject_id)
            if self._subject_access_scope_cache.get(subject_key) != entries:
                self._subject_access_scope_cache[subject_key] = entries
                cache_changed = True
        if cache_changed:
            self._save_access_cache()

    def _invalidate_access_scope_cache(
        self,
        grade_id: str,
        term_id: str,
        *,
        subject_id: str = "",
    ) -> None:
        """Drops one stale permission cache branch so the next run re-discovers it live."""
        matrix_key = self._access_matrix_key(grade_id, term_id)
        cache_changed = False
        normalized_subject_id = subject_id.strip()
        if normalized_subject_id:
            subject_key = self._access_subject_key(grade_id, term_id, normalized_subject_id)
            if subject_key in self._subject_access_scope_cache:
                self._subject_access_scope_cache.pop(subject_key, None)
                cache_changed = True
        else:
            if matrix_key in self._full_access_scope_cache:
                self._full_access_scope_cache.pop(matrix_key, None)
                cache_changed = True
            if matrix_key in self._empty_access_scope_target_cache:
                self._empty_access_scope_target_cache.pop(matrix_key, None)
                cache_changed = True
            subject_keys_to_remove = [
                key for key in self._subject_access_scope_cache if key[0] == grade_id.strip() and key[1] == term_id.strip()
            ]
            for subject_key in subject_keys_to_remove:
                self._subject_access_scope_cache.pop(subject_key, None)
                cache_changed = True
        if cache_changed:
            self._save_access_cache()

    def _copy_access_scope(
        self,
        context: ScorebookContext,
        source_context: ScorebookContext | None,
    ) -> ScorebookContext:
        """Preserves one already-scanned permission matrix when the returned snapshot omits it."""
        if self._context_has_access_scope(context):
            return context
        target_grade_id = context.selected_grade_id.strip() or context.accessible_grade_id.strip()
        target_term_id = context.selected_term_id.strip() or context.accessible_term_id.strip()
        if not target_grade_id or not target_term_id:
            return context
        target_subject_id = context.selected_subject_id.strip()
        cached_entries, cached_mode, cached_subject_id = self._lookup_cached_access_scope(
            target_grade_id,
            target_term_id,
            subject_id=target_subject_id,
        )
        if cached_entries:
            return self._set_context_access_scope(
                context,
                cached_entries,
                target_grade_id,
                target_term_id,
                scope_mode=cached_mode,
                scope_subject_id=cached_subject_id,
            )
        cached_empty_target = self._lookup_cached_empty_access_target(target_grade_id, target_term_id)
        if cached_empty_target is not None:
            return self._set_context_access_scope(
                context,
                [],
                target_grade_id,
                target_term_id,
                scope_mode=AccessScopeMode.FULL_MATRIX,
                scope_subject_id="",
            )
        if source_context is None:
            return context
        if not self._context_has_access_scope(source_context):
            return context
        source_grade_id = source_context.accessible_grade_id.strip() or source_context.selected_grade_id.strip()
        source_term_id = source_context.accessible_term_id.strip() or source_context.selected_term_id.strip()
        if target_grade_id != source_grade_id or target_term_id != source_term_id:
            return context
        source_scope_mode = str(getattr(source_context, "_access_scope_mode", "")).strip()
        source_scope_subject_id = str(getattr(source_context, "_access_scope_subject_id", "")).strip()
        if (
            source_scope_mode == AccessScopeMode.SUBJECT_FAST
            and target_subject_id
            and source_scope_subject_id
            and target_subject_id != source_scope_subject_id
        ):
            return context
        return self._set_context_access_scope(
            context,
            list(source_context.accessible_entries),
            source_grade_id,
            source_term_id,
            scope_mode=source_scope_mode,
            scope_subject_id=source_scope_subject_id,
        )

    def _accessible_class_options(self, context: ScorebookContext) -> list[ScoreOption]:
        """Returns only classes that belong to the score-entry permission matrix."""
        if not self._context_has_access_scope(context):
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

    def _accessible_subject_options(self, context: ScorebookContext, class_id: str) -> list[ScoreOption]:
        """Returns only subjects that remain valid for the selected accessible class."""
        if not self._context_has_access_scope(context):
            return list(context.subject_options)
        if str(getattr(context, "_access_scope_mode", "")).strip() == AccessScopeMode.SUBJECT_FAST:
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

    def _repair_access_context_selection(self, context: ScorebookContext) -> ScorebookContext:
        """Repairs stale selected class-subject ids against the discovered permission matrix."""
        if not self._context_has_access_scope(context):
            return context
        if not context.accessible_entries:
            context.selected_class_id = ""
            context.selected_subject_id = ""
            return context
        resolved_class_id, resolved_subject_id, _notes = resolve_accessible_selection(
            list(context.accessible_entries),
            preferred_class_id=context.selected_class_id,
            preferred_subject_id=context.selected_subject_id,
        )
        if resolved_class_id:
            context.selected_class_id = resolved_class_id
        if resolved_subject_id:
            context.selected_subject_id = resolved_subject_id
        return context

    def _selected_access_entry(self, context: ScorebookContext) -> object | None:
        """Returns the access-entry row that matches the currently selected class-subject pair."""
        if not context.accessible_entries:
            return None
        selected_class_id = context.selected_class_id.strip()
        selected_subject_id = context.selected_subject_id.strip()
        if str(getattr(context, "_access_scope_mode", "")).strip() == AccessScopeMode.SUBJECT_FAST:
            access_scope_subject_id = str(getattr(context, "_access_scope_subject_id", "")).strip()
            if access_scope_subject_id and selected_subject_id and selected_subject_id != access_scope_subject_id:
                return None
        exact_entry = next(
            (
                entry
                for entry in context.accessible_entries
                if entry.class_id.strip() == selected_class_id and entry.subject_id.strip() == selected_subject_id
            ),
            None,
        )
        if exact_entry is not None:
            return exact_entry
        if selected_class_id:
            class_entry = next(
                (entry for entry in context.accessible_entries if entry.class_id.strip() == selected_class_id),
                None,
            )
            if class_entry is not None:
                return class_entry
        return context.accessible_entries[0]

    def _clean_context_display_text(self, value: str) -> str:
        """Normalizes browser text before it is shown in the Tkinter status shell."""
        return " ".join(str(value or "").replace("\xa0", " ").split()).strip()

    def _effective_teacher_text(self, context: ScorebookContext) -> str:
        """Returns the teacher line that matches the filtered accessible class-subject pair."""
        access_entry = self._selected_access_entry(context)
        if access_entry is not None:
            teacher_text = self._clean_context_display_text(getattr(access_entry, "teacher_text", ""))
            if teacher_text:
                return teacher_text
        return self._clean_context_display_text(context.teacher_text)

    def _effective_permission_text(self, context: ScorebookContext) -> str:
        """Returns the permission line that matches the filtered accessible class-subject pair."""
        access_entry = self._selected_access_entry(context)
        if access_entry is not None:
            permission_text = self._clean_context_display_text(getattr(access_entry, "permission_text", ""))
            if permission_text:
                return permission_text
        return self._clean_context_display_text(context.permission_text)

    def _log_effective_context_identity(self, context: ScorebookContext) -> None:
        """Logs the teacher/permission lines that correspond to the filtered score-entry scope."""
        effective_permission_text = self._effective_permission_text(context)
        effective_teacher_text = self._effective_teacher_text(context)
        if effective_permission_text:
            self._log(effective_permission_text)
        if effective_teacher_text:
            self._log(effective_teacher_text)

    def _apply_context(self, context: ScorebookContext, clear_score_rows: bool = True) -> None:
        context = self._copy_access_scope(context, self.current_context)
        access_scope_ready = self._context_has_access_scope(context)
        accessible_class_options = self._accessible_class_options(context)
        selected_class_ids = {option.option_id for option in accessible_class_options}
        if accessible_class_options:
            if context.selected_class_id not in selected_class_ids:
                context.selected_class_id = accessible_class_options[0].option_id
        elif access_scope_ready:
            context.selected_class_id = ""

        accessible_subject_options = self._accessible_subject_options(context, context.selected_class_id)
        selected_subject_ids = {option.option_id for option in accessible_subject_options}
        if accessible_subject_options:
            if context.selected_subject_id not in selected_subject_ids:
                context.selected_subject_id = accessible_subject_options[0].option_id
        elif access_scope_ready:
            context.selected_subject_id = ""

        self.current_context = context
        self._cache_access_scope(context)
        self._cancel_context_autosync()
        self._invalidated_context_fields.clear()
        self._suspend_context_autosync = True
        try:
            self._render_options(self.grade_combo, self.grade_var, self.grade_label_to_id, context.grade_options, context.selected_grade_id)
            self._render_options(
                self.class_combo,
                self.class_var,
                self.class_label_to_id,
                accessible_class_options,
                context.selected_class_id,
            )
            if access_scope_ready and not accessible_class_options:
                self.class_var.set("(không có lớp được nhập điểm)")
            self._render_options(
                self.subject_combo,
                self.subject_var,
                self.subject_label_to_id,
                accessible_subject_options,
                context.selected_subject_id,
            )
            if access_scope_ready and not accessible_subject_options:
                self.subject_var.set("(không có môn được nhập điểm)")
            self._render_options(self.term_combo, self.term_var, self.term_label_to_id, context.term_options, context.selected_term_id)
        finally:
            self._suspend_context_autosync = False
        self._sync_context_combo_states()
        self._last_applied_context_ids = (
            context.selected_grade_id.strip(),
            context.selected_class_id.strip(),
            context.selected_subject_id.strip(),
            context.selected_term_id.strip(),
        )
        self.window_title_var.set(context.window_title.strip() or "(không có tiêu đề)")
        self.teacher_var.set(self._effective_teacher_text(context) or "(chưa có)")
        self.permission_var.set(self._effective_permission_text(context) or "(chưa có)")
        self.column_count_var.set(str(len(context.column_schemas)))
        self.comment_input_var.set(str(context.enabled_comment_input_count or context.comment_input_count or 0))
        self._render_score_columns(context)
        if clear_score_rows:
            self._clear_score_rows("Ngữ cảnh đã đổi. Hãy quét lại danh sách học sinh.", log_reason=False)
        class_wording = "lớp được nhập điểm" if access_scope_ready else "lớp"
        subject_wording = "môn được nhập điểm" if access_scope_ready else "môn"
        self._set_progress(
            self._progress_value,
            (
                f"Đã đọc Sổ điểm: {len(context.grade_options)} khối, "
                f"{len(accessible_class_options)} {class_wording}, "
                f"{len(accessible_subject_options)} {subject_wording}, "
                f"{len(context.term_options)} học kỳ."
            ),
        )

    def _require_context(self) -> ScorebookContext:
        if self.current_context is None:
            raise RuntimeError("Hãy đăng nhập và load Sổ điểm trước.")
        return self.current_context

    def _summary(self) -> str:
        context = self._require_context()
        grade_id, class_id, subject_id, term_id = self._selected_context_ids()
        pending_count = sum(1 for row in self._score_rows_by_key.values() if row.pending_score)
        lines = [
            f"Cửa sổ: {context.window_title or '(không có)'}",
            f"Giáo viên: {self._effective_teacher_text(context) or '(chưa có)'}",
            f"Quyền: {self._effective_permission_text(context) or '(chưa có)'}",
            f"Khối: {self.grade_var.get()} [{grade_id or context.selected_grade_id}]",
            f"Lớp: {self.class_var.get()} [{class_id or context.selected_class_id}]",
            f"Môn: {self.subject_var.get()} [{subject_id or context.selected_subject_id}]",
            f"Học kỳ: {self.term_var.get()} [{term_id or context.selected_term_id}]",
            f"Cột điểm đích: {self.target_score_column_var.get() or '(chưa chọn)'}",
            f"Số cột phát hiện: {len(context.column_schemas)}",
            f"Số ô nhận xét khả dụng: {context.enabled_comment_input_count or context.comment_input_count or 0}",
            f"Số học sinh đã quét: {len(self._score_rows_by_key)}",
            f"Số dòng đang chờ ghi: {pending_count}",
            f"Tên tác vụ mở rộng: {self.feature_name_var.get().strip() or '(chưa nhập)'}",
        ]
        return "\n".join(lines)

"""Nạp Sổ điểm và chọn context theo quyền."""

from __future__ import annotations

from tkinter import messagebox

from ..automation.fast_fetch import _direct_fetch_score_entries
from ..automation.permissions import (
    _permission_allows_score_entry,
    _rewrite_access_message_for_score_ui,
)
from ..config import ProgressCallback
from ..models import AccessScopeMode, LogTag
from ..scorebook_core import (
    create_subprogress_reporter,
    resolve_accessible_selection,
    ScorebookContext,
    ScoreColumnSchema,
    VnEduScoreAutomation,
)


class ScorebookLoadMixin:
    """Nạp Sổ điểm và chọn context theo quyền."""

    def _load_scorebook_shell_with_access(
        self,
        automation: VnEduScoreAutomation,
        username: str,
        password: str,
        progress_callback: ProgressCallback | None = None,
    ) -> object:
        """Loads one scorebook shell and prefers the score-permission filtered variant when available."""
        if hasattr(automation, "load_accessible_scorebook_context"):
            quick_shell_progress = create_subprogress_reporter(progress_callback, 5.0, 38.0)
            cached_apply_progress = create_subprogress_reporter(progress_callback, 38.0, 100.0)
            if progress_callback is not None:
                progress_callback(5.0, "Đang đọc nhanh Sổ điểm và kiểm tra cache quyền nhập điểm...")
            quick_shell_result = automation.load_scorebook_context(
                username=username,
                password=password,
                progress_callback=quick_shell_progress,
            )
            if isinstance(quick_shell_result, tuple) and quick_shell_result and isinstance(quick_shell_result[0], ScorebookContext):
                quick_context, quick_login_message = quick_shell_result[:2]
                quick_context = self._copy_access_scope(self._repair_access_context_selection(quick_context), None)
                if self._context_has_access_scope(quick_context):
                    current_permission_text = self._effective_permission_text(quick_context) or quick_context.permission_text
                    if quick_context.accessible_entries and _permission_allows_score_entry(current_permission_text):
                        if progress_callback is not None:
                            progress_callback(100.0, "Đã đọc xong dữ liệu Sổ điểm bằng cache quyền.")
                        return (
                            self._repair_access_context_selection(quick_context),
                            quick_login_message,
                            "Dùng cache quyền nhập điểm đã lưu để đọc nhanh Sổ điểm.",
                        )
                    if quick_context.selected_grade_id and quick_context.selected_term_id:
                        cached_result = self._select_scorebook_context_with_access(
                            automation,
                            grade_id=quick_context.selected_grade_id,
                            class_id=quick_context.selected_class_id,
                            subject_id=quick_context.selected_subject_id,
                            term_id=quick_context.selected_term_id,
                            username=username,
                            password=password,
                            progress_callback=cached_apply_progress,
                        )
                        if isinstance(cached_result, tuple) and cached_result and isinstance(cached_result[0], ScorebookContext):
                            cached_context, cached_login_message, cached_selection_message = cached_result[:3]
                            combined_login_message = " ".join(
                                part
                                for part in (quick_login_message, cached_login_message)
                                if str(part or "").strip()
                            ).strip()
                            combined_selection_message = " ".join(
                                part
                                for part in (
                                    "Dùng cache quyền nhập điểm để mở nhanh ngữ cảnh hợp lệ.",
                                    cached_selection_message,
                                )
                                if str(part or "").strip()
                            ).strip()
                            return (
                                self._repair_access_context_selection(cached_context),
                                combined_login_message,
                                combined_selection_message,
                            )
            if progress_callback is not None:
                progress_callback(38.0, "Không đủ cache phù hợp, app chuyển sang dò quyền nhập điểm live...")
            result = automation.load_accessible_scorebook_context(username=username, password=password)
            if isinstance(result, tuple) and result and isinstance(result[0], ScorebookContext):
                result = (self._repair_access_context_selection(result[0]), *result[1:])
            if progress_callback is not None:
                progress_callback(100.0, "Đã đọc xong dữ liệu Sổ điểm và quyền nhập điểm.")
            return result
        return automation.load_scorebook_context(
            username=username,
            password=password,
            progress_callback=progress_callback,
        )

    def _cached_transition_kind(
        self,
        *,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
    ) -> str:
        """Classifies cached context switches so narrow fast paths can be used safely."""
        if self.current_context is None:
            return ""
        current_grade_id = self.current_context.selected_grade_id.strip()
        current_class_id = self.current_context.selected_class_id.strip()
        current_subject_id = self.current_context.selected_subject_id.strip()
        current_term_id = self.current_context.selected_term_id.strip()
        normalized_grade_id = grade_id.strip() or current_grade_id
        normalized_class_id = class_id.strip() or current_class_id
        normalized_subject_id = subject_id.strip() or current_subject_id
        normalized_term_id = term_id.strip() or current_term_id
        if (
            normalized_grade_id == current_grade_id
            and normalized_term_id == current_term_id
            and normalized_subject_id == current_subject_id
            and normalized_class_id
            and normalized_class_id != current_class_id
        ):
            return "class_only"
        if (
            normalized_grade_id == current_grade_id
            and normalized_class_id == current_class_id
            and normalized_subject_id == current_subject_id
            and normalized_term_id
            and normalized_term_id != current_term_id
        ):
            return "term_only"
        return ""

    def _cached_scope_still_valid(
        self,
        context: ScorebookContext,
        *,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        expect_access: bool,
    ) -> bool:
        """Verifies that one cache-backed context still matches the live page after selection."""
        if context.selected_grade_id.strip() != grade_id.strip():
            return False
        if class_id.strip() and context.selected_class_id.strip() != class_id.strip():
            return False
        if subject_id.strip() and context.selected_subject_id.strip() != subject_id.strip():
            return False
        if term_id.strip() and context.selected_term_id.strip() != term_id.strip():
            return False
        permission_text = self._effective_permission_text(context) or context.permission_text
        if expect_access:
            return _permission_allows_score_entry(permission_text)
        return not _permission_allows_score_entry(permission_text)

    def _try_fast_cached_context_select(
        self,
        automation: VnEduScoreAutomation,
        *,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str,
        password: str,
        cached_entries: list[object],
        cached_mode: str,
        cached_subject_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[ScorebookContext, str, str] | None:
        """Uses a narrow live select path for cached class-only and term-only switches."""
        transition_kind = self._cached_transition_kind(
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
        )
        if not transition_kind:
            return None
        target_class_id = class_id.strip()
        target_subject_id = subject_id.strip()
        target_term_id = term_id.strip()
        target_grade_id = grade_id.strip()
        if not (target_grade_id and target_class_id and target_subject_id and target_term_id):
            return None
        try:
            with automation._open_page() as page:
                login_message = automation._login_if_needed_on_page(page, username=username, password=password)
                if not automation._has_scorebook_controls(page):
                    return None
                snapshot = automation._best_effort_scorebook_snapshot(page)
                if not isinstance(snapshot, dict) or not automation._scorebook_snapshot_ready_for_live_score_work(snapshot):
                    snapshot = automation._wait_for_scorebook_snapshot(page, timeout_sec=5.0)
                current_grade_id = automation._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
                current_class_id = automation._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
                current_subject_id = automation._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
                current_term_id = automation._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
                if transition_kind == "class_only":
                    if current_grade_id != target_grade_id or current_subject_id != target_subject_id or current_term_id != target_term_id:
                        return None
                elif transition_kind == "term_only":
                    if current_grade_id != target_grade_id or current_subject_id != target_subject_id or current_class_id != target_class_id:
                        return None
                if progress_callback is not None:
                    fast_caption = "Đang đổi lớp nhanh bằng cache quyền..." if transition_kind == "class_only" else "Đang đổi học kỳ nhanh bằng cache quyền..."
                    progress_callback(10.0, fast_caption)
                updated_snapshot, selection_message = automation._select_scorebook_context_on_page(
                    page,
                    snapshot,
                    grade_id=target_grade_id,
                    class_id=target_class_id,
                    subject_id=target_subject_id,
                    term_id=target_term_id,
                )
                context = automation._build_scorebook_context(updated_snapshot)
        except Exception:
            return None
        context = self._set_context_access_scope(
            context,
            cached_entries,
            target_grade_id,
            target_term_id,
            scope_mode=cached_mode,
            scope_subject_id=cached_subject_id,
        )
        context = self._repair_access_context_selection(context)
        if not self._cached_scope_still_valid(
            context,
            grade_id=target_grade_id,
            class_id=target_class_id,
            subject_id=target_subject_id,
            term_id=target_term_id,
            expect_access=bool(cached_entries),
        ):
            return None
        if progress_callback is not None:
            progress_callback(100.0, "Đã áp nhanh ngữ cảnh bằng cache quyền.")
        selection_parts = [
            "Dùng fast path cache để đổi ngữ cảnh nhanh hơn.",
            selection_message,
        ]
        return (
            context,
            login_message,
            " ".join(part for part in selection_parts if str(part).strip()).strip(),
        )

    def _select_scorebook_context_with_access(
        self,
        automation: VnEduScoreAutomation,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str,
        password: str,
        progress_callback: ProgressCallback | None = None,
    ) -> object:
        """Applies one context while preferring the permission-filtered score workflow."""
        requested_grade_id = grade_id.strip()
        requested_term_id = term_id.strip()
        requested_subject_id = self._resolve_cached_lookup_subject_id(
            requested_grade_id,
            requested_term_id,
            subject_id,
        )
        cached_entries, cached_mode, cached_subject_id = self._lookup_cached_access_scope(
            requested_grade_id,
            requested_term_id,
            subject_id=requested_subject_id,
        )
        if cached_entries:
            resolved_class_id, resolved_subject_id, fallback_notes = resolve_accessible_selection(
                list(cached_entries),
                preferred_class_id=class_id,
                preferred_subject_id=subject_id,
            )
            fast_result = self._try_fast_cached_context_select(
                automation,
                grade_id=requested_grade_id,
                class_id=resolved_class_id,
                subject_id=resolved_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                cached_entries=list(cached_entries),
                cached_mode=cached_mode,
                cached_subject_id=cached_subject_id,
                progress_callback=progress_callback,
            )
            if fast_result is not None:
                context, login_message, selection_message = fast_result
                message_parts = [selection_message]
                message_parts.extend(fallback_notes)
                return context, login_message, " ".join(
                    part for part in message_parts if str(part).strip()
                ).strip()
            if progress_callback is not None:
                progress_callback(5.0, "Đang áp ngữ cảnh bằng cache quyền nhập điểm...")
            result = automation.select_scorebook_context(
                grade_id=requested_grade_id,
                class_id=resolved_class_id,
                subject_id=resolved_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 100.0),
            )
            if isinstance(result, tuple) and len(result) >= 3 and isinstance(result[0], ScorebookContext):
                context, login_message, selection_message = result[:3]
                context = self._set_context_access_scope(
                    context,
                    cached_entries,
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=cached_mode,
                    scope_subject_id=cached_subject_id,
                )
                context = self._repair_access_context_selection(context)
                if not self._cached_scope_still_valid(
                    context,
                    grade_id=requested_grade_id,
                    class_id=resolved_class_id,
                    subject_id=resolved_subject_id,
                    term_id=requested_term_id,
                    expect_access=True,
                ):
                    self._invalidate_access_scope_cache(
                        requested_grade_id,
                        requested_term_id,
                        subject_id=(cached_subject_id or resolved_subject_id) if cached_mode == AccessScopeMode.SUBJECT_FAST else "",
                    )
                else:
                    message_parts = ["Dùng cache quyền nhập điểm để áp ngữ cảnh nhanh hơn."]
                    message_parts.extend(fallback_notes)
                    if selection_message:
                        message_parts.append(selection_message)
                    return context, login_message, " ".join(part for part in message_parts if str(part).strip()).strip()
            else:
                return result
        cached_empty_target = self._lookup_cached_empty_access_target(requested_grade_id, requested_term_id)
        if cached_empty_target is not None:
            cached_class_id, cached_subject_id = cached_empty_target
            if progress_callback is not None:
                progress_callback(5.0, "Đang áp ngữ cảnh bằng cache không có quyền nhập điểm...")
            result = automation.select_scorebook_context(
                grade_id=requested_grade_id,
                class_id=cached_class_id,
                subject_id=cached_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 100.0),
            )
            if isinstance(result, tuple) and len(result) >= 3 and isinstance(result[0], ScorebookContext):
                context, login_message, selection_message = result[:3]
                context = self._set_context_access_scope(
                    context,
                    [],
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=AccessScopeMode.FULL_MATRIX,
                    scope_subject_id="",
                )
                if self._cached_scope_still_valid(
                    context,
                    grade_id=requested_grade_id,
                    class_id=cached_class_id,
                    subject_id=cached_subject_id,
                    term_id=requested_term_id,
                    expect_access=False,
                ):
                    return (
                        context,
                        login_message,
                        " ".join(
                            part
                            for part in (
                                "Dùng cache quyền: khối/học kỳ này hiện không có lớp được nhập điểm.",
                                selection_message,
                            )
                            if str(part).strip()
                        ).strip(),
                    )
                self._invalidate_access_scope_cache(requested_grade_id, requested_term_id)
            else:
                return result
        if hasattr(automation, "select_accessible_scorebook_context"):
            if progress_callback is not None:
                progress_callback(5.0, "Đang áp ngữ cảnh và dò quyền nhập điểm...")
            result = automation.select_accessible_scorebook_context(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
            )
            if isinstance(result, tuple) and result and isinstance(result[0], ScorebookContext):
                result = (self._repair_access_context_selection(result[0]), *result[1:])
            if progress_callback is not None:
                progress_callback(100.0, "Đã áp xong ngữ cảnh và dò quyền nhập điểm.")
            return result
        return automation.select_scorebook_context(
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
            username=username,
            password=password,
            progress_callback=progress_callback,
        )

    def _scan_score_rows_with_access(
        self,
        automation: VnEduScoreAutomation,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        target_column_key: str,
        target_column_label: str,
        username: str,
        password: str,
        progress_callback: ProgressCallback | None = None,
    ) -> object:
        """Refreshes permission-filtered context first, then scans the chosen live score column."""
        if not hasattr(automation, "select_accessible_scorebook_context"):
            return automation.scan_score_entries(
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                target_column_key=target_column_key,
                target_column_label=target_column_label,
                username=username,
                password=password,
                progress_callback=progress_callback,
            )

        requested_grade_id = grade_id.strip()
        requested_term_id = term_id.strip()
        requested_subject_id = self._resolve_cached_lookup_subject_id(
            requested_grade_id,
            requested_term_id,
            subject_id,
        )
        cached_entries, cached_mode, cached_subject_scope_id = self._lookup_cached_access_scope(
            requested_grade_id,
            requested_term_id,
            subject_id=requested_subject_id,
        )
        login_message = ""
        selection_message = ""
        if cached_entries:
            resolved_class_id, resolved_subject_id, fallback_notes = resolve_accessible_selection(
                list(cached_entries),
                preferred_class_id=class_id,
                preferred_subject_id=subject_id,
            )
            fast_result = self._try_fast_cached_context_select(
                automation,
                grade_id=requested_grade_id,
                class_id=resolved_class_id,
                subject_id=resolved_subject_id,
                term_id=requested_term_id,
                username=username,
                password=password,
                cached_entries=list(cached_entries),
                cached_mode=cached_mode,
                cached_subject_id=cached_subject_scope_id,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 42.0),
            )
            if fast_result is not None:
                access_context, login_message, selection_message = fast_result
                selection_message = " ".join(
                    part for part in (*fallback_notes, selection_message) if str(part).strip()
                ).strip()
            else:
                access_result = automation.select_scorebook_context(
                    grade_id=requested_grade_id,
                    class_id=resolved_class_id,
                    subject_id=resolved_subject_id,
                    term_id=requested_term_id,
                    username=username,
                    password=password,
                    progress_callback=create_subprogress_reporter(progress_callback, 5.0, 42.0),
                )
                if not isinstance(access_result, tuple) or len(access_result) < 3:
                    raise RuntimeError("Kết quả select_scorebook_context không hợp lệ khi dùng cache quyền.")
                access_context, login_message, selection_message = access_result[:3]
                if not isinstance(access_context, ScorebookContext):
                    raise RuntimeError("Không nhận được ScorebookContext hợp lệ khi dùng cache quyền.")
                access_context = self._set_context_access_scope(
                    access_context,
                    cached_entries,
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=cached_mode,
                    scope_subject_id=cached_subject_scope_id,
                )
                access_context = self._repair_access_context_selection(access_context)
                if not self._cached_scope_still_valid(
                    access_context,
                    grade_id=requested_grade_id,
                    class_id=resolved_class_id,
                    subject_id=resolved_subject_id,
                    term_id=requested_term_id,
                    expect_access=True,
                ):
                    self._invalidate_access_scope_cache(
                        requested_grade_id,
                        requested_term_id,
                        subject_id=(cached_subject_scope_id or resolved_subject_id) if cached_mode == AccessScopeMode.SUBJECT_FAST else "",
                    )
                    selection_result = automation.select_accessible_scorebook_context(
                        grade_id=grade_id,
                        class_id=class_id,
                        subject_id=subject_id,
                        term_id=term_id,
                        username=username,
                        password=password,
                    )
                    if not isinstance(selection_result, tuple) or len(selection_result) < 3:
                        raise RuntimeError("Kết quả select_accessible_scorebook_context không hợp lệ.")
                    access_context, login_message, selection_message = selection_result[:3]
                    if not isinstance(access_context, ScorebookContext):
                        raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi dò quyền nhập điểm.")
                    access_context = self._repair_access_context_selection(access_context)
                    if self._context_has_access_scope(access_context) and not access_context.accessible_entries:
                        access_context.selected_class_id = ""
                        access_context.selected_subject_id = ""
                else:
                    selection_message = " ".join(
                        part
                        for part in ("Dùng cache quyền nhập điểm để quét nhanh hơn.", *fallback_notes, selection_message)
                        if str(part).strip()
                    ).strip()
        else:
            cached_empty_target = self._lookup_cached_empty_access_target(requested_grade_id, requested_term_id)
            if cached_empty_target is not None:
                cached_class_id, cached_subject_id = cached_empty_target
                access_result = automation.select_scorebook_context(
                    grade_id=requested_grade_id,
                    class_id=cached_class_id,
                    subject_id=cached_subject_id,
                    term_id=requested_term_id,
                    username=username,
                    password=password,
                    progress_callback=create_subprogress_reporter(progress_callback, 5.0, 42.0),
                )
                if not isinstance(access_result, tuple) or len(access_result) < 3:
                    raise RuntimeError("Kết quả select_scorebook_context không hợp lệ khi dùng cache không quyền.")
                access_context, login_message, selection_message = access_result[:3]
                if not isinstance(access_context, ScorebookContext):
                    raise RuntimeError("Không nhận được ScorebookContext hợp lệ khi dùng cache không quyền.")
                access_context = self._set_context_access_scope(
                    access_context,
                    [],
                    requested_grade_id,
                    requested_term_id,
                    scope_mode=AccessScopeMode.FULL_MATRIX,
                    scope_subject_id="",
                )
                if self._cached_scope_still_valid(
                    access_context,
                    grade_id=requested_grade_id,
                    class_id=cached_class_id,
                    subject_id=cached_subject_id,
                    term_id=requested_term_id,
                    expect_access=False,
                ):
                    selection_message = " ".join(
                        part
                        for part in (
                            "Dùng cache quyền: khối/học kỳ này hiện không có lớp được nhập điểm.",
                            selection_message,
                        )
                        if str(part).strip()
                    ).strip()
                else:
                    self._invalidate_access_scope_cache(requested_grade_id, requested_term_id)
                    selection_result = automation.select_accessible_scorebook_context(
                        grade_id=grade_id,
                        class_id=class_id,
                        subject_id=subject_id,
                        term_id=term_id,
                        username=username,
                        password=password,
                    )
                    if not isinstance(selection_result, tuple) or len(selection_result) < 3:
                        raise RuntimeError("Kết quả select_accessible_scorebook_context không hợp lệ.")
                    access_context, login_message, selection_message = selection_result[:3]
                    if not isinstance(access_context, ScorebookContext):
                        raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi dò quyền nhập điểm.")
                    access_context = self._repair_access_context_selection(access_context)
                    if self._context_has_access_scope(access_context) and not access_context.accessible_entries:
                        access_context.selected_class_id = ""
                        access_context.selected_subject_id = ""
            else:
                selection_result = automation.select_accessible_scorebook_context(
                    grade_id=grade_id,
                    class_id=class_id,
                    subject_id=subject_id,
                    term_id=term_id,
                    username=username,
                    password=password,
                )
                if not isinstance(selection_result, tuple) or len(selection_result) < 3:
                    raise RuntimeError("Kết quả select_accessible_scorebook_context không hợp lệ.")
                access_context, login_message, selection_message = selection_result[:3]
                if not isinstance(access_context, ScorebookContext):
                    raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi dò quyền nhập điểm.")
                access_context = self._repair_access_context_selection(access_context)
                if self._context_has_access_scope(access_context) and not access_context.accessible_entries:
                    access_context.selected_class_id = ""
                    access_context.selected_subject_id = ""
        if progress_callback is not None:
            progress_callback(42.0, "Đang quét danh sách học sinh theo lớp có quyền nhập điểm...")
        if not access_context.selected_class_id or not access_context.selected_subject_id:
            if progress_callback is not None:
                progress_callback(100.0, "Khối/Học kỳ hiện tại không có lớp được nhập điểm.")
            return access_context, [], login_message, selection_message

        # ---------- Fast Direct Score Fetch (bypass ExtJS combo cascade) ----------
        _fast_grade = access_context.selected_grade_id or grade_id
        _fast_class = access_context.selected_class_id
        _fast_subject = access_context.selected_subject_id
        _fast_term = access_context.selected_term_id or term_id
        try:
            with automation._open_page() as _fast_page:
                _fast_login = automation._login_if_needed_on_page(
                    _fast_page, username=username, password=password,
                )
                _fast_snap_result = automation._best_effort_scorebook_snapshot(_fast_page)
                _fast_snapshot = _fast_snap_result[0] if isinstance(_fast_snap_result, tuple) else _fast_snap_result
                if not isinstance(_fast_snapshot, dict):
                    _fast_snapshot = automation._wait_for_scorebook_snapshot(_fast_page, timeout_sec=5.0)
                if isinstance(_fast_snapshot, dict):
                    _fast_data = _direct_fetch_score_entries(
                        automation, _fast_page, _fast_snapshot,
                        grade_id=_fast_grade, class_id=_fast_class,
                        subject_id=_fast_subject, term_id=_fast_term,
                        target_column_key=target_column_key,
                    )
                    if _fast_data is not None and _fast_data.get("entries"):
                        _fast_entries_raw = list(_fast_data.get("entries") or [])
                        # H2 FIX: Fast path is only valid when the target column was
                        # actually resolved to live input fields. If targetLeafIndex
                        # was not found, or no row exposes a target_input_name, the
                        # write step would silently lose every score — so fall back
                        # to the robust slow scan instead of returning broken rows.
                        # Giữ đúng chỉ số 0 (trước đây `int(x or -1)` biến cột đầu tiên thành -1).
                        _raw_target_leaf = _fast_data.get("targetLeafIndex")
                        try:
                            _fast_target_leaf = int(_raw_target_leaf) if str(_raw_target_leaf).strip() not in ("", "None") else -1
                        except (TypeError, ValueError):
                            _fast_target_leaf = -1
                        _fast_linked = sum(
                            1 for _e in _fast_entries_raw
                            if str(_e.get("target_input_name", "") or "").strip()
                        )
                        if _fast_target_leaf < 0 or _fast_linked == 0:
                            self._log(
                                "Fast score fetch không gắn được ô nhập cho cột điểm này "
                                f"(targetLeafIndex={_fast_target_leaf}, linked={_fast_linked}); "
                                "chuyển sang quét chuẩn.",
                                tag=LogTag.WARNING,
                            )
                            raise RuntimeError("fast_fetch_no_target_input")
                        # --- Permission validation for multi-teacher accuracy ---
                        _fast_perm = str(_fast_data.get("permissionText") or "").strip()
                        _perm_lower = _fast_perm.lower()
                        if "không có quyền" in _perm_lower or "khong co quyen" in _perm_lower:
                            raise RuntimeError("Fast fetch: giáo viên không có quyền nhập điểm cho lớp/môn này.")

                        # Update context metadata
                        if _fast_perm:
                            access_context.permission_text = _fast_perm
                        _fast_teacher = str(_fast_data.get("teacherText") or "").strip()
                        if _fast_teacher:
                            access_context.teacher_text = _fast_teacher
                        cmt_count = _fast_data.get("commentInputCount")
                        if cmt_count:
                            access_context.comment_input_count = int(cmt_count)
                        en_cmt = _fast_data.get("enabledCommentCount")
                        if en_cmt:
                            access_context.enabled_comment_input_count = int(en_cmt)

                        # --- Ensure context IDs match the actual fetched selection ---
                        access_context.selected_grade_id = _fast_grade
                        access_context.selected_class_id = _fast_class
                        access_context.selected_subject_id = _fast_subject
                        access_context.selected_term_id = _fast_term

                        # --- Build column schemas for this teacher's subject/grade ---
                        _raw_schemas = _fast_data.get("schemas") or []
                        if _raw_schemas:
                            _column_schemas = []
                            for _rs in _raw_schemas:
                                _column_schemas.append(ScoreColumnSchema(
                                    column_key=str(_rs.get("column_key", "")),
                                    header_path=tuple(str(h) for h in (_rs.get("header_path") or [])),
                                    leaf_index=int(_rs.get("leaf_index", 0)),
                                    display_name=str(_rs.get("display_name", "")),
                                    editable=bool(_rs.get("editable", False)),
                                    role_hint=str(_rs.get("role_hint", "static")),
                                    input_kind=str(_rs.get("input_kind", "")),
                                    sample_value=str(_rs.get("sample_value", "")),
                                    block_index=str(_rs.get("block_index", "")),
                                    child_index=str(_rs.get("child_index", "")),
                                    data_column=str(_rs.get("data_column", "")),
                                    input_name=str(_rs.get("input_name", "")),
                                ))
                            _column_schemas = automation._finalize_schema_identity(_column_schemas)
                            access_context.column_schemas = _column_schemas
                            access_context.detected_columns = automation._detect_scorebook_columns(_column_schemas)

                        _fast_entries = list(_fast_data["entries"])
                        if progress_callback is not None:
                            progress_callback(100.0, f"Đã quét nhanh {len(_fast_entries)} HS qua direct API fetch.")
                        _fcl = " ".join(
                            part for part in (login_message, _fast_login or "") if str(part or "").strip()
                        ).strip()
                        _fcs = " ".join(
                            part for part in (selection_message, "Direct API fetch.") if str(part or "").strip()
                        ).strip()
                        return access_context, _fast_entries, _fcl, _fcs
        except Exception as _fast_err:  # noqa: BLE001 — graceful fallback to slow scan
            # BUG-08 FIX: Log lỗi thay vì nuốt im lặng để hỗ trợ debug
            self._log(
                f"Fast score fetch fallback: {type(_fast_err).__name__}: {_fast_err}",
                tag=LogTag.WARNING,
            )
            pass
        # ---------- End Fast Direct Score Fetch ----------

        scan_result = automation.scan_score_entries(
            grade_id=access_context.selected_grade_id or grade_id,
            class_id=access_context.selected_class_id,
            subject_id=access_context.selected_subject_id,
            term_id=access_context.selected_term_id or term_id,
            target_column_key=target_column_key,
            target_column_label=target_column_label,
            username=username,
            password=password,
            progress_callback=create_subprogress_reporter(progress_callback, 42.0, 100.0),
        )
        if not isinstance(scan_result, tuple) or len(scan_result) < 4:
            raise RuntimeError("Kết quả scan_score_entries không hợp lệ sau khi dò quyền nhập điểm.")
        scan_context, entries, scan_login_message, scan_selection_message = scan_result[:4]
        if not isinstance(scan_context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi quét học sinh.")
        scan_context = self._copy_access_scope(scan_context, access_context)
        combined_login = " ".join(part for part in (login_message, scan_login_message) if str(part or "").strip()).strip()
        combined_selection = " ".join(
            part for part in (selection_message, scan_selection_message) if str(part or "").strip()
        ).strip()
        return scan_context, entries, combined_login, combined_selection

    def on_load_scorebook_shell(self) -> None:
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi load Sổ điểm", str(error))
            self._log(f"Lỗi load Sổ điểm: {error}")
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        self._run_background(
            "Đang đăng nhập và vào Sổ điểm...",
            lambda progress: self._load_scorebook_shell_with_access(
                automation,
                username=username,
                password=password,
                progress_callback=progress,
            ),
            self._handle_load_scorebook_success,
            lambda error: (messagebox.showerror("Lỗi load Sổ điểm", str(error)), self._log(f"Lỗi load Sổ điểm: {error}"), self._set_progress(0.0, "Chưa load được Sổ điểm")),
        )

    def _schedule_auto_scan(self) -> None:
        if not self.auto_scan_rows_var.get():
            return
        if not self._selected_target_score_key():
            return
        self._cancel_pending_auto_scan()
        self._auto_scan_after_id = self.root.after(30, self._run_scheduled_auto_scan)

    def _run_scheduled_auto_scan(self) -> None:
        self._auto_scan_after_id = None
        if self._busy or self.current_context is None:
            return
        if not self.auto_scan_rows_var.get():
            return
        if not self._selected_target_score_key():
            return
        self.on_scan_score_rows()

    def _handle_load_scorebook_success(self, result: object) -> None:
        context = None
        login_message = ""
        selection_message = ""
        if isinstance(result, tuple):
            if len(result) >= 3:
                context, login_message, selection_message = result[:3]
            elif len(result) >= 2:
                context, login_message = result[:2]
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ.")
        if login_message:
            self._log(login_message)
        rewritten_selection_message = _rewrite_access_message_for_score_ui(selection_message)
        if rewritten_selection_message:
            self._log(rewritten_selection_message)
        self._apply_context(context)
        self._log_effective_context_identity(context)
        self._log("Đã vào Sổ điểm và đọc xong khung Khối/Lớp/Môn/Học kỳ.")
        self._schedule_auto_scan()

    def _handle_apply_context_error(self, error: Exception) -> None:
        if self.current_context is not None and self._invalidated_context_fields:
            self._apply_context(self.current_context, clear_score_rows=False)
        messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
        self._log(f"Lỗi áp ngữ cảnh: {error}")
        self._set_progress(0.0, "Áp ngữ cảnh thất bại")

    def on_apply_selected_context(self) -> None:
        if self.current_context is None:
            messagebox.showwarning("Chưa có dữ liệu", "Hãy load Sổ điểm trước khi áp ngữ cảnh.")
            self._log("Bỏ qua áp ngữ cảnh vì chưa có dữ liệu Sổ điểm.")
            return
        try:
            automation = self._build_automation()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Lỗi áp ngữ cảnh", str(error))
            self._log(f"Lỗi áp ngữ cảnh: {error}")
            return
        grade_id, class_id, subject_id, term_id = self._context_request_ids()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        target_column_key = self._selected_target_score_key()
        target_column_label = self.target_score_column_var.get().strip()
        if (
            self.auto_scan_rows_var.get()
            and target_column_key
            and hasattr(automation, "scan_score_entries")
        ):
            self._run_background(
                "Đang áp ngữ cảnh, dò quyền nhập điểm và quét học sinh...",
                lambda progress: self._scan_score_rows_with_access(
                    automation,
                    grade_id=grade_id,
                    class_id=class_id,
                    subject_id=subject_id,
                    term_id=term_id,
                    target_column_key=target_column_key,
                    target_column_label=target_column_label,
                    username=username,
                    password=password,
                    progress_callback=progress,
                ),
                self._handle_scan_score_rows_success,
                self._handle_apply_context_error,
            )
            return
        self._run_background(
            "Đang áp ngữ cảnh Sổ điểm và dò quyền nhập điểm...",
            lambda progress: self._select_scorebook_context_with_access(
                automation,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=progress,
            ),
            self._handle_apply_context_success,
            self._handle_apply_context_error,
        )

    def _handle_apply_context_success(self, result: object) -> None:
        context = None
        login_message = ""
        selection_message = ""
        if isinstance(result, tuple):
            if len(result) >= 3:
                context, login_message, selection_message = result[:3]
            elif len(result) >= 2:
                context, login_message = result[:2]
        if not isinstance(context, ScorebookContext):
            raise RuntimeError("Không nhận được ScorebookContext hợp lệ sau khi áp ngữ cảnh.")
        if login_message:
            self._log(login_message)
        rewritten_selection_message = _rewrite_access_message_for_score_ui(selection_message)
        if rewritten_selection_message:
            self._log(rewritten_selection_message)
        self._apply_context(context)
        self._log_effective_context_identity(context)
        self._log("Đã áp ngữ cảnh Khối/Lớp/Môn/Học kỳ lên live browser.")
        self._schedule_auto_scan()

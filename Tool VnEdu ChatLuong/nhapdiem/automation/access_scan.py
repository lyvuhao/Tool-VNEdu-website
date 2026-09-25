"""Quét danh sách lớp/môn được phép nhập điểm."""

from __future__ import annotations

from ..config import ProgressCallback
from ..models import AccessScopeMode
from ..scorebook_core import VnEduScoreAutomation


# ---------------------------------------------------------------------------
#  NOTE: Original monkey-patching lines removed.
#  All overrides are now in the VnEduScoreEntryAutomation subclass below.
# ---------------------------------------------------------------------------

_ORIGINAL_WAIT_FOR_SCOREBOOK_PERMISSION_SNAPSHOT = (
    VnEduScoreAutomation._wait_for_scorebook_permission_snapshot
)


_ORIGINAL_SELECT_SCOREBOOK_CONTEXT_ON_PAGE = (
    VnEduScoreAutomation._select_scorebook_context_on_page
)


_ORIGINAL_LOAD_ACCESSIBLE_SCOREBOOK_CONTEXT = (
    VnEduScoreAutomation.load_accessible_scorebook_context
)


_ORIGINAL_SELECT_ACCESSIBLE_SCOREBOOK_CONTEXT = (
    VnEduScoreAutomation.select_accessible_scorebook_context
)


def _live_scorebook_snapshot_or_fallback(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    """Reads one fresh scorebook snapshot and falls back to the provided cached snapshot."""
    live_snapshot, _error = self._best_effort_scorebook_snapshot(page)
    if isinstance(live_snapshot, dict) and live_snapshot:
        return live_snapshot
    return dict(snapshot or {})


def _snapshot_mismatch_messages(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    *,
    expected_class_id: str | None = None,
    expected_subject_id: str | None = None,
    expected_term_id: str | None = None,
) -> list[str]:
    """Builds human-readable mismatch details for one live scorebook snapshot."""
    messages: list[str] = []
    actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
    actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
    actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
    if expected_class_id is not None and actual_class_id != expected_class_id.strip():
        messages.append(
            f"Lớp thực tế là `{snapshot.get('currentClassText', '') or actual_class_id or '(trống)'}` "
            f"(id={actual_class_id or '(trống)'}), khác lớp mong đợi id={expected_class_id.strip()}"
        )
    if expected_subject_id is not None and actual_subject_id != expected_subject_id.strip():
        messages.append(
            f"Môn thực tế là `{snapshot.get('currentSubjectText', '') or actual_subject_id or '(trống)'}` "
            f"(id={actual_subject_id or '(trống)'}), khác môn mong đợi id={expected_subject_id.strip()}"
        )
    if expected_term_id is not None and actual_term_id != expected_term_id.strip():
        messages.append(
            f"Học kỳ thực tế là `{snapshot.get('currentTermText', '') or actual_term_id or '(trống)'}` "
            f"(id={actual_term_id or '(trống)'}), khác học kỳ mong đợi id={expected_term_id.strip()}"
        )
    return messages


def _patched_wait_for_scorebook_permission_snapshot(
    self: VnEduScoreAutomation,
    page: object,
    expected_class_id: str | None = None,
    expected_subject_id: str | None = None,
    expected_term_id: str | None = None,
    timeout_sec: float = 6.0,
    progress_callback: ProgressCallback | None = None,
    progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",
) -> dict[str, object]:
    """Waits for one permission snapshot and fails closed if the live page never reaches the expected ids."""
    snapshot = _ORIGINAL_WAIT_FOR_SCOREBOOK_PERMISSION_SNAPSHOT(
        self,
        page,
        expected_class_id=expected_class_id,
        expected_subject_id=expected_subject_id,
        expected_term_id=expected_term_id,
        timeout_sec=timeout_sec,
        progress_callback=progress_callback,
        progress_message=progress_message,
    )
    snapshot = _live_scorebook_snapshot_or_fallback(self, page, snapshot)
    mismatches = _snapshot_mismatch_messages(
        self,
        snapshot,
        expected_class_id=expected_class_id,
        expected_subject_id=expected_subject_id,
        expected_term_id=expected_term_id,
    )
    if mismatches:
        raise RuntimeError(
            "Sổ điểm chưa đồng bộ đúng quyền/ngữ cảnh sau khi chuyển lớp-môn-học kỳ. "
            + " ".join(mismatches)
        )
    return snapshot


def _patched_select_scorebook_context_on_page(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    grade_id: str = "",
    class_id: str = "",
    subject_id: str = "",
    term_id: str = "",
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, object], str]:
    """Refreshes the incoming snapshot from the live page before deciding whether a selection step can be skipped."""
    fresh_snapshot = _live_scorebook_snapshot_or_fallback(self, page, snapshot)
    return _ORIGINAL_SELECT_SCOREBOOK_CONTEXT_ON_PAGE(
        self,
        page,
        fresh_snapshot,
        grade_id=grade_id,
        class_id=class_id,
        subject_id=subject_id,
        term_id=term_id,
        progress_callback=progress_callback,
    )


# NOTE: Monkey-patching for _wait_for_scorebook_permission_snapshot
#       and _select_scorebook_context_on_page removed — now in subclass.


def _snapshot_has_option_id(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    options_key: str,
    option_id: str,
) -> bool:
    """Checks whether one snapshot option store contains the requested id."""
    normalized_id = str(option_id or "").strip()
    if not normalized_id:
        return False
    return normalized_id in {
        option.option_id.strip()
        for option in self._build_options(list(snapshot.get(options_key, [])))
        if option.option_id.strip()
    }


def _ordered_grade_ids_for_access_scan(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
) -> list[str]:
    """Returns grade ids ordered with the currently selected grade first."""
    ordered_ids: list[str] = []
    current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
    if current_grade_id:
        ordered_ids.append(current_grade_id)
    for option in self._build_options(list(snapshot.get("gradeOptions", []))):
        option_id = option.option_id.strip()
        if option_id and option_id not in ordered_ids:
            ordered_ids.append(option_id)
    return ordered_ids


def _pick_subject_id_for_fast_access_scan(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    preferred_subject_id: str = "",
) -> str:
    """Chooses the best subject id for the fast class-only permission scan."""
    normalized_preferred_id = str(preferred_subject_id or "").strip()
    if normalized_preferred_id and _snapshot_has_option_id(self, snapshot, "subjectOptions", normalized_preferred_id):
        return normalized_preferred_id
    current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
    if current_subject_id and _snapshot_has_option_id(self, snapshot, "subjectOptions", current_subject_id):
        return current_subject_id
    subject_options = self._build_options(list(snapshot.get("subjectOptions", [])))
    return next((option.option_id.strip() for option in subject_options if option.option_id.strip()), "")


def _ordered_subject_ids_for_access_scan(
    self: VnEduScoreAutomation,
    snapshot: dict[str, object],
    *,
    preferred_subject_id: str = "",
    exclude_subject_ids: set[str] | None = None,
) -> list[str]:
    """Returns subject ids ordered by preference for one fast permission probe."""
    ordered_ids: list[str] = []
    excluded_ids = {str(item or "").strip() for item in (exclude_subject_ids or set()) if str(item or "").strip()}

    def add_subject(subject_id: str) -> None:
        normalized_subject_id = str(subject_id or "").strip()
        if (
            not normalized_subject_id
            or normalized_subject_id in excluded_ids
            or normalized_subject_id in ordered_ids
            or not _snapshot_has_option_id(self, snapshot, "subjectOptions", normalized_subject_id)
        ):
            return
        ordered_ids.append(normalized_subject_id)

    add_subject(preferred_subject_id)
    add_subject(self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"))
    for option in self._build_options(list(snapshot.get("subjectOptions", []))):
        add_subject(option.option_id)
    return ordered_ids


def _discover_first_accessible_subject_in_snapshot(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    grade_id: str,
    term_id: str,
    preferred_subject_id: str = "",
    exclude_subject_ids: set[str] | None = None,
) -> tuple[list[object], str]:
    """Scans subjects in priority order and returns the first accessible subject hit."""
    subject_ids = _ordered_subject_ids_for_access_scan(
        self,
        snapshot,
        preferred_subject_id=preferred_subject_id,
        exclude_subject_ids=exclude_subject_ids,
    )
    for candidate_subject_id in subject_ids:
        entries = _discover_accessible_entries_for_fixed_subject(
            self,
            page,
            snapshot,
            grade_id=grade_id,
            term_id=term_id,
            subject_id=candidate_subject_id,
        )
        if entries:
            return entries, candidate_subject_id
    return [], ""


def _discover_accessible_entries_for_fixed_subject(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    grade_id: str,
    term_id: str,
    subject_id: str,
) -> list[object]:
    """Fast-path permission scan for one grade-term-subject across all classes."""
    normalized_grade_id = str(grade_id or "").strip()
    normalized_term_id = str(term_id or "").strip()
    normalized_subject_id = str(subject_id or "").strip()
    if not normalized_grade_id or not normalized_term_id or not normalized_subject_id:
        return []

    hydrated_snapshot = self._hydrate_scorebook_snapshot_options(
        page,
        snapshot,
        include_grade=False,
        include_class=True,
        include_subject=True,
        include_term=False,
        merge_existing=False,
        preserve_selected_if_missing=False,
    )
    class_options = self._build_options(list(hydrated_snapshot.get("classOptions", [])))
    if not class_options:
        return []
    if not _snapshot_has_option_id(self, hydrated_snapshot, "subjectOptions", normalized_subject_id):
        return []

    school_year = (
        str(hydrated_snapshot.get("hiddenSchoolYear", "")).strip()
        or str(hydrated_snapshot.get("currentSchoolYear", "")).strip()
    )
    window_id = str(hydrated_snapshot.get("windowId", "")).strip()
    if not school_year or not window_id:
        return []

    grade_text = self._option_text_by_id(
        self._build_options(list(hydrated_snapshot.get("gradeOptions", []))),
        normalized_grade_id,
    )
    term_text = self._option_text_by_id(
        self._build_options(list(hydrated_snapshot.get("termOptions", []))),
        normalized_term_id,
    )
    subject_text = self._option_text_by_id(
        self._build_options(list(hydrated_snapshot.get("subjectOptions", []))),
        normalized_subject_id,
    )
    if not subject_text:
        subject_text = str(hydrated_snapshot.get("currentSubjectText", "")).strip()

    raw_entries = page.evaluate(
        """async ({ schoolYear, windowId, gradeId, gradeText, termId, termText, subjectId, subjectText, classOptions }) => {
        const endpoint = '/v5/?load=edu.so_diem.nhap';
        const base = {
            app_nam_hoc: schoolYear,
            nam_hoc: schoolYear,
            iKhoi: gradeId,
            iHocKyId: termId,
            iMonHocId: subjectId,
            winid: windowId,
        };

        const parseRoleText = (doc) => {
            const roleCell = Array.from(doc.querySelectorAll('td')).find(td =>
                /quyền hạn/i.test((td.textContent || '').trim())
            );
            return (roleCell?.textContent || '').trim();
        };

        const parseTeacherText = (doc) => ((doc.querySelector('#gvbm')?.textContent) || '').trim();
        const tasks = (classOptions || []).map((classOption) => ({ classOption }));
        const results = [];
        const concurrency = 8;
        for (let index = 0; index < tasks.length; index += concurrency) {
            const batch = tasks.slice(index, index + concurrency);
            const batchResults = await Promise.all(batch.map(async (task) => {
                const params = new URLSearchParams({
                    ...base,
                    iLopId: task.classOption.option_id,
                });
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        },
                        body: params.toString(),
                    });
                    const html = await response.text();
                    const doc = new DOMParser().parseFromString(html, 'text/html');
                    const commentInputs = Array.from(doc.querySelectorAll('input.input_nhan_xet'));
                    const enabledCommentInputCount = commentInputs.filter(input => !input.disabled && !input.readOnly).length;
                    return {
                        gradeId,
                        gradeText,
                        classId: task.classOption.option_id,
                        classText: task.classOption.option_text,
                        subjectId,
                        subjectText,
                        termId,
                        termText,
                        teacherText: parseTeacherText(doc),
                        permissionText: parseRoleText(doc),
                        commentInputCount: commentInputs.length,
                        enabledCommentInputCount,
                    };
                } catch (error) {
                    return {
                        gradeId,
                        gradeText,
                        classId: task.classOption.option_id,
                        classText: task.classOption.option_text,
                        subjectId,
                        subjectText,
                        termId,
                        termText,
                        teacherText: '',
                        permissionText: `Lỗi dò quyền: ${String(error)}`,
                        commentInputCount: 0,
                        enabledCommentInputCount: 0,
                    };
                }
            }));
            results.push(...batchResults);
        }
        return results;
    }""",
        {
            "schoolYear": school_year,
            "windowId": window_id,
            "gradeId": normalized_grade_id,
            "gradeText": grade_text,
            "termId": normalized_term_id,
            "termText": term_text,
            "subjectId": normalized_subject_id,
            "subjectText": subject_text,
            "classOptions": [
                {
                    "option_id": option.option_id,
                    "option_text": option.option_text,
                }
                for option in class_options
            ],
        },
    )
    entries = self._build_access_entries(list(raw_entries or []))
    return [
        entry
        for entry in entries
        if self._can_comment_scorebook(entry.permission_text, entry.enabled_comment_input_count)
    ]


def _finalize_accessible_context_result(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    entries: list[object],
    grade_id: str,
    term_id: str,
    preferred_class_id: str = "",
    preferred_subject_id: str = "",
    message_parts: list[str] | None = None,
    access_scope_mode: str = "full_matrix",
    access_scope_subject_id: str = "",
) -> tuple[object, str]:
    """Selects one accessible class-subject pair and builds the final context payload."""
    normalized_grade_id = str(grade_id or "").strip()
    normalized_term_id = str(term_id or "").strip()
    final_message_parts = list(message_parts or [])
    working_snapshot = dict(snapshot)
    if entries:
        class_id, subject_id, fallback_notes = self._resolve_accessible_selection(
            list(entries),
            preferred_class_id=preferred_class_id,
            preferred_subject_id=preferred_subject_id,
        )
        if class_id and subject_id:
            working_snapshot, select_notes = self._select_scorebook_context_on_page(
                page,
                working_snapshot,
                grade_id=normalized_grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=normalized_term_id,
            )
            if select_notes:
                final_message_parts.append(select_notes)
        final_message_parts.extend(fallback_notes)
    context = self._build_scorebook_context(working_snapshot)
    self._apply_access_entries_to_context(
        context,
        list(entries),
        grade_id=normalized_grade_id,
        term_id=normalized_term_id,
    )
    setattr(context, "_access_scope_mode", str(access_scope_mode or "").strip())
    setattr(context, "_access_scope_subject_id", str(access_scope_subject_id or "").strip())
    return context, " ".join(part for part in final_message_parts if str(part or "").strip()).strip()


def _patched_load_accessible_scorebook_context(
    self: VnEduScoreAutomation,
    username: str = "",
    password: str = "",
) -> tuple[object, str, str]:
    """Loads one permission-filtered scorebook shell with multi-phase grade/subject fallback."""
    with self._open_page() as page:
        login_message = self._login_if_needed_on_page(page, username=username, password=password)
        self._ensure_scorebook_screen(page)
        snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
        )
        snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=True,
            include_class=True,
            include_subject=True,
            include_term=True,
            merge_existing=False,
            preserve_selected_if_missing=False,
        )

        original_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        original_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        original_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        target_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        grade_order = _ordered_grade_ids_for_access_scan(self, snapshot)
        visited_snapshots: dict[str, dict[str, object]] = {}
        primary_subject_id = _pick_subject_id_for_fast_access_scan(
            self,
            snapshot,
            preferred_subject_id=original_subject_id,
        )

        def get_grade_snapshot(candidate_grade_id: str) -> dict[str, object]:
            working_snapshot = visited_snapshots.get(candidate_grade_id)
            if working_snapshot is not None:
                return working_snapshot
            if candidate_grade_id == self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"):
                working_snapshot = dict(snapshot)
                working_snapshot = self._hydrate_scorebook_snapshot_options(
                    page,
                    working_snapshot,
                    include_grade=False,
                    include_class=True,
                    include_subject=True,
                    include_term=False,
                    merge_existing=False,
                    preserve_selected_if_missing=False,
                )
            else:
                # PERF/ACCURACY: khi đổi sang khối khác, class store reload bất đồng
                # bộ. Chờ store class fresh (loại bỏ option thuộc khối cũ) bằng
                # _wait_for_hydrated_scorebook_options thay vì hydrate thẳng — đảm
                # bảo không đọc nhầm danh sách lớp của khối trước đó.
                stale_class_id = self._effective_snapshot_selected_id(
                    snapshot, "currentClassId", "hiddenClassId"
                )
                changed_snapshot, _notes = self._select_scorebook_grade_term_on_page(
                    page,
                    snapshot,
                    grade_id=candidate_grade_id,
                    term_id=target_term_id,
                )
                working_snapshot = self._wait_for_hydrated_scorebook_options(
                    page,
                    changed_snapshot,
                    options_key="classOptions",
                    include_class=True,
                    include_subject=True,
                    expected_grade_id=candidate_grade_id,
                    stale_option_id=stale_class_id,
                    timeout_sec=5.0,
                )
            visited_snapshots[candidate_grade_id] = working_snapshot
            return working_snapshot

        def grade_text(candidate_snapshot: dict[str, object], candidate_grade_id: str) -> str:
            return self._option_text_by_id(
                self._build_options(list(candidate_snapshot.get("gradeOptions", []))),
                candidate_grade_id,
            )

        def subject_text(candidate_snapshot: dict[str, object], candidate_subject_id: str) -> str:
            return self._option_text_by_id(
                self._build_options(list(candidate_snapshot.get("subjectOptions", []))),
                candidate_subject_id,
            )

        current_grade_snapshot = get_grade_snapshot(original_grade_id)

        if primary_subject_id:
            current_grade_entries = _discover_accessible_entries_for_fixed_subject(
                self,
                page,
                current_grade_snapshot,
                grade_id=original_grade_id,
                term_id=target_term_id,
                subject_id=primary_subject_id,
            )
            if current_grade_entries:
                current_grade_text = grade_text(current_grade_snapshot, original_grade_id)
                context, selection_message = _finalize_accessible_context_result(
                    self,
                    page,
                    current_grade_snapshot,
                    entries=current_grade_entries,
                    grade_id=original_grade_id,
                    term_id=target_term_id,
                    preferred_class_id=original_class_id,
                    preferred_subject_id=primary_subject_id,
                    message_parts=[
                        f"Đã dò nhanh quyền nhập điểm theo môn hiện tại cho {current_grade_text}: {len(current_grade_entries)} lớp hợp lệ."
                    ],
                    access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                    access_scope_subject_id=primary_subject_id,
                )
                return context, login_message, selection_message

        for candidate_grade_id in grade_order:
            if not primary_subject_id or candidate_grade_id == original_grade_id:
                continue
            working_snapshot = get_grade_snapshot(candidate_grade_id)
            fast_entries = _discover_accessible_entries_for_fixed_subject(
                self,
                page,
                working_snapshot,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                subject_id=primary_subject_id,
            )
            if not fast_entries:
                continue
            candidate_grade_text = grade_text(working_snapshot, candidate_grade_id)
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                working_snapshot,
                entries=fast_entries,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_class_id="",
                preferred_subject_id=primary_subject_id,
                message_parts=[
                    f"Khối hiện tại không có quyền nhập điểm cho môn đang chọn, app chuyển sang {candidate_grade_text}.",
                    f"Đã dò nhanh quyền nhập điểm theo môn hiện tại cho {candidate_grade_text}: {len(fast_entries)} lớp hợp lệ.",
                ],
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=primary_subject_id,
            )
            return context, login_message, selection_message

        current_grade_entries, fallback_subject_id = _discover_first_accessible_subject_in_snapshot(
            self,
            page,
            current_grade_snapshot,
            grade_id=original_grade_id,
            term_id=target_term_id,
            preferred_subject_id=primary_subject_id,
            exclude_subject_ids={primary_subject_id} if primary_subject_id else set(),
        )
        if current_grade_entries and fallback_subject_id:
            current_grade_text = grade_text(current_grade_snapshot, original_grade_id)
            fallback_subject_text = subject_text(current_grade_snapshot, fallback_subject_id) or fallback_subject_id
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                current_grade_snapshot,
                entries=current_grade_entries,
                grade_id=original_grade_id,
                term_id=target_term_id,
                preferred_class_id=original_class_id,
                preferred_subject_id=fallback_subject_id,
                message_parts=[
                    f"Khối hiện tại không có quyền nhập điểm cho môn đang chọn, app chuyển sang môn {fallback_subject_text} trong cùng khối.",
                    f"Đã dò nhanh quyền nhập điểm cho {current_grade_text} / {fallback_subject_text}: {len(current_grade_entries)} lớp hợp lệ.",
                ],
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=fallback_subject_id,
            )
            return context, login_message, selection_message

        for candidate_grade_id in grade_order:
            if candidate_grade_id == original_grade_id:
                continue
            working_snapshot = get_grade_snapshot(candidate_grade_id)
            entries, fallback_subject_id = _discover_first_accessible_subject_in_snapshot(
                self,
                page,
                working_snapshot,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_subject_id=primary_subject_id,
                exclude_subject_ids={primary_subject_id} if primary_subject_id else set(),
            )
            if not entries or not fallback_subject_id:
                continue
            candidate_grade_text = grade_text(working_snapshot, candidate_grade_id)
            fallback_subject_text = subject_text(working_snapshot, fallback_subject_id) or fallback_subject_id
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                working_snapshot,
                entries=entries,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_class_id="",
                preferred_subject_id=fallback_subject_id,
                message_parts=[
                    f"Môn đang chọn không có quyền ở các khối đã dò trước đó, app chuyển sang {candidate_grade_text} / {fallback_subject_text}.",
                    f"Đã dò nhanh quyền nhập điểm cho {candidate_grade_text} / {fallback_subject_text}: {len(entries)} lớp hợp lệ.",
                ],
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=fallback_subject_id,
            )
            return context, login_message, selection_message

        for candidate_grade_id in grade_order:
            working_snapshot = get_grade_snapshot(candidate_grade_id)
            entries = self._discover_accessible_entries_for_current_grade(
                page,
                working_snapshot,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
            )
            if not entries:
                continue
            candidate_grade_text = grade_text(working_snapshot, candidate_grade_id)
            message_parts = []
            if candidate_grade_id != original_grade_id:
                message_parts.append(
                    f"App fallback sang dò toàn bộ ma trận quyền và chọn {candidate_grade_text} vì các nhánh quét nhanh không tìm thấy quyền phù hợp."
                )
            else:
                message_parts.append("App fallback sang dò toàn bộ ma trận quyền trong khối hiện tại.")
            message_parts.append(
                f"Đã dò toàn bộ quyền nhập điểm cho {candidate_grade_text}: {len(entries)} tổ hợp lớp/môn hợp lệ."
            )
            context, selection_message = _finalize_accessible_context_result(
                self,
                page,
                working_snapshot,
                entries=entries,
                grade_id=candidate_grade_id,
                term_id=target_term_id,
                preferred_class_id=original_class_id if candidate_grade_id == original_grade_id else "",
                preferred_subject_id=original_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.FULL_MATRIX,
            )
            return context, login_message, selection_message

        context = self._build_scorebook_context(snapshot)
        self._apply_access_entries_to_context(context, [], grade_id=original_grade_id, term_id=target_term_id)
        return (
            context,
            login_message,
            "Không tìm thấy lớp/môn nào có quyền nhập điểm ở tất cả các khối hiện có.",
        )


def _patched_select_accessible_scorebook_context(
    self: VnEduScoreAutomation,
    grade_id: str = "",
    class_id: str = "",
    subject_id: str = "",
    term_id: str = "",
    username: str = "",
    password: str = "",
) -> tuple[object, str, str]:
    """Applies one target grade-term and uses a fast subject-first scan with same-grade subject fallback."""
    with self._open_page() as page:
        login_message = self._login_if_needed_on_page(page, username=username, password=password)
        self._ensure_scorebook_screen(page)
        snapshot = self._wait_for_scorebook_snapshot(page, timeout_sec=8.0)
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
        )
        initial_context = self._build_scorebook_context(snapshot)
        target_grade_id = grade_id.strip() or initial_context.selected_grade_id
        target_term_id = term_id.strip() or initial_context.selected_term_id
        snapshot, selection_message = self._select_scorebook_grade_term_on_page(
            page,
            snapshot,
            grade_id=target_grade_id,
            term_id=target_term_id,
        )
        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or target_grade_id
        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or target_term_id
        snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=False,
            include_class=True,
            include_subject=True,
            include_term=False,
            merge_existing=False,
            preserve_selected_if_missing=False,
        )

        preferred_subject_id = subject_id.strip() or _pick_subject_id_for_fast_access_scan(
            self,
            snapshot,
            preferred_subject_id=initial_context.selected_subject_id,
        )
        entries: list[object] = []
        if preferred_subject_id:
            entries = _discover_accessible_entries_for_fixed_subject(
                self,
                page,
                snapshot,
                grade_id=current_grade_id,
                term_id=current_term_id,
                subject_id=preferred_subject_id,
            )
        if entries:
            message_parts = [selection_message] if selection_message else []
            message_parts.append(
                f"Đã dò nhanh quyền nhập điểm theo môn hiện tại: {len(entries)} lớp hợp lệ."
            )
            context, final_message = _finalize_accessible_context_result(
                self,
                page,
                snapshot,
                entries=entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
                preferred_class_id=class_id,
                preferred_subject_id=preferred_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=preferred_subject_id,
            )
            return context, login_message, final_message

        entries, fallback_subject_id = _discover_first_accessible_subject_in_snapshot(
            self,
            page,
            snapshot,
            grade_id=current_grade_id,
            term_id=current_term_id,
            preferred_subject_id=preferred_subject_id,
            exclude_subject_ids={preferred_subject_id} if preferred_subject_id else set(),
        )
        if entries and fallback_subject_id:
            fallback_subject_text = self._option_text_by_id(
                self._build_options(list(snapshot.get("subjectOptions", []))),
                fallback_subject_id,
            ) or fallback_subject_id
            message_parts = [selection_message] if selection_message else []
            message_parts.append(
                f"Môn đang chọn không có quyền trong khối này, app chuyển sang môn {fallback_subject_text}."
            )
            message_parts.append(
                f"Đã dò nhanh quyền nhập điểm cho môn {fallback_subject_text}: {len(entries)} lớp hợp lệ."
            )
            context, final_message = _finalize_accessible_context_result(
                self,
                page,
                snapshot,
                entries=entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
                preferred_class_id=class_id,
                preferred_subject_id=fallback_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.SUBJECT_FAST,
                access_scope_subject_id=fallback_subject_id,
            )
            return context, login_message, final_message

        entries = self._discover_accessible_entries_for_current_grade(
            page,
            snapshot,
            grade_id=current_grade_id,
            term_id=current_term_id,
        )
        message_parts = [selection_message] if selection_message else []
        if entries:
            message_parts.append(
                f"Đã dò toàn bộ quyền nhập điểm cho khối đang chọn: {len(entries)} tổ hợp lớp/môn hợp lệ."
            )
            context, final_message = _finalize_accessible_context_result(
                self,
                page,
                snapshot,
                entries=entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
                preferred_class_id=class_id,
                preferred_subject_id=subject_id.strip() or preferred_subject_id,
                message_parts=message_parts,
                access_scope_mode=AccessScopeMode.FULL_MATRIX,
            )
            return context, login_message, final_message

        context = self._build_scorebook_context(snapshot)
        self._apply_access_entries_to_context(
            context,
            [],
            grade_id=current_grade_id,
            term_id=current_term_id,
        )
        message_parts.append("Khối/Học kỳ hiện tại không có lớp/môn nào được phép nhập điểm.")
        return context, login_message, " ".join(part for part in message_parts if part).strip()

"""Quét quyền truy cập Sổ điểm theo khối/lớp/môn."""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from playwright.sync_api import Error as PlaywrightError, Page

from ..access import apply_access_entries_to_context, resolve_accessible_selection
from ..config import ProgressCallback
from ..models import ScorebookAccessEntry, ScorebookContext, ScoreOption
from ..progress import emit_progress


class AccessScanMixin:
    """Quét quyền truy cập Sổ điểm theo khối/lớp/môn."""

    def _option_text_by_id(self, options: List[ScoreOption], option_id: str) -> str:
        """Finds the human-readable text for one score option id."""
        option_id = option_id.strip()
        for option in options:
            if option.option_id == option_id:
                return option.option_text
        return option_id

    def _subject_options_by_class_for_current_grade(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str,
        term_id: str,
        class_options: List[ScoreOption],
    ) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
        """Enumerates subject options per class and restores the original live scorebook context afterwards."""
        original_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        original_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        original_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        original_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        current_snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=False,
            include_class=True,
            include_subject=True,
            include_term=False,
        )
        class_subject_specs: List[Dict[str, object]] = []

        for class_option in class_options:
            class_id = class_option.option_id.strip()
            if not class_id:
                continue
            current_class_id = self._effective_snapshot_selected_id(current_snapshot, "currentClassId", "hiddenClassId")
            if current_class_id != class_id:
                class_combo_id = str(current_snapshot.get("classComboId", "")).strip()
                if not class_combo_id or not self._set_combo_value(page, class_combo_id, class_id):
                    raise RuntimeError(f"Không thể chọn lớp id={class_id} trong lúc dò quyền lớp/môn.")
                page.wait_for_timeout(150)
                current_snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=grade_id or None,
                    expected_class_id=class_id,
                    expected_term_id=term_id or None,
                    timeout_sec=6.0,
                )
                actual_class_id = self._effective_snapshot_selected_id(current_snapshot, "currentClassId", "hiddenClassId")
                if actual_class_id != class_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={class_id} để dò quyền.")
            current_snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                current_snapshot,
                include_grade=False,
                include_class=False,
                include_subject=True,
                include_term=False,
            )
            subject_options = self._build_options(list(current_snapshot.get("subjectOptions", [])))
            if not subject_options:
                continue
            class_subject_specs.append(
                {
                    "classId": class_option.option_id,
                    "classText": class_option.option_text,
                    "subjectOptions": [
                        {
                            "option_id": option.option_id,
                            "option_text": option.option_text,
                        }
                        for option in subject_options
                    ],
                }
            )

        if self._requested_scorebook_context_differs(
            current_snapshot,
            grade_id=original_grade_id,
            class_id=original_class_id,
            subject_id=original_subject_id,
            term_id=original_term_id,
        ):
            current_snapshot, _ = self._select_scorebook_context_on_page(
                page,
                current_snapshot,
                grade_id=original_grade_id,
                class_id=original_class_id,
                subject_id=original_subject_id,
                term_id=original_term_id,
            )

        return class_subject_specs, current_snapshot

    def _can_comment_scorebook(self, permission_text: str, enabled_comment_input_count: int) -> bool:
        """Returns whether the current scorebook payload allows comment editing."""
        normalized_permission = permission_text.strip().lower()
        if enabled_comment_input_count <= 0:
            return False
        return "không có quyền" not in normalized_permission and "khong co quyen" not in normalized_permission

    def _best_effort_scorebook_snapshot(self, page: Page) -> Tuple[Dict[str, object] | None, Exception | None]:
        """Reads one scorebook snapshot while treating transient live-tab errors as retryable."""
        try:
            return self._scorebook_snapshot(page), None
        except (RuntimeError, PlaywrightError) as error:
            return None, error

    def _finish_scorebook_snapshot_wait(
        self,
        last_snapshot: Dict[str, object],
        last_error: Exception | None,
        timeout_sec: float,
        purpose: str,
    ) -> Dict[str, object]:
        """Returns the last good snapshot, or raises the last read error if none succeeded."""
        if last_snapshot:
            return last_snapshot
        if last_error is not None:
            raise RuntimeError(
                f"Hết thời gian chờ {purpose} trong {timeout_sec:.1f}s. Lỗi cuối: {last_error}"
            ) from last_error
        raise RuntimeError(f"Hết thời gian chờ {purpose} trong {timeout_sec:.1f}s nhưng chưa đọc được snapshot nào.")

    def _discover_accessible_entries_for_current_grade(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str,
        term_id: str,
    ) -> List[ScorebookAccessEntry]:
        """Scans the current grade-term matrix and keeps only class-subject pairs with comment rights."""
        grade_id = grade_id.strip()
        term_id = term_id.strip()
        snapshot = self._hydrate_scorebook_snapshot_options(
            page,
            snapshot,
            include_grade=False,
            include_class=True,
            include_subject=True,
            include_term=False,
        )
        class_options = self._build_options(list(snapshot.get("classOptions", [])))
        if not grade_id or not term_id or not class_options:
            return []

        grade_text = self._option_text_by_id(self._build_options(list(snapshot.get("gradeOptions", []))), grade_id)
        term_text = self._option_text_by_id(self._build_options(list(snapshot.get("termOptions", []))), term_id)
        school_year = str(snapshot.get("hiddenSchoolYear", "")).strip() or str(snapshot.get("currentSchoolYear", "")).strip()
        window_id = str(snapshot.get("windowId", "")).strip()
        if not school_year or not window_id:
            return []
        class_subject_specs, _restored_snapshot = self._subject_options_by_class_for_current_grade(
            page,
            snapshot,
            grade_id=grade_id,
            term_id=term_id,
            class_options=class_options,
        )
        if not class_subject_specs:
            return []

        raw_entries = page.evaluate(
            """async ({ schoolYear, windowId, gradeId, gradeText, termId, termText, classSubjectSpecs }) => {
            const endpoint = '/v5/?load=edu.so_diem.nhap';
            const base = {
                app_nam_hoc: schoolYear,
                nam_hoc: schoolYear,
                iKhoi: gradeId,
                iHocKyId: termId,
                winid: windowId,
            };

            const parseRoleText = (doc) => {
                const roleCell = Array.from(doc.querySelectorAll('td')).find(td =>
                    /quyền hạn/i.test((td.textContent || '').trim())
                );
                return (roleCell?.textContent || '').trim();
            };

            const parseTeacherText = (doc) => ((doc.querySelector('#gvbm')?.textContent) || '').trim();
            const tasks = [];
            for (const classSpec of classSubjectSpecs) {
                for (const subjectOption of classSpec.subjectOptions || []) {
                    tasks.push({
                        classOption: {
                            option_id: classSpec.classId,
                            option_text: classSpec.classText,
                        },
                        subjectOption,
                    });
                }
            }

            const results = [];
            const concurrency = 6;
            for (let index = 0; index < tasks.length; index += concurrency) {
                const batch = tasks.slice(index, index + concurrency);
                const batchResults = await Promise.all(batch.map(async (task) => {
                    const params = new URLSearchParams({
                        ...base,
                        iLopId: task.classOption.option_id,
                        iMonHocId: task.subjectOption.option_id,
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
                            subjectId: task.subjectOption.option_id,
                            subjectText: task.subjectOption.option_text,
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
                            subjectId: task.subjectOption.option_id,
                            subjectText: task.subjectOption.option_text,
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
                "gradeId": grade_id,
                "gradeText": grade_text,
                "termId": term_id,
                "termText": term_text,
                "classSubjectSpecs": class_subject_specs,
            },
        )
        entries = self._build_access_entries(list(raw_entries or []))
        return [
            entry
            for entry in entries
            if self._can_comment_scorebook(entry.permission_text, entry.enabled_comment_input_count)
        ]

    def _wait_for_scorebook_snapshot(
        self,
        page: Page,
        expected_grade_id: str | None = None,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm đồng bộ dữ liệu...",
    ) -> Dict[str, object]:
        """Waits until scorebook combobox state reaches the expected ids."""
        deadline = time.time() + timeout_sec
        last_snapshot: Dict[str, object] = {}
        last_error: Exception | None = None
        while time.time() < deadline:
            snapshot, snapshot_error = self._best_effort_scorebook_snapshot(page)
            if snapshot is None:
                last_error = snapshot_error
                page.wait_for_timeout(250)
                continue
            last_snapshot = snapshot
            last_error = None

            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")

            grade_ready = expected_grade_id is None or actual_grade_id == expected_grade_id
            class_ready = expected_class_id is None or actual_class_id == expected_class_id
            subject_ready = expected_subject_id is None or actual_subject_id == expected_subject_id
            term_ready = expected_term_id is None or actual_term_id == expected_term_id

            class_store_ready = True
            if str(snapshot.get("classComboId", "")).strip():
                class_store_ready = len(list(snapshot.get("classOptions", []))) > 0

            subject_store_ready = True
            if str(snapshot.get("subjectComboId", "")).strip():
                subject_store_ready = len(list(snapshot.get("subjectOptions", []))) > 0

            term_store_ready = True
            if str(snapshot.get("termComboId", "")).strip():
                term_store_ready = len(list(snapshot.get("termOptions", []))) > 0

            if (
                grade_ready
                and class_ready
                and subject_ready
                and term_ready
                and class_store_ready
                and subject_store_ready
                and term_store_ready
            ):
                emit_progress(progress_callback, 100.0, progress_message)
                return snapshot
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(progress_callback, max(5.0, elapsed_ratio * 95.0), progress_message)
            page.wait_for_timeout(250)
        return self._finish_scorebook_snapshot_wait(
            last_snapshot,
            last_error,
            timeout_sec,
            "Sổ điểm đồng bộ Khối/Lớp/Môn/Học kỳ",
        )

    def _wait_for_scorebook_permission_snapshot(
        self,
        page: Page,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",
    ) -> Dict[str, object]:
        """Waits until the scorebook body catches up and exposes teacher/permission badges."""
        deadline = time.time() + timeout_sec
        last_snapshot: Dict[str, object] = {}
        last_error: Exception | None = None
        while time.time() < deadline:
            snapshot, snapshot_error = self._best_effort_scorebook_snapshot(page)
            if snapshot is None:
                last_error = snapshot_error
                page.wait_for_timeout(250)
                continue
            last_snapshot = snapshot
            last_error = None
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            class_ready = expected_class_id is None or actual_class_id == expected_class_id
            subject_ready = expected_subject_id is None or actual_subject_id == expected_subject_id
            term_ready = expected_term_id is None or actual_term_id == expected_term_id
            content_ready = bool(
                str(snapshot.get("permissionText", "")).strip()
                or str(snapshot.get("teacherText", "")).strip()
                or int(snapshot.get("commentInputCount", 0) or 0) > 0
            )
            if class_ready and subject_ready and term_ready and content_ready:
                emit_progress(progress_callback, 100.0, progress_message)
                return snapshot
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(progress_callback, max(5.0, elapsed_ratio * 95.0), progress_message)
            page.wait_for_timeout(250)
        return self._finish_scorebook_snapshot_wait(
            last_snapshot,
            last_error,
            timeout_sec,
            "Sổ điểm cập nhật quyền/giáo viên",
        )

    def _resolve_option_id(
        self,
        options: List[ScoreOption],
        current_id: str,
        preferred_id: str,
        label: str,
        notes: List[str],
    ) -> str:
        """Resolves a preferred combo id while tolerating stale GUI selections."""
        valid_ids = {item.option_id for item in options}
        preferred_id = preferred_id.strip()
        current_id = current_id.strip()

        if preferred_id and preferred_id in valid_ids:
            return preferred_id

        if preferred_id and preferred_id not in valid_ids:
            fallback_id = current_id if current_id in valid_ids else (options[0].option_id if options else "")
            if fallback_id:
                notes.append(f"{label} đã đổi dữ liệu trên web, app dùng lựa chọn hiện có gần nhất.")
                return fallback_id

        if current_id and current_id in valid_ids:
            return current_id

        return options[0].option_id if options else ""

    def _resolve_accessible_selection(
        self,
        entries: List[ScorebookAccessEntry],
        preferred_class_id: str = "",
        preferred_subject_id: str = "",
    ) -> Tuple[str, str, List[str]]:
        """Chooses a valid class-subject pair from the discovered permission matrix."""
        return resolve_accessible_selection(
            entries,
            preferred_class_id=preferred_class_id,
            preferred_subject_id=preferred_subject_id,
        )

    def _apply_access_entries_to_context(
        self,
        context: ScorebookContext,
        entries: List[ScorebookAccessEntry],
        grade_id: str,
        term_id: str,
    ) -> ScorebookContext:
        """Attaches discovered permission entries to the returned scorebook context."""
        return apply_access_entries_to_context(
            context,
            entries,
            grade_id=grade_id,
            term_id=term_id,
        )

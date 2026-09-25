"""Chọn khối/học kỳ/lớp/môn trên trang và nạp context."""

from __future__ import annotations

from typing import Dict, List, Tuple

from playwright.sync_api import Page

from ..config import ProgressCallback
from ..models import ScorebookAccessEntry, ScorebookContext
from ..progress import create_subprogress_reporter, emit_progress


class ContextSelectMixin:
    """Chọn khối/học kỳ/lớp/môn trên trang và nạp context."""

    def _select_scorebook_grade_term_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str = "",
        term_id: str = "",
    ) -> Tuple[Dict[str, object], str]:
        """Applies grade and term first, without assuming the old class remains valid."""
        notes: List[str] = []
        target_grade_id = grade_id.strip()
        target_term_id = term_id.strip()
        context_requires_sync = self._requested_scorebook_context_differs(
            snapshot,
            grade_id=target_grade_id,
            term_id=target_term_id,
        )

        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        if target_grade_id:
            grade_options = self._build_options(list(snapshot.get("gradeOptions", [])))
            target_grade_id = self._resolve_option_id(
                grade_options,
                current_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"),
                preferred_id=target_grade_id,
                label="Khối",
                notes=notes,
            )
            grade_combo_id = str(snapshot.get("gradeComboId", "")).strip()
            current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            if grade_combo_id and target_grade_id and current_grade_id != target_grade_id:
                if not self._set_combo_value(page, grade_combo_id, target_grade_id):
                    raise RuntimeError(f"Không thể chọn khối id={target_grade_id} trên Sổ điểm.")
                page.wait_for_timeout(150)
                snapshot = self._wait_for_scorebook_snapshot(page, expected_grade_id=target_grade_id, timeout_sec=6.0)
                actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
                if actual_grade_id != target_grade_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")
                snapshot = self._hydrate_scorebook_snapshot_options(
                    page,
                    snapshot,
                    include_grade=False,
                    include_class=True,
                    include_subject=True,
                    include_term=True,
                )

        if target_term_id:
            term_options = self._build_options(list(snapshot.get("termOptions", [])))
            target_term_id = self._resolve_option_id(
                term_options,
                current_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId"),
                preferred_id=target_term_id,
                label="Học kỳ",
                notes=notes,
            )
            term_combo_id = str(snapshot.get("termComboId", "")).strip()
            current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            if term_combo_id and target_term_id and current_term_id != target_term_id:
                if not self._set_combo_value(page, term_combo_id, target_term_id):
                    raise RuntimeError(f"Không thể chọn học kỳ id={target_term_id} trên Sổ điểm.")
                page.wait_for_timeout(150)
                snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                    expected_term_id=target_term_id,
                    timeout_sec=6.0,
                )
                actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
                if actual_term_id != target_term_id:
                    snapshot = self._wait_for_scorebook_permission_snapshot(
                        page,
                        expected_term_id=target_term_id,
                        timeout_sec=8.0,
                    )
                    actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
                if actual_term_id != target_term_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn học kỳ id={target_term_id}.")
                snapshot = self._hydrate_scorebook_snapshot_options(
                    page,
                    snapshot,
                    include_grade=False,
                    include_class=True,
                    include_subject=True,
                    include_term=True,
                )

        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        return snapshot, " ".join(part for part in notes if part).strip()

    def _select_scorebook_context_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[Dict[str, object], str]:
        """Applies scorebook combo selections directly on the live VNEDU window."""
        notes: List[str] = []
        emit_progress(progress_callback, 5.0, "Đang so sánh ngữ cảnh Khối/Lớp/Môn/Học kỳ...")
        context_requires_sync = self._requested_scorebook_context_differs(
            snapshot,
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
        )
        if context_requires_sync:
            emit_progress(progress_callback, 10.0, "Đang nạp danh sách lựa chọn hiện tại từ Sổ điểm...")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        grade_options = self._build_options(list(snapshot.get("gradeOptions", [])))
        target_grade_id = self._resolve_option_id(
            grade_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId"),
            preferred_id=grade_id,
            label="Khối",
            notes=notes,
        )
        grade_combo_id = str(snapshot.get("gradeComboId", "")).strip()
        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        if grade_combo_id and target_grade_id and current_grade_id != target_grade_id:
            emit_progress(progress_callback, 18.0, "Đang đổi Khối trên Sổ điểm...")
            if not self._set_combo_value(page, grade_combo_id, target_grade_id):
                raise RuntimeError(f"Không thể chọn khối id={target_grade_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=target_grade_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 18.0, 30.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Khối...",
            )
            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            if actual_grade_id != target_grade_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=False,
                include_class=True,
                include_subject=True,
                include_term=True,
            )

        class_options = self._build_options(list(snapshot.get("classOptions", [])))
        target_class_id = self._resolve_option_id(
            class_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),
            preferred_id=class_id,
            label="Lớp",
            notes=notes,
        )
        class_combo_id = str(snapshot.get("classComboId", "")).strip()
        current_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        if class_combo_id and target_class_id and current_class_id != target_class_id:
            emit_progress(progress_callback, 34.0, "Đang đổi Lớp trên Sổ điểm...")
            if not self._set_combo_value(page, class_combo_id, target_class_id):
                raise RuntimeError(f"Không thể chọn lớp id={target_class_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=target_class_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 34.0, 48.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Lớp...",
            )
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            if actual_class_id != target_class_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={target_class_id}.")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=False,
                include_class=True,
                include_subject=True,
                include_term=False,
            )

        subject_options = self._build_options(list(snapshot.get("subjectOptions", [])))
        target_subject_id = self._resolve_option_id(
            subject_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),
            preferred_id=subject_id,
            label="Môn",
            notes=notes,
        )
        subject_combo_id = str(snapshot.get("subjectComboId", "")).strip()
        current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        if subject_combo_id and target_subject_id and current_subject_id != target_subject_id:
            emit_progress(progress_callback, 52.0, "Đang đổi Môn trên Sổ điểm...")
            if not self._set_combo_value(page, subject_combo_id, target_subject_id):
                raise RuntimeError(f"Không thể chọn môn id={target_subject_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=target_subject_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 52.0, 66.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Môn...",
            )
            actual_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
            if actual_subject_id != target_subject_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn môn id={target_subject_id}.")

        term_options = self._build_options(list(snapshot.get("termOptions", [])))
        target_term_id = self._resolve_option_id(
            term_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId"),
            preferred_id=term_id,
            label="Học kỳ",
            notes=notes,
        )
        term_combo_id = str(snapshot.get("termComboId", "")).strip()
        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        if term_combo_id and target_term_id and current_term_id != target_term_id:
            emit_progress(progress_callback, 70.0, "Đang đổi Học kỳ trên Sổ điểm...")
            if not self._set_combo_value(page, term_combo_id, target_term_id):
                raise RuntimeError(f"Không thể chọn học kỳ id={target_term_id} trên Sổ điểm.")
            page.wait_for_timeout(150)
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=target_term_id,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 70.0, 84.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Học kỳ...",
            )
            actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            if actual_term_id != target_term_id:
                snapshot = self._wait_for_scorebook_permission_snapshot(
                    page,
                    expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                    expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                    expected_term_id=target_term_id,
                    timeout_sec=8.0,
                    progress_callback=create_subprogress_reporter(progress_callback, 84.0, 90.0),
                    progress_message="Đang chờ quyền và giáo viên cập nhật sau khi đổi Học kỳ...",
                )
                actual_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            if actual_term_id != target_term_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn học kỳ id={target_term_id}.")

        emit_progress(progress_callback, 92.0, "Đang xác minh ngữ cảnh cuối cùng trên Sổ điểm...")
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
            progress_callback=create_subprogress_reporter(progress_callback, 92.0, 100.0),
            progress_message="Đang xác minh quyền và giáo viên cho ngữ cảnh mới...",
        )
        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )
        emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")
        return snapshot, " ".join(notes).strip()

    def load_scorebook_context(
        self,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, str]:
        """Loads the visible scorebook shell from the live browser."""
        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(
                page,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 30.0),
            )
            self._ensure_scorebook_screen(
                page,
                progress_callback=create_subprogress_reporter(progress_callback, 30.0, 45.0),
            )
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                timeout_sec=8.0,
                progress_callback=create_subprogress_reporter(progress_callback, 45.0, 65.0),
                progress_message="Đang đọc dữ liệu khung Sổ điểm...",
            )
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 65.0, 80.0),
                progress_message="Đang đọc quyền hạn và giáo viên...",
            )
            emit_progress(progress_callback, 82.0, "Đang nạp danh sách Khối/Lớp/Môn/Học kỳ...")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=True,
                include_subject=True,
                include_term=True,
            )
            emit_progress(progress_callback, 94.0, "Đang dựng ngữ cảnh Sổ điểm trong GUI...")
            context = self._build_scorebook_context(snapshot)
            emit_progress(progress_callback, 100.0, "Đã đọc xong dữ liệu Sổ điểm.")
            return context, login_message

    def load_accessible_scorebook_context(
        self,
        username: str = "",
        password: str = "",
    ) -> Tuple[ScorebookContext, str, str]:
        """Loads the current scorebook shell, then filters it to only class-subject pairs with permission."""
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
            )
            grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
            entries = self._discover_accessible_entries_for_current_grade(page, snapshot, grade_id=grade_id, term_id=term_id)
            selection_message_parts: List[str] = []
            if entries:
                class_id, subject_id, fallback_notes = self._resolve_accessible_selection(
                    entries,
                    preferred_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),
                    preferred_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),
                )
                if class_id and subject_id:
                    snapshot, select_notes = self._select_scorebook_context_on_page(
                        page,
                        snapshot,
                        grade_id=grade_id,
                        class_id=class_id,
                        subject_id=subject_id,
                        term_id=term_id,
                    )
                    if select_notes:
                        selection_message_parts.append(select_notes)
                selection_message_parts.extend(fallback_notes)
            context = self._build_scorebook_context(snapshot)
            self._apply_access_entries_to_context(context, entries, grade_id=grade_id, term_id=term_id)
            if entries:
                selection_message_parts.append(
                    f"Đã dò quyền lớp/môn cho {self._option_text_by_id(context.grade_options, grade_id)}: "
                    f"{len(entries)} tổ hợp có thể nhập nhận xét."
                )
            else:
                selection_message_parts.append("Không tìm thấy tổ hợp lớp/môn nào có quyền nhập nhận xét trong khối hiện tại.")
            return context, login_message, " ".join(part for part in selection_message_parts if part).strip()

    def select_scorebook_context(
        self,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, str, str]:
        """Applies the requested scorebook context on the live browser and returns the refreshed shell."""
        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            login_message = self._login_if_needed_on_page(
                page,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 20.0),
            )
            self._ensure_scorebook_screen(
                page,
                progress_callback=create_subprogress_reporter(progress_callback, 20.0, 32.0),
            )
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                timeout_sec=8.0,
                progress_callback=create_subprogress_reporter(progress_callback, 32.0, 45.0),
                progress_message="Đang đọc trạng thái hiện tại của Sổ điểm...",
            )
            snapshot = self._wait_for_scorebook_permission_snapshot(
                page,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 45.0, 55.0),
                progress_message="Đang đọc quyền trước khi đổi combobox...",
            )
            snapshot, selection_message = self._select_scorebook_context_on_page(
                page,
                snapshot,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                progress_callback=create_subprogress_reporter(progress_callback, 55.0, 100.0),
            )
            emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")
            return self._build_scorebook_context(snapshot), login_message, selection_message

    def select_accessible_scorebook_context(
        self,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        username: str = "",
        password: str = "",
    ) -> Tuple[ScorebookContext, str, str]:
        """Applies grade-term, scans permission matrix, then lands on one valid class-subject pair only."""
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
            entries = self._discover_accessible_entries_for_current_grade(
                page,
                snapshot,
                grade_id=current_grade_id,
                term_id=current_term_id,
            )
            effective_class_id, effective_subject_id, fallback_notes = self._resolve_accessible_selection(
                entries,
                preferred_class_id=class_id,
                preferred_subject_id=subject_id,
            )
            if effective_class_id and effective_subject_id:
                snapshot, post_select_message = self._select_scorebook_context_on_page(
                    page,
                    snapshot,
                    grade_id=current_grade_id,
                    class_id=effective_class_id,
                    subject_id=effective_subject_id,
                    term_id=current_term_id,
                )
                if post_select_message:
                    selection_message = f"{selection_message} {post_select_message}".strip()
            context = self._build_scorebook_context(snapshot)
            self._apply_access_entries_to_context(
                context,
                entries,
                grade_id=current_grade_id,
                term_id=current_term_id,
            )
            final_message_parts = [selection_message, *fallback_notes]
            if entries:
                final_message_parts.append(
                    f"Đã dò quyền lớp/môn cho {self._option_text_by_id(context.grade_options, current_grade_id)}: "
                    f"{len(entries)} tổ hợp có thể nhập nhận xét."
                )
            else:
                final_message_parts.append("Không tìm thấy tổ hợp lớp/môn nào có quyền nhập nhận xét trong khối hiện tại.")
            return context, login_message, " ".join(part for part in final_message_parts if part).strip()

    def discover_accessible_entries_for_current_context(
        self,
        username: str = "",
        password: str = "",
        expected_grade_id: str = "",
        expected_term_id: str = "",
    ) -> Tuple[ScorebookContext, List[ScorebookAccessEntry], str]:
        """Scans permission entries for the current live scorebook grade-term without changing combo selections."""
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
            context = self._build_scorebook_context(snapshot)
            grade_id = context.selected_grade_id
            term_id = context.selected_term_id
            if expected_grade_id.strip() and grade_id != expected_grade_id.strip():
                raise RuntimeError("Ngữ cảnh live Chrome đã đổi khối trước khi quét quyền nền hoàn tất.")
            if expected_term_id.strip() and term_id != expected_term_id.strip():
                raise RuntimeError("Ngữ cảnh live Chrome đã đổi học kỳ trước khi quét quyền nền hoàn tất.")
            entries = self._discover_accessible_entries_for_current_grade(
                page,
                snapshot,
                grade_id=grade_id,
                term_id=term_id,
            )
            self._apply_access_entries_to_context(context, entries, grade_id=grade_id, term_id=term_id)
            return context, entries, login_message

    def open_target_page(self, progress_callback: ProgressCallback | None = None) -> str:
        """Opens the VNEDU target page in the connected browser session."""
        emit_progress(progress_callback, 5.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            emit_progress(progress_callback, 35.0, "Đang mở trang VNEDU...")
            self._goto_target_page(page)
            emit_progress(progress_callback, 100.0, "Đã mở trang VNEDU.")
            return page.url

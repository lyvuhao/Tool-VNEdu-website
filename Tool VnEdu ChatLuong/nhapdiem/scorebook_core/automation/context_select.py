"""Chọn khối/lớp/môn/học kỳ và nạp context."""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from playwright.sync_api import Page

from nhanxet.config import ProgressCallback
from nhanxet.models import ScorebookContext, ScoreOption
from nhanxet.progress import create_subprogress_reporter, emit_progress


class ContextSelectMixin:
    """Chọn khối/lớp/môn/học kỳ và nạp context."""

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
                current_snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=grade_id or None,
                    expected_class_id=class_id,
                    expected_term_id=term_id or None,
                    required_store_keys=("subject",),
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
                merge_existing=False,
                preserve_selected_if_missing=False,
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

    def _wait_for_scorebook_snapshot(
        self,
        page: Page,
        expected_grade_id: str | None = None,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        stale_class_option_id: str = "",
        required_store_keys: Tuple[str, ...] | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm đồng bộ dữ liệu...",
    ) -> Dict[str, object]:
        """Waits until scorebook combobox state reaches the expected ids."""
        deadline = time.time() + timeout_sec
        last_snapshot: Dict[str, object] = {}
        last_error: Exception | None = None
        if required_store_keys is None:
            required_store_key_set = {"class", "subject", "term"}
        else:
            required_store_key_set = {str(key).strip().lower() for key in required_store_keys if str(key).strip()}
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
            if "class" in required_store_key_set and str(snapshot.get("classComboId", "")).strip():
                class_store_ready = bool(self._snapshot_option_ids(snapshot, "classOptions")) and self._snapshot_store_excludes_stale_option(
                    snapshot,
                    options_key="classOptions",
                    stale_option_id=stale_class_option_id,
                    actual_selected_id=actual_class_id,
                )

            subject_store_ready = True
            if "subject" in required_store_key_set and str(snapshot.get("subjectComboId", "")).strip():
                subject_store_ready = len(list(snapshot.get("subjectOptions", []))) > 0

            term_store_ready = True
            if "term" in required_store_key_set and str(snapshot.get("termComboId", "")).strip():
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

    def _resolve_option_id(
        self,
        options: List[ScoreOption],
        current_id: str,
        preferred_id: str,
        label: str,
        notes: List[str],
        fail_closed_on_missing_preferred: bool = False,
    ) -> str:
        """Resolves a preferred combo id while tolerating stale GUI selections."""
        valid_ids = {item.option_id for item in options}
        preferred_id = preferred_id.strip()
        current_id = current_id.strip()

        if preferred_id and preferred_id in valid_ids:
            return preferred_id

        if preferred_id and preferred_id not in valid_ids:
            if fail_closed_on_missing_preferred:
                notes.append(f"{label} đã đổi dữ liệu trên web và không còn khớp với lựa chọn đang yêu cầu.")
                return ""
            fallback_id = current_id if current_id in valid_ids else (options[0].option_id if options else "")
            if fallback_id:
                notes.append(f"{label} đã đổi dữ liệu trên web, app dùng lựa chọn hiện có gần nhất.")
                return fallback_id

        if current_id and current_id in valid_ids:
            return current_id

        return options[0].option_id if options else ""

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
        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        requested_grade_diff = bool(target_grade_id and current_grade_id != target_grade_id)
        requested_term_diff = bool(target_term_id and current_term_id != target_term_id)
        context_requires_sync = requested_grade_diff or requested_term_diff

        if requested_grade_diff:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=True,
                include_class=False,
                include_subject=False,
                include_term=False,
            )
        elif requested_term_diff:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=False,
                include_class=False,
                include_subject=False,
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
                snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=target_grade_id,
                    required_store_keys=("term",) if target_term_id else (),
                    timeout_sec=6.0,
                )
                actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
                if actual_grade_id != target_grade_id:
                    raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")
                if target_term_id:
                    snapshot = self._hydrate_scorebook_snapshot_options(
                        page,
                        snapshot,
                        include_grade=False,
                        include_class=False,
                        include_subject=False,
                        include_term=True,
                        merge_existing=False,
                        preserve_selected_if_missing=False,
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
                snapshot = self._wait_for_scorebook_snapshot(
                    page,
                    expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                    expected_term_id=target_term_id,
                    required_store_keys=(),
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

        if context_requires_sync:
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=requested_grade_diff,
                include_class=False,
                include_subject=False,
                include_term=requested_grade_diff or requested_term_diff,
                merge_existing=False,
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
        current_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
        current_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        current_term_id = self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId")
        stale_class_id_before_grade_change = current_class_id
        requested_grade_diff = bool(grade_id.strip() and grade_id.strip() != current_grade_id)
        requested_class_diff = bool(class_id.strip() and class_id.strip() != current_class_id)
        requested_subject_diff = bool(subject_id.strip() and subject_id.strip() != current_subject_id)
        requested_term_diff = bool(term_id.strip() and term_id.strip() != current_term_id)
        context_requires_sync = (
            requested_grade_diff
            or requested_class_diff
            or requested_subject_diff
            or requested_term_diff
        )
        if context_requires_sync:
            emit_progress(progress_callback, 10.0, "Đang nạp danh sách lựa chọn hiện tại từ Sổ điểm...")
            snapshot = self._hydrate_scorebook_snapshot_options(
                page,
                snapshot,
                include_grade=requested_grade_diff,
                include_class=requested_grade_diff or requested_class_diff,
                include_subject=requested_grade_diff or requested_subject_diff,
                include_term=requested_grade_diff or requested_term_diff,
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
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=target_grade_id,
                stale_class_option_id=stale_class_id_before_grade_change,
                required_store_keys=("class",),
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 18.0, 30.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Khối...",
            )
            actual_grade_id = self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId")
            if actual_grade_id != target_grade_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn khối id={target_grade_id}.")
            snapshot = self._wait_for_hydrated_scorebook_options(
                page,
                snapshot,
                options_key="classOptions",
                include_grade=False,
                include_class=True,
                include_subject=True,
                include_term=True,
                expected_grade_id=target_grade_id,
                stale_option_id=stale_class_id_before_grade_change,
                timeout_sec=5.0,
                progress_callback=create_subprogress_reporter(progress_callback, 30.0, 34.0),
                progress_message="Đang nạp lại danh sách Lớp/Môn sau khi đổi Khối...",
            )

        class_options = self._build_options(list(snapshot.get("classOptions", [])))
        requested_class_id = class_id.strip()
        target_class_id = self._resolve_option_id(
            class_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId"),
            preferred_id=class_id,
            label="Lớp",
            notes=notes,
            fail_closed_on_missing_preferred=bool(requested_class_id),
        )
        if requested_class_id and not target_class_id:
            available_classes = ", ".join(option.option_text for option in class_options[:6])
            available_suffix = (
                f" Các lớp hiện có sau khi đổi Khối: {available_classes}."
                if available_classes
                else " Web không trả về danh sách Lớp hợp lệ sau khi đổi Khối."
            )
            raise RuntimeError("Lớp đang chọn không còn tồn tại trong ngữ cảnh hiện tại." + available_suffix)
        class_combo_id = str(snapshot.get("classComboId", "")).strip()
        current_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
        if class_combo_id and target_class_id and current_class_id != target_class_id:
            emit_progress(progress_callback, 34.0, "Đang đổi Lớp trên Sổ điểm...")
            if not self._set_combo_value(page, class_combo_id, target_class_id):
                raise RuntimeError(f"Không thể chọn lớp id={target_class_id} trên Sổ điểm.")
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=target_class_id,
                required_store_keys=("subject",),
                timeout_sec=6.0,
                progress_callback=create_subprogress_reporter(progress_callback, 34.0, 48.0),
                progress_message="Đang đồng bộ dữ liệu sau khi đổi Lớp...",
            )
            actual_class_id = self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId")
            if actual_class_id != target_class_id:
                raise RuntimeError(f"Không thể đồng bộ Sổ điểm sau khi chọn lớp id={target_class_id}.")
            snapshot = self._wait_for_hydrated_scorebook_options(
                page,
                snapshot,
                options_key="subjectOptions",
                include_grade=False,
                include_class=True,
                include_subject=True,
                include_term=False,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=target_class_id,
                timeout_sec=5.0,
                progress_callback=create_subprogress_reporter(progress_callback, 48.0, 52.0),
                progress_message="Đang nạp lại danh sách Môn sau khi đổi Lớp...",
            )

        subject_options = self._build_options(list(snapshot.get("subjectOptions", [])))
        requested_subject_id = subject_id.strip()
        target_subject_id = self._resolve_option_id(
            subject_options,
            current_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId"),
            preferred_id=subject_id,
            label="Môn",
            notes=notes,
            fail_closed_on_missing_preferred=bool(requested_subject_id),
        )
        if requested_subject_id and not target_subject_id:
            available_subjects = ", ".join(option.option_text for option in subject_options[:6])
            available_suffix = (
                f" Các môn hiện có cho Lớp này: {available_subjects}."
                if available_subjects
                else " Web không trả về danh sách Môn hợp lệ cho Lớp hiện tại."
            )
            raise RuntimeError("Môn đang chọn không còn tồn tại trong ngữ cảnh hiện tại." + available_suffix)
        subject_combo_id = str(snapshot.get("subjectComboId", "")).strip()
        current_subject_id = self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId")
        if subject_combo_id and target_subject_id and current_subject_id != target_subject_id:
            emit_progress(progress_callback, 52.0, "Đang đổi Môn trên Sổ điểm...")
            if not self._set_combo_value(page, subject_combo_id, target_subject_id):
                raise RuntimeError(f"Không thể chọn môn id={target_subject_id} trên Sổ điểm.")
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=target_subject_id,
                required_store_keys=(),
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
            snapshot = self._wait_for_scorebook_snapshot(
                page,
                expected_grade_id=self._effective_snapshot_selected_id(snapshot, "currentGradeId", "hiddenGradeId") or None,
                expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
                expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
                expected_term_id=target_term_id,
                required_store_keys=(),
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
                include_grade=requested_grade_diff,
                include_class=requested_grade_diff or requested_class_diff,
                include_subject=requested_grade_diff or requested_class_diff or requested_subject_diff,
                include_term=requested_grade_diff or requested_term_diff,
                merge_existing=False,
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
                only_when_incomplete=True,
            )
            emit_progress(progress_callback, 94.0, "Đang dựng ngữ cảnh Sổ điểm trong GUI...")
            context = self._build_scorebook_context(snapshot)
            emit_progress(progress_callback, 100.0, "Đã đọc xong dữ liệu Sổ điểm.")
            return context, login_message

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
            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 5.0, 100.0),
            )
            emit_progress(progress_callback, 100.0, "Đã áp xong ngữ cảnh Sổ điểm.")
            return self._build_scorebook_context(snapshot), login_message, selection_message

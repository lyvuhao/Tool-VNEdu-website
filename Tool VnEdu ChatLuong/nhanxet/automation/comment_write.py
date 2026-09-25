"""Ghi nhận xét vào bảng và xác minh lưu trên server."""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Tuple

from playwright.sync_api import Page

from ..config import ProgressCallback
from ..models import CommentRule, CommentWriteResult, CommentWriteRow, ScorebookContext
from ..progress import create_subprogress_reporter, emit_progress
from ..rules import compile_comment_rules
from ..write_plan import (
    build_comment_write_payload,
    build_comment_write_rows_from_live_data,
    evaluate_server_save_verification,
    ready_comment_write_rows,
    select_relevant_server_save_requests,
    summarize_comment_write_result,
)


class CommentWriteMixin:
    """Ghi nhận xét vào bảng và xác minh lưu trên server."""

    def _extract_live_write_rows(
        self,
        page: Page,
        context: ScorebookContext,
        source_column_key: str,
        comment_column_key: str,
    ) -> List[Dict[str, object]]:
        """Extracts all visible student rows for internal analysis/write from the active scorebook table."""
        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)
        comment_schema = self._find_schema_by_key(context.column_schemas, comment_column_key)
        if source_schema is None:
            raise RuntimeError(f"Không tìm thấy schema cho cột điểm `{source_column_key}`.")
        if comment_schema is None:
            raise RuntimeError(f"Không tìm thấy schema cho cột nhận xét `{comment_column_key}`.")

        student_name_indices = [
            schema.leaf_index
            for schema in sorted(context.column_schemas, key=lambda item: item.leaf_index)
            if schema.role_hint == "student_name"
        ]
        student_code_schema = next(
            (schema for schema in context.column_schemas if schema.role_hint == "student_code"),
            None,
        )
        student_code_index = student_code_schema.leaf_index if student_code_schema is not None else 1

        return list(
            page.evaluate(
                """({ sourceIndex, commentIndex, studentNameIndices, studentCodeIndex }) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                if (!root) return [];
                const table = root.querySelector('table.table.tablefix');
                if (!table) return [];

                const normalizeText = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const cellValue = (cell) => {
                    if (!cell) return '';
                    const input = cell.querySelector('input, textarea, select');
                    if (input) return normalizeText(input.value || '');
                    return normalizeText(cell.innerText || cell.textContent || '');
                };

                return Array.from(table.querySelectorAll('tbody tr')).map((tr, rowIndex) => {
                    const cells = Array.from(tr.children);
                    const scoreCell = cells[sourceIndex];
                    const commentCell = cells[commentIndex];
                    const commentInput = commentCell ? commentCell.querySelector('input, textarea, select') : null;
                    const nameParts = studentNameIndices
                        .map(index => normalizeText(cells[index]?.innerText || cells[index]?.textContent || ''))
                        .filter(Boolean);
                    let studentName = nameParts.join(' ');
                    if (!studentName) {
                        const fallback = cells
                            .slice(0, 6)
                            .map(cell => normalizeText(cell.innerText || cell.textContent || ''))
                            .find(text => text && !/^\\d+$/.test(text) && !/\\d{2}\\/\\d{2}\\/\\d{4}/.test(text));
                        studentName = fallback || '';
                    }
                    return {
                        rowIndex: rowIndex + 1,
                        rowId: tr.id || '',
                        studentCode: normalizeText(cells[studentCodeIndex]?.innerText || cells[studentCodeIndex]?.textContent || ''),
                        studentName,
                        sourceValue: cellValue(scoreCell),
                        currentComment: cellValue(commentCell),
                        commentInputName: commentInput ? (commentInput.name || commentInput.id || '') : '',
                    };
                });
            }""",
                {
                    "sourceIndex": source_schema.leaf_index,
                    "commentIndex": comment_schema.leaf_index,
                    "studentNameIndices": student_name_indices,
                    "studentCodeIndex": student_code_index,
                },
            )
        )

    def build_comment_write_queue(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        source_column_key: str,
        comment_column_key: str,
        rules: List[CommentRule],
        username: str = "",
        password: str = "",
        allow_overwrite_existing_comment: bool = False,
    ) -> Tuple[ScorebookContext, List[CommentWriteRow], str, str]:
        """Builds the internal write queue that would be written by the current rule set."""
        compiled_rules = compile_comment_rules(rules)
        if not compiled_rules:
            raise RuntimeError("Chưa có rule hợp lệ để phân tích dữ liệu ghi nhận xét.")

        with self._open_page() as page:
            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
            )
            context, write_rows = self._build_comment_write_queue_on_page(
                page,
                snapshot,
                source_column_key=source_column_key,
                comment_column_key=comment_column_key,
                compiled_rules=compiled_rules,
                allow_overwrite_existing_comment=allow_overwrite_existing_comment,
            )
            return context, write_rows, login_message, selection_message

    def _open_selected_scorebook_snapshot_on_page(
        self,
        page: Page,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[Dict[str, object], str, str]:
        """Logs in if needed, opens the scorebook screen, and applies one target context on the current page."""
        emit_progress(progress_callback, 5.0, "Đang chuẩn bị phiên VNEDU để đọc dữ liệu...")
        login_message = self._login_if_needed_on_page(
            page,
            username=username,
            password=password,
            progress_callback=create_subprogress_reporter(progress_callback, 5.0, 28.0),
        )
        self._ensure_scorebook_screen(
            page,
            progress_callback=create_subprogress_reporter(progress_callback, 28.0, 42.0),
        )
        snapshot = self._wait_for_scorebook_snapshot(
            page,
            timeout_sec=8.0,
            progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),
            progress_message="Đang đọc dữ liệu khung Sổ điểm...",
        )
        snapshot = self._wait_for_scorebook_permission_snapshot(
            page,
            expected_class_id=self._effective_snapshot_selected_id(snapshot, "currentClassId", "hiddenClassId") or None,
            expected_subject_id=self._effective_snapshot_selected_id(snapshot, "currentSubjectId", "hiddenSubjectId") or None,
            expected_term_id=self._effective_snapshot_selected_id(snapshot, "currentTermId", "hiddenTermId") or None,
            timeout_sec=6.0,
            progress_callback=create_subprogress_reporter(progress_callback, 58.0, 70.0),
            progress_message="Đang đọc quyền và thông tin giáo viên...",
        )
        snapshot, selection_message = self._select_scorebook_context_on_page(
            page,
            snapshot,
            grade_id=grade_id,
            class_id=class_id,
            subject_id=subject_id,
            term_id=term_id,
            progress_callback=create_subprogress_reporter(progress_callback, 70.0, 100.0),
        )
        return snapshot, login_message, selection_message

    def _build_comment_write_queue_on_page(
        self,
        page: Page,
        snapshot: Dict[str, object],
        source_column_key: str,
        comment_column_key: str,
        compiled_rules: List[Tuple[Callable[[object], bool], str, str]],
        allow_overwrite_existing_comment: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, List[CommentWriteRow]]:
        """Builds one comment write queue from the already-selected live scorebook page."""
        emit_progress(progress_callback, 10.0, "Đang phân tích cấu trúc Sổ điểm...")
        context = self._build_scorebook_context(snapshot)
        emit_progress(progress_callback, 35.0, "Đang đọc dữ liệu từng học sinh từ live Chrome...")
        live_rows = self._extract_live_write_rows(
            page,
            context,
            source_column_key=source_column_key,
            comment_column_key=comment_column_key,
        )
        source_schema = self._find_schema_by_key(context.column_schemas, source_column_key)
        write_rows = build_comment_write_rows_from_live_data(
            live_rows,
            source_column_key=source_column_key,
            source_column_name=source_schema.display_name if source_schema is not None else source_column_key,
            compiled_rules=compiled_rules,
            allow_overwrite_existing_comment=allow_overwrite_existing_comment,
        )
        emit_progress(progress_callback, 100.0, "Đã phân tích xong hàng chờ ghi nhận xét.")
        return context, write_rows

    def _apply_comment_payload_to_active_scorebook(
        self,
        page: Page,
        payload: List[Dict[str, str]],
        auto_save: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, object]:
        """Writes one prepared payload into the active scorebook window in batches so the GUI can reflect live progress."""
        if not payload:
            return {"updated": [], "failed": [], "saveClicked": False}

        batch_size = 1 if len(payload) <= 12 else (3 if len(payload) <= 30 else 5)
        updated_input_names: List[str] = []
        failed_input_names: List[str] = []
        save_clicked = False

        emit_progress(progress_callback, 5.0, "Đang ghi dữ liệu lên các ô nhận xét...")
        for batch_start in range(0, len(payload), batch_size):
            batch_rows = payload[batch_start : batch_start + batch_size]
            batch_result = dict(
                page.evaluate(
                    """({ rows, finalizeMode }) => {
                    const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                    const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                    const root = active || wins[wins.length - 1];
                    if (!root) {
                        return { updated: [], failed: rows.map(item => item.inputName), saveClicked: false };
                    }

                    const setValue = (input, value) => {
                        const proto = input.tagName === 'TEXTAREA'
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                        const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (nativeSetter && nativeSetter.set) {
                            nativeSetter.set.call(input, value);
                        } else {
                            input.value = value;
                        }
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    };

                    const updated = [];
                    const failed = [];
                    for (const row of rows) {
                        const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;
                        const input = root.querySelector(selector);
                        if (!input) {
                            failed.push(row.inputName);
                            continue;
                        }
                        try {
                            input.focus();
                            setValue(input, row.text);
                            updated.push(row.inputName);
                        } catch (error) {
                            failed.push(row.inputName);
                        }
                    }

                    let saveClicked = false;
                    if (finalizeMode === 'save') {
                        const saveButton = Array.from(root.querySelectorAll('button, span, a, div'))
                            .find(el => /^lưu$/i.test((el.innerText || '').trim()));
                        if (saveButton) {
                            saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                            saveClicked = true;
                        }
                    } else if (finalizeMode === 'blur') {
                        const firstReadonlyCell = root.querySelector('table.table.tablefix tbody tr td');
                        if (firstReadonlyCell) {
                            firstReadonlyCell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                        }
                    }
                    return { updated, failed, saveClicked };
                }""",
                    {"rows": batch_rows, "finalizeMode": "none"},
                )
            )
            updated_input_names.extend(str(item).strip() for item in list(batch_result.get("updated", [])) if str(item).strip())
            failed_input_names.extend(str(item).strip() for item in list(batch_result.get("failed", [])) if str(item).strip())
            processed_rows = min(batch_start + len(batch_rows), len(payload))
            emit_progress(
                progress_callback,
                10.0 + ((processed_rows / max(len(payload), 1)) * 78.0),
                f"Đang ghi nhận xét {processed_rows}/{len(payload)} học sinh...",
            )

        emit_progress(
            progress_callback,
            92.0,
            "Đang bấm Lưu dữ liệu..." if auto_save else "Đang chốt dữ liệu trên ô nhận xét...",
        )
        finalize_result = dict(
            page.evaluate(
                """({ finalizeMode }) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                if (!root) {
                    return { updated: [], failed: [], saveClicked: false };
                }

                let saveClicked = false;
                if (finalizeMode === 'save') {
                    const saveButton = Array.from(root.querySelectorAll('button, span, a, div'))
                        .find(el => /^lưu$/i.test((el.innerText || '').trim()));
                    if (saveButton) {
                        saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                        saveClicked = true;
                    }
                } else if (finalizeMode === 'blur') {
                    const firstReadonlyCell = root.querySelector('table.table.tablefix tbody tr td');
                    if (firstReadonlyCell) {
                        firstReadonlyCell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    }
                }
                return { updated: [], failed: [], saveClicked };
            }""",
                {"finalizeMode": ("save" if auto_save else "blur")},
            )
        )
        save_clicked = bool(finalize_result.get("saveClicked"))
        emit_progress(progress_callback, 100.0, "Đã điền xong dữ liệu vào các ô nhận xét.")
        return {
            "updated": list(dict.fromkeys(updated_input_names)),
            "failed": list(dict.fromkeys(failed_input_names)),
            "saveClicked": save_clicked,
        }

    def _arm_scorebook_save_monitor(self, page: Page) -> int:
        """Installs or resets one browser-side monitor that records save requests after the next click."""
        return int(
            page.evaluate(
                """() => {
                const previewText = (value) => {
                    if (value == null) return '';
                    try {
                        return String(value).slice(0, 400);
                    } catch (error) {
                        return '';
                    }
                };

                if (!window.__codexScorebookSaveMonitor) {
                    const monitor = {
                        nextRunId: 1,
                        requests: [],
                    };

                    const originalOpen = XMLHttpRequest.prototype.open;
                    const originalSend = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                        this.__codexSaveMethod = method;
                        this.__codexSaveUrl = url;
                        return originalOpen.call(this, method, url, ...rest);
                    };
                    XMLHttpRequest.prototype.send = function(body) {
                        const runId = monitor.activeRunId || 0;
                        const request = {
                            runId,
                            transport: 'xhr',
                            method: previewText(this.__codexSaveMethod || 'GET').toUpperCase(),
                            url: previewText(this.__codexSaveUrl || ''),
                            bodyPreview: previewText(body),
                            startedAt: Date.now(),
                            finished: false,
                            status: 0,
                            responsePreview: '',
                        };
                        monitor.requests.push(request);
                        const finish = () => {
                            if (request.finished) return;
                            request.finished = true;
                            request.status = Number(this.status || 0);
                            request.finishedAt = Date.now();
                            try {
                                request.responsePreview = previewText(this.responseText || '');
                            } catch (error) {
                                request.responsePreview = '';
                            }
                        };
                        this.addEventListener('loadend', finish);
                        this.addEventListener('error', finish);
                        this.addEventListener('abort', finish);
                        return originalSend.call(this, body);
                    };

                    if (typeof window.fetch === 'function') {
                        const originalFetch = window.fetch.bind(window);
                        window.fetch = function(input, init) {
                            const runId = monitor.activeRunId || 0;
                            const request = {
                                runId,
                                transport: 'fetch',
                                method: previewText((init && init.method) || 'GET').toUpperCase(),
                                url: previewText(typeof input === 'string' ? input : ((input && input.url) || '')),
                                bodyPreview: previewText((init && init.body) || ''),
                                startedAt: Date.now(),
                                finished: false,
                                status: 0,
                                responsePreview: '',
                            };
                            monitor.requests.push(request);
                            return originalFetch(input, init).then(
                                async (response) => {
                                    request.status = Number(response.status || 0);
                                    request.finished = true;
                                    request.finishedAt = Date.now();
                                    try {
                                        const clone = response.clone();
                                        request.responsePreview = previewText(await clone.text());
                                    } catch (error) {
                                        request.responsePreview = '';
                                    }
                                    return response;
                                },
                                (error) => {
                                    request.finished = true;
                                    request.finishedAt = Date.now();
                                    request.responsePreview = previewText(error && error.message);
                                    throw error;
                                }
                            );
                        };
                    }

                    window.__codexScorebookSaveMonitor = monitor;
                }

                const monitor = window.__codexScorebookSaveMonitor;
                const runId = Number(monitor.nextRunId || 1);
                monitor.nextRunId = runId + 1;
                monitor.activeRunId = runId;
                monitor.requests = monitor.requests.filter(item => Number(item.runId || 0) !== runId);
                return runId;
            }"""
            )
        )

    def _read_scorebook_save_monitor_requests(self, page: Page, run_id: int) -> List[Dict[str, object]]:
        """Returns browser-observed save requests for one armed save run id."""
        return list(
            page.evaluate(
                """(runId) => {
                const monitor = window.__codexScorebookSaveMonitor;
                if (!monitor) return [];
                return (monitor.requests || [])
                    .filter(item => Number(item.runId || 0) === Number(runId || 0))
                    .map(item => ({
                        transport: String(item.transport || ''),
                        method: String(item.method || ''),
                        url: String(item.url || ''),
                        bodyPreview: String(item.bodyPreview || ''),
                        finished: Boolean(item.finished),
                        status: Number(item.status || 0),
                        responsePreview: String(item.responsePreview || ''),
                    }));
            }""",
                run_id,
            )
        )

    def _verify_scorebook_save_on_server(
        self,
        page: Page,
        save_monitor_run_id: int,
        auto_save: bool,
        save_clicked: bool,
        payload_input_names: List[str],
        timeout_sec: float = 8.0,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, object]:
        """Waits for server-side save requests and classifies the save outcome."""
        if not auto_save or not save_clicked:
            emit_progress(progress_callback, 100.0, "Không cần chờ xác minh lưu tự động.")
            save_verified, verification_mode, verification_detail = evaluate_server_save_verification(
                auto_save_requested=auto_save,
                save_clicked=save_clicked,
                save_requests=[],
                request_markers=payload_input_names,
            )
            return {
                "saveVerified": save_verified,
                "saveVerificationMode": verification_mode,
                "saveVerificationDetail": verification_detail,
            }

        deadline = time.time() + timeout_sec
        latest_requests: List[Dict[str, object]] = []
        while time.time() < deadline:
            latest_requests = self._read_scorebook_save_monitor_requests(page, save_monitor_run_id)
            relevant_requests = select_relevant_server_save_requests(
                latest_requests,
                request_markers=payload_input_names,
            )
            if relevant_requests and all(bool(request.get("finished")) for request in relevant_requests):
                emit_progress(progress_callback, 100.0, "Đã nhận phản hồi lưu dữ liệu từ server.")
                break
            elapsed_ratio = min((time.time() - (deadline - timeout_sec)) / max(timeout_sec, 0.1), 1.0)
            emit_progress(
                progress_callback,
                max(10.0, elapsed_ratio * 95.0),
                "Đang chờ VNEDU phản hồi thao tác Lưu...",
            )
            page.wait_for_timeout(250)

        save_verified, verification_mode, verification_detail = evaluate_server_save_verification(
            auto_save_requested=auto_save,
            save_clicked=save_clicked,
            save_requests=latest_requests,
            request_markers=payload_input_names,
        )
        emit_progress(progress_callback, 100.0, "Đã hoàn tất xác minh lưu dữ liệu.")
        return {
            "saveVerified": save_verified,
            "saveVerificationMode": verification_mode,
            "saveVerificationDetail": verification_detail,
        }

    def _read_comment_payload_values(
        self,
        page: Page,
        payload: List[Dict[str, str]],
    ) -> Dict[str, object]:
        """Reads the current DOM values for one prepared write payload from the active scorebook window."""
        return dict(
            page.evaluate(
                """(rows) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                const values = {};
                if (!root) return values;
                for (const row of rows) {
                    const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;
                    const input = root.querySelector(selector);
                    values[row.inputName] = input ? String(input.value || '') : '';
                }
                return values;
            }""",
                payload,
            )
        )

    def apply_comment_write_rows(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        write_rows: List[CommentWriteRow],
        username: str = "",
        password: str = "",
        auto_save: bool = True,
    ) -> Tuple[ScorebookContext, CommentWriteResult, str, str]:
        """Writes analyzed rows back into the live scorebook and verifies the updated values."""
        rows_to_apply = ready_comment_write_rows(write_rows)
        if not rows_to_apply:
            raise RuntimeError("Không có dòng nào trong hàng chờ ghi sẵn sàng để nhập nhận xét.")

        with self._open_page() as page:
            _snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
            )
            context, result = self._apply_comment_write_rows_on_page(
                page,
                write_rows,
                auto_save=auto_save,
            )
            return context, result, login_message, selection_message

    def _apply_comment_write_rows_on_page(
        self,
        page: Page,
        write_rows: List[CommentWriteRow],
        auto_save: bool = True,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, CommentWriteResult]:
        """Writes one analyzed comment queue back into the already-selected live scorebook page."""
        rows_to_apply = ready_comment_write_rows(write_rows)
        if not rows_to_apply:
            raise RuntimeError("Không có dòng nào trong hàng chờ ghi sẵn sàng để nhập nhận xét.")

        emit_progress(progress_callback, 8.0, "Đang chuẩn bị dữ liệu để ghi lên cột nhận xét...")
        payload = build_comment_write_payload(rows_to_apply)
        emit_progress(progress_callback, 16.0, "Đang gắn bộ theo dõi thao tác Lưu...")
        save_monitor_run_id = self._arm_scorebook_save_monitor(page)
        result_payload = self._apply_comment_payload_to_active_scorebook(
            page,
            payload,
            auto_save=auto_save,
            progress_callback=create_subprogress_reporter(progress_callback, 16.0, 68.0),
        )
        result_payload.update(
            self._verify_scorebook_save_on_server(
                page,
                save_monitor_run_id=save_monitor_run_id,
                auto_save=auto_save,
                save_clicked=bool(result_payload.get("saveClicked")),
                payload_input_names=[str(item.get("inputName", "")).strip() for item in payload],
                progress_callback=create_subprogress_reporter(progress_callback, 68.0, 84.0),
            )
        )
        emit_progress(progress_callback, 88.0, "Đang xác minh lại dữ liệu vừa ghi trên giao diện...")
        page.wait_for_timeout(800)
        verification_map = self._read_comment_payload_values(
            page,
            payload,
        )
        emit_progress(progress_callback, 94.0, "Đang tải lại ngữ cảnh Sổ điểm sau khi ghi...")
        context = self._build_scorebook_context(self._scorebook_snapshot(page))
        result = summarize_comment_write_result(
            write_rows,
            rows_to_apply,
            result_payload=result_payload,
            verification_map=verification_map,
        )
        emit_progress(progress_callback, 100.0, "Đã hoàn tất thao tác ghi nhận xét.")
        return context, result

    def analyze_and_apply_comment_rows(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        source_column_key: str,
        comment_column_key: str,
        rules: List[CommentRule],
        username: str = "",
        password: str = "",
        auto_save: bool = True,
        allow_overwrite_existing_comment: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[
        ScorebookContext,
        List[CommentWriteRow],
        str,
        str,
        ScorebookContext,
        CommentWriteResult | None,
        str,
        str,
    ]:
        """Runs analyze and apply on one live page session to avoid reopening the same scorebook twice."""
        emit_progress(progress_callback, 3.0, "Đang kiểm tra rule và chuẩn bị ghi nhận xét...")
        compiled_rules = compile_comment_rules(rules)
        if not compiled_rules:
            raise RuntimeError("Chưa có rule hợp lệ để phân tích dữ liệu ghi nhận xét.")

        emit_progress(progress_callback, 8.0, "Đang kết nối live Chrome qua CDP...")
        with self._open_page() as page:
            snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 8.0, 42.0),
            )
            queue_context, write_rows = self._build_comment_write_queue_on_page(
                page,
                snapshot,
                source_column_key=source_column_key,
                comment_column_key=comment_column_key,
                compiled_rules=compiled_rules,
                allow_overwrite_existing_comment=allow_overwrite_existing_comment,
                progress_callback=create_subprogress_reporter(progress_callback, 42.0, 58.0),
            )
            if not ready_comment_write_rows(write_rows):
                emit_progress(progress_callback, 100.0, "Không có dòng nào sẵn sàng để ghi nhận xét.")
                return (
                    queue_context,
                    write_rows,
                    login_message,
                    selection_message,
                    queue_context,
                    None,
                    "",
                    "",
                )
            apply_context, apply_result = self._apply_comment_write_rows_on_page(
                page,
                write_rows,
                auto_save=auto_save,
                progress_callback=create_subprogress_reporter(progress_callback, 58.0, 100.0),
            )
            emit_progress(progress_callback, 100.0, "Đã hoàn tất quá trình ghi nhận xét.")
            return (
                queue_context,
                write_rows,
                login_message,
                selection_message,
                apply_context,
                apply_result,
                "",
                "",
            )

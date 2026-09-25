"""Ghi payload điểm/nhận xét vào bảng và xác minh lưu."""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from playwright.sync_api import Page

from nhanxet.config import ProgressCallback
from nhanxet.models import ScorebookContext
from nhanxet.progress import create_subprogress_reporter, emit_progress
from nhanxet.write_plan import select_relevant_server_save_requests

from ..models import ScoreWriteEntry, ScoreWriteResult
from ..save_verification import (
    _request_blob_contains_expected_score_pair,
    evaluate_server_save_verification,
)
from ..score_write import (
    build_score_write_payload,
    build_score_write_request_pairs,
    normalize_score_text,
    ready_score_write_entries,
    summarize_score_write_result,
)


class PayloadWriteMixin:
    """Ghi payload điểm/nhận xét vào bảng và xác minh lưu."""

    def _apply_payload_to_active_scorebook(
        self,
        page: Page,
        payload: List[Dict[str, str]],
        auto_save: bool,
        progress_callback: ProgressCallback | None = None,
        field_label: str = "dữ liệu",
        item_label: str = "dòng",
    ) -> Dict[str, object]:
        """Writes one prepared payload into the active scorebook window in batches and optionally clicks Save."""
        if not payload:
            return {"updated": [], "failed": [], "saveClicked": False}

        batch_size = 1 if len(payload) <= 12 else (3 if len(payload) <= 30 else 5)
        updated_input_names: List[str] = []
        failed_input_names: List[str] = []

        emit_progress(progress_callback, 5.0, f"Đang ghi {field_label}...")
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
                        if (String(input.tagName || '').toUpperCase() === 'SPAN') {
                            input.textContent = value;
                            input.innerText = value;
                            input.setAttribute('data-value', value);
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            return;
                        }
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
                        const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], span[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;
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
                f"Đang ghi {item_label} {processed_rows}/{len(payload)}...",
            )

        emit_progress(
            progress_callback,
            92.0,
            "Đang bấm Lưu dữ liệu..." if auto_save else f"Đang chốt {field_label}...",
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
        emit_progress(progress_callback, 100.0, f"Đã điền xong {field_label}.")
        return {
            "updated": list(dict.fromkeys(updated_input_names)),
            "failed": list(dict.fromkeys(failed_input_names)),
            "saveClicked": bool(finalize_result.get("saveClicked")),
        }

    def _apply_comment_payload_to_active_scorebook(
        self,
        page: Page,
        payload: List[Dict[str, str]],
        auto_save: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, object]:
        """Writes one prepared comment payload into the active scorebook window."""
        return self._apply_payload_to_active_scorebook(
            page,
            payload,
            auto_save=auto_save,
            progress_callback=progress_callback,
            field_label="dữ liệu vào các ô nhận xét",
            item_label="nhận xét",
        )

    def _apply_score_payload_to_active_scorebook(
        self,
        page: Page,
        payload: List[Dict[str, str]],
        auto_save: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, object]:
        """Writes one prepared score payload into the active scorebook window."""
        return self._apply_payload_to_active_scorebook(
            page,
            payload,
            auto_save=auto_save,
            progress_callback=progress_callback,
            field_label="điểm vào các ô mục tiêu",
            item_label="điểm",
        )

    def _arm_scorebook_save_monitor(self, page: Page) -> int:
        """Installs or resets one browser-side monitor that records save requests after the next click."""
        return int(
            page.evaluate(
                """() => {
                const previewText = (value) => {
                    if (value == null) return '';
                    try {
                        if (typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams) {
                            return value.toString().slice(0, 1200);
                        }
                        if (typeof FormData !== 'undefined' && value instanceof FormData) {
                            const parts = [];
                            value.forEach((entryValue, entryKey) => {
                                parts.push(`${String(entryKey)}=${String(entryValue)}`);
                            });
                            return parts.join('&').slice(0, 1200);
                        }
                        if (typeof value === 'string') {
                            return value.slice(0, 1200);
                        }
                        return String(value).slice(0, 1200);
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

    def _verify_scorebook_save_on_server(
        self,
        page: Page,
        save_monitor_run_id: int,
        auto_save: bool,
        save_clicked: bool,
        payload_input_names: List[str],
        expected_score_pairs: List[Tuple[str, str]] | None = None,
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
                expected_score_pairs=expected_score_pairs,
            )
            return {
                "saveVerified": save_verified,
                "saveVerificationMode": verification_mode,
                "saveVerificationDetail": verification_detail,
            }

        deadline = time.time() + timeout_sec
        latest_requests: List[Dict[str, object]] = []
        normalized_expected_pairs = [
            (input_name.strip(), normalize_score_text(value))
            for input_name, value in list(expected_score_pairs or [])
            if input_name.strip() and normalize_score_text(value)
        ]
        while time.time() < deadline:
            latest_requests = self._read_scorebook_save_monitor_requests(page, save_monitor_run_id)
            relevant_requests = select_relevant_server_save_requests(
                latest_requests,
                request_markers=payload_input_names,
            )
            payload_pair_observed = not normalized_expected_pairs or any(
                _request_blob_contains_expected_score_pair(
                    " ".join(
                        [
                            str(request.get("url", "")).strip(),
                            str(request.get("bodyPreview", "")).strip(),
                            str(request.get("responsePreview", "")).strip(),
                        ]
                    ),
                    input_name,
                    value,
                )
                for request in latest_requests
                for input_name, value in normalized_expected_pairs
            )
            if (
                relevant_requests
                and all(bool(request.get("finished")) for request in relevant_requests)
                and payload_pair_observed
            ):
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
            expected_score_pairs=expected_score_pairs,
        )
        emit_progress(progress_callback, 100.0, "Đã hoàn tất xác minh lưu dữ liệu.")
        return {
            "saveVerified": save_verified,
            "saveVerificationMode": verification_mode,
            "saveVerificationDetail": verification_detail,
        }

    def _read_scorebook_payload_values(
        self,
        page: Page,
        payload: List[Dict[str, str]],
    ) -> Dict[str, object]:
        """Reads the current DOM values for one prepared scorebook payload."""
        return dict(
            page.evaluate(
                """(rows) => {
                const wins = Array.from(document.querySelectorAll('.x-window')).filter(win => /sổ điểm/i.test(win.innerText || ''));
                const active = wins.find(win => /(ux-desktop-active-win|x-window-active)/i.test(win.className || ''));
                const root = active || wins[wins.length - 1];
                const values = {};
                if (!root) return values;
                for (const row of rows) {
                    const selector = `input[name="${row.inputName}"], textarea[name="${row.inputName}"], span[name="${row.inputName}"], #${CSS.escape(row.inputName)}`;
                    const input = root.querySelector(selector);
                    values[row.inputName] = input
                        ? (
                            String(input.tagName || '').toUpperCase() === 'SPAN'
                                ? String(input.textContent || input.innerText || '')
                                : String(input.value || '')
                        )
                        : '';
                }
                return values;
            }""",
                payload,
            )
        )

    def _wait_for_expected_scorebook_payload_values(
        self,
        page: Page,
        payload: List[Dict[str, str]],
        timeout_sec: float = 0.8,
        poll_ms: int = 120,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ giao diện phản ánh điểm vừa ghi...",
    ) -> Dict[str, object]:
        """Polls live inputs until the DOM reflects the expected payload, or the deadline expires."""
        timeout_window = max(timeout_sec, 0.1)
        deadline = time.time() + timeout_window
        latest_values: Dict[str, object] = {}
        while True:
            latest_values = self._read_scorebook_payload_values(page, payload)
            if self._scorebook_payload_matches_expected(payload, latest_values):
                emit_progress(progress_callback, 100.0, "Đã xác minh xong dữ liệu vừa ghi trên giao diện.")
                return latest_values
            if time.time() >= deadline:
                emit_progress(progress_callback, 100.0, "Đã hết thời gian chờ phản ánh dữ liệu trên giao diện.")
                return latest_values
            elapsed_ratio = min((time.time() - (deadline - timeout_window)) / timeout_window, 1.0)
            emit_progress(progress_callback, max(10.0, elapsed_ratio * 95.0), progress_message)
            page.wait_for_timeout(max(int(poll_ms), 40))

    def _scorebook_payload_matches_expected(
        self,
        payload: List[Dict[str, str]],
        verification_map: Dict[str, object],
    ) -> bool:
        """Returns whether every payload input already exposes the expected value in the DOM."""
        for item in payload:
            input_name = str(item.get("inputName", "")).strip()
            expected_value = str(item.get("text", "")).strip()
            actual_value = str(verification_map.get(input_name, "")).strip()
            if actual_value != expected_value:
                return False
        return True

    def _read_comment_payload_values(
        self,
        page: Page,
        payload: List[Dict[str, str]],
    ) -> Dict[str, object]:
        """Reads the current DOM values for one prepared comment payload."""
        return self._read_scorebook_payload_values(page, payload)

    def apply_score_entries(
        self,
        grade_id: str,
        class_id: str,
        subject_id: str,
        term_id: str,
        entries: List[ScoreWriteEntry | Dict[str, object]],
        username: str = "",
        password: str = "",
        auto_save: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, ScoreWriteResult, str, str]:
        """Writes score entries back into the live scorebook and verifies the updated DOM values."""
        rows_to_apply = ready_score_write_entries(entries)
        if not rows_to_apply:
            raise RuntimeError("Không có dòng điểm nào sẵn sàng để ghi lên VNEDU.")

        with self._open_page() as page:
            selected_snapshot, login_message, selection_message = self._open_selected_scorebook_snapshot_on_page(
                page,
                grade_id=grade_id,
                class_id=class_id,
                subject_id=subject_id,
                term_id=term_id,
                username=username,
                password=password,
                progress_callback=create_subprogress_reporter(progress_callback, 0.0, 36.0),
            )
            active_context = self._build_scorebook_context(selected_snapshot)
            context, result = self._apply_score_entries_on_page(
                page,
                rows_to_apply,
                context=active_context,
                auto_save=auto_save,
                progress_callback=create_subprogress_reporter(progress_callback, 36.0, 100.0),
            )
            return context, result, login_message, selection_message

    def _apply_score_entries_on_page(
        self,
        page: Page,
        entries: List[ScoreWriteEntry | Dict[str, object]],
        context: ScorebookContext | None = None,
        auto_save: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> Tuple[ScorebookContext, ScoreWriteResult]:
        """Writes one score-entry queue back into the already-selected live scorebook page."""
        rows_to_apply = ready_score_write_entries(entries)
        if not rows_to_apply:
            raise RuntimeError("Không có dòng điểm nào sẵn sàng để ghi lên VNEDU.")

        emit_progress(progress_callback, 8.0, "Đang chuẩn bị dữ liệu để ghi lên cột điểm...")
        payload = build_score_write_payload(rows_to_apply)
        expected_score_pairs = build_score_write_request_pairs(payload)
        emit_progress(progress_callback, 16.0, "Đang gắn bộ theo dõi thao tác Lưu..." if auto_save else "Đang chuẩn bị xác minh DOM sau khi ghi điểm...")
        save_monitor_run_id = self._arm_scorebook_save_monitor(page)
        result_payload = self._apply_score_payload_to_active_scorebook(
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
        emit_progress(progress_callback, 88.0, "Đang xác minh lại điểm vừa ghi trên giao diện...")
        verification_map = self._wait_for_expected_scorebook_payload_values(
            page,
            payload,
            timeout_sec=0.8,
            poll_ms=120,
            progress_callback=create_subprogress_reporter(progress_callback, 88.0, 94.0),
        )
        result = summarize_score_write_result(
            entries,
            rows_to_apply,
            result_payload=result_payload,
            verification_map=verification_map,
        )
        emit_progress(progress_callback, 100.0, "Đã hoàn tất thao tác ghi điểm.")
        active_context = context if context is not None else self._build_scorebook_context(self._scorebook_snapshot(page))
        return active_context, result

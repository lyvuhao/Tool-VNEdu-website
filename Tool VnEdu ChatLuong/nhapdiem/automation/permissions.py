"""Kiểm tra quyền nhập điểm và đăng nhập VNEDU (bản dành cho Nhập điểm)."""

from __future__ import annotations

import time
import unicodedata

from ..config import ProgressCallback
from ..scorebook_core import (
    apply_access_entries_to_context,
    emit_progress,
    ScorebookContext,
    ScoreOption,
    VnEduScoreAutomation,
)


def _normalize_permission_text_for_score_access(permission_text: str) -> str:
    """Normalizes one VNEDU permission string for reliable score-access checks."""
    normalized = unicodedata.normalize("NFD", str(permission_text or "").strip().lower())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return " ".join(normalized.split())


def _permission_allows_score_entry(permission_text: str) -> bool:
    """Returns whether one VNEDU permission string explicitly allows score entry."""
    normalized = _normalize_permission_text_for_score_access(permission_text)
    if not normalized:
        return False
    if "khong co quyen nhap diem" in normalized or "khong co quyen" in normalized:
        return False
    return "giao vien bo mon" in normalized


def _rewrite_access_message_for_score_ui(message: str) -> str:
    """Rewords embedded permission-scan messages so they match the score-entry GUI."""
    normalized = str(message or "").strip()
    if not normalized:
        return ""
    replacements = (
        ("có quyền nhập nhận xét", "có quyền nhập điểm"),
        ("không có quyền nhập nhận xét", "không có quyền nhập điểm"),
        ("có thể nhập nhận xét", "có thể nhập điểm"),
        ("quyền nhập nhận xét", "quyền nhập điểm"),
        ("dò quyền lớp/môn", "dò quyền nhập điểm lớp/môn"),
        ("nhập nhận xét", "nhập điểm"),
    )
    for old_text, new_text in replacements:
        normalized = normalized.replace(old_text, new_text)
    return normalized


def _patched_can_comment_scorebook(self: VnEduScoreAutomation, permission_text: str, enabled_comment_input_count: int) -> bool:
    """Filters live class-subject pairs by score-entry permission instead of comment cells."""
    _ = enabled_comment_input_count
    return _permission_allows_score_entry(permission_text)


def _patched_apply_access_entries_to_context(
    self: VnEduScoreAutomation,
    context: ScorebookContext,
    entries: list[object],
    grade_id: str,
    term_id: str,
) -> ScorebookContext:
    """Keeps the scanned grade-term scope even when no class has score-entry permission."""
    normalized_grade_id = str(grade_id or "").strip()
    normalized_term_id = str(term_id or "").strip()
    resolved_context = apply_access_entries_to_context(
        context,
        list(entries or []),
        grade_id=normalized_grade_id,
        term_id=normalized_term_id,
    )
    if entries:
        return resolved_context
    resolved_context.accessible_entries = []
    resolved_context.accessible_grade_id = normalized_grade_id
    resolved_context.accessible_term_id = normalized_term_id
    return resolved_context


def _patched_subject_options_by_class_for_current_grade(
    self: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    grade_id: str,
    term_id: str,
    class_options: list[ScoreOption],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Builds class-subject specs without requiring the hidden class id to settle immediately."""
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
    class_subject_specs: list[dict[str, object]] = []

    for class_option in class_options:
        class_id = class_option.option_id.strip()
        if not class_id:
            continue
        current_class_id = self._effective_snapshot_selected_id(
            current_snapshot,
            "currentClassId",
            "hiddenClassId",
        )
        if current_class_id != class_id:
            class_combo_id = str(current_snapshot.get("classComboId", "")).strip()
            if not class_combo_id or not self._set_combo_value(page, class_combo_id, class_id):
                raise RuntimeError(f"Không thể chọn lớp id={class_id} trong lúc dò quyền lớp/môn.")
            current_snapshot = self._wait_for_hydrated_scorebook_options(
                page,
                current_snapshot,
                options_key="subjectOptions",
                include_grade=False,
                include_class=False,
                include_subject=True,
                include_term=False,
                expected_grade_id=grade_id or None,
                timeout_sec=6.0,
            )
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
            subject_combo_id = str(current_snapshot.get("subjectComboId", "")).strip()
            if subject_combo_id:
                subject_options = self._build_options(self._load_live_combo_options(page, subject_combo_id))
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


def _patched_find_visible_input(
    self: VnEduScoreAutomation,
    page: object,
    selectors: list[str],
):
    """Returns the first visible input that matches any selector, falling back to the first match."""
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        if count > 0:
            return locator.first
    return None


def _patched_find_username_input(self: VnEduScoreAutomation, page: object):
    """Returns a visible username input instead of the first hidden text field in DOM order."""
    selectors = [
        "input[name*='user' i]",
        "input[id*='user' i]",
        "input[name*='login' i]",
        "input[id*='login' i]",
        "input[name*='account' i]",
        "input[id*='account' i]",
        "input[type='email']",
        "input[type='text']",
    ]
    username_input = self._find_visible_input(page, selectors)
    if username_input is None:
        raise RuntimeError("Không tìm thấy ô nhập tài khoản trên form đăng nhập.")
    return username_input


def _patched_read_login_surface_state(
    self: VnEduScoreAutomation,
    page: object,
) -> dict[str, object]:
    """Reads one compact VNEDU login/shell state so login success is not inferred from password DOM alone."""
    state = {
        "url": str(getattr(page, "url", "") or "").strip(),
        "login_form_visible": False,
        "password_visible": False,
        "captcha_visible": False,
        "shell_visible": False,
        "scorebook_visible": False,
        "error_text": "",
    }
    try:
        state["scorebook_visible"] = bool(self._has_scorebook_controls(page))
    except Exception:
        state["scorebook_visible"] = False
    if state["scorebook_visible"]:
        state["shell_visible"] = True
        return state

    try:
        snapshot = page.evaluate(
            """() => {
            const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
                    return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const normalizeText = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visiblePasswords = Array.from(document.querySelectorAll("input[type='password']")).filter(isVisible);
            const visibleUsers = Array.from(document.querySelectorAll("input[type='text'], input[type='email']")).filter(isVisible);
            const visibleLoginButtons = Array.from(document.querySelectorAll("button, input[type='submit'], a"))
                .filter(isVisible)
                .filter(el => {
                    const text = normalizeText(el.innerText || el.value || el.getAttribute('title') || '').toLowerCase();
                    return text.includes('đăng nhập') || text.includes('dang nhap') || text.includes('login');
                });
            const visibleCaptcha = Array.from(
                document.querySelectorAll("input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='captcha' i]")
            ).filter(isVisible);
            const shellVisible = Boolean(
                document.querySelector('.ux-taskbar, #ux-taskbar, .ux-desktop-shortcut, #x-desktop, .x-desktop')
            );
            const errorKeywords = [
                'sai tài khoản',
                'sai mật khẩu',
                'tài khoản hoặc mật khẩu',
                'đăng nhập không thành công',
                'đăng nhập thất bại',
                'không thể đăng nhập',
                'mã xác thực',
                'captcha',
            ];
            const textCandidates = Array.from(document.querySelectorAll('div, span, td, p, label, li'))
                .filter(isVisible)
                .map(el => normalizeText(el.innerText || el.textContent || ''))
                .filter(text => text && text.length <= 220);
            const errorText = textCandidates.find(text => {
                const normalized = text.toLowerCase();
                return errorKeywords.some(keyword => normalized.includes(keyword));
            }) || '';
            return {
                loginFormVisible: Boolean(visiblePasswords.length && (visibleUsers.length || visibleLoginButtons.length)),
                passwordVisible: Boolean(visiblePasswords.length),
                captchaVisible: Boolean(visibleCaptcha.length),
                shellVisible,
                errorText,
            };
        }"""
        )
    except Exception:
        snapshot = {}

    state["login_form_visible"] = bool(snapshot.get("loginFormVisible"))
    state["password_visible"] = bool(snapshot.get("passwordVisible"))
    state["captcha_visible"] = bool(snapshot.get("captchaVisible"))
    state["shell_visible"] = bool(snapshot.get("shellVisible"))
    state["error_text"] = str(snapshot.get("errorText", "") or "").strip()
    return state


def _patched_login_if_needed_on_page(
    self: VnEduScoreAutomation,
    page: object,
    username: str = "",
    password: str = "",
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Logs in using visible login controls and confirms success from VNEDU shell state instead of password DOM removal."""

    emit_progress(progress_callback, 5.0, "Đang truy cập trang VNEDU...")
    self._goto_target_page(page)
    self._close_notice_popup(page)

    login_state = self._read_login_surface_state(page)
    if not login_state["login_form_visible"]:
        emit_progress(progress_callback, 100.0, "Không cần đăng nhập lại, phiên đã sẵn sàng.")
        if username.strip() or password:
            return "Không phát hiện form đăng nhập; có thể phiên đã đăng nhập sẵn."
        return ""

    if not username.strip() or not password:
        raise RuntimeError(
            "Phiên hiện tại đang ở màn hình đăng nhập. Hãy nhập tài khoản và mật khẩu VNEDU."
        )

    password_input = self._find_visible_input(page, ["input[type='password']"])
    if password_input is None:
        raise RuntimeError("Không tìm thấy ô nhập mật khẩu đang hiển thị trên form đăng nhập.")
    username_input = self._find_username_input(page)
    emit_progress(progress_callback, 20.0, "Đang điền tài khoản và mật khẩu VNEDU...")
    username_input.fill(username.strip())
    password_input.fill(password)

    captcha_input = page.locator(
        "input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='captcha' i]"
    )
    if captcha_input.count() > 0:
        try:
            visible_captcha = next(
                (
                    captcha_input.nth(index)
                    for index in range(captcha_input.count())
                    if captcha_input.nth(index).is_visible()
                ),
                None,
            )
        except Exception:
            visible_captcha = captcha_input.first
        if visible_captcha is not None:
            captcha_value = visible_captcha.input_value().strip()
            if not captcha_value:
                try:
                    visible_captcha.focus()
                except Exception:
                    pass
                raise RuntimeError(
                    "Trang đăng nhập VNEDU đang yêu cầu mã captcha. "
                    "App đã điền sẵn tài khoản và mật khẩu trên tab hiện tại; "
                    "hãy nhập captcha rồi bấm Đăng nhập thủ công, sau đó nhấn lại 'Đăng nhập + load Sổ điểm'."
                )

    emit_progress(progress_callback, 40.0, "Đang gửi yêu cầu đăng nhập...")
    clicked = page.evaluate(
        """() => {
        const isVisible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };
        const candidates = Array.from(document.querySelectorAll('button, input[type="submit"], a')).filter(isVisible);
        const target = candidates.find(el => {
            const text = (el.innerText || el.value || el.getAttribute('title') || '').trim().toLowerCase();
            return text.includes('đăng nhập') || text.includes('dang nhap') || text.includes('login');
        });
        if (!target) return false;
        target.click();
        return true;
    }"""
    )
    if not clicked:
        password_input.press("Enter")

    started_at = time.time()
    deadline = started_at + 30.0
    last_state = login_state
    while time.time() < deadline:
        page.wait_for_timeout(250)
        self._close_notice_popup(page)
        last_state = self._read_login_surface_state(page)
        if last_state["error_text"]:
            raise RuntimeError(f"Đăng nhập VNEDU thất bại: {last_state['error_text']}")
        if last_state["scorebook_visible"] or last_state["shell_visible"]:
            emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")
            return "Đã gửi đăng nhập và xác thực thành công."
        if not last_state["login_form_visible"] and not last_state["password_visible"]:
            emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")
            return "Đã gửi đăng nhập và xác thực thành công."
        elapsed_ratio = min((time.time() - started_at) / 30.0, 1.0)
        emit_progress(
            progress_callback,
            40.0 + (elapsed_ratio * 55.0),
            "Đang chờ VNEDU xác thực đăng nhập...",
        )

    if last_state.get("captcha_visible"):
        raise RuntimeError(
            "Đăng nhập VNEDU chưa hoàn tất vì hệ thống đang yêu cầu captcha/xác thực bổ sung."
        )
    if last_state.get("error_text"):
        raise RuntimeError(f"Đăng nhập VNEDU thất bại: {last_state['error_text']}")
    raise RuntimeError(
        "Đăng nhập VNEDU chưa được xác nhận hoàn tất. Form đăng nhập vẫn còn hiển thị hoặc phiên CDP chưa chuyển sang màn hình làm việc."
    )

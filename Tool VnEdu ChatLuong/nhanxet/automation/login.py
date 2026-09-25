"""Đăng nhập VNEDU và mở màn hình Sổ điểm."""

from __future__ import annotations

import time
from typing import Dict

from playwright.sync_api import Error as PlaywrightError, Page

from ..config import ProgressCallback
from ..progress import create_subprogress_reporter, emit_progress


class LoginMixin:
    """Đăng nhập VNEDU và mở màn hình Sổ điểm."""

    def _close_notice_popup(self, page: Page) -> None:
        """Dismisses VNEDU modal notices that block further clicks."""
        try:
            ok_button = page.locator("button:has-text('OK')")
            if ok_button.count() > 0 and ok_button.first.is_visible():
                ok_button.first.click(timeout=800)
                page.wait_for_timeout(200)
        except PlaywrightError:
            return

    def _find_username_input(self, page: Page):
        """Returns a likely username input on the current login form."""
        selectors = [
            "input[name*='user' i]",
            "input[id*='user' i]",
            "input[name*='login' i]",
            "input[id*='login' i]",
            "input[name*='account' i]",
            "input[id*='account' i]",
            "input[type='email']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator.first

        fallback = page.locator("input[type='text']")
        if fallback.count() > 0:
            return fallback.first
        raise RuntimeError("Không tìm thấy ô nhập tài khoản trên form đăng nhập.")

    def _login_if_needed_on_page(
        self,
        page: Page,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        """Logs in when a password field is visible."""
        emit_progress(progress_callback, 5.0, "Đang truy cập trang VNEDU...")
        self._goto_target_page(page)
        self._close_notice_popup(page)

        password_input = page.locator("input[type='password']")
        if password_input.count() == 0:
            emit_progress(progress_callback, 100.0, "Không cần đăng nhập lại, phiên đã sẵn sàng.")
            if username.strip() or password:
                return "Không phát hiện form đăng nhập; có thể phiên đã đăng nhập sẵn."
            return ""

        if not username.strip() or not password:
            raise RuntimeError(
                "Phiên hiện tại đang ở màn hình đăng nhập. Hãy nhập tài khoản và mật khẩu VNEDU."
            )

        username_input = self._find_username_input(page)
        emit_progress(progress_callback, 20.0, "Đang điền tài khoản và mật khẩu VNEDU...")
        username_input.fill(username.strip())
        password_input.first.fill(password)

        captcha_input = page.locator(
            "input[name*='captcha' i], input[id*='captcha' i], input[placeholder*='captcha' i]"
        )
        if captcha_input.count() > 0 and captcha_input.first.is_visible():
            captcha_value = captcha_input.first.input_value().strip()
            if not captcha_value:
                try:
                    captcha_input.first.focus()
                except PlaywrightError:
                    pass
                raise RuntimeError(
                    "Trang đăng nhập VNEDU đang yêu cầu mã captcha. "
                    "App đã điền sẵn tài khoản và mật khẩu trên tab hiện tại; "
                    "hãy nhập captcha rồi bấm Đăng nhập thủ công, sau đó nhấn lại 'Đăng nhập + đọc Sổ điểm'."
                )

        emit_progress(progress_callback, 40.0, "Đang gửi yêu cầu đăng nhập...")
        clicked = page.evaluate(
            """() => {
            const candidates = Array.from(document.querySelectorAll('button, input[type="submit"]'));
            const target = candidates.find(el => {
                const text = (el.innerText || el.value || '').trim().toLowerCase();
                return text.includes('đăng nhập') || text.includes('dang nhap') || text.includes('login');
            });
            if (!target) return false;
            target.click();
            return true;
        }"""
        )
        if not clicked:
            password_input.first.press("Enter")

        deadline = time.time() + 20
        while time.time() < deadline:
            page.wait_for_timeout(250)
            self._close_notice_popup(page)
            elapsed_ratio = min((time.time() - (deadline - 20)) / 20.0, 1.0)
            emit_progress(
                progress_callback,
                40.0 + (elapsed_ratio * 55.0),
                "Đang chờ VNEDU xác thực đăng nhập...",
            )
            if page.locator("input[type='password']").count() == 0:
                emit_progress(progress_callback, 100.0, "Đăng nhập VNEDU thành công.")
                return "Đã gửi đăng nhập và xác thực thành công."

        raise RuntimeError("Đăng nhập chưa thành công. Vui lòng kiểm tra tài khoản hoặc xác thực bổ sung.")

    def _has_scorebook_controls(self, page: Page) -> bool:
        """Checks whether the active VNEDU window looks like the scorebook screen."""
        return bool(
            page.evaluate(
                """() => {
                const windows = Array.from(document.querySelectorAll('.x-window'))
                    .filter(win => /sổ điểm/i.test((win.innerText || '')));
                const active = windows.find(win =>
                    /(ux-desktop-active-win|x-window-active)/i.test(win.className || '')
                );
                const root = active || (windows.length ? windows[windows.length - 1] : null);
                if (!root) return false;

                const labels = Array.from(root.querySelectorAll('label.x-form-item-label'))
                    .map(label => (label.innerText || '').toLowerCase());
                const required = ['khối', 'lớp', 'môn', 'học kỳ'];
                return required.every(token => labels.some(label => label.includes(token)));
            }"""
            )
        )

    def _find_scorebook_shortcut(self, page: Page, timeout_sec: float = 10.0) -> Dict[str, object]:
        """Finds the VNEDU desktop shortcut metadata for `Sổ điểm` with retry and debug details."""
        deadline = time.time() + max(timeout_sec, 1.0)
        last_snapshot: Dict[str, object] = {}
        while time.time() < deadline:
            self._close_notice_popup(page)
            last_snapshot = page.evaluate(
                """() => {
                const normalizeText = (value) => (value || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/đ/g, 'd')
                    .replace(/\\s+/g, ' ')
                    .trim();

                const serialize = (el) => ({
                    id: el.id || '',
                    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '),
                    className: el.className || '',
                });

                const desktopCandidates = Array.from(
                    document.querySelectorAll('.ux-desktop-shortcut, .x-view-item, [id$="-shortcut"]')
                );
                const visibleCandidates = desktopCandidates.filter(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
                const target = document.querySelector('#Sổ điểm-shortcut')
                    || document.querySelector('#So diem-shortcut')
                    || visibleCandidates.find(el => {
                        const haystacks = [
                            normalizeText(el.id),
                            normalizeText(el.innerText || el.textContent || ''),
                            normalizeText(el.getAttribute('title') || ''),
                        ];
                        return haystacks.some(text =>
                            text.includes('so diem') || text.includes('sodiem')
                        );
                    });
                if (!target) {
                    return {
                        found: false,
                        shortcutCount: visibleCandidates.length,
                        sampleShortcuts: visibleCandidates.slice(0, 12).map(serialize),
                    };
                }

                if (typeof target.scrollIntoView === 'function') {
                    target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                }
                const rect = target.getBoundingClientRect();
                return {
                    found: true,
                    shortcutCount: visibleCandidates.length,
                    sampleShortcuts: visibleCandidates.slice(0, 12).map(serialize),
                    id: target.id || '',
                    text: (target.innerText || target.textContent || '').trim(),
                    className: target.className || '',
                    centerX: rect.x + (rect.width / 2),
                    centerY: rect.y + (rect.height / 2),
                    width: rect.width,
                    height: rect.height,
                };
            }"""
            )
            if last_snapshot.get("found"):
                return last_snapshot
            page.wait_for_timeout(300)

        sample_labels = []
        for item in list(last_snapshot.get("sampleShortcuts", []))[:6]:
            if isinstance(item, dict):
                label = str(item.get("text", "")).strip() or str(item.get("id", "")).strip()
                if label:
                    sample_labels.append(label)
        observed = f"shortcut thấy được: {int(last_snapshot.get('shortcutCount', 0) or 0)}"
        if sample_labels:
            observed += f" | mẫu: {', '.join(sample_labels)}"
        raise RuntimeError(
            "Không tìm thấy shortcut Sổ điểm trên desktop VNEDU. "
            f"{observed}"
        )

    def _open_scorebook_from_desktop(
        self,
        page: Page,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Opens the scorebook window by double-clicking the live desktop shortcut."""
        emit_progress(progress_callback, 10.0, "Đang tìm shortcut Sổ điểm...")
        shortcut = self._find_scorebook_shortcut(page)
        clicked = False
        shortcut_id = str(shortcut.get("id", "")).strip()
        if shortcut_id:
            clicked = bool(
                page.evaluate(
                    """(shortcutId) => {
                    const target = document.getElementById(shortcutId);
                    if (!target) return false;
                    if (typeof target.scrollIntoView === 'function') {
                        target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                    }
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
                    return true;
                }""",
                    shortcut_id,
                )
            )
        if not clicked:
            page.mouse.dblclick(float(shortcut["centerX"]), float(shortcut["centerY"]))
        for _ in range(30):
            page.wait_for_timeout(400)
            self._close_notice_popup(page)
            elapsed_ratio = (_ + 1) / 30.0
            emit_progress(
                progress_callback,
                25.0 + (elapsed_ratio * 70.0),
                "Đang chờ cửa sổ Sổ điểm mở ra...",
            )
            if self._has_scorebook_controls(page):
                emit_progress(progress_callback, 100.0, "Đã mở cửa sổ Sổ điểm.")
                return
        raise RuntimeError("Đã bấm shortcut 'Sổ điểm' nhưng không mở được cửa sổ Sổ điểm.")

    def _ensure_scorebook_screen(
        self,
        page: Page,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Ensures the live VNEDU session ends up on the scorebook window."""
        emit_progress(progress_callback, 5.0, "Đang kiểm tra cửa sổ Sổ điểm...")
        self._goto_target_page(page)
        self._close_notice_popup(page)
        if self._has_scorebook_controls(page):
            emit_progress(progress_callback, 100.0, "Cửa sổ Sổ điểm đã sẵn sàng.")
            return
        emit_progress(progress_callback, 20.0, "Đang mở cửa sổ Sổ điểm...")
        self._open_scorebook_from_desktop(
            page,
            progress_callback=create_subprogress_reporter(progress_callback, 20.0, 100.0),
        )

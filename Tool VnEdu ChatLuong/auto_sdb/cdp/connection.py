"""Kết nối/ngắt CDP, tìm tab VnEdu, nhận diện trang đăng nhập."""

import time

from ..compat import HAS_PLAYWRIGHT, sync_playwright
from .config import CDP_HOST, logger, VNEDU_URL_PATTERNS
from .health import list_cdp_tabs


class ConnectionMixin:
    """Kết nối/ngắt CDP, tìm tab VnEdu, nhận diện trang đăng nhập."""

    # -----------------------------------------------------------------
    # 3.1: CONNECTION MANAGEMENT
    # -----------------------------------------------------------------

    def connect(self, allow_any_tab=False):
        """Kết nối Chrome qua CDP và tìm tab VnEdu.

        Returns:
            (success: bool, message: str)
        """
        try:
            if not HAS_PLAYWRIGHT:
                return False, "Playwright chưa cài. Chạy: pip install playwright"

            # Khởi tạo Playwright
            self._pw = sync_playwright().start()

            # Kết nối Chrome qua CDP
            self.browser = self._pw.chromium.connect_over_cdp(
                f"http://{CDP_HOST}:{self.port}"
            )

            # Tìm tab VnEdu
            self.page = self._find_vnedu_tab()
            if not self.page and allow_any_tab:
                self.page = self._find_first_page()
            if not self.page:
                # Liệt kê tabs hiện có để debug
                tabs = list_cdp_tabs(self.port)
                tab_info = ", ".join(t["title"][:30] for t in tabs[:5]) or "(trống)"
                self.disconnect()
                return False, (
                    f"Không tìm thấy tab VnEdu.\n"
                    f"Tabs hiện có: {tab_info}\n"
                    f"Hãy mở trang VnEdu trước."
                )

            # Set timeout mặc định
            self.page.set_default_timeout(self.timeout_ms)

            self._connected = True
            page_title = self.page.title()[:50]
            logger.info(f"CDP connected: {page_title}")
            return True, f"Đã kết nối: {page_title}"

        except Exception as e:
            logger.error(f"CDP connect error: {e}")
            self.disconnect()
            return False, f"Lỗi kết nối CDP: {type(e).__name__}: {str(e)[:100]}"

    def disconnect(self):
        """Ngắt kết nối CDP. An toàn gọi nhiều lần."""
        self._connected = False
        self.page = None
        self._cached_selectors = {}
        # Page cũ sẽ chết — bỏ reference handler để lần connect sau gắn lại sạch
        self._dialog_handler = None
        # QUAN TRỌNG: instance này attach vào Chrome thật bằng connect_over_cdp().
        # Gọi browser.close() ở đây có thể đóng/phá context của tab VnEdu người dùng
        # đang thao tác, gây TargetClosedError và làm web mất khả năng click dropdown.
        # Dừng Playwright driver bên dưới là đủ để detach phiên automation.
        self.browser = None
        try:
            if self._pw:
                self._pw.stop()
        except Exception as e:
            logger.debug(f"Playwright stop error (ignored): {e}")
        finally:
            self._pw = None
        logger.info("CDP disconnected")

    @property
    def is_connected(self):
        """Kiểm tra kết nối còn sống không."""
        if not self._connected or not self.page:
            return False
        try:
            # Ping page bằng cách đọc URL
            _ = self.page.url
            return True
        except Exception:
            self._connected = False
            return False

    def request_stop(self):
        """Yêu cầu dừng batch operation."""
        self._stop_requested = True

    def reset_stop(self):
        """Reset stop signal."""
        self._stop_requested = False

    @property
    def should_stop(self):
        """Kiểm tra có yêu cầu dừng không."""
        return self._stop_requested

    def _find_vnedu_tab(self):
        """Tìm tab VnEdu trong tất cả contexts/pages của Chrome.

        Returns:
            Page | None — Playwright Page object hoặc None
        """
        if not self.browser:
            return None

        candidates = []
        for ctx in self.browser.contexts:
            for page in ctx.pages:
                url = page.url.lower()
                if any(pat in url for pat in VNEDU_URL_PATTERNS):
                    score = 0
                    if "/v5/" in url:
                        score = 400
                    elif "quan-ly-truong-hoc" in url:
                        score = 300
                    elif "diendan.vnedu.vn" in url:
                        score = 200
                    elif "user.vnedu.vn" in url or "/sso/" in url:
                        score = 100
                    else:
                        score = 150
                    candidates.append((score, page))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        page = candidates[0][1]
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    def _find_first_page(self):
        """Lấy page đầu tiên khả dụng; dùng cho auto-flow khi Chrome mới mở chưa có tab VnEdu."""
        if not self.browser:
            return None
        for ctx in self.browser.contexts:
            if ctx.pages:
                try:
                    ctx.pages[0].bring_to_front()
                except Exception:
                    pass
                return ctx.pages[0]
        for ctx in self.browser.contexts:
            try:
                page = ctx.new_page()
                return page
            except Exception:
                continue
        return None

    def _is_login_page(self):
        """Kiểm tra page hiện tại có đang ở form đăng nhập SSO không."""
        if not self.page:
            return False
        try:
            return bool(self.page.evaluate('''() => {
                const user = document.querySelector('#txtUsername, input[placeholder="Tài khoản"]');
                const pwd = document.querySelector('#txtPassword, input[placeholder="Mật khẩu"], input[type="password"]');
                if (!user || !pwd) return false;
                // Form phải đang hiển thị thật (tránh nhận nhầm input ẩn).
                const visible = (el) => !!el && el.offsetParent !== null;
                return visible(user) && visible(pwd);
            }'''))
        except Exception:
            return False

    def _is_session_expired_page(self):
        """Kiểm tra VnEdu có đang báo phiên làm việc hết hiệu lực hay không."""
        if not self.page:
            return False
        try:
            return bool(self.page.evaluate('''() => {
                const bodyText = String(document.body ? document.body.innerText || '' : '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                return /Phiên làm việc hết hiệu lực|phiên làm việc hết hiệu lực|bật cookies|enable cookies/i.test(bodyText);
            }'''))
        except Exception:
            return False

    def _is_v5_detail_ready(self):
        """Kiểm tra đã vào đúng màn Chi tiết sổ đầu bài hay chưa."""
        if not self.page:
            return False
        try:
            if self._is_session_expired_page():
                return False
            return bool(self.page.evaluate('''() => {
                try {
                    if (typeof Ext !== 'undefined' && Ext.ComponentQuery) {
                        const names = Ext.ComponentQuery.query('combobox')
                            .map(c => {
                                try { return c.getName ? c.getName() : (c.name || ''); }
                                catch(e) { return ''; }
                            })
                            .filter(Boolean);
                        if (names.includes('cboTuanHoc') && names.includes('cboLopHoc')) {
                            return true;
                        }
                    }
                } catch(e) {}
                return !!document.querySelector('a.add_tiet_so_dau_bai');
            }'''))
        except Exception:
            return False

    def _wait_until(self, predicate_js, timeout_s=15, interval_s=0.25):
        """Chờ một predicate JS trả về truthy."""
        deadline = time.time() + max(timeout_s, 0.5)
        last_error = None
        while time.time() < deadline:
            try:
                if self.page.evaluate(predicate_js):
                    return True, ""
            except Exception as e:
                msg = str(e)
                if "Execution context was destroyed" not in msg:
                    last_error = msg
            time.sleep(interval_s)
        return False, last_error or "timeout"

    def _get_login_error_text(self):
        """Đọc message lỗi đăng nhập nếu form SSO đang hiện thông báo."""
        if not self.page:
            return ""
        try:
            return str(self.page.evaluate('''() => {
                const selectors = [
                    '.alert-danger',
                    '.text-danger',
                    '.invalid-feedback',
                    '.error',
                    '.message-error',
                    '.ant-form-item-explain-error'
                ];
                for (const selector of selectors) {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    for (const node of nodes) {
                        const text = String(node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (text) return text;
                    }
                }
                const bodyText = String(document.body ? document.body.innerText || '' : '');
                const lines = bodyText
                    .split(/\\r?\\n/)
                    .map(line => String(line || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean);
                const candidates = lines.filter(line =>
                    /sai|không đúng|that bai|thất bại|khong ton tai|captcha|xac minh|xác minh/i.test(line)
                );
                return candidates.length ? candidates[0] : '';
            }''') or "").strip()
        except Exception:
            return ""

    def _click_text_fallback(self, target_text):
        """Fallback click theo text hiển thị khi selector chuẩn không ổn định."""
        if not self.page:
            return False
        try:
            return bool(self.page.evaluate('''(target) => {
                const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const fireClick = (el) => {
                    const evtOpts = {bubbles: true, cancelable: true, view: window};
                    el.dispatchEvent(new MouseEvent('mouseover', evtOpts));
                    el.dispatchEvent(new MouseEvent('mousedown', evtOpts));
                    el.dispatchEvent(new MouseEvent('mouseup', evtOpts));
                    el.click();
                    return true;
                };
                const nodes = Array.from(document.querySelectorAll('a,button,div,span,li,label,strong,p'));
                for (const exact of [true, false]) {
                    for (const node of nodes) {
                        const text = norm(node.innerText || node.textContent || '');
                        if (!text || !isVisible(node)) continue;
                        if ((exact && text === target) || (!exact && text.includes(target))) {
                            return fireClick(node);
                        }
                    }
                }
                return false;
            }''', str(target_text or "")))
        except Exception:
            return False

    def _get_teacher_portal_href(self):
        """Lấy href thật để vào portal giáo viên từ trang VnEdu hiện tại."""
        if not self.page:
            return ""
        try:
            return str(self.page.evaluate(r'''() => {
                const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
                const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const anchors = Array.from(document.querySelectorAll('a[href="/quan-ly-truong-hoc"]'));
                const exactCandidates = ['Giáo viên', 'Quản lí Thông tin Trường học', 'Quản lý Thông tin Trường học'];

                for (const target of exactCandidates) {
                    const found = anchors.find(el => isVisible(el) && norm(el.innerText || el.textContent || '') === target);
                    if (found) return found.href || found.getAttribute('href') || '';
                }
                const visibleAny = anchors.find(el => isVisible(el));
                if (visibleAny) return visibleAny.href || visibleAny.getAttribute('href') || '';
                const firstAny = anchors[0];
                if (firstAny) return firstAny.href || firstAny.getAttribute('href') || '';
                return '';
            }''') or "").strip()
        except Exception:
            return ""
